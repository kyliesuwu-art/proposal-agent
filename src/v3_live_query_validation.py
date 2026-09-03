"""Controlled live retrieval acceptance for a rebuilt V3 candidate library.

The only remote action is one DashScope embedding batch for the three fixed
acceptance queries.  It never invokes generation, rewriting, MinerU, or ingest.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import chromadb

from src.adapters.vector_store import DashScopeEmbeddingFunction
from src.cache_batch import EMBEDDING_MODEL_ID, EmbeddingCache
from src.hybrid_v2 import _FTS_TOKEN, fuse_rrf
from src.v3_hnsw_rebuild import COLLECTION, NoRemoteEmbedding


QUERIES = ("智能配电系统", "绿电直连", "工商业储能收益")


def _open_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _fts_terms(query: str) -> list[str]:
    return list(dict.fromkeys(token for token in _FTS_TOKEN.findall(query) if len(token) >= 2))


def _assets(root: Path, record: dict[str, Any]) -> dict[str, Any]:
    paths = [str(image.get("path")) for image in record.get("images", []) if image.get("path")]
    return {"paths": paths, "all_exist": all((root / path).is_file() for path in paths)}


def _summary(record: dict[str, Any], matched_terms: list[str]) -> str:
    text = " ".join(str(record.get(key, "")) for key in ("title", "section_title", "content"))
    return text.replace("\n", " ").strip()[:180]


def _reconcile_running(root: Path, fts: sqlite3.Connection, cache: sqlite3.Connection, collection: Any) -> list[dict[str, Any]]:
    """Close only interrupted target HNSW rows after checking their source data."""
    state_path = root / "hnsw_rebuild_manifest.sqlite3"
    state = sqlite3.connect(state_path)
    state.row_factory = sqlite3.Row
    results: list[dict[str, Any]] = []
    try:
        running = state.execute("SELECT * FROM rebuild_pages WHERE status='running'").fetchall()
        for row in running:
            item = dict(row)
            payload_row = fts.execute("SELECT payload FROM pages WHERE page_id=?", (item["page_id"],)).fetchone()
            page = json.loads(payload_row["payload"]) if payload_row else None
            sidecar = bool(page) and (root / "sidecars" / page["document_id"] / page["version_id"] / "index.json").is_file()
            cached = bool(page) and bool(cache.execute(
                "SELECT 1 FROM embeddings WHERE model_id=? AND text_hash=?",
                (EMBEDDING_MODEL_ID, EmbeddingCache._hash(str(page["retrieval_text"]))),
            ).fetchone())
            chroma_ids = collection.get(ids=[item["page_id"]], include=[])["ids"]
            chroma = bool(chroma_ids)
            complete = sidecar and bool(page) and cached and chroma
            new_status = "completed" if complete else "interrupted"
            new_stage = "completed" if complete else str(item.get("stage") or "unknown")
            error = None if complete else "reconciliation incomplete: " + ", ".join(
                name for name, okay in (("sidecar", sidecar), ("fts", bool(page)), ("embedding_cache", cached), ("chroma", chroma)) if not okay
            )
            with state:
                state.execute(
                    "UPDATE rebuild_pages SET status=?, error=? WHERE page_id=?",
                    (new_status, error, item["page_id"]),
                )
            results.append({
                "page_id": item["page_id"], "source_key": page["source_key"] if page else None, "file_name": page["source_file"] if page else None,
                "page_number": page["page_number"] if page else None, "prior_stage": item["status"],
                "last_updated_observed": datetime.fromtimestamp(state_path.stat().st_mtime, tz=timezone.utc).isoformat(),
                "components": {"sidecar": sidecar, "fts_page": bool(page), "embedding_cache": cached, "chroma": chroma},
                "final_status": new_status, "final_stage": new_stage, "missing": [] if complete else error.split(": ", 1)[1].split(", "),
            })
    finally:
        state.close()
    return results


def _source_running(source_root: Path, root: Path, fts: sqlite3.Connection, cache: sqlite3.Connection, collection: Any) -> list[dict[str, Any]]:
    """Inspect the original manifest read-only; its state is deliberately not edited."""
    state = _open_readonly(source_root / "cache_ingest_manifest.sqlite3")
    try:
        results = []
        for row in state.execute("SELECT * FROM cache_documents WHERE status='running'"):
            item = dict(row)
            pages = [json.loads(value["payload"]) for value in fts.execute(
                "SELECT payload FROM pages WHERE document_id=? AND version_id=?", (item["document_id"], item["version_id"])
            )]
            sidecar = (root / "sidecars" / item["document_id"] / item["version_id"] / "index.json").is_file()
            cached = bool(pages) and all(cache.execute("SELECT 1 FROM embeddings WHERE model_id=? AND text_hash=?", (EMBEDDING_MODEL_ID, EmbeddingCache._hash(page["retrieval_text"]))).fetchone() for page in pages)
            chroma_ids = collection.get(ids=[page["page_id"] for page in pages], include=[])["ids"] if pages else []
            complete = sidecar and bool(pages) and cached and len(chroma_ids) == len(pages)
            results.append({"source_key": item["source_key"], "file_name": Path(item["zip_path"]).name, "stage": item["stage"], "last_updated_observed": datetime.fromtimestamp((source_root / "cache_ingest_manifest.sqlite3").stat().st_mtime, tz=timezone.utc).isoformat(), "components": {"sidecar": sidecar, "fts_pages": len(pages), "embedding_cache": cached, "chroma_pages": len(chroma_ids)}, "complete_in_rebuilt": complete, "source_manifest_unchanged": True})
        return results
    finally:
        state.close()


def validate(root: Path, source_root: Path, prior_external_requests: int = 0) -> dict[str, Any]:
    root = root.resolve()
    fts = _open_readonly(root / "hybrid_lexical.sqlite3")
    cache = _open_readonly(source_root.resolve() / "embedding_cache.sqlite3")
    client = chromadb.PersistentClient(path=str(root / "chroma_v2"))
    collection = client.get_collection(COLLECTION, embedding_function=NoRemoteEmbedding())
    try:
        running = _reconcile_running(root, fts, cache, collection)
        source_running = _source_running(source_root.resolve(), root, fts, cache, collection)
        # Chroma's ``get(include=["embeddings"])`` requests a compactor backfill in
        # this installed version.  The source cache is the authoritative vector
        # material used by the rebuild, so read its dimension directly without
        # touching that unrelated Chroma endpoint.
        cached_vector = cache.execute(
            "SELECT vector_json FROM embeddings WHERE model_id=? LIMIT 1", (EMBEDDING_MODEL_ID,)
        ).fetchone()
        document_dimensions = len(json.loads(cached_vector["vector_json"])) if cached_vector else 0
        # Exactly one invocation with exactly these three strings.  With three inputs,
        # DashScopeEmbeddingFunction performs one successful HTTP batch request.
        query_vectors = DashScopeEmbeddingFunction()(list(QUERIES))
        if len(query_vectors) != len(QUERIES):
            raise RuntimeError("DashScope query vector count mismatch")
        if any(len(vector) != document_dimensions for vector in query_vectors):
            raise RuntimeError("DashScope query vector dimension does not match rebuilt Chroma")
        queries: list[dict[str, Any]] = []
        for query, vector in zip(QUERIES, query_vectors):
            semantic: list[dict[str, Any]] = []
            semantic_error = None
            try:
                semantic_raw = collection.query(query_embeddings=[vector], n_results=15, include=["distances"])
                for rank, (page_id, distance) in enumerate(zip(semantic_raw["ids"][0], semantic_raw["distances"][0]), 1):
                    row = fts.execute("SELECT payload FROM pages WHERE page_id=?", (page_id,)).fetchone()
                    if row:
                        record = json.loads(row["payload"])
                        record["semantic_score"] = round(1 / (1 + max(float(distance), 0)), 8)
                        record["semantic_reason"] = f"Chroma cosine distance={float(distance):.6f}, rank={rank}"
                        semantic.append(record)
            except Exception as exc:
                semantic_error = f"{type(exc).__name__}: {exc}"
            terms = _fts_terms(query)
            expression = " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)
            lexical: list[dict[str, Any]] = []
            if expression:
                for rank, row in enumerate(fts.execute(
                    "SELECT page_id, bm25(page_fts) AS rank FROM page_fts WHERE page_fts MATCH ? ORDER BY rank LIMIT 15", (expression,)
                ), 1):
                    payload = fts.execute("SELECT payload FROM pages WHERE page_id=?", (row["page_id"],)).fetchone()
                    if payload:
                        record = json.loads(payload["payload"])
                        haystack = " ".join([record["title"], record["section_title"], *record["keywords"], *record["entities"], *record["parameters"], record["content"]]).casefold()
                        matched = [term for term in terms if term.casefold() in haystack]
                        record["lexical_score"] = round(-float(row["rank"]), 8)
                        record["matched_terms"] = matched
                        record["lexical_reason"] = f"SQLite FTS5 exact token match: {', '.join(matched)}"
                        lexical.append(record)
            fused = fuse_rrf(semantic, lexical, 5)
            top5 = []
            for record in fused:
                retrieval = record["retrieval"]
                top5.append({
                    "file_name": record["source_file"], "page_number": record["page_number"],
                    "semantic": retrieval["semantic"], "fts": retrieval["lexical"], "rrf_score": retrieval["rrf_score"],
                    "matched_terms": retrieval["matched_terms"], "summary": _summary(record, retrieval["matched_terms"]),
                    "assets": _assets(root, record),
                })
            queries.append({"query": query, "semantic_executed": semantic_error is None, "semantic_error": semantic_error, "fts_executed": True, "rrf_executed": True, "fts_terms": terms, "fts_result_count": len(lexical), "top5": top5})
        return {
            "target_db": str(root), "rebuilt_running_reconciliation": running, "source_running_readonly": source_running,
            "embedding": {"model": EMBEDDING_MODEL_ID, "normalization": "whitespace collapsed via ' '.join(text.split()) for document-cache keys", "document_dimension": document_dimensions, "query_dimensions": [len(vector) for vector in query_vectors], "external_requests_this_run": 1, "external_requests_total": prior_external_requests + 1, "texts_this_run": len(QUERIES), "query_vectors_total": (prior_external_requests + 1) * len(QUERIES), "query_texts": list(QUERIES)},
            "queries": queries,
        }
    finally:
        fts.close(); cache.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--source", default="v3_candidate_db", help="read-only source cache directory")
    parser.add_argument("--prior-external-requests", type=int, default=0)
    args = parser.parse_args()
    report = validate(Path(args.target), Path(args.source), args.prior_external_requests)
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
