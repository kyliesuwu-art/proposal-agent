from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading
import subprocess
import sys

import pytest

from src.wecom.adapter import IncomingMessage, WeComAgentAdapter
from src.wecom.models import TaskStatus
from src.wecom import proposal_runner
from src.wecom.proposal_runner import ExistingProposalCliRunner
from src.wecom.store import SQLiteTaskStore
from src.wecom.task_service import TaskService
from src import wecom_bot


class FakeRunner:
    def __init__(self, *, fail: bool = False, block: bool = False) -> None:
        self.fail, self.block = fail, block
        self.calls: list[tuple[str, Path]] = []
        self.started = threading.Event()
        self.release = threading.Event()

    def run(self, request: str, output_dir: Path) -> Path:
        self.calls.append((request, output_dir))
        self.started.set()
        if self.block:
            self.release.wait(timeout=2)
        if self.fail:
            raise RuntimeError("offline runner failed")
        path = output_dir / "proposal.md"
        path.write_text("# 测试方案\n", encoding="utf-8")
        return path


class FakeTransport:
    def __init__(self, *, fail_reply: bool = False) -> None:
        self.fail_reply = fail_reply
        self.replies: list[str] = []
        self.sent: list[tuple[str, str]] = []
        self.files: list[tuple[str, Path]] = []

    async def reply_text(self, _frame: object, text: str) -> None:
        if self.fail_reply:
            raise RuntimeError("transport unavailable")
        self.replies.append(text)

    async def send_text(self, chatid: str, text: str) -> None:
        self.sent.append((chatid, text))

    async def send_file(self, chatid: str, path: Path) -> None:
        self.files.append((chatid, path))


@pytest.fixture
def harness(tmp_path: Path):
    store = SQLiteTaskStore(tmp_path / "wecom.sqlite")
    runner = FakeRunner()
    service = TaskService(store, runner, output_root=tmp_path / "tasks")
    transport = FakeTransport()
    adapter = WeComAgentAdapter(service, transport)
    yield store, runner, service, transport, adapter
    service.shutdown()
    store.close()


def test_unclear_request_clarifies_without_starting_runner(harness):
    store, runner, _, transport, adapter = harness
    asyncio.run(adapter.handle(IncomingMessage("u1", "c1", "帮我做个方案"), object()))
    task = store.tasks_for("u1", "c1")[0]
    assert task.status is TaskStatus.CLARIFYING
    assert runner.calls == []
    assert "项目" in transport.replies[-1]


def test_controlled_live_mode_never_starts_runner_after_clarification(tmp_path: Path):
    store = SQLiteTaskStore(tmp_path / "wecom.sqlite")
    runner = FakeRunner()
    service = TaskService(store, runner, output_root=tmp_path / "tasks", generation_enabled=False)
    try:
        first, _ = service.receive(IncomingMessage("u1", "c1", "帮我做个方案", message_id="m1"))
        second, response = service.receive(IncomingMessage(
            "u1", "c1", "茂名医院光伏项目，重点现场状况、投资回报和实施方案", message_id="m2"
        ))
        assert first.status is TaskStatus.CLARIFYING
        assert second.task_id == first.task_id
        assert store.get(first.task_id).status is TaskStatus.CLARIFYING
        assert runner.calls == []
        assert "联调" in response
    finally:
        service.shutdown()
        store.close()


def test_production_mode_still_starts_runner(tmp_path: Path):
    store = SQLiteTaskStore(tmp_path / "wecom.sqlite")
    runner = FakeRunner()
    service = TaskService(store, runner, output_root=tmp_path / "tasks", generation_enabled=True)
    try:
        task, _ = service.receive(IncomingMessage("u1", "c1", "医院供电改造方案，重点故障预警和智能巡检"))
        service.wait_for_idle()
        assert store.get(task.task_id).status is TaskStatus.WAITING_MD_APPROVAL
        assert len(runner.calls) == 1
    finally:
        service.shutdown(); store.close()


def test_restart_marks_interrupted_generation_failed_without_rerunning(tmp_path: Path):
    store = SQLiteTaskStore(tmp_path / "wecom.sqlite")
    runner = FakeRunner()
    service = TaskService(store, runner, output_root=tmp_path / "tasks")
    task = service._new_task(IncomingMessage("u1", "c1", "request"), TaskStatus.GENERATING_MD, original="request")
    service.shutdown(); store.close()
    reopened = SQLiteTaskStore(tmp_path / "wecom.sqlite")
    recovered = TaskService(reopened, runner, output_root=tmp_path / "tasks", generation_enabled=False)
    try:
        saved = reopened.get(task.task_id)
        assert saved.status is TaskStatus.FAILED
        assert saved.error_stage == "interrupted_restart"
        assert runner.calls == []
    finally:
        recovered.shutdown(); reopened.close()


def test_clear_request_acks_then_starts_background_runner(harness):
    store, runner, service, transport, adapter = harness
    runner.block = True
    asyncio.run(adapter.handle(IncomingMessage("u1", "c1", "医院园区高可靠供电改造方案，重点是数字化配电和故障预警"), object()))
    assert "正在生成方案" in transport.replies[-1]
    assert runner.started.wait(1)
    task = store.tasks_for("u1", "c1")[0]
    assert task.status is TaskStatus.GENERATING_MD
    runner.release.set(); service.wait_for_idle()


def test_success_records_markdown_waits_for_approval_and_emits_event(harness):
    store, _, service, _, _ = harness
    events = []
    service.subscribe(events.append)
    task, _ = service.receive(IncomingMessage("u1", "c1", "医院供电改造方案，重点故障预警和智能巡检"))
    service.wait_for_idle()
    saved = store.get(task.task_id)
    assert saved.status is TaskStatus.WAITING_MD_APPROVAL
    assert Path(saved.proposal_md_path).is_file()
    assert "MD_GENERATION_COMPLETED" in [event.name for event in events]


def test_failure_records_safe_stage_and_event(harness):
    store, runner, service, _, _ = harness
    runner.fail = True
    events = []
    service.subscribe(events.append)
    task, _ = service.receive(IncomingMessage("u1", "c1", "医院供电改造方案，重点故障预警和智能巡检"))
    service.wait_for_idle()
    saved = store.get(task.task_id)
    assert saved.status is TaskStatus.FAILED
    assert saved.error_stage == "proposal_generation"
    assert "MD_GENERATION_FAILED" in [event.name for event in events]


def test_unique_waiting_task_is_approved(harness):
    store, _, service, _, _ = harness
    task, _ = service.receive(IncomingMessage("u1", "c1", "医院供电改造方案，重点故障预警和智能巡检"))
    service.wait_for_idle()
    approved, response = service.receive(IncomingMessage("u1", "c1", "确认"))
    saved = store.get(task.task_id)
    assert approved.task_id == task.task_id
    assert saved.status is TaskStatus.MD_APPROVED
    assert Path(saved.approved_md_path).read_text(encoding="utf-8") == "# 测试方案\n"
    assert "已确认" in response


def test_confirmation_message_id_is_deduplicated(harness):
    store, _, service, _, _ = harness
    task, _ = service.receive(IncomingMessage("u1", "c1", "医院供电改造方案，重点故障预警和智能巡检"))
    service.wait_for_idle()
    first, _ = service.receive(IncomingMessage("u1", "c1", "确认", message_id="confirm-1"))
    second, response = service.receive(IncomingMessage("u1", "c1", "确认", message_id="confirm-1"))
    assert first.task_id == task.task_id == second.task_id
    assert store.get(task.task_id).status is TaskStatus.MD_APPROVED
    assert store.task_for_message("confirm-1").task_id == task.task_id
    assert "已处理" in response


def test_two_users_do_not_share_tasks(harness):
    store, _, service, _, _ = harness
    one, _ = service.receive(IncomingMessage("u1", "c1", "医院供电改造方案，重点故障预警和智能巡检"))
    two, _ = service.receive(IncomingMessage("u2", "c2", "工厂储能建设方案，重点容量规划和安全运行"))
    service.wait_for_idle()
    assert one.task_id != two.task_id
    assert store.tasks_for("u1", "c1")[0].userid == "u1"
    assert store.tasks_for("u2", "c2")[0].userid == "u2"


def test_same_user_sequential_tasks_are_distinct_and_confirmation_is_ambiguous(harness):
    _, _, service, _, _ = harness
    first, _ = service.receive(IncomingMessage("u1", "c1", "医院供电改造方案，重点故障预警和智能巡检"))
    second, _ = service.receive(IncomingMessage("u1", "c1", "园区能源管理方案，重点能耗分析和平台建设"))
    service.wait_for_idle()
    task, response = service.receive(IncomingMessage("u1", "c1", "确认"))
    assert first.task_id != second.task_id
    assert task is None
    assert "任务编号" in response


def test_transport_exception_does_not_lose_persisted_task(harness):
    store, _, service, transport, adapter = harness
    transport.fail_reply = True
    with pytest.raises(RuntimeError, match="transport unavailable"):
        asyncio.run(adapter.handle(IncomingMessage("u1", "c1", "医院供电改造方案，重点故障预警和智能巡检"), object()))
    service.wait_for_idle()
    assert store.tasks_for("u1", "c1")[0].status is TaskStatus.WAITING_MD_APPROVAL


def test_duplicate_message_id_creates_only_one_task(harness):
    store, runner, service, _, _ = harness
    message = IncomingMessage("u1", "c1", "医院供电改造方案，重点故障预警和智能巡检", message_id="stable-1")
    first, _ = service.receive(message)
    second, _ = service.receive(message)
    service.wait_for_idle()
    assert first.task_id == second.task_id
    assert len(store.tasks_for("u1", "c1")) == 1
    assert len(runner.calls) == 1


def test_uploaded_markdown_replaces_master_for_unique_waiting_task(harness):
    store, _, service, _, _ = harness
    task, _ = service.receive(IncomingMessage("u1", "c1", "医院供电改造方案，重点故障预警和智能巡检"))
    service.wait_for_idle()
    uploaded, response = service.approve_uploaded_markdown("u1", "c1", "edited.md", "# 用户修订\n".encode("utf-8"))
    saved = store.get(task.task_id)
    assert uploaded.task_id == task.task_id
    assert saved.status is TaskStatus.MD_APPROVED
    assert Path(saved.approved_md_path).read_text(encoding="utf-8") == "# 用户修订\n"
    assert "已保存" in response


def test_uploaded_markdown_message_id_is_deduplicated(harness):
    store, _, service, _, _ = harness
    task, _ = service.receive(IncomingMessage("u1", "c1", "医院供电改造方案，重点故障预警和智能巡检"))
    service.wait_for_idle()
    first, _ = service.approve_uploaded_markdown("u1", "c1", "edited.md", b"# One\n", message_id="file-1")
    second, response = service.approve_uploaded_markdown("u1", "c1", "edited.md", b"# Two\n", message_id="file-1")
    assert first.task_id == task.task_id == second.task_id
    assert Path(store.get(task.task_id).approved_md_path).read_text(encoding="utf-8") == "# One\n"
    assert store.task_for_message("file-1").task_id == task.task_id
    assert "已处理" in response


def test_completion_event_sends_follow_up_text_and_markdown_file(harness):
    _, _, service, transport, adapter = harness

    async def scenario() -> None:
        await adapter.handle(IncomingMessage("u1", "c1", "医院供电改造方案，重点故障预警和智能巡检"), object())
        await asyncio.to_thread(service.wait_for_idle)
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert any("Markdown 已生成" in text for _, text in transport.sent)
    assert len(transport.files) == 1
    assert transport.files[0][1].name == "proposal.md"


def test_acceptance_script_is_directly_runnable_from_project_root():
    result = subprocess.run(
        [sys.executable, "scripts/run_wecom_v1_acceptance.py", "--help"],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0
    assert "--mode" in result.stdout


def test_wecom_bot_accepts_isolated_storage_paths():
    args = wecom_bot.parse_args([
        "--db-path", "runtime_data/live.sqlite",
        "--output-root", "outputs/live",
        "--proactive-markdown-chatid", "live-chat",
        "--proactive-markdown", "# test",
        "--proactive-file-chatid", "live-chat",
        "--proactive-file-path", "outputs/live/test.md",
    ])
    assert args.db_path == Path("runtime_data/live.sqlite")
    assert args.output_root == Path("outputs/live")
    assert args.proactive_markdown_chatid == "live-chat"
    assert args.proactive_markdown == "# test"
    assert args.proactive_file_chatid == "live-chat"
    assert args.proactive_file_path == Path("outputs/live/test.md")


def test_production_runner_persists_redacted_failure_diagnostics(tmp_path: Path, monkeypatch):
    secret = "sk-test-secret-123456"

    def failed_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["python", "src/main.py"], returncode=7,
            stdout="progress output", stderr=f"Authorization: Bearer {secret}\n{secret}",
        )

    monkeypatch.setattr(proposal_runner.subprocess, "run", failed_run)
    runner = ExistingProposalCliRunner(tmp_path)
    output_dir = tmp_path / "task"
    with pytest.raises(subprocess.CalledProcessError):
        runner.run("private request", output_dir)

    result = json.loads((output_dir / "runner_result.json").read_text(encoding="utf-8"))
    assert result["returncode"] == 7
    assert result["command_type"] == "proposal_cli"
    assert (output_dir / "runner.stdout.log").read_text(encoding="utf-8") == "progress output"
    stderr = (output_dir / "runner.stderr.log").read_text(encoding="utf-8")
    assert secret not in stderr
    assert "[REDACTED]" in stderr


def test_production_runner_success_still_returns_markdown_and_records_result(tmp_path: Path, monkeypatch):
    def successful_run(*args, **_kwargs):
        output = Path(args[0][args[0].index("--output") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("# Proposal\n", encoding="utf-8")
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(proposal_runner.subprocess, "run", successful_run)
    output = ExistingProposalCliRunner(tmp_path).run("private request", tmp_path / "task")
    assert output.read_text(encoding="utf-8") == "# Proposal\n"
    assert json.loads((tmp_path / "task" / "runner_result.json").read_text(encoding="utf-8"))["returncode"] == 0
