"""Isolated V2 test store: Chroma semantic retrieval plus SQLite FTS lexical retrieval."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import chromadb

from src.adapters.vector_store import DashScopeEmbeddingFunction
from src.domain import DocumentIdentity
from src.retrieval_fields import RetrievalFields, extract_retrieval_fields


_FTS_TOKEN = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9.-]*|\d+(?:\.\d+)?(?:kV|KV|MW|kW|MWh|kWh|kvar|kVar)")


def page_id(identity: DocumentIdentity, page_number: int) -> str:
    return f"{identity.document_id}:{identity.version_id}:p{page_number}"


def fuse_rrf(semantic: list[dict], lexical: list[dict], limit: int, k: int = 60) -> list[dict]:
    """Stable reciprocal-rank fusion; records retain every contributing reason."""
    merged: dict[str, dict] = {}
    for mode, records in (("semantic", semantic), ("lexical", lexical)):
        for rank, original in enumerate(records, 1):
            record = merged.setdefault(original["page_id"], dict(original))
            retrieval = record.setdefault("retrieval", {"mode": "hybrid", "semantic": None, "lexical": None, "matched_terms": []})
            retrieval[mode] = {"rank": rank, "score": original.get(f"{mode}_score"), "reason": original.get(f"{mode}_reason", "")}
            if mode == "lexical":
                retrieval["matched_terms"] = list(original.get("matched_terms", []))
            retrieval["rrf_score"] = round(float(retrieval.get("rrf_score", 0)) + 1 / (k + rank), 8)
    results = list(merged.values())
    for record in results:
        retrieval = record["retrieval"]
        modes = [name for name in ("semantic", "lexical") if retrieval.get(name)]
        retrieval["fusion_reason"] = f"RRF(k={k}); contributing_modes={','.join(modes)}"
    results.sort(key=lambda item: (-item["retrieval"]["rrf_score"], item["page_id"]))
    return results[:limit]


class HybridTestStore:
    """V2 store rooted in an explicitly supplied, disposable directory only."""

    def __init__(self, test_db: str | Path, embedding_function: Any | None = None) -> None:
        self.root = Path(test_db).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.root / "hybrid_lexical.sqlite3")
        self._db.row_factory = sqlite3.Row
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS pages (
                page_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, version_id TEXT NOT NULL,
                source_key TEXT NOT NULL, page_number INTEGER NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS pages_document_version ON pages(document_id, version_id);
            CREATE VIRTUAL TABLE IF NOT EXISTS page_fts USING fts5(page_id UNINDEXED, title, section_title, keywords, entities, parameters, content);
        """)
        self._db.commit()
        client = chromadb.PersistentClient(path=str(self.root / "chroma_v2"))
        self._collection = client.get_or_create_collection(
            "electrical_pages_v2", metadata={"hnsw:space": "cosine"},
            embedding_function=embedding_function or DashScopeEmbeddingFunction(),
        )

    def close(self) -> None:
        self._db.close()

    def existing_version(self, document_id: str) -> str | None:
        row = self._db.execute("SELECT version_id FROM pages WHERE document_id=? LIMIT 1", (document_id,)).fetchone()
        return str(row["version_id"]) if row else None

    def add_version(self, identity: DocumentIdentity, pages: list[dict], document_type: str = "proposal") -> int:
        """Write a complete new version before deleting prior versions of this document."""
        records = [self._record(identity, page, document_type) for page in pages if page.get("indexable", True)]
        if not records:
            return 0
        ids = [record["page_id"] for record in records]
        try:
            self._collection.add(ids=ids, documents=[record["retrieval_text"] for record in records], metadatas=[record["identity_metadata"] for record in records])
            with self._db:
                self._db.executemany("INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?)", [(r["page_id"], r["document_id"], r["version_id"], r["source_key"], r["page_number"], json.dumps(r, ensure_ascii=False)) for r in records])
                self._db.executemany("INSERT INTO page_fts VALUES (?, ?, ?, ?, ?, ?, ?)", [(r["page_id"], r["title"], r["section_title"], " ".join(r["keywords"]), " ".join(r["entities"]), " ".join(r["parameters"]), r["content"]) for r in records])
        except Exception:
            # Preserve old versions.  Remove only a partly-added new vector version when possible.
            try:
                self._collection.delete(ids=ids)
            except Exception:
                pass
            raise
        self.delete_versions(identity.document_id, except_version=identity.version_id)
        return len(records)

    def delete_versions(self, document_id: str, except_version: str | None = None) -> int:
        sql = "SELECT page_id FROM pages WHERE document_id=?" + (" AND version_id<>?" if except_version else "")
        args = (document_id, except_version) if except_version else (document_id,)
        ids = [row[0] for row in self._db.execute(sql, args)]
        if not ids:
            return 0
        self._collection.delete(ids=ids)
        with self._db:
            self._db.executemany("DELETE FROM page_fts WHERE page_id=?", [(value,) for value in ids])
            self._db.executemany("DELETE FROM pages WHERE page_id=?", [(value,) for value in ids])
        return len(ids)

    def search(self, query: str, limit: int = 10) -> list[dict]:
        semantic = self.semantic_search(query, limit * 3)
        lexical = self.lexical_search(query, limit * 3)
        return fuse_rrf(semantic, lexical, limit)

    def semantic_search(self, query: str, limit: int) -> list[dict]:
        limit = min(limit, self.count())
        if limit == 0:
            return []
        result = self._collection.query(query_texts=[query], n_results=limit)
        records = []
        for rank, (value, distance) in enumerate(zip(result.get("ids", [[]])[0], result.get("distances", [[]])[0]), 1):
            record = self._payload(value)
            if record:
                record["semantic_score"] = round(1 / (1 + max(float(distance), 0)), 8)
                record["semantic_reason"] = f"Chroma cosine distance={float(distance):.6f}, rank={rank}"
                records.append(record)
        return records

    def lexical_search(self, query: str, limit: int) -> list[dict]:
        terms = list(dict.fromkeys(token for token in _FTS_TOKEN.findall(query) if len(token) >= 2))
        if not terms:
            return []
        # Exact parameters and abbreviations should narrow lexical candidates.  Chroma
        # remains the complementary broad-recall path for natural-language questions.
        expression = " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)
        rows = self._db.execute("SELECT page_id, bm25(page_fts) AS rank FROM page_fts WHERE page_fts MATCH ? ORDER BY rank LIMIT ?", (expression, limit)).fetchall()
        records = []
        for row in rows:
            record = self._payload(row["page_id"])
            if record:
                haystack = " ".join([record["title"], record["section_title"], *record["keywords"], *record["entities"], *record["parameters"], record["content"]]).casefold()
                matched = [term for term in terms if term.casefold() in haystack]
                record["lexical_score"] = round(-float(row["rank"]), 8)
                record["matched_terms"] = matched
                record["lexical_reason"] = f"SQLite FTS5 exact token match: {', '.join(matched)}"
                records.append(record)
        return records

    def get_adjacent_pages(self, source_key: str, page_number: int, window: int = 1) -> list[dict]:
        rows = self._db.execute("SELECT payload FROM pages WHERE source_key=? AND page_number BETWEEN ? AND ? AND page_number<>? ORDER BY page_number", (source_key, page_number - window, page_number + window, page_number)).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM pages").fetchone()[0])

    def _payload(self, value: str) -> dict | None:
        row = self._db.execute("SELECT payload FROM pages WHERE page_id=?", (value,)).fetchone()
        return json.loads(row["payload"]) if row else None

    @staticmethod
    def _record(identity: DocumentIdentity, page: dict, document_type: str) -> dict:
        fields: RetrievalFields = extract_retrieval_fields(page.get("title", ""), page.get("content", ""), page.get("context_summary", ""))
        number = int(page.get("page_number", page.get("slide_number", 0)))
        identifier = page_id(identity, number)
        return {
            "page_id": identifier, "document_id": identity.document_id, "version_id": identity.version_id,
            "source_key": identity.source_key, "page_number": number, "slide_number": number, "source_file": identity.source_key,
            "document_type": document_type, "title": fields.title, "section_title": fields.section_title,
            "content": page.get("content", ""), "context_summary": page.get("context_summary", ""),
            "retrieval_text": fields.retrieval_text, "keywords": fields.keywords, "entities": fields.entities,
            "parameters": fields.parameters, "images": page.get("images", []),
            "identity_metadata": {"document_id": identity.document_id, "version_id": identity.version_id, "source_key": identity.source_key, "page_number": number, "document_type": document_type},
        }
