"""Structured, conservative failure reports for PPT production jobs."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

PPT_FAILURE_SCHEMA = "ppt-failure/v1"
PPT_FAILURE_FILENAME = "ppt_failure.json"
MAX_FAILURE_REPORT_BYTES = 64 * 1024
MAX_STAGE_LENGTH = 100
MAX_SAFE_MESSAGE_LENGTH = 500
_UNSAFE_MESSAGE_MARKERS = ("authorization", "bearer ", "api_key", "api-key", "traceback")


class PptFailureCode(str, Enum):
    MODEL_TRANSPORT_RETRY_EXHAUSTED = "PPT_MODEL_TRANSPORT_RETRY_EXHAUSTED"
    MODEL_PROVIDER_TRANSIENT = "PPT_MODEL_PROVIDER_TRANSIENT"
    MODEL_OUTPUT_CONTRACT_RETRY_EXHAUSTED = "PPT_MODEL_OUTPUT_CONTRACT_RETRY_EXHAUSTED"
    MODEL_BUDGET_EXHAUSTED = "PPT_MODEL_BUDGET_EXHAUSTED"
    INPUT_INVALID = "PPT_INPUT_INVALID"
    RESUME_CHECKPOINT_INVALID = "PPT_RESUME_CHECKPOINT_INVALID"
    SECURITY_REJECTED = "PPT_SECURITY_REJECTED"
    CONFIG_INVALID = "PPT_CONFIG_INVALID"
    AUTH_REJECTED = "PPT_AUTH_REJECTED"
    PRODUCER_FAILED = "PPT_PRODUCER_FAILED"
    RENDER_FAILED = "PPT_RENDER_FAILED"
    EVALUATION_FAILED = "PPT_EVALUATION_FAILED"
    UNKNOWN_FAILURE = "PPT_UNKNOWN_FAILURE"


@dataclass(frozen=True)
class PptFailureInfo:
    code: PptFailureCode
    stage: str
    retryable: bool
    safe_message: str
    ark_attempts_used: int | None = None
    ark_attempts_max: int | None = None


class PptRunnerFailure(RuntimeError):
    """A future Runner boundary can raise this without string parsing."""

    def __init__(self, failure: PptFailureInfo) -> None:
        self.failure = failure
        super().__init__(failure.safe_message)


class PptModelBudgetExceededError(RuntimeError):
    """The producer-wide Ark request budget was exhausted before a request."""


class PptProducerConfigError(RuntimeError):
    """Required producer configuration is invalid or unavailable."""


class PptProducerAuthError(RuntimeError):
    """The provider rejected the configured credentials."""


class PptProducerInputError(RuntimeError):
    """Locked PPT inputs cannot be safely consumed."""


class PptProducerSecurityError(RuntimeError):
    """A path or filesystem safety contract was rejected."""


class PptProducerRenderError(RuntimeError):
    """The deterministic PPT rendering stage failed."""


def unknown_ppt_failure(*, stage: str = "unknown") -> PptFailureInfo:
    return PptFailureInfo(
        code=PptFailureCode.UNKNOWN_FAILURE,
        stage=stage,
        retryable=False,
        safe_message="PPT failed without a valid structured failure report.",
    )


def _is_link_or_reparse_point(path: Path) -> bool:
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        return False
    is_junction = getattr(path, "is_junction", lambda: False)
    file_attributes = getattr(stat_result, "st_file_attributes", 0)
    file_attribute_reparse_point = 0x400
    return path.is_symlink() or is_junction() or bool(file_attributes & file_attribute_reparse_point)


def _safe_job_root(job_root: Path) -> Path:
    if not job_root.is_dir() or _is_link_or_reparse_point(job_root):
        raise ValueError("job root must be a normal directory")
    return job_root.resolve(strict=True)

def _path_exists_or_link(path: Path) -> bool:
    return os.path.lexists(path)


def _report_path(job_root: Path, *, existing_required: bool) -> Path:
    root = _safe_job_root(job_root)
    target = root / PPT_FAILURE_FILENAME
    if target.parent.resolve(strict=True) != root:
        raise ValueError("failure report escaped job root")
    if existing_required and not _path_exists_or_link(target):
        raise FileNotFoundError(target)
    if _path_exists_or_link(target) and (
        _is_link_or_reparse_point(target) or target.is_dir() or not target.is_file()
    ):
        raise ValueError("failure report must be a normal file")
    if _path_exists_or_link(target) and target.resolve(strict=True).parent != root:
        raise ValueError("failure report escaped job root")
    return target


def _is_nonnegative_int_or_none(value: object) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool) and value >= 0)


def _validate_failure(failure: PptFailureInfo) -> None:
    if not isinstance(failure.code, PptFailureCode):
        raise ValueError("unknown PPT failure code")
    if (
        not isinstance(failure.stage, str)
        or not failure.stage
        or failure.stage != failure.stage.strip()
        or len(failure.stage) > MAX_STAGE_LENGTH
    ):
        raise ValueError("invalid PPT failure stage")
    if (
        not isinstance(failure.retryable, bool)
        or not isinstance(failure.safe_message, str)
        or len(failure.safe_message) > MAX_SAFE_MESSAGE_LENGTH
        or any(marker in failure.safe_message.lower() for marker in _UNSAFE_MESSAGE_MARKERS)
    ):
        raise ValueError("invalid PPT failure fields")
    if not _is_nonnegative_int_or_none(failure.ark_attempts_used) or not _is_nonnegative_int_or_none(
        failure.ark_attempts_max
    ):
        raise ValueError("invalid Ark attempt counts")
    if (
        failure.ark_attempts_used is not None
        and failure.ark_attempts_max is not None
        and failure.ark_attempts_used > failure.ark_attempts_max
    ):
        raise ValueError("Ark attempts used exceeds maximum")


def _payload(failure: PptFailureInfo) -> dict[str, Any]:
    return {
        "schema": PPT_FAILURE_SCHEMA,
        "code": failure.code.value,
        "stage": failure.stage,
        "retryable": failure.retryable,
        "safe_message": failure.safe_message,
        "ark_attempts_used": failure.ark_attempts_used,
        "ark_attempts_max": failure.ark_attempts_max,
    }


def write_ppt_failure(job_root: Path, failure: PptFailureInfo) -> Path:
    """Atomically publish the sole structured failure report for a job."""
    _validate_failure(failure)
    target = _report_path(job_root, existing_required=False)
    temporary = target.with_name(f".{target.name}.tmp")
    if _path_exists_or_link(temporary) and (
        _is_link_or_reparse_point(temporary) or temporary.is_dir()
    ):
        raise ValueError("unsafe failure report temporary path")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(_payload(failure), handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if _path_exists_or_link(temporary) and not _is_link_or_reparse_point(temporary):
            temporary.unlink()
    return target


def load_ppt_failure_strict(job_root: Path) -> PptFailureInfo:
    """Read and validate a report, raising on any unsafe or invalid input."""
    target = _report_path(job_root, existing_required=True)
    if target.stat().st_size > MAX_FAILURE_REPORT_BYTES:
        raise ValueError("failure report is too large")
    with target.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    required_keys = {
        "schema",
        "code",
        "stage",
        "retryable",
        "safe_message",
        "ark_attempts_used",
        "ark_attempts_max",
    }
    if not isinstance(payload, dict) or set(payload) != required_keys:
        raise ValueError("invalid failure report schema")
    if payload["schema"] != PPT_FAILURE_SCHEMA:
        raise ValueError("unsupported failure report schema")
    failure = PptFailureInfo(
        code=PptFailureCode(payload["code"]),
        stage=payload["stage"],
        retryable=payload["retryable"],
        safe_message=payload["safe_message"],
        ark_attempts_used=payload["ark_attempts_used"],
        ark_attempts_max=payload["ark_attempts_max"],
    )
    _validate_failure(failure)
    return failure


def read_ppt_failure(job_root: Path) -> PptFailureInfo:
    """Conservative reader: every read failure becomes UNKNOWN/non-retryable."""
    try:
        return load_ppt_failure_strict(job_root)
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return unknown_ppt_failure()