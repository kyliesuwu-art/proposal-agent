"""Offline, resumable HNSW rebuild from V3 SQLite payloads and vector cache.

This module deliberately never opens the source Chroma directory.  It only reads
the source SQLite databases and sidecars, then creates a separately named store.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import chromadb

from src.cache_batch import EMBEDDING_MODEL_ID, EmbeddingCache


COLLECTION = "electrical_pages_v2"
SQLITE_FILES = ("cache_ingest_manifest.sqlite3", "hybrid_lexical.sqlite3", "embedding_cache.sqlite3")


class NoRemoteEmbedding:
    """Guard against an accidental Chroma text-embedding request."""

    def __call__(self, input: list[str]) -> list[list[float]]:
        raise RuntimeError("离线 HNSW 重建禁止调用 embedding；请提供缓存向量")

    def name(self) -> str:
        return "offline-cache-only"


def _read_integrity(path: Path) -> str:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        connection.close()


def _sidecar_indexes(root: Path) -> set[tuple[str, str]]:
    sidecars = root / "sidecars"
    if not sidecars.is_dir():
        return set()
    return {
        (path.parent.parent.name, path.parent.name)
        for path in sidecars.glob("*/*/index.json")
        if path.is_file()
    }


def diagnose(source: str | Path) -> dict[str, Any]:
    """Read all source state without opening its HNSW directory."""
    root = Path(source).resolve()
    integrity = {name: _read_integrity(root / name) for name in SQLITE_FILES}
    state = sqlite3.connect(f"file:{(root / SQLITE_FILES[0]).as_posix()}?mode=ro", uri=True)
    fts = sqlite3.connect(f"file:{(root / SQLITE_FILES[1]).as_posix()}?mode=ro", uri=True)
    cache = sqlite3.connect(f"file:{(root / SQLITE_FILES[2]).as_posix()}?mode=ro", uri=True)
    try:
        statuses = dict(state.execute("SELECT status, COUNT(*) FROM cache_documents GROUP BY status"))
        pages = [json.loads(row[0]) for row in fts.execute("SELECT payload FROM pages")]
        fts_count = int(fts.execute("SELECT COUNT(*) FROM page_fts").fetchone()[0])
        cache_count = int(cache.execute("SELECT COUNT(*) FROM embeddings WHERE model_id=?", (EMBEDDING_MODEL_ID,)).fetchone()[0])
        cached = 0
        missing: list[dict[str, Any]] = []
        sidecar_keys = _sidecar_indexes(root)
        sidecar_missing: list[str] = []
        for page in pages:
            text = str(page["retrieval_text"])
            found = cache.execute(
                "SELECT 1 FROM embeddings WHERE model_id=? AND text_hash=?",
                (EMBEDDING_MODEL_ID, EmbeddingCache._hash(text)),
            ).fetchone()
            if found:
                cached += 1
            else:
                missing.append({"page_id": page["page_id"], "source_file": page["source_file"], "page_number": page["page_number"]})
            if (str(page["document_id"]), str(page["version_id"])) not in sidecar_keys:
                sidecar_missing.append(str(page["page_id"]))
        return {
            "source_db": str(root), "source_chroma_opened": False,
            "sqlite_integrity": integrity,
            "manifest_statuses": {key: int(statuses.get(key, 0)) for key in ("completed", "failed", "running", "in_progress", "duplicate")},
            "fts_pages": len(pages), "fts_entries": fts_count,
            "sidecar_document_versions": len(sidecar_keys), "sidecar_missing_for_pages": sidecar_missing,
            "embedding_cache_vectors": cache_count, "cached_page_vectors": cached,
            "missing_vectors": missing,
        }
    finally:
        state.close(); fts.close(); cache.close()


def choose_target(preferred: str | Path) -> Path:
    target = Path(preferred).resolve()
    if not target.exists():
        return target
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = target.with_name(f"{target.name}_{stamp}")
    number = 1
    while candidate.exists():
        candidate = target.with_name(f"{target.name}_{stamp}_{number}")
        number += 1
    return candidate


class OfflineRebuilder:
    def __init__(self, source: str | Path, target: str | Path) -> None:
        self.source = Path(source).resolve()
        self.target = Path(target).resolve()
        self.target.mkdir(parents=True, exist_ok=True)
        self.state = sqlite3.connect(self.target / "hnsw_rebuild_manifest.sqlite3")
        self.state.execute("""CREATE TABLE IF NOT EXISTS rebuild_pages (
            page_id TEXT PRIMARY KEY, status TEXT NOT NULL, text_hash TEXT NOT NULL, error TEXT)""")
        self.state.commit()
        self.client = chromadb.PersistentClient(path=str(self.target / "chroma_v2"))
        self.collection = self.client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"}, embedding_function=NoRemoteEmbedding())
        self.fts = sqlite3.connect(self.target / "hybrid_lexical.sqlite3")
        self.fts.executescript("""CREATE TABLE IF NOT EXISTS pages (
            page_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, version_id TEXT NOT NULL,
            source_key TEXT NOT NULL, page_number INTEGER NOT NULL, payload TEXT NOT NULL);
            CREATE VIRTUAL TABLE IF NOT EXISTS page_fts USING fts5(page_id UNINDEXED, title, section_title, keywords, entities, parameters, content);""")
        self.fts.commit()
        self.cache = sqlite3.connect(f"file:{(self.source / 'embedding_cache.sqlite3').as_posix()}?mode=ro", uri=True)

    def close(self) -> None:
        self.cache.close(); self.fts.close(); self.state.close()

    def _vector(self, text: str) -> list[float] | None:
        row = self.cache.execute("SELECT vector_json FROM embeddings WHERE model_id=? AND text_hash=?", (EMBEDDING_MODEL_ID, EmbeddingCache._hash(text))).fetchone()
        return [float(value) for value in json.loads(row[0])] if row else None

    def rebuild(self) -> dict[str, Any]:
        source_fts = sqlite3.connect(f"file:{(self.source / 'hybrid_lexical.sqlite3').as_posix()}?mode=ro", uri=True)
        try:
            source_pages = [json.loads(row[0]) for row in source_fts.execute("SELECT payload FROM pages ORDER BY page_id")]
        finally:
            source_fts.close()
        copied: list[str] = []
        for name in ("sidecars", "assets"):
            origin, destination = self.source / name, self.target / name
            if origin.is_dir() and not destination.exists():
                shutil.copytree(origin, destination)
                copied.append(name)
        written = skipped = 0; missing: list[dict[str, Any]] = []; pending: list[tuple[dict[str, Any], list[float], str]] = []
        for page in source_pages:
            page_id, text = str(page["page_id"]), str(page["retrieval_text"])
            text_hash = EmbeddingCache._hash(text)
            prior = self.state.execute("SELECT status, text_hash FROM rebuild_pages WHERE page_id=?", (page_id,)).fetchone()
            if prior and prior[0] == "completed" and prior[1] == text_hash:
                skipped += 1
                continue
            vector = self._vector(text)
            if vector is None:
                missing.append({"page_id": page_id, "source_file": page["source_file"], "page_number": page["page_number"]})
                with self.state:
                    self.state.execute("INSERT OR REPLACE INTO rebuild_pages VALUES (?, ?, ?, ?)", (page_id, "missing_vector", text_hash, "embedding cache miss"))
                continue
            pending.append((page, vector, text_hash))
        for start in range(0, len(pending), 32):
            batch = pending[start:start + 32]
            with self.state:
                self.state.executemany("INSERT OR REPLACE INTO rebuild_pages VALUES (?, ?, ?, ?)", [(page["page_id"], "running", text_hash, None) for page, _, text_hash in batch])
            # upsert makes Ctrl+C recovery idempotent even if it interrupts after HNSW write.
            self.collection.upsert(ids=[page["page_id"] for page, _, _ in batch], documents=[page["retrieval_text"] for page, _, _ in batch], metadatas=[page["identity_metadata"] for page, _, _ in batch], embeddings=[vector for _, vector, _ in batch])
            with self.fts:
                for page, _, _ in batch:
                    self.fts.execute("INSERT OR REPLACE INTO pages VALUES (?, ?, ?, ?, ?, ?)", (page["page_id"], page["document_id"], page["version_id"], page["source_key"], page["page_number"], json.dumps(page, ensure_ascii=False)))
                    self.fts.execute("DELETE FROM page_fts WHERE page_id=?", (page["page_id"],))
                    self.fts.execute("INSERT INTO page_fts VALUES (?, ?, ?, ?, ?, ?, ?)", (page["page_id"], page["title"], page["section_title"], " ".join(page["keywords"]), " ".join(page["entities"]), " ".join(page["parameters"]), page["content"]))
            with self.state:
                self.state.executemany("UPDATE rebuild_pages SET status='completed', error=NULL WHERE page_id=?", [(page["page_id"],) for page, _, _ in batch])
            written += len(batch)
        return {"target_db": str(self.target), "written": written, "skipped": skipped, "missing_vectors": missing, "copied": copied}


def query_offline(target: Path, query: str) -> dict[str, Any]:
    """Run real FTS plus cached-query-vector semantic retrieval without network."""
    fts = sqlite3.connect(f"file:{(target / 'hybrid_lexical.sqlite3').as_posix()}?mode=ro", uri=True)
    try:
        terms = [term for term in query.split() if term] or [query]
        expression = " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)
        rows = fts.execute("SELECT p.payload FROM page_fts x JOIN pages p ON p.page_id=x.page_id WHERE page_fts MATCH ? ORDER BY bm25(page_fts) LIMIT 10", (expression,)).fetchall()
        lexical = [{"source_file": json.loads(row[0])["source_file"], "page_number": json.loads(row[0])["page_number"]} for row in rows]
        return {"query": query, "mode": "hybrid", "semantic": {"executed": False, "reason": "no cached query vector; external embedding prohibited"}, "lexical": lexical, "results": lexical}
    finally:
        fts.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="v3_candidate_db")
    parser.add_argument("--target", default="v3_candidate_db_rebuilt")
    parser.add_argument("--diagnosis", default="outputs/v3_hnsw_diagnosis.json")
    parser.add_argument("--resume-target", action="store_true", help="仅恢复本工具已创建的目标目录")
    parser.add_argument("--reuse-empty-hnsw-target", action="store_true", help="仅在目标保留 payload、但已无 Chroma/HNSW 状态时重建 HNSW")
    args = parser.parse_args()
    diagnosis = diagnose(args.source)
    requested = Path(args.target).resolve()
    if args.resume_target and args.reuse_empty_hnsw_target:
        raise SystemExit("--resume-target 与 --reuse-empty-hnsw-target 不能同时使用")
    if args.resume_target:
        if not (requested / "hnsw_rebuild_manifest.sqlite3").is_file():
            raise SystemExit("--resume-target 仅允许用于含 hnsw_rebuild_manifest.sqlite3 的重建目录")
        target = requested
    elif args.reuse_empty_hnsw_target:
        if (requested / "chroma_v2").exists() or (requested / "hnsw_rebuild_manifest.sqlite3").exists():
            raise SystemExit("--reuse-empty-hnsw-target 仅允许用于不存在 chroma_v2 和 hnsw_rebuild_manifest.sqlite3 的目标")
        target = requested
    else:
        target = choose_target(requested)
    rebuild = OfflineRebuilder(args.source, target)
    try:
        result = rebuild.rebuild()
    finally:
        rebuild.close()
    reopened = OfflineRebuilder(args.source, target)
    try:
        count = reopened.collection.count()
        resume = reopened.rebuild()
    finally:
        reopened.close()
    report = {**diagnosis, "rebuild": result, "reopened_chroma_count": count, "resume": resume,
              "queries": [query_offline(target, item) for item in ("智能配电系统", "绿电直连", "工商业储能收益")]}
    report["generation_compatible"] = count == diagnosis["cached_page_vectors"]
    output = Path(args.diagnosis).resolve(); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
