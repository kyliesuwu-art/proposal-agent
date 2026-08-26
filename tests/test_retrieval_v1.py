"""检索 V1 的纯离线规则与 fake-store 编排测试。"""

from __future__ import annotations

from src import pipeline
from src.retrieval import (
    explicit_requested_type,
    normalize_proposal_type,
    rerank_candidates,
    select_supporting_context,
    type_match,
)


def _record(source: str, page: int, *, title: str, content: str, distance: float, proposal_type: str = "") -> dict:
    return {
        "source_file": source,
        "slide_number": page,
        "title": title,
        "content": content,
        "distance": distance,
        "proposal_type": proposal_type,
        "images": [],
    }


def test_type_normalization_and_related_match_are_small_and_explicit() -> None:
    assert normalize_proposal_type("工商业光储充一体化解决方案") == "光储充"
    assert explicit_requested_type("请做一份源网荷储方案") == ("源网荷储", "源网荷储")
    assert type_match("光储充", "光储充一体化")[0] == "exact"
    assert type_match("光储充", "光伏")[0] == "related"
    assert type_match("风电", "配电")[0] == "none"


def test_rerank_keeps_strong_vector_hit_ahead_of_weak_type_signal_and_deduplicates() -> None:
    records = [
        _record("语义最相关.pdf", 1, title="施工方案", content="电缆敷设与验收", distance=0.05, proposal_type="配电"),
        _record("类型相同.pdf", 1, title="光储充", content="一般介绍", distance=0.70, proposal_type="光储充"),
        _record("语义最相关.pdf", 1, title="旧重复", content="重复", distance=0.30, proposal_type="光储充"),
    ]

    ranked = rerank_candidates(records, "电缆施工要求", "光储充", limit=10)

    assert [item["source_file"] for item in ranked] == ["语义最相关.pdf", "类型相同.pdf"]
    assert ranked[0]["retrieval"]["type_match"] == "none"
    assert ranked[1]["retrieval"]["type_match"] == "exact"
    assert "rerank_score" in ranked[0]["retrieval"]


def test_supporting_context_excludes_unrelated_pages_and_obeys_budgets() -> None:
    primary = _record("A.pdf", 2, title="储能系统配置", content="储能 PCS 电池配置与并网要求" * 20, distance=0.1)
    related = _record("A.pdf", 3, title="储能系统参数", content="PCS 并网保护参数", distance=0.2)
    unrelated = _record("A.pdf", 1, title="公司简介", content="企业文化与联系方式", distance=0.2)
    other_file = _record("B.pdf", 3, title="储能系统参数", content="PCS 并网保护参数", distance=0.2)

    primaries, supporting = select_supporting_context(
        [primary],
        {("A.pdf", 2): [related, unrelated, other_file]},
        "储能并网配置",
        max_pages_per_file=2,
        max_total_pages=2,
        max_total_characters=10_000,
    )

    assert primaries == [primary]
    assert [(item["source_file"], item["slide_number"]) for item in supporting] == [("A.pdf", 3)]
    assert supporting[0]["supporting_reason"].startswith("shared_keywords:")


def test_short_primary_allows_one_supporting_page_but_character_budget_can_block_it() -> None:
    primary = _record("A.pdf", 1, title="概述", content="很短", distance=0.1)
    adjacent = _record("A.pdf", 2, title="补充", content="足够长的补充内容", distance=0.2)
    _, included = select_supporting_context(
        [primary], {("A.pdf", 1): [adjacent]}, "无关问题", max_total_characters=1_000
    )
    _, blocked = select_supporting_context(
        [primary], {("A.pdf", 1): [adjacent]}, "无关问题", max_total_characters=3
    )
    assert included[0]["supporting_reason"] == "primary_content_short"
    assert blocked == []


class _FallbackStore:
    def __init__(self) -> None:
        self.calls: list[str | None] = []

    def count(self) -> int:
        return 3

    def search(self, _query: str, n_results: int, proposal_type: str | None = None) -> list[dict]:
        assert n_results == 30
        self.calls.append(proposal_type)
        if proposal_type is not None:
            return []
        return [
            _record("A.pdf", 1, title="光储充方案", content="光伏储能充电方案", distance=0.1, proposal_type="光储充"),
            _record("B.pdf", 1, title="配电方案", content="配电系统", distance=0.2, proposal_type="配电"),
        ]

    def get_adjacent_slides(self, _source: str, _page: int, window: int) -> list[dict]:
        assert window == 1
        return []


class _NoServiceLLM:
    def rewrite_query(self, _question: str) -> dict:
        return {"queries": ["光储充方案"], "proposal_type": "光储充"}

    def generate(self, _prompt: str, system_prompt: str = "") -> str:
        assert system_prompt
        return "正文。[来源: A.pdf, 第 1 页]"


def test_query_only_attempts_type_filter_when_user_explicitly_requests_and_falls_back(monkeypatch) -> None:
    store = _FallbackStore()
    monkeypatch.setattr(pipeline, "VectorStore", lambda: store)
    monkeypatch.setattr(pipeline, "LLMClient", _NoServiceLLM)

    result = pipeline.query("需要光储充方案")

    assert any(value is not None for value in store.calls)
    assert None in store.calls
    assert result.retrieval_metadata["user_explicit_type_requested"] is True
    assert result.retrieval_metadata["type_filter_fallback"] is True
    assert any("已回退" in warning for warning in result.warnings)


def test_query_does_not_hard_filter_when_type_only_comes_from_rewrite(monkeypatch) -> None:
    store = _FallbackStore()
    monkeypatch.setattr(pipeline, "VectorStore", lambda: store)
    monkeypatch.setattr(pipeline, "LLMClient", _NoServiceLLM)

    result = pipeline.query("construction request")

    assert store.calls == [None]
    assert result.retrieval_metadata["user_explicit_type_requested"] is False
    assert result.retrieval_metadata["type_filter_attempted"] is False
    assert result.retrieval_metadata["type_filter_fallback"] is False
