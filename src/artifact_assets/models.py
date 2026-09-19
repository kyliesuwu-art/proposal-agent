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
    max_width_px: int = 32_768
    max_height_px: int = 32_768
    max_frames: int = 256
    max_cumulative_frame_pixels: int = 200_000_000
    resource_policy_version: str = "image-resource-policy/v1"
    supported_formats: frozenset[str] = field(default_factory=lambda: frozenset({
        "PNG", "JPEG", "GIF", "BMP", "TIFF", "WEBP",
    }))

    def resource_policy(self) -> dict[str, int | str]:
        return {"version": self.resource_policy_version, "max_file_size_bytes": self.max_file_size_bytes,
                "max_width_px": self.max_width_px, "max_height_px": self.max_height_px,
                "max_frame_pixels": self.max_pixels, "max_frames": self.max_frames,
                "max_cumulative_frame_pixels": self.max_cumulative_frame_pixels}


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
