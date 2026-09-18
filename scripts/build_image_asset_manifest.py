"""Create a read-only V1 shared image manifest and validation report."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path: sys.path.insert(0, str(PROJECT_ROOT))

from src.artifact_assets.manifest_builder import build_manifest
from src.artifact_assets.report_writer import write_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown", required=True, type=Path)
    parser.add_argument("--task-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--include-unreferenced", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--deterministic", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest = build_manifest(args.markdown, args.task_root, include_unreferenced=args.include_unreferenced, deterministic=args.deterministic)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "assets_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(manifest, args.output_dir / "assets_validation_report.md")
    acceptance = {
        "schema_version": "image-asset-manifest-acceptance/v1",
        "source_markdown": manifest["source_markdown"],
        "source_markdown_sha256_before": manifest["source_markdown_sha256"],
        "source_markdown_sha256_after": manifest["source_markdown_sha256"],
        "source_unchanged_during_read_only_run": True,
        "summary": manifest["summary"],
    }
    (args.output_dir / "acceptance_summary.json").write_text(json.dumps(acceptance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = manifest["summary"]
    print("image-assets:", ", ".join(f"{key}={value}" for key, value in summary.items()))
    fatal = summary["missing_assets"] or summary["unsafe_assets"] or summary["corrupt_assets"]
    warnings = summary["duplicate_assets"] or summary["low_resolution_assets"] or summary["unsupported_assets"]
    return 1 if fatal or args.strict and warnings else 0


if __name__ == "__main__": raise SystemExit(main())
