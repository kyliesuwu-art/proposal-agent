"""Safe planning and dry-run validation for parser-version-targeted reindexing."""
from __future__ import annotations

import hashlib, json, sqlite3, zipfile
from collections import defaultdict
from pathlib import Path

from src.adapters.parser import INDEX_SCHEMA_VERSION, PARSER_VERSION
from src.cache_recovery import recover_debug_zip

SPECIAL = {"aside_text", "chart", "equation", "index"}

def _manifest(path: Path) -> dict[str, dict]:
    uri = "file:" + path.resolve().as_posix() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        return {row["zip_path"]: dict(row) for row in db.execute("SELECT * FROM cache_documents")}

def _special(zip_path: Path) -> tuple[dict[str, list[int]], int, int]:
    found: dict[str, set[int]] = defaultdict(set); image_count = 0
    with zipfile.ZipFile(zip_path) as z:
        member = next(name for name in z.namelist() if name.endswith("content_list.json"))
        blocks = json.loads(z.read(member))
    for block in blocks:
        kind = block.get("type"); page = int(block.get("page_idx", 0)) + 1
        if kind in SPECIAL: found[kind].add(page)
        if kind in {"image", "chart"} and block.get("img_path"): image_count += 1
    return {key: sorted(value) for key, value in found.items()}, len(blocks), image_count

def build_plan(cache_dir: str | Path, source_db: str | Path) -> dict:
    cache, db = Path(cache_dir).resolve(), Path(source_db).resolve()
    records = _manifest(db / "cache_ingest_manifest.sqlite3")
    legacy, failed, duplicates = [], [], []
    for archive in sorted(cache.glob("*.zip")):
        rel = archive.name; row = records.get(rel)
        if not row: continue
        recovered = recover_debug_zip(archive, recover_pages=False)
        base = {"document_id": recovered.identity.document_id, "source_file": recovered.identity.source_key,
                "cache_zip": rel, "sha256": recovered.identity.content_hash, "manifest_status": row["status"],
                "current_parser_version": row.get("parser_version") or "legacy", "target_parser_version": PARSER_VERSION}
        if row["status"] == "duplicate":
            duplicates.append({**base, "canonical_cache_zip": row.get("duplicate_of")})
        elif row["status"] == "failed":
            try:
                special, pages, images = _special(archive); readable = True
            except Exception as exc:
                special, pages, images, readable = {}, 0, 0, False
            failed.append({**base, "failure_stage": row.get("stage"), "failure_reason": row.get("error") or "not_recorded",
                           "zip_complete": readable, "mineru_json_readable": readable, "has_sidecar": (db / "sidecars" / recovered.identity.document_id).exists(),
                           "classification": "retryable" if readable else "data_or_cache_error", "recommended_action": "retry in isolated candidate after root cause review"})
        elif row["status"] == "completed":
            special, pages, images = _special(archive)
            if special and (row.get("parser_version") or "legacy") != PARSER_VERSION:
                legacy.append({**base, "reasons": sorted(special), "affected_pages": sorted({p for values in special.values() for p in values}),
                               "block_pages": special, "estimated_pages": row.get("page_count") or pages, "estimated_image_candidates": images})
    return {"schema_version": 1, "source_db": str(db), "target_parser_version": PARSER_VERSION,
            "index_schema_version": INDEX_SCHEMA_VERSION, "reprocess_legacy": legacy,
            "failed_retry_candidates": failed, "duplicates": duplicates}

def write_plan(plan: dict, output_dir: str | Path) -> tuple[Path, Path, Path]:
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    plan_path, failed_path = out / "reindex_plan.json", out / "failed_documents.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    failed_path.write_text(json.dumps(plan["failed_retry_candidates"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = out / "reindex_plan.md"
    md.write_text("\n".join(["# 定向索引升级计划", "", f"- legacy：{len(plan['reprocess_legacy'])}", f"- failed：{len(plan['failed_retry_candidates'])}", f"- duplicate：{len(plan['duplicates'])}", "", "仅 dry-run；候选库必须独立、可恢复且经用户授权后才可创建。", ""]), encoding="utf-8")
    return plan_path, md, failed_path

def dry_run(plan_path: str | Path, source_db: str | Path, target_db: str | Path, *, limit: int | None = None) -> dict:
    plan, source, target = json.loads(Path(plan_path).read_text(encoding="utf-8")), Path(source_db).resolve(), Path(target_db).resolve()
    if source == target: raise ValueError("source-db 与 target-db 不得相同")
    if target.exists(): raise ValueError("target-db 已存在；dry-run 不会创建或复用候选库")
    selected = plan["reprocess_legacy"][:limit] if limit else plan["reprocess_legacy"]
    return {"mode": "dry-run", "selected": len(selected), "failed_retry_candidates": len(plan["failed_retry_candidates"]),
            "duplicates_excluded": len(plan["duplicates"]), "estimated_pages": sum(x["estimated_pages"] for x in selected),
            "estimated_image_candidates": sum(x["estimated_image_candidates"] for x in selected), "embedding_calls": 0,
            "database_writes": 0, "network_calls": 0}

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", required=True); parser.add_argument("--source-db", required=True); parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print("\n".join(map(str, write_plan(build_plan(args.cache_dir, args.source_db), args.output))) )
