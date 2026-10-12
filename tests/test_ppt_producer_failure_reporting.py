from __future__ import annotations

from pathlib import Path

import pytest

import scripts.run_ppt_v4_ark_full20 as producer
from src.wecom.ppt_checkpoint import ResumeRejected
from src.wecom.ppt_failures import (
    PptFailureCode,
    PptModelBudgetExceededError,
    PptProducerAuthError,
    PptProducerConfigError,
    PptProducerInputError,
    PptProducerSecurityError,
    PptProducerRenderError,
    read_ppt_failure,
)


@pytest.fixture(autouse=True)
def reset_budget() -> None:
    producer.v4.MODEL_CALL_BUDGET = None
    yield
    producer.v4.MODEL_CALL_BUDGET = None


def call_producer(tmp_path: Path) -> None:
    producer.produce(
        tmp_path / "approved.md",
        tmp_path,
        tmp_path / "generated_scene_graph",
        tmp_path / "visual",
        tmp_path / "producer_manifest.json",
        26,
    )


def failing_produce(exc: BaseException, stage: str, *, used: int | None = None, maximum: int = 26):
    def inner(*_args: object, stage_callback=None, **_kwargs: object) -> int:
        assert stage_callback is not None
        stage_callback(stage)
        if used is not None:
            producer.v4.configure_model_call_budget(maximum, used=used)
        raise exc

    return inner


@pytest.mark.parametrize(
    ("exc", "code", "retryable"),
    [
        (producer.v4.ModelTransportError("changed", retryable=True), PptFailureCode.MODEL_TRANSPORT_RETRY_EXHAUSTED, True),
        (producer.v4.ModelTransportError("changed", retryable=True, status_code=503), PptFailureCode.MODEL_PROVIDER_TRANSIENT, True),
        (producer.v4.ModelJsonContractError("changed"), PptFailureCode.MODEL_OUTPUT_CONTRACT_RETRY_EXHAUSTED, True),
        (PptModelBudgetExceededError("changed"), PptFailureCode.MODEL_BUDGET_EXHAUSTED, False),
        (PptProducerConfigError("changed"), PptFailureCode.CONFIG_INVALID, False),
        (PptProducerAuthError("changed"), PptFailureCode.AUTH_REJECTED, False),
        (ResumeRejected("changed"), PptFailureCode.RESUME_CHECKPOINT_INVALID, False),
        (PptProducerSecurityError("changed"), PptFailureCode.SECURITY_REJECTED, False),
        (PptProducerInputError("changed"), PptFailureCode.INPUT_INVALID, False),
        (PptProducerRenderError("changed"), PptFailureCode.RENDER_FAILED, False),
        (RuntimeError("Timeout Authorization secret"), PptFailureCode.UNKNOWN_FAILURE, False),
    ],
)
def test_typed_failure_is_written_and_original_exception_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exc: BaseException, code: PptFailureCode, retryable: bool) -> None:
    monkeypatch.setattr(producer, "_produce", failing_produce(exc, "global_art_direction", used=3))
    with pytest.raises(type(exc)):
        call_producer(tmp_path)
    report = read_ppt_failure(tmp_path)
    assert (report.code, report.retryable, report.stage) == (code, retryable, "global_art_direction")
    assert (report.ark_attempts_used, report.ark_attempts_max) == (3, 26)
    assert "secret" not in (tmp_path / "ppt_failure.json").read_text(encoding="utf-8")


def test_structured_http_auth_status_is_not_retryable() -> None:
    report = producer.classify_producer_failure(
        producer.v4.ModelTransportError("any text", retryable=False, status_code=401),
        stage="global_art_direction",
        ark_attempts_used=1,
        ark_attempts_max=26,
    )
    assert report.code is PptFailureCode.AUTH_REJECTED
    assert report.retryable is False

def test_page_stage_and_inherited_budget_are_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        producer,
        "_produce",
        failing_produce(PptModelBudgetExceededError("budget"), "page_design:10", used=13),
    )
    with pytest.raises(PptModelBudgetExceededError):
        call_producer(tmp_path)
    report = read_ppt_failure(tmp_path)
    assert report.stage == "page_design:10"
    assert (report.ark_attempts_used, report.ark_attempts_max) == (13, 26)


def test_write_failure_error_does_not_mask_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = PptProducerInputError("input")
    monkeypatch.setattr(producer, "_produce", failing_produce(original, "startup"))
    monkeypatch.setattr(producer, "write_ppt_failure", lambda *_args: (_ for _ in ()).throw(OSError("write")))
    with pytest.raises(PptProducerInputError):
        call_producer(tmp_path)


def test_success_does_not_create_failure_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(producer, "_produce", lambda *_args, **_kwargs: 0)
    assert call_producer(tmp_path) is None
    assert not (tmp_path / "ppt_failure.json").exists()


def test_classification_never_uses_exception_message() -> None:
    first = producer.classify_producer_failure(
        producer.v4.ModelTransportError("first words", retryable=True),
        stage="page_design:01",
        ark_attempts_used=1,
        ark_attempts_max=26,
    )
    second = producer.classify_producer_failure(
        producer.v4.ModelTransportError("different Authorization body", retryable=True),
        stage="page_design:01",
        ark_attempts_used=1,
        ark_attempts_max=26,
    )
    assert first.code is second.code is PptFailureCode.MODEL_TRANSPORT_RETRY_EXHAUSTED