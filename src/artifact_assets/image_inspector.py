"""Decode and inspect raster images without executing active content."""
from __future__ import annotations

import hashlib
from pathlib import Path
import warnings

from PIL import Image, UnidentifiedImageError

from .models import FORMAT_EXTENSIONS, MIME_TYPES, PPT_FORMATS, WECOM_FORMATS, WORD_FORMATS, AssetValidationProfile


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_image(path: Path, profile: AssetValidationProfile) -> dict:
    size = path.stat().st_size
    if size == 0:
        return {"file_size": 0, "sha256": hashlib.sha256(b"").hexdigest(), "error": "EMPTY_FILE"}
    if size > profile.max_file_size_bytes:
        return {"file_size": size, "sha256": sha256_file(path), "error": "FILE_TOO_LARGE"}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                detected_format = image.format or "UNKNOWN"
                width, height = image.size
                if width * height > profile.max_pixels:
                    return {"file_size": size, "sha256": sha256_file(path), "error": "PIXEL_LIMIT"}
                image.verify()
            with Image.open(path) as image:
                image.load()
                mode = image.mode
                dpi = image.info.get("dpi")
                has_alpha = "A" in mode or "transparency" in image.info
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        return {"file_size": size, "sha256": sha256_file(path), "error": "CORRUPT"}
    extension = path.suffix.lower()
    return {
        "file_size": size, "sha256": sha256_file(path), "detected_format": detected_format,
        "mime_type": MIME_TYPES.get(detected_format), "width_px": width, "height_px": height,
        "aspect_ratio": round(width / height, 6) if height else None, "color_mode": mode,
        "has_alpha": has_alpha, "dpi": list(dpi) if isinstance(dpi, tuple) else dpi,
        "extension_matches": extension in FORMAT_EXTENSIONS.get(detected_format, set()),
        "word_compatible": detected_format in WORD_FORMATS,
        "ppt_compatible": detected_format in PPT_FORMATS,
        "wecom_compatible": detected_format in WECOM_FORMATS,
    }
