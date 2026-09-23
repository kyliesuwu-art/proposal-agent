from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class TaskStatus(StrEnum):
    NEW = "NEW"
    CLARIFYING = "CLARIFYING"
    READY = "READY"
    GENERATING_MD = "GENERATING_MD"
    WAITING_MD_APPROVAL = "WAITING_MD_APPROVAL"
    MD_APPROVED = "MD_APPROVED"
    FAILED = "FAILED"


class ArtifactType(StrEnum):
    WORD = "WORD"
    PPTX = "PPTX"


class ArtifactJobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    READY = "READY"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class WeComTask:
    task_id: str
    userid: str
    chatid: str
    original_request: str
    clarified_request: str | None
    status: TaskStatus
    created_at: str
    updated_at: str
    proposal_md_path: str | None = None
    approved_md_path: str | None = None
    error_stage: str | None = None
    error_message: str | None = None

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass(frozen=True)
class AgentEvent:
    name: str
    task: WeComTask


@dataclass(frozen=True)
class ArtifactJob:
    job_id: str
    task_id: str
    artifact_type: ArtifactType
    status: ArtifactJobStatus
    source_md_path: str
    source_md_sha256: str
    output_dir: str
    primary_artifact_path: str | None
    preview_artifact_path: str | None
    error_stage: str | None
    error_message: str | None
    created_at: str
    updated_at: str
    delivered_at: str | None = None
    parent_job_id: str | None = None
    resume_from_job_id: str | None = None
    retryable: bool = False
    failure_code: str | None = None

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        data["artifact_type"] = self.artifact_type.value
        data["status"] = self.status.value
        return data


@dataclass(frozen=True)
class ArtifactEvent:
    name: str
    job: ArtifactJob
    task: WeComTask
