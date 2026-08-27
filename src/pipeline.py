# pipeline.py
"""方案知识库业务编排逻辑：串联 parser → vector_store → llm_client。

被 main.py 调用，本身不处理命令行参数——那部分留在 main.py，
这里只负责 ingest / query / status 这几条业务流程怎么串起来。
"""

import csv
import re
import sys
from pathlib import Path

from src.adapters.parser import MinerUParser
from src.adapters.vector_store import VectorStore
from src.adapters.llm_client import LLMClient
from src.config import CHROMA_PATH, INGEST_MANIFEST_PATH
from src.domain import build_document_identity
from src.hybrid_v2 import HybridTestStore
from src.query_result import Citation, QueryResult, SearchHit
from src.retrieval import (
    TYPE_MATCH_RULES,
    explicit_requested_type,
    metadata_filter_values,
    rerank_candidates,
    select_supporting_context,
)

# ingest_manifest.csv 路径：用于判断文件的 doc_type（policy / proposal）
_MANIFEST_PATH = INGEST_MANIFEST_PATH
_manifest_cache: dict[str, str] | None = None

# 相邻 slide 扩展：命中的 slide 前后各带几张，见 claude.md 检索流程第 4 步
#！！！！后期可以思考一下，有没有更好的减少上下文损益的方式
_ADJACENT_WINDOW = 1
_CANDIDATE_MULTIPLIER = 3
_TYPE_FILTER_MIN_RESULTS = 2


def _get_doc_type(filename: str) -> str:
    """从 ingest_manifest.csv 查询文件的 doc_type。

    先按 rel_path 匹配（含子目录层级），再按 filename 兜底匹配。
    manifest 不存在或找不到记录时，默认返回 "proposal"。
    """
    global _manifest_cache
    if _manifest_cache is None:
        _manifest_cache = {}
        if _MANIFEST_PATH.exists():
            with open(_MANIFEST_PATH, newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    # rel_path 优先（含子目录层级，更精确）
                    rel = row.get("rel_path", "").replace("\\", "/")
                    if rel:
                        _manifest_cache[rel] = row.get("doc_type", "proposal")
                    # 再记一份 filename 兜底
                    fn = row.get("filename", "")
                    if fn:
                        _manifest_cache[fn] = row.get("doc_type", "proposal")

    if _manifest_cache:
        return _manifest_cache.get(filename, "proposal")
    return "proposal"

# 生成方案初稿时使用的系统提示词。核心约束：只能基于参考资料改写，不能
# 编造参考资料里没有的具体数据/型号——这类工程方案文档如果被模型瞎编
# 技术参数，后果比检索不到更糟。每段标注来源，方便方案员回头核对原始设计。
_GENERATION_SYSTEM_PROMPT = """你是康晋电气的方案撰写助手，负责基于历史方案库中的相关内容，
为新的客户需求撰写方案初稿。

要求：
- 内容基于下面提供的参考资料改写、整合，不要编造参考资料中没有出现的具体数据、型号、参数
- 保持专业、简洁的方案文档语气
- 按逻辑结构组织内容（如：项目背景、技术方案、系统组成、优势亮点等），不必完全照搬参考资料的原始顺序
- 在每个段落末尾用严格的 [来源: <文件名>, 第 <页码> 页] 格式标注改写自哪份参考资料，方便后续核对原始设计
- **重要**：引用必须从下方"可引用来源集合"中逐字选择文件名和页码，不得拼接不同来源或编造不存在的组合"""


def ingest(pptx_path: str) -> None:
    """解析一份 PPTX，生成 context/metadata 增强后存入向量库。

    内容没变化（哈希一致）会直接跳过，不重复调用 MinerU / embedding / LLM；
    内容变了会先完整解析出新版本，生成 context 后，再清空旧版本、插入新
    版本，保证库里同一个文件永远只有一份最新数据。
    """
    pptx_file = Path(pptx_path)
    if not pptx_file.exists():
        print(f"错误：文件不存在: {pptx_file}")
        sys.exit(1)

    print(f"=== 开始入库: {pptx_file.name} ===")

    # 第零步：算文件哈希，跟库里记录的上次入库版本比对
    file_hash = MinerUParser.compute_file_hash(pptx_file)
    store = VectorStore()
    existing_hash = store.get_existing_hash(pptx_file.name)

    if existing_hash == file_hash:
        print("  内容未变化（hash 一致），跳过入库，不调用 MinerU / embedding / LLM")
        print(f"  库中总计: {store.count()} 个 slide")
        return

    # 从 manifest 获取文档类型（policy / proposal）
    doc_type = _get_doc_type(pptx_file.name)
    if doc_type == "policy":
        print(f"  [政策参考类] 将标记为 doc_type=policy（检索时默认过滤）")

    # 第一步：MinerU 解析。先解析成功拿到完整数据，再动 Chroma——
    # 万一解析中途失败，旧数据还留着，不会出现"删了旧的、新的又没进来"的空档
    print("\n[1/3] 调用 MinerU 解析 PPTX...")
    parser = MinerUParser()
    parsed_pages = parser.parse_document(pptx_file)
    print(f"  解析完成，共 {len(parsed_pages)} 个页面")

    if not parsed_pages:
        print("  警告：没有提取到任何文字内容")
        return

    slides = [page for page in parsed_pages if page.get("indexable", True)]
    skipped_pages = len(parsed_pages) - len(slides)
    if skipped_pages:
        print(f"  已识别并跳过 {skipped_pages} 个目录页，不写入向量索引")
    if not slides:
        print("  警告：没有可写入向量索引的页面，保留已有库内容")
        return

    # 打印前几个 slide 预览，顺带看一眼图片提取情况
    for s in slides[:3]:
        preview = s["content"][:60] + "..." if len(s["content"]) > 60 else s["content"]
        img_count = len(s.get("images", []))
        img_note = f"（含 {img_count} 张图片）" if img_count else ""
        print(f"  Slide {s['slide_number']}: {s['title'] or '(无标题)'}{img_note} — {preview}")
    if len(slides) > 3:
        print(f"  ... 以及 {len(slides) - 3} 个更多 slide")

    # 第二步：先把原始内容裸存进向量库，不等 context 生成。MinerU 解析
    # 这一步慢、还调用外部服务，容易出问题——解析结果先落库，即使接下来
    # 生成 context 的步骤中途失败，这次解析换来的结果也不会丢，不需要
    # 重新调用 MinerU。
    print("\n[2/3] 存入向量库（原始内容）...")
    if existing_hash is not None:
        deleted = store.delete_source(pptx_file.name)
        print(f"  检测到内容更新，已清空旧版本 {deleted} 个 slide")

    count = store.add_slides(slides, file_hash=file_hash, doc_type=doc_type)
    print(f"  已存入 {count} 个 slide（doc_type={doc_type}，尚未生成 context 增强）")

    # 第三步：逐张生成 context/metadata（LLM 调用逐张进行，没法合并），
    # 全部生成完之后一次性批量 update 回 Chroma——不是生成一张存一张。
    # 单张生成失败只影响这一张（保留裸存内容），不影响其他 slide。
    print(f"\n[3/3] 生成 slide context 与 metadata（共 {len(slides)} 次 LLM 调用）...")
    _annotate_slides(store, slides)

    print(f"\n=== 入库完成 ===")
    print(f"  库中总计: {store.count()} 个 slide")


def _annotate_slides(store: VectorStore, slides: list[dict]) -> None:
    """逐张生成 context/图片 caption，全部生成完之后一次性批量写回向量库。

    文字 context 和图片 caption 是两条独立链路，分别调用、分别处理失败：
    任何一条失败都不影响另一条继续跑。图片 caption 这条链路内部粒度更细——
    一张 slide 可能有多张图片，其中某几张生成失败，不影响同一 slide 里
    其他图片继续生成（每张图片独立失败、独立兜底为空字符串）。

    只有当这张 slide 的文字 context 生成失败、且图片 caption 一张都没成功
    （包括本来就没有图片的情况）时，才判定为完全没有产出，跳过写回，
    保留裸存时的原始内容。
    """
    llm = LLMClient()
    # 只取标题，不传全文，控制 token 成本——用作"文档大纲"上下文，
    # 帮模型判断每张 slide 大致在方案里的章节位置
    outline_titles = [s["title"] for s in slides]

    succeeded_slides = []
    succeeded_contexts = []
    skipped_count = 0

    for i, slide in enumerate(slides, 1):
        print(f"  [{i}/{len(slides)}] Slide {slide['slide_number']} 处理中...")
        ctx = None
        try:
            ctx = llm.generate_slide_context(slide, outline_titles)
        except Exception as e:  # noqa: BLE001
            print(f"    警告：文字 context 生成失败（{e}）")

        captions_updated = False
        if slide.get("images"):
            try:
                captions = llm.generate_image_captions(slide)
                for img, generated_caption in zip(slide["images"], captions):
                    # 视觉模型成功时覆盖 MinerU caption；失败时保留原始 caption。
                    if generated_caption:
                        img["caption"] = generated_caption
                    if img.get("caption"):
                        captions_updated = True
            except Exception as e:  # noqa: BLE001
                print(f"    警告：图片 caption 生成失败（{e}）")
                # 即使整次视觉调用失败，也让已有 MinerU caption 作为 fallback
                # 写回索引文本，而不是把图片说明完全丢掉。
                captions_updated = any(img.get("caption") for img in slide["images"])
        
        if ctx is None and not captions_updated:
            skipped_count += 1
            print("    文字和图片均无产出，该 slide 保留原始内容，跳过增强")
            continue

        succeeded_slides.append(slide)
        succeeded_contexts.append(ctx or {})

    if succeeded_slides:
        store.update_slide_contexts(succeeded_slides, succeeded_contexts)

    success = len(succeeded_slides)
    print(f"  处理完成（{success}/{len(slides)} 张 slide 有更新，已批量写回，{skipped_count} 张跳过）")

def annotate(source_file: str) -> None:
    """对已入库但还没做 context 增强的文件，补跑 LLM context/metadata 生成。

    不调用 MinerU，直接从 Chroma 里已存的内容重新生成 context，适用于
    "当初用旧逻辑裸存入库、现在想补上增强"这种场景。
    """
    store = VectorStore()
    slides = store.get_all_slides(source_file)

    if not slides:
        print(f"错误：库中没有找到 {source_file} 的任何 slide")
        return

    print(f"=== 对 {source_file} 补跑 context 增强（共 {len(slides)} 个 slide）===")
    _annotate_slides(store, slides)
    print("=== 完成 ===")


def _build_context_for_generation(results: list[dict]) -> str:
    """把检索结果（含相邻 slide）拼成 LLM 生成时可用的参考资料文本。

    每条参考资料标注来源文件+页码，方便生成时模型在段末标注引用，
    也方便方案员后续核对某段内容具体改写自哪份历史方案的哪一页。
    """
    blocks = []
    for i, r in enumerate(results, 1):
        lines = [f"### 参考{i}：{r['source_file']} - Slide {r['slide_number']}"]
        if r["title"]:
            lines.append(f"标题：{r['title']}")
        lines.append(f"内容：\n{r['content']}")

        images = r.get("images") or []
        if images:
            img_captions = "; ".join(img.get("caption") or "无说明" for img in images)
            lines.append(f"（该页含 {len(images)} 张图片素材：{img_captions}）")

        adjacent = r.get("adjacent") or []
        if adjacent:
            adj_text = "\n".join(
                f"  - Slide {a['slide_number']}（{a['title'] or '无标题'}）：{a['content'][:150]}"
                for a in adjacent
            )
            lines.append(f"相邻页补充上下文：\n{adj_text}")

        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def _generate_proposal_draft(llm: LLMClient, need_description: str, results: list[dict]) -> str:
    """基于检索到的历史 slide 内容，生成一份方案初稿。

    会把本次实际检索到的 slide 编号集合显式列在 prompt 里，约束模型只能
    从该集合中选择引用编号，防止 citation hallucination（编造不存在的编号）。
    """
    context = _build_context_for_generation(results)

    # 收集本次上下文中所有可引用的 slide 编号（主结果 + 相邻扩展）
    available_numbers = set()
    for r in results:
        available_numbers.add(r["slide_number"])
        for a in r.get("adjacent") or []:
            available_numbers.add(a["slide_number"])
    available_list = ", ".join(f"Slide {n}" for n in sorted(available_numbers))

    prompt = (
        f"客户需求描述：\n{need_description}\n\n"
        f"可引用编号集合：{available_list}\n\n"
        f"可参考的历史方案内容：\n{context}\n\n"
        f"请基于以上参考资料，撰写一份方案初稿。"
    )
    return llm.generate(prompt, system_prompt=_GENERATION_SYSTEM_PROMPT)


def _validate_citations(draft: str, results: list[dict]) -> list[dict]:
    """校验生成稿中的 Slide 引用编号是否都在实际检索到的集合内。

    正则提取所有 "Slide N" 形式的引用，与 available_numbers 做差集比对。
    发现不在集合内的编号时打印警告，不自动删除或重新生成。

    Returns:
        非法引用列表，每项为 {"slide_number": int, "context_snippet": str}
    """
    available_numbers = set()
    for r in results:
        available_numbers.add(r["slide_number"])
        for a in r.get("adjacent") or []:
            available_numbers.add(a["slide_number"])

    # 匹配 "Slide 123" 形式（不区分大小写）
    pattern = re.compile(r"[Ss]lide\s+(\d+)")
    cited_numbers = set(int(m) for m in pattern.findall(draft))

    invalid = cited_numbers - available_numbers
    if invalid:
        print("\n⚠  引用校验发现非法 Slide 编号：")
        for num in sorted(invalid):
            # 找到该编号在生成稿中的上下文片段（前后各 30 字符）
            for m in pattern.finditer(draft):
                if int(m.group(1)) == num:
                    start = max(0, m.start() - 30)
                    end = min(len(draft), m.end() + 30)
                    snippet = draft[start:end].replace("\n", " ")
                    print(f"  - Slide {num}  （上下文：…{snippet}…）")
                    break

    return [{"slide_number": n, "context_snippet": ""} for n in sorted(invalid)]


def query(need_description: str, top_n: int = 10) -> None:
    """完整检索链路：query 改写 → metadata 过滤 → 向量检索 → 相邻 slide 扩展 → 生成初稿。"""
    print(f"=== 检索: \"{need_description}\" ===")

    store = VectorStore()
    total = store.count()
    if total == 0:
        print("库为空，请先用 ingest 命令入库")
        return

    # 第一步：query 改写。把需求描述转成 1-3 个更适合检索的 query，
    # 顺带尝试判断方案类型，用于后面的 metadata 过滤
    print("\n[1/4] 改写检索 query...")
    llm = LLMClient()
    rewrite = llm.rewrite_query(need_description)
    queries = rewrite["queries"]
    proposal_type = rewrite["proposal_type"] or None
    print(f"  改写为 {len(queries)} 个 query: {queries}")
    if proposal_type:
        print(f"  推测方案类型: {proposal_type}（将用于过滤）")

    # 第二步：向量检索。每个改写后的 query 各自查一遍，按 slide 去重合并，
    # 同一张 slide 被多个 query 命中时，保留距离最小（最相关）的一条。
    # 目前 <100 份文档规模，纯语义检索够用，不接 hybrid（见 claude.md）。
    print("\n[2/4] 向量检索...")
    merged: dict[str, dict] = {}
    for q in queries:
        for r in store.search(q, n_results=top_n, proposal_type=proposal_type):
            key = f"{r['source_file']}_slide_{r['slide_number']}"
            existing = merged.get(key)
            if existing is None or (r["distance"] or 0) < (existing["distance"] or 0):
                merged[key] = r

    results = sorted(merged.values(), key=lambda r: r["distance"] or 0)[:top_n]

    # 加了 proposal_type 过滤却一无所获时，大概率是入库时打的标签措辞
    # 跟这次猜的类型对不上（两次都是 LLM 自由生成的文本，没法保证完全
    # 一致）。这种情况下直接放弃过滤、退化成纯语义检索兜底，而不是让
    # 用户看到"未找到相关结果"——精确匹配失败不代表内容真的不相关。

    if not results and proposal_type:
        print(f"  按类型「{proposal_type}」过滤未命中，改为不限类型重新检索...")
        merged = {}
        for q in queries:
            for r in store.search(q, n_results=top_n, proposal_type=None):
                key = f"{r['source_file']}_slide_{r['slide_number']}"
                existing = merged.get(key)
                if existing is None or (r["distance"] or 0) < (existing["distance"] or 0):
                    merged[key] = r
        results = sorted(merged.values(), key=lambda r: r["distance"] or 0)[:top_n]

    if not results:
        print("未找到相关结果")
        return
    print(f"  合并去重后共 {len(results)} 个相关 slide")

    # 第三步：相邻 slide 扩展。命中的 slide 前后各带 _ADJACENT_WINDOW 张，
    # 补足生成时需要的上下文
    print("\n[3/4] 相邻 slide 扩展...")
    for r in results:
        r["adjacent"] = store.get_adjacent_slides(
            r["source_file"], r["slide_number"], window=_ADJACENT_WINDOW
        )

    print(f"\n找到 {len(results)} 个相关 slide（按相关性排序）:\n")
    for i, r in enumerate(results, 1):
        dist = f"{r['distance']:.4f}" if r.get("distance") is not None else "N/A"
        print(f"--- 结果 {i} (距离: {dist}) ---")
        print(f"  来源: {r['source_file']} | Slide {r['slide_number']}")
        if r["title"]:
            print(f"  标题: {r['title']}")
        if r.get("proposal_type"):
            print(f"  方案类型: {r['proposal_type']}")

        # 内容太长时截断显示
        content = r["content"]
        if len(content) > 300:
            content = content[:300] + "..."
        print(f"  内容: {content}")

        # 这个 slide 对应的图片素材，方案员可以直接拿去用
        images = r.get("images") or []
        if images:
            print(f"  图片素材（{len(images)} 张）:")
            for img in images:
                caption = img.get("caption") or "(无标题)"
                print(f"    - {img['path']}  [{caption}]")

        # 相邻 slide，供后续生成阶段补充上下文用
        adjacent = r.get("adjacent") or []
        if adjacent:
            print(f"  相邻 slide（{len(adjacent)} 张，供生成时补充上下文）:")
            for a in adjacent:
                a_preview = (
                    a["content"][:60] + "..." if len(a["content"]) > 60 else a["content"]
                )
                print(f"    - Slide {a['slide_number']}: {a['title'] or '(无标题)'} — {a_preview}")
        print()

    # 第四步：基于检索结果（含相邻 slide 补充的上下文），调用 LLM 生成方案初稿。
    # 只在真正拿到检索结果时才生成，避免空转浪费一次 LLM 调用。
    print("\n[4/4] 生成方案初稿...")
    draft = _generate_proposal_draft(llm, need_description, results)

    # 第五步：校验生成稿中的 Slide 引用编号是否合法
    invalid_refs = _validate_citations(draft, results)

    print("\n" + "=" * 60)
    print("方案初稿：")
    print("=" * 60)
    print(draft)


def status() -> None:
    """查看向量库当前状态。"""
    store = VectorStore()
    total = store.count()
    sources = store.list_sources()

    print("=== 向量库状态 ===")
    print(f"  Slide 总数: {total}")
    if sources:
        print(f"  已入库文件 ({len(sources)} 个):")
        for s in sources:
            print(f"    - {s}")
    else:
        print("  暂无入库文件")


# 查询输出模型化后，以下函数成为 query() 的实际实现；上方的旧辅助函数保留
# 在这一批中不再调用，避免把已有入库/查询策略改动扩散到无关路径。
def _hit_from_record(record: dict, context_role: str) -> SearchHit:
    """将现有 Chroma 记录的 slide_number 映射为通用 page_number。"""
    metadata = {
        key: value for key, value in record.items()
        if key not in {"source_file", "slide_number", "title", "content", "distance", "images", "adjacent"}
    }
    return SearchHit(
        source_file=record["source_file"],
        page_number=record["slide_number"],
        title=record.get("title", ""),
        content=record.get("content", ""),
        distance=record.get("distance"),
        images=record.get("images") or [],
        context_role=context_role,
        metadata=metadata,
    )


def _citations_from_hits(hits: list[SearchHit]) -> list[Citation]:
    """按文件名和页码去重，保留主命中和相邻页全部来源。"""
    citations: list[Citation] = []
    seen: set[tuple[str, int]] = set()
    for hit in hits:
        identity = (hit.source_file, hit.page_number)
        if identity not in seen:
            citations.append(Citation(hit.source_file, hit.page_number, hit.title, tuple(hit.images)))
            seen.add(identity)
    return citations


def _build_structured_context(hits: list[SearchHit]) -> str:
    """以统一引用格式组织生成上下文。"""
    blocks = []
    for index, hit in enumerate(hits, 1):
        role = "主命中" if hit.context_role == "primary" else "补充上下文"
        lines = [f"### 参考{index}（{role}）：[来源: {hit.source_file}, 第 {hit.page_number} 页]"]
        if hit.title:
            lines.append(f"标题：{hit.title}")
        lines.append(f"内容：\n{hit.content}")
        if hit.images:
            captions = "; ".join(image.get("caption") or "无说明" for image in hit.images)
            lines.append(f"（该页含 {len(hit.images)} 张图片素材：{captions}）")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _validate_structured_citations(draft: str, citations: list[Citation]) -> list[str]:
    """只接受本次 hits 中真实存在的“文件名 + 页码”引用组合。"""
    available = {(citation.source_file, citation.page_number) for citation in citations}
    pattern = re.compile(r"\[来源:\s*(?P<source>.+),\s*第\s*(?P<page>\d+)\s*页\]")
    warnings = []
    for match in pattern.finditer(draft):
        identity = (match.group("source").strip(), int(match.group("page")))
        if identity not in available:
            warnings.append(f"引用不在本次检索来源中：{match.group(0)}")
    return list(dict.fromkeys(warnings))


def render_markdown(result: QueryResult) -> str:
    """将 QueryResult 渲染为 CLI 与文件可复用的 Markdown。"""
    lines = ["# 方案查询结果", "", "## 用户问题", "", result.original_query, ""]
    if result.rewritten_queries:
        lines.extend(["## 检索查询", ""])
        lines.extend(f"- {item}" for item in result.rewritten_queries)
        lines.append("")
    lines.extend(["## 方案正文", "", result.proposal_markdown or "（未生成方案正文）", "", "## 来源列表", ""])
    if result.citations:
        for citation in result.citations:
            title_note = f" — {citation.title}" if citation.title else ""
            image_note = f"；图片 {len(citation.images)} 张" if citation.images else ""
            lines.append(f"- {citation.display()}{title_note}{image_note}")
    else:
        lines.append("（本次没有命中可引用页面）")
    if result.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in result.warnings)
    return "\n".join(lines).rstrip() + "\n"


def query(need_description: str, top_n: int = 10) -> QueryResult:
    """两阶段召回与纯本地轻量重排，返回渠道无关的结构化结果。"""
    store = VectorStore()
    total = store.count()
    if total == 0:
        return QueryResult(need_description, [], [], "", [], ["知识库为空，请先入库后再查询。"], {"total_documents": total})

    llm = LLMClient()
    rewrite = llm.rewrite_query(need_description)
    queries = rewrite["queries"]
    requested_type, matched_alias = explicit_requested_type(need_description)
    candidate_limit = max(top_n, top_n * _CANDIDATE_MULTIPLIER)
    filter_values = metadata_filter_values(requested_type, matched_alias)

    def search_with(filter_values: list[str] | None) -> list[dict]:
        merged: dict[str, dict] = {}
        for query_text in queries:
            for filter_value in filter_values or [None]:
                for record in store.search(
                    query_text, n_results=candidate_limit, proposal_type=filter_value
                ):
                    key = f"{record['source_file']}_slide_{record['slide_number']}"
                    existing = merged.get(key)
                    if existing is None or (record["distance"] or 0) < (existing["distance"] or 0):
                        merged[key] = record
        return list(merged.values())

    type_filter_attempted = bool(filter_values)
    filtered_candidates = search_with(filter_values) if type_filter_attempted else []
    fallback_used = False
    fallback_used = type_filter_attempted and len(filtered_candidates) < min(top_n, _TYPE_FILTER_MIN_RESULTS)
    if not type_filter_attempted or fallback_used:
        candidates = search_with(None)
    else:
        candidates = filtered_candidates
    primary_records = rerank_candidates(
        candidates,
        "\n".join([need_description, *queries]),
        requested_type,
        top_n,
    )
    retrieval_metadata = {
        "total_documents": total,
        "candidate_limit": candidate_limit,
        "user_explicit_type_requested": bool(requested_type),
        "requested_type": requested_type,
        "requested_type_alias": matched_alias,
        "rewrite_proposal_type": rewrite.get("proposal_type", ""),
        "type_filter_attempted": type_filter_attempted,
        "type_filter_values": filter_values,
        "type_filter_candidate_count": len(filtered_candidates),
        "type_filter_fallback": fallback_used,
        "type_match_rules": list(TYPE_MATCH_RULES),
        "primary_hit_count": len(primary_records),
    }
    if not primary_records:
        return QueryResult(need_description, queries, [], "", [], ["未找到相关来源。"], retrieval_metadata)

    adjacent_by_primary = {
        (record["source_file"], record["slide_number"]): store.get_adjacent_slides(
            record["source_file"], record["slide_number"], window=_ADJACENT_WINDOW
        )
        for record in primary_records
    }
    primary_records, supporting_records = select_supporting_context(
        primary_records,
        adjacent_by_primary,
        "\n".join([need_description, *queries]),
    )
    retrieval_metadata["supporting_hit_count"] = len(supporting_records)
    retrieval_metadata["context_selection"] = {
        "adjacent_window": _ADJACENT_WINDOW,
        "max_pages_per_file": 4,
        "max_total_pages": 14,
        "max_total_characters": 16_000,
    }
    hits = [_hit_from_record(record, "primary") for record in primary_records]
    hits.extend(_hit_from_record(record, "supporting") for record in supporting_records)
    citations = _citations_from_hits(hits)
    available_sources = "\n".join(citation.display() for citation in citations)
    prompt = (
        f"客户需求描述：\n{need_description}\n\n可引用来源集合：\n{available_sources}\n\n"
        f"可参考的历史方案内容：\n{_build_structured_context(hits)}\n\n请基于以上参考资料，撰写一份方案初稿。"
    )
    draft = llm.generate(prompt, system_prompt=_GENERATION_SYSTEM_PROMPT)
    return QueryResult(
        need_description,
        queries,
        hits,
        draft,
        citations,
        (["proposal_type 精确过滤候选不足，已回退到不过滤类型的候选集。"] if fallback_used and type_filter_attempted else [])
        + _validate_structured_citations(draft, citations),
        retrieval_metadata,
    )


def ingest_v2(source_path: str | Path, *, source_root: str | Path, test_db: str | Path) -> dict:
    """Parse into the explicitly selected disposable V2 library; never opens old Chroma."""
    source = Path(source_path)
    root = Path(source_root)
    if not source.is_file():
        raise ValueError(f"V2 入库只接受文件: {source}")
    if not root.is_dir():
        raise ValueError(f"V2 必须显式指定存在的 --source-root: {root}")
    if Path(test_db).resolve() == CHROMA_PATH.resolve():
        raise ValueError("--test-db 不能指向旧 CHROMA_PATH；请指定独立、可删除的 V2 测试目录")
    identity = build_document_identity(source, root)
    store = HybridTestStore(test_db)
    try:
        existing = store.existing_version(identity.document_id)
        if existing == identity.version_id:
            return {"status": "skipped", "source_key": identity.source_key, "version_id": identity.version_id, "message": "内容未变化，跳过 V2 入库。"}
        parsed = MinerUParser().parse_document(source)
        pages = []
        for page in parsed.to_legacy_pages():
            copied = dict(page)
            copied["page_number"] = copied.get("page_number", copied.get("slide_number"))
            pages.append(copied)
        written = store.add_version(identity, pages, _get_doc_type(identity.source_key))
        return {"status": "updated" if existing else "created", "source_key": identity.source_key, "version_id": identity.version_id, "pages": written, "message": "新版本已成功写入；旧版本随后删除。" if existing else "已写入独立 V2 测试库。"}
    finally:
        store.close()


def query_v2(query_text: str, *, test_db: str | Path, top_n: int = 10) -> QueryResult:
    """Return V2 hybrid retrieval results without query rewriting or LLM generation."""
    store = HybridTestStore(test_db)
    try:
        primary = store.search(query_text, top_n)
        adjacent = {(record["source_key"], record["page_number"]): store.get_adjacent_pages(record["source_key"], record["page_number"], _ADJACENT_WINDOW) for record in primary}
        primaries, supporting = select_supporting_context(primary, adjacent, query_text)
    finally:
        store.close()
    hits = [_hit_from_record(record, "primary") for record in primaries]
    hits.extend(_hit_from_record(record, "supporting") for record in supporting)
    citations = _citations_from_hits(hits)
    return QueryResult(query_text, [query_text], hits, "", citations, retrieval_metadata={"retrieval_mode": "hybrid_v2", "fusion": "RRF(k=60)", "semantic_candidates": len(primary), "supporting_hit_count": len(supporting)})
