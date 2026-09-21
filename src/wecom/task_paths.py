"""Safe resolution for Task paths persisted in SQLite."""
from __future__ import annotations

from pathlib import Path, PureWindowsPath


class PersistedTaskPathError(ValueError):
    """A persisted Task path is outside its configured trust root or unsafe."""


def _reject_unsafe_persisted_syntax(raw: str, persisted: Path) -> None:
    windows = PureWindowsPath(raw)
    if not raw or "\x00" in raw:
        raise PersistedTaskPathError("persisted Task path is invalid")
    if raw.startswith(("\\\\", "//")):
        raise PersistedTaskPathError("persisted Task path must not use UNC syntax")
    if any(part in {"", ".", ".."} for part in persisted.parts):
        raise PersistedTaskPathError("persisted Task path traversal is not allowed")
    if not persisted.is_absolute() and (windows.drive or persisted.drive):
        raise PersistedTaskPathError("relative persisted Task path must not use a drive")


def resolve_persisted_task_path(persisted_path: str, task_path_root: str | Path) -> Path:
    """Resolve one persisted approved Markdown path inside an explicit root."""
    root_input = Path(task_path_root)
    if not root_input.is_dir():
        raise PersistedTaskPathError("task path root must be an existing directory")
    root = root_input.resolve(strict=True)
    raw = str(persisted_path)
    persisted = Path(raw)
    _reject_unsafe_persisted_syntax(raw, persisted)
    if persisted.is_absolute():
        candidate = persisted
    else:
        candidate = root / persisted
    try:
        lexical_relative = candidate.relative_to(root)
    except ValueError as exc:
        raise PersistedTaskPathError("persisted Task path escapes task path root") from exc
    traversal = root
    for part in lexical_relative.parts:
        traversal = traversal / part
        if traversal.is_symlink():
            raise PersistedTaskPathError("persisted Task path may not traverse a symlink")
    if not candidate.exists() or not candidate.is_file():
        raise PersistedTaskPathError("persisted Task path must be an existing regular file")
    if candidate.suffix.lower() != ".md":
        raise PersistedTaskPathError("persisted Task path must be a Markdown file")
    if candidate.stat().st_size <= 0:
        raise PersistedTaskPathError("persisted Task Markdown must be non-empty")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PersistedTaskPathError("persisted Task path escapes task path root") from exc
    return resolved

