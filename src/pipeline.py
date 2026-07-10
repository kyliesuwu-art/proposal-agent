# pipeline.py
"""方案知识库业务编排逻辑：串联 parser → vector_store → llm_client。

被 main.py 调用，本身不处理命令行参数——那部分留在 main.py，
这里只负责 ingest / query / status 这几条业务流程怎么串起来。
"""

import re
import sys
from pathlib import Path

from src.adapters.parser import MinerUParser
from src.adapters.vector_store import VectorStore
from src.adapters.llm_client import LLMClient

# 相邻 slide 扩展：命中的 slide 前后各带几张，见 claude.md 检索流程第 4 步
_ADJACENT_WINDOW = 1

# 生成方案初稿时使用的系统提示词。核心约束：只能基于参考资料改写，不能
# 编造参考资料里没有的具体数据/型号——这类工程方案文档如果被模型瞎编
# 技术参数，后果比检索不到更糟。每段标注来源，方便方案员回头核对原始设计。
_GENERATION_SYSTEM_PROMPT = """你是康晋电气的方案撰写助手，负责基于历史方案库中的相关内容，
为新的客户需求撰写方案初稿。

要求：
- 内容基于下面提供的参考资料改写、整合，不要编造参考资料中没有出现的具体数据、型号、参数
- 保持专业、简洁的方案文档语气
- 按逻辑结构组织内容（如：项目背景、技术方案、系统组成、优势亮点等），不必完全照搬参考资料的原始顺序
- 在每个段落末尾用类似 [来源: xxx.pptx Slide N] 的方式标注改写自哪份参考资料，方便后续核对原始设计
- **重要**：标注来源时，Slide 编号只能从下方"可引用编号集合"中选择，不得编造不存在的编号"""


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

    # 第一步：MinerU 解析。先解析成功拿到完整数据，再动 Chroma——
    # 万一解析中途失败，旧数据还留着，不会出现"删了旧的、新的又没进来"的空档
    print("\n[1/3] 调用 MinerU 解析 PPTX...")
    parser = MinerUParser()
    slides = parser.parse_pptx(pptx_file)
    print(f"  解析完成，共 {len(slides)} 个 slide")

    if not slides:
        print("  警告：没有提取到任何文字内容")
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

    count = store.add_slides(slides, file_hash=file_hash)
    print(f"  已存入 {count} 个 slide（尚未生成 context 增强）")

    # 第三步：逐张生成 context/metadata（LLM 调用逐张进行，没法合并），
    # 全部生成完之后一次性批量 update 回 Chroma——不是生成一张存一张。
    # 单张生成失败只影响这一张（保留裸存内容），不影响其他 slide。
    print(f"\n[3/3] 生成 slide context 与 metadata（共 {len(slides)} 次 LLM 调用）...")
    _annotate_slides(store, slides)

    print(f"\n=== 入库完成 ===")
    print(f"  库中总计: {store.count()} 个 slide")


def _annotate_slides(store: VectorStore, slides: list[dict]) -> None:
    """逐张生成 context/metadata，全部生成完之后一次性批量写回向量库。

    LLM 调用仍然是逐张进行的（context 是 slide 专属的，没法合并成一次
    调用），但对 Chroma 的写入合并成一次批量 update，不是生成一张就写
    一次。单张生成失败时直接跳过这张，不纳入最后的批量更新，该 slide
    保留裸存时的原始内容，不影响其他 slide 继续处理。
    """
    llm = LLMClient()
    # 只取标题，不传全文，控制 token 成本——用作"文档大纲"上下文，
    # 帮模型判断每张 slide 大致在方案里的章节位置
    outline_titles = [s["title"] for s in slides]

    succeeded_slides = []
    succeeded_contexts = []
    failed_count = 0

    for i, slide in enumerate(slides, 1):
        print(f"  [{i}/{len(slides)}] Slide {slide['slide_number']} 生成 context...")
        try:
            ctx = llm.generate_slide_context(slide, outline_titles)
            succeeded_slides.append(slide)
            succeeded_contexts.append(ctx)
        except Exception as e:  # noqa: BLE001 — 有意宽泛捕获，见函数说明
            failed_count += 1
            print(f"    警告：生成失败（{e}），该 slide 保留原始内容，跳过增强")

    if succeeded_slides:
        store.update_slide_contexts(succeeded_slides, succeeded_contexts)

    success = len(slides) - failed_count
    print(f"  context 生成完成（{success}/{len(slides)} 成功，已批量写回）")

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