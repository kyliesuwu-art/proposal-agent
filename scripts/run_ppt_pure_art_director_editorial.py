"""Local editorial assembly for the completed Pure Art Director Full15 deck.

It never calls a model and never overwrites the completed Full15 source.
"""
from __future__ import annotations

import importlib.util
from io import BytesIO
import json
import re
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.util import Inches

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "outputs" / "ppt_pure_art_director" / "full15"
OUT = SOURCE / "editorial_polish"
ORDER = tuple("GIHAJBCKLDNF MEO".replace(" ", ""))
PROJECT_FOOTER = "医院园区智慧配电改造方案"
# This company introduction deck is the locally analysed COMKING source; its
# cover contains the approved wide image wordmark used on its formal pages.
# The government-deck cover embeds a white reverse logo, unsuitable for the
# light V4 content background. This formal COMKING proposal deck carries the
# approved blue wordmark with a high-contrast outline.
FORMAL_LOGO_DECK = ROOT / "files" / "工业园综合智慧能源解决方案.pptx"


def load_pad():
    spec = importlib.util.spec_from_file_location("pad", ROOT / "scripts" / "run_ppt_pure_art_director.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


PAD = load_pad()


def extract_formal_logo(destination: Path) -> Path:
    """Copy the embedded COMKING wordmark from the approved company template.

    The logo stays as its original image asset.  We select the wide picture on
    the template cover rather than recreating the mark with editable text.
    """
    destination.mkdir(parents=True, exist_ok=True)
    template = Presentation(FORMAL_LOGO_DECK)
    candidates = []
    for slide in template.slides:
        for shape in slide.shapes:
            if not hasattr(shape, "image") or not shape.width or not shape.height:
                continue
            ratio = shape.width / shape.height
            # The approved logo appears in the top chrome, not in a content image.
            if 2.5 < ratio < 12 and shape.top < Inches(.9) and shape.width < Inches(4):
                with Image.open(BytesIO(shape.image.blob)).convert("RGB") as image:
                    blue_pixels = sum(
                        1 for red, green, blue in image.resize((160, max(1, round(160 / ratio)))).getdata()
                        if blue > red + 25 and blue > green + 8 and blue < 245
                    )
                candidates.append((blue_pixels, ratio, shape))
    if not candidates:
        raise RuntimeError("approved COMKING logo image was not found in company template")
    # Prefer the blue original that is visible on light formal content pages.
    logo = max(candidates, key=lambda item: (item[0], item[1]))[2]
    ext = logo.image.ext or "png"
    result = destination / f"comking_official_logo.{ext}"
    result.write_bytes(logo.image.blob)
    return result


def chrome(slide, page_no: int, *, logo_path: Path | None = None, logo_source_shape=None, project_footer: str = PROJECT_FOOTER, total_pages: int = 15) -> None:
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = RGBColor(*PAD.BG)
    bar = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, 0, 0, Inches(.13), Inches(PAD.H))
    bar.fill.solid(); bar.fill.fore_color.rgb = RGBColor(*PAD.BLUE); bar.line.fill.background()
    if logo_source_shape is not None:
        # Clone the existing approved wordmark object, rather than recreating
        # its typography.  This preserves the previously verified asset.
        cloned = deepcopy(logo_source_shape._element)
        slide.shapes._spTree.insert_element_before(cloned, "p:extLst")
        logo = next(shape for shape in slide.shapes if shape.name == "brand_wordmark")
        logo.left, logo.top, logo.width, logo.height = Inches(.58), Inches(.17), Inches(1.72), Inches(.24)
    elif logo_path is not None:
        # The formal asset is positioned by width only, preserving its native ratio.
        logo = slide.shapes.add_picture(str(logo_path), Inches(.58), Inches(.16), width=Inches(1.72))
        logo.name = "brand_logo"
    else:
        raise RuntimeError("common chrome requires an approved logo source")
    line = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, Inches(.65), Inches(6.91), Inches(12.05), Inches(.02))
    line.fill.solid(); line.fill.fore_color.rgb = RGBColor(*PAD.LIGHT_BLUE); line.line.fill.background()
    PAD.add_text(slide, {"id": "footer_project", "x": .65, "y": 7.00, "w": 4.8, "h": .22, "text": project_footer, "font_size": 10, "color": "#405466"})
    PAD.add_text(slide, {"id": "footer_page", "x": 11.82, "y": 6.99, "w": .82, "h": .22, "text": f"{page_no:02} / {total_pages:02}", "font_size": 10, "font_weight": "bold", "color": "#1F497D", "alignment": "right"})


def editorial_scene(letter: str, source: dict) -> tuple[dict, list[str]]:
    scene = json.loads(json.dumps(source))
    changes: list[str] = []
    # Existing page-local logos, page numbers and footers predate the common
    # chrome. Keep a single deterministic source so branding never wraps or
    # displays a stale page number after editorial resequencing.
    original_count = len(scene["elements"])
    scene["elements"] = [
        e for e in scene["elements"]
        if not (
            (e.get("type") == "text" and e.get("text") == "COMKING")
            or any(token in str(e.get("id", "")).lower() for token in ("footer", "page_num", "page_number", "logo_top"))
        )
    ]
    if len(scene["elements"]) != original_count:
        changes.append("replaced page-local logo/footer/page number with common chrome")
    if letter == "G":
        # The isolated thumbnail was visually weaker than the cover's calm
        # editorial whitespace; remove only that nonessential asset.
        before = len(scene["elements"])
        scene["elements"] = [e for e in scene["elements"] if e.get("type") != "image"]
        if len(scene["elements"]) != before: changes.append("removed isolated cover thumbnail")
    if letter == "L":
        scene["elements"] = [e for e in scene["elements"] if e.get("type") != "image"]
        for e in scene["elements"]:
            ident = str(e.get("id", ""))
            if ident == "bg_text":
                e.update({"x": .65, "y": .85, "w": 12.0, "h": 5.9, "fill": "#F4F8FC", "z_order": 0})
            elif ident == "title":
                e.update({"x": .95, "y": 1.2, "w": 11.0, "h": .75, "font_size": 28, "color": "#1F497D", "z_order": 2})
            elif ident.startswith("dot"):
                index = int(ident[-1]); e.update({"x": 1.0, "y": 2.35 + (index - 1) * 1.05, "z_order": 2})
            elif ident.startswith("bullet"):
                e.update({"x": 1.32, "w": 10.7, "color": "#334155", "z_order": 2})
        changes.append("removed unusable 2.7KB image-003.jpg and rebuilt page as a readable dynamic-dispatch logic composition")
    if letter == "O":
        text_elements = [e for e in scene["elements"] if e.get("type") == "text"]
        if text_elements:
            title = max(text_elements, key=lambda e: float(e.get("font_size", 0)))
            title["color"] = "#1F497D"
            title["font_weight"] = "bold"
            changes.append("set closing title to deep-blue high contrast")
    return scene, changes


def mainline_scene(letter: str, source: dict) -> tuple[dict, list[str]]:
    """Apply only the two verified title-fit corrections to editorial scenes."""
    scene = json.loads(json.dumps(source))
    changes: list[str] = []
    if letter == "H":
        for element in scene["elements"]:
            if element.get("id") == "txt_title":
                element.update({"x": .6, "y": .85, "w": 4.45, "h": 1.3, "font_size": 27})
            elif element.get("id") == "txt_lead":
                element.update({"y": 2.3, "w": 4.35, "h": .85})
            elif element.get("id") == "img_main":
                element.update({"x": 5.15, "w": 7.85})
        changes.append("widened H title and adjusted lead position for natural Chinese line breaks")
    if letter == "B":
        for element in scene["elements"]:
            if element.get("id") == "page_title_prefix":
                element.update({"w": 1.08, "h": .48})
            elif element.get("id") == "page_title_highlight":
                element.update({"x": 1.75, "y": .8, "h": .48})
            elif element.get("id") == "page_title_suffix":
                element.update({"x": 2.22, "y": .8, "w": 10.25, "h": .75})
        changes.append("widened B title prefix and reset title segments to prevent an orphan Chinese character")
    return scene, changes


def draw(scenes: dict[str, dict], *, output: Path, logo_path: Path) -> Path:
    target = output
    prs = Presentation(); prs.slide_width = Inches(PAD.W); prs.slide_height = Inches(PAD.H); blank = prs.slide_layouts[6]
    for number, letter in enumerate(ORDER, 1):
        slide = prs.slides.add_slide(blank); chrome(slide, number, logo_path=logo_path); positions = {}
        for raw in sorted(scenes[letter]["elements"], key=lambda e: float(e.get("z_order", e.get("z", 10)))):
            e = PAD.clamp(dict(raw)); typ = e.get("type")
            if typ in {"text", "number", "caption"}: shape = PAD.add_text(slide, e)
            elif typ == "image": shape = PAD.add_image(slide, e)
            elif typ in {"rectangle", "rounded_rectangle", "circle", "callout"}: shape = PAD.add_shape(slide, e)
            elif typ in {"line", "arrow"}: shape = PAD.add_line(slide, e, positions)
            else: continue
            positions[str(e["id"])] = shape
    prs.save(target)
    return target


def render(deck: Path) -> list[Path]:
    pages = OUT / "pages"; pages.mkdir(parents=True, exist_ok=True)
    subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / "render_pptx_windows.ps1"), "-InputPath", str(deck), "-OutputDir", str(pages)], cwd=ROOT, check=True, timeout=420)
    pngs = sorted((pages / "slide").glob("*.png"), key=lambda p: int(re.search(r"(\d+)$", p.stem).group(1)))
    if len(pngs) != 15: raise RuntimeError(f"expected 15 rendered PNGs, got {len(pngs)}")
    return pngs


def contact(pngs: list[Path]) -> Path:
    first = Image.open(pngs[0]).convert("RGB"); width, gap, header = 340, 12, 28; height = round(first.height * width / first.width)
    sheet = Image.new("RGB", (width * 3 + gap * 2, (height + header) * 5), "white"); pen = ImageDraw.Draw(sheet)
    for index, png in enumerate(pngs):
        row, col = divmod(index, 3); x, y = col * (width + gap), row * (height + header)
        pen.text((x + 4, y + 4), f"{index + 1:02} | {ORDER[index]}", fill=PAD.TEXT)
        sheet.paste(Image.open(png).convert("RGB").resize((width, height)), (x, y + header))
    path = OUT / "contact_sheet.png"; sheet.save(path); return path


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True); editorial = OUT / "scene_graphs"; editorial.mkdir(exist_ok=True)
    briefs = json.loads((SOURCE / "content_briefs.json").read_text(encoding="utf-8"))
    scenes, manifest_pages = {}, []
    for final_no, letter in enumerate(ORDER, 1):
        original = SOURCE / "plans" / f"page_{letter}_scene.json"
        raw = SOURCE / "raw" / f"page_{letter}_raw.json"
        if not raw.is_file() and letter in "ABCDF":
            raw = ROOT / "outputs" / "ppt_pure_art_director" / "raw" / f"page_{letter}_revision_raw.json"
        source = json.loads(original.read_text(encoding="utf-8")); scene, changes = editorial_scene(letter, source)
        destination = editorial / original.name; destination.write_text(json.dumps(scene, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest_pages.append({"letter": letter, "original_page": "ABCDEFGHIJKLMNO".index(letter) + 1, "final_page": final_no, "title": briefs[letter]["title"], "scene_graph": str(destination.relative_to(ROOT)), "raw_response": str(raw.relative_to(ROOT)) if raw.is_file() else None, "image_assets": briefs[letter]["image_assets"], "original_png": str((SOURCE / "rendered" / "png" / "slide" / f"幻灯片{'ABCDEFGHIJKLMNO'.index(letter)+1}.png").relative_to(ROOT)), "image_driven": bool(briefs[letter]["image_assets"]), "changes": changes or ["preserved"]})
        scenes[letter] = scene
    logo_path = extract_formal_logo(OUT / "assets")
    deck = draw(scenes, output=OUT / "pure_art_director_full15_editorial.pptx", logo_path=logo_path); pngs = render(deck); sheet = contact(pngs)
    manifest = {"source_pptx": str((SOURCE / "pure_art_director_full15.pptx").relative_to(ROOT)), "order": list(ORDER), "pages": manifest_pages, "ark_calls": 0}
    (OUT / "editorial_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    checks = []
    for index, (letter, png) in enumerate(zip(ORDER, pngs), 1):
        scene = scenes[letter]; validation = PAD.validate(scene, set(briefs[letter]["image_assets"]))
        checks.append({"page": index, "letter": letter, "png": str(png.relative_to(ROOT)), "hard_errors": validation["hard_errors"], "bounds": validation["preclamp_out_of_bounds"]})
    lines = ["# Editorial polish validation", "", "- Ark calls: 0", "- PowerPoint COM PNG pages: 15", "- Contact sheet: `contact_sheet.png`", "", "## Page checks", ""]
    lines.extend(f"- {x['page']:02} {x['letter']}: {'PASS' if not x['hard_errors'] and not x['bounds'] else 'FAIL'}" for x in checks)
    (OUT / "validation_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"pptx": str(deck), "contact_sheet": str(sheet), "order": list(ORDER)}, ensure_ascii=False))


if __name__ == "__main__": main()
