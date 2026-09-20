from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from src.wecom.artifact_service import ArtifactService
from src.wecom.models import ArtifactJob, ArtifactJobStatus, ArtifactType, TaskStatus, WeComTask
from src.wecom.store import SQLiteTaskStore


TASK_ID_RE = r"[0-9a-f]{12}"
_STATUS_RE = re.compile(rf"^(?:状态|任务状态)(?:\s+({TASK_ID_RE}))?$", re.IGNORECASE)
_RESEND_RE = re.compile(rf"^(?:重发|重新发送)word(?:\s+({TASK_ID_RE}))?$", re.IGNORECASE)
_PPT_RESEND_RE = re.compile(rf"^(?:重发|重新发送)ppt(?:\s+({TASK_ID_RE}))?$", re.IGNORECASE)
_STATUS_PREFIX_RE = re.compile(r"^(?:状态|任务状态)(?:\s+\S+)?$", re.IGNORECASE)
_RESEND_PREFIX_RE = re.compile(r"^(?:重发|重新发送)word(?:\s+\S+)?$", re.IGNORECASE)
_HELP_RE = re.compile(r"^(?:帮助|help)$", re.IGNORECASE)
_MAX_WORD_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class ControlResult:
    response: str | None
    send_path: Path | None = None


class TaskControlService:
    """Explicit, read-only task controls ahead of ordinary request handling."""

    def __init__(self, store: SQLiteTaskStore, artifacts: ArtifactService) -> None:
        self._store, self._artifacts = store, artifacts

    @staticmethod
    def _normalized(text: str) -> str:
        return text.strip()

    def receive(self, message) -> ControlResult | None:
        text = self._normalized(message.content)
        command_name: str | None = None
        invalid = False
        if _HELP_RE.fullmatch(text):
            command_name = "help"
        elif _STATUS_RE.fullmatch(text):
            command_name = "status"
        elif _STATUS_PREFIX_RE.fullmatch(text):
            command_name, invalid = "status", True
        elif _RESEND_RE.fullmatch(text):
            command_name = "resend_word"
        elif _PPT_RESEND_RE.fullmatch(text):
            command_name = "resend_ppt"
        elif _RESEND_PREFIX_RE.fullmatch(text):
            command_name, invalid = "resend_word", True
        if command_name is None:
            return None
        if self._store.control_message_seen(message.message_id):
            return ControlResult(None)
        self._store.record_control_message(message.message_id, command_name)
        if invalid:
            return ControlResult("任务编号格式不正确。")
        if command_name == "help":
            return ControlResult("当前支持：发送方案需求、确认、上传 Markdown、生成Word、重新生成Word、重发Word、生成PPT、重新生成PPT、重发PPT、状态。")
        if command_name == "status":
            return ControlResult(self._status(message, _STATUS_RE.fullmatch(text).group(1)))
        if command_name == "resend_ppt":
            return self._resend(message, _PPT_RESEND_RE.fullmatch(text).group(1), ArtifactType.PPTX)
        return self._resend(message, _RESEND_RE.fullmatch(text).group(1), ArtifactType.WORD)

    def _owned_task(self, userid: str, chatid: str, task_id: str | None) -> WeComTask | None:
        tasks = self._store.tasks_for(userid, chatid)
        if task_id:
            return next((task for task in tasks if task.task_id == task_id), None)
        return tasks[-1] if tasks else None

    @staticmethod
    def _markdown_text(status: TaskStatus) -> str:
        return {
            TaskStatus.CLARIFYING: "待补充需求",
            TaskStatus.READY: "准备生成",
            TaskStatus.GENERATING_MD: "正在生成",
            TaskStatus.WAITING_MD_APPROVAL: "待确认",
            TaskStatus.MD_APPROVED: "已确认",
            TaskStatus.FAILED: "生成失败",
            TaskStatus.NEW: "新建",
        }[status]

    def _word_state(self, task: WeComTask) -> tuple[str, str]:
        jobs = self._store.artifact_jobs_for(task.task_id, ArtifactType.WORD)
        if not jobs:
            return "未生成", "发送“生成Word”可生成文件" if task.status is TaskStatus.MD_APPROVED else "请先完成 Markdown 确认"
        latest = jobs[-1]
        if latest.status is ArtifactJobStatus.QUEUED:
            return "已进入队列", "请等待 Word 生成"
        if latest.status is ArtifactJobStatus.RUNNING:
            return "正在生成", "请等待 Word 生成"
        if latest.status in {ArtifactJobStatus.READY, ArtifactJobStatus.DELIVERED}:
            return "已生成", "发送“重发Word”可再次获取文件"
        return "生成失败", "发送“重新生成Word”可创建新的生成任务"

    def _status(self, message, task_id: str | None) -> str:
        task = self._owned_task(message.userid, message.chatid, task_id)
        if task is None:
            return "当前没有可查询的方案任务。请直接发送方案需求开始。" if task_id is None else "未找到可访问的任务。"
        word, next_step = self._word_state(task)
        ppt_jobs = self._store.artifact_jobs_for(task.task_id, ArtifactType.PPTX)
        ppt = "未生成" if not ppt_jobs else {ArtifactJobStatus.QUEUED: "已进入队列", ArtifactJobStatus.RUNNING: "正在生成", ArtifactJobStatus.FAILED: "生成失败"}.get(ppt_jobs[-1].status, "已生成")
        return f"任务编号：{task.task_id}\nMarkdown：{self._markdown_text(task.status)}\nWord：{word}\nPPT：{ppt}\n更新时间：{task.updated_at}\n下一步：{next_step}"

    def _safe_word_file(self, task: WeComTask, job: ArtifactJob) -> Path | None:
        if job.artifact_type is not ArtifactType.WORD or job.status not in {ArtifactJobStatus.READY, ArtifactJobStatus.DELIVERED} or not job.primary_artifact_path:
            return None
        root = Path(job.output_dir).resolve()
        path = Path(job.primary_artifact_path)
        if path.is_symlink():
            return None
        resolved = path.resolve()
        output_root = self._artifacts.output_root
        if output_root not in root.parents or task.task_id not in root.parts or root not in resolved.parents:
            return None
        if not resolved.is_file() or resolved.suffix.lower() != ".docx" or resolved.stat().st_size <= 0 or resolved.stat().st_size > _MAX_WORD_BYTES:
            return None
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.docx", resolved.name):
            return None
        return resolved

    def _resend(self, message, task_id: str | None, artifact_type: ArtifactType = ArtifactType.WORD) -> ControlResult:
        task = self._owned_task(message.userid, message.chatid, task_id)
        if task is None:
            return ControlResult("未找到可访问的任务。")
        jobs = self._store.artifact_jobs_for(task.task_id, artifact_type)
        successful = [job for job in jobs if job.status in {ArtifactJobStatus.READY, ArtifactJobStatus.DELIVERED}]
        if successful:
            path = self._safe_file(task, successful[-1], artifact_type)
            if path:
                return ControlResult(f"正在重新发送已生成的 Word 文件。\n任务编号：{task.task_id}", path)
            return ControlResult(f"已找到成功记录，但文件当前不可用。请发送“重新生成{'PPT' if artifact_type is ArtifactType.PPTX else 'Word'}”创建新的生成任务。")
        if not jobs:
            label = "PPT" if artifact_type is ArtifactType.PPTX else "Word"; return ControlResult(f"当前没有已生成的 {label} 文件。请发送“生成{label}”。")
        latest = jobs[-1]
        if latest.status in {ArtifactJobStatus.QUEUED, ArtifactJobStatus.RUNNING}:
            return ControlResult("Word 文档正在生成，请等待完成。")
        label = "PPT" if artifact_type is ArtifactType.PPTX else "Word"; return ControlResult(f"最近一次 {label} 生成失败。请发送“重新生成{label}”创建新的生成任务。")

    def _safe_file(self, task: WeComTask, job: ArtifactJob, artifact_type: ArtifactType) -> Path | None:
        if artifact_type is ArtifactType.WORD:
            return self._safe_word_file(task, job)
        if job.artifact_type is not ArtifactType.PPTX or job.status not in {ArtifactJobStatus.READY, ArtifactJobStatus.DELIVERED} or not job.primary_artifact_path:
            return None
        root, path = Path(job.output_dir).resolve(), Path(job.primary_artifact_path)
        resolved = path.resolve()
        if path.is_symlink() or self._artifacts.output_root not in root.parents or task.task_id not in root.parts or root not in resolved.parents:
            return None
        if not resolved.is_file() or resolved.suffix.lower() != ".pptx" or resolved.stat().st_size <= 0 or resolved.stat().st_size > 50 * 1024 * 1024:
            return None
        return resolved
