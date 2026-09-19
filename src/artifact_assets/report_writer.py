"""Human-readable companion report without absolute paths or Markdown body."""
from __future__ import annotations

from pathlib import Path


def write_report(manifest: dict, output_path: str | Path) -> Path:
    output = Path(output_path)
    summary = manifest["summary"]
    lines = ["# Image asset validation report", "", f"Schema: `{manifest['schema_version']}`", f"Source Markdown: `{manifest['source_markdown']}`", "", "## Summary", ""]
    for key, value in summary.items(): lines.append(f"- {key}: {value}")
    lines.extend(["", "## Assets", "", "| Path | Status | Format | Dimensions | References | Warnings |", "|---|---|---|---|---:|---|"])
    for asset in manifest["assets"]:
        dimensions = "" if asset["width_px"] is None else f"{asset['width_px']}×{asset['height_px']}"
        lines.append(f"| `{asset['normalized_relative_path']}` | {asset['status']} | {asset['detected_format'] or ''} | {dimensions} | {asset['reference_count']} | {', '.join(asset['warnings'])} |")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output
