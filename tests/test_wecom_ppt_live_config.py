from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import pytest

import src.wecom_bot as bot
from src.wecom.ppt_runner import ProductionPptRunner


def args(*extra: str) -> argparse.Namespace:
    return bot.parse_args(list(extra))


def valid_command() -> str:
    return json.dumps(["${PYTHON_EXECUTABLE}", "producer", "--output", "${SCENE_DIR}", "--references", "${VISUAL_REFERENCE_ROOT}", "--budget", "${MODEL_MAX_CALLS}"])


def write_producer_file(path: Path, content: str | bytes | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else (content or valid_command()).encode("utf-8"))
    return path


def enabled_file_args(path: Path, reference_root: Path) -> argparse.Namespace:
    return args("--enable-ppt-model", "--ppt-model-scene-command-file", str(path), "--ppt-visual-reference-root", str(reference_root), "--ppt-model-max-calls", "26")


def test_default_bot_runner_keeps_model_disabled():
    runner = bot.build_ppt_runner_from_args(args())
    assert runner.model_enabled is False and runner.model_scene_command == () and runner.model_max_calls is None


def test_live_gate_alone_never_enables_model(monkeypatch):
    monkeypatch.setenv("PPT_MODEL_LIVE_APPROVED", "1")
    assert bot.build_ppt_runner_from_args(args()).model_enabled is False


@pytest.mark.parametrize("extra, message", [
    (("--enable-ppt-model",), "PPT_MODEL_LIVE_APPROVED"),
    (("--enable-ppt-model", "--ppt-model-scene-command-json", valid_command()), "PPT_MODEL_LIVE_APPROVED"),
])
def test_requested_live_model_requires_gate(monkeypatch, extra, message):
    monkeypatch.delenv("PPT_MODEL_LIVE_APPROVED", raising=False)
    with pytest.raises(ValueError, match=message):
        bot.build_ppt_runner_from_args(args(*extra))


@pytest.mark.parametrize("extra, message", [
    (("--enable-ppt-model", "--ppt-model-max-calls", "1"), "scene-command"),
    (("--enable-ppt-model", "--ppt-model-scene-command-json", valid_command(), "--ppt-model-max-calls", "1"), "visual-reference-root"),
    (("--enable-ppt-model", "--ppt-model-scene-command-json", valid_command(), "--ppt-visual-reference-root", "."), "max-calls"),
    (("--enable-ppt-model", "--ppt-model-scene-command-json", valid_command(), "--ppt-model-max-calls", "0"), "positive"),
    (("--enable-ppt-model", "--ppt-model-scene-command-json", valid_command(), "--ppt-model-max-calls", "-1"), "positive"),
    (("--enable-ppt-model", "--ppt-model-scene-command-json", json.dumps(["producer", "${UNKNOWN}", "${MODEL_MAX_CALLS}"]), "--ppt-model-max-calls", "1"), "unknown placeholder"),
])
def test_requested_live_model_rejects_incomplete_or_unsafe_config(monkeypatch, extra, message):
    monkeypatch.setenv("PPT_MODEL_LIVE_APPROVED", "1")
    with pytest.raises(ValueError, match=message):
        bot.build_ppt_runner_from_args(args(*extra))


def test_live_runner_receives_validated_argv_and_budget(monkeypatch):
    monkeypatch.setenv("PPT_MODEL_LIVE_APPROVED", "1")
    runner = bot.build_ppt_runner_from_args(args("--enable-ppt-model", "--ppt-model-scene-command-json", valid_command(), "--ppt-visual-reference-root", ".", "--ppt-model-max-calls", "7"))
    assert runner.model_enabled and runner.model_scene_command[1] == "producer" and runner.model_max_calls == 7 and runner.visual_reference_root == Path(".").resolve()


def test_file_command_constructs_model_enabled_runner_with_windows_style_unicode_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PPT_MODEL_LIVE_APPROVED", "1")
    producer_file = write_producer_file(tmp_path / "配置 空格" / "中文 producer.json")
    parsed = enabled_file_args(producer_file, tmp_path)
    assert parsed.ppt_model_scene_command_file == producer_file
    runner = bot.build_ppt_runner_from_args(parsed)
    assert runner.model_enabled is True
    assert runner.model_scene_command == tuple(json.loads(valid_command()))
    assert runner.model_max_calls == 26
    assert runner.visual_reference_root == tmp_path.resolve()


@pytest.mark.parametrize("content, message", [
    (b"\xff\xfe", "UTF-8"),
    ("{TOP_SECRET:not-json}", "JSON argv list"),
    (json.dumps({"argv": []}), "JSON array"),
    (json.dumps(["producer", 7]), "array of strings"),
    (json.dumps(["${UNKNOWN}", "${MODEL_MAX_CALLS}", "${VISUAL_REFERENCE_ROOT} "]), "unknown placeholder"),
])
def test_file_command_rejects_invalid_or_unsafe_contents_without_leaking_content(tmp_path, monkeypatch, content, message):
    monkeypatch.setenv("PPT_MODEL_LIVE_APPROVED", "1")
    producer_file = write_producer_file(tmp_path / "producer.json", content)
    with pytest.raises(ValueError, match=message) as exc_info:
        bot.build_ppt_runner_from_args(enabled_file_args(producer_file, tmp_path))
    assert "TOP_SECRET" not in str(exc_info.value)


@pytest.mark.parametrize("path_factory, message", [(lambda root: root / "missing.json", "existing regular file"), (lambda root: root, "existing regular file")])
def test_file_command_rejects_missing_and_directory_paths(tmp_path, monkeypatch, path_factory, message):
    monkeypatch.setenv("PPT_MODEL_LIVE_APPROVED", "1")
    with pytest.raises(ValueError, match=message):
        bot.build_ppt_runner_from_args(enabled_file_args(path_factory(tmp_path), tmp_path))


def test_file_command_rejects_oversized_and_unreadable_files(tmp_path, monkeypatch):
    monkeypatch.setenv("PPT_MODEL_LIVE_APPROVED", "1")
    oversized = write_producer_file(tmp_path / "large.json", " " * (64 * 1024 + 1))
    with pytest.raises(ValueError, match="64 KiB"):
        bot.build_ppt_runner_from_args(enabled_file_args(oversized, tmp_path))
    unreadable = write_producer_file(tmp_path / "unreadable.json")
    monkeypatch.setattr(Path, "read_bytes", lambda _self: (_ for _ in ()).throw(OSError("denied")))
    with pytest.raises(ValueError, match="readable"):
        bot.build_ppt_runner_from_args(enabled_file_args(unreadable, tmp_path))


def test_file_and_inline_command_are_mutually_exclusive(tmp_path):
    producer_file = write_producer_file(tmp_path / "producer.json")
    with pytest.raises(SystemExit):
        args("--ppt-model-scene-command-json", valid_command(), "--ppt-model-scene-command-file", str(producer_file))


def test_live_model_requires_exactly_one_command_source_with_file_option(monkeypatch, tmp_path):
    monkeypatch.setenv("PPT_MODEL_LIVE_APPROVED", "1")
    with pytest.raises(ValueError, match="exactly one"):
        bot.build_ppt_runner_from_args(args("--enable-ppt-model", "--ppt-visual-reference-root", str(tmp_path), "--ppt-model-max-calls", "26"))


def test_file_command_preserves_gate_and_never_starts_network(monkeypatch, tmp_path):
    monkeypatch.delenv("PPT_MODEL_LIVE_APPROVED", raising=False)
    producer_file = write_producer_file(tmp_path / "producer.json")
    with pytest.raises(ValueError, match="PPT_MODEL_LIVE_APPROVED"):
        bot.build_ppt_runner_from_args(enabled_file_args(producer_file, tmp_path))


def test_producer_argv_rejects_unknown_placeholder_and_never_uses_shell(tmp_path, monkeypatch):
    import scripts.run_ppt_production as production
    with pytest.raises(RuntimeError, match="unknown placeholder"):
        production._producer_argv(json.dumps(["producer", "${NOPE}", "${VISUAL_REFERENCE_ROOT}", "${MODEL_MAX_CALLS}"]), input_md=tmp_path / "a.md", task_root=tmp_path, output_dir=tmp_path / "out", scene_dir=tmp_path / "scene", visual_reference_root=tmp_path, model_max_calls=1)
    calls = []
    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs)); (tmp_path / "out" / "generated_scene_graph" / "page_01.json").write_text("{}", encoding="utf-8")
        return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    monkeypatch.setenv("PPT_MODEL_LIVE_APPROVED", "1")
    monkeypatch.setattr(production.subprocess, "run", fake_run)
    production.generate_scene_graph(valid_command(), tmp_path / "a.md", tmp_path, tmp_path / "out", tmp_path, 1)
    assert calls[0][1]["shell"] is False and "${SCENE_DIR}" not in calls[0][0]


def test_model_enabled_rejects_existing_scene_directory(tmp_path, monkeypatch):
    import scripts.run_ppt_production as production
    monkeypatch.setenv("PPT_MODEL_LIVE_APPROVED", "1")
    (tmp_path / "out" / "generated_scene_graph").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="refuses an existing"):
        production.generate_scene_graph(valid_command(), tmp_path / "a.md", tmp_path, tmp_path / "out", tmp_path, 1)


def test_v4_budget_stops_before_second_network_call(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("v4_budget", Path("scripts/run_ppt_visual_calibration_v4_ark.py"))
    module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)
    module.configure_model_call_budget(1)
    calls = []
    monkeypatch.setattr(module.PAD, "ask", lambda *_a, **_k: (calls.append(1) or ("{}", {"elapsed_seconds": 0, "ttft_seconds": 0})))
    with pytest.raises(RuntimeError, match="budget exhausted"):
        module.ask_json(call_id="two", system="x", content=[], raw_file=tmp_path / "two.json") if (module.MODEL_CALL_BUDGET.consume("one") is None) else None
    assert not calls


def test_production_runner_serializes_live_argv_without_secret(tmp_path, monkeypatch):
    seen = []
    def fake_run(command, **kwargs):
        seen.append((command, kwargs)); output = Path(command[command.index("--output-pptx") + 1]); output.parent.mkdir(parents=True, exist_ok=True)
        from zipfile import ZipFile
        with ZipFile(output, "w") as archive: archive.writestr("ppt/presentation.xml", "x")
        report = output.parent / "artifact_evaluation" / "evaluation_report.json"; report.parent.mkdir(); report.write_text('{"overall_status":"PASS","limitations":[]}', encoding="utf-8")
        return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    monkeypatch.setattr("src.wecom.ppt_runner.subprocess.run", fake_run)
    runner = ProductionPptRunner(tmp_path, model_enabled=True, model_scene_command=json.loads(valid_command()), model_max_calls=2, visual_reference_root=tmp_path)
    source = tmp_path / "approved.md"; source.write_text("# x", encoding="utf-8")
    runner.run(source, tmp_path, tmp_path / "out", "task", "job")
    assert "--enable-model" in seen[0][0] and "--disable-model" not in seen[0][0] and seen[0][1]["shell"] is False


def test_fake_producer_dry_run_writes_fresh_scene_graph_in_job_dir(tmp_path):
    import scripts.run_ppt_production as production
    output = tmp_path / "任务 输出"
    command = json.dumps([sys.executable, "-c", "import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.joinpath('page_01.json').write_text('{}')", "${SCENE_DIR}", "${VISUAL_REFERENCE_ROOT}", "${MODEL_MAX_CALLS}"])
    import os
    old = os.environ.get("PPT_MODEL_LIVE_APPROVED"); os.environ["PPT_MODEL_LIVE_APPROVED"] = "1"
    try:
        scene = production.generate_scene_graph(command, tmp_path / "approved.md", tmp_path, output, tmp_path, 3)
    finally:
        if old is None: os.environ.pop("PPT_MODEL_LIVE_APPROVED", None)
        else: os.environ["PPT_MODEL_LIVE_APPROVED"] = old
    assert scene == output / "generated_scene_graph" and (scene / "page_01.json").is_file()


def test_v4_producer_budget_contract_is_26_calls():
    import scripts.run_ppt_v4_ark_full20 as producer
    assert producer.MAX_MODEL_CALLS == 26


def test_v4_producer_requires_explicit_complete_visual_reference_root(tmp_path):
    import scripts.run_ppt_v4_ark_full20 as producer
    with pytest.raises(RuntimeError, match="visual resources"):
        producer.configure_production_visual_resources(tmp_path)
def test_v4_fake_producer_serializes_runtime_paths_relative_to_cross_root_job(tmp_path, monkeypatch):
    import scripts.run_ppt_v4_ark_full20 as producer

    job_root = tmp_path / "separate task root" / "job"
    input_md = job_root / "approved.md"; input_md.parent.mkdir(parents=True); input_md.write_text("# approved", encoding="utf-8")
    scene_dir = job_root / "generated_scene_graph"; manifest_path = job_root / "producer_manifest.json"
    monkeypatch.setattr(producer, "configure_production_visual_resources", lambda _root: None)
    monkeypatch.setattr(producer, "set_context", lambda: None)
    def fake_art_direction():
        (job_root / "scene_graphs").mkdir()
        return {"direction": "fake"}

    monkeypatch.setattr(producer.v4, "art_direction", fake_art_direction)
    monkeypatch.setattr(producer.v4, "page_request", lambda page, _direction, revision=False: {"elements": [], "page": page, "revision": revision})
    monkeypatch.setattr(producer.v4, "draw", lambda _scenes: job_root / "fake.pptx")

    def fake_render(_deck):
        pages = job_root / "pages" / "slide"; pages.mkdir(parents=True, exist_ok=True)
        rendered = []
        for page in range(1, 21):
            path = pages / f"slide{page}.png"; path.write_bytes(b"fake"); rendered.append(path)
        return rendered

    monkeypatch.setattr(producer.v4, "render", fake_render)
    monkeypatch.setattr(producer.v4, "contact", lambda _rendered, path, _labels: path.write_bytes(b"fake"))
    monkeypatch.setattr(producer.v4, "critic", lambda _direction, _comparison: {"selected_pages": []})
    monkeypatch.setattr(producer.v4, "compare", lambda _rendered: job_root / "comparison.png")
    producer.produce(input_md, job_root, scene_dir, tmp_path / "visual root", manifest_path, 26)
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["scene_graph_dir"] == "generated_scene_graph"
    assert (scene_dir / "page_01_G.json").is_file()

    calibration = producer.v4; calibration.OUT = job_root
    raw = job_root / "raw" / "cross-root.json"
    monkeypatch.setattr(calibration.PAD, "ask", lambda *_a, **_k: ("{}", {"elapsed_seconds": 0, "ttft_seconds": 0}))
    calibration.ask_json(call_id="fake", system="x", content=[], raw_file=raw)
    assert json.loads((job_root / "ark_calls.json").read_text(encoding="utf-8"))[0]["response_path"] == str(Path("raw") / "cross-root.json")

    (job_root / "scene_graphs").mkdir(exist_ok=True)
    (job_root / "scene_graphs" / "page_01_G.json").write_text('{"elements": []}', encoding="utf-8")
    monkeypatch.setattr(calibration, "PICK", (1,)); monkeypatch.setattr(calibration, "NAMES", {1: "G"})
    monkeypatch.setattr(calibration.PAD, "validate", lambda *_a, **_k: {})
    calibration.report({}, {1: {"elements": []}}, {}, [job_root / "pages" / "slide1.png"])
    report = json.loads((job_root / "visual_calibration_v4_report.json").read_text(encoding="utf-8"))
    assert report["scenes"] == {"1": str(Path("scene_graphs") / "page_01_G.json")}
    assert report["rendered_pages"] == [str(Path("pages") / "slide1.png")]

def test_live_producer_uses_inherited_env_when_its_project_env_file_is_absent(tmp_path, monkeypatch):
    import scripts.run_ppt_pure_art_director as director
    monkeypatch.setattr(director, "ROOT", tmp_path)
    monkeypatch.setenv("ARK_API_KEY", "test-inherited-key")
    monkeypatch.setenv("ARK_BASE_URL", "https://example.invalid")
    assert director.read_env() == {
        "ARK_API_KEY": "test-inherited-key",
        "ARK_BASE_URL": "https://example.invalid",
    }
