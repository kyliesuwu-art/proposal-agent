"""Offline V2 field extraction, fusion, and replacement-contract tests."""

from __future__ import annotations

import sqlite3

import pytest

from src.domain.models import DocumentIdentity
from src.hybrid_v2 import HybridTestStore, fuse_rrf
from src.retrieval_fields import extract_retrieval_fields


class _Collection:
    def __init__(self) -> None:
        self.values: dict[str, dict] = {}

    def add(self, *, ids, documents, metadatas) -> None:
        for value, document, metadata in zip(ids, documents, metadatas):
            if value in self.values:
                raise ValueError("duplicate")
            self.values[value] = {"document": document, "metadata": metadata}

    def delete(self, *, ids) -> None:
        for value in ids:
            self.values.pop(value, None)


def _store() -> HybridTestStore:
    store = HybridTestStore.__new__(HybridTestStore)
    store._db = sqlite3.connect(":memory:")
    store._db.row_factory = sqlite3.Row
    store._db.executescript("""
        CREATE TABLE pages (page_id TEXT PRIMARY KEY, document_id TEXT, version_id TEXT, source_key TEXT, page_number INTEGER, payload TEXT);
        CREATE VIRTUAL TABLE page_fts USING fts5(page_id UNINDEXED, title, section_title, keywords, entities, parameters, content);
    """)
    store._collection = _Collection()
    return store


def _identity(source_key: str, version: str = "ver_one") -> DocumentIdentity:
    return DocumentIdentity(source_key, "doc_" + source_key.replace("/", "_"), version, version)


def _page(number: int, content: str) -> dict:
    return {"slide_number": number, "title": "储能 PCS 参数", "content": content}


def test_extracts_explainable_technical_fields_without_llm() -> None:
    fields = extract_retrieval_fields("35kV BESS 系统", "PCS 型号 ABC-500，容量 100MW / 200MWh，某园区项目")

    assert fields.retrieval_text == "PCS 型号 ABC-500，容量 100MW / 200MWh，某园区项目"
    assert {"PCS", "BESS", "35kV", "100MW", "200MWh"} <= set(fields.keywords)
    assert {"PCS", "BESS", "ABC-500", "某园区项目"} <= set(fields.entities)
    assert fields.parameters == ["100MW", "200MWh"]


def test_same_filename_in_different_directories_coexists_and_lexical_hits_parameter() -> None:
    store = _store()
    try:
        store.add_version(_identity("项目A/方案.pdf"), [_page(1, "PCS 容量为 100MW，接入 35kV 母线")])
        store.add_version(_identity("项目B/方案.pdf"), [_page(1, "BESS 容量为 200MWh，接入 110kV 母线")])

        results = store.lexical_search("35kV PCS 100MW", 10)
        assert [item["source_key"] for item in results] == ["项目A/方案.pdf"]
        assert set(results[0]["matched_terms"]) >= {"35kV", "PCS", "100MW"}
        assert store.count() == 2
    finally:
        store.close()


def test_new_version_is_written_before_old_version_is_removed_and_failure_preserves_old() -> None:
    store = _store()
    try:
        old = _identity("项目A/方案.pdf", "ver_old")
        new = _identity("项目A/方案.pdf", "ver_new")
        store.add_version(old, [_page(1, "旧版本 10kV")])
        store.add_version(new, [_page(1, "新版本 35kV")])
        assert store.existing_version(old.document_id) == "ver_new"
        assert store.count() == 1

        store._collection.add(ids=["doc_项目A_方案.pdf:ver_bad:p1"], documents=["already exists"], metadatas=[{}])
        with pytest.raises(ValueError, match="duplicate"):
            store.add_version(_identity("项目A/方案.pdf", "ver_bad"), [_page(1, "失败版本")])
        assert store.existing_version(old.document_id) == "ver_new"
        assert store.count() == 1
    finally:
        store.close()


def test_rrf_retains_scores_terms_and_stable_fusion_reason() -> None:
    semantic = [{"page_id": "a", "semantic_score": 0.9, "semantic_reason": "vector"}, {"page_id": "b", "semantic_score": 0.8, "semantic_reason": "vector"}]
    lexical = [{"page_id": "b", "lexical_score": 4.0, "lexical_reason": "FTS", "matched_terms": ["35kV"]}, {"page_id": "a", "lexical_score": 2.0, "lexical_reason": "FTS", "matched_terms": ["PCS"]}]

    fused = fuse_rrf(semantic, lexical, 2)

    assert [item["page_id"] for item in fused] == ["a", "b"]  # deterministic page-id tiebreak
    assert fused[0]["retrieval"]["semantic"]["score"] == 0.9
    assert fused[0]["retrieval"]["lexical"]["reason"] == "FTS"
    assert fused[0]["retrieval"]["fusion_reason"] == "RRF(k=60); contributing_modes=semantic,lexical"
