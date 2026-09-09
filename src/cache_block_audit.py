"""Read-only MinerU cache audit; it never opens Chroma or writes a database."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import zipfile
from collections import Counter, defaultdict
from pathlib import Path


AUDIT_BLOCK_TYPES = ("text", "title", "image", "table", "aside_text", "chart", "equation", "index")
REPROCESS_TYPES = {"aside_text", "chart", "equation", "index"}


def _content_member(names: list[str]) -> str | None:
    return next((name for name in names if name.endswith("content_list.json")), None)


def _sample(block: dict) -> dict:
    """Expose shape only; never put business document text in the audit report."""
    value = {"field_names": sorted(block)[:24]}
    for key in ("page_idx", "bbox"):
        if key in block:
            value[key] = block[key]
    for key in ("img_path", "image_path", "latex", "equation_latex", "chart_caption", "caption", "footnote"):
        raw = block.get(key)
        if raw:
            text = str(raw).replace("\n", " ")
            value[key] = (text[:80] + "…") if len(text) > 80 else text
    for key in ("lines", "spans", "blocks", "items"):
        if key in block:
            raw = block[key]
            value[f"{key}_kind"] = type(raw).__name__
            value[f"{key}_count"] = len(raw) if isinstance(raw, (list, dict)) else 1
    return value


def audit_cache(source_dir: str | Path, *, manifest_path: str | Path | None = None) -> dict:
    root = Path(source_dir)
    counts, documents, pages = Counter(), defaultdict(set), defaultdict(set)
    samples: dict[str, list[dict]] = defaultdict(list)
    affected: dict[str, set[str]] = defaultdict(set)
    failures: list[dict] = []
    archives = sorted(root.glob("*.zip"))
    for archive in archives:
        try:
            with zipfile.ZipFile(archive) as zf:
                member = _content_member(zf.namelist())
                if not member:
                    raise ValueError("content_list.json missing")
                raw = json.loads(zf.read(member).decode("utf-8"))
                if not isinstance(raw, list):
                    raise ValueError("content_list.json is not a list")
        except Exception as exc:  # audit must continue across corrupt archives
            failures.append({"archive": archive.name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        for block in raw:
            if not isinstance(block, dict):
                block = {"type": "malformed", "value": type(block).__name__}
            kind = str(block.get("type") or "unknown")
            counts[kind] += 1
            documents[kind].add(archive.name)
            pages[kind].add((archive.name, int(block.get("page_idx", 0)) + 1))
            if kind in REPROCESS_TYPES:
                affected[kind].add(archive.name)
            if len(samples[kind]) < 3:
                samples[kind].append({"document_id": hashlib.sha256(archive.name.encode()).hexdigest()[:12], **_sample(block)})
    manifest = {"status_counts": {}, "document_count": None, "page_count": None}
    if manifest_path and Path(manifest_path).is_file():
        uri = "file:" + Path(manifest_path).resolve().as_posix() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as db:
            rows = db.execute("SELECT status, COUNT(*) FROM cache_documents GROUP BY status").fetchall()
            manifest["status_counts"] = dict(rows)
            manifest["document_count"] = db.execute("SELECT COUNT(*) FROM cache_documents WHERE status='completed'").fetchone()[0]
            manifest["page_count"] = db.execute("SELECT COALESCE(SUM(page_count), 0) FROM cache_documents WHERE status='completed'").fetchone()[0]
            columns = {row[1] for row in db.execute("PRAGMA table_info(cache_documents)")}
            manifest["version_columns_present"] = {name: name in columns for name in ("parser_version", "index_schema_version")}
            manifest["documents_with_legacy_parser"] = manifest["document_count"] if "parser_version" not in columns else db.execute(
                "SELECT COUNT(*) FROM cache_documents WHERE status='completed' AND (parser_version IS NULL OR parser_version != 'mineru-blocks-v2')"
            ).fetchone()[0]
    per_type = {
        kind: {"count": counts[kind], "document_count": len(documents[kind]), "page_count": len(pages[kind]),
               "samples": samples[kind], "strategy": _strategy(kind)}
        for kind in sorted(set(counts) | set(AUDIT_BLOCK_TYPES))
    }
    return {"schema_version": 1, "zip_total": len(archives), "parsed_zip_total": len(archives) - len(failures),
            "corrupt_zip_total": len(failures), "failures": failures, "block_types": per_type,
            "manifest": manifest, "reprocess_plan": {"parser_version": "mineru-blocks-v2",
            "index_schema_version": "proposal-index-v2", "affected_document_count": len(set().union(*affected.values()) if affected else set()),
            "affected_by_type": {kind: len(value) for kind, value in affected.items()},
            "legacy_manifest_document_count": manifest.get("documents_with_legacy_parser"),
            "action": "dry-run only; parse, embed and validate a document version before any atomic replacement"}}


def _strategy(kind: str) -> str:
    return {"aside_text": "preserve as same-page note", "equation": "latex, text/spans, then image metadata",
            "chart": "preserve image/caption/footnote/bbox as image candidate", "index": "filter TOC; preserve useful index",
            "image": "preserve image candidate", "unknown": "record only; do not silently discard"}.get(kind, "existing parser handling")


def write_audit(report: dict, output_dir: str | Path) -> tuple[Path, Path]:
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    json_path = out / "block_audit.json"; markdown_path = out / "block_audit.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# MinerU 缓存 Block 审计", "", f"- ZIP：{report['zip_total']}；成功解析：{report['parsed_zip_total']}；损坏：{report['corrupt_zip_total']}",
             f"- 未来受影响文档：{report['reprocess_plan']['affected_document_count']}（仅 dry-run）", "", "## Block 统计", "", "| 类型 | 数量 | 文档 | 页面 | 策略 |", "|---|---:|---:|---:|---|"]
    for kind, item in report["block_types"].items():
        lines.append(f"| {kind} | {item['count']} | {item['document_count']} | {item['page_count']} | {item['strategy']} |")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Read-only MinerU cache block audit")
    parser.add_argument("source_dir")
    parser.add_argument("--manifest")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = audit_cache(args.source_dir, manifest_path=args.manifest)
    paths = write_audit(report, args.output)
    print("\n".join(str(path.resolve()) for path in paths))
