from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from src.wecom.adapter import IncomingMessage, WeComAgentAdapter
from src.wecom.artifact_service import PptResumeRejected
from src.wecom.control_service import TaskControlService
from src.wecom.models import ArtifactJob, ArtifactJobStatus, ArtifactType, TaskStatus, WeComTask
from src.wecom.store import SQLiteTaskStore
from src.wecom.task_service import TaskService


class ProposalRunner:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(self, request: str, output_dir: Path) -> Path:
        self.calls.append(request)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "proposal.md"
        path.write_text("# proposal\n", encoding="utf-8")
        return path


class FakeArtifacts:
    def __init__(self, output_root: Path, *, reject: bool = False) -> None:
        self.output_root = output_root.resolve()
        self.reject = reject
        self.calls: list[dict[str, str]] = []
        self.completed = False

    def subscribe(self, _listener) -> None:
        return None

    def receive(self, _message):
        return None

    def resume_ppt(self, *, task_id: str, source_job_id: str, user_id: str, chat_id: str) -> ArtifactJob:
        self.calls.append({"task_id": task_id, "source_job_id": source_job_id, "user_id": user_id, "chat_id": chat_id})
        if self.reject:
            raise PptResumeRejected("source rejected")
        now = "2026-09-24T00:00:00+08:00"
        return ArtifactJob(
            "resume-child", task_id, ArtifactType.PPTX, ArtifactJobStatus.QUEUED,
            "approved.md", "sha", str(self.output_root / task_id / "resume-child"),
            None, None, None, None, now, now,
            parent_job_id=source_job_id, resume_from_job_id=source_job_id,
        )


class Transport:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, _frame: object, text: str) -> None:
        self.replies.append(text)

    async def send_text(self, _chatid: str, _text: str) -> None:
        return None

    async def send_file(self, _chatid: str, _path: Path) -> None:
        return None


@dataclass
class Parts:
    store: SQLiteTaskStore
    proposal: ProposalRunner
    tasks: TaskService
    artifacts: FakeArtifacts
    controls: TaskControlService
    transport: Transport
    adapter: WeComAgentAdapter


def harness(tmp_path: Path, *, reject: bool = False) -> Parts:
    store = SQLiteTaskStore(tmp_path / "state.sqlite")
    proposal = ProposalRunner()
    tasks = TaskService(store, proposal, output_root=tmp_path / "tasks")
    artifacts = FakeArtifacts(tmp_path / "artifacts", reject=reject)
    controls = TaskControlService(store, artifacts)
    transport = Transport()
    return Parts(store, proposal, tasks, artifacts, controls, transport, WeComAgentAdapter(tasks, transport, artifacts, controls))


def task(parts: Parts, *, user: str = "u1", chat: str = "c1") -> WeComTask:
    root = parts.tasks._output_root / "a1b2c3d4e5f6"
    root.mkdir(parents=True, exist_ok=True)
    approved = root / "approved.md"
    approved.write_text("# approved\n", encoding="utf-8")
    value = WeComTask("a1b2c3d4e5f6", user, chat, "request", "request", TaskStatus.MD_APPROVED, "2026-09-24T00:00:00+08:00", "2026-09-24T00:00:00+08:00", approved_md_path=str(approved))
    parts.store.create(value)
    return value


def source(parts: Parts, owner: WeComTask, job_id: str, *, created_at: str, retryable: bool = True, status: ArtifactJobStatus = ArtifactJobStatus.FAILED) -> ArtifactJob:
    job = ArtifactJob(job_id, owner.task_id, ArtifactType.PPTX, status, owner.approved_md_path or "", "sha", str(parts.artifacts.output_root / owner.task_id / job_id), None, None, None, None, created_at, created_at, retryable=retryable)
    parts.store.create_artifact_job(job)
    return job


def close(parts: Parts) -> None:
    parts.tasks.shutdown()
    parts.store.close()


def send(parts: Parts, content: str, message_id: str) -> None:
    asyncio.run(parts.adapter.handle(IncomingMessage("u1", "c1", content, message_id), object()))


def test_exact_resume_command_selects_latest_source_and_acknowledges_before_completion(tmp_path: Path) -> None:
    parts = harness(tmp_path)
    try:
        owner = task(parts)
        source(parts, owner, "oldsource001", created_at="2026-09-24T00:00:00+08:00")
        latest = source(parts, owner, "newsource001", created_at="2026-09-24T01:00:00+08:00")
        send(parts, f"  继续生成PPT   {owner.task_id}  ", "resume-1")
        assert parts.artifacts.calls == [{"task_id": owner.task_id, "source_job_id": latest.job_id, "user_id": "u1", "chat_id": "c1"}]
        assert "断点继续生成" in parts.transport.replies[-1]
        assert parts.artifacts.completed is False
        assert parts.proposal.calls == []
    finally:
        close(parts)


@pytest.mark.parametrize("content", ["继续生成PPT", "继续生成PPT a1b2c3d4e5f6 extra", "继续生成ppt a1b2c3d4e5f6", "继续做PPT a1b2c3d4e5f6"])
def test_invalid_resume_syntax_is_handled_as_control_without_side_effects(tmp_path: Path, content: str) -> None:
    parts = harness(tmp_path)
    try:
        owner = task(parts)
        send(parts, content, f"invalid-{content}")
        assert "继续生成PPT <任务编号>" in parts.transport.replies[-1]
        assert not parts.artifacts.calls
        assert parts.proposal.calls == []
        assert parts.store.tasks_for("u1", "c1") == [owner]
    finally:
        close(parts)


def test_unsupported_new_ppt_command_does_not_create_clarifying_task(tmp_path: Path) -> None:
    parts = harness(tmp_path)
    try:
        send(parts, "新生成PPT a1b2c3d4e5f6", "new-ppt")
        assert "未识别该 PPT 命令" in parts.transport.replies[-1]
        assert parts.store.tasks_for("u1", "c1") == []
        assert parts.proposal.calls == [] and not parts.artifacts.calls
    finally:
        close(parts)


def test_missing_or_invalid_latest_source_never_falls_back_to_older_source(tmp_path: Path) -> None:
    parts = harness(tmp_path, reject=True)
    try:
        owner = task(parts)
        source(parts, owner, "oldsource001", created_at="2026-09-24T00:00:00+08:00")
        latest = source(parts, owner, "newsource001", created_at="2026-09-24T01:00:00+08:00")
        send(parts, f"继续生成PPT {owner.task_id}", "resume-invalid")
        assert [call["source_job_id"] for call in parts.artifacts.calls] == [latest.job_id]
        assert "重新生成PPT" in parts.transport.replies[-1]
    finally:
        close(parts)


def test_missing_source_active_job_and_user_chat_isolation_are_safe(tmp_path: Path) -> None:
    parts = harness(tmp_path)
    try:
        owner = task(parts)
        send(parts, f"继续生成PPT {owner.task_id}", "none")
        assert "重新生成PPT" in parts.transport.replies[-1] and not parts.artifacts.calls
        source(parts, owner, "activejob001", created_at="2026-09-24T01:00:00+08:00", status=ArtifactJobStatus.RUNNING)
        send(parts, f"继续生成PPT {owner.task_id}", "active")
        assert "正在生成" in parts.transport.replies[-1] and not parts.artifacts.calls
        asyncio.run(parts.adapter.handle(IncomingMessage("u2", "c1", f"继续生成PPT {owner.task_id}", "other-user"), object()))
        asyncio.run(parts.adapter.handle(IncomingMessage("u1", "c2", f"继续生成PPT {owner.task_id}", "other-chat"), object()))
        assert all("未找到可访问的任务" in reply for reply in parts.transport.replies[-2:])
    finally:
        close(parts)


def test_resume_command_msgid_is_deduplicated_across_restart_and_help_is_updated(tmp_path: Path) -> None:
    parts = harness(tmp_path)
    try:
        owner = task(parts)
        source(parts, owner, "sourcejob001", created_at="2026-09-24T01:00:00+08:00")
        send(parts, f"继续生成PPT {owner.task_id}", "duplicate")
        send(parts, f"继续生成PPT {owner.task_id}", "duplicate")
        assert len(parts.artifacts.calls) == 1
        restarted = TaskControlService(parts.store, parts.artifacts)
        assert restarted.receive(IncomingMessage("u1", "c1", f"继续生成PPT {owner.task_id}", "duplicate")).response is None
        send(parts, "帮助", "help")
        assert "继续生成PPT <任务编号>" in parts.transport.replies[-1]
    finally:
        close(parts)


def test_ordinary_requirement_still_reaches_task_service(tmp_path: Path) -> None:
    parts = harness(tmp_path)
    try:
        send(parts, "医院园区供配电改造方案，重点包括可靠性和智能巡检", "ordinary")
        parts.tasks.wait_for_idle()
        assert len(parts.proposal.calls) == 1
        assert not parts.artifacts.calls
    finally:
        close(parts)
