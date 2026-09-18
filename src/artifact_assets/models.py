"""Public data model constants for the shared image asset contract."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class AssetStatus(StrEnum):
    VALID = "VALID"
    MISSING = "MISSING"
    UNSAFE_PATH = "UNSAFE_PATH"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    CORRUPT = "CORRUPT"
    EXTENSION_MISMATCH = "EXTENSION_MISMATCH"
    DUPLICATE = "DUPLICATE"
    LOW_RESOLUTION = "LOW_RESOLUTION"
    EMPTY_FILE = "EMPTY_FILE"


@dataclass(frozen=True)
class AssetValidationProfile:
    """Source-level checks only; layout consumers apply their own sizing rules."""
    low_resolution_edge_px: int = 300
    low_resolution_total_pixels: int = 90_000
    max_file_size_bytes: int = 50 * 1024 * 1024
    max_pixels: int = 100_000_000
    supported_formats: frozenset[str] = field(default_factory=lambda: frozenset({
        "PNG", "JPEG", "GIF", "BMP", "TIFF", "WEBP",
    }))


FORMAT_EXTENSIONS = {
    "PNG": {".png"}, "JPEG": {".jpg", ".jpeg"}, "GIF": {".gif"},
    "BMP": {".bmp"}, "TIFF": {".tif", ".tiff"}, "WEBP": {".webp"},
}
MIME_TYPES = {
    "PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif",
    "BMP": "image/bmp", "TIFF": "image/tiff", "WEBP": "image/webp",
}
# Current local renderers use python-docx/python-pptx add_picture.  SVG is
# intentionally excluded: V1 never evaluates its XML, scripts, or references.
WORD_FORMATS = frozenset({"PNG", "JPEG", "GIF", "BMP", "TIFF"})
PPT_FORMATS = frozenset({"PNG", "JPEG", "GIF", "BMP", "TIFF"})
WECOM_FORMATS = frozenset({"PNG", "JPEG", "GIF"})
