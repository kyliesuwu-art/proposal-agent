from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class EvaluationStatus(StrEnum):
    PASS = "PASS"
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
    FAIL = "FAIL"
    NOT_RUN = "NOT_RUN"


class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(frozen=True)
class CheckResult:
    code: str
    status: EvaluationStatus
    severity: Severity
    artifact_type: str
    message: str
    evidence: str | None = None
    location: str | None = None
    metric_value: Any = None
    expected_value: Any = None

    def payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ArtifactReport:
    artifact_type: str
    path: str
    sha256: str | None
    file_size: int | None
    profile: str
    metrics: dict[str, Any] = field(default_factory=dict)
    checks: list[CheckResult] = field(default_factory=list)
    status: EvaluationStatus = EvaluationStatus.NOT_RUN

    def payload(self) -> dict[str, Any]:
        data = asdict(self); data["status"] = self.status.value
        data["checks"] = [item.payload() for item in self.checks]
        return data


def aggregate(checks: list[CheckResult], *, required: bool = True) -> EvaluationStatus:
    """Never turn skipped work into a pass."""
    if any(item.status is EvaluationStatus.FAIL or item.severity is Severity.ERROR for item in checks):
        return EvaluationStatus.FAIL
    if any(item.status is EvaluationStatus.PASS_WITH_WARNINGS or item.severity is Severity.WARNING for item in checks):
        return EvaluationStatus.PASS_WITH_WARNINGS
    if not checks or (required and all(item.status is EvaluationStatus.NOT_RUN for item in checks)):
        return EvaluationStatus.NOT_RUN
    return EvaluationStatus.PASS


@dataclass
class EvaluationReport:
    schema_version: str
    generated_at: str
    profile: str
    inputs: dict[str, str]
    artifact_reports: list[ArtifactReport]
    cross_artifact_checks: list[CheckResult]
    overall_status: EvaluationStatus
    summary: dict[str, Any]
    limitations: list[str]

    def payload(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "generated_at": self.generated_at, "profile": self.profile,
                "inputs": self.inputs, "artifact_reports": [item.payload() for item in self.artifact_reports],
                "cross_artifact_checks": [item.payload() for item in self.cross_artifact_checks],
                "overall_status": self.overall_status.value, "summary": self.summary, "limitations": self.limitations}
