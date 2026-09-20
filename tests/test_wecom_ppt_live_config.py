from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import pytest

import src.wecom_bot as bot
from src.wecom.ppt_runner import ProductionPptRunner


def args(*extra: str) -> argparse.Namespace:
    return bot.parse_args(list(extra))


def valid_command() -> str:
    return json.dumps(["producer", "--output", "${SCENE_DIR}", "--budget", "${MODEL_MAX_CALLS}"])


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
    (("--enable-ppt-model", "--ppt-model-scene-command-json", valid_command()), "max-calls"),
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
    runner = bot.build_ppt_runner_from_args(args("--enable-ppt-model", "--ppt-model-scene-command-json", valid_command(), "--ppt-model-max-calls", "7"))
    assert runner.model_enabled and runner.model_scene_command[0] == "producer" and runner.model_max_calls == 7


def test_producer_argv_rejects_unknown_placeholder_and_never_uses_shell(tmp_path, monkeypatch):
    import scripts.run_ppt_production as production
    with pytest.raises(RuntimeError, match="unknown placeholder"):
        production._producer_argv(json.dumps(["producer", "${NOPE}", "${MODEL_MAX_CALLS}"]), input_md=tmp_path / "a.md", task_root=tmp_path, output_dir=tmp_path / "out", scene_dir=tmp_path / "scene", model_max_calls=1)
    calls = []
    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs)); (tmp_path / "out" / "generated_scene_graph" / "page_01.json").write_text("{}", encoding="utf-8")
        return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    monkeypatch.setenv("PPT_MODEL_LIVE_APPROVED", "1")
    monkeypatch.setattr(production.subprocess, "run", fake_run)
    production.generate_scene_graph(valid_command(), tmp_path / "a.md", tmp_path, tmp_path / "out", 1)
    assert calls[0][1]["shell"] is False and "${SCENE_DIR}" not in calls[0][0]


def test_model_enabled_rejects_existing_scene_directory(tmp_path, monkeypatch):
    import scripts.run_ppt_production as production
    monkeypatch.setenv("PPT_MODEL_LIVE_APPROVED", "1")
    (tmp_path / "out" / "generated_scene_graph").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="refuses an existing"):
        production.generate_scene_graph(valid_command(), tmp_path / "a.md", tmp_path, tmp_path / "out", 1)


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
    runner = ProductionPptRunner(tmp_path, model_enabled=True, model_scene_command=json.loads(valid_command()), model_max_calls=2)
    source = tmp_path / "approved.md"; source.write_text("# x", encoding="utf-8")
    runner.run(source, tmp_path, tmp_path / "out", "task", "job")
    assert "--enable-model" in seen[0][0] and "--disable-model" not in seen[0][0] and seen[0][1].get("shell") is None
