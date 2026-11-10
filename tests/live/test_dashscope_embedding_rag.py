"""Opt-in real text-embedding-v3 + hybrid RAG acceptance test (billable, disabled by default).

Enable only for an explicit, budgeted acceptance run:

    DASHSCOPE_LIVE_APPROVED=1 DASHSCOPE_API_KEY=... uv run python -m pytest tests/live -q

A disposable index is built inside the pytest temporary directory with the real
DashScopeEmbeddingFunction. The formal runtime_data/word_test_db library is never
opened, copied or written, so the 256-dimensional offline hash indexes cannot be
reused to fake 1024-dimensional semantic acceptance.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.adapters.vector_store import DashScopeEmbeddingFunction
from src.config import RAG_DB_PATH
from src.domain.models import DocumentIdentity
from src.hybrid_v2 import HybridTestStore, page_id

pytestmark = pytest.mark.skipif(
    os.environ.get("DASHSCOPE_LIVE_APPROVED") != "1",
    reason="set DASHSCOPE_LIVE_APPROVED=1 for the billable DashScope embedding run",
)

_EXPECTED_DIMENSION = 1024


def _identity() -> DocumentIdentity:
    return DocumentIdentity(
        source_key="live-acceptance/synthetic-corpus.pdf",
        document_id="doc_live_acceptance_synthetic",
        content_hash="0" * 64,
        version_id="ver_live_acceptance_1",
    )


def _corpus() -> list[dict]:
    return [
        {
            "page_number": 1,
            "title": "光伏储能系统配置",
            "content": "医院光储一体化方案采用磷酸铁锂电池，配置 500kW 光伏阵列与 1MWh 储能容量，配合 EMS 能量管理系统统一调度。",
        },
        {
            "page_number": 2,
            "title": "储能变流器 PCS 参数",
            "content": "储能变流器 PCS 额定功率 250kW，支持并网与离网切换，逆变效率不低于 98%，配套升压变压器接入配电室。",
        },
        {
            "page_number": 3,
            "title": "低压配电安全规范",
            "content": "低压配电系统采用 TN-S 接地形式，配置剩余电流保护装置与浪涌保护器 SPD，保障用电设备安全。",
        },
        {
            "page_number": 4,
            "title": "职工食堂餐饮管理办法",
            "content": "职工食堂每周公示菜单与食材采购清单，餐饮从业人员持健康证上岗，餐具执行高温消毒流程。",
        },
        {
            "page_number": 5,
            "title": "园区绿化养护规程",
            "content": "办公园区绿化养护包括乔灌木修剪、草坪灌溉、施肥与病虫害防治，雨季加强排水沟清理。",
        },
        {
            "page_number": 6,
            "title": "会议室预约管理制度",
            "content": "会议室通过行政系统在线预约，使用结束后关闭投影、空调与照明设备，并恢复桌椅摆放。",
        },
    ]


def test_real_embedding_index_answers_semantic_queries_in_isolation(tmp_path: Path) -> None:
    if not os.environ.get("DASHSCOPE_API_KEY", "").strip():
        pytest.skip("DASHSCOPE_API_KEY is not configured")

    formal_root = Path(RAG_DB_PATH).resolve()
    temp_root = (tmp_path / "live_rag_db").resolve()
    assert temp_root != formal_root
    assert not str(temp_root).startswith(str(formal_root))
    assert not temp_root.exists()

    embedding_function = DashScopeEmbeddingFunction()
    probe = embedding_function(["储能电池容量与光伏装机配置"])
    assert len(probe) == 1
    assert len(probe[0]) == _EXPECTED_DIMENSION

    identity = _identity()
    store = HybridTestStore(temp_root, embedding_function)
    try:
        corpus = _corpus()
        assert store.add_version(identity, corpus) == len(corpus)
        assert store.count() == len(corpus)

        hits = store.search("医院光伏配储项目的容量是多少", limit=4)
        assert hits
        top_ids = [hit["page_id"] for hit in hits]
        relevant = {page_id(identity, number) for number in (1, 2)}
        unrelated = {page_id(identity, number) for number in (4, 5, 6)}
        matched_relevant = [top_ids.index(identifier) for identifier in relevant if identifier in top_ids]
        assert matched_relevant, f"relevant pages missing from {top_ids}"
        best_relevant = min(matched_relevant)
        assert all(best_relevant < top_ids.index(identifier) for identifier in unrelated if identifier in top_ids)
        assert "retrieval" in hits[0]
    finally:
        store.close()

    for path in temp_root.rglob("*"):
        assert str(path.resolve()).startswith(str(temp_root))
