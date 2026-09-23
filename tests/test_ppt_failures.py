from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.wecom import ppt_failures
from src.wecom.ppt_failures import (
    MAX_FAILURE_REPORT_BYTES,
    PPT_FAILURE_FILENAME,
    PPT_FAILURE_SCHEMA,
    PptFailureCode,
    PptFailureInfo,
    PptRunnerFailure,
    read_ppt_failure,
    unknown_ppt_failure,
    write_ppt_failure,
)


def failure(**changes: object) -> PptFailureInfo:
    values: dict[str, object] = {
        "code": PptFailureCode.MODEL_TRANSPORT_RETRY_EXHAUSTED,
        "stage": "page_design",
        "retryable": True,
        "safe_message": "PPT model transport attempts were exhausted.",
        "ark_attempts_used": 3,
        "ark_attempts_max": 26,
    }
    values.update(changes)
    return PptFailureInfo(**values)  # type: ignore[arg-type]


def payload(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema": PPT_FAILURE_SCHEMA,
        "code": PptFailureCode.MODEL_TRANSPORT_RETRY_EXHAUSTED.value,
        "stage": "page_design",
        "retryable": True,
        "safe_message": "safe",
        "ark_attempts_used": 3,
        "ark_attempts_max": 26,
    }
    values.update(changes)
    return values


def write_raw(root: Path, content: bytes) -> None:
    (root / PPT_FAILURE_FILENAME).write_bytes(content)


def test_round_trip_all_codes_and_runner_exception(tmp_path: Path) -> None:
    for code in PptFailureCode:
        original = failure(code=code, retryable=code is PptFailureCode.MODEL_PROVIDER_TRANSIENT)
        write_ppt_failure(tmp_path, original)
        assert read_ppt_failure(tmp_path) == original
    error = PptRunnerFailure(failure())
    assert error.failure == failure()


def test_missing_and_invalid_reports_are_conservative(tmp_path: Path) -> None:
    assert read_ppt_failure(tmp_path).code is PptFailureCode.UNKNOWN_FAILURE
    invalids = [
        b"{bad",
        b"\xff\xfe",
        json.dumps(payload(schema="wrong")).encode(),
        json.dumps(payload(code="NOPE")).encode(),
        json.dumps(payload(retryable=1)).encode(),
        json.dumps(payload(ark_attempts_used=-1)).encode(),
        json.dumps(payload(ark_attempts_used=27, ark_attempts_max=26)).encode(),
    ]
    for raw in invalids:
        write_raw(tmp_path, raw)
        recovered = read_ppt_failure(tmp_path)
        assert recovered.code is PptFailureCode.UNKNOWN_FAILURE
        assert recovered.retryable is False


def test_limits_directory_and_safe_message_validation(tmp_path: Path) -> None:
    write_raw(tmp_path, b"x" * (MAX_FAILURE_REPORT_BYTES + 1))
    assert read_ppt_failure(tmp_path).retryable is False
    (tmp_path / PPT_FAILURE_FILENAME).unlink()
    (tmp_path / PPT_FAILURE_FILENAME).mkdir()
    assert read_ppt_failure(tmp_path).code is PptFailureCode.UNKNOWN_FAILURE
    (tmp_path / PPT_FAILURE_FILENAME).rmdir()
    with pytest.raises(ValueError):
        write_ppt_failure(tmp_path, failure(safe_message="x" * 501))
    with pytest.raises(ValueError):
        write_ppt_failure(tmp_path, failure(safe_message="Authorization: secret"))
    with pytest.raises(ValueError):
        write_ppt_failure(tmp_path, failure(ark_attempts_used=True))


def test_atomic_output_is_deterministic_and_has_no_official_temp(tmp_path: Path) -> None:
    original = failure()
    report = write_ppt_failure(tmp_path, original)
    first = json.loads(report.read_text(encoding="utf-8"))
    write_ppt_failure(tmp_path, original)
    second = json.loads(report.read_text(encoding="utf-8"))
    assert first == second
    assert not (tmp_path / f".{PPT_FAILURE_FILENAME}.tmp").exists()
    text = report.read_text(encoding="utf-8")
    assert "Authorization" not in text and "Traceback" not in text


def test_failed_replace_leaves_no_formal_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_replace(source: object, destination: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(ppt_failures.os, "replace", fail_replace)
    with pytest.raises(OSError):
        write_ppt_failure(tmp_path, failure())
    assert not (tmp_path / PPT_FAILURE_FILENAME).exists()
    assert not (tmp_path / f".{PPT_FAILURE_FILENAME}.tmp").exists()


def test_symlink_report_and_job_root_are_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text(json.dumps(payload()), encoding="utf-8")
    report_link = tmp_path / PPT_FAILURE_FILENAME
    try:
        report_link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    assert read_ppt_failure(tmp_path).code is PptFailureCode.UNKNOWN_FAILURE
    linked_root = tmp_path.parent / f"{tmp_path.name}-linked"
    try:
        linked_root.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")
    assert read_ppt_failure(linked_root).code is PptFailureCode.UNKNOWN_FAILURE


def test_unknown_is_never_retryable() -> None:
    assert unknown_ppt_failure().retryable is False
    assert unknown_ppt_failure(stage="read_failure").code is PptFailureCode.UNKNOWN_FAILURE