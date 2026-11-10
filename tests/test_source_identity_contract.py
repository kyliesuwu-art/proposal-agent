"""Portable identity and ambiguity contracts, without external services."""
from collections import OrderedDict
import json
from pathlib import Path

import pytest

from src.markdown_proposal import (
    EvidenceItem, ProposalQualityError, SectionPlan, _SectionDraft, _ImageAsset, _evidence,
    _source_labels, _source_manifest, _validate_ids,
)
from src.query_result import SearchHit
from src.render_pptx import RenderPptxError, _ids_for, _load_sources, _sentence_items


def item(source, page, document="", version=""):
    return EvidenceItem("S1", source, page, "content", [], document_id=document, version_id=version)


def labels(*items):
    plan = SectionPlan("sec", "标题", 2, "目标", ["查询"])
    return _source_labels({"sec": _SectionDraft(plan, "", list(items), {})})


def test_same_name_different_page_and_same_name_versions_get_unique_labels():
    a = item("a/技术.pdf", 1, "doc_a", "v1")
    b = item("b/技术.pdf", 2, "doc_b", "v1")
    v2 = item("a/技术.pdf", 1, "doc_a", "v2")
    found = labels(a, b, v2)
    assert len(set(found.values())) == 3
    assert all("技术.pdf" in label for label in found.values())
    assert found == labels(v2, b, a)


def test_distinct_names_and_single_name_keep_original_display():
    assert set(labels(item("a/甲.pdf", 12), item("b/乙.pdf", 12)).values()) == {"甲.pdf", "乙.pdf"}
    assert set(labels(item("a/甲.pdf", 12)).values()) == {"甲.pdf"}
    assert labels() == {}


def test_absolute_paths_never_leak_in_labels_or_sidecar():
    a = item(r"C:\secret\a\技术.pdf", 12)
    b = item(r"D:\private\b\技术.pdf", 12)
    mapping = labels(a, b)
    markdown = "\n".join(f"[来源: {label}, 第 12 页]" for label in mapping.values())
    manifest = _source_manifest(markdown, {}, used_evidence=[a, b], labels=mapping)
    assert len(manifest["sources"]) == 2
    output = json.dumps(manifest, ensure_ascii=False) + markdown
    assert "secret" not in output and "private" not in output and "C:" not in output and "D:" not in output


def test_unknown_or_literal_model_citation_rejected():
    one = item("a.pdf", 1)
    for text in ("错误 [S2]", "[来源: a.pdf, 第 1 页]"):
        with pytest.raises(ValueError):
            _validate_ids(text, [one], {})


def test_evidence_separates_versions_and_deduplicates_same_identity():
    def hit(version):
        return SearchHit("same.pdf", 12, "", "", None, metadata={"document_id": "doc", "version_id": version})
    result = _evidence([hit("v1"), hit("v1"), hit("v2")])
    assert [e.version_id for e in result] == ["v1", "v2"]


def test_manifest_does_not_include_unreferenced_candidate():
    used = item("used.pdf", 1, "doc1", "ver1")
    unused = item("unused.pdf", 1, "doc2", "ver2")
    mapping = labels(used, unused)
    result = _source_manifest("[来源: used.pdf, 第 1 页]", {}, used_evidence=[used], labels=mapping)
    assert [source["filename"] for source in result["sources"]] == ["used.pdf"]
    assert result["sources"][0]["document_id"] == "doc1"
    with pytest.raises(ProposalQualityError):
        _source_manifest("[来源: unused.pdf, 第 1 页]", {}, used_evidence=[used], labels=mapping)


def test_historical_and_new_sidecar_read_and_ambiguous_match_fails(tmp_path):
    md = tmp_path / "proposal.md"
    old = tmp_path / "sources.json"
    old.write_text(json.dumps({"sources": [{"source_id": "S1", "filename": "a.pdf", "pages": [12]}]}), encoding="utf-8")
    sources = _load_sources(old, md, "", [])
    assert _ids_for("[来源: a.pdf, 第 12 页]", sources) == ["S1"]
    assert sources["S1"]["identity_status"] == "unknown"
    old.write_text(json.dumps({"sources": [{"source_id": "S1", "filename": "a.pdf", "pages": [12], "document_id": "doc1", "version_id": "v1"}]}), encoding="utf-8")
    sources = _load_sources(old, md, "", [])
    assert _ids_for("[来源: a.pdf, 第 12 页]", sources) == ["S1"]
    assert sources["S1"]["identity_status"] == "known"
    ambiguous = OrderedDict([(key, {"filename": "a.pdf", "pages": ["12"]}) for key in ("S1", "S2")])
    with pytest.raises(RenderPptxError, match="ambiguous"):
        _ids_for("[来源: a.pdf, 第 12 页]", ambiguous)


def test_ppt_separates_citations_per_sentence_in_one_paragraph():
    sources = OrderedDict([(key, {"filename": name, "pages": ["12"]}) for key, name in (("S1", "a.pdf"), ("S2", "b.pdf"))])
    items = _sentence_items("容量 500 kW。[来源: a.pdf, 第 12 页]容量 800 kW。[来源: b.pdf, 第 12 页]", sources)
    assert items == [("容量 500 kW。", ["S1"]), ("容量 800 kW。", ["S2"])]


def test_collision_image_source_matches_its_structured_evidence():
    a = item("a/技术.pdf", 12, "doc_a", "v1")
    b = item("b/技术.pdf", 12, "doc_b", "v1")
    mapping = labels(a, b)
    image = _ImageAsset("section", "IMG1", "LLM", Path("unused"), "assets/image-001.png", "图", b)
    manifest = _source_manifest("", {("section", "IMG1"): image}, used_evidence=[b], labels=mapping)
    assert len(manifest["sources"]) == 1
    assert manifest["sources"][0]["document_id"] == "doc_b"
    assert manifest["images"][0]["source_id"] == manifest["sources"][0]["source_id"]
    assert manifest["images"][0]["source_file"] == manifest["sources"][0]["filename"]
