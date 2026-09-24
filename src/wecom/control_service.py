from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from src.wecom.artifact_service import ArtifactService, PptResumeRejected
from src.wecom.models import ArtifactJob, ArtifactJobStatus, ArtifactType, TaskStatus, WeComTask
from src.wecom.store import SQLiteTaskStore


TASK_ID_RE = r"[0-9a-f]{12}"
_STATUS_RE = re.compile(rf"^(?:状态|任务状态)(?:\s+({TASK_ID_RE}))?$", re.IGNORECASE)
_RESEND_RE = re.compile(rf"^(?:重发|重新发送)word(?:\s+({TASK_ID_RE}))?$", re.IGNORECASE)
_PPT_RESEND_RE = re.compile(rf"^(?:重发|重新发送)ppt(?:\s+({TASK_ID_RE}))?$", re.IGNORECASE)
_PPT_RESUME_RE = re.compile(rf"^\u7ee7\u7eed\u751f\u6210PPT +({TASK_ID_RE})$")
_PPT_RESUME_PREFIX_RE = re.compile(r"^\u7ee7\u7eed\u751f\u6210(?:PPT|ppt)(?: +.*)?$")
_PPT_ARTIFACT_INVALID_RE = re.compile(r"^(?:\u65b0\u751f\u6210|\u7ee7\u7eed\u505a)(?:PPT|ppt)(?: +.*)?$")
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
        elif _PPT_RESUME_RE.fullmatch(text):
            command_name = "resume_ppt"
        elif _PPT_RESUME_PREFIX_RE.fullmatch(text):
            command_name, invalid = "resume_ppt", True
        elif _PPT_ARTIFACT_INVALID_RE.fullmatch(text):
            command_name, invalid = "invalid_ppt_artifact", True
        elif _RESEND_PREFIX_RE.fullmatch(text):
            command_name, invalid = "resend_word", True
        if command_name is None:
            return None
        if self._store.control_message_seen(message.message_id):
            return ControlResult(None)
        self._store.record_control_message(message.message_id, command_name)
        if invalid:
            if command_name == "invalid_ppt_artifact":
                return ControlResult("\u672a\u8bc6\u522b\u8be5 PPT \u547d\u4ee4\u3002\u7ee7\u7eed\u65ad\u70b9\u751f\u6210\u8bf7\u53d1\u9001\uff1a\n\u7ee7\u7eed\u751f\u6210PPT <\u4efb\u52a1\u7f16\u53f7>")
            if command_name == "resume_ppt":
                return ControlResult("\u7ee7\u7eed\u65ad\u70b9\u751f\u6210\u8bf7\u53d1\u9001\uff1a\n\u7ee7\u7eed\u751f\u6210PPT <\u4efb\u52a1\u7f16\u53f7>")
            return ControlResult("\u4efb\u52a1\u7f16\u53f7\u683c\u5f0f\u4e0d\u6b63\u786e\u3002")
        if command_name == "help":
            return ControlResult("\u5f53\u524d\u652f\u6301\uff1a\u53d1\u9001\u65b9\u6848\u9700\u6c42\u3001\u786e\u8ba4\u3001\u4e0a\u4f20 Markdown\u3001\u751f\u6210Word\u3001\u91cd\u65b0\u751f\u6210Word\u3001\u91cd\u53d1Word\u3001\u751f\u6210PPT\u3001\u91cd\u65b0\u751f\u6210PPT\u3001\u91cd\u53d1PPT\u3001\u7ee7\u7eed\u751f\u6210PPT <\u4efb\u52a1\u7f16\u53f7>\uff08\u4ece\u4e0a\u6b21\u6210\u529f\u9875\u9762\u7ee7\u7eed\u751f\u6210\u5931\u8d25\u7684PPT\uff09\u3001\u72b6\u6001\u3002")
        if command_name == "status":
            return ControlResult(self._status(message, _STATUS_RE.fullmatch(text).group(1)))
        if command_name == "resend_ppt":
            return self._resend(message, _PPT_RESEND_RE.fullmatch(text).group(1), ArtifactType.PPTX)
        if command_name == "resume_ppt":
            return self._resume_ppt(message, _PPT_RESUME_RE.fullmatch(text).group(1))
        return self._resend(message, _RESEND_RE.fullmatch(text).group(1), ArtifactType.WORD)

    def _resume_ppt(self, message, task_id: str) -> ControlResult:
        task = self._owned_task(message.userid, message.chatid, task_id)
        if task is None:
            return ControlResult("\u672a\u627e\u5230\u53ef\u8bbf\u95ee\u7684\u4efb\u52a1\u3002")
        jobs = self._store.artifact_jobs_for(task.task_id, ArtifactType.PPTX)
        if any(job.status in {ArtifactJobStatus.QUEUED, ArtifactJobStatus.RUNNING} for job in jobs):
            return ControlResult(f"\u8be5\u4efb\u52a1\u5df2\u6709 PPT \u6b63\u5728\u751f\u6210\uff0c\u8bf7\u52ff\u91cd\u590d\u63d0\u4ea4\u3002\u4efb\u52a1\u7f16\u53f7\uff1a{task.task_id}")
        sources = self._store.list_resumable_ppt_jobs(task.task_id, message.userid, message.chatid)
        if not sources:
            return ControlResult(f"\u5f53\u524d\u6ca1\u6709\u53ef\u7ee7\u7eed\u7684 PPT \u751f\u6210\u8bb0\u5f55\u3002\u8bf7\u53d1\u9001\uff1a\n\u91cd\u65b0\u751f\u6210PPT {task.task_id}")
        try:
            child = self._artifacts.resume_ppt(task_id=task.task_id, source_job_id=sources[0].job_id, user_id=message.userid, chat_id=message.chatid)
        except PptResumeRejected:
            if self._store.has_active_resume_child(sources[0].job_id):
                return ControlResult(f"\u8be5\u4efb\u52a1\u5df2\u6709 PPT \u6b63\u5728\u751f\u6210\uff0c\u8bf7\u52ff\u91cd\u590d\u63d0\u4ea4\u3002\u4efb\u52a1\u7f16\u53f7\uff1a{task.task_id}")
            return ControlResult(f"\u5f53\u524d PPT \u8bb0\u5f55\u65e0\u6cd5\u7ee7\u7eed\u751f\u6210\u3002\u8bf7\u53d1\u9001\uff1a\n\u91cd\u65b0\u751f\u6210PPT {task.task_id}")
        if child.status is ArtifactJobStatus.FAILED:
            return ControlResult(f"PPT \u751f\u6210\u542f\u52a8\u5931\u8d25\u3002\u4efb\u52a1\u7f16\u53f7\uff1a{task.task_id}")
        return ControlResult(f"PPT \u5c06\u4ece\u5df2\u4fdd\u5b58\u7684\u65ad\u70b9\u7ee7\u7eed\u751f\u6210\u3002\u4efb\u52a1\u7f16\u53f7\uff1a{task.task_id}")
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
