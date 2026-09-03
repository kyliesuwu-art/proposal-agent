"""Offline recovery of page records from existing MinerU debug ZIP caches."""

from __future__ import annotations

import hashlib
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from src.adapters.parser import MinerUParser
from src.domain.models import DocumentIdentity
from src.domain.identity import DOCUMENT_ID_NAMESPACE


SUPPORTED_CACHE_EXTENSIONS = {".pdf", ".pptx", ".docx", ".doc"}


@dataclass(frozen=True)
class CachedParse:
    identity: DocumentIdentity
    pages: list[dict]
    origin_extension: str
    source_key_origin: str


def recover_debug_zip(zip_path: str | Path, *, recover_pages: bool = True) -> CachedParse:
    """Recover cache identity and, when requested, indexable pages without extraction.

    Dry-run callers can validate the exact identity/source-key rules without invoking
    page rendering (which may emit warnings for unsupported MinerU block types).
    """
    path = Path(zip_path)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        origins = [name for name in names if "_origin." in name]
        content_lists = [name for name in names if name.endswith("_content_list.json")]
    if not origins or not content_lists:
        raise ValueError("缓存缺少 _origin.<ext> 或 content_list.json，不能可靠恢复")
    extension = Path(origins[0]).suffix.lower()
    if extension not in SUPPORTED_CACHE_EXTENSIONS:
        raise ValueError(f"缓存原始格式不受支持: {extension}")

    # MinerU stores a UUID origin name, so the ZIP filename is the only human-readable
    # source label.  Use it only when its extension agrees with _origin.<ext>.
    zip_stem = path.name.removesuffix(".zip")
    if Path(zip_stem).suffix.lower() == extension:
        source_key = f"cache/{zip_stem}"
        source_key_origin = "zip_filename_matched_origin_extension"
    else:
        source_key = f"cache/{path.stem}{extension}"
        source_key_origin = "safe_cache_filename_fallback"
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    identity = DocumentIdentity(
        source_key=source_key,
        document_id=f"doc_{uuid.uuid5(DOCUMENT_ID_NAMESPACE, source_key).hex}",
        content_hash=content_hash,
        version_id=f"ver_cache_sha256_{content_hash}",
    )
    pages: list[dict] = []
    if recover_pages:
        pages = MinerUParser.preview_debug_zip(path, source_file=source_key)
        for page in pages:
            page["page_number"] = page["slide_number"]
            page["cache_source_key_origin"] = source_key_origin
    return CachedParse(identity, pages, extension, source_key_origin)
