"""Build stable, non-mutating image manifests from one task Markdown file."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
from pathlib import Path

from .image_inspector import inspect_image, sha256_file
from .markdown_parser import MarkdownImageReference, extract_markdown_images
from .models import AssetStatus, AssetValidationProfile
from .safe_resolver import PathSafetyError, resolve_task_image

SCHEMA_VERSION = "image-asset-manifest/v1"
_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".svg"}


def _asset_id(digest: str | None, relative: str) -> str:
    key = f"{digest or 'unavailable'}:{relative}".encode("utf-8")
    return "asset-" + hashlib.sha256(key).hexdigest()[:20]


def _safe_reference(reference: str) -> str:
    if reference.startswith(("/", "\\")) or len(reference) > 1 and reference[1] == ":":
        return "[unsafe absolute reference omitted]"
    return reference.replace("\x00", "[NUL]")


def _base_record(relative: str, refs: list[MarkdownImageReference], referenced: bool) -> dict:
    return {
        "asset_id": _asset_id(None, relative), "source_markdown_path": None,
        "markdown_reference": _safe_reference(refs[0].reference) if refs else None,
        "alt_text": refs[0].alt_text if refs else None,
        "normalized_relative_path": relative, "sha256": None, "file_size": None,
        "extension": Path(relative).suffix.lower(), "detected_format": None, "mime_type": None,
        "width_px": None, "height_px": None, "aspect_ratio": None, "color_mode": None,
        "has_alpha": None, "dpi": None, "status": AssetStatus.VALID.value, "warnings": [],
        "frame_count": None, "cumulative_frame_pixels": None,
        "duplicate_of": None, "referenced": referenced, "reference_count": len(refs),
        "line_numbers": [item.line_number for item in refs],
        "word_compatible": False, "ppt_compatible": False, "wecom_compatible": False,
    }


def _status_for(info: dict, profile: AssetValidationProfile) -> tuple[str, list[str]]:
    error = info.get("error")
    if error == "EMPTY_FILE": return AssetStatus.EMPTY_FILE.value, []
    if error: return AssetStatus.CORRUPT.value, [error]
    if info["detected_format"] not in profile.supported_formats:
        return AssetStatus.UNSUPPORTED_FORMAT.value, []
    if not info["extension_matches"]:
        return AssetStatus.EXTENSION_MISMATCH.value, []
    warnings = []
    if (info["width_px"] < profile.low_resolution_edge_px or info["height_px"] < profile.low_resolution_edge_px
            or info["width_px"] * info["height_px"] < profile.low_resolution_total_pixels):
        warnings.append(AssetStatus.LOW_RESOLUTION.value)
        return AssetStatus.LOW_RESOLUTION.value, warnings
    return AssetStatus.VALID.value, warnings


def build_manifest(markdown_path: str | Path, task_root: str | Path, *, include_unreferenced: bool = False,
                   deterministic: bool = False, profile: AssetValidationProfile | None = None) -> dict:
    profile = profile or AssetValidationProfile()
    root = Path(task_root).resolve(strict=True)
    markdown = Path(markdown_path).resolve(strict=True)
    try: source_relative = markdown.relative_to(root).as_posix()
    except ValueError as exc: raise ValueError("markdown must be inside task root") from exc
    text = markdown.read_text(encoding="utf-8")
    grouped: dict[str, list[MarkdownImageReference]] = defaultdict(list)
    unsafe: list[MarkdownImageReference] = []
    paths: dict[str, Path] = {}
    for reference in extract_markdown_images(text):
        try:
            resolved = resolve_task_image(root, reference.normalized_reference)
        except PathSafetyError:
            unsafe.append(reference); continue
        grouped[resolved.normalized_relative_path].append(reference)
        paths[resolved.normalized_relative_path] = resolved.path
    records: list[dict] = []
    for relative in sorted(grouped):
        record = _base_record(relative, grouped[relative], True); record["source_markdown_path"] = source_relative
        path = paths[relative]
        if not path.exists(): record["status"] = AssetStatus.MISSING.value
        elif path.suffix.lower() not in _EXTENSIONS or path.suffix.lower() == ".svg":
            record.update({"file_size": path.stat().st_size, "sha256": sha256_file(path)})
            record["asset_id"] = _asset_id(record["sha256"], relative)
            record["status"] = AssetStatus.UNSUPPORTED_FORMAT.value
        else:
            info = inspect_image(path, profile); record.update({key: value for key, value in info.items() if key not in {"error", "extension_matches"}})
            record["status"], record["warnings"] = _status_for(info, profile)
            record["asset_id"] = _asset_id(record["sha256"], relative)
        records.append(record)
    for index, reference in enumerate(unsafe, 1):
        record = _base_record(f"[unsafe-{index}]", [reference], True); record["source_markdown_path"] = source_relative
        record["status"] = AssetStatus.UNSAFE_PATH.value; records.append(record)
    if include_unreferenced:
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in _EXTENSIONS: continue
            relative = path.relative_to(root).as_posix()
            if relative in grouped: continue
            record = _base_record(relative, [], False)
            if path.suffix.lower() == ".svg":
                record.update({"file_size": path.stat().st_size, "sha256": sha256_file(path)})
                record["asset_id"] = _asset_id(record["sha256"], relative)
                record["status"] = AssetStatus.UNSUPPORTED_FORMAT.value
            else:
                info = inspect_image(path, profile); record.update({key: value for key, value in info.items() if key not in {"error", "extension_matches"}})
                record["status"], record["warnings"] = _status_for(info, profile); record["asset_id"] = _asset_id(record["sha256"], relative)
            records.append(record)
    digest_owner: dict[str, dict] = {}
    for record in sorted(records, key=lambda item: item["normalized_relative_path"]):
        digest = record["sha256"]
        if not digest or record["status"] not in {AssetStatus.VALID.value, AssetStatus.LOW_RESOLUTION.value}: continue
        if digest in digest_owner:
            record["duplicate_of"] = digest_owner[digest]["asset_id"]
            record["warnings"].append(AssetStatus.DUPLICATE.value)
            if record["status"] == AssetStatus.VALID.value: record["status"] = AssetStatus.DUPLICATE.value
        else: digest_owner[digest] = record
    records.sort(key=lambda item: item["normalized_relative_path"])
    summary = {
        "total_references": sum(record["reference_count"] for record in records), "unique_assets": len(records),
        "valid_assets": sum(item["status"] == AssetStatus.VALID.value for item in records),
        "missing_assets": sum(item["status"] == AssetStatus.MISSING.value for item in records),
        "unsafe_assets": sum(item["status"] == AssetStatus.UNSAFE_PATH.value for item in records),
        "corrupt_assets": sum(item["status"] in {AssetStatus.CORRUPT.value, AssetStatus.EMPTY_FILE.value} for item in records),
        "unsupported_assets": sum(item["status"] == AssetStatus.UNSUPPORTED_FORMAT.value for item in records),
        "duplicate_assets": sum(item["duplicate_of"] is not None for item in records),
        "low_resolution_assets": sum(AssetStatus.LOW_RESOLUTION.value in item["warnings"] for item in records),
        "unreferenced_assets": sum(not item["referenced"] for item in records),
    }
    return {"schema_version": SCHEMA_VERSION, "generated_at": "1970-01-01T00:00:00Z" if deterministic else datetime.now(timezone.utc).isoformat(), "resource_policy": profile.resource_policy(),
            "task_root_label": root.name, "source_markdown": source_relative, "source_markdown_sha256": sha256_file(markdown),
            "assets": records, "summary": summary,
            "limitations": ["V1 performs source-file validation only, not layout-quality assessment.", "V1 detects exact SHA-256 duplicates only; it does not perform perceptual comparison.", "SVG is intentionally unsupported and is never parsed or executed."]}
