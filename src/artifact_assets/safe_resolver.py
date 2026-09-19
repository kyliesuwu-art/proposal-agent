"""Task-root confined local file resolver; it never creates or follows escapes."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import unquote


_DEVICE = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.I)
_DRIVE = re.compile(r"^[A-Za-z]:")


class PathSafetyError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedTaskImage:
    path: Path
    normalized_relative_path: str


def _fail(message: str) -> None:
    raise PathSafetyError(message)


def resolve_task_image(task_root: str | Path, reference: str) -> ResolvedTaskImage:
    root = Path(task_root).resolve(strict=True)
    raw = unquote(reference)
    if not raw or "\x00" in raw or raw.lower().startswith(("file:", "http:", "https:")):
        _fail("non-local image reference")
    if raw.startswith(("/", "\\")) or _DRIVE.match(raw):
        _fail("absolute, drive, or UNC image reference")
    portable = raw.replace("\\", "/")
    parts = portable.split("/")
    if any(part in {"", ".", ".."} for part in parts) or any(_DEVICE.match(part) for part in parts):
        _fail("unsafe image path component")
    relative = Path(*parts)
    candidate = root / relative
    # A symlink itself is refused, even one currently pointing inside the task.
    if candidate.is_symlink():
        _fail("symlink image reference")
    resolved = candidate.resolve(strict=False)
    try:
        normalized = resolved.relative_to(root).as_posix()
    except ValueError:
        _fail("image path escapes task root")
    if candidate.exists() and not candidate.is_file():
        _fail("image reference is not a regular file")
    return ResolvedTaskImage(candidate, normalized)
