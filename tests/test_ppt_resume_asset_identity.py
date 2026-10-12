import hashlib
import json
from pathlib import Path

import pytest

from src.artifact_assets.manifest_builder import build_manifest
from src.wecom.ppt_checkpoint import CheckpointStore, ResumeRejected, import_resume_state, validate_resume_assets


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source(tmp_path: Path) -> tuple[Path, Path, dict]:
    task = tmp_path / "task"; task.mkdir()
    approved = task / "approved.md"; approved.write_text("# Approved\n", encoding="utf-8")
    source = tmp_path / "source-job"; source.mkdir()
    manifest = build_manifest(approved, task)
    manifest_path = source / "assets_manifest.json"; manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    global_path = source / "global_art_direction.json"; global_path.write_text("{}", encoding="utf-8")
    store = CheckpointStore(source, task_id="task", job_id="source-job", approved_sha256=_sha(approved), assets_manifest_sha256=_sha(manifest_path), compatibility={"producer":"p","prompt":"q","scene_schema":"s","pages":20}, model_max_calls=26)
    store.record(stage="global_art_direction", canonical_path=global_path, page=None, calls_used=7)
    (source / "ark_calls.json").write_text(json.dumps([{"network_request_started": True}] * 7), encoding="utf-8")
    return task, source, manifest


def _expected(source: Path, approved: Path) -> dict:
    return {"source_task_id":"task", "source_job_id":"source-job", "approved_md_sha256":_sha(approved), "assets_manifest_sha256":_sha(source / "assets_manifest.json"), "producer_compatibility_version":"p", "prompt_contract_version":"q", "scene_schema_version":"s", "total_slide_count":20, "model_max_calls":26}


def test_generated_at_is_compatible_but_source_bytes_are_immutable(tmp_path: Path):
    task, source, current = _source(tmp_path); approved = task / "approved.md"
    current = {**current, "generated_at":"different"}
    expected = _expected(source, approved)
    state = import_resume_state(parent_root=source, child_root=tmp_path / "child", expected=expected, validate_global=lambda _: None, validate_page=lambda _n, _: None, current_assets_manifest=current)
    assert state.historical_calls_used == 7
    changed = json.loads((source / "assets_manifest.json").read_text()); changed["generated_at"] = "tampered"
    (source / "assets_manifest.json").write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ResumeRejected, match="source assets manifest integrity mismatch"):
        validate_resume_assets(parent_root=source, locked_sha256=expected["assets_manifest_sha256"], current_manifest=current)


@pytest.mark.parametrize("field,value", [("schema_version", "other"), ("task_root_label", "other"), ("assets", [{"asset_id":"changed"}])])
def test_only_generated_at_is_excluded(tmp_path: Path, field: str, value: object):
    task, source, current = _source(tmp_path); candidate = {**current, "generated_at":"new", field:value}
    with pytest.raises(ResumeRejected, match="current assets"):
        validate_resume_assets(parent_root=source, locked_sha256=_sha(source / "assets_manifest.json"), current_manifest=candidate)


def test_current_asset_scan_detects_file_content_change(tmp_path: Path):
    task = tmp_path / "task"; task.mkdir(); image = task / "x.png"
    image.write_bytes(b"not-a-real-image")
    approved = task / "approved.md"; approved.write_text("![x](x.png)\n", encoding="utf-8")
    # An invalid image is still recorded; changing bytes changes the fresh scan's content SHA.
    source = tmp_path / "source-job"; source.mkdir(); old = build_manifest(approved, task)
    path = source / "assets_manifest.json"; path.write_text(json.dumps(old), encoding="utf-8")
    image.write_bytes(b"changed-image-bytes")
    with pytest.raises(ResumeRejected, match="current assets differ"):
        validate_resume_assets(parent_root=source, locked_sha256=_sha(path), current_manifest=build_manifest(approved, task))


def test_unsafe_or_invalid_source_manifest_rejected_before_import(tmp_path: Path):
    task, source, current = _source(tmp_path); path = source / "assets_manifest.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ResumeRejected, match="integrity mismatch"):
        validate_resume_assets(parent_root=source, locked_sha256="0" * 64, current_manifest=current)
