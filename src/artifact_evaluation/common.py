from __future__ import annotations
import hashlib
import re
import zipfile
from pathlib import Path
from .models import CheckResult, EvaluationStatus, Severity

MAX_FILE_SIZE = 250 * 1024 * 1024
SAFE_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"}
INTERNAL = re.compile(r"【待确认】|核心提示|来源与依据|(?:来源|图片来源)\s*[：:]|\[S\d+\]|\[IMG\d+\]", re.I)
SOURCE_LEAK = re.compile(r"[^\s/\\]+\.(?:pdf|docx?|pptx?)\s*[,，]?(?:第\s*\d+\s*页|page\s*\d+)", re.I)
ABS_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|/(?:Users|home|tmp)/)")

def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""): h.update(block)
    return h.hexdigest()

def check(code: str, status: EvaluationStatus, severity: Severity, artifact: str, message: str, **kwargs) -> CheckResult:
    evidence = kwargs.pop("evidence", None)
    if evidence: evidence = str(evidence).replace("\n", " ")[:200]
    return CheckResult(code, status, severity, artifact, message, evidence=evidence, **kwargs)

def zip_is_safe(path: Path, expected: str) -> tuple[bool, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if expected not in names: return False, f"missing {expected}"
            if any(name.startswith(("/", "\\")) or ".." in Path(name).parts for name in names): return False, "unsafe archive member"
        return True, "valid OOXML package"
    except (OSError, zipfile.BadZipFile) as exc: return False, type(exc).__name__

def text_counts(text: str) -> tuple[int, int, int]:
    return len(INTERNAL.findall(text)), len(SOURCE_LEAK.findall(text)), len(ABS_PATH.findall(text))
