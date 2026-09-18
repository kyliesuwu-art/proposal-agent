from __future__ import annotations

import json
import shutil
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from src.wecom.models import AgentEvent, TaskStatus, WeComTask
from src.wecom.proposal_runner import ProposalRunner
from src.wecom.store import SQLiteTaskStore


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _is_confirmation(text: str) -> bool:
    return text.strip().lower() in {"确认", "没问题", "继续", "确认继续", "ok", "okay"}


def is_clear_request(text: str) -> bool:
    """V1 rule: enough concrete topic plus goal/focus, never an LLM judgement."""
    compact = "".join(text.split())
    if len(compact) < 18:
        return False
    topic_markers = ("方案", "改造", "建设", "项目", "园区", "医院", "工厂", "配电", "储能", "能源")
    focus_markers = ("重点", "包括", "解决", "目标", "预警", "巡检", "管理", "数字化", "可靠")
    return any(marker in compact for marker in topic_markers) and any(marker in compact for marker in focus_markers)


class TaskService:
    def __init__(self, store: SQLiteTaskStore, runner: ProposalRunner, *, output_root: Path = Path("outputs/wecom_tasks"), generation_enabled: bool = True) -> None:
        self._store, self._runner, self._output_root = store, runner, output_root
        self._generation_enabled = generation_enabled
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="wecom-proposal")
        self._futures: set[Future[None]] = set()
        self._future_lock = threading.Lock()
        self._subscribers: list[Callable[[AgentEvent], None]] = []
        self._recover_interrupted_generations()

    def _recover_interrupted_generations(self) -> None:
        """A process restart cannot safely resume an unknown proposal subprocess."""
        for task in self._store.tasks_with_status(TaskStatus.GENERATING_MD):
            self._save(replace(
                task,
                status=TaskStatus.FAILED,
                error_stage="interrupted_restart",
                error_message="proposal generation interrupted by restart",
                updated_at=_now(),
            ))

    def subscribe(self, listener: Callable[[AgentEvent], None]) -> None:
        self._subscribers.append(listener)

    def _emit(self, name: str, task: WeComTask) -> None:
        event = AgentEvent(name, task)
        for listener in tuple(self._subscribers):
            try:
                listener(event)
            except Exception:
                # A delivery failure must never roll back a persisted task.
                pass

    def _write_state(self, task: WeComTask) -> None:
        directory = self._output_root / task.task_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "task_state.json").write_text(json.dumps(task.payload(), ensure_ascii=False, indent=2), encoding="utf-8")

    def _save(self, task: WeComTask) -> WeComTask:
        task = self._store.update(task)
        self._write_state(task)
        return task

    def _new_task(self, message, status: TaskStatus, *, original: str, clarified: str | None = None) -> WeComTask:
        now = _now()
        task = WeComTask(uuid.uuid4().hex[:12], message.userid, message.chatid, original, clarified, status, now, now)
        self._store.create(task, message_id=message.message_id)
        directory = self._output_root / task.task_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "request.json").write_text(json.dumps({"task_id": task.task_id, "request": clarified or original}, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_state(task)
        return task

    def receive(self, message) -> tuple[WeComTask | None, str]:
        duplicate = self._store.task_for_message(message.message_id)
        if duplicate:
            return duplicate, f"该消息已处理。任务编号：{duplicate.task_id}"
        text = message.content.strip()
        if _is_confirmation(text):
            waiting = self._store.tasks_for(message.userid, message.chatid, statuses=(TaskStatus.WAITING_MD_APPROVAL,))
            if len(waiting) != 1:
                return None, "当前有多个或没有等待确认的方案，请提供需要确认的任务编号。"
            task, response = self.approve(waiting[0].task_id)
            self._store.record_message(message.message_id, task.task_id)
            return task, response
        clarifying = self._store.tasks_for(message.userid, message.chatid, statuses=(TaskStatus.CLARIFYING,))
        if clarifying and is_clear_request(text):
            task = clarifying[-1]
            if not self._generation_enabled:
                return task, "当前为企业微信联调模式，已收到补充信息；本轮不会生成方案。"
            request = f"{task.original_request}\n补充信息：{text}"
            task = replace(task, clarified_request=request, status=TaskStatus.READY, updated_at=_now())
            task = self._save(task)
            return self._start(task)
        if not is_clear_request(text):
            task = self._new_task(message, TaskStatus.CLARIFYING, original=text)
            self._emit("TASK_CREATED", task)
            return task, "为了生成可用方案，请补充：项目/应用场景、主要目标，以及 1–3 个重点关注内容。"
        if not self._generation_enabled:
            task = self._new_task(message, TaskStatus.CLARIFYING, original=text)
            self._emit("TASK_CREATED", task)
            return task, "当前为企业微信联调模式，已收到需求；本轮不会生成方案。"
        task = self._new_task(message, TaskStatus.READY, original=text, clarified=text)
        self._emit("TASK_CREATED", task)
        return self._start(task)

    def _start(self, task: WeComTask) -> tuple[WeComTask, str]:
        task = self._save(replace(task, status=TaskStatus.GENERATING_MD, updated_at=_now()))
        self._emit("MD_GENERATION_STARTED", task)
        future = self._executor.submit(self._generate, task.task_id)
        with self._future_lock:
            self._futures.add(future)
        future.add_done_callback(lambda done: self._futures.discard(done))
        return task, f"需求已收到，我正在生成方案。任务编号：{task.task_id}"

    def _generate(self, task_id: str) -> None:
        task = self._store.get(task_id)
        directory = self._output_root / task.task_id
        try:
            proposal = self._runner.run(task.clarified_request or task.original_request, directory)
            completed = self._save(replace(task, status=TaskStatus.WAITING_MD_APPROVAL, proposal_md_path=str(proposal), updated_at=_now()))
            self._emit("MD_GENERATION_COMPLETED", completed)
        except Exception:
            failed = self._save(replace(task, status=TaskStatus.FAILED, error_stage="proposal_generation", error_message="proposal generation failed", updated_at=_now()))
            self._emit("MD_GENERATION_FAILED", failed)

    def approve(self, task_id: str) -> tuple[WeComTask, str]:
        task = self._store.get(task_id)
        if task.status is not TaskStatus.WAITING_MD_APPROVAL or not task.proposal_md_path:
            raise ValueError("task is not waiting for Markdown approval")
        approved = self._output_root / task.task_id / "approved.md"
        shutil.copyfile(task.proposal_md_path, approved)
        saved = self._save(replace(task, status=TaskStatus.MD_APPROVED, approved_md_path=str(approved), updated_at=_now()))
        self._emit("MD_APPROVED", saved)
        return saved, f"Markdown 内容已确认。如需生成 Word，请回复“生成Word”。任务编号：{saved.task_id}"

    def approve_uploaded_markdown(
        self,
        userid: str,
        chatid: str,
        filename: str,
        content: bytes,
        *,
        message_id: str | None = None,
    ) -> tuple[WeComTask | None, str]:
        """Accept only a Markdown upload for the one waiting task in this conversation."""
        duplicate = self._store.task_for_message(message_id)
        if duplicate:
            return duplicate, f"该消息已处理。任务编号：{duplicate.task_id}"
        if not filename.lower().endswith((".md", ".markdown")):
            return None, "请上传 Markdown（.md）文件后再确认。"
        waiting = self._store.tasks_for(userid, chatid, statuses=(TaskStatus.WAITING_MD_APPROVAL,))
        if len(waiting) != 1:
            return None, "当前有多个或没有等待确认的方案，请先提供需要确认的任务编号。"
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return None, "上传的 Markdown 不是 UTF-8 编码，请转换后重试。"
        task = waiting[0]
        approved = self._output_root / task.task_id / "approved.md"
        approved.write_text(text, encoding="utf-8")
        saved = self._save(replace(task, status=TaskStatus.MD_APPROVED, approved_md_path=str(approved), updated_at=_now()))
        self._store.record_message(message_id, saved.task_id)
        self._emit("MD_APPROVED", saved)
        return saved, f"已保存你修改后的 Markdown。任务编号：{saved.task_id}"

    def wait_for_idle(self) -> None:
        while True:
            with self._future_lock:
                futures = tuple(self._futures)
            if not futures:
                return
            for future in futures:
                future.result()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)
