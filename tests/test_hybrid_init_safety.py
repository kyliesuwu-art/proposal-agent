"""Offline regression tests for initialization ownership and read-only boundaries."""
import sqlite3

import pytest

from src import hybrid_v2


class TrackingConnection(sqlite3.Connection):
    closed = False

    def close(self):
        self.closed = True
        super().close()


@pytest.mark.parametrize("failure", ["chroma", "embedding"])
def test_initialization_failure_closes_open_sqlite(tmp_path, monkeypatch, failure):
    real_connect = sqlite3.connect
    opened = []

    def connect(*args, **kwargs):
        conn = real_connect(*args, factory=TrackingConnection, **kwargs)
        opened.append(conn)
        return conn

    class Client:
        def get_or_create_collection(self, *args, **kwargs):
            raise AssertionError("collection must not be reached")

    def chroma(*args, **kwargs):
        if failure == "chroma":
            raise RuntimeError("chroma failed")
        return Client()

    def embedding():
        raise RuntimeError("embedding failed")

    monkeypatch.setattr(hybrid_v2.sqlite3, "connect", connect)
    monkeypatch.setattr(hybrid_v2.chromadb, "PersistentClient", chroma)
    monkeypatch.setattr(hybrid_v2, "DashScopeEmbeddingFunction", embedding)
    with pytest.raises(RuntimeError, match=f"{failure} failed"):
        hybrid_v2.HybridTestStore(tmp_path / "index")
    assert len(opened) == 1
    assert opened[0].closed


def _readonly_layout(root):
    root.mkdir()
    db = sqlite3.connect(root / "hybrid_lexical.sqlite3")
    db.executescript("""
        CREATE TABLE pages (page_id TEXT, document_id TEXT, version_id TEXT,
                            source_key TEXT, page_number INTEGER, payload TEXT);
        CREATE VIRTUAL TABLE page_fts USING fts5(page_id UNINDEXED, title, section_title,
                                                 keywords, entities, parameters, content);
    """)
    db.close()
    (root / "chroma_v2").mkdir()
    (root / "chroma_v2" / "chroma.sqlite3").touch()


@pytest.mark.parametrize("missing", ["root", "lexical", "chroma_dir", "chroma_file"])
def test_readonly_missing_path_never_opens_chroma(tmp_path, monkeypatch, missing):
    root = tmp_path / "index"
    if missing != "root":
        root.mkdir()
    if missing in {"chroma_dir", "chroma_file"}:
        (root / "hybrid_lexical.sqlite3").touch()
    if missing == "chroma_file":
        (root / "chroma_v2").mkdir()
    def unexpected(*args, **kwargs):
        pytest.fail("Chroma must not be opened on an incomplete path")
    monkeypatch.setattr(hybrid_v2.chromadb, "PersistentClient", unexpected)
    with pytest.raises(FileNotFoundError, match="检索库不存在或不完整"):
        hybrid_v2.HybridTestStore(root, readonly=True)
    if missing == "root":
        assert not root.exists()
    if missing == "chroma_dir":
        assert not (root / "chroma_v2").exists()
    if missing == "chroma_file":
        assert not (root / "chroma_v2" / "chroma.sqlite3").exists()


@pytest.mark.parametrize("bad_db", ["corrupt", "missing_schema"])
def test_readonly_invalid_lexical_db_fails_before_chroma(tmp_path, monkeypatch, bad_db):
    root = tmp_path / "index"
    _readonly_layout(root)
    lexical = root / "hybrid_lexical.sqlite3"
    if bad_db == "corrupt":
        lexical.write_bytes(b"not a sqlite database")
    else:
        db = sqlite3.connect(lexical)
        db.execute("DROP TABLE page_fts")
        db.close()
    def unexpected(*args, **kwargs):
        pytest.fail("Chroma must not open for an invalid lexical database")
    monkeypatch.setattr(hybrid_v2.chromadb, "PersistentClient", unexpected)
    with pytest.raises(sqlite3.DatabaseError):
        hybrid_v2.HybridTestStore(root, readonly=True)


def test_sqlite_open_failure_does_not_initialize_chroma(tmp_path, monkeypatch):
    failure = sqlite3.OperationalError("open failed")
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(hybrid_v2.sqlite3, "connect", fail)
    monkeypatch.setattr(hybrid_v2.chromadb, "PersistentClient", lambda **kwargs: pytest.fail("unexpected Chroma"))
    with pytest.raises(sqlite3.OperationalError) as raised:
        hybrid_v2.HybridTestStore(tmp_path / "index")
    assert raised.value is failure


def test_cleanup_error_does_not_mask_initialization_error(tmp_path, monkeypatch):
    real_connect = sqlite3.connect
    class FailingClose(sqlite3.Connection):
        def close(self):
            super().close()
            raise RuntimeError("cleanup failed")
    monkeypatch.setattr(hybrid_v2.sqlite3, "connect", lambda *a, **kw: real_connect(*a, factory=FailingClose, **kw))
    failure = RuntimeError("initialization failed")
    def fail(**kwargs):
        raise failure
    monkeypatch.setattr(hybrid_v2.chromadb, "PersistentClient", fail)
    with pytest.raises(RuntimeError) as raised:
        hybrid_v2.HybridTestStore(tmp_path / "index")
    assert raised.value is failure


@pytest.mark.parametrize("failure", ["chroma", "embedding", "collection"])
def test_readonly_init_failure_closes_sqlite(tmp_path, monkeypatch, failure):
    root = tmp_path / "index"
    _readonly_layout(root)
    real_connect = sqlite3.connect
    opened = []
    def connect(*args, **kwargs):
        conn = real_connect(*args, factory=TrackingConnection, **kwargs)
        opened.append(conn)
        return conn
    class Client:
        def get_collection(self, *args, **kwargs):
            raise RuntimeError("collection failed")
    def chroma(**kwargs):
        if failure == "chroma":
            raise RuntimeError("chroma failed")
        return Client()
    def embedding():
        if failure == "embedding":
            raise RuntimeError("embedding failed")
        return object()
    monkeypatch.setattr(hybrid_v2.sqlite3, "connect", connect)
    monkeypatch.setattr(hybrid_v2.chromadb, "PersistentClient", chroma)
    monkeypatch.setattr(hybrid_v2, "DashScopeEmbeddingFunction", embedding)
    with pytest.raises(RuntimeError, match=f"{failure} failed"):
        hybrid_v2.HybridTestStore(root, readonly=True)
    assert len(opened) == 1 and opened[0].closed


def test_real_temporary_store_readonly_round_trip(tmp_path):
    from src.domain import DocumentIdentity
    root = tmp_path / "index"
    embedding = hybrid_v2.LocalHashEmbeddingFunction()
    store = hybrid_v2.HybridTestStore(root, embedding)
    try:
        store.add_version(DocumentIdentity("folder/source.pdf", "doc1", "hash1", "v1"),
                          [{"page_number": 1, "title": "PCS", "content": "PCS 100MW"}])
        before = store.search("PCS", 10)
    finally:
        store.close()
    reader = hybrid_v2.HybridTestStore(root, embedding, readonly=True)
    try:
        after = reader.search("PCS", 10)
        assert after == before
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader._db.execute("DELETE FROM pages")
    finally:
        reader.close()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        reader.count()


def test_missing_collection_propagates_and_closes_sqlite(tmp_path, monkeypatch):
    root = tmp_path / "index"
    _readonly_layout(root)
    failure = ValueError("collection missing")
    class Client:
        def get_collection(self, *args, **kwargs):
            raise failure
    real_connect = sqlite3.connect
    opened = []
    def connect(*args, **kwargs):
        conn = real_connect(*args, factory=TrackingConnection, **kwargs)
        opened.append(conn)
        return conn
    monkeypatch.setattr(hybrid_v2.sqlite3, "connect", connect)
    monkeypatch.setattr(hybrid_v2.chromadb, "PersistentClient", lambda **kwargs: Client())
    with pytest.raises(ValueError) as raised:
        hybrid_v2.HybridTestStore(root, object(), readonly=True)
    assert raised.value is failure
    assert opened[0].closed


def test_corrupt_chroma_file_fails_with_temporary_index(tmp_path):
    root = tmp_path / "index"
    _readonly_layout(root)
    (root / "chroma_v2" / "chroma.sqlite3").write_bytes(b"not a sqlite database")
    with pytest.raises(Exception):
        hybrid_v2.HybridTestStore(root, hybrid_v2.LocalHashEmbeddingFunction(), readonly=True)
