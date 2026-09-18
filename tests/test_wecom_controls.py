from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from docx import Document

from src.wecom.adapter import IncomingMessage, WeComAgentAdapter
from src.wecom.artifact_service import ArtifactService
from src.wecom.control_service import TaskControlService
from src.wecom.models import ArtifactJob, ArtifactJobStatus, ArtifactType, TaskStatus
from src.wecom.store import SQLiteTaskStore
from src.wecom.task_service import TaskService
from src.wecom.word_runner import WordResult


class ProposalRunner:
    calls: list[object]
    def __init__(self): self.calls = []
    def run(self, request, output_dir):
        self.calls.append(request); output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "proposal.md"; path.write_text("# proposal\n", encoding="utf-8"); return path


class WordRunner:
    def __init__(self): self.calls = []
    def run(self, approved_md_path, output_dir, task_id):
        self.calls.append((approved_md_path, output_dir, task_id))
        output = output_dir / "client_delivery_editorial_v2" / "proposal.docx"; output.parent.mkdir(parents=True, exist_ok=True)
        Document().save(output); return WordResult(output)


class Transport:
    def __init__(self): self.replies, self.sent, self.files = [], [], []
    async def reply_text(self, _frame, text): self.replies.append(text)
    async def send_text(self, chatid, text): self.sent.append((chatid, text))
    async def send_file(self, chatid, path): self.files.append((chatid, path))


def harness(tmp_path: Path):
    store = SQLiteTaskStore(tmp_path / "wecom.sqlite")
    proposal, word = ProposalRunner(), WordRunner()
    tasks = TaskService(store, proposal, output_root=tmp_path / "tasks")
    artifacts = ArtifactService(store, word, task_output_root=tmp_path / "tasks", output_root=tmp_path / "artifacts")
    controls = TaskControlService(store, artifacts)
    transport = Transport(); adapter = WeComAgentAdapter(tasks, transport, artifacts, controls)
    return store, proposal, word, tasks, artifacts, controls, transport, adapter


def approved_task(parts, *, userid="u1", chatid="c1"):
    store, proposal, _word, tasks, _artifacts, *_ = parts
    task, _ = tasks.receive(IncomingMessage(userid, chatid, "医院配电改造方案，重点包括供电可靠性和智能巡检"))
    tasks.wait_for_idle(); approved, _ = tasks.approve(task.task_id)
    proposal.calls.clear()
    return store.get(approved.task_id)


def add_job(parts, task, *, status=ArtifactJobStatus.DELIVERED, path=None):
    store, *_rest, artifacts, _controls, _transport, _adapter = parts
    output = Path(path or (Path(task.approved_md_path).parent.parent.parent / "artifacts" / task.task_id / "job1" / "proposal.docx"))
    output.parent.mkdir(parents=True, exist_ok=True)
    if path is None: Document().save(output)
    now = "2026-09-18T10:00:00+08:00"
    job = ArtifactJob("abcdef123456", task.task_id, ArtifactType.WORD, status, task.approved_md_path, "digest", str(output.parent), str(output), None, None, None, now, now, now if status is ArtifactJobStatus.DELIVERED else None)
    store.create_artifact_job(job)
    return job


def close(parts):
    store, _proposal, _word, tasks, artifacts, *_ = parts
    artifacts.shutdown(); tasks.shutdown(); store.close()


def test_help_is_explicit_zero_side_effect_and_deduplicated(tmp_path):
    parts = harness(tmp_path); store, proposal, word, _tasks, _artifacts, _controls, transport, adapter = parts
    try:
        async def scenario():
            await adapter.handle(IncomingMessage("u1", "c1", "帮助", "help-1"), object())
            await adapter.handle(IncomingMessage("u1", "c1", "帮助", "help-1"), object())
            await adapter.handle(IncomingMessage("u1", "c1", "help", "help-2"), object())
        asyncio.run(scenario())
        assert len(transport.replies) == 2 and "生成Word" in transport.replies[0]
        assert store.tasks_for("u1", "c1") == [] and proposal.calls == [] and word.calls == []
    finally: close(parts)


def test_status_maps_task_and_word_without_side_effects(tmp_path):
    parts = harness(tmp_path); store, proposal, word, _tasks, _artifacts, _controls, transport, adapter = parts
    try:
        task = approved_task(parts); add_job(parts, task)
        before = store.get(task.task_id)
        asyncio.run(adapter.handle(IncomingMessage("u1", "c1", f"状态 {task.task_id}", "status-1"), object()))
        assert "Markdown：已确认" in transport.replies[-1] and "Word：已生成" in transport.replies[-1]
        assert store.get(task.task_id) == before and proposal.calls == [] and word.calls == [] and not transport.files
    finally: close(parts)


def test_status_isolated_by_user_and_chat_and_no_task_is_safe(tmp_path):
    parts = harness(tmp_path); _store, _proposal, _word, _tasks, _artifacts, _controls, transport, adapter = parts
    try:
        task = approved_task(parts)
        async def scenario():
            await adapter.handle(IncomingMessage("u2", "c1", f"状态 {task.task_id}", "s1"), object())
            await adapter.handle(IncomingMessage("u1", "c2", f"状态 {task.task_id}", "s2"), object())
            await adapter.handle(IncomingMessage("u3", "c3", "任务状态", "s3"), object())
        asyncio.run(scenario())
        assert all("不存在" in text or "没有" in text or "未找到" in text for text in transport.replies[-3:])
    finally: close(parts)


def test_status_maps_all_visible_states_and_invalid_ids_do_not_create_tasks(tmp_path):
    parts = harness(tmp_path); store, proposal, word, tasks, _artifacts, _controls, transport, adapter = parts
    try:
        task = approved_task(parts)
        expected = {
            TaskStatus.CLARIFYING: "待补充需求", TaskStatus.GENERATING_MD: "正在生成",
            TaskStatus.WAITING_MD_APPROVAL: "待确认", TaskStatus.MD_APPROVED: "已确认",
        }
        for number, (status, label) in enumerate(expected.items()):
            store.update(replace(task, status=status, updated_at=f"2026-09-18T10:0{number}:00+08:00"))
            asyncio.run(adapter.handle(IncomingMessage("u1", "c1", "状态", f"state-{number}"), object()))
            assert f"Markdown：{label}" in transport.replies[-1]
        count = len(store.tasks_for("u1", "c1"))
        asyncio.run(adapter.handle(IncomingMessage("u1", "c1", "状态 not-a-task", "bad-1"), object()))
        assert "格式不正确" in transport.replies[-1] and len(store.tasks_for("u1", "c1")) == count
        assert proposal.calls == [] and word.calls == []
    finally: close(parts)


def test_resend_existing_docx_never_starts_runner_or_creates_job_and_deduplicates(tmp_path):
    parts = harness(tmp_path); store, proposal, word, _tasks, _artifacts, _controls, transport, adapter = parts
    try:
        task = approved_task(parts); add_job(parts, task)
        async def scenario():
            await adapter.handle(IncomingMessage("u1", "c1", "重发Word", "resend-1"), object())
            await adapter.handle(IncomingMessage("u1", "c1", "重发Word", "resend-1"), object())
        asyncio.run(scenario())
        assert len(transport.files) == 1 and len(store.artifact_jobs_for(task.task_id)) == 1
        assert proposal.calls == [] and word.calls == []
    finally: close(parts)


def test_resend_rejects_missing_or_outside_docx_and_handles_historical_success(tmp_path):
    parts = harness(tmp_path); store, proposal, word, _tasks, _artifacts, _controls, transport, adapter = parts
    try:
        task = approved_task(parts); job = add_job(parts, task)
        store.update_artifact_job(replace(job, primary_artifact_path=str(tmp_path / "outside.docx")))
        asyncio.run(adapter.handle(IncomingMessage("u1", "c1", f"重发Word {task.task_id}", "resend-1"), object()))
        assert "重新生成Word" in transport.replies[-1] and not transport.files
        store.update_artifact_job(job)
        failed = replace(job, job_id="bbbbbb123456", status=ArtifactJobStatus.FAILED, created_at="2026-09-18T11:00:00+08:00")
        store.create_artifact_job(failed)
        asyncio.run(adapter.handle(IncomingMessage("u1", "c1", "重发Word", "resend-2"), object()))
        assert len(transport.files) == 1 and proposal.calls == [] and word.calls == []
    finally: close(parts)


def test_status_maps_word_queue_running_and_failure_without_starting_runner(tmp_path):
    parts = harness(tmp_path); store, proposal, word, _tasks, _artifacts, _controls, transport, adapter = parts
    try:
        task = approved_task(parts); job = add_job(parts, task, status=ArtifactJobStatus.QUEUED)
        for number, (status, label) in enumerate(((ArtifactJobStatus.QUEUED, "已进入队列"), (ArtifactJobStatus.RUNNING, "正在生成"), (ArtifactJobStatus.FAILED, "生成失败"))):
            store.update_artifact_job(replace(job, status=status, updated_at=f"2026-09-18T12:0{number}:00+08:00"))
            asyncio.run(adapter.handle(IncomingMessage("u1", "c1", "状态", f"word-status-{number}"), object()))
            assert f"Word：{label}" in transport.replies[-1]
        assert proposal.calls == [] and word.calls == []
    finally: close(parts)


def test_resend_rejects_other_user_and_chat_without_sending(tmp_path):
    parts = harness(tmp_path); _store, _proposal, _word, _tasks, _artifacts, _controls, transport, adapter = parts
    try:
        task = approved_task(parts); add_job(parts, task)
        async def scenario():
            await adapter.handle(IncomingMessage("u2", "c1", f"重发Word {task.task_id}", "other-user"), object())
            await adapter.handle(IncomingMessage("u1", "c2", f"重发Word {task.task_id}", "other-chat"), object())
        asyncio.run(scenario())
        assert not transport.files and all("未找到" in reply for reply in transport.replies)
    finally: close(parts)


def test_resend_control_msgid_stays_deduplicated_after_sqlite_reopen(tmp_path):
    parts = harness(tmp_path); store, _proposal, _word, tasks, artifacts, _controls, transport, adapter = parts
    try:
        task = approved_task(parts); add_job(parts, task)
        asyncio.run(adapter.handle(IncomingMessage("u1", "c1", "重发Word", "persist-1"), object()))
        assert len(transport.files) == 1
        artifacts.shutdown(); tasks.shutdown(); store.close()
        reopened = SQLiteTaskStore(tmp_path / "wecom.sqlite")
        fresh_tasks = TaskService(reopened, ProposalRunner(), output_root=tmp_path / "tasks")
        fresh_artifacts = ArtifactService(reopened, WordRunner(), task_output_root=tmp_path / "tasks", output_root=tmp_path / "artifacts")
        fresh_transport = Transport(); fresh_adapter = WeComAgentAdapter(fresh_tasks, fresh_transport, fresh_artifacts, TaskControlService(reopened, fresh_artifacts))
        asyncio.run(fresh_adapter.handle(IncomingMessage("u1", "c1", "重发Word", "persist-1"), object()))
        assert not fresh_transport.files and not fresh_transport.replies
        fresh_artifacts.shutdown(); fresh_tasks.shutdown(); reopened.close()
    except Exception:
        # The fixture may already have closed the resources on a failed assertion.
        raise


def test_control_words_inside_normal_requests_are_not_commands(tmp_path):
    parts = harness(tmp_path); store, proposal, _word, tasks, _artifacts, _controls, _transport, adapter = parts
    try:
        asyncio.run(adapter.handle(IncomingMessage("u1", "c1", "请帮助我生成医院配电方案", "normal-1"), object()))
        assert store.tasks_for("u1", "c1") and not proposal.calls
        tasks.wait_for_idle()
    finally: close(parts)
