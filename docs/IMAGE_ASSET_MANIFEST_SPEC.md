# Shared Image Asset Manifest V1

`assets_manifest.json` is a versioned, read-only inventory generated from a task Markdown file and its task root. It is the common handoff contract for future Word, PPT, WeCom Media, and Evaluation Framework work; it does not change their current flows.

## Contract

The root contains `schema_version` (`image-asset-manifest/v1`), generation metadata, a relative source Markdown identity and SHA-256, stable ordered `assets`, a summary, and V1 limitations. No local absolute path is persisted. Each asset carries a repeatable `asset_id`, normalized task-relative path, reference metadata and count, content SHA-256, file metadata, decoded image attributes, status, warnings, duplicate link, and `word_compatible`, `ppt_compatible`, and `wecom_compatible` booleans.

V1 accepts decoded PNG, JPEG, GIF, BMP, TIFF and WEBP as inspectable source images. Current local Word/PPT compatibility is PNG/JPEG/GIF/BMP/TIFF; WeCom compatibility is conservatively PNG/JPEG/GIF. SVG is never parsed or executed and is `UNSUPPORTED_FORMAT`.

## Safety and status

Only regular files confined to the task root are considered. Absolute, drive, UNC, URL, `file://`, NUL-containing, traversal, reserved-device, escaping symlink/junction and directory references are `UNSAFE_PATH`. Empty, undecodable, oversized or decompression-risk files are not valid. Extension/magic disagreement is retained as `EXTENSION_MISMATCH`. SHA-256 exact duplicates link through `duplicate_of`; no perceptual duplicate detection is attempted.

The default low-source-resolution risk is an edge below 300 px or fewer than 90,000 total pixels. It is a source-risk warning, not a claim about final page quality; thresholds are in `AssetValidationProfile`.

## Consumer boundary

Future Word/PPT consumers should obtain a safe local file only by resolving a `VALID` compatible manifest asset under their task root. WeCom Media must send only `VALID` and `wecom_compatible` assets. The Evaluation Framework may read the manifest to evaluate completeness. Semantic image choice, high-definition regeneration, OCR, upload and all renderer integrations are outside V1.

## CLI

`uv run python scripts/build_image_asset_manifest.py --markdown <approved.md> --task-root <task_dir> --output-dir <report_dir>` writes `assets_manifest.json`, `assets_validation_report.md`, and a compact `acceptance_summary.json` that records the source hash before/after its read-only run. `--include-unreferenced` adds eligible task images not mentioned by Markdown. `--deterministic` fixes the timestamp for reproducible tests. Fatal missing, unsafe or corrupt assets exit 1; warnings exit 0 unless `--strict`; parameter errors exit 2.
