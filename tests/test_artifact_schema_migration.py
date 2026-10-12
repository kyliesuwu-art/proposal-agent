from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3
import pytest

from src.wecom.models import ArtifactJob, ArtifactJobStatus, ArtifactType
from src.wecom.store import SQLiteTaskStore


LEGACY_ARTIFACT_TABLE = """
CREATE TABLE wecom_artifact_jobs (
    job_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, artifact_type TEXT NOT NULL,
    status TEXT NOT NULL, source_md_path TEXT NOT NULL, source_md_sha256 TEXT NOT NULL,
    output_dir TEXT NOT NULL, primary_artifact_path TEXT, preview_artifact_path TEXT,
    error_stage TEXT, error_message TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    delivered_at TEXT
)
"""


def _legacy_database(path: Path, *, partial: bool = False) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE wecom_tasks (
                task_id TEXT PRIMARY KEY, userid TEXT NOT NULL, chatid TEXT NOT NULL,
                original_request TEXT NOT NULL, clarified_request TEXT,
                status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                proposal_md_path TEXT, approved_md_path TEXT,
                error_stage TEXT, error_message TEXT
            );
            CREATE TABLE wecom_messages (message_id TEXT PRIMARY KEY, task_id TEXT NOT NULL);
            CREATE TABLE wecom_control_messages (message_id TEXT PRIMARY KEY, command_name TEXT NOT NULL);
            """
            + LEGACY_ARTIFACT_TABLE
        )
        if partial:
            connection.execute("ALTER TABLE wecom_artifact_jobs ADD COLUMN parent_job_id TEXT")
        connection.execute(
            """INSERT INTO wecom_tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("task-00000001", "user-1", "chat-1", "request", None, "MD_APPROVED", "2026-09-24T00:00:00+08:00", "2026-09-24T00:00:00+08:00", None, "tasks/task-00000001/approved.md", None, None),
        )
        connection.execute("INSERT INTO wecom_messages VALUES (?, ?)", ("message-1", "task-00000001"))
        connection.execute(
            """INSERT INTO wecom_artifact_jobs
            (job_id, task_id, artifact_type, status, source_md_path, source_md_sha256, output_dir,
             primary_artifact_path, preview_artifact_path, error_stage, error_message, created_at, updated_at, delivered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("legacy-word", "task-00000001", "WORD", "DELIVERED", "tasks/task-00000001/approved.md", "a" * 64, "artifacts/task-00000001/legacy-word", "artifacts/task-00000001/legacy-word/proposal.docx", None, None, None, "2026-09-24T00:00:00+08:00", "2026-09-24T00:00:00+08:00", "2026-09-24T00:01:00+08:00"),
        )
        connection.commit()
    finally:
        connection.close()


def _columns(path: Path) -> dict[str, tuple[str, int, object]]:
    connection = sqlite3.connect(path)
    try:
        return {row[1]: (row[2], row[3], row[4]) for row in connection.execute("PRAGMA table_info(wecom_artifact_jobs)")}
    finally:
        connection.close()


def _job(job_id: str, *, status: ArtifactJobStatus = ArtifactJobStatus.QUEUED, parent: str | None = None) -> ArtifactJob:
    now = "2026-09-24T00:00:00+08:00"
    return ArtifactJob(
        job_id, "task-00000001", ArtifactType.PPTX, status,
        "tasks/task-00000001/approved.md", "b" * 64, f"artifacts/task-00000001/{job_id}",
        None, None, None, None, now, now,
        parent_job_id=parent, resume_from_job_id=parent,
    )


def test_legacy_artifact_schema_migrates_without_mutating_existing_rows(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite"
    _legacy_database(path)
    before = sqlite3.connect(path).execute("SELECT * FROM wecom_artifact_jobs WHERE job_id='legacy-word'").fetchone()
    store = SQLiteTaskStore(path)
    try:
        columns = _columns(path)
        assert {"parent_job_id", "resume_from_job_id", "retryable", "failure_code"} <= set(columns)
        assert columns["retryable"] == ("INTEGER", 1, "0")
        legacy = store.get_artifact_job("legacy-word")
        assert legacy.parent_job_id is None
        assert legacy.resume_from_job_id is None
        assert legacy.retryable is False
        assert legacy.failure_code is None
        after = sqlite3.connect(path).execute("SELECT * FROM wecom_artifact_jobs WHERE job_id='legacy-word'").fetchone()
        assert tuple(after[: len(before)]) == tuple(before)
        assert sqlite3.connect(path).execute("SELECT count(*) FROM wecom_messages").fetchone()[0] == 1
        store.create_artifact_job(_job("new-ppt"))
        source = replace(_job("source-ppt", status=ArtifactJobStatus.FAILED), retryable=True, failure_code="PPT_MODEL_TRANSPORT_RETRY_EXHAUSTED")
        store.create_artifact_job(source)
        child = _job("resume-ppt", parent=source.job_id)
        store.create_resume_child_atomic(source.job_id, child)
        assert store.get_artifact_job(child.job_id).parent_job_id == source.job_id
        assert [job.job_id for job in store.list_resumable_ppt_jobs("task-00000001", "user-1", "chat-1")] == [source.job_id]
    finally:
        store.close()


def test_partial_legacy_schema_is_completed_and_second_open_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "partial.sqlite"
    _legacy_database(path, partial=True)
    first = SQLiteTaskStore(path)
    first.close()
    assert {"parent_job_id", "resume_from_job_id", "retryable", "failure_code"} <= set(_columns(path))
    second = SQLiteTaskStore(path)
    try:
        assert second.get_artifact_job("legacy-word").retryable is False
    finally:
        second.close()


def test_migration_failure_preserves_legacy_table_and_rows(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "failed-migration.sqlite"
    _legacy_database(path)

    def fail_migration(_store) -> None:
        raise sqlite3.OperationalError("simulated migration failure")

    monkeypatch.setattr(SQLiteTaskStore, "_migrate_artifact_job_schema", fail_migration)
    with pytest.raises(sqlite3.OperationalError, match="simulated migration failure"):
        SQLiteTaskStore(path)

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("SELECT count(*) FROM wecom_artifact_jobs").fetchone()[0] == 1
        assert [row[1] for row in connection.execute("PRAGMA table_info(wecom_artifact_jobs)")] == [
            "job_id", "task_id", "artifact_type", "status", "source_md_path", "source_md_sha256",
            "output_dir", "primary_artifact_path", "preview_artifact_path", "error_stage", "error_message",
            "created_at", "updated_at", "delivered_at",
        ]
    finally:
        connection.close()

def test_new_database_initialization_keeps_lineage_columns(tmp_path: Path) -> None:
    path = tmp_path / "new.sqlite"
    store = SQLiteTaskStore(path)
    try:
        assert {"parent_job_id", "resume_from_job_id", "retryable", "failure_code"} <= set(_columns(path))
    finally:
        store.close()
