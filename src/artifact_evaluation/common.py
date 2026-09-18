from __future__ import annotations
import hashlib
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from .models import CheckResult, EvaluationStatus, Severity

MAX_FILE_SIZE = 250 * 1024 * 1024
@dataclass(frozen=True)
class OoxmlZipSafetyPolicy:
    max_members: int = 4096
    max_member_bytes: int = 64 * 1024 * 1024
    max_total_bytes: int = 256 * 1024 * 1024
    max_compression_ratio: int = 100
    allowed_compression: frozenset[int] = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA})
    chunk_size: int = 64 * 1024

DEFAULT_OOXML_ZIP_POLICY = OoxmlZipSafetyPolicy()
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

def zip_is_safe(path: Path, expected: str, policy: OoxmlZipSafetyPolicy = DEFAULT_OOXML_ZIP_POLICY) -> tuple[bool, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist(); names = [item.filename for item in infos]
            if expected not in names: return False, f"missing {expected}"
            if len(infos) > policy.max_members: return False, "too_many_members"
            total = 0; normalized = set()
            for item in infos:
                name = item.filename.replace("\\", "/")
                normalized_name = name.casefold().rstrip("/")
                if not normalized_name or normalized_name in normalized: return False, "duplicate_member"
                normalized.add(normalized_name)
                if name.startswith(("/", "\\")) or ":" in name.split("/")[0] or ".." in Path(name).parts: return False, "unsafe_archive_member"
                if item.flag_bits & 0x1: return False, "encrypted_member"
                if item.compress_type not in policy.allowed_compression: return False, "unsupported_compression"
                if item.file_size < 0 or item.file_size > policy.max_member_bytes: return False, "member_too_large"
                total += item.file_size
                if total > policy.max_total_bytes: return False, "total_uncompressed_too_large"
                if item.file_size and (not item.compress_size or item.file_size / item.compress_size > policy.max_compression_ratio): return False, "suspicious_compression_ratio"
            actual_total = 0
            for item in infos:
                actual_member = 0
                with archive.open(item, "r") as stream:
                    while block := stream.read(policy.chunk_size):
                        actual_member += len(block); actual_total += len(block)
                        if actual_member > policy.max_member_bytes: return False, "actual_member_too_large"
                        if actual_total > policy.max_total_bytes: return False, "actual_total_too_large"
        return True, "valid OOXML package"
    except (OSError, EOFError, RuntimeError, zipfile.BadZipFile) as exc: return False, "corrupt_or_truncated_zip"

def text_counts(text: str) -> tuple[int, int, int]:
    return len(INTERNAL.findall(text)), len(SOURCE_LEAK.findall(text)), len(ABS_PATH.findall(text))
