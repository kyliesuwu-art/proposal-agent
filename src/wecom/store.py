from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from src.wecom.models import ArtifactJob, ArtifactJobStatus, ArtifactType, TaskStatus, WeComTask


class ArtifactLineageError(RuntimeError):
    pass


class SQLiteTaskStore:
    """Small, independent task store; never opens the RAG SQLite/Chroma stores."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._connection:
            self._connection.executescript("""
                CREATE TABLE IF NOT EXISTS wecom_tasks (
                    task_id TEXT PRIMARY KEY, userid TEXT NOT NULL, chatid TEXT NOT NULL,
                    original_request TEXT NOT NULL, clarified_request TEXT,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    proposal_md_path TEXT, approved_md_path TEXT,
                    error_stage TEXT, error_message TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_wecom_tasks_owner_status
                    ON wecom_tasks(userid, chatid, status, updated_at);
                CREATE TABLE IF NOT EXISTS wecom_messages (
                    message_id TEXT PRIMARY KEY, task_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wecom_artifact_jobs (
                    job_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, artifact_type TEXT NOT NULL,
                    status TEXT NOT NULL, source_md_path TEXT NOT NULL, source_md_sha256 TEXT NOT NULL,
                    output_dir TEXT NOT NULL, primary_artifact_path TEXT, preview_artifact_path TEXT,
                    error_stage TEXT, error_message TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    delivered_at TEXT, parent_job_id TEXT, resume_from_job_id TEXT, retryable INTEGER NOT NULL DEFAULT 0, failure_code TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_wecom_artifact_jobs_task_type
                    ON wecom_artifact_jobs(task_id, artifact_type, created_at);
                CREATE TABLE IF NOT EXISTS wecom_control_messages (
                    message_id TEXT PRIMARY KEY, command_name TEXT NOT NULL
                );
            """)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @staticmethod
    def _task(row: sqlite3.Row) -> WeComTask:
        data = dict(row)
        data["status"] = TaskStatus(data["status"])
        return WeComTask(**data)

    def create(self, task: WeComTask, *, message_id: str | None = None) -> WeComTask:
        with self._lock, self._connection:
            self._connection.execute("""INSERT INTO wecom_tasks VALUES
                (:task_id, :userid, :chatid, :original_request, :clarified_request, :status,
                 :created_at, :updated_at, :proposal_md_path, :approved_md_path,
                 :error_stage, :error_message)""", task.payload())
            if message_id:
                self._connection.execute("INSERT INTO wecom_messages VALUES (?, ?)", (message_id, task.task_id))
        return task

    def get(self, task_id: str) -> WeComTask:
        with self._lock:
            row = self._connection.execute("SELECT * FROM wecom_tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self._task(row)

    def update(self, task: WeComTask) -> WeComTask:
        with self._lock, self._connection:
            self._connection.execute("""UPDATE wecom_tasks SET
                clarified_request=:clarified_request, status=:status, updated_at=:updated_at,
                proposal_md_path=:proposal_md_path, approved_md_path=:approved_md_path,
                error_stage=:error_stage, error_message=:error_message WHERE task_id=:task_id""", task.payload())
        return task

    def tasks_for(self, userid: str, chatid: str, *, statuses: tuple[TaskStatus, ...] | None = None) -> list[WeComTask]:
        sql, values = "SELECT * FROM wecom_tasks WHERE userid=? AND chatid=?", [userid, chatid]
        if statuses:
            sql += " AND status IN (" + ",".join("?" for _ in statuses) + ")"
            values.extend(status.value for status in statuses)
        sql += " ORDER BY created_at"
        with self._lock:
            rows = self._connection.execute(sql, values).fetchall()
        return [self._task(row) for row in rows]

    def tasks_with_status(self, status: TaskStatus) -> list[WeComTask]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM wecom_tasks WHERE status=?", (status.value,)).fetchall()
        return [self._task(row) for row in rows]

    def task_for_message(self, message_id: str | None) -> WeComTask | None:
        if not message_id:
            return None
        with self._lock:
            row = self._connection.execute("""SELECT t.* FROM wecom_messages m
                JOIN wecom_tasks t ON t.task_id=m.task_id WHERE m.message_id=?""", (message_id,)).fetchone()
        return self._task(row) if row else None

    def record_message(self, message_id: str | None, task_id: str) -> None:
        if not message_id:
            return
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR IGNORE INTO wecom_messages VALUES (?, ?)",
                (message_id, task_id),
            )

    @staticmethod
    def _artifact_job(row: sqlite3.Row) -> ArtifactJob:
        data = dict(row)
        data["artifact_type"] = ArtifactType(data["artifact_type"])
        data["status"] = ArtifactJobStatus(data["status"]); data["retryable"] = bool(data.get("retryable", False))
        return ArtifactJob(**data)

    def create_artifact_job(self, job: ArtifactJob) -> ArtifactJob:
        with self._lock, self._connection:
            self._connection.execute("""INSERT INTO wecom_artifact_jobs (job_id,task_id,artifact_type,status,source_md_path,source_md_sha256,output_dir,primary_artifact_path,preview_artifact_path,error_stage,error_message,created_at,updated_at,delivered_at,parent_job_id,resume_from_job_id,retryable,failure_code) VALUES
                (:job_id, :task_id, :artifact_type, :status, :source_md_path, :source_md_sha256,
                 :output_dir, :primary_artifact_path, :preview_artifact_path, :error_stage,
                 :error_message, :created_at, :updated_at, :delivered_at, :parent_job_id, :resume_from_job_id, :retryable, :failure_code)""", job.payload())
        return job

    def update_artifact_job(self, job: ArtifactJob) -> ArtifactJob:
        with self._lock, self._connection:
            self._connection.execute("""UPDATE wecom_artifact_jobs SET
                status=:status, source_md_path=:source_md_path, source_md_sha256=:source_md_sha256,
                output_dir=:output_dir, primary_artifact_path=:primary_artifact_path,
                preview_artifact_path=:preview_artifact_path, error_stage=:error_stage,
                error_message=:error_message, updated_at=:updated_at, delivered_at=:delivered_at, parent_job_id=:parent_job_id, resume_from_job_id=:resume_from_job_id, retryable=:retryable, failure_code=:failure_code
                WHERE job_id=:job_id""", job.payload())
        return job

    def get_artifact_job(self, job_id: str) -> ArtifactJob:
        with self._lock:
            row = self._connection.execute("SELECT * FROM wecom_artifact_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._artifact_job(row)

    def artifact_jobs_for(self, task_id: str, artifact_type: ArtifactType | None = None) -> list[ArtifactJob]:
        sql, values = "SELECT * FROM wecom_artifact_jobs WHERE task_id=?", [task_id]
        if artifact_type is not None:
            sql += " AND artifact_type=?"
            values.append(artifact_type.value)
        sql += " ORDER BY created_at"
        with self._lock:
            rows = self._connection.execute(sql, values).fetchall()
        return [self._artifact_job(row) for row in rows]

    def artifact_jobs_with_status(self, status: ArtifactJobStatus) -> list[ArtifactJob]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM wecom_artifact_jobs WHERE status=?", (status.value,)).fetchall()
        return [self._artifact_job(row) for row in rows]


    def list_artifact_children(self, parent_job_id: str) -> list[ArtifactJob]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM wecom_artifact_jobs WHERE parent_job_id=? ORDER BY created_at", (parent_job_id,)).fetchall()
        return [self._artifact_job(row) for row in rows]

    def has_active_resume_child(self, parent_job_id: str) -> bool:
        with self._lock:
            return self._connection.execute("SELECT 1 FROM wecom_artifact_jobs WHERE parent_job_id=? AND resume_from_job_id IS NOT NULL AND status IN (?,?)", (parent_job_id, ArtifactJobStatus.QUEUED.value, ArtifactJobStatus.RUNNING.value)).fetchone() is not None
    def list_resumable_ppt_jobs(self, task_id: str, user_id: str, chatid: str) -> list[ArtifactJob]:
        with self._lock:
            rows = self._connection.execute("""SELECT j.* FROM wecom_artifact_jobs j JOIN wecom_tasks t ON t.task_id=j.task_id
                WHERE j.task_id=? AND t.userid=? AND t.chatid=? AND j.artifact_type=? AND j.status=? AND j.retryable=1
                ORDER BY j.created_at DESC, j.job_id DESC""", (task_id, user_id, chatid, ArtifactType.PPTX.value, ArtifactJobStatus.FAILED.value)).fetchall()
        return [self._artifact_job(row) for row in rows]

    def walk_artifact_parent_chain(self, job_id: str, *, max_depth: int = 32) -> list[ArtifactJob]:
        if max_depth < 1: raise ArtifactLineageError("invalid parent chain depth")
        chain, seen = [], set(); current = job_id; identity = None
        while current:
            if current in seen: raise ArtifactLineageError("artifact parent chain cycle")
            if len(chain) >= max_depth: raise ArtifactLineageError("artifact parent chain exceeds depth")
            try: job = self.get_artifact_job(current)
            except KeyError as exc: raise ArtifactLineageError("artifact parent is missing") from exc
            with self._lock:
                owner = self._connection.execute("SELECT userid,chatid FROM wecom_tasks WHERE task_id=?", (job.task_id,)).fetchone()
            if owner is None or job.artifact_type is not ArtifactType.PPTX: raise ArtifactLineageError("artifact parent chain scope mismatch")
            node_identity = (job.task_id, owner[0], owner[1], job.artifact_type)
            if identity is None: identity = node_identity
            elif node_identity != identity: raise ArtifactLineageError("artifact parent chain scope mismatch")
            chain.append(job); seen.add(current); current = job.parent_job_id
        return chain

    def create_resume_child_atomic(self, source_job_id: str, child: ArtifactJob) -> ArtifactJob:
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                source = self.get_artifact_job(source_job_id)
                if source.artifact_type is not ArtifactType.PPTX or source.status is not ArtifactJobStatus.FAILED or not source.retryable: raise ArtifactLineageError("source Job is not resumable")
                if child.parent_job_id != source.job_id or child.resume_from_job_id != source.job_id or child.task_id != source.task_id or child.artifact_type is not ArtifactType.PPTX or child.status is not ArtifactJobStatus.QUEUED: raise ArtifactLineageError("resume child identity is invalid")
                active = self._connection.execute("SELECT 1 FROM wecom_artifact_jobs WHERE parent_job_id=? AND resume_from_job_id IS NOT NULL AND status IN (?,?)", (source_job_id, ArtifactJobStatus.QUEUED.value, ArtifactJobStatus.RUNNING.value)).fetchone()
                if active: raise ArtifactLineageError("source Job already has an active resume child")
                self._connection.execute("""INSERT INTO wecom_artifact_jobs (job_id,task_id,artifact_type,status,source_md_path,source_md_sha256,output_dir,primary_artifact_path,preview_artifact_path,error_stage,error_message,created_at,updated_at,delivered_at,parent_job_id,resume_from_job_id,retryable,failure_code) VALUES (:job_id,:task_id,:artifact_type,:status,:source_md_path,:source_md_sha256,:output_dir,:primary_artifact_path,:preview_artifact_path,:error_stage,:error_message,:created_at,:updated_at,:delivered_at,:parent_job_id,:resume_from_job_id,:retryable,:failure_code)""", child.payload())
                self._connection.commit(); return child
            except Exception:
                self._connection.rollback(); raise
    def control_message_seen(self, message_id: str | None) -> bool:
        if not message_id:
            return False
        with self._lock:
            return self._connection.execute(
                "SELECT 1 FROM wecom_control_messages WHERE message_id=?", (message_id,),
            ).fetchone() is not None

    def record_control_message(self, message_id: str | None, command_name: str) -> None:
        if not message_id:
            return
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR IGNORE INTO wecom_control_messages VALUES (?, ?)", (message_id, command_name),
            )
