from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

import pytest

import scripts.run_ppt_production as production
from src.wecom.ppt_failures import (
    PPT_FAILURE_FILENAME,
    PptFailureCode,
    PptFailureInfo,
    PptRunnerFailure,
    read_ppt_failure,
    write_ppt_failure,
)
from src.wecom.ppt_runner import PptResult, ProductionPptRunner


@pytest.fixture
def model_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PPT_MODEL_LIVE_APPROVED", "1")


def command() -> str:
    return json.dumps(["fake-producer", "${SCENE_DIR}", "${VISUAL_REFERENCE_ROOT}", "${MODEL_MAX_CALLS}"])


def run_generate(tmp_path: Path) -> Path:
    return production.generate_scene_graph(
        command(), tmp_path / "approved.md", tmp_path, tmp_path / "job", tmp_path, 26
    )


def fake_completed(returncode: int, *, stdout: str = "", stderr: str = ""):
    return type("Completed", (), {"returncode": returncode, "stdout": stdout, "stderr": stderr})()


def test_nonzero_producer_preserves_valid_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model_gate: None) -> None:
    root = tmp_path / "job"; root.mkdir()
    original = PptFailureInfo(PptFailureCode.MODEL_TRANSPORT_RETRY_EXHAUSTED, "page_design:10", True, "PPT model transport attempts were exhausted.", 13, 26)
    write_ppt_failure(root, original)
    monkeypatch.setattr(production.subprocess, "run", lambda *_args, **_kwargs: fake_completed(1, stderr="Timeout"))
    with pytest.raises(RuntimeError):
        run_generate(tmp_path)
    assert read_ppt_failure(root) == original


def test_nonzero_producer_missing_or_invalid_report_gets_conservative_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model_gate: None) -> None:
    monkeypatch.setattr(production.subprocess, "run", lambda *_args, **_kwargs: fake_completed(1, stderr="Timeout"))
    with pytest.raises(RuntimeError):
        run_generate(tmp_path)
    assert read_ppt_failure(tmp_path / "job").code is PptFailureCode.PRODUCER_FAILED

    root = tmp_path / "job2"; root.mkdir(); (root / PPT_FAILURE_FILENAME).write_text("{bad", encoding="utf-8")
    with pytest.raises(RuntimeError):
        production.generate_scene_graph(command(), tmp_path / "approved.md", tmp_path, root, tmp_path, 26)
    assert read_ppt_failure(root).code is PptFailureCode.PRODUCER_FAILED


def test_main_wrapper_writes_render_failure_without_message_parsing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(context: production._ProductionContext) -> int:
        context.output_dir = tmp_path
        context.stage = "render"
        raise RuntimeError("Timeout Authorization secret")

    monkeypatch.setattr(production, "_main", broken)
    with pytest.raises(RuntimeError):
        production.main()
    assert read_ppt_failure(tmp_path).code is PptFailureCode.RENDER_FAILED


def runner_with_fake_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, completed) -> ProductionPptRunner:
    monkeypatch.setattr("src.wecom.ppt_runner.subprocess.run", lambda *_args, **kwargs: completed)
    return ProductionPptRunner(tmp_path, model_enabled=True, model_scene_command=["fake", "${VISUAL_REFERENCE_ROOT}", "${MODEL_MAX_CALLS}"], model_max_calls=26, visual_reference_root=tmp_path)


@pytest.mark.parametrize(
    "failure",
    [
        PptFailureInfo(PptFailureCode.MODEL_TRANSPORT_RETRY_EXHAUSTED, "page_design", True, "PPT model transport attempts were exhausted."),
        PptFailureInfo(PptFailureCode.MODEL_PROVIDER_TRANSIENT, "page_design", True, "PPT model provider attempts were exhausted."),
        PptFailureInfo(PptFailureCode.MODEL_OUTPUT_CONTRACT_RETRY_EXHAUSTED, "global_art_direction", True, "PPT model output contract attempts were exhausted."),
        PptFailureInfo(PptFailureCode.MODEL_BUDGET_EXHAUSTED, "page_design", False, "PPT model call budget was exhausted."),
        PptFailureInfo(PptFailureCode.SECURITY_REJECTED, "startup", False, "PPT producer filesystem safety validation failed."),
    ],
)
def test_runner_propagates_valid_structured_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: PptFailureInfo) -> None:
    write_ppt_failure(tmp_path, failure)
    runner = runner_with_fake_process(tmp_path, monkeypatch, fake_completed(1, stderr="Timeout changed"))
    approved = tmp_path / "approved.md"; approved.write_text("# approved", encoding="utf-8")
    with pytest.raises(PptRunnerFailure) as caught:
        runner.run(approved, tmp_path, tmp_path, "task", "job")
    assert caught.value.failure == failure
    assert isinstance(caught.value.__cause__, subprocess.CalledProcessError)


def test_runner_missing_or_invalid_report_is_unknown_and_stderr_is_not_classified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = runner_with_fake_process(tmp_path, monkeypatch, fake_completed(1, stderr="Timeout changed"))
    approved = tmp_path / "approved.md"; approved.write_text("# approved", encoding="utf-8")
    with pytest.raises(PptRunnerFailure) as caught:
        runner.run(approved, tmp_path, tmp_path, "task", "job")
    assert caught.value.failure.code is PptFailureCode.UNKNOWN_FAILURE
    assert caught.value.failure.retryable is False
    assert "secret" not in (tmp_path / "ppt_runner.stderr.log").read_text(encoding="utf-8")


def test_runner_success_behavior_and_shell_false_are_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict] = []

    def successful(command: list[str], **kwargs: object):
        seen.append(kwargs)
        output = Path(command[command.index("--output-pptx") + 1])
        with ZipFile(output, "w") as archive:
            archive.writestr("ppt/presentation.xml", "x")
        report = output.parent / "artifact_evaluation" / "evaluation_report.json"; report.parent.mkdir()
        report.write_text('{"overall_status":"PASS","limitations":[]}', encoding="utf-8")
        return fake_completed(0)

    monkeypatch.setattr("src.wecom.ppt_runner.subprocess.run", successful)
    runner = ProductionPptRunner(tmp_path, existing_scene_dir=tmp_path / "scene")
    approved = tmp_path / "approved.md"; approved.write_text("# approved", encoding="utf-8")
    result = runner.run(approved, tmp_path, tmp_path, "task", "job")
    assert isinstance(result, PptResult)
    assert seen[0]["shell"] is False
    assert not (tmp_path / PPT_FAILURE_FILENAME).exists()


@dataclass
class FakePptRunner:
    failure: PptFailureInfo | None = None

    def run(self, *_args: object, **_kwargs: object) -> PptResult:
        if self.failure:
            raise PptRunnerFailure(self.failure)
        return PptResult(Path("proposal.pptx"), evaluation_status="PASS")


def test_fake_runner_can_raise_structured_failure() -> None:
    failure = PptFailureInfo(PptFailureCode.INPUT_INVALID, "startup", False, "PPT producer input is invalid.")
    with pytest.raises(PptRunnerFailure) as caught:
        FakePptRunner(failure).run()
    assert caught.value.failure is failure