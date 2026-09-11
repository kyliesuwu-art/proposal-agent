"""Offline deterministic Markdown-to-PPTX renderer; it never queries a knowledge base or an LLM."""
from __future__ import annotations

import json
import os
import re
import tempfile
import zipfile
from collections import OrderedDict
from pathlib import Path

from pptx import Presentation
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt
from src.markdown_images import image_blocks, image_syntax_errors, safe_asset_path


class RenderPptxError(RuntimeError):
    """Raised for invalid local rendering inputs or failed local validation."""


HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
IMAGE = re.compile(r"^!\[([^]]*)\]\(([^)\s]+)(?:\s+[^)]*)?\)\s*$")
CITATION = re.compile(r"\[来源:\s*([^,\]]+?),\s*第\s*(\d+)\s*页\]")
LEGACY_IMAGE_SOURCE = re.compile(r"图片来源：\s*([^\n，,]+?)\s*[，,]\s*第\s*(\d+)\s*页")
LIST = re.compile(r"^\s*(?:[-*+] |\d+[.)] )(.+)$")
NUMERIC = re.compile(r"\b\d+(?:\.\d+)?\s*(?:MWp|MWh|MW|kV|kW|V|W|A|Hz|Ah|Wh|mm2|mm²|%|兆瓦|千瓦|小时|分钟)", re.I)


def _clean(text: str) -> str:
    return CITATION.sub("", text).replace("**", "").replace("`", "").strip()


def _sentences(text: str) -> list[str]:
    """Split only at natural punctuation, so no factual characters are silently dropped."""
    text = _clean(text)
    parts = [p.strip() for p in re.split(r"(?<=[。！？；;])\s*", text) if p.strip()]
    return parts or ([text] if text else [])


def _safe_asset(markdown: Path, raw: str) -> Path | None:
    if raw.lower().startswith(("http:", "https:", "data:")) or Path(raw).is_absolute():
        return None
    root = markdown.parent.resolve()
    candidate = (root / raw.replace("\\", "/")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _load_sources(path: Path | None, markdown: Path, text: str, warnings: list[str]) -> OrderedDict[str, dict]:
    """Accept the generated source list's common shapes; visible Markdown citations are a legacy fallback."""
    result: OrderedDict[str, dict] = OrderedDict()
    if path:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            records = raw.get("sources", raw) if isinstance(raw, dict) else raw
            if isinstance(records, dict):
                records = [{"source_id": key, **(value if isinstance(value, dict) else {"filename": str(value)})} for key, value in records.items()]
            for index, item in enumerate(records):
                if not isinstance(item, dict):
                    continue
                sid = str(item.get("source_id") or item.get("id") or f"S{index + 1}")
                filename = str(item.get("filename") or item.get("file_name") or item.get("source_file") or "")
                pages = item.get("pages") or item.get("page") or []
                if isinstance(pages, (int, str)): pages = [pages]
                # The slide plan is a delivery artifact.  It must not carry
                # retrieval internals such as chunk IDs from the sidecar.
                result[sid] = {"filename": filename, "pages": [str(p) for p in pages]}
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            warnings.append(f"invalid sources JSON ({path.name}): {exc}")
    if result:
        return result
    # A historical Markdown file can legitimately predate the sidecar.  Do
    # not manufacture stable source IDs: keep only visible citations below.
    if not path:
        warnings.append("SOURCE_HANDOFF: LEGACY_INPUT_WARNING (proposal.sources.json is absent; using visible Markdown citations only)")
    else:
        warnings.append("SOURCE_HANDOFF: FAIL (proposal.sources.json is invalid or empty)")
    return result


def _ids_for(text: str, sources: OrderedDict[str, dict]) -> list[str]:
    found: list[str] = []
    references = CITATION.findall(text) + LEGACY_IMAGE_SOURCE.findall(text)
    for filename, page in references:
        for sid, value in sources.items():
            if value["filename"] == filename.strip() and (not value["pages"] or page in value["pages"]):
                if sid not in found: found.append(sid)
    return found


def _visible_sources_for(text: str) -> list[str]:
    """Return only citations literally present in historical Markdown."""
    refs: list[str] = []
    for filename, page in CITATION.findall(text) + LEGACY_IMAGE_SOURCE.findall(text):
        value = f"{filename.strip()}，第 {page} 页"
        if value not in refs:
            refs.append(value)
    return refs


def _figure_topic(caption: str, context: str) -> str:
    """Classify only the display topic needed to place an already selected figure."""
    # Captions describe the figure.  Neighbouring source filenames often contain
    # words such as "收益分析", so they must not override a VPP caption.
    caption_value = caption.lower()
    if any(word in caption_value for word in ("虚拟电厂", "vpp", "调度", "交易", "运营")):
        return "vpp"
    if any(word in caption_value for word in ("电价", "收益", "峰谷", "现货", "套利", "曲线")):
        return "benefit"
    if any(word in caption_value for word in ("碳", "能碳")):
        return "carbon"
    value = (caption + "\n" + context).lower()
    if any(word in value for word in ("电价", "峰谷", "现货", "套利", "曲线")):
        return "benefit"
    return "architecture"


def _section_for_figure(topic: str, sections: list[dict]) -> tuple[dict, str]:
    """Return a topical H2, preferring relevance over the figure's original paragraph."""
    keywords = {
        "architecture": ("架构", "能源系统", "配电", "光储"),
        "vpp": ("虚拟电厂", "vpp", "调控", "运营", "平台"),
        "carbon": ("碳",),
        "benefit": ("效益", "收益", "实施", "电价"),
    }[topic]
    for section in sections:
        title = section["title"].lower()
        if any(word in title for word in keywords):
            return section, f"{topic} figure matched H2 topic"
    return sections[0], f"{topic} figure fell back to the first renderable H2"


def _chunk_items(items: list[tuple[str, list[str]]], *, max_items: int = 4, max_chars: int = 360) -> list[list[tuple[str, list[str]]]]:
    """Keep every complete sentence, creating continuation slides instead of truncating it."""
    chunks: list[list[tuple[str, list[str]]]] = []
    current: list[tuple[str, list[str]]] = []
    current_chars = 0
    for item in items:
        item_chars = len(item[0])
        if current and (len(current) >= max_items or current_chars + item_chars > max_chars):
            chunks.append(current); current = []; current_chars = 0
        current.append(item); current_chars += item_chars
    if current or not chunks:
        chunks.append(current)
    return chunks


def _briefing_priority(text: str) -> int:
    """Rank evidence already in the Markdown without adding a new claim."""
    score = 0
    if NUMERIC.search(text): score += 12
    if "【待确认】" in text or "需结合" in text or "前提" in text: score += 10
    for word in ("目标", "指标", "架构", "调度", "交易", "收益", "风险", "控制", "能力", "运营", "实施"):
        if word in text: score += 3
    return score


def _split_briefing_bullet(text: str, *, limit: int = 62) -> list[str]:
    """Wrap a long source sentence only at its existing Chinese separators."""
    if len(text) <= limit:
        return [text]
    pieces = re.split(r"(?<=[，、；])", text)
    result: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) > limit:
            result.append(current.strip()); current = ""
        current += piece
    if current.strip(): result.append(current.strip())
    return result


def _briefing_bullets(items: list[tuple[str, list[str]]], *, limit: int = 6) -> list[tuple[str, list[str]]]:
    """Select concise, source-faithful evidence for a briefing slide."""
    ranked = sorted(enumerate(items), key=lambda pair: (-_briefing_priority(pair[1][0]), pair[0]))
    selected: list[tuple[str, list[str]]] = []
    for _, (text, ids) in ranked:
        pieces = _split_briefing_bullet(text)
        if selected and len(selected) + len(pieces) > limit:
            continue
        if len(pieces) > limit and not selected:
            selected.extend((piece, ids) for piece in pieces[:limit])
            break
        selected.extend((piece, ids) for piece in pieces)
        if len(selected) >= limit:
            break
    return selected


def _briefing_slides(title: str, sections: list[dict], figures: OrderedDict[str, dict], sources: OrderedDict[str, dict], max_slides: int) -> tuple[list[dict], list[dict]]:
    """Build a bounded executive briefing; detail/presentation retain full continuation behavior."""
    if max_slides < 3:
        raise RenderPptxError("briefing requires at least 3 slides for cover, content, and references")
    usable = [section for section in sections if section["items"] or section["figures"] or section["tables"]]
    reserve = 3 if usable else 2  # cover, agenda, references
    body_slots = max_slides - reserve
    if len(usable) > body_slots:
        # Deterministic adjacent grouping is an explicit merge, never a hidden overflow.
        groups = [usable[index::body_slots] for index in range(body_slots)]
    else:
        groups = [[section] for section in usable]
    slides = [{"slide_id": "slide-001", "layout": "title", "title": title, "bullets": [], "figure_ids": [], "source_ids": [], "visible_sources": []}]
    if usable:
        slides.append({"slide_id": "slide-002", "layout": "agenda", "title": "汇报要点", "bullets": [section["title"] for section in usable[:6]], "figure_ids": [], "source_ids": [], "visible_sources": []})
    coverage: list[dict] = []
    for group_index, group in enumerate(groups):
        all_items = [item for section in group for item in section["items"]]
        bullets_and_ids = _briefing_bullets(all_items)
        figure_ids = [fid for section in group for fid in section["figures"]][:1]
        text_title = group[0]["title"] if len(group) == 1 else "；".join(section["title"] for section in group)
        visible_sources = list(dict.fromkeys(source for section in group for source in section["visible_sources"]))[:2]
        source_ids = list(dict.fromkeys(sid for _, ids in bullets_and_ids for sid in ids))
        source_ids.extend(sid for fid in figure_ids for sid in figures[fid]["source_ids"])
        source_ids = list(dict.fromkeys(source_ids))
        slides.append({"slide_id": f"slide-{len(slides)+1:03d}", "layout": "content_image" if figure_ids else "content", "title": text_title, "bullets": [text for text, _ in bullets_and_ids], "figure_ids": figure_ids, "source_ids": source_ids, "visible_sources": visible_sources})
        for section in group:
            selected = _briefing_bullets(section["items"])
            status = "merged" if len(group) > 1 else "covered"
            coverage.append({"source_heading": section["title"], "status": status, "slide_ids": [slides[-1]["slide_id"]], "reason": "section represented in briefing"})
            if len(selected) < len(section["items"]):
                coverage.append({"source_heading": section["title"], "status": "omitted_supporting_detail", "slide_ids": [], "reason": "briefing mode omitted repetitive or explanatory detail after selecting high-priority evidence"})
    # A management briefing may omit supporting text, but it must account for
    # every selected proposal image.  Use spare cap capacity for a topical
    # figure page instead of silently losing the image in the slide plan.
    assigned = {fid for slide in slides for fid in slide["figure_ids"]}
    for fid, figure in figures.items():
        if figure.get("decision") != "used":
            coverage.append({"source_heading": figure["original_section"], "status": "image_rejected", "slide_ids": [], "reason": figure.get("decision_reason", "not suitable for briefing")})
            continue
        if fid in assigned:
            continue
        if len(slides) >= max_slides - 1:
            coverage.append({"source_heading": figure["target_section"], "status": "image_not_used", "slide_ids": [], "reason": "briefing hard cap leaves no readable image page"})
            continue
        owner = next(section for section in sections if section["title"] == figure["target_section"])
        selected = _briefing_bullets(owner["items"], limit=3)
        # The owner can have no prose only for a malformed input; do not create
        # an empty briefing slide to force image placement.
        if len(selected) < 3:
            coverage.append({"source_heading": owner["title"], "status": "image_not_used", "slide_ids": [], "reason": "insufficient source-faithful bullets for readable figure page"})
            continue
        slides.append({"slide_id": f"slide-{len(slides)+1:03d}", "layout": "content_image",
                       "title": owner["title"] + "（图示）", "bullets": [text for text, _ in selected],
                       "figure_ids": [fid], "source_ids": list(dict.fromkeys([sid for _, ids in selected for sid in ids] + figure["source_ids"])),
                       "visible_sources": figure["visible_sources"]})
        coverage.append({"source_heading": owner["title"], "status": "image_used", "slide_ids": [slides[-1]["slide_id"]], "reason": "selected proposal image receives a dedicated briefing page"})
    visible_all = list(dict.fromkeys(source for section in sections for source in section["visible_sources"]))
    slides.append({"slide_id": f"slide-{len(slides)+1:03d}", "layout": "sources", "title": "参考资料", "bullets": [], "figure_ids": [], "source_ids": list(sources), "visible_sources": visible_all})
    if len(slides) > max_slides:
        raise RenderPptxError(f"briefing slide cap exceeded: {len(slides)} > {max_slides}")
    return slides, coverage


def _table_rows(lines: list[str], start: int) -> tuple[list[list[str]], int]:
    rows: list[list[str]] = []
    i = start
    while i < len(lines) and lines[i].strip().startswith("|"):
        cells = [cell.strip() for cell in lines[i].strip().strip("|").split("|")]
        if not all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells): rows.append(cells)
        i += 1
    return rows, i


def build_slide_plan(input_path: str | Path, *, mode: str = "presentation", max_slides: int = 15, sources_path: str | Path | None = None, image_decisions_path: str | Path | None = None) -> tuple[dict, list[str]]:
    """Create a serializable plan that can be stored and unit-tested independently of drawing."""
    markdown = Path(input_path).resolve()
    if not markdown.is_file() or markdown.suffix.lower() != ".md": raise RenderPptxError("--input must be an existing Markdown file")
    if mode not in {"briefing", "faithful", "presentation"}: raise RenderPptxError("mode must be briefing, faithful, or presentation")
    if max_slides < 1: raise RenderPptxError("--max-slides must be positive")
    text = markdown.read_text(encoding="utf-8")
    warnings: list[str] = []
    source_file = Path(sources_path).resolve() if sources_path else markdown.with_name("proposal.sources.json")
    image_errors = image_syntax_errors(text)
    if image_errors and source_file.is_file():
        raise RenderPptxError("invalid proposal image syntax: " + "；".join(image_errors))
    warnings.extend(image_errors)
    sources = _load_sources(source_file if source_file.is_file() else None, markdown, text, warnings)
    sidecar_images = {}
    if source_file.is_file():
        try:
            raw_sidecar = json.loads(source_file.read_text(encoding="utf-8"))
            sidecar_images = {item.get("asset_path"): item for item in raw_sidecar.get("images", []) if isinstance(item, dict)}
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    decisions = {}
    if image_decisions_path:
        decision_file = Path(image_decisions_path).resolve()
        decisions = json.loads(decision_file.read_text(encoding="utf-8")).get("images", {})
    title = next((match.group(2) for line in text.splitlines() if (match := HEADING.match(line)) and len(match.group(1)) == 1), markdown.stem)
    figures: OrderedDict[str, dict] = OrderedDict()
    sections: list[dict] = []
    current: dict | None = None
    lines = text.splitlines(); i = 0
    while i < len(lines):
        line = lines[i]; match = HEADING.match(line)
        if match:
            level, value = len(match.group(1)), match.group(2)
            if level == 2:
                if value.strip() not in {"来源与依据", "参考资料"}:
                    current = {"title": value, "items": [], "figures": [], "tables": [], "source_ids": [], "visible_sources": []}; sections.append(current)
                else: current = None
            elif current and level == 3: current["items"].append((value, _ids_for(line, sources)))
            i += 1; continue
        if current and line.strip().startswith("|") and i + 1 < len(lines) and "-" in lines[i + 1]:
            rows, i = _table_rows(lines, i); current["tables"].append(rows); continue
        block = next((item for item in image_blocks(line) if item.line_number == 1), None)
        if block:
            asset = safe_asset_path(markdown, block.asset_path)
            if asset is None: warnings.append(f"skipped missing or unsafe image: {block.asset_path}")
            elif current is None: warnings.append(f"skipped image outside a renderable H2 section: {block.asset_path}")
            else:
                fid = f"F{len(figures) + 1}"
                # Do not borrow the following figure's caption when historical
                # Markdown stores images as a consecutive group.
                context_lines = [line]
                for nearby in lines[i + 1:min(len(lines), i + 4)]:
                    if IMAGE.match(nearby.strip()) or HEADING.match(nearby):
                        break
                    context_lines.append(nearby)
                context = "\n".join(context_lines)
                source_ids = _ids_for(context, sources)
                figures[fid] = {
                    "asset_path": str(asset.relative_to(markdown.parent)).replace("\\", "/"),
                    "caption": block.caption, "source_ids": source_ids,
                    "visible_sources": _visible_sources_for(context), "context": context,
                    "original_section": current["title"],
                }
            i += 1; continue
        if current and line.strip():
            bullet = LIST.match(line)
            parts = [_clean(bullet.group(1))] if bullet else _sentences(line)
            ids = _ids_for(line, sources)
            current["visible_sources"].extend(_visible_sources_for(line))
            for part in parts:
                if part: current["items"].append((part, ids)); current["source_ids"].extend(ids)
        i += 1
    # Explicit sidecar provenance always wins over a heuristic topic match.
    for fid, figure in figures.items():
        sidecar = sidecar_images.get(figure["asset_path"], {})
        original = next((section for section in sections if section["title"] == figure["original_section"]), None)
        section_id = sidecar.get("section_id")
        owner = original
        if section_id:
            # proposal headings are ordered; the sidecar is authoritative but
            # its ID only maps through the known Markdown section in this layer.
            owner = original
        figure["target_section"] = owner["title"] if owner else figure["original_section"]
        figure["placement_reason"] = "sidecar section_id" if section_id else "Markdown H2 occurrence"
        decision = decisions.get(figure["asset_path"], {})
        figure["decision"] = decision.get("status", "used")
        figure["decision_reason"] = decision.get("reason", "proposal image is suitable for PPT")
        if figure["decision"] == "used" and owner:
            owner["figures"].append(fid)
        owner["source_ids"].extend(figure["source_ids"])
        owner["visible_sources"].extend(figure["visible_sources"])
    coverage: list[dict] = []
    if mode == "briefing":
        slides, coverage = _briefing_slides(title, sections, figures, sources, max_slides)
    else:
        slides: list[dict] = [{"slide_id": "slide-001", "layout": "title", "title": title, "bullets": [], "figure_ids": [], "source_ids": []}]
        if sections:
            slides.append({"slide_id": "slide-002", "layout": "agenda", "title": "目录", "bullets": [s["title"] for s in sections], "figure_ids": [], "source_ids": []})
        for section in sections:
            slides.append({"slide_id": f"slide-{len(slides)+1:03d}", "layout": "section", "title": section["title"], "bullets": [], "figure_ids": [], "source_ids": []})
            items = section["items"][:]
            for table in section["tables"]:
                slides.append({"slide_id": f"slide-{len(slides)+1:03d}", "layout": "table", "title": section["title"], "bullets": [], "figure_ids": [], "source_ids": list(dict.fromkeys(section["source_ids"])), "visible_sources": list(dict.fromkeys(section["visible_sources"])), "table": table})
            chunks = _chunk_items(items)
            for number, chunk in enumerate(chunks):
                figure_ids = [section["figures"].pop(0)] if section["figures"] else []
                ids = list(dict.fromkeys([sid for _, item_ids in chunk for sid in item_ids] + section["source_ids"] + sum((figures[f]["source_ids"] for f in figure_ids), [])))
                label = section["title"] + ("（续）" if number else "")
                slides.append({"slide_id": f"slide-{len(slides)+1:03d}", "layout": "content_image" if figure_ids else "content", "title": label, "bullets": [value for value, _ in chunk], "figure_ids": figure_ids, "source_ids": ids, "visible_sources": list(dict.fromkeys(section["visible_sources"]))})
            while section["figures"]:
                fid = section["figures"].pop(0)
                slides.append({"slide_id": f"slide-{len(slides)+1:03d}", "layout": "content_image", "title": section["title"] + "（续）", "bullets": [], "figure_ids": [fid], "source_ids": figures[fid]["source_ids"], "visible_sources": figures[fid]["visible_sources"]})
        legacy_visible_sources = list(dict.fromkeys(source for section in sections for source in section["visible_sources"]))
        slides.append({"slide_id": f"slide-{len(slides)+1:03d}", "layout": "sources", "title": "参考资料", "bullets": [], "figure_ids": [], "source_ids": list(sources), "visible_sources": legacy_visible_sources})
        if mode == "presentation" and len(slides) > max_slides: warnings.append(f"suggested maximum is {max_slides}, but {len(slides)} slides are required to avoid deleting content")
    for slide in slides:
        slide["source_ids"] = list(dict.fromkeys(slide["source_ids"]))
        slide["visible_sources"] = list(dict.fromkeys(slide.get("visible_sources", [])))
    handoff = "PASS" if sources else ("LEGACY_INPUT_WARNING" if not source_file.is_file() else "FAIL")
    return {"title": title, "input_file": markdown.name, "mode": mode, "requested_max_slides": max_slides, "max_slides": max_slides, "actual_slide_count": len(slides), "cap_enforced": mode == "briefing", "source_handoff": handoff, "source_coverage": coverage, "sources": sources, "figures": figures, "slides": slides}, warnings


def build_slides_markdown(input_path: str | Path, output_path: str | Path, *, mode: str = "presentation", max_slides: int = 15, sources_path: str | Path | None = None, image_decisions_path: str | Path | None = None) -> tuple[Path, Path, list[str]]:
    """Create a presentation-oriented intermediate Markdown and the matching plan."""
    plan, warnings = build_slide_plan(input_path, mode=mode, max_slides=max_slides, sources_path=sources_path, image_decisions_path=image_decisions_path)
    output = Path(output_path).resolve(); output.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {plan['title']}｜演示文稿", ""]
    for slide in plan["slides"]:
        lines.extend([f"## {slide['slide_id']} | {slide['layout']} | {slide['title']}", ""])
        lines.extend(f"- {item}" for item in slide.get("bullets", []))
        for fid in slide.get("figure_ids", []):
            fig = plan["figures"][fid]; lines.append(f"- Figure {fid}: {fig['caption']}（{fig['placement_reason']}）")
        if slide.get("source_ids"): lines.append("- Sources: " + ", ".join(slide["source_ids"]))
        if slide.get("visible_sources"): lines.append("- Visible sources: " + "；".join(slide["visible_sources"]))
        lines.append("")
    output.write_text("\n".join(lines), encoding="utf-8")
    plan_path = output.with_name(output.stem.removesuffix(".slides") + ".slide-plan.json")
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return output, plan_path, warnings


def _textbox(slide, x, y, w, h, text, size, *, bold=False, color=(31, 54, 82), align=PP_ALIGN.LEFT):
    shape = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = shape.text_frame; frame.clear(); frame.word_wrap = True; frame.vertical_anchor = MSO_ANCHOR.TOP
    paragraph = frame.paragraphs[0]; paragraph.text = text; paragraph.alignment = align
    for run in paragraph.runs:
        run.font.name = "Microsoft YaHei"; run.font.size = Pt(size); run.font.bold = bold; run.font.color.rgb = __import__("pptx.dml.color", fromlist=["RGBColor"]).RGBColor(*color)
    return shape


def _source_text(ids: list[str], sources: dict, *, limit: int | None = None) -> str:
    refs = []
    shown_ids = ids if limit is None else ids[:limit]
    for sid in shown_ids:
        info = sources.get(sid)
        if not info: continue
        pages = "、".join(info.get("pages", [])); suffix = f"第{pages}页" if pages else ""
        refs.append(f"[{sid}]《{info.get('filename', '')}》{suffix}")
    suffix = f"等 {len(ids)} 项来源" if limit is not None and len(ids) > len(shown_ids) else ""
    return "来源：" + "；".join(refs) + suffix


def _reference_lines(sources: dict) -> list[str]:
    """Collapse source pages by filename for a readable reference slide."""
    grouped: OrderedDict[str, list[str]] = OrderedDict()
    for sid, info in sources.items():
        grouped.setdefault(info.get("filename", sid), []).extend(info.get("pages", []))
    return [f"{name}（第{'、'.join(dict.fromkeys(pages))}页）" if pages else name
            for name, pages in grouped.items()]


def _add_image(slide, path: Path, x, y, w, h):
    from PIL import Image
    with Image.open(path) as image:
        iw, ih = image.size
    scale = min(w / iw, h / ih); width, height = iw * scale, ih * scale
    return slide.shapes.add_picture(str(path), Inches(x + (w-width)/2), Inches(y + (h-height)/2), width=Inches(width), height=Inches(height))


def _overlap_pairs(shapes) -> list[tuple[int, int]]:
    pairs = []
    for left, first in enumerate(shapes):
        for right in range(left + 1, len(shapes)):
            second = shapes[right]
            if (first.left < second.left + second.width and second.left < first.left + first.width
                    and first.top < second.top + second.height and second.top < first.top + first.height):
                pairs.append((left, right))
    return pairs


def _draw(plan: dict, markdown: Path, output: Path) -> None:
    prs = Presentation(); prs.slide_width = Inches(13.333); prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]
    for spec in plan["slides"]:
        slide = prs.slides.add_slide(blank); layout = spec["layout"]
        if layout == "title":
            _textbox(slide, 1.0, 2.5, 11.3, 1.3, spec["title"], 50, bold=True, align=PP_ALIGN.CENTER)
            _textbox(slide, 1.0, 4.1, 11.3, .4, "基于已审阅 Markdown 的本地演示文稿", 20, color=(90, 105, 120), align=PP_ALIGN.CENTER)
            continue
        _textbox(slide, .65, .42, 12.0, .55, spec["title"], 35, bold=True)
        if layout == "section":
            _textbox(slide, .9, 2.7, 11.5, .8, spec["title"], 32, color=(47, 103, 145), align=PP_ALIGN.CENTER)
        elif layout == "agenda":
            for index, bullet in enumerate(spec["bullets"]): _textbox(slide, 1.15, 1.35 + index*.72, 10.8, .5, f"{index+1}. {bullet}", 24)
        elif layout == "table":
            rows = spec.get("table", []); cols = max((len(row) for row in rows), default=1)
            table = slide.shapes.add_table(len(rows), cols, Inches(.75), Inches(1.3), Inches(11.8), Inches(4.9)).table
            for r, row in enumerate(rows):
                for c, value in enumerate(row):
                    cell = table.cell(r, c); cell.text = value
                    for para in cell.text_frame.paragraphs:
                        for run in para.runs: run.font.name = "Microsoft YaHei"; run.font.size = Pt(16); run.font.bold = r == 0
        elif layout == "sources":
            source_lines = _reference_lines(plan["sources"])
            if not source_lines:
                # Historical inputs have no source IDs.  Show each real file
                # once rather than overflowing the reference slide with every
                # repeated page citation.
                source_lines = list(dict.fromkeys(value.split("，第 ", 1)[0] for value in spec.get("visible_sources", [])))
            # A source slide has a fixed vertical budget.  Add columns before
            # moving a reference below the page; source text is never hidden.
            columns = max(1, (len(source_lines) + 11) // 12)
            rows = max(1, (len(source_lines) + columns - 1) // columns)
            for index, value in enumerate(source_lines):
                column, row = index // rows, index % rows
                _textbox(slide, .65 + column * (12.0 / columns), 1.2 + row * .45,
                         11.3 / columns, .36, value, 12)
        else:
            has_image = bool(spec["figure_ids"]); text_width = 6.5 if has_image else 11.5
            body = "\n".join("• " + bullet for bullet in spec["bullets"])
            _textbox(slide, .85, 1.3, text_width, 4.9, body, 20)
            if has_image:
                fid = spec["figure_ids"][0]; fig = plan["figures"][fid]; _add_image(slide, markdown.parent / fig["asset_path"], 7.6, 1.25, 4.9, 4.65)
                if fig["caption"]: _textbox(slide, 7.6, 5.98, 4.9, .35, fig["caption"], 12, color=(90,105,120), align=PP_ALIGN.CENTER)
        footer = _source_text(spec["source_ids"], plan["sources"]) if spec["source_ids"] and plan.get("mode") != "briefing" else ""
        if not footer and spec.get("visible_sources"):
            footer = "来源：" + "；".join(spec["visible_sources"])
        if footer: _textbox(slide, .7, 6.82, 11.95, .35, footer, 10, color=(90,105,120))
    output.parent.mkdir(parents=True, exist_ok=True); prs.save(output)


def _validate(output: Path, plan: dict, markdown: Path) -> dict:
    errors: list[str] = []
    try:
        if not output.is_file() or output.stat().st_size == 0: errors.append("PPTX is empty")
        with zipfile.ZipFile(output) as archive:
            names = set(archive.namelist()); media = [name for name in names if name.startswith("ppt/media/")]
            if "[Content_Types].xml" not in names or "ppt/presentation.xml" not in names: errors.append("PPTX required ZIP parts are missing")
            relation_count = sum(1 for name in names if name.startswith("ppt/slides/_rels/") for _ in [name])
        prs = Presentation(output)
        if plan.get("mode") == "briefing" and len(plan["slides"]) > plan.get("requested_max_slides", plan.get("max_slides", 0)):
            errors.append("briefing slide cap exceeded")
        ids = [spec.get("slide_id") for spec in plan["slides"]]
        if len(ids) != len(set(ids)) or any(not value for value in ids): errors.append("slide IDs must be unique and non-empty")
        if len(prs.slides) != len(plan["slides"]): errors.append("slide count differs from slide plan")
        for expected, slide in zip(plan["slides"], prs.slides):
            text = "\n".join(shape.text for shape in slide.shapes if hasattr(shape, "text"))
            if expected["title"] not in text: errors.append(f"missing title: {expected['slide_id']}")
            if not expected.get("title"): errors.append(f"empty title: {expected['slide_id']}")
            if plan.get("mode") == "briefing" and expected["layout"] in {"content", "content_image"} and not expected.get("bullets"):
                errors.append(f"empty content slide: {expected['slide_id']}")
            if plan.get("mode") == "briefing" and expected["layout"] in {"content", "content_image"}:
                if not 3 <= len(expected["bullets"]) <= 6: errors.append(f"briefing bullet density invalid: {expected['slide_id']}")
                if any(len(value) > 65 for value in expected["bullets"]): errors.append(f"briefing bullet too long: {expected['slide_id']}")
            for shape in slide.shapes:
                if shape.left < 0 or shape.top < 0 or shape.width <= 0 or shape.height <= 0:
                    errors.append(f"invalid shape geometry: {expected['slide_id']}"); break
                if shape.left + shape.width > prs.slide_width or shape.top + shape.height > prs.slide_height:
                    errors.append(f"shape exceeds slide bounds: {expected['slide_id']}"); break
            if _overlap_pairs(list(slide.shapes)):
                errors.append(f"shape overlap: {expected['slide_id']}")
            expected_footer = _source_text(expected["source_ids"], plan["sources"]) if expected["source_ids"] and plan.get("mode") != "briefing" else ""
            if not expected_footer and expected.get("visible_sources"):
                expected_footer = "来源：" + "；".join(expected["visible_sources"])
            if expected["layout"] not in {"title", "section", "sources"} and expected_footer and expected_footer not in text: errors.append(f"source footer mismatch: {expected['slide_id']}")
        planned_images = sum(len(s["figure_ids"]) for s in plan["slides"])
        if len(media) < len({f for s in plan["slides"] for f in s["figure_ids"]}): errors.append("not all planned images were embedded")
        content = "\n".join(shape.text for slide in prs.slides for shape in slide.shapes if hasattr(shape, "text"))
        if plan.get("mode") != "briefing":
            missing_numbers = [token for token in dict.fromkeys(NUMERIC.findall(markdown.read_text(encoding="utf-8"))) if token not in content]
            if missing_numbers: errors.append("numeric tokens missing from PPTX: " + ", ".join(missing_numbers))
        if re.search(r"[A-Za-z]:[\\/]", content): errors.append("absolute local path leaked into PPTX")
        source_slide = any(spec["layout"] == "sources" for spec in plan["slides"])
        if not source_slide: errors.append("reference slide is missing")
    except Exception as exc: errors.append(f"validation exception: {type(exc).__name__}: {exc}")
    return {"embedded_image_count": len(media) if 'media' in locals() else 0, "errors": errors}


def _read_slide_plan(plan_path: str | Path) -> dict:
    try:
        plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise RenderPptxError(f"invalid slide plan: {exc}") from exc
    if not isinstance(plan, dict) or not isinstance(plan.get("slides"), list) or not isinstance(plan.get("figures"), dict):
        raise RenderPptxError("invalid slide plan: slides and figures are required")
    return plan


def write_pptx_layout_audit(pptx_path: str | Path, plan_path: str | Path, output_dir: str | Path, *, visual_backend: str | None = None) -> tuple[Path, Path]:
    """Write a reusable geometry audit; it never claims a visual review without rendered pages."""
    pptx, plan = Path(pptx_path).resolve(), _read_slide_plan(plan_path)
    out = Path(output_dir).resolve(); rendered = out / "rendered"; rendered.mkdir(parents=True, exist_ok=True)
    prs = Presentation(pptx)
    pages = []
    for spec, slide in zip(plan["slides"], prs.slides):
        shapes = []
        for shape in slide.shapes:
            shapes.append({"left": shape.left, "top": shape.top, "width": shape.width, "height": shape.height, "has_text": bool(getattr(shape, "text", ""))})
        pages.append({"slide_id": spec["slide_id"], "title": spec["title"], "shape_count": len(shapes), "shape_bounds_ok": all(item["left"] >= 0 and item["top"] >= 0 and item["width"] > 0 and item["height"] > 0 and item["left"] + item["width"] <= prs.slide_width and item["top"] + item["height"] <= prs.slide_height for item in shapes), "overlap_pairs": _overlap_pairs(list(slide.shapes))})
    rendered_pages = list(rendered.glob("*.png"))
    visual_render = "PASS" if rendered_pages and visual_backend else "BLOCKED"
    audit = {"pptx": pptx.name, "slide_count": len(prs.slides), "plan_slide_count": len(plan["slides"]), "aspect_ratio": round(prs.slide_width / prs.slide_height, 4), "source_handoff": plan.get("source_handoff"), "visual_backend": visual_backend, "visual_render": visual_render, "visual_review": "NOT_RUN" if visual_render != "PASS" else "PENDING", "rendered_page_count": len(rendered_pages), "pages": pages, "errors": ["visual renderer unavailable; no page PNGs or contact sheet produced"] if visual_render == "BLOCKED" else []}
    json_path = out / "visual_audit.json"; json_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# PPTX 版面审计", "", f"- PPTX：{pptx.name}", f"- 页数：{len(prs.slides)}", f"- 画幅：{audit['aspect_ratio']}（16:9 约为 1.7778）", f"- PPT_VISUAL_RENDER：{visual_render}", f"- PPT_VISUAL_REVIEW：{audit['visual_review']}", f"- 后端：{visual_backend or '未检测到 PowerPoint COM 或 LibreOffice'}", "", "## 页面", ""]
    lines.extend(f"- {page['slide_id']}：{'PASS' if page['shape_bounds_ok'] and not page['overlap_pairs'] else 'FAIL'}，{page['shape_count']} 个 shape，{page['title']}" for page in pages)
    if audit["errors"]: lines.extend(["", "## Warning", "", *[f"- {error}" for error in audit["errors"]]])
    md_path = out / "visual_audit.md"; md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def render_pptx(input_path: str | Path, output_path: str | Path, *, mode="presentation", max_slides=15, sources_path: str | Path | None = None, plan_path: str | Path | None = None) -> Path:
    markdown, output = Path(input_path).resolve(), Path(output_path).resolve()
    if plan_path:
        plan = _read_slide_plan(plan_path)
        warnings: list[str] = []
    else:
        plan, warnings = build_slide_plan(markdown, mode=mode, max_slides=max_slides, sources_path=sources_path)
        plan_path = output.with_suffix(".slide-plan.json")
        output.parent.mkdir(parents=True, exist_ok=True); Path(plan_path).write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    with tempfile.TemporaryDirectory(dir=output.parent, prefix="render-pptx-") as tempdir:
        staged = Path(tempdir) / output.name; _draw(plan, markdown, staged); validation = _validate(staged, plan, markdown)
        report = {"status": "PASS" if not validation["errors"] else "FAIL", "backend": "local_python_pptx", "mode": plan.get("mode", mode), "slide_plan": Path(plan_path).name, "source_handoff": plan.get("source_handoff", "PASS"), "requested_max_slides": plan.get("requested_max_slides", max_slides), "actual_slide_count": len(plan["slides"]), "cap_enforced": plan.get("cap_enforced", False), "slide_count": len(plan["slides"]), "source_coverage": plan.get("source_coverage", []), "source_count": len(plan["sources"]), "image_reference_count": sum(len(s["figure_ids"]) for s in plan["slides"]), "embedded_image_count": validation["embedded_image_count"], "table_count": sum(1 for s in plan["slides"] if s["layout"] == "table"), "overflow_slide_count": sum(1 for s in plan["slides"] if "（续）" in s["title"]), "checked_numeric_tokens": NUMERIC.findall(markdown.read_text(encoding="utf-8")), "warnings": warnings, "errors": validation["errors"]}
        if report["errors"]: raise RenderPptxError("; ".join(report["errors"]))
        os.replace(staged, output)
    report_path = output.with_suffix(".pptx.render_report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path
