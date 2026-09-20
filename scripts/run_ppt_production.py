"""Parameterized, model-disabled-by-default PPT Scene Graph production CLI."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

from src.artifact_assets.bundle import build_validated_asset_set
from src.artifact_assets.manifest_builder import build_manifest
from src.artifact_assets.safe_resolver import resolve_task_image
from src.artifact_evaluation.report_writer import write_report
from src.artifact_evaluation.runner import evaluate

W, H = 13.333, 7.5
SAFE_FOOTER_Y = 6.91


def _color(value: str | None, fallback: str = "#1A2A3A") -> RGBColor:
    value = (value or fallback).lstrip("#")
    return RGBColor(*bytes.fromhex(value))


def _page_number(path: Path) -> int:
    found = re.search(r"page_(\d+)", path.stem)
    if not found:
        raise ValueError(f"scene file lacks a page number: {path.name}")
    return int(found.group(1))


def _reflow_chinese(value: str, width: int = 5) -> str:
    compact = "".join(value.split())
    if len(compact) <= width:
        return compact
    sizes = []
    while len(compact) > width:
        take = min(width, len(compact) - width)
        sizes.append(take)
        compact = compact[take:]
    sizes.append(len(compact))
    if sizes and sizes[-1] == 1 and len(sizes) > 1:
        sizes[-2] -= 1; sizes[-1] += 1
    raw = "".join(value.split())
    lines, cursor = [], 0
    for size in sizes:
        lines.append(raw[cursor:cursor + size]); cursor += size
    return "\n".join(lines)


def polish_scene(scene: dict, page: int, approved_text: str) -> list[str]:
    """Apply repeatable readability rules without changing source Markdown."""
    warnings: list[str] = []
    for element in scene.get("elements", []):
        ident = str(element.get("id", ""))
        if page == 3 and element.get("type") == "text" and "callout" in ident:
            element["text"] = _reflow_chinese(str(element.get("text", "")))
            element["font_size"] = max(28, int(element.get("font_size", 28)))
        if page == 14 and re.search(r"(?:phase\d+_number|.*_num)$", ident):
            element["color"] = "#FFFFFF"; element["font_size"] = max(32, int(element.get("font_size", 30)))
        if page == 19 and element.get("type") == "text" and int(element.get("font_size", 16)) < 14 and float(element.get("h", 1)) < .42:
            element["text"] = str(element.get("text", ""))[:12]
            element["font_size"] = 14
        if page == 19 and element.get("type") == "text":
            text = str(element.get("text", ""))
            for token in re.findall(r"\d+(?:\.\d+)?%", text):
                if token not in approved_text:
                    element["text"] = text.replace(token, "")
                    warnings.append(f"unsupported_percentage_removed:{token}")
    return warnings


def _add_text(slide, value: dict) -> None:
    shape = slide.shapes.add_textbox(Inches(value["x"]), Inches(value["y"]), Inches(value["w"]), Inches(value["h"]))
    frame = shape.text_frame; frame.clear(); frame.word_wrap = True; frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    paragraph = frame.paragraphs[0]; paragraph.text = str(value.get("text", ""))
    paragraph.alignment = {"center": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT}.get(value.get("alignment"), PP_ALIGN.LEFT)
    run = paragraph.runs[0]; run.font.size = Pt(float(value.get("font_size", 16))); run.font.name = "微软雅黑"
    run.font.bold = str(value.get("font_weight", "")).lower() in {"bold", "600", "semi-bold"}
    run.font.color.rgb = _color(value.get("color"))


def _shape_type(kind: str):
    return {"rectangle": MSO_AUTO_SHAPE_TYPE.RECTANGLE, "rounded_rectangle": MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE, "circle": MSO_AUTO_SHAPE_TYPE.OVAL}.get(kind, MSO_AUTO_SHAPE_TYPE.RECTANGLE)


def _add_shape(slide, value: dict) -> None:
    shape = slide.shapes.add_shape(_shape_type(value["type"]), Inches(value["x"]), Inches(value["y"]), Inches(value["w"]), Inches(value["h"]))
    shape.fill.solid(); shape.fill.fore_color.rgb = _color(value.get("fill") or value.get("fill_color"), "#FFFFFF")
    shape.line.fill.background()


def _add_line(slide, value: dict) -> None:
    start, end = value["from"], value["to"]
    line = slide.shapes.add_connector(1, Inches(start[0]), Inches(start[1]), Inches(end[0]), Inches(end[1]))
    line.line.color.rgb = _color(value.get("color")); line.line.width = Pt(float(value.get("line_width", 1)))


def _asset_path(asset_set, source: str) -> Path | None:
    asset = asset_set.resolve_asset_by_reference(source)
    if not asset or asset not in asset_set.valid_assets or asset.get("detected_format") not in {"PNG", "JPEG", "GIF", "BMP", "TIFF"}:
        return None
    try:
        return resolve_task_image(asset_set.task_root, asset["normalized_relative_path"]).path
    except Exception:
        return None


def _add_image(slide, value: dict, asset_set) -> str | None:
    path = _asset_path(asset_set, str(value.get("image_source", "")))
    if not path:
        return f"asset_not_usable:{value.get('image_source', '')}"
    asset = asset_set.resolve_asset_by_reference(str(value["image_source"]))
    full_bleed = float(value.get("w", 0)) >= W * .8 or float(value.get("h", 0)) >= H * .75
    if asset.get("status") == "LOW_RESOLUTION" and full_bleed:
        return f"low_resolution_full_bleed_omitted:{value['image_source']}"
    slide.shapes.add_picture(str(path), Inches(value["x"]), Inches(value["y"]), width=Inches(value["w"]), height=Inches(value["h"]))
    return None


def _chrome(slide, number: int, total: int, logo: Path) -> None:
    slide.shapes.add_picture(str(logo), Inches(.58), Inches(.16), width=Inches(1.72))
    rule = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, Inches(.65), Inches(SAFE_FOOTER_Y), Inches(12.05), Inches(.02)); rule.fill.solid(); rule.fill.fore_color.rgb = _color("#C9D8E9"); rule.line.fill.background()
    _add_text(slide, {"x": .65, "y": 7.0, "w": 8, "h": .2, "text": "COMKING 技术方案", "font_size": 10, "color": "#405466"})
    if number != total:
        _add_text(slide, {"x": 11.82, "y": 6.99, "w": .82, "h": .22, "text": f"{number:02} / {total:02}", "font_size": 10, "font_weight": "bold", "alignment": "right", "color": "#1F497D"})


def render(scene_dir: Path, output: Path, asset_set, approved_text: str, target_slides: int | None) -> tuple[list[str], int]:
    sources = sorted(scene_dir.glob("*.json"), key=_page_number)
    if target_slides and len(sources) != target_slides:
        raise RuntimeError(f"scene count {len(sources)} does not equal target slides {target_slides}")
    logo = scene_dir / "assets" / "comking_official_logo.png"
    if not logo.is_file():
        raise RuntimeError("scene directory lacks the approved high-contrast logo")
    presentation = Presentation(); presentation.slide_width = Inches(W); presentation.slide_height = Inches(H); blank = presentation.slide_layouts[6]
    warnings: list[str] = []
    for index, path in enumerate(sources, 1):
        scene = json.loads(path.read_text(encoding="utf-8")); warnings.extend(polish_scene(scene, _page_number(path), approved_text))
        slide = presentation.slides.add_slide(blank); _chrome(slide, index, len(sources), logo)
        background = scene.get("background", {})
        if background.get("type") in {"rectangle", "solid"}:
            fill = background.get("fill") or background.get("color")
            if fill: slide.background.fill.solid(); slide.background.fill.fore_color.rgb = _color(fill)
        for element in sorted(scene.get("elements", []), key=lambda x: float(x.get("z_order", 0))):
            kind = element.get("type")
            if kind in {"text", "number", "caption"}: _add_text(slide, element)
            elif kind in {"rectangle", "rounded_rectangle", "circle", "callout"}: _add_shape(slide, element)
            elif kind in {"line", "arrow"}: _add_line(slide, element)
            elif kind == "image":
                warning = _add_image(slide, element, asset_set)
                if warning: warnings.append(warning)
    output.parent.mkdir(parents=True, exist_ok=True); presentation.save(output)
    return warnings, len(sources)


_PRODUCER_PLACEHOLDERS = {
    "${PYTHON_EXECUTABLE}", "${INPUT_MD}", "${TASK_ROOT}", "${OUTPUT_DIR}", "${SCENE_DIR}", "${VISUAL_REFERENCE_ROOT}", "${MODEL_MAX_CALLS}",
}


def _producer_argv(command_json: str, *, input_md: Path, task_root: Path, output_dir: Path, scene_dir: Path, visual_reference_root: Path, model_max_calls: int) -> list[str]:
    try:
        values = json.loads(command_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError("--model-scene-command-json must be a JSON argv list") from exc
    if not isinstance(values, list) or not values or not all(isinstance(item, str) and item for item in values):
        raise RuntimeError("--model-scene-command-json must be a non-empty argv list of strings")
    if not any("${MODEL_MAX_CALLS}" in item for item in values):
        raise RuntimeError("producer argv must receive ${MODEL_MAX_CALLS}")
    if not any("${VISUAL_REFERENCE_ROOT}" in item for item in values):
        raise RuntimeError("producer argv must receive ${VISUAL_REFERENCE_ROOT}")
    replacements = {
        "${PYTHON_EXECUTABLE}": sys.executable, "${INPUT_MD}": str(input_md), "${TASK_ROOT}": str(task_root), "${OUTPUT_DIR}": str(output_dir),
        "${SCENE_DIR}": str(scene_dir), "${VISUAL_REFERENCE_ROOT}": str(visual_reference_root), "${MODEL_MAX_CALLS}": str(model_max_calls),
    }
    argv: list[str] = []
    for item in values:
        unknown = set(re.findall(r"\$\{[^}]+\}", item)) - _PRODUCER_PLACEHOLDERS
        if unknown:
            raise RuntimeError("producer argv contains an unknown placeholder")
        for placeholder, replacement in replacements.items():
            item = item.replace(placeholder, replacement)
        argv.append(item)
    return argv


def generate_scene_graph(command_json: str, input_md: Path, task_root: Path, output_dir: Path, visual_reference_root: Path, model_max_calls: int) -> Path:
    """Run the explicitly approved live Scene Graph producer.

    This seam deliberately does not know credentials or a model endpoint.  A
    separately approved producer receives only the task-scoped inputs and must
    write its Scene Graph below this job output directory.
    """
    if os.environ.get("PPT_MODEL_LIVE_APPROVED") != "1":
        raise RuntimeError("--enable-model requires PPT_MODEL_LIVE_APPROVED=1")
    if model_max_calls <= 0:
        raise RuntimeError("--model-max-calls must be positive")
    if not visual_reference_root.is_dir():
        raise RuntimeError("--visual-reference-root must be an existing directory")
    scene_dir = output_dir / "generated_scene_graph"
    if scene_dir.exists():
        raise RuntimeError("model-enabled production refuses an existing Scene Graph directory")
    scene_dir.mkdir(parents=True)
    started = datetime.now().timestamp()
    args = _producer_argv(command_json, input_md=input_md, task_root=task_root, output_dir=output_dir, scene_dir=scene_dir, visual_reference_root=visual_reference_root, model_max_calls=model_max_calls)
    completed = subprocess.run(args, check=False, capture_output=True, text=True, shell=False)
    (output_dir / "model_scene_generator.stdout.log").write_text(completed.stdout or "", encoding="utf-8")
    (output_dir / "model_scene_generator.stderr.log").write_text(completed.stderr or "", encoding="utf-8")
    if completed.returncode or not any(path.stat().st_mtime >= started for path in scene_dir.glob("*.json")):
        raise RuntimeError("approved model Scene Graph producer failed")
    return scene_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-md", type=Path, required=True); parser.add_argument("--task-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True); parser.add_argument("--output-pptx", type=Path, required=True)
    parser.add_argument("--run-log", type=Path, required=True); parser.add_argument("--target-slides", type=int)
    model_mode = parser.add_mutually_exclusive_group(required=True)
    model_mode.add_argument("--disable-model", action="store_true", help="offline re-render using --existing-scene-dir only")
    model_mode.add_argument("--enable-model", action="store_true", help="run the separately approved Scene Graph producer")
    parser.add_argument("--existing-scene-dir", type=Path); parser.add_argument("--model-scene-command-json"); parser.add_argument("--model-max-calls", type=int); parser.add_argument("--visual-reference-root", type=Path)
    parser.add_argument("--enable-visual-critic", action="store_true"); parser.add_argument("--max-revisions", type=int, default=0)
    parser.add_argument("--task-id", default=""); parser.add_argument("--job-id", default="")
    args = parser.parse_args()
    if args.disable_model and not args.existing_scene_dir:
        raise RuntimeError("--existing-scene-dir is required for model-disabled production")
    if args.enable_model and (args.existing_scene_dir or not args.model_scene_command_json or not args.model_max_calls or not args.visual_reference_root):
        raise RuntimeError("--enable-model requires producer argv, visual reference root, positive model call limit, and cannot reuse an existing Scene Graph")
    before = hashlib.sha256(args.input_md.read_bytes()).hexdigest()
    manifest = build_manifest(args.input_md, args.task_root); args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "assets_manifest.json"; manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    scene_dir = args.existing_scene_dir or generate_scene_graph(args.model_scene_command_json, args.input_md, args.task_root, args.output_dir, args.visual_reference_root, args.model_max_calls)
    warnings, page_count = render(scene_dir, args.output_pptx, build_validated_asset_set(manifest, args.task_root), args.input_md.read_text(encoding="utf-8"), args.target_slides)
    report = evaluate(profile="client-delivery", markdown=str(args.input_md), pptx=str(args.output_pptx), expected_source_hash=before)
    write_report(report, args.output_dir / "artifact_evaluation")
    args.run_log.write_text(json.dumps({"model_calls": 1 if args.enable_model else 0, "visual_critic": False, "max_revisions": 0, "page_count": page_count, "warnings": warnings, "approved_sha256_before": before, "approved_sha256_after": hashlib.sha256(args.input_md.read_bytes()).hexdigest()}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if report.overall_status.value == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
