"""Ark-led eight-page visual calibration, resumable and isolated from V2/V3.

Ark owns the page composition.  This runner only supplies locked facts and
rendered visual references, persists every response, validates the returned
Scene Graphs, applies the existing common chrome, and renders with PowerPoint.
"""
from __future__ import annotations

import base64
import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path

from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.util import Inches

ROOT = Path(__file__).resolve().parent.parent
FULL15 = ROOT / "outputs" / "ppt_pure_art_director" / "full15"
BASE = FULL15 / "mainline_expanded_v2"
V3 = FULL15 / "visual_calibration_v3"
OUT = FULL15 / "visual_calibration_v4_ark"
PICK = (1, 2, 3, 5, 6, 14, 19, 20)
NAMES = {1: "G", 2: "TOC", 3: "BACKGROUND", 5: "H", 6: "ARCH", 14: "D", 19: "E", 20: "CLOSE"}
APPROVED_TEXT = ""
V3_PAGE_MAP = {1: 1, 2: 2, 3: 3, 5: 4, 6: 5, 14: 6, 19: 7, 20: 8}
REFERENCE_SHEETS = (
    ROOT / "outputs" / "ppt_template_library_v1" / "source_analysis" / "comking_company" / "contact_sheet.png",
    ROOT / "outputs" / "ppt_template_library_v1" / "source_analysis" / "fujian" / "contact_sheet.png",
    ROOT / "outputs" / "ppt_template_library_v1" / "source_analysis" / "industrial_park" / "contact_sheet.png",
    ROOT / "outputs" / "ppt_template_library_v1" / "source_analysis" / "jiamusi" / "contact_sheet.png",
)


class ModelCallBudget:
    """A producer-wide metered-call budget consumed before every Ark request."""
    def __init__(self, maximum: int) -> None:
        if maximum <= 0:
            raise ValueError("model call budget must be positive")
        self.maximum, self.used = maximum, 0

    def consume(self, _call_id: str) -> None:
        if self.used >= self.maximum:
            raise RuntimeError("PPT model call budget exhausted before Ark request")
        self.used += 1


MODEL_CALL_BUDGET: ModelCallBudget | None = None


def configure_model_call_budget(maximum: int | None) -> None:
    global MODEL_CALL_BUDGET
    MODEL_CALL_BUDGET = ModelCallBudget(maximum) if maximum is not None else None


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


PAD = load("pad_v4", "run_ppt_pure_art_director.py")
EDITORIAL = load("editorial_v4", "run_ppt_pure_art_director_editorial.py")


def say(message: str) -> None:
    print(f"[{time.strftime('%H:%M')}] {message}", flush=True)


def page_png(folder: Path, page: int) -> Path:
    matches = [p for p in (folder / "pages" / "slide").glob("*.png") if re.search(r"(\d+)$", p.stem) and int(re.search(r"(\d+)$", p.stem).group(1)) == page]
    if len(matches) != 1:
        raise RuntimeError(f"could not locate rendered page {page} in {folder}")
    return matches[0]


def image_url(path: Path, limit: int = 1100) -> str:
    """Use a bounded reference image, keeping each Ark request practical."""
    with Image.open(path) as source:
        image = source.convert("RGB")
        if max(image.size) > limit:
            scale = limit / max(image.size)
            image = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
        from io import BytesIO
        stream = BytesIO(); image.save(stream, format="JPEG", quality=86, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(stream.getvalue()).decode("ascii")


def image_part(path: Path, label: str) -> list[dict]:
    return [{"type": "text", "text": label}, {"type": "image_url", "image_url": {"url": image_url(path)}}]


def source_scene(page: int) -> dict:
    if APPROVED_TEXT:
        return {"elements": []}
    path = BASE / "scene_graphs" / f"page_{page:02}_{NAMES[page]}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def text_content(scene: dict) -> list[str]:
    return [str(e["text"]) for e in scene.get("elements", []) if e.get("type") == "text" and str(e.get("text", "")).strip()]


def allowed_assets(scene: dict) -> list[str]:
    return sorted({str(e["image_source"]) for e in scene.get("elements", []) if e.get("type") == "image" and str(e.get("image_source", "")).startswith("assets/")})


def context_for(page: int) -> dict:
    scene = source_scene(page)
    return {
        "page": page,
        "page_name": NAMES[page],
        "locked_display_text": text_content(scene),
        "allowed_assets": allowed_assets(scene),
        "known_constraint": "Image image-001.jpg is only 537x489; do not make it a full-bleed or enlarged hero image." if page == 5 else None,
        "approved_markdown": APPROVED_TEXT if APPROVED_TEXT else None,
    }


def record_call(item: dict) -> None:
    path = OUT / "ark_calls.json"
    calls = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    calls.append(item); path.write_text(json.dumps(calls, ensure_ascii=False, indent=2), encoding="utf-8")


def ask_json(*, call_id: str, system: str, content: list[dict], raw_file: Path, max_tokens: int = 12000) -> tuple[dict, dict]:
    """Perform one streaming Ark call and persist its unmodified answer first."""
    if MODEL_CALL_BUDGET is not None:
        MODEL_CALL_BUDGET.consume(call_id)
    started = time.time(); say(f"Ark {call_id} request started")
    raw, meta = PAD.ask([{"role": "system", "content": system}, {"role": "user", "content": content}], call_id, max_tokens=max_tokens)
    raw_file.parent.mkdir(parents=True, exist_ok=True); raw_file.write_text(raw, encoding="utf-8")
    try:
        parsed = PAD.as_json(raw)
    except Exception as exc:
        record_call({"call_id": call_id, **meta, "started_at": started, "status": "PARSE_FAILED", "input_image_count": sum(1 for x in content if x.get("type") == "image_url"), "response_path": str(raw_file.resolve().relative_to(OUT.resolve())), "error": type(exc).__name__})
        raise
    record_call({"call_id": call_id, **meta, "started_at": started, "status": "OK", "input_image_count": sum(1 for x in content if x.get("type") == "image_url"), "response_path": str(raw_file.resolve().relative_to(OUT.resolve()))})
    say(f"Ark {call_id} saved ({meta['elapsed_seconds']}s; ttft={meta['ttft_seconds']}s)")
    return parsed, meta


def art_direction() -> dict:
    target = OUT / "global_art_direction.json"
    if target.is_file():
        say("global art direction checkpoint reused")
        return json.loads(target.read_text(encoding="utf-8"))
    content = [{"type": "text", "text": "You are the global art director for an enterprise COMKING hospital smart-energy proposal. The following are real visual references: four company decks, then V2 and V3 calibration contact sheets. Analyze them visually, not by assuming textual descriptions."}]
    labels = ("COMKING company deck contact sheet", "Fujian nuclear smart-building deck contact sheet", "industrial-park smart-energy deck contact sheet", "Jiamusi university comprehensive-energy deck contact sheet")
    for label, sheet in zip(labels, REFERENCE_SHEETS): content.extend(image_part(sheet, label))
    content.extend(image_part(BASE / "contact_sheet.png", "Current complete V2 contact sheet"))
    content.extend(image_part(V3 / "contact_sheet.png", "Local V3 eight-page calibration contact sheet"))
    content.append({"type": "text", "text": json.dumps({"target_pages": [context_for(x) for x in PICK], "strict": ["Use mature corporate proposal design, not web UI", "Avoid repeated rounded-card grids, pill labels, meaningless diagonal or crossing lines", "Make diagrams communicate relationships", "Keep all text editable and preserve facts/boundaries", "Use one official logo and renderer-owned footer only"], "required_keys": ["design_intent", "visual_personality", "color_system", "typography_hierarchy", "spacing_rhythm", "image_treatment", "diagram_language", "brand_motif", "page_families", "density_strategy", "do_not_use", "page_directions"]}, ensure_ascii=False)})
    system = "Return only a valid JSON object. You are judging actual attached images. Do not output a slide Scene Graph in this call; output the requested Global Art Direction object with concise, actionable page directions for pages 1,2,3,5,6,14,19,20."
    value, _ = ask_json(call_id="global_art_direction", system=system, content=content, raw_file=OUT / "global_art_direction_raw.json", max_tokens=7000)
    required = {"design_intent", "visual_personality", "color_system", "typography_hierarchy", "spacing_rhythm", "image_treatment", "diagram_language", "brand_motif", "page_families", "density_strategy", "do_not_use", "page_directions"}
    missing = sorted(required - set(value))
    if missing: raise RuntimeError("global art direction missing keys: " + ", ".join(missing))
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return value


def page_request(page: int, direction: dict, *, revision: bool = False) -> dict:
    scene_path = OUT / "scene_graphs" / f"page_{page:02}_{NAMES[page]}.json"
    if scene_path.is_file() and not revision:
        say(f"page {page:02} scene checkpoint reused")
        return json.loads(scene_path.read_text(encoding="utf-8"))
    ctx = context_for(page)
    content = [{"type": "text", "text": "Attached first: V2 current rendering; second: V3 local calibration. Use them as visual baselines, but create a materially more mature composition based on Global Art Direction."}]
    content.extend(image_part(page_png(BASE, page), f"V2 page {page:02}"))
    if page in V3_PAGE_MAP:
        content.extend(image_part(page_png(V3, V3_PAGE_MAP[page]), f"V3 counterpart for original page {page:02}"))
    else:
        content.extend(image_part(V3 / "contact_sheet.png", "V3 calibration contact sheet: use as family reference, not as this page's layout"))
    content.extend(image_part(REFERENCE_SHEETS[0], "Closest real COMKING reference deck contact sheet"))
    content.append({"type": "text", "text": json.dumps({"global_art_direction": direction, "page_context": ctx, "canvas_inches": [13.333, 7.5], "safe_zones": {"logo": [0.45, 0.08, 2.05, 0.48], "footer": [0.55, 6.84, 12.15, 0.48]}, "schema": {"root": ["background", "elements"], "element_types": ["text", "image", "rectangle", "rounded_rectangle", "circle", "line", "arrow"], "common": ["id", "type", "x", "y", "w", "h", "z_order"], "text": ["text", "font_size", "font_weight", "alignment", "color"], "image": ["image_source", "crop"], "line_or_arrow": ["from", "to", "color", "line_width"]}, "requirements": ["Return a complete parseable Scene Graph only", "Do not draw logo/footer/page number", "Keep all main content outside safe zones", "Use only allowed_assets; do not invent images", "All coordinates within canvas", "Text in normal body >=16pt; no markdown, URLs or new facts", "Do not rely on a local template; choose a page-specific composition", "The result must be directly editable with native PowerPoint shapes"]}, ensure_ascii=False)})
    if revision:
        previous = json.loads(scene_path.read_text(encoding="utf-8")); content.append({"type": "text", "text": "This is a single targeted revision after visual critique. Preserve locked facts and improve only the cited defects. Current Scene Graph: " + json.dumps(previous, ensure_ascii=False)})
    system = "You are a senior enterprise presentation art director. You can see the attached actual images. Return ONLY one JSON object Scene Graph, with background and elements. Do not explain your work. Avoid card-grid/UI conventions; every line, arrow, color block, or image crop must carry information or hierarchy."
    folder = OUT / ("revisions" if revision else "raw")
    raw = folder / f"page_{page:02}_{NAMES[page]}_{'revision_' if revision else ''}raw.json"
    value, _ = ask_json(call_id=f"page_{page:02}_{'revision' if revision else 'design'}", system=system, content=content, raw_file=raw)
    validation = PAD.validate(value, set(ctx["allowed_assets"]))
    if validation["hard_errors"] or validation["preclamp_out_of_bounds"]:
        raise RuntimeError(f"page {page:02} invalid Ark scene: {validation}")
    scene_path.parent.mkdir(parents=True, exist_ok=True); scene_path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return value


def draw(scenes: dict[int, dict]) -> Path:
    logo = EDITORIAL.extract_formal_logo(OUT / "assets")
    prs = Presentation(); prs.slide_width = Inches(PAD.W); prs.slide_height = Inches(PAD.H); blank = prs.slide_layouts[6]
    for page in PICK:
        slide = prs.slides.add_slide(blank)
        EDITORIAL.chrome(slide, page, logo_path=logo, total_pages=20)
        positions = {}
        for raw in sorted(scenes[page]["elements"], key=lambda e: float(e.get("z_order", e.get("z", 10)))):
            element = PAD.clamp(dict(raw)); typ = element.get("type")
            if typ in {"text", "number", "caption"}: shape = PAD.add_text(slide, element)
            elif typ == "image": shape = PAD.add_image(slide, element)
            elif typ in {"rectangle", "rounded_rectangle", "circle", "callout"}: shape = PAD.add_shape(slide, element)
            elif typ in {"line", "arrow"}: shape = PAD.add_line(slide, element, positions)
            else: continue
            positions[str(element["id"])] = shape
    deck = OUT / "pure_art_director_visual_calibration_v4_ark.pptx"; prs.save(deck); return deck


def render(deck: Path) -> list[Path]:
    pages = OUT / "pages"; pages.mkdir(parents=True, exist_ok=True)
    subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / "render_pptx_windows.ps1"), "-InputPath", str(deck), "-OutputDir", str(pages)], cwd=ROOT, check=True, timeout=420)
    cmd = "$a=New-Object -ComObject PowerPoint.Application;$a.Visible=$true;$p=$a.Presentations.Open('" + str(deck.resolve()).replace("'", "''") + "',$true,$true,$false);$p.SaveAs('" + str((OUT / "pure_art_director_visual_calibration_v4_ark.pdf").resolve()).replace("'", "''") + "',32);$p.Close();$a.Quit()"
    subprocess.run(["powershell", "-NoProfile", "-Command", cmd], check=True, timeout=420)
    pngs = sorted((pages / "slide").glob("*.png"), key=lambda p: int(re.search(r"(\d+)$", p.stem).group(1)))
    if len(pngs) != len(PICK): raise RuntimeError(f"COM render expected {len(PICK)} PNGs, got {len(pngs)}")
    return pngs


def contact(pngs: list[Path], path: Path, labels: list[str]) -> None:
    sample = Image.open(pngs[0]).convert("RGB"); w, gap, header = 480, 16, 28; h = round(sample.height * w / sample.width)
    sheet = Image.new("RGB", (w * 2 + gap, (h + header) * ((len(pngs) + 1) // 2)), "white"); draw = ImageDraw.Draw(sheet)
    for i, png in enumerate(pngs):
        x, y = (i % 2) * (w + gap), (i // 2) * (h + header); draw.text((x + 4, y + 4), labels[i], fill=PAD.TEXT)
        sheet.paste(Image.open(png).convert("RGB").resize((w, h)), (x, y + header))
    path.parent.mkdir(parents=True, exist_ok=True); sheet.save(path)


def compare(v4: list[Path]) -> Path:
    rows: list[tuple[str, Path]] = []
    for i, page in enumerate(PICK):
        v3_reference = page_png(V3, V3_PAGE_MAP[page]) if page in V3_PAGE_MAP else V3 / "contact_sheet.png"
        rows.extend([(f"V2 | {page:02}", page_png(BASE, page)), (f"V3 | {page:02}", v3_reference), (f"V4 Ark | {page:02}", v4[i])])
    w, h, header, gap = 360, 203, 25, 8; result = Image.new("RGB", (w * 3 + gap * 2, (h + header) * len(PICK)), "white"); draw = ImageDraw.Draw(result)
    for i, (label, path) in enumerate(rows):
        row, col = divmod(i, 3); x, y = col * (w + gap), row * (h + header); draw.text((x + 3, y + 3), label, fill=PAD.TEXT)
        result.paste(Image.open(path).convert("RGB").resize((w, h)), (x, y + header))
    target = OUT / "comparison_v2_v3_v4.png"; result.save(target); return target


def critic(direction: dict, sheet: Path, *, max_pages: int = 4) -> dict:
    target = OUT / "visual_critic.json"
    if target.is_file(): return json.loads(target.read_text(encoding="utf-8"))
    content = [{"type": "text", "text": f"You are the Visual Critic. Compare the attached V2/V3/V4 3-column sheet and real COMKING company-deck contact sheet. V4 is Ark-created. Select no more than {max_pages} V4 page numbers for a single targeted revision only if genuinely needed."}]
    content.extend(image_part(sheet, "V2 / V3 / V4 comparison: each row is an original page number"))
    content.extend(image_part(REFERENCE_SHEETS[0], "Real COMKING reference visual language"))
    content.append({"type": "text", "text": json.dumps({"global_direction": direction, "required": {"v4_better_than_v2_v3": "boolean", "selected_pages": "list of max 4 page numbers from [1,2,3,5,6,14,19,20]", "pages": "per-page critique", "revision_briefs": "page-specific defect / retain / target / locked-facts", "overall_verdict": "PASS or NEEDS_ONE_MORE_PASS"}}, ensure_ascii=False)})
    value, _ = ask_json(call_id="visual_critic", system="Return only the requested JSON Visual Critic report. Judge actual attached rendering, not intentions.", content=content, raw_file=OUT / "visual_critic_raw.json", max_tokens=6500)
    selected = [int(x) for x in value.get("selected_pages", []) if str(x).isdigit() and int(x) in PICK][:max_pages]
    value["selected_pages"] = selected; target.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"); return value


def report(direction: dict, scenes: dict[int, dict], critic_result: dict, rendered: list[Path]) -> None:
    audits = {str(p): PAD.validate(scenes[p], set(context_for(p)["allowed_assets"])) for p in PICK}
    text = {str(p): text_content(scenes[p]) for p in PICK}
    manifest = {"source_v2": str((BASE / "pure_art_director_mainline_expanded_v2.pptx").relative_to(ROOT)), "source_v3": str((V3 / "pure_art_director_visual_calibration_v3.pptx").relative_to(ROOT)), "pages": list(PICK), "model": PAD.MODEL, "ark_calls": len(json.loads((OUT / "ark_calls.json").read_text(encoding="utf-8"))), "scenes": {str(p): str((OUT / "scene_graphs" / f"page_{p:02}_{NAMES[p]}.json").resolve().relative_to(OUT.resolve())) for p in PICK}, "before_text": {str(p): text_content(source_scene(p)) for p in PICK}, "after_text": text, "text_change_reason": "Ark visual hierarchy and concision only; facts and boundary conditions were locked in prompts.", "audit": audits, "critic": critic_result, "rendered_pages": [str(x.resolve().relative_to(OUT.resolve())) for x in rendered]}
    (OUT / "visual_calibration_v4_report.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Resumable Ark V4 visual calibration")
    parser.add_argument("--page", type=int, choices=PICK, help="Generate exactly one missing V4 page Scene Graph")
    parser.add_argument("--finish", action="store_true", help="Render all saved pages, critique, and make capped revisions")
    parser.add_argument("--model-max-calls", type=int, help="hard Ark-call budget, consumed before every request")
    args = parser.parse_args()
    configure_model_call_budget(args.model_max_calls)
    OUT.mkdir(parents=True, exist_ok=True)
    for path in (BASE / "contact_sheet.png", V3 / "contact_sheet.png", *REFERENCE_SHEETS):
        if not path.is_file(): raise RuntimeError(f"required visual reference missing: {path}")
    if not PAD.read_env().get("ARK_API_KEY"): raise RuntimeError("ARK_API_KEY is missing")
    say("V2/V3/reference decks inspected; Ark configuration PRESENT")
    direction = art_direction()
    if args.page:
        page_request(args.page, direction)
        return
    scenes = {page: page_request(page, direction) for page in PICK}
    if not args.finish:
        return
    say("8/8 Ark Scene Graphs saved; PowerPoint COM rendering started")
    deck = draw(scenes); rendered = render(deck); v4_sheet = OUT / "contact_sheet.png"; contact(rendered, v4_sheet, [f"V4 Ark | {p:02}" for p in PICK]); comparison_sheet = compare(rendered)
    critic_result = critic(direction, comparison_sheet)
    selected = critic_result.get("selected_pages", [])
    if selected:
        say("critic selected " + ", ".join(f"{x:02}" for x in selected) + " for one targeted revision")
        for page in selected:
            before = OUT / "revisions" / f"page_{page:02}_{NAMES[page]}_before.png"; before.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(rendered[PICK.index(page)], before)
            scenes[page] = page_request(page, direction, revision=True)
        deck = draw(scenes); rendered = render(deck); contact(rendered, v4_sheet, [f"V4 Ark | {p:02}" for p in PICK]); comparison_sheet = compare(rendered)
    report(direction, scenes, critic_result, rendered)
    say("final PowerPoint COM render completed")


if __name__ == "__main__":
    try: main()
    except Exception as exc:
        print(f"V4_BLOCKED_OR_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        raise
