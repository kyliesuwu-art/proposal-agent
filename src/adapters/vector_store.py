# vector_store.py
"""Chroma 向量库封装：按 slide 存入，支持语义检索 + metadata 过滤。"""

import json
import os
import time
from typing import NotRequired, TypedDict

import chromadb
from chromadb.utils.embedding_functions import EmbeddingFunction
from openai import APIStatusError, OpenAI
from src.adapters.parser import MinerUParser
from src.config import CHROMA_PATH, resolve_project_path


class SlideDict(TypedDict):
    """单个 slide 的数据结构，与 parser.py 返回格式一致。"""
    slide_number: int
    title: str
    content: str
    source_file: str
    # 每项为 {"path": 本地图片路径, "caption": 图片描述（可能是空字符串）}
    images: list[dict]
    # 目录页等保留给调用方审查、但不参与向量索引的页面。
    indexable: NotRequired[bool]
    raw_blocks: NotRequired[list[dict]]

#前期保持联网embedding吧，别改了
class DashScopeEmbeddingFunction(EmbeddingFunction):
    """调用 DashScope text-embedding-v3 生成向量，替换 Chroma 默认的本地 ONNX 模型。"""

    def __init__(self) -> None:
        # DashScope 兼容 OpenAI 接口，直接用 openai 包调用
        self._client = OpenAI(
            api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
            base_url=os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        )

    # DashScope text-embedding-v3 单次请求最多接受 10 条文本，超过会报
    # InvalidParameter: batch size is invalid, it should not be larger than 10.
    _MAX_BATCH_SIZE = 10

    # 每批调用之间的间隔（秒）。批量导入几十个 pptx 时，slide 数量可能到
    # 几百条，几十次连续请求容易触发 DashScope 的 QPS 限流，加个小间隔留出余量。
    _BATCH_SLEEP_SECONDS = 0.2

    # 单批请求失败时的最大重试次数，用于兜底偶发的限流 / 网络抖动
    _MAX_RETRIES = 3

    # 重试前的等待时间（秒），比批次间隔更长，给限流窗口一点恢复时间
    _RETRY_SLEEP_SECONDS = 2.0

    def __call__(self, input: list[str]) -> list[list[float]]:
        """把一批文本转成向量列表，自动按 _MAX_BATCH_SIZE 分批调用 API。"""
        all_embeddings: list[list[float]] = []
        total_batches = (len(input) + self._MAX_BATCH_SIZE - 1) // self._MAX_BATCH_SIZE

        for batch_idx, start in enumerate(range(0, len(input), self._MAX_BATCH_SIZE)):
            batch = input[start : start + self._MAX_BATCH_SIZE]
            sorted_data = self._embed_batch_with_retry(batch)
            all_embeddings.extend(item.embedding for item in sorted_data)

            # 批次之间留个间隔，避免连续请求触发 QPS 限流；最后一批不用等
            is_last_batch = batch_idx == total_batches - 1
            if not is_last_batch:
                time.sleep(self._BATCH_SLEEP_SECONDS)

        return all_embeddings

    def _embed_batch_with_retry(self, batch: list[str]) -> list:
        """调用一次 DashScope embeddings 接口，遇到限流等瞬时错误时重试。

        Returns:
            按 index 排好序的 embedding 数据列表（每项带 .embedding / .index)
        """
        last_error: Exception | None = None

        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                resp = self._client.embeddings.create(
                    model="text-embedding-v3",
                    input=batch,
                )
                # 保险起见按 index 排序，防止极端情况下返回顺序与输入顺序不一致
                return sorted(resp.data, key=lambda item: item.index)
            except APIStatusError as e:
                last_error = e
                # 429 是限流，其他 5xx 也大概率是瞬时问题，值得重试；
                # 其余错误（如参数错误）重试没有意义，直接抛出
                is_retryable = e.status_code == 429 or e.status_code >= 500
                if not is_retryable or attempt == self._MAX_RETRIES:
                    raise
                print(
                    f"  DashScope embedding 请求失败（第 {attempt} 次，"
                    f"status={e.status_code}），{self._RETRY_SLEEP_SECONDS}s 后重试..."
                )
                time.sleep(self._RETRY_SLEEP_SECONDS)

        # 理论上不会走到这里（循环内要么 return 要么 raise），仅做类型兜底
        raise last_error if last_error else RuntimeError("embedding 请求未知失败")


class VectorStore:
    """封装 Chroma，按 slide 存入和检索。"""

    def __init__(self, persist_dir: str | None = None) -> None:
        """初始化 Chroma 客户端，使用持久化存储。

        Args:
            persist_dir: Chroma 数据持久化目录。未传时使用项目根目录下的
                绝对默认路径；相对覆盖路径也相对项目根目录解析。
        """
        # 使用 PersistentClient 保证重启后数据不丢失
        database_path = CHROMA_PATH if persist_dir is None else resolve_project_path(persist_dir)
        self._client = chromadb.PersistentClient(path=str(database_path))

        # 获取或创建 collection，使用 cosine 距离适合中文语义检索
        self._collection = self._client.get_or_create_collection(
            name="electrical_slides",
            metadata={"hnsw:space": "cosine"},
            embedding_function=DashScopeEmbeddingFunction(),
        )


    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def add_slides(
        self,
        slides: list[SlideDict],
        file_hash: str,
        slide_contexts: list[dict] | None = None,
        doc_type: str = "proposal",
    ) -> int:
        """将一批 slide 存入向量库。

        调用前应确保这批 slide 对应的旧数据已经清空（配合 get_existing_hash /
        delete_source 使用，见 main.py 里的 ingest 流程）。这里不再逐条检查
        ID 是否已存在——如果调用前没有清空干净，重复 ID 会导致 Chroma
        add() 直接报错，这是有意为之，方便尽早发现调用方漏掉了清空这一步，
        而不是像之前那样悄悄跳过、库里留着旧内容。

        Args:
            slides: parser 返回的 slide 列表，每个 slide 的 "images" 字段是
                [{"path": 本地图片路径, "caption": 图片描述}, ...]
            file_hash: 这批 slide 所属 pptx 文件内容的 MD5，用于下次 ingest
                时判断文件是否有更新（见 get_existing_hash）
            slide_contexts: 与 slides 一一对应（按下标）的 context/metadata
                列表，每项是 llm_client.generate_slide_context() 的返回值
                {"context_summary", "proposal_type", "client_industry"}。
                不传时按原来的行为存（不做 context 增强，metadata 里两个
                业务字段留空），保持向后兼容。
            doc_type: 文档类型标记，"proposal"（方案/技术资料）或 "policy"
                （政策参考类）。默认 "proposal"，检索时默认过滤掉 policy 类。

        Returns:
            实际入库的 slide 数量
        """
        if not slides:
            return 0

        if slide_contexts is None:
            slide_contexts = [{}] * len(slides)

        indexable_pairs = [
            (slide, ctx)
            for slide, ctx in zip(slides, slide_contexts)
            if slide.get("indexable", True)
        ]
        if not indexable_pairs:
            return 0

        ids = []
        documents = []
        metadatas = []

        for slide, ctx in indexable_pairs:
            # 用 source_file + slide_number 做唯一 ID
            doc_id = f"{slide['source_file']}_slide_{slide['slide_number']}"
            ids.append(doc_id)

            # 把 title 和 content 拼合作为基础文本，title 权重更高；
            # content 里已经包含了有 caption 的图片描述，天然参与语义检索
            base_text = MinerUParser.build_index_text(
                slide, include_image_captions=False
            )

            # contextual retrieval：如果生成了 context 摘要，prepend 到
            # embedding 文本最前面，帮单张 slide 补上"这属于哪份方案、
            # 大致章节位置"的上下文，缓解单张 slide 脱离上下文检索不到的问题。
            # 摘要为空（未启用 / 生成失败）时就是原来的纯文本，不受影响。
            context_summary = ctx.get("context_summary", "")
            text = f"{context_summary}\n{base_text}" if context_summary else base_text
            documents.append(text)

            # Chroma metadata 只支持标量类型，images 是列表所以序列化成
            # JSON 字符串存，取出时用 json.loads 还原（见 search()）
            metadatas.append({
                "slide_number": slide["slide_number"],
                "title": slide["title"],
                "source_file": slide["source_file"],
                "file_hash": file_hash,
                "images": json.dumps(slide.get("images", []), ensure_ascii=False),
                # 方案类型 / 客户行业：用于检索时按类型过滤（相当于低成本的
                # 大纲引导式检索）。缺失时留空字符串，而不是不写这个 key，
                # 保证 Chroma metadata 结构一致，方便 where 过滤查询。
                "proposal_type": ctx.get("proposal_type", ""),
                "client_industry": ctx.get("client_industry", ""),
                # 文档类型：policy（政策参考）/ proposal（方案/技术资料）
                "doc_type": doc_type,
            })

        self._collection.add(
            documents=documents,
            ids=ids,
            metadatas=metadatas,
        )
        print(f"  入库 {len(ids)} 个 slide")
        return len(ids)

    def update_slide_contexts(self, slides: list[SlideDict], contexts: list[dict]) -> None:
        """批量更新一批已入库 slide 的 context 摘要 + metadata。

        配合"先裸存原始内容，LLM 全部生成完再一次性批量写回"的分阶段
        ingest 流程：解析结果已经安全落库在先；LLM 生成阶段仍然是逐张
        调用（context 是 slide 专属内容，没法合并成一次调用），但写回
        数据库这一步合并成一次批量操作，不是生成一张就调一次 Chroma。

        用 Chroma 的 update() 而不是重新 add()——实测过 Chroma 的
        metadata 更新是按字段合并、不是整条替换，这里只传两个业务字段，
        不会把 add_slides 时写入的 slide_number / title 等字段冲掉。

        Args:
            slides: 要更新的 slide 列表（与 contexts 按下标一一对应，
                通常是本次 ingest 里 context 生成成功的那些 slide——
                生成失败的不要传进来，让它们保留裸存时的原始内容）
            contexts: 每个 slide 对应的 llm_client.generate_slide_context()
                返回值
        """
        if not slides:
            return

        indexable_pairs = [
            (slide, ctx)
            for slide, ctx in zip(slides, contexts)
            if slide.get("indexable", True)
        ]
        if not indexable_pairs:
            return

        ids = []
        documents = []
        metadatas = []

        for slide, ctx in indexable_pairs:
            doc_id = f"{slide['source_file']}_slide_{slide['slide_number']}"
            # 复用 parser 的纯文本拼接规则，确保离线预览与最终更新文本一致。
            base_text = MinerUParser.build_index_text(slide)

            context_summary = ctx.get("context_summary", "")
            text = f"{context_summary}\n{base_text}" if context_summary else base_text

            ids.append(doc_id)
            documents.append(text)
            metadatas.append({
                "proposal_type": ctx.get("proposal_type", ""),
                "client_industry": ctx.get("client_industry", ""),
                # 把更新后的 images（含真实 caption）写回，覆盖 add_slides 时
                # 存的空 caption 版本——Chroma metadata 是按字段合并，不传这个
                # key 的话旧的空 caption 版本会一直留着
                "images": json.dumps(slide.get("images", []), ensure_ascii=False),
            })

        self._collection.update(ids=ids, documents=documents, metadatas=metadatas)
        print(f"  批量更新 {len(ids)} 个 slide 的 context/metadata")

    def get_existing_hash(self, source_file: str) -> str | None:
        """查询某个源文件当前库里存的内容哈希（版本标识）。

        同一个 source_file 的所有 slide 入库时写入的是同一个 file_hash，
        取任意一条即可代表这份 pptx 上次入库时的版本。

        Returns:
            存在则返回哈希字符串；这个源文件还没入库过则返回 None。
        """
        result = self._collection.get(
            where={"source_file": source_file},
            limit=1,
            include=["metadatas"],
        )
        metadatas = result.get("metadatas", [])
        if not metadatas:
            return None
        return metadatas[0].get("file_hash")

    def delete_source(self, source_file: str) -> int:
        """删除某个源文件名下的所有 slide，用于重新入库前清空旧版本。

        Returns:
            实际删除的 slide 数量
        """
        result = self._collection.get(where={"source_file": source_file}, include=[])
        ids = result.get("ids", [])
        if ids:
            self._collection.delete(ids=ids)
        return len(ids)

    def search(
        self,
        query: str,
        n_results: int = 10,
        proposal_type: str | None = None,
        client_industry: str | None = None,
        include_policy: bool = False,
    ) -> list[dict]:
        """按语义检索，返回最相关的 slide，可选按 metadata 过滤。

        Args:
            query: 检索关键词或自然语言查询
            n_results: 返回结果数量，默认 10
            proposal_type: 可选，按方案类型过滤（如"光伏"），需要与入库时
                写入的 proposal_type 完全匹配
            client_industry: 可选，按客户行业过滤，同上
            include_policy: 是否包含政策参考类文档（doc_type="policy"）。
                默认 False，检索时自动过滤掉政策类，只在方案/技术资料
                中检索；传 True 时不做 doc_type 过滤，检索全部内容。

        Returns:
            按相关性排序的 slide 列表，每个包含完整元信息
        """
        where = self._build_where(proposal_type, client_industry, include_policy)

        results = self._collection.query(
            query_texts=[query],
            n_results=n_results,
            where=where,
        )

        ids = results.get("ids", [[]])[0]
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        slides = []
        for i, doc_id in enumerate(ids):
            meta = metadatas[i] if i < len(metadatas) else {}
            slides.append(self._slide_from_meta(meta, documents[i] if i < len(documents) else "", distances[i] if i < len(distances) else None))

        return slides

    def get_adjacent_slides(
        self, source_file: str, slide_number: int, window: int = 1
    ) -> list[dict]:
        """获取某个源文件里，指定 slide 前后 window 张的内容。

        用于检索命中之后的"相邻 slide 扩展"：单张 slide 有时候信息不够
        撑起生成时需要的上下文，把前后几张一起带出来补充。

        Args:
            source_file: 目标 slide 所属的源文件名
            slide_number: 目标 slide 的页码（中心点，不包含在返回结果里）
            window: 前后各取几张，默认 1（即前一张 + 后一张）

        Returns:
            按 slide_number 升序排列的相邻 slide 列表（不含中心 slide 本身，
            调用方如果需要把中心 slide 也拼进去，自己在外面加）
        """
        target_numbers = [
            n
            for n in range(slide_number - window, slide_number + window + 1)
            if n != slide_number and n > 0
        ]
        if not target_numbers:
            return []

        result = self._collection.get(
            where={
                "$and": [
                    {"source_file": source_file},
                    {"slide_number": {"$in": target_numbers}},
                ]
            },
            include=["documents", "metadatas"],
        )

        documents = result.get("documents", [])
        metadatas = result.get("metadatas", [])

        slides = [
            self._slide_from_meta(meta, documents[i] if i < len(documents) else "")
            for i, meta in enumerate(metadatas)
        ]
        slides.sort(key=lambda s: s["slide_number"])
        return slides

    def list_sources(self) -> list[str]:
        """列出库里已入库的所有源文件名。"""
        all_data = self._collection.get(include=["metadatas"])
        metadatas = all_data.get("metadatas", [])
        sources = set()
        for meta in metadatas:
            if meta and "source_file" in meta:
                sources.add(meta["source_file"])
        return sorted(sources)
    
    
    def get_all_slides(self, source_file: str) -> list[dict]:
        """按 source_file 取出已入库的全部 slide，按 slide_number 升序排列。

        用于在不重新调用 MinerU 的情况下，对已入库内容补跑 context/metadata
        生成——比如这次的情况：当初入库时用的是没有 context 增强的旧逻辑，
        事后想补上这一步，不需要重新解析原始 pptx。

        注意：这里取回的 content 字段是入库时存的 document 文本（title+content
        拼接后的结果），跟 parser 刚解析出来时 title/content 分离的干净格式
        略有出入，不影响 generate_slide_context 使用，只是 title 会在 content
        里重复出现一次，属于可接受的小瑕疵。
        """
        result = self._collection.get(
            where={"source_file": source_file},
            include=["documents", "metadatas"],
        )
        documents = result.get("documents", [])
        metadatas = result.get("metadatas", [])
        slides = [
            self._slide_from_meta(meta, documents[i] if i < len(documents) else "")
            for i, meta in enumerate(metadatas)
        ]
        slides.sort(key=lambda s: s["slide_number"])
        return slides


    def count(self) -> int:
        """返回库中 slide 总数。"""
        try:
            return self._collection.count()
        except Exception as e:
            print("Vector DB corrupted:")
            print(e)
            return -1


    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _build_where(
        proposal_type: str | None,
        client_industry: str | None,
        include_policy: bool = False,
    ) -> dict | None:
        """把可选的过滤条件拼成 Chroma 的 where 子句，没有条件时返回 None。

        Args:
            proposal_type: 按方案类型精确匹配
            client_industry: 按客户行业精确匹配
            include_policy: 是否包含政策参考类。默认 False 时会追加
                doc_type="proposal" 的过滤条件，排除政策类文档。
        """
        conditions = []
        if proposal_type:
            conditions.append({"proposal_type": proposal_type})
        if client_industry:
            conditions.append({"client_industry": client_industry})
        # 默认过滤掉政策类文档，只在方案/技术资料中检索
        if not include_policy:
            conditions.append({"doc_type": "proposal"})

        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    @staticmethod
    def _slide_from_meta(meta: dict, content: str, distance: float | None = None) -> dict:
        """把 Chroma 返回的一条 metadata + document 组装成统一的 slide 字典。

        search() 和 get_adjacent_slides() 共用这个组装逻辑，避免重复代码、
        避免两处字段名不小心写歪。
        """
        # images 存的时候序列化成了 JSON 字符串，这里还原成列表；
        # 万一某条旧数据里没有这个字段或者格式不对，兜底给空列表
        images_raw = meta.get("images", "[]")
        try:
            images = json.loads(images_raw) if images_raw else []
        except (json.JSONDecodeError, TypeError):
            images = []

        return {
            "slide_number": meta.get("slide_number", 0),
            "title": meta.get("title", ""),
            "content": content,
            "source_file": meta.get("source_file", ""),
            "images": images,
            "proposal_type": meta.get("proposal_type", ""),
            "client_industry": meta.get("client_industry", ""),
            "doc_type": meta.get("doc_type", "proposal"),
            "distance": distance,
        }
