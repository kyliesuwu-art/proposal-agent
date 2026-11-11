"""Tests use disposable temporary indexes and local embeddings only."""
import json
import sqlite3
import subprocess
import sys

import pytest

from src.domain import DocumentIdentity
from src.hybrid_v2 import HybridTestStore, LocalHashEmbeddingFunction
from src.kb_consistency_check import inspect_copy


@pytest.fixture
def index(tmp_path):
    root = tmp_path / "index"
    store = HybridTestStore(root, LocalHashEmbeddingFunction())
    store.add_version(DocumentIdentity("hospital/a.pdf", "doc_a", "hash", "v1"),
                      [{"page_number": 1, "title": "PCS", "content": "PCS 500 kW"}])
    db = sqlite3.connect(root / "cache_ingest_manifest.sqlite3")
    db.execute("CREATE TABLE cache_documents(zip_path TEXT, zip_hash TEXT, source_key TEXT, document_id TEXT, version_id TEXT, status TEXT)")
    db.commit(); db.close()
    attestation = root / "attestation.json"
    attestation.write_text(json.dumps({"confirmed_by": "fixture reviewer", "basis": "synthetic fixture", "zip_inventory_complete": True, "documents": {"doc_a": "v1"}}), encoding="utf-8")
    yield root, store, attestation
    store.close()


def run(index, attested=True):
    root, _, attestation = index
    return inspect_copy(root, disposable_copy=True, attestation=attestation if attested else None)


def finding(report, name):
    return next(item for item in report["checks"] if item["name"] == name)


def test_complete_single_version_with_explicit_attestation(index):
    report = run(index)
    assert report["status"] == "PASS" and report["can_enter_next_stage"]
    assert all(check["status"] == "PASS" for check in report["checks"])
    assert any("cannot be verified" in item for item in report["unable_to_check"])
    print(json.dumps(report, ensure_ascii=False, indent=2))


def test_missing_vector(index):
    index[1]._collection.delete(ids=["doc_a:v1:p1"])
    report = run(index)
    assert report["status"] == "BLOCKED" and "missing=1" in finding(report, "chroma_pages")["findings"][0]


def test_orphan_vector_missing_sqlite_page(index):
    db = sqlite3.connect(index[0] / "hybrid_lexical.sqlite3")
    db.execute("DELETE FROM pages"); db.commit(); db.close()
    report = run(index)
    assert report["status"] == "BLOCKED" and "orphan=1" in finding(report, "chroma_pages")["findings"][0]


def test_fts_mismatch(index):
    db = sqlite3.connect(index[0] / "hybrid_lexical.sqlite3")
    db.execute("UPDATE page_fts SET content='changed'"); db.commit(); db.close()
    report = run(index)
    assert finding(report, "fts_pages")["status"] == "BLOCKED"


def test_multiple_versions_blocked(index, monkeypatch):
    store = index[1]
    monkeypatch.setattr(store, "delete_versions", lambda *args, **kwargs: 0)
    store.add_version(DocumentIdentity("hospital/a.pdf", "doc_a", "hash2", "v2"), [{"page_number": 1, "title": "PCS", "content": "PCS 800 kW"}])
    report = run(index)
    assert finding(report, "page_identity_versions")["status"] == "BLOCKED"
    assert "multi_version_docs=1" in finding(report, "page_identity_versions")["findings"][0]


def test_source_identity_collision(index):
    store = index[1]
    store.add_version(DocumentIdentity("hospital/a.pdf", "doc_b", "hash2", "v2"), [{"page_number": 1, "title": "PCS", "content": "PCS 800 kW"}])
    report = run(index)
    assert "identity_conflicts=1" in finding(report, "page_identity_versions")["findings"][0]
    assert report["status"] == "BLOCKED"


def test_zip_collision(index):
    db = sqlite3.connect(index[0] / "cache_ingest_manifest.sqlite3")
    db.executemany("INSERT INTO cache_documents VALUES (?,?,?,?,?,?)", [("a/one.zip", "hash1", "cache/a.pdf", "doc_z", "v1", "completed"), ("b/two.zip", "hash2", "cache/a.pdf", "doc_z", "v2", "completed")])
    db.commit(); db.close()
    assert finding(run(index), "zip_identity")["status"] == "BLOCKED"


@pytest.mark.parametrize("damage", ["missing_table", "corrupt", "missing_file"])
def test_damaged_index_never_passes(index, damage):
    root = index[0]
    if damage == "missing_file":
        (root / "cache_ingest_manifest.sqlite3").unlink()
    elif damage == "corrupt":
        (root / "cache_ingest_manifest.sqlite3").write_bytes(b"broken")
    else:
        db = sqlite3.connect(root / "hybrid_lexical.sqlite3")
        db.execute("DROP TABLE page_fts"); db.commit(); db.close()
    assert run(index)["status"] == "BLOCKED"


def test_no_version_attestation_is_blocked(index):
    report = run(index, attested=False)
    assert finding(report, "version_attestation")["status"] == "BLOCKED"
    assert not report["can_enter_next_stage"]


def test_empty_index_is_blocked(index):
    root, store, _ = index
    store.delete_versions("doc_a")
    report = run(index)
    assert report["status"] == "BLOCKED"
    assert "pages=0" in finding(report, "page_identity_versions")["findings"][0]


def test_exception_fails_closed(index, monkeypatch):
    def fail(**kwargs): raise RuntimeError("synthetic Chroma failure")
    monkeypatch.setattr("src.kb_consistency_check.chromadb.PersistentClient", fail)
    report = run(index)
    assert report["status"] == "BLOCKED" and finding(report, "inspection_execution")["status"] == "BLOCKED"


def test_disposable_declaration_required(index):
    assert inspect_copy(index[0])["status"] == "BLOCKED"


def test_fts_missing_is_blocked(index):
    db = sqlite3.connect(index[0] / "hybrid_lexical.sqlite3")
    db.execute("DELETE FROM page_fts"); db.commit(); db.close()
    assert "missing=1" in finding(run(index), "fts_pages")["findings"][0]


def test_duplicate_fts_entry_is_blocked(index):
    db = sqlite3.connect(index[0] / "hybrid_lexical.sqlite3")
    db.execute("INSERT INTO page_fts SELECT page_id,title,section_title,keywords,entities,parameters,content FROM page_fts")
    db.commit(); db.close()
    assert "duplicate=1" in finding(run(index), "fts_pages")["findings"][0]


def test_chroma_metadata_inconsistency_is_blocked(index):
    index[1]._collection.update(ids=["doc_a:v1:p1"], metadatas=[{"document_id": "wrong"}])
    assert "metadata_mismatch=1" in finding(run(index), "chroma_pages")["findings"][0]


def test_payload_identity_inconsistency_is_blocked(index):
    db = sqlite3.connect(index[0] / "hybrid_lexical.sqlite3")
    raw = json.loads(db.execute("SELECT payload FROM pages").fetchone()[0])
    raw["source_key"] = "different.pdf"
    db.execute("UPDATE pages SET payload=?", (json.dumps(raw),)); db.commit(); db.close()
    assert "invalid=1" in finding(run(index), "page_identity_versions")["findings"][0]


def test_mismatched_attestation_and_incomplete_zip_are_blocked(index):
    index[2].write_text(json.dumps({"confirmed_by": "reviewer", "basis": "test", "zip_inventory_complete": True, "documents": {"doc_a": "v2"}}), encoding="utf-8")
    assert finding(run(index), "version_attestation")["status"] == "BLOCKED"
    db = sqlite3.connect(index[0] / "cache_ingest_manifest.sqlite3")
    db.execute("INSERT INTO cache_documents VALUES (?,?,?,?,?,?)", ("a.zip", "hash", "cache/a.pdf", "doc_a", "v1", "running"))
    db.commit(); db.close()
    assert finding(run(index), "zip_identity")["status"] == "BLOCKED"


def test_non_temporary_path_is_refused_before_chroma(monkeypatch):
    def fail(**kwargs): pytest.fail("must not open Chroma")
    monkeypatch.setattr("src.kb_consistency_check.chromadb.PersistentClient", fail)
    assert inspect_copy(__file__, disposable_copy=True)["status"] == "BLOCKED"


def test_duplicate_cache_hash_across_names_is_warning(index):
    db = sqlite3.connect(index[0] / "cache_ingest_manifest.sqlite3")
    db.executemany("INSERT INTO cache_documents VALUES (?,?,?,?,?,?)", [("a.zip", "same", "cache/a.pdf", "doc_x", "v1", "duplicate"), ("b.zip", "same", "cache/b.pdf", "doc_y", "v1", "duplicate")])
    db.commit(); db.close()
    report = run(index)
    assert report["status"] == "WARNING" and report["can_enter_next_stage"]
    assert "duplicate_hash_sources=1" in finding(report, "zip_identity")["findings"][0]


def test_cli_reports_json_and_exit_status(index):
    result = subprocess.run([sys.executable, "-X", "utf8", "-m", "src.kb_consistency_check", str(index[0]), "--disposable-copy", "--attestation", str(index[2])], text=True, encoding="utf-8", capture_output=True, check=False)
    assert result.returncode == 0 and json.loads(result.stdout)["status"] == "PASS"
    blocked = subprocess.run([sys.executable, "-X", "utf8", "-m", "src.kb_consistency_check", str(index[0]), "--disposable-copy"], text=True, encoding="utf-8", capture_output=True, check=False)
    assert blocked.returncode == 1 and json.loads(blocked.stdout)["status"] == "BLOCKED"
