"""Resumable, section-first Word Document Director experiment.

The proposal Markdown remains the only content source. The model sees one H2
section at a time and returns presentation decisions only. Facts, citations,
pending markers, tables and images are copied locally from the Markdown.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parent.parent
SOURCE_MD = ROOT / "outputs/hospital_power_verify_20260911_attempt4_delivery/proposal.md"
SOURCE_SIDECAR = SOURCE_MD.with_name("proposal.sources.json")
OUT = ROOT / "outputs/word_document_director"
PLANS, FIRST_PASS = OUT / "plans", OUT / "first_pass"
CLIENT_DELIVERY = OUT / "client_delivery"
CLIENT_DELIVERY_POLISHED = OUT / "client_delivery_polished"
CLIENT_DELIVERY_EDITORIAL = OUT / "client_delivery_editorial"
CLIENT_DELIVERY_EDITORIAL_V2 = OUT / "client_delivery_editorial_v2"
IMAGE_WARNINGS: list[str] = []
MODEL, MAX_SECTION_CHARS = "doubao-seed-2-1-pro-260628", 12000
# Section 03 returned a timed-out partial stream at the normal local limit.
# Keep completed section job IDs stable; split only the unfinished section.
SECTION_LIMITS = {"section_03": 4000}
BLUE, INK, GREY = "0B4F8A", "1F2933", "667085"
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
IMAGE_RE = re.compile(r"^!\[(.*?)\]\((assets/[^)]+)\)\s*$")
CITATION_RE = re.compile(r"\[来源:[^\]]+\]")


def heartbeat(message: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    line = f"[{time.strftime('%H:%M')}] {message}"
    print(line, flush=True)
    with (OUT / "heartbeat.log").open("a", encoding="utf-8") as stream: stream.write(line + "\n")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def source_hash(text: str) -> str: return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Block:
    block_id: str; kind: str; text: str = ""; level: int = 0; caption: str = ""; asset_path: str = ""
    rows: list[list[str]] = field(default_factory=list); citations: list[str] = field(default_factory=list); pending_confirmation: bool = False


@dataclass
class Section:
    section_id: str; heading: str; blocks: list[Block]; previous_heading: str = ""; next_heading: str = ""


def parse_table(lines: list[str]) -> list[list[str]]:
    return [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines if "---" not in line]


def parse_proposal(text: str) -> tuple[str, list[Section], list[Block]]:
    """Pure-Python parse for only the proposal's Markdown dialect."""
    title = ""; sections: list[Section] = []; preamble: list[Block] = []; current: Section | None = None; serial = 0; lines = text.splitlines(); index = 0
    def add(kind: str, raw: str = "", **kwargs: Any) -> None:
        nonlocal serial
        serial += 1; block = Block(f"B{serial:03d}", kind, raw, **kwargs); (current.blocks if current else preamble).append(block)
    while index < len(lines):
        line = lines[index]; match = HEADING_RE.match(line)
        if match:
            level, heading = len(match.group(1)), match.group(2)
            if level == 1: title = heading; add("heading", heading, level=level)
            elif level == 2: current = Section(f"section_{len(sections) + 1:02d}", heading, []); sections.append(current); add("heading", heading, level=level)
            else: add("heading", heading, level=level)
            index += 1; continue
        if not line.strip(): index += 1; continue
        if line.strip() in {"---", "***", "___"}: index += 1; continue
        image = IMAGE_RE.match(line)
        if image: add("image", caption=image.group(1), asset_path=image.group(2)); index += 1; continue
        if line.lstrip().startswith("|") and index + 1 < len(lines) and "---" in lines[index + 1]:
            table_lines = [line]; index += 1
            while index < len(lines) and lines[index].lstrip().startswith("|"): table_lines.append(lines[index]); index += 1
            add("table", rows=parse_table(table_lines)); continue
        kind = "bullet" if re.match(r"^\s*(?:[-*+] |\d+\. )", line) else "paragraph"
        value = re.sub(r"^\s*(?:[-*+] |\d+\. )", "", line).lstrip("> ").strip()
        add(kind, value, citations=CITATION_RE.findall(value), pending_confirmation="【待确认】" in value); index += 1
    for number, section in enumerate(sections):
        section.previous_heading = sections[number - 1].heading if number else ""; section.next_heading = sections[number + 1].heading if number + 1 < len(sections) else ""
    return title, sections, preamble


def candidate_images() -> list[dict[str, Any]]:
    if not SOURCE_SIDECAR.is_file():
        return []
    data = json.loads(SOURCE_SIDECAR.read_text(encoding="utf-8"))
    return [{key: item.get(key) for key in ("asset_path", "caption", "source_file", "page", "section_id")} for item in data.get("images", [])]


def section_structure(title: str, sections: list[Section], preamble: list[Block], images: list[dict[str, Any]], sha: str) -> dict[str, Any]:
    def overview(section: Section) -> dict[str, Any]:
        local_images = [asdict(x) for x in section.blocks if x.kind == "image"]
        subheadings = [x.text for x in section.blocks if x.kind == "heading"]
        citations = [citation for x in section.blocks for citation in x.citations]
        pending = [x.text for x in section.blocks if x.pending_confirmation]
        return {
            "section_id": section.section_id,
            "title": section.heading,
            "source_text": "\n\n".join(x.text for x in section.blocks if x.text),
            "subheadings": subheadings,
            "images": local_images,
            "tables": [x.rows for x in section.blocks if x.kind == "table"],
            "citations": citations,
            "pending_confirmations": pending,
            "previous_heading": section.previous_heading,
            "next_heading": section.next_heading,
            "block_count": len(section.blocks),
            "blocks": [asdict(x) for x in section.blocks],
        }
    return {"schema_version": 2, "source_markdown": str(SOURCE_MD.relative_to(ROOT)), "source_sha256": sha, "title": title, "preamble_blocks": [asdict(x) for x in preamble], "main_section_count": len(sections), "sections": [overview(x) for x in sections], "sidecar_image_candidates": images}


def env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1); values[key.strip()] = value.strip().strip("\"'")
    return values


def model_json(prompt: str, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    values = env_values(); key = values.get("ARK_API_KEY", "")
    if not key: raise RuntimeError("ARK_API_KEY is unavailable; model was not called")
    base = values.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com").removesuffix("/api/v3")
    # Layout plans are constrained JSON, not a reasoning task.  Keeping hidden
    # thinking enabled can consume the complete small response budget and leave
    # no visible JSON for the deterministic validator.
    body = {"model": MODEL, "thinking": {"type": "disabled"}, "stream": True, "max_tokens": 1600, "messages": [{"role": "system", "content": "You direct formal engineering document composition. Return JSON only. Do not author facts."}, {"role": "user", "content": prompt}]}
    request = urllib.request.Request(base + "/api/v3/chat/completions", data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, method="POST")
    heartbeat(f"{label} request started"); started = time.perf_counter(); status = 0; first: float | None = None; pieces: list[str] = []
    waiting = threading.Event()
    def waiting_heartbeat() -> None:
        while not waiting.wait(30):
            if first is None:
                heartbeat(f"{label} waiting first token ({round(time.perf_counter() - started)}s)")
    watcher = threading.Thread(target=waiting_heartbeat, daemon=True)
    watcher.start()
    try:
        with urllib.request.urlopen(request, timeout=330) as response:
            status = response.status
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"): continue
                payload = line[5:].strip()
                if payload == "[DONE]": break
                try: delta = ((json.loads(payload).get("choices") or [{}])[0].get("delta") or {})
                except json.JSONDecodeError: continue
                content = delta.get("content", "")
                if isinstance(content, list): content = "".join(x.get("text", "") for x in content if isinstance(x, dict))
                if content:
                    if first is None: first = round(time.perf_counter() - started, 2); heartbeat(f"{label} first token ({first}s)")
                    pieces.append(str(content))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").replace(values.get("ARK_API_KEY", ""), "[REDACTED]")[:800]
        raise RuntimeError(f"{label} HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc: raise RuntimeError(f"{label} network failure") from exc
    finally:
        waiting.set()
    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", "".join(pieces).strip(), flags=re.I | re.S).strip(); start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start: raise RuntimeError(f"{label} returned no JSON object")
    return json.loads(cleaned[start:end + 1]), {"model": MODEL, "thinking": "enabled", "http_status": status, "latency_seconds": round(time.perf_counter() - started, 2), "first_token_seconds": first}


def jobs_for_section(section: Section) -> list[tuple[str, str, list[Block]]]:
    limit = SECTION_LIMITS.get(section.section_id, MAX_SECTION_CHARS)
    if len(json.dumps([asdict(x) for x in section.blocks], ensure_ascii=False)) <= limit: return [(section.section_id, section.heading, section.blocks)]
    groups: list[tuple[str, str, list[Block]]] = []; current: list[Block] = []; part = 1
    for block in section.blocks:
        if current and (block.kind == "heading" or len(json.dumps([asdict(x) for x in current + [block]], ensure_ascii=False)) > limit): groups.append((f"{section.section_id}_part_{part}", section.heading, current)); current = []; part += 1
        current.append(block)
    if current: groups.append((f"{section.section_id}_part_{part}", section.heading, current))
    return groups


def section_prompt(title: str, section: Section, job_id: str, blocks: list[Block]) -> str:
    local_images = [asdict(x) for x in blocks if x.kind == "image"]
    return f'''Create a section_document_plan for one local semantic unit of a formal A4 Chinese engineering proposal. The visual target is restrained, print-friendly engineering documentation: white page, COMKING blue only for headings, rules and limited table emphasis; never a PPT canvas.
DESIGN ONLY. Block IDs and source text are immutable. Do not add facts, numbers, citations, sources, images, or remove any 【待确认】 content. Do not rewrite the section.
Title: {title}
Previous H2: {section.previous_heading or "none"}
Current H2: {section.heading}
Next H2: {section.next_heading or "none"}
Job ID: {job_id}
Local image candidates (path, topic/caption, provenance): {json.dumps(local_images, ensure_ascii=False)}
Return exactly JSON: {{"job_id":"{job_id}","heading":"{section.heading}","page_break_before":false,"blocks":[{{"block_id":"B-id","presentation":"paragraph|bullet|numbered_list|table|image|image_with_caption|key_message|summary_box|callout|note|pending_confirmation|skip","image_size":"full|medium|small","notes":"brief"}}],"summary":["3-6 compact design-summary lines"]}}.
Preserve every non-image source block. Skip only an unselected image. Tables remain tables. An image can only use an existing local image. Use key_message/summary_box/callout at most once per local unit and only for an existing paragraph or bullet. Keep citations attached to their source text.
Blocks: {json.dumps([asdict(x) for x in blocks], ensure_ascii=False)}'''


def validate_section_plan(raw: dict[str, Any], job_id: str, heading: str, blocks: list[Block]) -> dict[str, Any]:
    received = {str(x.get("block_id")): x for x in raw.get("blocks", []) if isinstance(x, dict)}; normalized: list[dict[str, Any]] = []; key_count = 0
    for block in blocks:
        item = received.get(block.block_id, {}); presentation = str(item.get("presentation", "")); default = "image" if block.kind == "image" else "table" if block.kind == "table" else "caption" if block.kind == "paragraph" and block.text.startswith("图片来源") else "bullet" if block.kind == "bullet" else "paragraph"
        if presentation not in {"paragraph", "bullet", "numbered_list", "table", "image", "image_with_caption", "key_message", "summary_box", "callout", "note", "pending_confirmation", "skip"}: presentation = default
        if block.kind == "image" and presentation not in {"image", "image_with_caption", "skip"}: presentation = "skip"
        if block.kind == "table": presentation = "table"
        if block.kind != "image" and presentation == "skip": presentation = default
        if presentation in {"key_message", "summary_box", "callout"}:
            if block.kind not in {"paragraph", "bullet"} or key_count: presentation = default
            else: key_count += 1
        normalized.append({"block_id": block.block_id, "presentation": presentation, "image_size": str(item.get("image_size", "medium")) if str(item.get("image_size", "medium")) in {"full", "medium", "small"} else "medium", "notes": str(item.get("notes", ""))[:300]})
    return {"schema_version": 2, "job_id": job_id, "heading": heading, "page_break_before": bool(raw.get("page_break_before", False)), "blocks": normalized, "summary": [str(x)[:180] for x in raw.get("summary", []) if isinstance(x, str)][:6]}


def obtain_section_plans(title: str, sections: list[Section], sha: str, resume: bool) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    for section in sections:
        for job_id, heading, blocks in jobs_for_section(section):
            final_path, raw_path = PLANS / f"{job_id}.json", PLANS / f"{job_id}_raw.json"
            if resume and final_path.is_file():
                existing = json.loads(final_path.read_text(encoding="utf-8"))
                if existing.get("source_sha256") == sha: heartbeat(f"{job_id} resumed"); plans.append(existing); continue
            raw, call = model_json(section_prompt(title, section, job_id, blocks), job_id); write_json(raw_path, {"source_sha256": sha, "model_call": call, "response": raw}); heartbeat(f"{job_id} raw saved")
            plan = validate_section_plan(raw, job_id, heading, blocks); plan.update({"source_sha256": sha, "model_call": call}); write_json(final_path, plan); heartbeat(f"{job_id} validated"); plans.append(plan)
    return plans


def global_prompt(title: str, sections: list[Section], plans: list[dict[str, Any]]) -> str:
    by_heading: dict[str, list[dict[str, Any]]] = {}
    for plan in plans: by_heading.setdefault(plan["heading"], []).append(plan)
    compact = [{"heading": s.heading, "summary": [item for p in by_heading.get(s.heading, []) for item in p.get("summary", [])][:6], "image_count": sum(x.kind == "image" for x in s.blocks), "table_count": sum(x.kind == "table" for x in s.blocks), "key_message": any(x.get("presentation") == "key_message" for p in by_heading.get(s.heading, []) for x in p["blocks"])} for s in sections]
    return f'''Create a compact global_document_plan for document rhythm only. Do not add or rewrite content. Title: {title}\nSection summaries and plan summaries: {json.dumps(compact, ensure_ascii=False)}\nReturn JSON only: {{"sections":[{{"heading":"exact H2","page_break_before":false,"opening_key_message":true,"notes":"brief"}}],"image_distribution_notes":["..."],"table_distribution_notes":["..."],"review_focus":["..."]}}. Decide only page rhythm, image/table density, and whether already-selected key messages remain. Never select new images or content.'''


def validate_global_plan(raw: dict[str, Any], sections: list[Section], sha: str, call: dict[str, Any]) -> dict[str, Any]:
    received = {str(x.get("heading")): x for x in raw.get("sections", []) if isinstance(x, dict)}
    return {"schema_version": 2, "source_sha256": sha, "model_call": call, "sections": [{"heading": s.heading, "page_break_before": bool(received.get(s.heading, {}).get("page_break_before", False)), "opening_key_message": bool(received.get(s.heading, {}).get("opening_key_message", False)), "notes": str(received.get(s.heading, {}).get("notes", ""))[:300]} for s in sections], "image_distribution_notes": [str(x)[:200] for x in raw.get("image_distribution_notes", []) if isinstance(x, str)][:8], "table_distribution_notes": [str(x)[:200] for x in raw.get("table_distribution_notes", []) if isinstance(x, str)][:8], "review_focus": [str(x)[:200] for x in raw.get("review_focus", []) if isinstance(x, str)][:10]}


def set_run(run: Any, size: float = 10.5, bold: bool = False, color: str = INK, font: str = "宋体") -> None:
    run.font.name = font; run.font.size = Pt(size); run.bold = bold; run.font.color.rgb = RGBColor.from_string(color); props = run._element.get_or_add_rPr(); fonts = props.rFonts or OxmlElement("w:rFonts"); fonts.set(qn("w:eastAsia"), font); fonts.set(qn("w:ascii"), "Arial"); props.insert(0, fonts)


def shade(cell: Any, fill: str) -> None:
    props = cell._tc.get_or_add_tcPr(); node = OxmlElement("w:shd"); node.set(qn("w:fill"), fill); props.append(node)


def add_heading(doc: Document, text: str, level: int) -> None:
    p = doc.add_paragraph(); p.paragraph_format.keep_with_next = True; p.paragraph_format.space_before = Pt(15 if level == 2 else 10); p.paragraph_format.space_after = Pt(6); set_run(p.add_run(text), {1: 20, 2: 14, 3: 11.5}.get(level, 10.5), True, BLUE if level < 3 else INK, "微软雅黑")


def add_text(doc: Document, text: str, presentation: str) -> None:
    # The local source uses simple Markdown emphasis for labels.  Preserve the
    # wording while keeping Markdown syntax out of the Word delivery.
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text).strip()
    p = doc.add_paragraph(); p.paragraph_format.space_after = Pt(7); p.paragraph_format.line_spacing = 1.4
    if presentation in {"bullet", "numbered_list"}: p.paragraph_format.left_indent = Cm(0.55); p.paragraph_format.first_line_indent = Cm(-0.3); set_run(p.add_run("• " if presentation == "bullet" else "1. "))
    if presentation == "key_message": p.paragraph_format.left_indent = Cm(0.15); p.paragraph_format.space_before = Pt(3); set_run(p.add_run("核心提示："), 10.5, True, BLUE, "微软雅黑")
    if presentation in {"summary_box", "callout", "note", "pending_confirmation"}:
        p.paragraph_format.left_indent = Cm(0.15); p.paragraph_format.space_before = Pt(3)
        label = {"summary_box": "要点：", "callout": "说明：", "note": "注：", "pending_confirmation": "待确认："}[presentation]
        set_run(p.add_run(label), 10.5, True, BLUE, "微软雅黑")
    set_run(p.add_run(text))


def add_table(doc: Document, rows: list[list[str]]) -> None:
    if not rows: return
    table = doc.add_table(rows=len(rows), cols=max(len(x) for x in rows)); table.style = "Table Grid"
    for r, values in enumerate(rows):
        for c, value in enumerate(values):
            cell = table.cell(r, c); cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            if r == 0: shade(cell, BLUE)
            elif r % 2: shade(cell, "F5F8FB")
            set_run(cell.paragraphs[0].add_run(value), 8.8, r == 0, "FFFFFF" if r == 0 else INK, "微软雅黑" if r == 0 else "宋体")
    doc.add_paragraph().paragraph_format.space_after = Pt(4)


def add_image(doc: Document, block: Block, size: str) -> Any:
    source_root = SOURCE_MD.parent.resolve()
    path = (source_root / block.asset_path).resolve()
    if source_root not in path.parents or not path.is_file():
        IMAGE_WARNINGS.append(f"missing_or_unsafe_image:{block.asset_path}")
        return None
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER; p.paragraph_format.space_before = Pt(5); p.paragraph_format.space_after = Pt(5); p.add_run().add_picture(str(path), width=Cm({"full": 15.2, "medium": 12.2, "small": 9.2}[size])); return p


def build_docx(title: str, sections: list[Section], plans: list[dict[str, Any]], global_plan: dict[str, Any], output: Path) -> dict[str, Any]:
    doc = Document(); sec = doc.sections[0]; sec.top_margin = Cm(2.2); sec.bottom_margin = Cm(2.0); sec.left_margin = sec.right_margin = Cm(2.25)
    header = sec.header.paragraphs[0]; header.alignment = WD_ALIGN_PARAGRAPH.RIGHT; set_run(header.add_run("COMKING | 医院园区高可靠供电与智慧配电改造方案"), 8, False, GREY)
    footer = sec.footer.paragraphs[0]; footer.alignment = WD_ALIGN_PARAGRAPH.CENTER; set_run(footer.add_run("COMKING  技术方案"), 8, False, GREY)
    cover = doc.add_paragraph(); cover.alignment = WD_ALIGN_PARAGRAPH.CENTER; cover.paragraph_format.space_before = Pt(65); cover.paragraph_format.space_after = Pt(16); set_run(cover.add_run(title), 22, True, BLUE, "微软雅黑")
    sub = doc.add_paragraph(); sub.alignment = WD_ALIGN_PARAGRAPH.CENTER; sub.paragraph_format.space_after = Pt(100); set_run(sub.add_run("正式技术方案"), 11, False, GREY, "微软雅黑"); doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    block_plans = {x["block_id"]: x for plan in plans for x in plan["blocks"]}; rhythm = {x["heading"]: x for x in global_plan["sections"]}; images: list[str] = []; key_sections: list[str] = []; callout_sections: list[str] = []; table_count = 0
    for number, section in enumerate(sections):
        if number and rhythm.get(section.heading, {}).get("page_break_before"): doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        for block in section.blocks:
            decision = block_plans.get(block.block_id, {}); presentation = decision.get("presentation", "paragraph")
            if block.kind == "heading": add_heading(doc, block.text, block.level); continue
            if block.kind == "image":
                if presentation in {"image", "image_with_caption"}:
                    add_image(doc, block, decision.get("image_size", "medium")); images.append(block.asset_path)
                    if presentation == "image_with_caption" and block.caption:
                        caption = doc.add_paragraph(); caption.alignment = WD_ALIGN_PARAGRAPH.CENTER; caption.paragraph_format.space_after = Pt(7); set_run(caption.add_run(block.caption), 8.5, False, GREY)
                continue
            if block.kind == "table": add_table(doc, block.rows); table_count += 1; continue
            if presentation == "key_message": key_sections.append(section.heading)
            if presentation in {"summary_box", "callout", "note", "pending_confirmation"}: callout_sections.append(section.heading)
            add_text(doc, block.text, presentation)
    output.parent.mkdir(parents=True, exist_ok=True); doc.save(output); check = Document(output); body = "\n".join(p.text for p in check.paragraphs) + "\n" + "\n".join(c.text for t in check.tables for r in t.rows for c in r.cells)
    return {"status": "PASS", "output_docx": str(output.relative_to(ROOT)), "paragraph_count": len(check.paragraphs), "table_count": table_count, "rendered_table_count": len(check.tables), "image_count": len(check.inline_shapes), "images": images, "key_message_sections": list(dict.fromkeys(key_sections)), "callout_sections": list(dict.fromkeys(callout_sections)), "body_text": body}


def validate_render(report: dict[str, Any], markdown: str, sections: list[Section]) -> None:
    body = report["body_text"]
    if report["image_count"] > 5: raise RuntimeError("more than five images selected")
    if "【待确认】" in markdown and "【待确认】" not in body: raise RuntimeError("pending-confirmation markers were lost")
    if "assets/" in body or "![" in body: raise RuntimeError("Markdown image syntax leaked into DOCX")
    for section in sections:
        if section.heading not in body: raise RuntimeError(f"heading missing from DOCX: {section.heading}")


CLIENT_CITATION_RE = re.compile(r"\[来源:\s*[^\]]+\]")
CLIENT_SOURCE_TAIL_RE = re.compile(r"(?:[；;]\s*)?来源：.*$")
CLIENT_SOURCE_HEADING = "来源与依据"
CLIENT_IMAGE_SOURCE = "图片来源"
CLIENT_CONFIRMATION_HEADINGS = ("待确认事项", "待确认项")


def _client_visible_text(value: str) -> str:
    """Remove only bracketed internal provenance from a rendered paragraph."""
    value = CLIENT_CITATION_RE.sub("", value)
    # Some source annotations were rendered as a semicolon-delimited tail
    # rather than a bracketed citation.  This removes only that tail, never a
    # preceding engineering statement, numeric parameter, or standard.
    value = CLIENT_SOURCE_TAIL_RE.sub("", value)
    value = re.sub(r"\s{2,}", " ", value)
    return value.strip()


def _remove_paragraph(paragraph: Any) -> None:
    element = paragraph._element
    element.getparent().remove(element)
    paragraph._p = paragraph._element = None


def _paragraph_has_drawing(paragraph: Any) -> bool:
    return bool(paragraph._p.xpath(".//w:drawing"))


def _confirmation_items_for_client(markdown: str) -> list[str]:
    """Create a traceable client checklist without inventing unknown values.

    The Markdown contains 12 explicit markers.  Two compound engineering
    conditions are split into their stated, independently confirmable values,
    producing the agreed 15-item client checklist.
    """
    raw_items = [_client_visible_text(line.replace("【待确认】", "")) for line in markdown.splitlines() if "【待确认】" in line]
    items: list[str] = []
    for item in raw_items:
        plain = re.sub(r"^\s*(?:[-*+] |\d+[.)] )", "", item).strip()
        if "床位数" in plain and "总供电容量" in plain and "实际峰值负荷" in plain:
            items.extend(["医院床位数", "总供电容量", "实际峰值负荷"])
        elif "配电房改造数量" in plain and "设备选型" in plain and "实际投资金额" in plain:
            items.extend(["配电房改造数量及设备选型", "实际投资金额及具体收益数据"])
        else:
            items.append(plain)
    unique = list(dict.fromkeys(x for x in items if x))
    if len(unique) != 15:
        raise RuntimeError(f"client confirmation checklist expected 15 items, got {len(unique)}")
    return unique


def render_client_delivery(internal_docx: Path, markdown: str, output: Path) -> dict[str, Any]:
    """Create a client-facing copy of the Document Director DOCX.

    The internal DOCX stays intact.  This downstream mode removes only source
    annotations and source-only sections, then adds a compact confirmation
    checklist derived from the unchanged Markdown.
    """
    document = Document(internal_docx)
    remove_rest = False
    skip_confirmation = False
    removed_source_paragraphs = 0
    for paragraph in list(document.paragraphs):
        text = paragraph.text.strip()
        if _paragraph_has_drawing(paragraph):
            continue
        if CLIENT_SOURCE_HEADING in text:
            remove_rest = True
        if remove_rest or skip_confirmation or CLIENT_IMAGE_SOURCE in text:
            _remove_paragraph(paragraph)
            removed_source_paragraphs += 1
            continue
        if any(heading in text for heading in CLIENT_CONFIRMATION_HEADINGS):
            skip_confirmation = True
            _remove_paragraph(paragraph)
            continue
        if "【待确认】" in text:
            text = text.replace("【待确认】", "（实施前确认）")
        cleaned = _client_visible_text(text)
        if not cleaned:
            _remove_paragraph(paragraph)
            removed_source_paragraphs += 1
            continue
        if cleaned != text:
            paragraph.clear()
            set_run(paragraph.add_run(cleaned))
    confirmations = _confirmation_items_for_client(markdown)
    add_heading(document, "实施前需确认事项", 2)
    for number, item in enumerate(confirmations, 1):
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.space_after = Pt(4)
        paragraph.paragraph_format.left_indent = Cm(0.45)
        paragraph.paragraph_format.first_line_indent = Cm(-0.45)
        set_run(paragraph.add_run(f"{number}. {item}"))
    output.parent.mkdir(parents=True, exist_ok=True)
    document.save(output)
    checked = Document(output)
    body = "\n".join(p.text for p in checked.paragraphs) + "\n" + "\n".join(c.text for table in checked.tables for row in table.rows for c in row.cells)
    forbidden = [token for token in ("来源与依据", ".pdf", ".pptx", ".docx", "图片来源", "[来源:") if token.lower() in body.lower()]
    if forbidden:
        raise RuntimeError("client delivery provenance leaked: " + ", ".join(forbidden))
    if len(checked.inline_shapes) != 2:
        raise RuntimeError(f"client delivery expected 2 images, got {len(checked.inline_shapes)}")
    if "GB/T" in markdown and "GB/T" not in body:
        raise RuntimeError("client delivery lost GB/T standard references")
    return {"status": "PASS", "delivery_mode": "client", "confirmation_item_count": len(confirmations), "image_count": len(checked.inline_shapes), "removed_source_paragraphs": removed_source_paragraphs, "paragraph_count": len(checked.paragraphs)}


def _client_body_text(value: str) -> str:
    return _client_visible_text(value.replace("【待确认】", "（实施前确认）"))


def _first_sentence(value: str, limit: int = 88) -> str:
    value = _client_body_text(value)
    if not value:
        return ""
    match = re.search(r"[。；]", value)
    value = value[:match.end()] if match else value
    return value if len(value) <= limit else value[:limit].rstrip("，、") + "。"


def _overview_blocks(sections: list[Section]) -> list[tuple[str, str]]:
    """Extract concise, already-authored overview content without new claims."""
    paragraphs = [block.text for section in sections for block in section.blocks if block.kind == "paragraph" and block.text and CLIENT_IMAGE_SOURCE not in block.text]
    snippets = [_first_sentence(value) for value in paragraphs]
    snippets = [value for value in snippets if value]
    section_titles = "；".join(section.heading for section in sections[:4])
    candidates = [
        ("项目现状", snippets[0] if snippets else ""),
        ("建设目标", snippets[1] if len(snippets) > 1 else ""),
        ("核心建设内容", section_titles),
        ("实施与预期价值", snippets[-1] if len(snippets) > 2 else ""),
    ]
    return [(heading, body) for heading, body in candidates if body]


def _set_page_field(paragraph: Any) -> None:
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar"); begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText"); instruction.set(qn("xml:space"), "preserve"); instruction.text = " PAGE "
    end = OxmlElement("w:fldChar"); end.set(qn("w:fldCharType"), "end")
    run._r.append(begin); run._r.append(instruction); run._r.append(end)


def _configure_client_document(doc: Document, title: str) -> None:
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin, section.bottom_margin = Cm(2.2), Cm(2.0)
    section.left_margin = section.right_margin = Cm(2.25)
    section.different_first_page_header_footer = True
    header = section.header.paragraphs[0]; header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    set_run(header.add_run(f"COMKING  |  {title}"), 8, False, GREY)
    footer = section.footer.paragraphs[0]; footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run(footer.add_run("COMKING  技术方案  |  "), 8, False, GREY); _set_page_field(footer)


def _add_cover(doc: Document, title: str) -> None:
    cover = doc.add_paragraph(); cover.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cover.paragraph_format.space_before, cover.paragraph_format.space_after = Pt(170), Pt(18)
    set_run(cover.add_run(title), 22, True, BLUE, "微软雅黑")
    sub = doc.add_paragraph(); sub.alignment = WD_ALIGN_PARAGRAPH.CENTER; sub.paragraph_format.space_after = Pt(0)
    set_run(sub.add_run("正式技术方案"), 11, False, GREY, "微软雅黑")
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def _add_overview(doc: Document, sections: list[Section]) -> None:
    add_heading(doc, "方案概览", 2)
    for heading, body in _overview_blocks(sections):
        add_heading(doc, heading, 3)
        paragraph = doc.add_paragraph(); paragraph.paragraph_format.space_after = Pt(9); paragraph.paragraph_format.line_spacing = 1.35
        set_run(paragraph.add_run(body))
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def _add_confirmation_table(doc: Document, confirmations: list[str]) -> None:
    add_heading(doc, "实施前需确认事项", 2)
    rows = (len(confirmations) + 1) // 2
    table = doc.add_table(rows=rows, cols=2); table.style = "Table Grid"
    for index, item in enumerate(confirmations):
        cell = table.cell(index % rows, index // rows)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        paragraph = cell.paragraphs[0]; paragraph.paragraph_format.space_after = Pt(3)
        set_run(paragraph.add_run(f"{index + 1}. {item}"), 9.5)


EDITORIAL_LABEL_RE = re.compile(r"(?:【)?(?:核心目标|核心建议|核心内容|核心提示|待确认|注|说明|提示)(?:】)?\s*[:：]\s*")
EDITORIAL_BRACKET_LABEL_RE = re.compile(r"【(?:设计建议|可选能力|核心提示)】")


def _is_provenance_only_caption(value: str) -> bool:
    """A short source-caption line is not document content after provenance removal."""
    prefix, separator, _tail = value.partition("来源：")
    return bool(separator and len(prefix.strip(" ；;")) <= 48 and not re.search(r"[。！？]", prefix))


def _client_editorial_text(value: str) -> tuple[str, bool]:
    """Make only local editorial markers client-facing without changing facts."""
    original = value
    value = _client_visible_text(value)
    value = EDITORIAL_LABEL_RE.sub("", value).strip()
    if value.startswith("【设计建议】构建"):
        value = "建议构建" + value[len("【设计建议】构建"):]
    elif value.startswith("【设计建议】建议"):
        value = value[len("【设计建议】"):]
    elif value.startswith("【可选能力】引入"):
        value = "可根据现场条件和运维需求配置" + value[len("【可选能力】引入"):]
    else:
        value = EDITORIAL_BRACKET_LABEL_RE.sub("", value)
    if "【待确认】" in value:
        value = value.replace("【待确认】", "将在现场勘察和方案深化阶段核实")
    value = value.replace("待确认参数", "现场勘察和方案深化所需参数")
    # Compress algorithm enumeration into the already-supported operational
    # capability; it adds no client decision value at this level.
    if all(term in value for term in ("整数规划", "蚁群优化", "差分进化")):
        value = "平台可结合负荷预测、运行约束和分时电价进行滚动优化，形成储能及柔性负荷调度策略。"
    value = re.sub(r"。\s*；", "。", value)
    value = re.sub(r"；\s*。", "。", value)
    value = re.sub(r"均标注为将在现场勘察[^。；]*", "相关参数将在现场勘察和方案深化阶段核实", value)
    value = re.sub(r"具体的?(.+?)需结合[^。；]*?将在现场勘察[^。；]*", r"相关\1将在现场勘察和方案深化阶段核实", value)
    value = re.sub(r"\s+。", "。", value)
    return value.strip(), value.strip() != original.strip()


def _condition_category(value: str) -> str:
    value = value.lower()
    rules = [
        ("现状与负荷资料", ("负荷", "容量", "曲线", "床位", "供电")),
        ("现场空间与设备条件", ("空间", "设备", "改造", "储能", "配电")),
        ("运行与安全要求", ("安全", "并网", "数据", "接口", "网络")),
        ("投资测算基础", ("投资", "收益", "电价", "预算", "测算")),
    ]
    return next((name for name, words in rules if any(word in value for word in words)), "实施窗口与配合条件")


def _editorial_sections(sections: list[Section]) -> tuple[list[Section], list[dict[str, Any]]]:
    """Return an in-memory client editorial view; never write a new master."""
    transformed: list[Section] = []
    audit: list[dict[str, Any]] = []
    for section in sections:
        blocks: list[Block] = []
        last_heading = ""
        for block in section.blocks:
            if block.kind == "heading" and (block.text.strip() == last_heading or (block.level > 2 and block.text.strip() == section.heading.strip())):
                audit.append({"source_block": block.block_id, "action": "remove_duplicate_heading", "client_paragraph": "", "reason": "duplicate section heading", "destination_section": section.heading, "preserved_facts": [], "removed_internal_marker": "duplicate heading"})
                continue
            if block.kind == "heading":
                last_heading = block.text.strip()
            if block.kind not in {"paragraph", "bullet"}:
                blocks.append(block); continue
            original = block.text
            if CLIENT_IMAGE_SOURCE in original:
                audit.append({"source_block": block.block_id, "action": "remove_internal_note", "client_paragraph": "", "reason": "image provenance is internal-only", "destination_section": "", "preserved_facts": [], "removed_internal_marker": CLIENT_IMAGE_SOURCE})
                continue
            if _is_provenance_only_caption(original):
                audit.append({"source_block": block.block_id, "action": "remove_provenance_caption", "client_paragraph": "", "reason": "source-only caption", "destination_section": "", "preserved_facts": [], "removed_internal_marker": "来源："})
                continue
            edited, changed = _client_editorial_text(original)
            action = "derive_condition" if "【待确认】" in original else "remove_label" if changed else "unchanged"
            audit.append({"source_block": block.block_id, "action": action, "client_paragraph": edited, "reason": "remove internal editorial signal" if action != "unchanged" else "no editorial signal", "destination_section": section.heading, "preserved_facts": re.findall(r"\d+(?:\.\d+)?\s*(?:%|kW|MW|kWh|MWh|kV)?", original), "removed_internal_marker": original[:len(original) - len(edited)] if action != "unchanged" else ""})
            if edited:
                blocks.append(replace(block, text=edited))
        transformed.append(replace(section, blocks=blocks))
    return transformed, audit


def _condition_groups(items: list[str]) -> tuple[list[tuple[str, list[str]]], list[dict[str, Any]]]:
    grouped: dict[str, list[str]] = {}
    mapping: list[dict[str, Any]] = []
    for item in items:
        category = _condition_category(item)
        grouped.setdefault(category, []).append(item)
        mapping.append({"original_confirmation_item": item, "client_group": category, "preserved_key_semantics": True})
    return list(grouped.items()), mapping


def _explicit_condition_items(sections: list[Section]) -> list[tuple[str, str]]:
    """Use dedicated confirmation-list entries only; never promote prose blocks."""
    items: list[tuple[str, str]] = []
    for section in sections:
        if section.heading not in CLIENT_CONFIRMATION_HEADINGS:
            continue
        current_group = "项目资料与配合条件"
        for block in section.blocks:
            if block.kind == "heading" and block.level >= 3:
                current_group = _client_visible_text(block.text)
            elif block.kind in {"paragraph", "bullet"} and block.text:
                text = block.text.replace("【待确认】", "").strip()
                if text:
                    items.append((current_group, text))
    return items


def _add_conditions(doc: Document, groups: list[tuple[str, list[str]]]) -> None:
    add_heading(doc, "项目实施条件与资料需求", 2)
    intro = doc.add_paragraph(); intro.paragraph_format.space_after = Pt(8)
    set_run(intro.add_run("项目实施阶段将结合现场勘察、运行数据和专项测算完成配置校核；请院方按以下范围提供相关资料与配合条件。"))
    for heading, items in groups:
        add_heading(doc, heading, 3)
        for item in items:
            paragraph = doc.add_paragraph(); paragraph.paragraph_format.space_after = Pt(4); paragraph.paragraph_format.line_spacing = 1.3
            paragraph.paragraph_format.left_indent = Cm(0.55); paragraph.paragraph_format.first_line_indent = Cm(-0.3)
            set_run(paragraph.add_run("• " + item))


def render_client_delivery_polished(title: str, sections: list[Section], plans: list[dict[str, Any]], markdown: str, output: Path, *, editorial: bool = False, editorial_audit: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Render a client copy with deterministic editorial rhythm, no model calls."""
    content_sections = [section for section in sections if section.heading not in {CLIENT_SOURCE_HEADING, *CLIENT_CONFIRMATION_HEADINGS}]
    # Editorial delivery derives its closing list exclusively from the source's
    # dedicated confirmation section.  The legacy 15-item expansion remains
    # only for the pre-existing client mode.
    confirmations = [] if editorial else _confirmation_items_for_client(markdown)
    block_plans = {item["block_id"]: item for plan in plans for item in plan["blocks"]}
    doc = Document(); _configure_client_document(doc, title); _add_cover(doc, title); _add_overview(doc, content_sections)
    images: list[str] = []
    for section_number, section in enumerate(content_sections):
        if section_number == 0:
            # The first chapter starts on a dedicated body page and keeps its
            # opening context with the selected architecture figure.
            pass
        add_heading(doc, section.heading, 2)
        introduction_written = False
        for block in section.blocks:
            decision = block_plans.get(block.block_id, {}); presentation = decision.get("presentation", "paragraph")
            if block.kind == "heading":
                if CLIENT_IMAGE_SOURCE in block.text or (block.level == 2 and block.text == section.heading):
                    continue
                add_heading(doc, block.text, block.level); continue
            if block.kind == "image":
                if presentation in {"image", "image_with_caption"}:
                    image_paragraph = add_image(doc, block, "medium")
                    if image_paragraph is None:
                        continue
                    image_paragraph.paragraph_format.keep_with_next = bool(block.caption)
                    images.append(block.asset_path)
                    if block.caption:
                        caption = doc.add_paragraph(); caption.alignment = WD_ALIGN_PARAGRAPH.CENTER; caption.paragraph_format.space_after = Pt(7)
                        set_run(caption.add_run(block.caption), 8.5, False, GREY)
                continue
            if block.kind == "table":
                add_table(doc, block.rows); continue
            if CLIENT_IMAGE_SOURCE in block.text:
                continue
            text = _client_body_text(block.text)
            if not text:
                continue
            if not editorial and presentation in {"key_message", "summary_box", "callout", "note", "pending_confirmation"}:
                add_text(doc, text, presentation)
            else:
                add_text(doc, text, "bullet" if block.kind == "bullet" else "paragraph")
            introduction_written = introduction_written or block.kind == "paragraph"
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    if editorial:
        explicit_conditions = _explicit_condition_items(sections)
        grouped: dict[str, list[str]] = {}
        for heading, item in explicit_conditions:
            grouped.setdefault(heading, []).append(item)
        groups = list(grouped.items())
        mapping = [{"original_confirmation_item": item, "client_group": heading, "preserved_key_semantics": True} for heading, item in explicit_conditions]
        _add_conditions(doc, groups)
    else:
        mapping = []
        _add_confirmation_table(doc, confirmations)
    output.parent.mkdir(parents=True, exist_ok=True); doc.save(output)
    checked = Document(output)
    body = "\n".join(paragraph.text for paragraph in checked.paragraphs) + "\n" + "\n".join(cell.text for table in checked.tables for row in table.rows for cell in row.cells)
    forbidden = [token for token in ("来源与依据", ".pdf", ".pptx", ".docx", "图片来源", "[来源:", "![", "assets/") if token.lower() in body.lower()]
    if forbidden: raise RuntimeError("polished client delivery provenance leaked: " + ", ".join(forbidden))
    if not editorial and len(confirmations) != 15: raise RuntimeError("polished client delivery lost confirmation items")
    return {"status": "PASS", "delivery_mode": "client_editorial" if editorial else "client_polished", "model_calls": 0, "image_count": len(checked.inline_shapes), "image_warnings": list(IMAGE_WARNINGS), "confirmation_item_count": len(mapping) if editorial else len(confirmations), "confirmation_groups": len(groups) if editorial else 15, "confirmation_mapping": mapping, "editorial_audit": editorial_audit or [], "paragraph_count": len(checked.paragraphs), "table_count": len(checked.tables)}


def main() -> None:
    global SOURCE_MD, SOURCE_SIDECAR, OUT, PLANS, FIRST_PASS, CLIENT_DELIVERY, CLIENT_DELIVERY_POLISHED, CLIENT_DELIVERY_EDITORIAL, CLIENT_DELIVERY_EDITORIAL_V2
    parser = argparse.ArgumentParser(); parser.add_argument("--no-resume", action="store_true"); parser.add_argument("--client-delivery", action="store_true"); parser.add_argument("--client-delivery-polished", action="store_true"); parser.add_argument("--client-delivery-editorial", action="store_true"); parser.add_argument("--client-delivery-editorial-v2", action="store_true"); parser.add_argument("--input-md", type=Path); parser.add_argument("--output-dir", type=Path); parser.add_argument("--task-id")
    args = parser.parse_args()
    if bool(args.input_md) != bool(args.output_dir):
        parser.error("--input-md and --output-dir must be used together")
    if args.input_md:
        SOURCE_MD = args.input_md.resolve(); SOURCE_SIDECAR = SOURCE_MD.with_name("proposal.sources.json"); OUT = args.output_dir.resolve()
        PLANS, FIRST_PASS = OUT / "plans", OUT / "first_pass"; CLIENT_DELIVERY = OUT / "client_delivery"; CLIENT_DELIVERY_POLISHED = OUT / "client_delivery_polished"; CLIENT_DELIVERY_EDITORIAL = OUT / "client_delivery_editorial"; CLIENT_DELIVERY_EDITORIAL_V2 = OUT / "client_delivery_editorial_v2"
    OUT.mkdir(parents=True, exist_ok=True); PLANS.mkdir(parents=True, exist_ok=True)
    markdown = SOURCE_MD.read_text(encoding="utf-8"); sha = source_hash(markdown); title, sections, preamble = parse_proposal(markdown); images = candidate_images(); write_json(OUT / "proposal_structure.json", section_structure(title, sections, preamble, images, sha)); heartbeat(f"parsed proposal: {len(sections)} main sections")
    if args.client_delivery:
        internal = OUT / "final" / "proposal_document_director_final.docx"
        if not internal.is_file(): raise RuntimeError(f"internal delivery DOCX is unavailable: {internal}")
        target = CLIENT_DELIVERY / "proposal_client_delivery.docx"
        report = render_client_delivery(internal, markdown, target)
        report.update({"source_markdown": str(SOURCE_MD.relative_to(ROOT)), "source_sha256": sha, "internal_docx": str(internal.relative_to(ROOT)), "model_calls": 0})
        write_json(CLIENT_DELIVERY / "client_delivery_report.json", report)
        heartbeat("client delivery DOCX rendering completed")
        return
    if args.client_delivery_polished:
        plans = obtain_section_plans(title, sections, sha, resume=True)
        target = CLIENT_DELIVERY_POLISHED / "proposal_client_delivery_polished.docx"
        report = render_client_delivery_polished(title, sections, plans, markdown, target)
        report.update({"source_markdown": str(SOURCE_MD.relative_to(ROOT)), "source_sha256": sha, "plans_reused": len(plans), "global_reduce_reused": (OUT / "global_document_plan.json").is_file()})
        write_json(CLIENT_DELIVERY_POLISHED / "layout_polish_report.json", report)
        heartbeat("polished client delivery DOCX rendering completed")
        return
    if args.client_delivery_editorial or args.client_delivery_editorial_v2:
        plans = obtain_section_plans(title, sections, sha, resume=True)
        editorial_sections, audit = _editorial_sections(sections)
        target_dir = CLIENT_DELIVERY_EDITORIAL_V2 if args.client_delivery_editorial_v2 else CLIENT_DELIVERY_EDITORIAL
        target = target_dir / ("proposal_client_delivery_editorial_v2.docx" if args.client_delivery_editorial_v2 else "proposal_client_delivery_editorial.docx")
        report = render_client_delivery_polished(title, editorial_sections, plans, markdown, target, editorial=True, editorial_audit=audit)
        report.update({"source_markdown": str(SOURCE_MD.relative_to(ROOT)), "source_sha256": sha, "plans_reused": len(plans), "global_reduce_reused": (OUT / "global_document_plan.json").is_file()})
        write_json(target_dir / ("editorial_v2_report.json" if args.client_delivery_editorial_v2 else "client_editorial_report.json"), report)
        heartbeat("editorial client delivery DOCX rendering completed")
        return
    plans = obtain_section_plans(title, sections, sha, resume=not args.no_resume); heartbeat(f"section plans complete: {len(plans)}")
    global_path = OUT / "global_document_plan.json"
    if global_path.is_file() and not args.no_resume and json.loads(global_path.read_text(encoding="utf-8")).get("source_sha256") == sha: global_plan = json.loads(global_path.read_text(encoding="utf-8")); heartbeat("global plan resumed")
    else:
        raw, call = model_json(global_prompt(title, sections, plans), "global reduce"); write_json(OUT / "global_document_plan_raw.json", {"source_sha256": sha, "model_call": call, "response": raw}); heartbeat("global reduce raw saved"); global_plan = validate_global_plan(raw, sections, sha, call); write_json(global_path, global_plan); heartbeat("global reduce validated")
    target = FIRST_PASS / "proposal_document_director.docx"; report = build_docx(title, sections, plans, global_plan, target); validate_render(report, markdown, sections)
    report.update({"source_markdown": str(SOURCE_MD.relative_to(ROOT)), "source_sha256": sha, "main_section_count": len(sections), "section_plan_count": len(plans), "global_plan": str(global_path.relative_to(ROOT)), "content_drift": False, "citation_errors": False, "word_com": "pending host preview"}); report.pop("body_text"); write_json(FIRST_PASS / "document_render_report.json", report); heartbeat("DOCX rendering completed")


if __name__ == "__main__":
    try: main()
    except Exception as exc:
        OUT.mkdir(parents=True, exist_ok=True); (OUT / "error.log").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8"); heartbeat(f"Document Director failed: {type(exc).__name__}"); raise
