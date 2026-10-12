from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from src.artifact_assets.safe_resolver import resolve_task_image
from src.wecom.artifact_service import ArtifactService
from src.wecom.models import ArtifactType, TaskStatus, WeComTask
from src.wecom.store import SQLiteTaskStore
from src.wecom.task_paths import PersistedTaskPathError, resolve_persisted_task_path


TASK_ID = "95f07bf23ea8"


class FakeWordRunner:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    def run(self, approved_md_path: Path, output_dir: Path, _task_id: str):
        self.calls.append(approved_md_path)
        output = output_dir / "proposal.docx"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"PK\\x03\\x04")
        from src.wecom.word_runner import WordResult
        return WordResult(output)


class FakePptRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path]] = []

    def run(self, approved_md_path: Path, task_root: Path, output_dir: Path, _task_id: str, _job_id: str):
        self.calls.append((approved_md_path, task_root))
        output = output_dir / "proposal.pptx"
        output.parent.mkdir(parents=True, exist_ok=True)
        from zipfile import ZipFile
        with ZipFile(output, "w") as archive:
            archive.writestr("ppt/presentation.xml", "x")
        report = output_dir / "artifact_evaluation" / "evaluation_report.json"
        report.parent.mkdir()
        report.write_text('{"overall_status":"PASS","limitations":[]}', encoding="utf-8")
        from src.wecom.ppt_runner import PptResult
        return PptResult(output, evaluation_status="PASS")


def write_task(root: Path, *, relative: str = "outputs/tasks/95f07bf23ea8/approved.md", content: str = "# approved\\n![image](assets/diagram.png)\\n") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    assets = path.parent / "assets"
    assets.mkdir(exist_ok=True)
    (assets / "diagram.png").write_bytes(b"image")
    return path


def task(persisted: str) -> WeComTask:
    now = datetime.now().astimezone().isoformat()
    return WeComTask(TASK_ID, "u1", "c1", "request", "request", TaskStatus.MD_APPROVED, now, now, approved_md_path=persisted)


def test_relative_persisted_path_uses_explicit_root_not_cwd(tmp_path, monkeypatch):
    root = tmp_path / "origin"; approved = write_task(root)
    other_worktree = tmp_path / "integration"; other_worktree.mkdir()
    monkeypatch.chdir(other_worktree)
    resolved = resolve_persisted_task_path("outputs/tasks/95f07bf23ea8/approved.md", root)
    assert resolved == approved.resolve()


def test_same_worktree_default_root_remains_compatible(tmp_path):
    task_root = tmp_path / "tasks"; approved = write_task(tmp_path, relative="tasks/95f07bf23ea8/approved.md")
    assert resolve_persisted_task_path(str(approved), task_root.parent) == approved.resolve()


@pytest.mark.parametrize("persisted", ["../outside.md", "outputs/tasks/95f07bf23ea8/../approved.md", "C:/outside/approved.md", r"\\server\\share\\approved.md"])
def test_persisted_path_rejects_traversal_drive_and_unc(tmp_path, persisted):
    root = tmp_path / "root"; root.mkdir()
    with pytest.raises(PersistedTaskPathError):
        resolve_persisted_task_path(persisted, root)


def test_absolute_path_outside_root_is_rejected(tmp_path):
    root = tmp_path / "root"; root.mkdir()
    outside = tmp_path / "outside.md"; outside.write_text("# x", encoding="utf-8")
    with pytest.raises(PersistedTaskPathError):
        resolve_persisted_task_path(str(outside), root)


@pytest.mark.parametrize("kind", ["directory", "empty", "extension", "missing"])
def test_persisted_path_requires_nonempty_regular_markdown(tmp_path, kind):
    root = tmp_path / "root"; root.mkdir()
    candidate = root / "outputs" / "tasks" / TASK_ID / "approved.md"
    candidate.parent.mkdir(parents=True)
    if kind == "directory": candidate.mkdir()
    elif kind == "empty": candidate.write_bytes(b"")
    elif kind == "extension":
        candidate = candidate.with_suffix(".txt"); candidate.write_text("# x", encoding="utf-8")
    elif kind == "missing": pass
    with pytest.raises(PersistedTaskPathError):
        resolve_persisted_task_path(candidate.relative_to(root).as_posix(), root)


def test_symlink_file_and_escape_are_rejected(tmp_path):
    root = tmp_path / "root"; root.mkdir(); outside = tmp_path / "outside.md"; outside.write_text("# x", encoding="utf-8")
    link = root / "outputs" / "tasks" / TASK_ID / "approved.md"; link.parent.mkdir(parents=True)
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    with pytest.raises(PersistedTaskPathError):
        resolve_persisted_task_path(link.relative_to(root).as_posix(), root)


def test_artifact_service_resolves_cross_worktree_before_fake_ppt_job(tmp_path, monkeypatch):
    root = tmp_path / "origin"; approved = write_task(root)
    monkeypatch.chdir(tmp_path / "integration") if (tmp_path / "integration").exists() else (tmp_path / "integration").mkdir()
    store = SQLiteTaskStore(tmp_path / "test.sqlite"); store.create(task("outputs/tasks/95f07bf23ea8/approved.md"))
    word, ppt = FakeWordRunner(), FakePptRunner()
    artifacts = ArtifactService(store, word, ppt, task_output_root=root / "outputs" / "tasks", task_path_root=root, output_root=tmp_path / "jobs")
    try:
        from src.wecom.adapter import IncomingMessage
        result = artifacts.receive(IncomingMessage("u1", "c1", f"生成PPT {TASK_ID}", "ppt-1"))
        assert result is not None
        artifacts.wait_for_idle()
        assert len(store.artifact_jobs_for(TASK_ID, ArtifactType.PPTX)) == 1
        assert ppt.calls == [(approved.resolve(), (root / "outputs" / "tasks" / TASK_ID).resolve())]
    finally:
        artifacts.shutdown(); store.close()


def test_missing_persisted_source_fails_before_job_runner_or_model(tmp_path):
    root = tmp_path / "root"; (root / "outputs" / "tasks").mkdir(parents=True)
    store = SQLiteTaskStore(tmp_path / "test.sqlite"); store.create(task("outputs/tasks/95f07bf23ea8/approved.md"))
    word, ppt = FakeWordRunner(), FakePptRunner()
    artifacts = ArtifactService(store, word, ppt, task_output_root=root / "outputs" / "tasks", task_path_root=root, output_root=tmp_path / "jobs")
    try:
        from src.wecom.adapter import IncomingMessage
        artifacts.receive(IncomingMessage("u1", "c1", f"生成PPT {TASK_ID}", "ppt-missing"))
        assert store.artifact_jobs_for(TASK_ID, ArtifactType.PPTX) == []
        assert ppt.calls == []
    finally:
        artifacts.shutdown(); store.close()


def test_markdown_images_remain_relative_to_resolved_task_directory(tmp_path):
    root = tmp_path / "origin"; approved = write_task(root)
    resolved = resolve_persisted_task_path("outputs/tasks/95f07bf23ea8/approved.md", root)
    image = resolve_task_image(resolved.parent, "assets/diagram.png")
    assert image.path == approved.parent / "assets" / "diagram.png"


def test_word_and_ppt_share_the_same_resolved_task_source(tmp_path):
    root = tmp_path / "origin"; approved = write_task(root)
    store = SQLiteTaskStore(tmp_path / "test.sqlite"); store.create(task("outputs/tasks/95f07bf23ea8/approved.md"))
    artifacts = ArtifactService(store, FakeWordRunner(), FakePptRunner(), task_output_root=root / "outputs" / "tasks", task_path_root=root, output_root=tmp_path / "jobs")
    try:
        source, _digest = artifacts._safe_source(store.get(TASK_ID))
        assert source == approved.resolve()
    finally:
        artifacts.shutdown(); store.close()


def test_bot_cli_accepts_explicit_task_path_root(tmp_path):
    import src.wecom_bot as bot
    args = bot.parse_args(["--task-path-root", str(tmp_path)])
    assert args.task_path_root == tmp_path


def test_absolute_path_with_traversal_is_rejected_even_if_it_normalizes_inside_root(tmp_path):
    root = tmp_path / "root"; approved = write_task(root)
    persisted = str(root / "outputs" / "tasks" / TASK_ID / ".." / TASK_ID / "approved.md")
    assert approved.is_file()
    with pytest.raises(PersistedTaskPathError):
        resolve_persisted_task_path(persisted, root)


def test_absolute_path_inside_root_remains_compatible(tmp_path):
    root = tmp_path / "root"; approved = write_task(root)
    assert resolve_persisted_task_path(str(approved), root) == approved.resolve()


def test_bot_main_passes_cli_task_path_root_to_artifact_service(tmp_path, monkeypatch):
    import asyncio
    import src.wecom_bot as bot
    captured = {}

    class FakeClient:
        def __init__(self, _options): pass
        def on(self, *_args): pass
        async def connect_async(self): raise RuntimeError("stop after construction")

    class FakeArtifacts:
        def __init__(self, *_args, **kwargs): captured["task_path_root"] = kwargs["task_path_root"]

    monkeypatch.setattr(bot, "_get_env", lambda _key: "x")
    monkeypatch.setattr(bot, "WSClientOptions", lambda **_kwargs: object())
    monkeypatch.setattr(bot, "WSClient", FakeClient)
    monkeypatch.setattr(bot, "SQLiteTaskStore", lambda _path: object())
    monkeypatch.setattr(bot, "TaskService", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(bot, "ArtifactService", FakeArtifacts)
    monkeypatch.setattr(bot, "WeComAgentAdapter", lambda *_args, **_kwargs: object())
    with pytest.raises(RuntimeError, match="stop after construction"):
        asyncio.run(bot.main(db_path=tmp_path / "db.sqlite", output_root=tmp_path / "tasks", artifact_output_root=tmp_path / "jobs", task_path_root=tmp_path, disable_proposal=True))
    assert captured["task_path_root"] == tmp_path
