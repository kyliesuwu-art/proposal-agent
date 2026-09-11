"""结构化查询、联合引用与 CLI Markdown 输出的离线测试。"""

import json
import sys

import pytest

from src import main, pipeline
from src.markdown_proposal import ProposalGenerationError, ProposalQualityError
from src.query_result import Citation, QueryResult


class FakeStore:
    """不连接 Chroma 的确定性检索替身。"""

    def count(self) -> int:
        return 2

    def search(self, _query, n_results, proposal_type=None) -> list[dict]:
        assert n_results == 30
        assert proposal_type is None
        return [
            {
                "source_file": "方案A.pdf",
                "slide_number": 1,
                "title": "A 首页",
                "content": "A 内容",
                "distance": 0.1,
                "images": [{"path": "images/a.png", "caption": "A 图"}],
                "proposal_type": "光伏",
            },
            {
                "source_file": "方案B.docx",
                "slide_number": 1,
                "title": "B 首页",
                "content": "B 内容",
                "distance": 0.2,
                "images": [],
                "proposal_type": "储能",
            },
        ]

    def get_adjacent_slides(self, _source_file, _slide_number, window) -> list[dict]:
        assert window == 1
        return []


class FakeLLM:
    def rewrite_query(self, _query: str) -> dict:
        return {"queries": ["测试检索"], "proposal_type": ""}

    def generate(self, _prompt: str, system_prompt: str = "") -> str:
        assert "可引用来源集合" in _prompt
        assert "Slide" not in _prompt
        assert system_prompt
        return "采用 A 方案。[来源: 方案A.pdf, 第 1 页]"


def _query_result(monkeypatch) -> QueryResult:
    monkeypatch.setattr(pipeline, "VectorStore", FakeStore)
    monkeypatch.setattr(pipeline, "LLMClient", FakeLLM)
    return pipeline.query("测试需求")


def test_same_page_number_from_different_files_remains_distinct(monkeypatch) -> None:
    result = _query_result(monkeypatch)

    assert {(hit.source_file, hit.page_number) for hit in result.hits} == {
        ("方案A.pdf", 1),
        ("方案B.docx", 1),
    }
    assert {(citation.source_file, citation.page_number) for citation in result.citations} == {
        ("方案A.pdf", 1),
        ("方案B.docx", 1),
    }
    assert result.hits[0].page_number == 1  # 旧 slide_number 的输出映射


def test_valid_and_invalid_compound_citations_are_distinguished() -> None:
    citations = [Citation("方案A.pdf", 1)]

    assert pipeline._validate_structured_citations(
        "有效。[来源: 方案A.pdf, 第 1 页]", citations
    ) == []
    assert pipeline._validate_structured_citations(
        "页码错误。[来源: 方案A.pdf, 第 2 页]", citations
    ) == ["引用不在本次检索来源中：[来源: 方案A.pdf, 第 2 页]"]
    assert pipeline._validate_structured_citations(
        "文件错误。[来源: 方案B.pdf, 第 1 页]", citations
    ) == ["引用不在本次检索来源中：[来源: 方案B.pdf, 第 1 页]"]


def test_query_result_renders_stable_markdown_with_sources_and_images(monkeypatch) -> None:
    result = _query_result(monkeypatch)

    markdown = pipeline.render_markdown(result)

    assert "# 方案查询结果" in markdown
    assert "[来源: 方案A.pdf, 第 1 页]" in markdown
    assert "[来源: 方案B.docx, 第 1 页]" in markdown
    assert "图片 1 张" in markdown
    assert "Slide 1" not in markdown


def test_cli_output_writes_only_requested_path(monkeypatch, tmp_path, capsys) -> None:
    result = QueryResult(
        original_query="测试需求",
        rewritten_queries=["测试检索"],
        hits=[],
        proposal_markdown="正文",
        citations=[Citation("方案A.pdf", 1)],
    )
    monkeypatch.setattr(main.pipeline, "query_rag", lambda _query: result)
    output = tmp_path / "query-result.md"
    monkeypatch.setattr(sys, "argv", ["main.py", "query", "测试需求", "--output", str(output)])

    main.main()

    assert output.read_text(encoding="utf-8") == pipeline.render_markdown(result)
    assert "已写入 Markdown" in capsys.readouterr().out


def test_query_entry_points_import_without_running_services() -> None:
    assert callable(pipeline.query)
    assert callable(pipeline.render_markdown)
    assert callable(main.main)


def test_help_returns_without_opening_services(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["main.py", "--help"])

    main.main()

    assert "proposal" in capsys.readouterr().out


def test_proposal_help_returns_without_opening_services(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["main.py", "proposal", "--help"])

    main.main()

    assert "--debug" in capsys.readouterr().out


def test_real_cache_image_shape_survives_hit_conversion_without_database_access() -> None:
    record = {
        "source_key": "资料.pdf", "source_file": "资料.pdf", "page_number": 3, "slide_number": 3,
        "title": "", "content": "### 源网荷储总体架构\n正文", "distance": 0.1,
        "images": [{"path": "assets/doc/version/page_3_0.png", "caption": ""}],
        "retrieval": {"rrf_score": 0.1},
    }
    hit = pipeline._hit_from_record(record, "primary")
    assert hit.images == record["images"]
    assert hit.images[0]["path"].startswith("assets/")


@pytest.mark.parametrize("debug", [False, True])
def test_proposal_cli_failure_is_nonzero_and_debug_can_print_traceback(monkeypatch, capsys, debug: bool, tmp_path) -> None:
    class FailingLLM:
        def __init__(self):
            raise RuntimeError("safe failure")
    monkeypatch.setattr(main, "LLMClient", FailingLLM)
    argv = ["main.py", "proposal", "需求", "--output", str(tmp_path / "proposal.md")]
    if debug:
        argv.append("--debug")
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as exited:
        main.main()

    output = capsys.readouterr()
    assert exited.value.code == 1
    assert "[planning]" in output.err
    assert "safe failure" in output.err
    assert ("Traceback" in output.err) is debug


def test_proposal_cli_records_complete_deterministic_quality_gate_error(monkeypatch, tmp_path) -> None:
    class ConfiguredLLM:
        connection_settings = {"model": "fake"}

    def fail_quality_gate(*_args, **_kwargs):
        raise ProposalGenerationError("quality_gate", ProposalQualityError("范围越界：章节“范围”将非核心内容作为核心模块"))

    output = tmp_path / "proposal.md"
    monkeypatch.setattr(main, "LLMClient", ConfiguredLLM)
    monkeypatch.setattr(main, "generate_markdown_proposal", fail_quality_gate)
    monkeypatch.setattr(sys, "argv", ["main.py", "proposal", "需求", "--output", str(output)])

    with pytest.raises(SystemExit):
        main.main()

    log = json.loads((tmp_path / "proposal.run.log").read_text(encoding="utf-8"))
    assert log["quality_gate_error_message"] == "范围越界：章节“范围”将非核心内容作为核心模块"


def test_retrieve_evidence_merges_planned_queries_without_llm(monkeypatch) -> None:
    class FakeHybridStore:
        queries: list[str] = []
        def __init__(self, _db, **_kwargs) -> None: pass
        def search(self, query, _limit):
            self.queries.append(query)
            return [{"page_id": f"id-{query}", "source_key": "A.pdf", "source_file": "A.pdf", "page_number": 1, "slide_number": 1, "title": "A", "content": "正文", "images": [], "retrieval": {"rrf_score": 0.1}}]
        def get_adjacent_pages(self, *_args): return []
        def close(self): pass
    monkeypatch.setattr(pipeline, "HybridTestStore", FakeHybridStore)
    result = pipeline.retrieve_evidence(["查询一", "查询二"], db="unused", top_n=3)
    assert FakeHybridStore.queries == ["查询一", "查询二"]
    assert result.rewritten_queries == ["查询一", "查询二"]
    assert [(hit.source_file, hit.page_number) for hit in result.hits] == [("A.pdf", 1)]
