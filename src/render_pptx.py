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


class RenderPptxError(RuntimeError):
    """Raised for invalid local rendering inputs or failed local validation."""


HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
IMAGE = re.compile(r"^!\[([^]]*)\]\(([^)\s]+)(?:\s+[^)]*)?\)\s*$")
CITATION = re.compile(r"\[来源:\s*([^,\]]+?),\s*第\s*(\d+)\s*页\]")
LIST = re.compile(r"^\s*(?:[-*+] |\d+[.)] )(.+)$")
NUMERIC = re.compile(r"\b\d+(?:\.\d+)?\s*(?:MWp|MWh|MW|kV|kW|V|W|A|Hz|Ah|Wh|mm2|mm²|%|兆瓦|千瓦|小时|分钟)\b", re.I)


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
                result[sid] = {"filename": filename, "pages": [str(p) for p in pages], "chunk_id": item.get("chunk_id")}
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            warnings.append(f"invalid sources JSON ({path.name}): {exc}")
    if result:
        return result
    for filename, page in CITATION.findall(text):
        key = (filename.strip(), page)
        found = next((sid for sid, info in result.items() if info["filename"] == key[0] and key[1] in info["pages"]), None)
        if not found:
            sid = f"S{len(result) + 1}"
            result[sid] = {"filename": key[0], "pages": [key[1]], "chunk_id": None}
    if result:
        warnings.append("proposal.sources.json missing or empty; derived sources from visible Markdown citations")
    else:
        warnings.append("no usable sources found; source footer and reference page may be incomplete")
    return result


def _ids_for(text: str, sources: OrderedDict[str, dict]) -> list[str]:
    found: list[str] = []
    for filename, page in CITATION.findall(text):
        for sid, value in sources.items():
            if value["filename"] == filename.strip() and (not value["pages"] or page in value["pages"]):
                if sid not in found: found.append(sid)
    return found


def _table_rows(lines: list[str], start: int) -> tuple[list[list[str]], int]:
    rows: list[list[str]] = []
    i = start
    while i < len(lines) and lines[i].strip().startswith("|"):
        cells = [cell.strip() for cell in lines[i].strip().strip("|").split("|")]
        if not all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells): rows.append(cells)
        i += 1
    return rows, i


def build_slide_plan(input_path: str | Path, *, mode: str = "presentation", max_slides: int = 15, sources_path: str | Path | None = None) -> tuple[dict, list[str]]:
    """Create a serializable plan that can be stored and unit-tested independently of drawing."""
    markdown = Path(input_path).resolve()
    if not markdown.is_file() or markdown.suffix.lower() != ".md": raise RenderPptxError("--input must be an existing Markdown file")
    if mode not in {"faithful", "presentation"}: raise RenderPptxError("mode must be faithful or presentation")
    if max_slides < 1: raise RenderPptxError("--max-slides must be positive")
    text = markdown.read_text(encoding="utf-8")
    warnings: list[str] = []
    source_file = Path(sources_path).resolve() if sources_path else markdown.with_name("proposal.sources.json")
    sources = _load_sources(source_file if source_file.is_file() else None, markdown, text, warnings)
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
                    current = {"title": value, "items": [], "figures": [], "tables": [], "source_ids": []}; sections.append(current)
                else: current = None
            elif current and level == 3: current["items"].append((value, _ids_for(line, sources)))
            i += 1; continue
        if current and line.strip().startswith("|") and i + 1 < len(lines) and "-" in lines[i + 1]:
            rows, i = _table_rows(lines, i); current["tables"].append(rows); continue
        image = IMAGE.match(line.strip())
        if image:
            asset = _safe_asset(markdown, image.group(2))
            if asset is None: warnings.append(f"skipped missing or unsafe image: {image.group(2)}")
            elif current is None: warnings.append(f"skipped image outside a renderable H2 section: {image.group(2)}")
            else:
                fid = f"F{len(figures) + 1}"; context = "\n".join(lines[i:min(len(lines), i + 3)])
                source_ids = _ids_for(context, sources)
                figures[fid] = {"asset_path": str(asset.relative_to(markdown.parent)).replace("\\", "/"), "caption": image.group(1), "source_ids": source_ids}
                current["figures"].append(fid); current["source_ids"].extend(source_ids)
            i += 1; continue
        if current and line.strip():
            bullet = LIST.match(line)
            parts = [_clean(bullet.group(1))] if bullet else _sentences(line)
            ids = _ids_for(line, sources)
            for part in parts:
                if part: current["items"].append((part, ids)); current["source_ids"].extend(ids)
        i += 1
    slides: list[dict] = [{"slide_id": "slide-001", "layout": "title", "title": title, "bullets": [], "figure_ids": [], "source_ids": []}]
    if sections:
        slides.append({"slide_id": "slide-002", "layout": "agenda", "title": "目录", "bullets": [s["title"] for s in sections], "figure_ids": [], "source_ids": []})
    for section in sections:
        slides.append({"slide_id": f"slide-{len(slides)+1:03d}", "layout": "section", "title": section["title"], "bullets": [], "figure_ids": [], "source_ids": []})
        items = section["items"][:]
        for table in section["tables"]:
            slides.append({"slide_id": f"slide-{len(slides)+1:03d}", "layout": "table", "title": section["title"], "bullets": [], "figure_ids": [], "source_ids": list(dict.fromkeys(section["source_ids"])), "table": table})
        # A figure is paired with the next text chunk when possible; remaining chunks are continued slides.
        chunks = [items[n:n+5] for n in range(0, len(items), 5)] or [[]]
        for number, chunk in enumerate(chunks):
            figure_ids = [section["figures"].pop(0)] if section["figures"] else []
            ids = list(dict.fromkeys([sid for _, item_ids in chunk for sid in item_ids] + section["source_ids"] + sum((figures[f]["source_ids"] for f in figure_ids), [])))
            label = section["title"] + ("（续）" if number else "")
            slides.append({"slide_id": f"slide-{len(slides)+1:03d}", "layout": "content_image" if figure_ids else "content", "title": label, "bullets": [value for value, _ in chunk], "figure_ids": figure_ids, "source_ids": ids})
        while section["figures"]:
            fid = section["figures"].pop(0)
            slides.append({"slide_id": f"slide-{len(slides)+1:03d}", "layout": "content_image", "title": section["title"] + "（续）", "bullets": [], "figure_ids": [fid], "source_ids": figures[fid]["source_ids"]})
    slides.append({"slide_id": f"slide-{len(slides)+1:03d}", "layout": "sources", "title": "参考资料", "bullets": [], "figure_ids": [], "source_ids": list(sources)})
    if mode == "presentation" and len(slides) > max_slides: warnings.append(f"suggested maximum is {max_slides}, but {len(slides)} slides are required to avoid deleting content")
    for slide in slides: slide["source_ids"] = list(dict.fromkeys(slide["source_ids"]))
    return {"title": title, "mode": mode, "max_slides": max_slides, "sources": sources, "figures": figures, "slides": slides}, warnings


def _textbox(slide, x, y, w, h, text, size, *, bold=False, color=(31, 54, 82), align=PP_ALIGN.LEFT):
    shape = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = shape.text_frame; frame.clear(); frame.word_wrap = True; frame.vertical_anchor = MSO_ANCHOR.TOP
    paragraph = frame.paragraphs[0]; paragraph.text = text; paragraph.alignment = align
    for run in paragraph.runs:
        run.font.name = "Microsoft YaHei"; run.font.size = Pt(size); run.font.bold = bold; run.font.color.rgb = __import__("pptx.dml.color", fromlist=["RGBColor"]).RGBColor(*color)
    return shape


def _source_text(ids: list[str], sources: dict) -> str:
    refs = []
    for sid in ids:
        info = sources.get(sid)
        if not info: continue
        pages = "、".join(info.get("pages", [])); suffix = f"第{pages}页" if pages else ""
        refs.append(f"[{sid}]《{info.get('filename', '')}》{suffix}")
    return "来源：" + "；".join(refs)


def _add_image(slide, path: Path, x, y, w, h):
    from PIL import Image
    with Image.open(path) as image:
        iw, ih = image.size
    scale = min(w / iw, h / ih); width, height = iw * scale, ih * scale
    return slide.shapes.add_picture(str(path), Inches(x + (w-width)/2), Inches(y + (h-height)/2), width=Inches(width), height=Inches(height))


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
            for index, (sid, info) in enumerate(plan["sources"].items()):
                pages = "、".join(info["pages"])
                _textbox(slide, .9, 1.25 + index*.42, 11.5, .35, f"[{sid}] {info['filename']} {('第' + pages + '页') if pages else ''}", 16)
        else:
            has_image = bool(spec["figure_ids"]); text_width = 6.5 if has_image else 11.5
            for index, bullet in enumerate(spec["bullets"]): _textbox(slide, .85, 1.3 + index*.72, text_width, .6, "• " + bullet, 20)
            if has_image:
                fid = spec["figure_ids"][0]; fig = plan["figures"][fid]; _add_image(slide, markdown.parent / fig["asset_path"], 7.6, 1.25, 4.9, 4.65)
                if fig["caption"]: _textbox(slide, 7.6, 5.98, 4.9, .35, fig["caption"], 12, color=(90,105,120), align=PP_ALIGN.CENTER)
        if spec["source_ids"]: _textbox(slide, .7, 6.82, 11.95, .35, _source_text(spec["source_ids"], plan["sources"]), 10, color=(90,105,120))
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
        if len(prs.slides) != len(plan["slides"]): errors.append("slide count differs from slide plan")
        for expected, slide in zip(plan["slides"], prs.slides):
            text = "\n".join(shape.text for shape in slide.shapes if hasattr(shape, "text"))
            if expected["title"] not in text: errors.append(f"missing title: {expected['slide_id']}")
            if expected["layout"] not in {"title", "section"} and expected["source_ids"] and _source_text(expected["source_ids"], plan["sources"]) not in text: errors.append(f"source footer mismatch: {expected['slide_id']}")
        planned_images = sum(len(s["figure_ids"]) for s in plan["slides"])
        if len(media) < len({f for s in plan["slides"] for f in s["figure_ids"]}): errors.append("not all planned images were embedded")
        content = "\n".join(shape.text for slide in prs.slides for shape in slide.shapes if hasattr(shape, "text"))
        if re.search(r"[A-Za-z]:[\\/]", content): errors.append("absolute local path leaked into PPTX")
        source_slide = any(spec["layout"] == "sources" for spec in plan["slides"])
        if not source_slide: errors.append("reference slide is missing")
    except Exception as exc: errors.append(f"validation exception: {type(exc).__name__}: {exc}")
    return {"embedded_image_count": len(media) if 'media' in locals() else 0, "errors": errors}


def render_pptx(input_path: str | Path, output_path: str | Path, *, mode="presentation", max_slides=15, sources_path: str | Path | None = None) -> Path:
    markdown, output = Path(input_path).resolve(), Path(output_path).resolve()
    plan, warnings = build_slide_plan(markdown, mode=mode, max_slides=max_slides, sources_path=sources_path)
    plan_path = output.with_suffix(".slide-plan.json")
    output.parent.mkdir(parents=True, exist_ok=True); plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    with tempfile.TemporaryDirectory(dir=output.parent, prefix="render-pptx-") as tempdir:
        staged = Path(tempdir) / output.name; _draw(plan, markdown, staged); validation = _validate(staged, plan, markdown)
        report = {"status": "PASS" if not validation["errors"] else "FAIL", "backend": "local_python_pptx", "mode": mode, "slide_count": len(plan["slides"]), "source_count": len(plan["sources"]), "image_reference_count": sum(len(s["figure_ids"]) for s in plan["slides"]), "embedded_image_count": validation["embedded_image_count"], "table_count": sum(1 for s in plan["slides"] if s["layout"] == "table"), "overflow_slide_count": sum(1 for s in plan["slides"] if "（续）" in s["title"]), "checked_numeric_tokens": NUMERIC.findall(markdown.read_text(encoding="utf-8")), "warnings": warnings, "errors": validation["errors"]}
        if report["errors"]: raise RenderPptxError("; ".join(report["errors"]))
        os.replace(staged, output)
    report_path = output.with_suffix(".pptx.render_report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path
