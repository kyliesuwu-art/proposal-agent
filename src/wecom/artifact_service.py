from __future__ import annotations

import hashlib
import json
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable
from zipfile import is_zipfile

from src.wecom.models import ArtifactEvent, ArtifactJob, ArtifactJobStatus, ArtifactType, TaskStatus, WeComTask
from src.wecom.task_paths import PersistedTaskPathError, resolve_persisted_task_path
from src.wecom.store import ArtifactLineageError, SQLiteTaskStore
from src.wecom.ppt_checkpoint import ResumeRejected, inspect_resume_source, load_checkpoint_manifest
from src.wecom.ppt_failures import PptFailureCode, PptRunnerFailure
from src.wecom.word_runner import WordRunner
from src.wecom.ppt_runner import PptRunner


class PptResumeRejected(RuntimeError):
    """A requested explicit PPT resume source is not safe or compatible."""


def _is_link_or_reparse_point(path: Path) -> bool:
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        return False
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)() or bool(getattr(stat_result, "st_file_attributes", 0) & 0x0400)
def _validate_checkpoint_object(value: object, _kind: str) -> None:
    if not isinstance(value, dict):
        raise ResumeRejected("checkpoint JSON root is invalid")

def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _command(text: str) -> tuple[ArtifactType, bool, str | None] | None:
    compact = "".join(text.strip().split()).lower()
    if compact in {"生成word", "导出word"}:
        return ArtifactType.WORD, False, None
    if compact == "重新生成word":
        return ArtifactType.WORD, True, None
    import re
    match = re.fullmatch(r"生成ppt([0-9a-f]{12})?", compact)
    if match:
        return ArtifactType.PPTX, False, match.group(1)
    match = re.fullmatch(r"重新生成ppt([0-9a-f]{12})?", compact)
    if match:
        return ArtifactType.PPTX, True, match.group(1)
    return None


class ArtifactService:
    """Asynchronous artifact jobs kept separate from the approved content task."""

    def __init__(self, store: SQLiteTaskStore, word_runner: WordRunner, ppt_runner: PptRunner | None = None, *, task_output_root: Path, output_root: Path, task_path_root: Path | None = None) -> None:
        self._store, self._word_runner = store, word_runner
        self._ppt_runner = ppt_runner
        self._task_output_root, self._output_root = task_output_root.resolve(), output_root.resolve()
        self._task_path_root = Path(task_path_root or task_output_root).resolve()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wecom-word")
        self._futures: set[Future[None]] = set()
        self._future_lock = threading.Lock()
        self._start_lock = threading.RLock()
        self._subscribers: list[Callable[[ArtifactEvent], None]] = []
        self._recover_interrupted_jobs()

    def subscribe(self, listener: Callable[[ArtifactEvent], None]) -> None:
        self._subscribers.append(listener)

    @property
    def output_root(self) -> Path:
        return self._output_root

    def _emit(self, name: str, job: ArtifactJob, task: WeComTask) -> None:
        event = ArtifactEvent(name, job, task)
        for listener in tuple(self._subscribers):
            try:
                listener(event)
            except Exception:
                pass

    def _recover_interrupted_jobs(self) -> None:
        for job in self._store.artifact_jobs_with_status(ArtifactJobStatus.RUNNING):
            self._store.update_artifact_job(replace(
                job, status=ArtifactJobStatus.FAILED, error_stage="interrupted_restart",
                error_message="Word generation interrupted by restart", updated_at=_now(),
            ))

    def receive(self, message) -> tuple[WeComTask | None, str] | None:
        parsed = _command(message.content)
        if parsed is None:
            return None
        artifact_type, retry, requested_task_id = parsed
        duplicate = self._store.task_for_message(message.message_id)
        if duplicate:
            return duplicate, f"该消息已处理。任务编号：{duplicate.task_id}"
        approved = self._store.tasks_for(message.userid, message.chatid, statuses=(TaskStatus.MD_APPROVED,))
        if requested_task_id:
            approved = [task for task in approved if task.task_id == requested_task_id]
        if not approved:
            return None, "请先确认 Markdown 内容，再生成 Word。"
        if len(approved) != 1:
            return None, "当前存在多个已确认方案，请先提供需要生成 Word 的任务编号。"
        task = approved[0]
        if artifact_type is ArtifactType.PPTX:
            if self._ppt_runner is None:
                return task, "PPT 尚未配置生产 Runner。"
            return self._request_ppt(task, message.message_id, retry=retry)
        return self._request_word(task, message.message_id, retry=retry)

    def _safe_source(self, task: WeComTask) -> tuple[Path, str]:
        if task.status is not TaskStatus.MD_APPROVED or not task.approved_md_path:
            raise ValueError("approved Markdown is unavailable")
        try:
            source = resolve_persisted_task_path(task.approved_md_path, self._task_path_root)
        except PersistedTaskPathError as exc:
            raise ValueError("approved Markdown path is unsafe or unavailable") from exc
        task_directory = (self._task_output_root / task.task_id).resolve()
        if task_directory not in source.parents:
            raise ValueError("approved Markdown path is outside its Task directory")
        return source, hashlib.sha256(source.read_bytes()).hexdigest()

    def _request_word(self, task: WeComTask, message_id: str | None, *, retry: bool) -> tuple[WeComTask, str]:
        with self._start_lock:
            jobs = self._store.artifact_jobs_for(task.task_id, ArtifactType.WORD)
            latest = jobs[-1] if jobs else None
            if latest and latest.status in {ArtifactJobStatus.QUEUED, ArtifactJobStatus.RUNNING}:
                self._store.record_message(message_id, task.task_id)
                return task, f"Word 文档正在生成，请勿重复提交。任务编号：{task.task_id}"
            if latest and latest.status in {ArtifactJobStatus.READY, ArtifactJobStatus.DELIVERED}:
                self._store.record_message(message_id, task.task_id)
                return task, f"Word 文档已经生成。任务编号：{task.task_id}"
            if latest and latest.status is ArtifactJobStatus.FAILED and not retry:
                self._store.record_message(message_id, task.task_id)
                return task, f"上一次 Word 文档生成失败。如需重试，请回复“重新生成Word”。任务编号：{task.task_id}"
            try:
                source, digest = self._safe_source(task)
            except ValueError:
                self._store.record_message(message_id, task.task_id)
                return task, f"Word 文档生成失败。任务编号：{task.task_id}"
            now, job_id = _now(), uuid.uuid4().hex[:12]
            output_dir = self._output_root / task.task_id / job_id
            job = ArtifactJob(job_id, task.task_id, ArtifactType.WORD, ArtifactJobStatus.QUEUED,
                              str(source), digest, str(output_dir), None, None, None, None, now, now)
            self._store.create_artifact_job(job)
            self._store.record_message(message_id, task.task_id)
            future = self._executor.submit(self._generate_word, task.task_id, job_id)
            with self._future_lock:
                self._futures.add(future)
            future.add_done_callback(lambda done: self._futures.discard(done))
            return task, f"Word 文档已开始生成。任务编号：{task.task_id}"

    def _request_ppt(self, task: WeComTask, message_id: str | None, *, retry: bool) -> tuple[WeComTask, str]:
        with self._start_lock:
            jobs = self._store.artifact_jobs_for(task.task_id, ArtifactType.PPTX)
            latest = jobs[-1] if jobs else None
            if latest and latest.status in {ArtifactJobStatus.QUEUED, ArtifactJobStatus.RUNNING}:
                self._store.record_message(message_id, task.task_id); return task, f"PPT 正在生成，请勿重复提交。任务编号：{task.task_id}"
            if latest and latest.status in {ArtifactJobStatus.READY, ArtifactJobStatus.DELIVERED} and not retry:
                self._store.record_message(message_id, task.task_id); return task, f"PPT 已经生成。任务编号：{task.task_id}"
            if latest and latest.status is ArtifactJobStatus.FAILED and not retry:
                self._store.record_message(message_id, task.task_id); return task, f"上一次 PPT 生成失败。如需重试，请回复“重新生成PPT”。任务编号：{task.task_id}"
            try: source, digest = self._safe_source(task)
            except ValueError:
                self._store.record_message(message_id, task.task_id); return task, f"PPT 生成失败。任务编号：{task.task_id}"
            now, job_id = _now(), uuid.uuid4().hex[:12]; output_dir = self._output_root / task.task_id / job_id
            job = ArtifactJob(job_id, task.task_id, ArtifactType.PPTX, ArtifactJobStatus.QUEUED, str(source), digest, str(output_dir), None, None, None, None, now, now)
            self._store.create_artifact_job(job); self._store.record_message(message_id, task.task_id)
            future = self._executor.submit(self._generate_ppt, task.task_id, job_id)
            with self._future_lock: self._futures.add(future)
            future.add_done_callback(lambda done: self._futures.discard(done))
            return task, f"PPT 已开始生成。任务编号：{task.task_id}"

    def _resume_source(self, *, task_id: str, source_job_id: str, user_id: str, chat_id: str) -> tuple[WeComTask, ArtifactJob, Path]:
        try:
            task = self._store.get(task_id)
            source = self._store.get_artifact_job(source_job_id)
        except KeyError as exc:
            raise PptResumeRejected("PPT resume source is unavailable") from exc
        if task.userid != user_id or task.chatid != chat_id:
            raise PptResumeRejected("PPT resume source does not belong to this user or chat")
        try:
            approved_path, approved_sha = self._safe_source(task)
        except ValueError as exc:
            raise PptResumeRejected("PPT approved Markdown is unavailable") from exc
        if (
            source.artifact_type is not ArtifactType.PPTX
            or source.status is not ArtifactJobStatus.FAILED
            or not source.retryable
            or source.task_id != task_id
            or source.source_md_sha256 != approved_sha
        ):
            raise PptResumeRejected("PPT resume source is not eligible")
        try:
            self._store.walk_artifact_parent_chain(source_job_id)
        except ArtifactLineageError as exc:
            raise PptResumeRejected("PPT resume lineage is invalid") from exc
        root = Path(source.output_dir)
        if not root.is_dir() or _is_link_or_reparse_point(root):
            raise PptResumeRejected("PPT resume source output is unsafe")
        try:
            root = root.resolve(strict=True)
            root.relative_to(self._output_root)
        except (OSError, ValueError) as exc:
            raise PptResumeRejected("PPT resume source output escapes artifact storage") from exc
        try:
            checkpoint = load_checkpoint_manifest(root)
            if checkpoint["source_job_id"] != source.job_id:
                raise ResumeRejected("checkpoint source Job does not match ArtifactJob")
            assets_path = root / "assets_manifest.json"
            if _is_link_or_reparse_point(assets_path) or not assets_path.is_file():
                raise ResumeRejected("source assets manifest is unsafe")
            assets_sha = hashlib.sha256(assets_path.read_bytes()).hexdigest()
            compatibility = {
                "producer": checkpoint["producer_compatibility_version"],
                "prompt": checkpoint["prompt_contract_version"],
                "scene_schema": checkpoint["scene_schema_version"],
                "pages": checkpoint["total_slide_count"],
            }
            expected = {
                "task_id": task_id,
                "source_job_id": source.job_id,
                "approved_md_sha256": approved_sha,
                "assets_manifest_sha256": assets_sha,
                "compatibility": compatibility,
                "model_max_calls": checkpoint["model_max_calls"],
                "pages": checkpoint["total_slide_count"],
            }
            state = inspect_resume_source(
                parent_root=root,
                expected=expected,
                validate_global=lambda value: _validate_checkpoint_object(value, "global"),
                validate_page=lambda _page, value: _validate_checkpoint_object(value, "page"),
            )
        except (OSError, ResumeRejected, ValueError, KeyError) as exc:
            raise PptResumeRejected("PPT resume checkpoint is invalid") from exc
        if state.global_direction is None or state.remaining_budget <= 0:
            raise PptResumeRejected("PPT resume checkpoint has no usable budget or global direction")
        return task, source, root

    def resume_ppt(self, *, task_id: str, source_job_id: str, user_id: str, chat_id: str) -> ArtifactJob:
        """Explicitly create one child Job from a verified FAILED PPT source."""
        if self._ppt_runner is None:
            raise PptResumeRejected("PPT production Runner is unavailable")
        with self._start_lock:
            task, source, source_root = self._resume_source(
                task_id=task_id, source_job_id=source_job_id, user_id=user_id, chat_id=chat_id
            )
            now, job_id = _now(), uuid.uuid4().hex[:12]
            output_dir = self._output_root / task_id / job_id
            child = ArtifactJob(
                job_id, task_id, ArtifactType.PPTX, ArtifactJobStatus.QUEUED,
                source.source_md_path, source.source_md_sha256, str(output_dir),
                None, None, None, None, now, now,
                parent_job_id=source.job_id, resume_from_job_id=source.job_id,
                retryable=False, failure_code=None,
            )
            try:
                self._store.create_resume_child_atomic(source.job_id, child)
            except ArtifactLineageError as exc:
                raise PptResumeRejected("PPT resume source already has an active child") from exc
            try:
                future = self._executor.submit(self._generate_ppt, task_id, job_id, source_root)
            except Exception:
                failed = self._store.update_artifact_job(replace(
                    child, status=ArtifactJobStatus.FAILED, error_stage="resume_scheduling",
                    error_message="PPT generation failed", failure_code=PptFailureCode.UNKNOWN_FAILURE.value,
                    retryable=False, updated_at=_now(),
                ))
                self._emit("PPT_GENERATION_FAILED", failed, task)
                return failed
            with self._future_lock:
                self._futures.add(future)
            future.add_done_callback(lambda done: self._futures.discard(done))
            return child
    def _generate_ppt(self, task_id: str, job_id: str, resume_from_job_root: Path | None = None) -> None:
        task, job = self._store.get(task_id), self._store.get_artifact_job(job_id)
        running = self._store.update_artifact_job(replace(job, status=ArtifactJobStatus.RUNNING, updated_at=_now()))
        self._emit("PPT_GENERATION_STARTED", running, task)
        try:
            source = Path(running.source_md_path); before = hashlib.sha256(source.read_bytes()).hexdigest()
            if before != running.source_md_sha256: raise RuntimeError("approved Markdown changed before PPT generation")
            if resume_from_job_root is None:
                result = self._ppt_runner.run(source, self._task_output_root / task.task_id, Path(running.output_dir), task_id, job_id)
            else:
                result = self._ppt_runner.run(source, self._task_output_root / task.task_id, Path(running.output_dir), task_id, job_id, resume_from_job_root=resume_from_job_root)
            deck = result.primary_artifact_path.resolve(); root = Path(running.output_dir).resolve()
            if root not in deck.parents or not deck.is_file() or deck.stat().st_size == 0 or not is_zipfile(deck): raise RuntimeError("invalid PPT output")
            if result.evaluation_status == "FAIL":
                failed = self._store.update_artifact_job(replace(running, status=ArtifactJobStatus.FAILED, error_stage="evaluation", error_message="PPT generation failed", failure_code=PptFailureCode.EVALUATION_FAILED.value, retryable=False, updated_at=_now()))
                self._emit("PPT_GENERATION_FAILED", failed, task)
                return
            if result.warnings:
                (root / "evaluation_warnings.json").write_text(json.dumps({
                    "evaluation_status": result.evaluation_status,
                    "warnings": list(result.warnings),
                }, ensure_ascii=False, indent=2), encoding="utf-8")
            if hashlib.sha256(source.read_bytes()).hexdigest() != before: raise RuntimeError("approved Markdown changed during PPT generation")
            ready = self._store.update_artifact_job(replace(running, status=ArtifactJobStatus.READY, primary_artifact_path=str(deck), preview_artifact_path=str(result.preview_artifact_path) if result.preview_artifact_path else None, updated_at=_now()))
            self._emit("PPT_GENERATION_READY", ready, task)
        except PptRunnerFailure as exc:
            failure = exc.failure
            failed = self._store.update_artifact_job(replace(
                running,
                status=ArtifactJobStatus.FAILED,
                error_stage=failure.stage,
                error_message="PPT generation failed",
                failure_code=failure.code.value,
                retryable=failure.retryable,
                updated_at=_now(),
            ))
            self._emit("PPT_GENERATION_FAILED", failed, task)
        except Exception:
            failed = self._store.update_artifact_job(replace(
                running,
                status=ArtifactJobStatus.FAILED,
                error_stage="ppt_generation",
                error_message="PPT generation failed",
                failure_code=PptFailureCode.UNKNOWN_FAILURE.value,
                retryable=False,
                updated_at=_now(),
            ))
            self._emit("PPT_GENERATION_FAILED", failed, task)

    def _generate_word(self, task_id: str, job_id: str) -> None:
        task, job = self._store.get(task_id), self._store.get_artifact_job(job_id)
        running = self._store.update_artifact_job(replace(job, status=ArtifactJobStatus.RUNNING, updated_at=_now()))
        self._emit("WORD_GENERATION_STARTED", running, task)
        try:
            source = Path(running.source_md_path)
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            if before != running.source_md_sha256:
                raise RuntimeError("approved Markdown changed before Word generation")
            result = self._word_runner.run(source, Path(running.output_dir), task_id)
            document = result.primary_artifact_path.resolve()
            job_root = Path(running.output_dir).resolve()
            if job_root not in document.parents or not document.is_file() or document.stat().st_size == 0 or not is_zipfile(document):
                raise RuntimeError("invalid Word output")
            if hashlib.sha256(source.read_bytes()).hexdigest() != before:
                raise RuntimeError("approved Markdown changed during Word generation")
            ready = self._store.update_artifact_job(replace(
                running, status=ArtifactJobStatus.READY, primary_artifact_path=str(document),
                preview_artifact_path=str(result.preview_artifact_path) if result.preview_artifact_path else None,
                updated_at=_now(),
            ))
            self._emit("WORD_GENERATION_READY", ready, task)
        except Exception:
            failed = self._store.update_artifact_job(replace(
                running, status=ArtifactJobStatus.FAILED, error_stage="word_generation",
                error_message="Word document generation failed", updated_at=_now(),
            ))
            self._emit("WORD_GENERATION_FAILED", failed, task)

    def mark_delivered(self, job_id: str) -> ArtifactJob:
        job = self._store.get_artifact_job(job_id)
        if job.status is not ArtifactJobStatus.READY:
            return job
        return self._store.update_artifact_job(replace(job, status=ArtifactJobStatus.DELIVERED, delivered_at=_now(), updated_at=_now()))

    def mark_delivery_failed(self, job_id: str) -> ArtifactJob:
        job = self._store.get_artifact_job(job_id)
        if job.status is not ArtifactJobStatus.READY:
            return job
        return self._store.update_artifact_job(replace(
            job, status=ArtifactJobStatus.FAILED, error_stage="media_delivery",
            error_message="Word document delivery failed", updated_at=_now(),
        ))

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
