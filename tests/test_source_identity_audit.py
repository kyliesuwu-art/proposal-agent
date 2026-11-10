"""Offline source identity investigation across retrieval and delivery."""
import functools
import json
import re
from pathlib import Path

import pytest
from docx import Document
from pptx import Presentation

from src import main, markdown_proposal, pipeline
from src.application import GenerateRequest, ProposalWorkflow
from src.domain import build_document_identity
from src.hybrid_v2 import HybridTestStore, LocalHashEmbeddingFunction
from src.render_pptx import build_slides_markdown, render_pptx
from src.render_word import render_word


class TwoEvidenceLLM:
    connection_settings = {"model": "offline-scripted"}

    def __init__(self):
        self.evidence_prompt = None

    def generate(self, prompt, system_prompt=""):
        if "规划助手" in system_prompt:
            return json.dumps({"title": "医院光伏方案", "sections": [{
                "section_id": "capacity", "heading": "光伏容量", "level": 2,
                "writing_goal": "比较历史光伏容量", "retrieval_queries": ["医院 光伏 容量"],
            }, {"section_id": "design", "heading": "系统设计", "level": 2,
                "writing_goal": "参考历史设计", "retrieval_queries": ["医院 光伏 容量"]},
                {"section_id": "delivery", "heading": "实施安排", "level": 2,
                 "writing_goal": "说明实施边界", "retrieval_queries": ["医院 光伏 容量"]}]}, ensure_ascii=False)
        if "审查完整" in system_prompt:
            return '{"issues":[]}'
        self.evidence_prompt = prompt
        mapping = {}
        for match in re.finditer(r"\[(S\d+)\] 证据角色：.*?正文：(.*?)(?=\n\[S\d+\] 证据角色：|\Z)", prompt, re.S):
            for capacity in ("500", "800"):
                if capacity in match.group(2):
                    mapping[capacity] = match.group(1)
        assert set(mapping) == {"500", "800"}, f"fixture must expose both sources: {prompt}"
        assert mapping["500"] != mapping["800"]
        return f"历史资料显示光伏容量为 500 kW。[{mapping['500']}]\n\n另一份历史资料显示光伏容量为 800 kW。[{mapping['800']}]"


@pytest.fixture
def evidence_run(tmp_path, monkeypatch):
    monkeypatch.setattr("src.hybrid_v2.DashScopeEmbeddingFunction", LocalHashEmbeddingFunction)
    source_root = tmp_path / "source"
    inputs = [("医院A", "500"), ("医院B", "800")]
    identities = []
    db = tmp_path / "index"
    store = HybridTestStore(db, LocalHashEmbeddingFunction())
    try:
        for hospital, capacity in inputs:
            path = source_root / hospital / "技术方案.pdf"
            path.parent.mkdir(parents=True)
            path.write_bytes(("%PDF fixture " + hospital + " " + capacity).encode("utf-8"))
            identity = build_document_identity(path, source_root)
            identities.append(identity)
            store.add_version(identity, [{"page_number": 12, "title": "医院光伏容量", "content": f"{hospital} PCS 光伏容量为 {capacity} kW。"}])
        semantic = store.semantic_search("医院 光伏 容量", 10)
        lexical = store.lexical_search("PCS", 10)
    finally:
        store.close()
    retriever = functools.partial(pipeline.retrieve_evidence, db=db)
    result = retriever(["医院 光伏 容量", "PCS"])
    evidence = markdown_proposal._evidence(result.hits)
    llm = TwoEvidenceLLM()
    workflow = ProposalWorkflow(llm_factory=lambda: llm, retriever=retriever,
        markdown_generator=markdown_proposal.generate_markdown_proposal, word_renderer=render_word,
        slides_builder=build_slides_markdown, pptx_renderer=render_pptx,
        run_log_writer=main._write_proposal_run_log, request_writer=main._write_proposal_request)
    output = tmp_path / "delivery"
    delivery = workflow.run(GenerateRequest("医院光伏建设方案", output, ("md", "docx", "pptx")))
    markdown = delivery.markdown_path.read_text(encoding="utf-8")
    sidecar = json.loads((output / "proposal.sources.json").read_text(encoding="utf-8"))
    plan = json.loads(delivery.slide_plan_path.read_text(encoding="utf-8"))
    word_text = "\n".join(p.text for p in Document(delivery.word_path).paragraphs)
    ppt_text = "\n".join(shape.text for slide in Presentation(delivery.pptx_path).slides for shape in slide.shapes if shape.has_text_frame)
    return locals()


def test_storage_retrieval_and_evidence_keep_distinct_identities(evidence_run):
    d = evidence_run
    expected = {"医院A/技术方案.pdf", "医院B/技术方案.pdf"}
    assert {i.source_key for i in d["identities"]} == expected
    assert len({i.document_id for i in d["identities"]}) == 2
    assert len({i.version_id for i in d["identities"]}) == 2
    for mode in ("semantic", "lexical"):
        assert {r["source_key"] for r in d[mode]} == expected
        assert len({r["page_id"] for r in d[mode]}) == 2
    hits = d["result"].hits
    assert {h.source_file for h in hits} == expected
    assert all(h.page_number == 12 for h in hits)
    assert len({h.metadata["page_id"] for h in hits}) == 2
    assert {h.metadata["document_id"] for h in hits} == {i.document_id for i in d["identities"]}
    assert {h.metadata["version_id"] for h in hits} == {i.version_id for i in d["identities"]}
    assert {h.metadata["source_key"] for h in hits} == expected
    assert {e.file_name for e in d["evidence"]} == expected
    assert len({e.evidence_id for e in d["evidence"]}) == 2
    assert d["llm"].evidence_prompt is not None


def test_delivery_mapping_matches_each_capacity(evidence_run):
    d = evidence_run
    records = {record["document_id"]: record for record in d["sidecar"]["sources"]}
    assert len(records) == 2
    by_capacity = {"500": records[d["identities"][0].document_id], "800": records[d["identities"][1].document_id]}
    for capacity, record in by_capacity.items():
        line = next(line for line in d["markdown"].splitlines() if f"{capacity} kW" in line)
        assert f"[来源: {record['filename']}, 第 12 页]" in line
        assert any(f"{capacity} kW" in line and record["filename"] in line for line in d["word_text"].splitlines())
        matched = [(bullet, ids) for slide in d["plan"]["slides"]
                   for bullet, ids in zip(slide.get("bullets", []), slide.get("bullet_source_ids", []))
                   if f"{capacity} kW" in bullet]
        assert matched and all(ids == [record["source_id"]] for _, ids in matched)
    assert "500 kW" in d["ppt_text"] and "800 kW" in d["ppt_text"]
    assert by_capacity["500"]["filename"] != by_capacity["800"]["filename"]


def test_delivery_preserves_two_distinct_source_identities(evidence_run):
    d = evidence_run
    assert "500 kW" in d["markdown"] and "800 kW" in d["markdown"]
    sources = d["sidecar"]["sources"]
    assert len(sources) == 2, f"two distinct sources required; actual: {sources!r}"
    assert len({s["source_id"] for s in sources}) == 2
    assert len(d["plan"]["sources"]) == 2
