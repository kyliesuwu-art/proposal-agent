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
