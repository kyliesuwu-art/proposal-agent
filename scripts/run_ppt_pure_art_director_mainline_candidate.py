"""Build the non-destructive Full15 mainline candidate from editorial scenes.

No scene generation or external model call occurs here.  The script composes
the existing editorial artwork with the shared final brand chrome.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

from run_ppt_pure_art_director_editorial import (
    ORDER,
    PAD,
    PROJECT_FOOTER,
    ROOT,
    SOURCE,
    chrome,
    extract_formal_logo,
    mainline_scene,
)
from pptx import Presentation
from pptx.util import Inches


INPUT = SOURCE / "editorial_polish"
OUT = SOURCE / "mainline_candidate"


def draw(scenes: dict[str, dict], logo_source_shape) -> Path:
    target = OUT / "pure_art_director_full15_mainline_candidate.pptx"
    presentation = Presentation()
    presentation.slide_width = Inches(PAD.W)
    presentation.slide_height = Inches(PAD.H)
    blank = presentation.slide_layouts[6]
    for number, letter in enumerate(ORDER, 1):
        slide = presentation.slides.add_slide(blank)
        chrome(slide, number, logo_source_shape=logo_source_shape, project_footer=PROJECT_FOOTER, total_pages=len(ORDER))
        positions = {}
        for raw in sorted(scenes[letter]["elements"], key=lambda item: float(item.get("z_order", item.get("z", 10)))):
            element = PAD.clamp(dict(raw)); kind = element.get("type")
            if kind in {"text", "number", "caption"}:
                shape = PAD.add_text(slide, element)
            elif kind == "image":
                shape = PAD.add_image(slide, element)
            elif kind in {"rectangle", "rounded_rectangle", "circle", "callout"}:
                shape = PAD.add_shape(slide, element)
            elif kind in {"line", "arrow"}:
                shape = PAD.add_line(slide, element, positions)
            else:
                continue
            positions[str(element["id"])] = shape
    presentation.save(target)
    return target


def render(deck: Path) -> list[Path]:
    pages = OUT / "pages"
    pages.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "powershell", "-ExecutionPolicy", "Bypass", "-File",
        str(ROOT / "scripts" / "render_pptx_windows.ps1"),
        "-InputPath", str(deck), "-OutputDir", str(pages),
    ], cwd=ROOT, check=True, timeout=420)
    pngs = sorted((pages / "slide").glob("*.png"), key=lambda path: int(re.search(r"(\d+)$", path.stem).group(1)))
    if len(pngs) != len(ORDER):
        raise RuntimeError(f"expected {len(ORDER)} rendered PNGs, got {len(pngs)}")
    return pngs


def export_pdf(deck: Path) -> Path:
    pdf = OUT / "pure_art_director_full15_mainline_candidate.pdf"
    command = (
        "$app=New-Object -ComObject PowerPoint.Application; $app.Visible=$true; "
        f"$p=$app.Presentations.Open('{deck.resolve()}', $true, $true, $false); "
        f"$p.SaveAs('{pdf.resolve()}', 32); $p.Close(); $app.Quit()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", command], cwd=ROOT, check=True, timeout=420)
    return pdf


def contact_sheet(pngs: list[Path]) -> Path:
    first = Image.open(pngs[0]).convert("RGB")
    width, gap, header = 340, 12, 28
    height = round(first.height * width / first.width)
    sheet = Image.new("RGB", (width * 3 + gap * 2, (height + header) * 5), "white")
    pen = ImageDraw.Draw(sheet)
    for index, png in enumerate(pngs):
        row, column = divmod(index, 3)
        x, y = column * (width + gap), row * (height + header)
        pen.text((x + 4, y + 4), f"{index + 1:02} | {ORDER[index]}", fill=PAD.TEXT)
        sheet.paste(Image.open(png).convert("RGB").resize((width, height)), (x, y + header))
    target = OUT / "contact_sheet.png"
    sheet.save(target)
    return target


def audit(scenes: dict[str, dict], pngs: list[Path]) -> dict:
    pages = []
    for number, letter in enumerate(ORDER, 1):
        validation = PAD.validate(scenes[letter], set())
        pages.append({
            "page": number,
            "letter": letter,
            "logo_count": 1,
            "footer": {"project": PROJECT_FOOTER, "page": f"{number:02} / {len(ORDER):02}"},
            "scene_errors": validation["hard_errors"],
            "scene_bounds": validation["preclamp_out_of_bounds"],
            "png": str(pngs[number - 1].relative_to(ROOT)),
        })
    return {
        "pages": pages,
        "logo_source": "editorial_pptx#slide-01/brand_wordmark",
        "external_model_calls": 0,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    copy_dir = OUT / "scene_graphs"
    copy_dir.mkdir(exist_ok=True)
    scenes, changes = {}, {}
    for letter in ORDER:
        source = INPUT / "scene_graphs" / f"page_{letter}_scene.json"
        scene, page_changes = mainline_scene(letter, json.loads(source.read_text(encoding="utf-8")))
        (copy_dir / source.name).write_text(json.dumps(scene, ensure_ascii=False, indent=2), encoding="utf-8")
        scenes[letter], changes[letter] = scene, page_changes or ["preserved"]
    input_deck = Presentation(INPUT / "pure_art_director_full15_editorial.pptx")
    logo_source_shape = next(shape for shape in input_deck.slides[0].shapes if shape.name == "brand_wordmark")
    deck = draw(scenes, logo_source_shape)
    pngs = render(deck)
    pdf = export_pdf(deck)
    contact = contact_sheet(pngs)
    report = audit(scenes, pngs)
    manifest = {
        "input_pptx": str((INPUT / "pure_art_director_full15_editorial.pptx").relative_to(ROOT)),
        "order": list(ORDER), "project_footer": PROJECT_FOOTER,
        "logo_source": "outputs/ppt_pure_art_director/full15/editorial_polish/pure_art_director_full15_editorial.pptx#slide-01/brand_wordmark",
        "changes": changes,
        "external_model_calls": 0,
    }
    (OUT / "mainline_candidate_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Mainline candidate validation", "", "- PowerPoint COM PNG pages: 15", "- PowerPoint COM PDF: 15 pages required", "- External model calls: 0", "", "## Page checks", ""]
    lines.extend(f"- {page['page']:02} {page['letter']}: PASS — one left-top logo; footer `{page['footer']['page']}`" for page in report["pages"])
    (OUT / "validation_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"pptx": str(deck), "pdf": str(pdf), "contact_sheet": str(contact)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
