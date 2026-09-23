"""Resume the Ark V4 calibration as a complete, isolated 20-page candidate."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import os
import sys
from pathlib import Path

# This producer is launched by an absolute script path from a job subprocess.
# Make its own worktree root importable before importing application modules.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.artifact_assets.bundle import build_validated_asset_set, load_manifest
from src.wecom.ppt_checkpoint import CheckpointStore, ResumeRejected, import_resume_state

ROOT = PROJECT_ROOT
CAL = ROOT / "outputs" / "ppt_pure_art_director" / "full15" / "visual_calibration_v4_ark"
OUT = ROOT / "outputs" / "ppt_pure_art_director" / "full15" / "v4_ark_full20_mainline_candidate"
APPROVED = ROOT / "outputs" / "wecom_v1_full_e2e_retry" / "20260917_retry" / "95f07bf23ea8" / "approved.md"
NAMES = {1:"G",2:"TOC",3:"BACKGROUND",4:"I",5:"H",6:"ARCH",7:"RELIABILITY",8:"A",9:"J",10:"B",11:"C",12:"K",13:"L",14:"D",15:"ORGANIZATION",16:"N",17:"F",18:"M",19:"E",20:"CLOSE"}
ACCEPTED = (1,2,3,5,6,14,19,20)
MAX_MODEL_CALLS = 26  # global direction + 20 pages + critic + up to 4 revisions
_REPARSE_POINT_ATTRIBUTE = 0x0400

spec = importlib.util.spec_from_file_location("v4", ROOT / "scripts" / "run_ppt_visual_calibration_v4_ark.py")
v4 = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(v4)


def set_context() -> None:
    v4.OUT = OUT
    v4.PICK = tuple(range(1, 21))
    v4.NAMES = NAMES


def configure_production_visual_resources(visual_reference_root: Path) -> None:
    """Bind producer-only visual inputs from an explicit, read-only root.

    The legacy calibration paths above remain available only to the historical
    maintenance commands.  A fresh ArtifactJob must never discover a prior
    task or experiment directory implicitly.
    """
    root = visual_reference_root.resolve()
    full15 = root / "outputs" / "ppt_pure_art_director" / "full15"
    references = (
        root / "outputs" / "ppt_template_library_v1" / "source_analysis" / "comking_company" / "contact_sheet.png",
        root / "outputs" / "ppt_template_library_v1" / "source_analysis" / "fujian" / "contact_sheet.png",
        root / "outputs" / "ppt_template_library_v1" / "source_analysis" / "industrial_park" / "contact_sheet.png",
        root / "outputs" / "ppt_template_library_v1" / "source_analysis" / "jiamusi" / "contact_sheet.png",
    )
    base = full15 / "mainline_expanded_v2"
    v3 = full15 / "visual_calibration_v3"
    logo = root / "files" / "工业园综合智慧能源解决方案.pptx"
    required = (base / "contact_sheet.png", v3 / "contact_sheet.png", *references, logo)
    if not root.is_dir() or any(not path.is_file() for path in required):
        raise RuntimeError("required production visual resources are unavailable")
    v4.FULL15 = full15
    v4.BASE = base
    v4.V3 = v3
    v4.REFERENCE_SHEETS = references
    v4.EDITORIAL.FORMAL_LOGO_DECK = logo


def prepare() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for item in ("global_art_direction.json", "global_art_direction_raw.json"):
        target = OUT / item
        if not target.exists(): shutil.copy2(CAL / item, target)
    destination = OUT / "scene_graphs"; destination.mkdir(exist_ok=True)
    for page in ACCEPTED:
        name = f"page_{page:02}_{NAMES[page]}.json"; target = destination / name
        if not target.exists(): shutil.copy2(CAL / "scene_graphs" / name, target)


def source_status(page: int) -> str:
    return "V4_REVISED" if page in {1,5,14,20} else "V4_ACCEPTED" if page in ACCEPTED else "ARK_REQUIRED"


def _is_symlink_or_reparse_point(path: Path) -> bool:
    """Reject links and Windows reparse points without resolving through them."""
    try:
        stat = path.lstat()
    except FileNotFoundError:
        return False
    is_junction = getattr(path, "is_junction", lambda: False)
    return path.is_symlink() or is_junction() or bool(getattr(stat, "st_file_attributes", 0) & _REPARSE_POINT_ATTRIBUTE)


def prepare_fresh_job_scene_output_directory(output_dir: Path, scene_dir: Path) -> Path:
    """Prepare only a new, job-scoped Scene Graph directory.

    The production wrapper may pre-create the empty ``generated_scene_graph``
    directory before it launches this producer. That is valid only for the
    current job: existing content, completed-producer evidence, links, or an
    escaped path are never reused or removed.
    """
    output_dir, scene_dir = Path(output_dir), Path(scene_dir)
    if not output_dir.is_absolute() or not scene_dir.is_absolute():
        raise RuntimeError("producer output and Scene Graph paths must be absolute")
    if _is_symlink_or_reparse_point(output_dir):
        raise RuntimeError("producer Job output root must not be a symlink or reparse point")
    if output_dir.exists() and not output_dir.is_dir():
        raise RuntimeError("producer Job output root must be a directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    root = output_dir.resolve(strict=True)
    if _is_symlink_or_reparse_point(scene_dir):
        raise RuntimeError("producer Scene Graph directory must not be a symlink or reparse point")
    canonical_scene = scene_dir.resolve(strict=False)
    try:
        canonical_scene.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("producer Scene Graph directory must be inside the current Job output root") from exc
    if (root / "scene_graphs").exists():
        raise RuntimeError("producer output already contains a Scene Graph")
    if (root / "producer_manifest.json").exists():
        raise RuntimeError("producer output already contains a completed producer record")
    if scene_dir.exists():
        if not scene_dir.is_dir():
            raise RuntimeError("producer Scene Graph path must be a directory")
        if any(scene_dir.iterdir()):
            raise RuntimeError("producer Scene Graph directory is not empty")
    else:
        scene_dir.mkdir(parents=False, exist_ok=False)
    return scene_dir


def generate_missing() -> None:
    set_context(); prepare(); direction = json.loads((OUT / "global_art_direction.json").read_text(encoding="utf-8"))
    for page in range(1, 21):
        path = OUT / "scene_graphs" / f"page_{page:02}_{NAMES[page]}.json"
        if not path.exists(): v4.page_request(page, direction)


def finish() -> None:
    set_context(); prepare(); direction = json.loads((OUT / "global_art_direction.json").read_text(encoding="utf-8"))
    scenes = {page: json.loads((OUT / "scene_graphs" / f"page_{page:02}_{NAMES[page]}.json").read_text(encoding="utf-8")) for page in range(1,21)}
    deck = v4.draw(scenes); deck.replace(OUT / "pure_art_director_v4_ark_full20.pptx")
    deck = OUT / "pure_art_director_v4_ark_full20.pptx"
    # v4.render writes the expected PDF name; normalize output naming after COM succeeds.
    pngs = v4.render(deck)
    old_pdf = OUT / "pure_art_director_visual_calibration_v4_ark.pdf"
    if old_pdf.exists(): old_pdf.replace(OUT / "pure_art_director_v4_ark_full20.pdf")
    v4.contact(pngs, OUT / "contact_sheet.png", [f"V4 Full 20 | {page:02}" for page in range(1,21)])
    approved_hash = hashlib.sha256(APPROVED.read_bytes()).hexdigest()
    manifest = {"approved_md_path": str(APPROVED.relative_to(ROOT)), "approved_md_sha256_before": approved_hash, "approved_md_sha256_after": hashlib.sha256(APPROVED.read_bytes()).hexdigest(), "pages": [{"page": p, "name": NAMES[p], "status": source_status(p), "scene_graph": str((OUT / "scene_graphs" / f"page_{p:02}_{NAMES[p]}.json").relative_to(ROOT))} for p in range(1,21)], "accepted_pages": list(ACCEPTED)}
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def produce(input_md: Path, output_dir: Path, scene_dir: Path, visual_reference_root: Path, manifest_path: Path, model_max_calls: int, resume_from_job_root: Path | None = None) -> int:
    """Fresh, job-scoped producer entry point used by ArtifactService.

    The producer never copies a prior Scene Graph.  Existing V4 rendered PNGs
    and brand references remain visual-only inputs, while approved Markdown is
    the locked content input for this run.
    """
    if not input_md.is_file() or input_md.stat().st_size == 0:
        raise RuntimeError("producer input Markdown is unavailable")
    if model_max_calls <= 0 or model_max_calls > MAX_MODEL_CALLS:
        raise RuntimeError(f"model-max-calls must be between 1 and {MAX_MODEL_CALLS}")
    scene_dir = prepare_fresh_job_scene_output_directory(output_dir, scene_dir)

    configure_production_visual_resources(visual_reference_root)
    assets_manifest = load_manifest(output_dir / "assets_manifest.json")
    assets = build_validated_asset_set(assets_manifest, input_md.parent)
    v4.CONTENT_ASSETS = tuple({
        "asset_id": asset["asset_id"],
        "image_source": asset["normalized_relative_path"],
        "width": asset.get("width"),
        "height": asset.get("height"),
        "usage": "content_asset",
    } for asset in assets.valid_assets)
    set_context(); v4.OUT = output_dir; v4.APPROVED_TEXT = input_md.read_text(encoding="utf-8")
    compatibility = {"producer": "run_ppt_v4_ark_full20/v1", "prompt": "v4-scene-prompt/v1", "scene_schema": "v4-scene-graph/v1", "model": v4.PAD.MODEL, "pages": 20, "visual_reference_root": str(visual_reference_root.resolve())}
    approved_sha = hashlib.sha256(input_md.read_bytes()).hexdigest(); assets_sha = hashlib.sha256((output_dir / "assets_manifest.json").read_bytes()).hexdigest()
    checkpoints = CheckpointStore(output_dir, task_id=os.environ.get("PPT_TASK_ID", ""), job_id=os.environ.get("PPT_JOB_ID", ""), approved_sha256=approved_sha, assets_manifest_sha256=assets_sha, compatibility=compatibility, model_max_calls=model_max_calls)
    inherited = 0
    if resume_from_job_root:
        expected = {"contract_version": "ppt-generation-checkpoint/v1", "task_id": os.environ.get("PPT_TASK_ID", ""), "approved_md_sha256": approved_sha, "assets_manifest_sha256": assets_sha, "compatibility": compatibility, "model_max_calls": model_max_calls, "pages": 20}
        def valid_global(value): v4.validate_global_art_direction(value)
        def valid_page(page, value):
            audit = v4.PAD.validate(value, set(v4.context_for(page)["allowed_image_sources"]))
            if audit.get("hard_errors") or audit.get("preclamp_out_of_bounds"): raise ResumeRejected(f"invalid page checkpoint {page}")
        state = import_resume_state(parent_root=resume_from_job_root, child_root=output_dir, expected=expected, validate_global=valid_global, validate_page=valid_page)
        inherited = state.historical_calls_used
        if state.global_direction: checkpoints.record(stage="global_art_direction", canonical_path=state.global_direction, page=None, calls_used=inherited)
        for page, source in state.page_paths: checkpoints.record(stage="page_scene_graph", canonical_path=source, page=page, calls_used=inherited)
    v4.configure_checkpointing(checkpoints); v4.configure_model_call_budget(model_max_calls, used=inherited)
    direction = v4.art_direction()
    scenes = {page: v4.page_request(page, direction) for page in range(1, 21)}
    deck = v4.draw(scenes)
    rendered = v4.render(deck)
    sheet = output_dir / "contact_sheet.png"; v4.contact(rendered, sheet, [f"V4 Full 20 | {page:02}" for page in range(1, 21)])
    critic_result = v4.critic(direction, v4.compare(rendered))
    for page in critic_result.get("selected_pages", [])[:4]:
        scenes[page] = v4.page_request(page, direction, revision=True)
    for page in range(1, 21):
        source = output_dir / "scene_graphs" / f"page_{page:02}_{NAMES[page]}.json"
        if not source.is_file():
            source.write_text(json.dumps(scenes[page], ensure_ascii=False, indent=2), encoding="utf-8")
        shutil.copy2(source, scene_dir / source.name)
    manifest = {
        "schema": "ppt-scene-producer/v1", "approved_md_sha256": hashlib.sha256(input_md.read_bytes()).hexdigest(),
        "model_max_calls": model_max_calls, "model_calls_used": v4.MODEL_CALL_BUDGET.used if v4.MODEL_CALL_BUDGET else None,
        "scene_graph_dir": str(scene_dir.resolve().relative_to(output_dir.resolve())), "visual_reference_root_configured": True, "pages": 20, "critic_selected_pages": critic_result.get("selected_pages", []), "resume_from_job_root": str(resume_from_job_root) if resume_from_job_root else None, "historical_calls_used": inherited,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True); manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--generate-missing", action="store_true"); parser.add_argument("--finish", action="store_true"); parser.add_argument("--produce", action="store_true"); parser.add_argument("--input-md", type=Path); parser.add_argument("--output-dir", type=Path); parser.add_argument("--scene-dir", type=Path); parser.add_argument("--visual-reference-root", type=Path); parser.add_argument("--manifest-path", type=Path); parser.add_argument("--model-max-calls", type=int); parser.add_argument("--resume-from-job-root", type=Path); args = parser.parse_args()
    if args.produce:
        if not all((args.input_md, args.output_dir, args.scene_dir, args.visual_reference_root, args.manifest_path, args.model_max_calls)):
            parser.error("--produce requires input, output, scene, visual-reference-root, manifest, and positive model-max-calls")
        produce(args.input_md, args.output_dir, args.scene_dir, args.visual_reference_root, args.manifest_path, args.model_max_calls, args.resume_from_job_root)
    elif args.generate_missing: generate_missing()
    elif args.finish: finish()
    else: parser.error("choose --generate-missing or --finish")
