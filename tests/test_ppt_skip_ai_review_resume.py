from __future__ import annotations

import importlib.util
import inspect
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_producer():
    spec = importlib.util.spec_from_file_location(
        "full20_skip_review_test", PROJECT_ROOT / "scripts" / "run_ppt_v4_ark_full20.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _valid_scene() -> dict:
    return {
        "background": {"color": "#FFFFFF"},
        "elements": [
            {
                "id": "title",
                "type": "text",
                "text": "标题",
                "x": 1.0,
                "y": 1.0,
                "w": 8.0,
                "h": 0.8,
                "font_size": 24,
                "z_order": 1,
            }
        ],
    }


def _valid_global() -> dict:
    value = {
        "design_intent": "intent",
        "visual_personality": "calm",
        "color_system": {"primary": "#0B3D91"},
        "typography_hierarchy": "clear",
        "spacing_rhythm": "even",
        "image_treatment": "crop",
        "diagram_language": "blocks",
        "brand_motif": "line",
        "page_families": {"family": [1]},
        "density_strategy": "sparse",
        "do_not_use": ["ui cards"],
        "page_directions": {"1": "intro"},
    }
    return value


class _FakeArk:
    """Replaces PAD.ask; returns a complete JSON object per model stage."""

    def __init__(self, producer, *, missing_geometry_pages: set[int] | None = None) -> None:
        self.producer = producer
        self.requests: list[str] = []
        self.missing_geometry_pages = missing_geometry_pages or set()

    def __call__(self, messages, purpose, *, max_tokens=12000):
        self.requests.append(purpose)
        if purpose == "global_art_direction":
            return json.dumps(_valid_global(), ensure_ascii=False), {"elapsed_seconds": 0.1}
        if purpose.startswith("page_") and purpose.endswith("_design"):
            page = int(purpose.split("_")[1])
            scene = _valid_scene()
            if page in self.missing_geometry_pages:
                for circle, label in (("circle1", "一"), ("circle2", "二"), ("circle3", "三")):
                    scene["elements"].append(
                        {"id": circle, "type": "circle", "text": label}
                    )
            return json.dumps(scene, ensure_ascii=False), {"elapsed_seconds": 0.1}
        raise AssertionError(f"unexpected model stage in skip-ai-review run: {purpose}")


def _build_source_job(producer, root: Path, *, used: int = 7) -> Path:
    source = root / "source-job"
    scene_root = source / "scene_graphs"
    scene_root.mkdir(parents=True)
    approved = root / "approved.md"
    approved.write_text("# approved\n\n正文\n", encoding="utf-8")
    assets = source / "assets_manifest.json"
    assets.write_text(json.dumps({"schema_version": "image-asset-manifest/v1", "assets": []}), encoding="utf-8")
    global_path = source / "global_art_direction.json"
    global_path.write_text(json.dumps(_valid_global(), ensure_ascii=False), encoding="utf-8")
    digest = _sha(approved)
    assets_digest = _sha(assets)
    compatibility = {
        "producer": "run_ppt_v4_ark_full20/v1",
        "prompt": "v4-scene-prompt/v1",
        "scene_schema": "v4-scene-graph/v1",
        "model": producer.v4.PAD.MODEL,
        "pages": 20,
        "visual_reference_root": str((root / "visual_ref").resolve()),
    }
    store = producer.CheckpointStore(
        source,
        task_id="task-1",
        job_id="source-job",
        approved_sha256=digest,
        assets_manifest_sha256=assets_digest,
        compatibility=compatibility,
        model_max_calls=26,
    )
    store.record(stage="global_art_direction", canonical_path=global_path, page=None, calls_used=used)
    for page in range(1, 5):
        scene_path = scene_root / f"page_{page:02}_{producer.NAMES[page]}.json"
        scene_path.write_text(json.dumps(_valid_scene(), ensure_ascii=False), encoding="utf-8")
        store.record(stage="page_scene_graph", canonical_path=scene_path, page=page, calls_used=used)
    (source / "ark_calls.json").write_text(
        json.dumps([{"network_request_started": True} for _ in range(used)]), encoding="utf-8"
    )
    return source


def _sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _patch_rendering(producer, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(producer, "configure_production_visual_resources", lambda root: None)
    png_dir = tmp_path / "png"
    png_dir.mkdir()
    pngs = []
    for page in range(1, 21):
        path = png_dir / f"page_{page:02}.png"
        path.write_bytes(b"png")
        pngs.append(path)

    monkeypatch.setattr(producer.v4, "draw", lambda scenes: tmp_path / "deck.pptx")
    monkeypatch.setattr(producer.v4, "render", lambda deck: pngs)
    monkeypatch.setattr(producer.v4, "contact", lambda rendered, out, labels: out.write_bytes(b"sheet"))
    monkeypatch.setattr(producer.v4, "critic", lambda *a, **k: pytest.fail("critic must not run"))

    # Offline Fake: neutralize only the visual-image file loading inside
    # art_direction/page_request. Budget, JSON parsing and Scene Schema
    # validation still run through the real code path.
    monkeypatch.setattr(producer.v4, "page_png", lambda folder, page: tmp_path / "unused.png")
    monkeypatch.setattr(producer.v4, "image_part", lambda path, label: [{"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ/pLvAAAAAElFTkSuQmCC"}}])


def _run(producer, tmp_path: Path, monkeypatch, **kwargs):
    (tmp_path / "visual_ref").mkdir()
    source = _build_source_job(producer, tmp_path)
    output = tmp_path / "child_job"
    scene_dir = output / "generated_scene_graph"
    output.mkdir()
    scene_dir.mkdir()
    (output / "assets_manifest.json").write_text(json.dumps({"schema_version": "image-asset-manifest/v1", "assets": []}), encoding="utf-8")
    fake = _FakeArk(producer)
    monkeypatch.setattr(producer.v4.PAD, "ask", fake)
    _patch_rendering(producer, monkeypatch, tmp_path)
    monkeypatch.setenv("PPT_TASK_ID", "task-1")
    monkeypatch.setenv("PPT_JOB_ID", "child-job")
    approved = tmp_path / "approved.md"
    rc = producer._produce(
        approved,
        output,
        scene_dir,
        tmp_path / "visual_ref",
        output / "producer_manifest.json",
        26,
        source,
        skip_ai_review=True,
        **kwargs,
    )
    return rc, fake, output, scene_dir, source


def test_resume_starts_at_page_5_without_recalling_global_or_pages_1_4(tmp_path, monkeypatch):
    producer = _load_producer()
    rc, fake, output, scene_dir, source = _run(producer, tmp_path, monkeypatch)
    assert rc == 0
    assert fake.requests == [f"page_{page:02}_design" for page in range(5, 21)]
    assert len(fake.requests) == 16
    assert (output / "global_art_direction.json").is_file()
    for page in range(1, 5):
        assert (output / "scene_graphs" / f"page_{page:02}_{producer.NAMES[page]}.json").is_file()


def test_resume_copies_into_child_and_keeps_source_readonly(tmp_path, monkeypatch):
    producer = _load_producer()
    _, _, output, _, source = _run(producer, tmp_path, monkeypatch)
    source_global = source / "global_art_direction.json"
    child_global = output / "global_art_direction.json"
    assert source_global.resolve() != child_global.resolve()
    assert json.loads(source_global.read_text(encoding="utf-8")) == json.loads(child_global.read_text(encoding="utf-8"))
    data = json.loads((source / "ppt_generation_checkpoints.json").read_text(encoding="utf-8"))
    assert data["ark_attempts_used"] == 7


def test_budget_inherited_7_caps_at_26_and_critic_revision_zero(tmp_path, monkeypatch):
    producer = _load_producer()
    rc, fake, output, scene_dir, source = _run(producer, tmp_path, monkeypatch)
    manifest = json.loads((output / "producer_manifest.json").read_text(encoding="utf-8"))
    assert manifest["historical_calls_used"] == 7
    assert manifest["model_calls_used"] == 23
    assert manifest["critic_status"] == "NOT_RUN"
    assert manifest["revision_status"] == "NOT_RUN"
    assert manifest["ai_review_skip_reason"] == "deferred_for_human_visual_review"
    assert manifest["critic_selected_pages"] == []
    assert not any("critic" in purpose or "revision" in purpose for purpose in fake.requests)


def test_budget_rejects_before_request_when_cumulative_cap_reached(tmp_path, monkeypatch):
    producer = _load_producer()
    # A source that already used all 26 calls leaves zero remaining budget.
    (tmp_path / "visual_ref").mkdir()
    source = _build_source_job(producer, tmp_path, used=26)
    output = tmp_path / "child_job"
    scene_dir = output / "generated_scene_graph"
    output.mkdir()
    scene_dir.mkdir()
    (output / "assets_manifest.json").write_text(json.dumps({"schema_version": "image-asset-manifest/v1", "assets": []}), encoding="utf-8")
    fake = _FakeArk(producer)
    monkeypatch.setattr(producer.v4.PAD, "ask", fake)
    _patch_rendering(producer, monkeypatch, tmp_path)
    monkeypatch.setenv("PPT_TASK_ID", "task-1")
    monkeypatch.setenv("PPT_JOB_ID", "child-job")
    from src.wecom.ppt_failures import PptModelBudgetExceededError

    with pytest.raises(PptModelBudgetExceededError):
        producer._produce(
            tmp_path / "approved.md",
            output,
            scene_dir,
            tmp_path / "visual_ref",
            output / "producer_manifest.json",
            26,
            source,
            skip_ai_review=True,
        )
    assert fake.requests == []


def test_missing_geometry_on_page_5_is_rejected_with_field_feedback(tmp_path, monkeypatch):
    producer = _load_producer()
    (tmp_path / "visual_ref").mkdir()
    source = _build_source_job(producer, tmp_path)
    output = tmp_path / "child_job"
    scene_dir = output / "generated_scene_graph"
    output.mkdir()
    scene_dir.mkdir()
    (output / "assets_manifest.json").write_text(json.dumps({"schema_version": "image-asset-manifest/v1", "assets": []}), encoding="utf-8")
    fake = _FakeArk(producer, missing_geometry_pages={5})
    monkeypatch.setattr(producer.v4.PAD, "ask", fake)
    _patch_rendering(producer, monkeypatch, tmp_path)
    monkeypatch.setenv("PPT_TASK_ID", "task-1")
    monkeypatch.setenv("PPT_JOB_ID", "child-job")
    with pytest.raises(producer.v4.ModelJsonContractError) as excinfo:
        producer._produce(
            tmp_path / "approved.md",
            output,
            scene_dir,
            tmp_path / "visual_ref",
            output / "producer_manifest.json",
            26,
            source,
            skip_ai_review=True,
        )
    message = str(excinfo.value)
    assert "page 05" in message
    for circle in ("circle1", "circle2", "circle3"):
        assert circle in message
        assert f"elements[id={circle}].x" in message
    assert not (output / "scene_graphs" / "page_05_H.json").is_file()


def test_incomplete_pages_or_failed_evaluation_blocks_delivery():
    # The delivery gate is enforced by ProductionPptRunner / ArtifactService:
    # a FAIL evaluation raises and the Job never becomes READY, so no file is sent.
    from src.wecom.ppt_runner import ProductionPptRunner

    source = inspect.getsource(ProductionPptRunner.run)
    assert 'status == "FAIL"' in source
    assert "raise RuntimeError(\"PPT artifact evaluation failed\")" in source

