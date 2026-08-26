"""纯本地的文件级身份生成，不依赖 Chroma、MinerU 或当前工作目录。"""

from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path

from src.domain.models import DocumentIdentity


DOCUMENT_ID_NAMESPACE = uuid.UUID("c580f010-8da3-4f91-8d59-b80b37af1ce5")
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:/")


def _normalized_path(value: str | Path) -> str:
    raw = str(value).replace("\\", "/")
    raw = re.sub(r"/+", "/", raw)
    return raw.rstrip("/") or "/"


def _is_absolute(value: str) -> bool:
    return value.startswith("/") or bool(_DRIVE_PREFIX.match(value))


def _relative_components(value: str) -> list[str]:
    parts = [part for part in value.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise ValueError("source_key 不允许包含 '..'，请提供位于 source_root 内的文件")
    return parts


def normalize_source_key(source_path: str | Path, source_root: str | Path | None = None) -> str:
    """生成以 ``/`` 分隔、无机器绝对路径的逻辑来源相对路径。"""
    source = _normalized_path(source_path)
    if source_root is None:
        if _is_absolute(source):
            raise ValueError("绝对 source_path 必须显式提供 source_root，不能写入机器路径")
        parts = _relative_components(source)
    else:
        root = _normalized_path(source_root)
        if _is_absolute(source):
            comparison_source = source.casefold() if _DRIVE_PREFIX.match(source) else source
            comparison_root = root.casefold() if _DRIVE_PREFIX.match(root) else root
            prefix = comparison_root.rstrip("/") + "/"
            if not comparison_source.startswith(prefix):
                raise ValueError("source_path 必须位于 source_root 内")
            relative = source[len(root.rstrip("/")) + 1:]
            parts = _relative_components(relative)
        else:
            parts = _relative_components(source)
    if not parts:
        raise ValueError("source_key 不能为空")
    return "/".join(parts)


def compute_content_hash(file_path: str | Path) -> str:
    """一次流式读取计算文件级 SHA-256；不按页重复计算。"""
    digest = hashlib.sha256()
    with Path(file_path).open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def build_document_identity(file_path: str | Path, source_root: str | Path | None = None) -> DocumentIdentity:
    """构建未来入库使用的身份；未给根时明确采用单文件父目录回退。"""
    path = Path(file_path)
    logical_root = Path(source_root) if source_root is not None else path.parent
    source_key = normalize_source_key(path, logical_root)
    content_hash = compute_content_hash(path)
    return DocumentIdentity(
        source_key=source_key,
        document_id=f"doc_{uuid.uuid5(DOCUMENT_ID_NAMESPACE, source_key).hex}",
        content_hash=content_hash,
        version_id=f"ver_sha256_{content_hash}",
    )
