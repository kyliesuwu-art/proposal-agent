from __future__ import annotations

import asyncio
import hashlib
import threading
from pathlib import Path

from docx import Document

from src.wecom.adapter import IncomingMessage, WeComAgentAdapter
from src.wecom.artifact_service import ArtifactService
from src.wecom.models import ArtifactJobStatus, ArtifactType, TaskStatus
from src.wecom.store import SQLiteTaskStore
from src.wecom.task_service import TaskService
from src.wecom.word_runner import WordResult


class ProposalRunner:
    def run(self, _request: str, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "proposal.md"
        path.write_text("# 已确认方案\n\n## 实施前确认项\n负荷【待确认】\n", encoding="utf-8")
        return path


class RecordingWordRunner:
    def __init__(self, *, fail: bool = False, block: bool = False) -> None:
        self.fail, self.block, self.calls = fail, block, []
        self.started, self.release = threading.Event(), threading.Event()

    def run(self, approved_md_path: Path, output_dir: Path, task_id: str) -> WordResult:
        self.calls.append((approved_md_path, output_dir, task_id)); self.started.set()
        if self.block:
            self.release.wait(timeout=2)
        if self.fail:
            raise RuntimeError("private renderer detail")
        document = output_dir / "client_delivery_editorial_v2" / "proposal_client_delivery_editorial_v2.docx"
        document.parent.mkdir(parents=True, exist_ok=True)
        doc = Document(); doc.add_heading("交付方案", 1); doc.save(document)
        return WordResult(document)


class Transport:
    def __init__(self, *, fail_file: bool = False) -> None:
        self.replies, self.sent, self.files, self.fail_file = [], [], [], fail_file

    async def reply_text(self, _frame, text: str) -> None: self.replies.append(text)
    async def send_text(self, chatid: str, text: str) -> None: self.sent.append((chatid, text))
    async def send_file(self, chatid: str, path: Path) -> None:
        if self.fail_file: raise RuntimeError("media failed")
        self.files.append((chatid, path))


def approved_harness(tmp_path: Path, *, runner: RecordingWordRunner | None = None):
    store = SQLiteTaskStore(tmp_path / "wecom.sqlite")
    tasks = TaskService(store, ProposalRunner(), output_root=tmp_path / "tasks")
    task, _ = tasks.receive(IncomingMessage("u1", "c1", "医院供电改造方案，重点包括配电数字化和故障预警"))
    tasks.wait_for_idle(); approved, _ = tasks.approve(task.task_id)
    word = runner or RecordingWordRunner()
    artifacts = ArtifactService(store, word, task_output_root=tmp_path / "tasks", output_root=tmp_path / "word-jobs")
    transport = Transport(); adapter = WeComAgentAdapter(tasks, transport, artifacts)
    return store, tasks, artifacts, word, transport, adapter, approved


def close(harness) -> None:
    store, tasks, artifacts, *_ = harness
    artifacts.shutdown(); tasks.shutdown(); store.close()


def test_approved_task_word_uses_approved_master_and_delivers(tmp_path: Path):
    harness = approved_harness(tmp_path)
    store, _tasks, artifacts, runner, transport, adapter, task = harness
    try:
        before = hashlib.sha256(Path(task.approved_md_path).read_bytes()).hexdigest()
        async def scenario():
            await adapter.handle(IncomingMessage("u1", "c1", "生成 Word", "word-1"), object())
            assert "Word 文档已开始生成" in transport.replies[-1]
            await asyncio.to_thread(artifacts.wait_for_idle); await asyncio.sleep(0)
        asyncio.run(scenario())
        jobs = store.artifact_jobs_for(task.task_id, ArtifactType.WORD)
        assert len(jobs) == 1 and jobs[0].status is ArtifactJobStatus.DELIVERED
        assert runner.calls[0][0] == Path(task.approved_md_path)
        assert Path(jobs[0].primary_artifact_path).is_file() and len(transport.files) == 1
        assert hashlib.sha256(Path(task.approved_md_path).read_bytes()).hexdigest() == before
        assert store.get(task.task_id).status is TaskStatus.MD_APPROVED
    finally: close(harness)


def test_word_ack_precedes_background_completion_and_duplicate_does_not_rerun(tmp_path: Path):
    word = RecordingWordRunner(block=True); harness = approved_harness(tmp_path, runner=word)
    store, _tasks, artifacts, _runner, transport, adapter, task = harness
    try:
        async def scenario():
            await adapter.handle(IncomingMessage("u1", "c1", "导出Word", "word-1"), object())
            assert "已开始生成" in transport.replies[-1] and word.started.wait(1)
            await adapter.handle(IncomingMessage("u1", "c1", "生成Word", "word-2"), object())
            assert "正在生成" in transport.replies[-1]
            word.release.set(); await asyncio.to_thread(artifacts.wait_for_idle); await asyncio.sleep(0)
        asyncio.run(scenario())
        assert len(word.calls) == 1 and len(store.artifact_jobs_for(task.task_id)) == 1
    finally: close(harness)


def test_unapproved_or_unsafe_master_cannot_start_word(tmp_path: Path):
    store = SQLiteTaskStore(tmp_path / "wecom.sqlite")
    tasks = TaskService(store, ProposalRunner(), output_root=tmp_path / "tasks")
    word = RecordingWordRunner(); artifacts = ArtifactService(store, word, task_output_root=tmp_path / "tasks", output_root=tmp_path / "jobs")
    try:
        response = artifacts.receive(IncomingMessage("u1", "c1", "生成Word", "m1"))[1]
        assert "请先确认" in response and not word.calls
    finally: artifacts.shutdown(); tasks.shutdown(); store.close()


def test_delivery_failure_and_restart_keep_content_approved_without_repeat(tmp_path: Path):
    harness = approved_harness(tmp_path)
    store, tasks, artifacts, _runner, transport, adapter, task = harness
    transport.fail_file = True
    try:
        async def scenario():
            await adapter.handle(IncomingMessage("u1", "c1", "生成Word", "word-1"), object())
            await asyncio.to_thread(artifacts.wait_for_idle); await asyncio.sleep(0)
        asyncio.run(scenario())
        assert store.artifact_jobs_for(task.task_id)[0].status is ArtifactJobStatus.FAILED
        assert store.get(task.task_id).status is TaskStatus.MD_APPROVED
        artifacts.shutdown()
        reopened = ArtifactService(store, RecordingWordRunner(), task_output_root=tmp_path / "tasks", output_root=tmp_path / "word-jobs")
        reopened.shutdown()
        assert store.artifact_jobs_for(task.task_id)[0].status is ArtifactJobStatus.FAILED
    finally:
        tasks.shutdown(); store.close()
