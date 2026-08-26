"""通用文档领域对象与稳定身份工具。"""

from src.domain.identity import build_document_identity, compute_content_hash, normalize_source_key
from src.domain.models import (
    ContentBlock,
    DocumentIdentity,
    DocumentMetadata,
    ImageAsset,
    Page,
    ParseResult,
    SourceDocument,
)

__all__ = [
    "ContentBlock", "DocumentIdentity", "DocumentMetadata", "ImageAsset", "Page",
    "ParseResult", "SourceDocument", "build_document_identity", "compute_content_hash",
    "normalize_source_key",
]
