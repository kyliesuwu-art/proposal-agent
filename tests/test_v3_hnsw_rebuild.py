from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.cache_batch import EMBEDDING_MODEL_ID, EmbeddingCache
from src.v3_hnsw_rebuild import OfflineRebuilder, diagnose


def _source(root: Path) -> None:
    root.mkdir(); (root / "sidecars" / "doc" / "ver").mkdir(parents=True)
    (root / "sidecars" / "doc" / "ver" / "index.json").write_text("{}", encoding="utf-8")
    manifest = sqlite3.connect(root / "cache_ingest_manifest.sqlite3")
    manifest.execute("CREATE TABLE cache_documents (zip_path TEXT PRIMARY KEY, zip_hash TEXT, source_key TEXT, document_id TEXT, version_id TEXT, status TEXT, stage TEXT, page_count INTEGER, duplicate_of TEXT, error TEXT, embedding_calls INTEGER)")
    manifest.execute("INSERT INTO cache_documents VALUES ('a.zip','x','a','doc','ver','completed','completed',1,NULL,NULL,0)"); manifest.commit(); manifest.close()
    fts = sqlite3.connect(root / "hybrid_lexical.sqlite3")
    fts.executescript("CREATE TABLE pages (page_id TEXT PRIMARY KEY, document_id TEXT, version_id TEXT, source_key TEXT, page_number INTEGER, payload TEXT); CREATE VIRTUAL TABLE page_fts USING fts5(page_id UNINDEXED, title, section_title, keywords, entities, parameters, content)")
    page = {"page_id":"doc:ver:p1","document_id":"doc","version_id":"ver","source_key":"a.pdf","page_number":1,"source_file":"a.pdf","retrieval_text":"智能配电系统","identity_metadata":{"document_id":"doc","version_id":"ver","source_key":"a.pdf","page_number":1,"document_type":"proposal"},"title":"智能配电系统","section_title":"","keywords":[],"entities":[],"parameters":[],"content":"智能配电系统"}
    fts.execute("INSERT INTO pages VALUES (?,?,?,?,?,?)", (page["page_id"],"doc","ver","a.pdf",1,json.dumps(page, ensure_ascii=False))); fts.execute("INSERT INTO page_fts VALUES (?,?,?,?,?,?,?)", (page["page_id"],"智能配电系统","","","","","智能配电系统")); fts.commit(); fts.close()
    cache = sqlite3.connect(root / "embedding_cache.sqlite3"); cache.execute("CREATE TABLE embeddings (model_id TEXT, text_hash TEXT, vector_json TEXT, PRIMARY KEY(model_id,text_hash))"); cache.execute("INSERT INTO embeddings VALUES (?,?,?)", (EMBEDDING_MODEL_ID, EmbeddingCache._hash("智能配电系统"), "[1.0,0.0]")); cache.commit(); cache.close()


def test_interrupt_is_resumable_without_duplicate_vectors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, target = tmp_path / "source", tmp_path / "rebuilt"; _source(source)
    rebuilder = OfflineRebuilder(source, target)
    original = rebuilder.collection.upsert
    monkeypatch.setattr(rebuilder.collection, "upsert", lambda **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt): rebuilder.rebuild()
    rebuilder.close()
    resumed = OfflineRebuilder(source, target)
    try:
        assert resumed.rebuild()["written"] == 1
        assert resumed.collection.count() == 1
        assert resumed.rebuild()["skipped"] == 1
        assert resumed.collection.count() == 1
    finally:
        resumed.close()
    assert diagnose(source)["sqlite_integrity"]["hybrid_lexical.sqlite3"] == "ok"
