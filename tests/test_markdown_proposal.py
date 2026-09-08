"""Offline tests for the single-file Markdown proposal workflow."""

from pathlib import Path
import json
import re

import pytest

from src import markdown_proposal
from src.markdown_proposal import ProposalGenerationError, ProposalQualityError, _confirmation_items, _image_catalog, _normalise_markdown, _plan_from, _quality_gate, _requested_confirmation_items, generate_markdown_proposal
from src.query_result import QueryResult, SearchHit


PLAN = '''{"title":"工商业储能方案","sections":[
{"section_id":"overview","heading":"项目概述","level":2,"writing_goal":"说明目标","retrieval_queries":["项目目标"]},
{"section_id":"configuration","heading":"系统配置","level":2,"writing_goal":"说明配置","retrieval_queries":["储能容量"]},
{"section_id":"implementation","heading":"实施安排","level":2,"writing_goal":"说明实施","retrieval_queries":["实施要求"]}
]}'''


class FakeLLM:
    def __init__(self, *, invalid_evidence: bool = False, invalid_image: bool = False, remove_revision_citation: bool = False) -> None:
        self.invalid_evidence = invalid_evidence
        self.invalid_image = invalid_image
        self.remove_revision_citation = remove_revision_citation
        self.calls: list[tuple[str, str]] = []

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        self.calls.append((system_prompt, prompt))
        if "规划助手" in system_prompt:
            return PLAN
        if "审查完整" in system_prompt:
            return '{"issues":[{"section_id":"configuration","issue_type":"clarity","instruction":"补充已知容量"}]}'
        if "只输出修订后的" in system_prompt:
            return "修订后的容量为 430kWh。" if self.remove_revision_citation else "修订后的容量为 430kWh。[S1]"
        if "无效 ID" in prompt:
            return "已修复正文。[S1]"
        if "当前章节：项目概述" in prompt:
            return "项目目标来自历史资料。[S1]" if not self.invalid_evidence else "项目目标。[S9]"
        if "当前章节：系统配置" in prompt:
            return "系统容量为430kWh。[S1] [IMG1]" if not self.invalid_image else "系统图 [IMG9]"
        return "园区建设周期【待确认】。"


def _image_root(tmp_path: Path) -> Path:
    root = tmp_path / "knowledge"
    image = root / "images" / "storage.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"fake-png")
    return root


def _retriever(calls: list[list[str]]):
    def retrieve(queries: list[str]) -> QueryResult:
        calls.append(queries)
        if queries == ["实施要求"]:
            return QueryResult("", queries, [], "", [])
        return QueryResult("", queries, [SearchHit(
            source_file="cache/项目资料/历史方案.pdf", page_number=12, title="储能系统架构",
            content="系统额定容量为430kWh。", distance=0.1,
            images=[{"path": "images/storage.png", "caption": "储能系统架构图"}],
        )], "", [])
    return retrieve


def _generate(tmp_path: Path, llm: FakeLLM, *, request: str = "建设工商业储能") -> tuple[Path, list[list[str]]]:
    output = tmp_path / "outputs" / "proposal.md"
    calls: list[list[str]] = []
    generate_markdown_proposal(request, output, llm=llm, retriever=_retriever(calls), image_root=_image_root(tmp_path))
    return output, calls


def test_revision_keeps_raw_evidence_ids_and_final_source_list(tmp_path: Path) -> None:
    llm = FakeLLM()
    output, calls = _generate(tmp_path, llm)
    text = output.read_text(encoding="utf-8")

    assert calls[0][0] == "建设工商业储能"
    assert calls[1:] == [["项目目标"], ["储能容量"], ["实施要求"]]
    assert "项目目标来自历史资料。[来源: 历史方案.pdf, 第 12 页]" in text
    assert "修订后的容量为 430kWh。[来源: 历史方案.pdf, 第 12 页]" in text
    assert "## 来源与依据\n\n- 历史方案.pdf：第 12 页" in text
    assert "（本方案没有使用可引用资料。）" not in text
    assert "cache/" not in text
    assert "[S1]" not in text and "[IMG1]" not in text
    revision_prompt = next(prompt for system, prompt in llm.calls if "只输出修订后的" in system)
    assert "修订后的容量为 430kWh。[S1]" not in revision_prompt
    assert "系统容量为430kWh。[S1] [IMG1]" in revision_prompt


def test_removed_revision_citation_is_removed_from_source_list(tmp_path: Path) -> None:
    class OnlyRevisedCitation(FakeLLM):
        def generate(self, prompt: str, system_prompt: str = "") -> str:
            if "只输出修订后的" in system_prompt:
                return "系统容量【待确认】。"
            if "当前章节：项目概述" in prompt:
                return "项目背景【待确认】。"
            if "当前章节：系统配置" in prompt:
                return "系统容量为430kWh。[S1]"
            return super().generate(prompt, system_prompt)
    output, _calls = _generate(tmp_path, OnlyRevisedCitation())
    text = output.read_text(encoding="utf-8")
    assert "[来源:" not in text
    assert "（本方案没有使用可引用资料。）" in text
    assert "历史方案.pdf：" not in text


def test_image_is_copied_with_stable_relative_path_and_caption(tmp_path: Path) -> None:
    class NoReviewLLM(FakeLLM):
        def generate(self, prompt: str, system_prompt: str = "") -> str:
            if "审查完整" in system_prompt:
                return '{"issues":[]}'
            return super().generate(prompt, system_prompt)
    output, _calls = _generate(tmp_path, NoReviewLLM())
    text = output.read_text(encoding="utf-8")
    assert "![储能系统架构图](assets/image-001.png)" in text
    assert "图片来源：历史方案.pdf，第 12 页" in text
    assert (output.parent / "assets" / "image-001.png").read_bytes() == b"fake-png"
    assert [path.name for path in (output.parent / "assets").iterdir()] == ["image-001.png"]
    assert text.startswith("> ⚠️")  # One relevant image is retained, but the 3-image target is unmet.


def test_missing_image_becomes_warned_draft_without_losing_markdown(tmp_path: Path) -> None:
    class NoReviewLLM(FakeLLM):
        def generate(self, prompt: str, system_prompt: str = "") -> str:
            if "审查完整" in system_prompt:
                return '{"issues":[]}'
            return super().generate(prompt, system_prompt)
    root = _image_root(tmp_path)
    (root / "images" / "storage.png").unlink()
    result = generate_markdown_proposal("建设工商业储能", tmp_path / "proposal.md", llm=NoReviewLLM(),
                                       retriever=_retriever([]), image_root=root, return_result=True)
    text = result.path.read_text(encoding="utf-8")
    assert result.quality_status == "DRAFT_WITH_WARNINGS" and text.startswith("> ⚠️")
    assert "assets/image" not in text and any("图片" in warning for warning in result.warnings)


def test_image_without_caption_description_or_page_title_is_not_offered() -> None:
    hit = SearchHit(source_file="cache/a.pdf", page_number=1, title="", content="", distance=0.1, images=[{"path": "images/a.png"}])
    assert _image_catalog(markdown_proposal._evidence([hit])) == {}


def test_quality_gate_reports_visible_citation_without_source_list(tmp_path: Path) -> None:
    markdown = "# 标题\n\n## 1. 正文\n\n事实。[来源: 文件.pdf, 第 1 页]\n\n## 来源与依据\n\n（本方案没有使用可引用资料。）\n"
    assert any("来源列表为空" in item for item in _quality_gate(markdown, tmp_path / "proposal.md", {}, ""))


def test_unknown_ids_are_repaired_once_before_writing(tmp_path: Path) -> None:
    class NoReviewLLM(FakeLLM):
        def generate(self, prompt: str, system_prompt: str = "") -> str:
            if "审查完整" in system_prompt:
                return '{"issues":[]}'
            return super().generate(prompt, system_prompt)
    for kind in ("evidence", "image"):
        llm = NoReviewLLM(invalid_evidence=kind == "evidence", invalid_image=kind == "image")
        output, _calls = _generate(tmp_path / kind, llm)
        text = output.read_text(encoding="utf-8")
        assert "S9" not in text and "IMG9" not in text
        assert any("无效 ID" in prompt for _system, prompt in llm.calls)


def test_visible_citation_or_image_path_in_raw_revision_is_fatal_after_controlled_repair(tmp_path: Path) -> None:
    class InvalidRevision(FakeLLM):
        def generate(self, prompt: str, system_prompt: str = "") -> str:
            if "只输出修订后的" in system_prompt:
                return "修订。[来源: 历史方案.pdf, 第 12 页]"
            if "修订稿包含无效" in prompt:
                return "仍然错误。![x](images/storage.png)"
            return super().generate(prompt, system_prompt)
    with pytest.raises(ProposalGenerationError, match="citation_validation:configuration"):
        _generate(tmp_path, InvalidRevision())


def test_standard_markdown_and_explicit_heading_coverage(tmp_path: Path) -> None:
    coverage_plan = '''{"title":"测试方案","sections":[
    {"section_id":"one","heading":"项目背景与建设必要性","level":2,"writing_goal":"背景","retrieval_queries":["a"]},
    {"section_id":"two","heading":"建设目标与总体原则","level":2,"writing_goal":"目标","retrieval_queries":["b"]},
    {"section_id":"three","heading":"实施建议","level":2,"writing_goal":"实施","retrieval_queries":["c"]}
    ]}'''
    class CoverageLLM(FakeLLM):
        def generate(self, prompt: str, system_prompt: str = "") -> str:
            if "规划助手" in system_prompt:
                return coverage_plan
            if "审查完整" in system_prompt:
                return '{"issues":[]}'
            if "当前章节：项目背景与建设必要性" in prompt:
                return "**### 项目背景与建设必要性**\n1\\. 背景【待确认】。"
            if "当前章节：建设目标与总体原则" in prompt:
                return "**### 建设目标与总体原则**\n目标【待确认】。"
            return "**### 实施建议**\n建议【待确认】。"
    def empty(_queries): return QueryResult("", [], [], "", [])
    output = tmp_path / "proposal.md"
    request = "方案应包含：\n1. 项目背景与建设必要性\n2. 建设目标与总体原则\n3. 实施建议"
    generate_markdown_proposal(request, output, llm=CoverageLLM(), retriever=empty, image_root=_image_root(tmp_path))
    text = output.read_text(encoding="utf-8")
    assert "### 项目背景与建设必要性" in text and "1. 背景" in text
    assert "**#" not in text and "**##" not in text and "]\\(" not in text and "1\\." not in text


def test_normalise_markdown_repairs_known_structures_is_idempotent_and_preserves_source_text() -> None:
    raw = (
        "**# 标题**\n**## 二级**\n**### 三级**\n"
        "1\\. **\\*\\*用户输入条件\\*\\***：来源文件.pdf，第 12 页\n"
        "\\- 第一阶段\n\u00a0\u00a0- 嵌套项\n"
        "```\n1\\. code must stay escaped\n```\n"
    )
    normalised = _normalise_markdown(raw)
    assert "# 标题\n## 二级\n### 三级" in normalised
    assert "1. **用户输入条件**：来源文件.pdf，第 12 页" in normalised
    assert "- 第一阶段\n  - 嵌套项" in normalised
    assert "1\\. code must stay escaped" in normalised
    assert _normalise_markdown(normalised) == normalised


def test_quality_gate_reports_all_known_final_markdown_violations(tmp_path: Path) -> None:
    markdown = (
        "**# 标题**\n\n1\\. **\\*\\*标签\\*\\***\n\\- 项\n"
        "[S1] [IMG1] cache/path C:\\private\\a /home/private/a\n"
        "### 4.3 待确认项\n- 【待确认】。\n## 待确认项\n"
        "## 来源与依据\n\n（本方案没有使用可引用资料。）\n"
    )
    message = "；".join(_quality_gate(markdown, tmp_path / "proposal.md", {}, ""))
    assert "缺少合法 H1 标题" in message
    assert "结构化 Markdown 转义" in message
    assert "内部证据或图片 ID" in message
    assert "缓存路径或机器绝对路径" in message
    assert "重复或非标准的待确认项标题" in message


def test_final_quality_gate_runs_before_any_proposal_file_is_written(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "outputs" / "proposal.md"
    monkeypatch.setattr(
        markdown_proposal,
        "_final_markdown",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ProposalQualityError("存在无效的结构化 Markdown 转义")),
    )
    with pytest.raises(ProposalGenerationError, match="quality_gate"):
        generate_markdown_proposal("需求", output, llm=FakeLLM(), retriever=_retriever([]), image_root=_image_root(tmp_path))
    assert not output.exists()


def test_confirmation_items_remove_empty_generic_and_duplicate_but_keep_specific_items() -> None:
    body = "\n".join([
        "待确认内容：具体参数待确认【待确认】。",
        "园区面积和负荷曲线需确认【待确认】。",
        "园区面积和负荷曲线需确认【待确认】。",
        "变压器容量及配电电压需确认【待确认】。",
        "【待确认】。",
    ])
    assert _confirmation_items(body) == [
        "园区面积和负荷曲线需确认【待确认】",
        "变压器容量及配电电压需确认【待确认】",
    ]


def test_explicit_ten_missing_parameters_render_as_one_canonical_nonduplicated_checklist() -> None:
    request = (
        "当前未提供园区面积、负荷曲线、变压器容量、配电电压、光伏可安装面积、设备容量、"
        "投资预算、电价、并网条件及建设周期。"
    )
    assert _requested_confirmation_items(request) == [
        "园区面积【待确认】", "负荷曲线【待确认】", "变压器容量【待确认】", "配电电压【待确认】",
        "光伏可安装面积【待确认】", "设备容量【待确认】", "投资预算【待确认】", "电价【待确认】",
        "并网条件【待确认】", "建设周期【待确认】",
    ]


def test_confirmation_items_keeps_short_but_specific_electricity_price_parameter() -> None:
    assert _confirmation_items("电价【待确认】") == ["电价【待确认】"]


def test_confirmation_pseudo_labels_are_removed_without_dropping_following_content() -> None:
    body, extracted = markdown_proposal._extract_confirmation_section(
        "**待确认内容**\n电价和并网条件均【待确认】。\n**待明确事项**：投资预算【待确认】。"
    )
    assert extracted == ""
    assert "待确认内容" not in body and "待明确事项" not in body
    assert "电价和并网条件均【待确认】" in body
    assert "投资预算【待确认】" in body


def test_quality_gate_requires_exact_explicit_confirmation_checklist_and_rejects_pseudo_label(tmp_path: Path) -> None:
    request = "当前未提供园区面积、负荷曲线、变压器容量、配电电压、光伏可安装面积、设备容量、投资预算、电价、并网条件及建设周期。"
    markdown = "# 标题\n\n## 正文\n\n**待确认内容**\n\n## 待确认项\n\n- 园区面积【待确认】\n\n## 来源与依据\n\n（本方案没有使用可引用资料。）\n"
    warnings = _quality_gate(markdown, tmp_path / "proposal.md", {}, request)
    assert "正文残留待确认视觉伪标题" in warnings
    assert "待确认项未完整规范覆盖用户明确参数" in warnings


def test_prompt_contracts_constrain_scope_evidence_and_design_advice() -> None:
    assert "用户未要求的物业、停车、租户收费" in markdown_proposal._PLAN_SYSTEM
    assert "水表管理或普通智慧楼宇功能" in markdown_proposal._WRITE_SYSTEM
    assert "uncited_knowledge_fact" in markdown_proposal._REVIEW_SYSTEM
    assert "unmarked_design_advice" in markdown_proposal._REVIEW_SYSTEM
    assert "删除用户未要求" in markdown_proposal._REVISE_SYSTEM


def test_revised_sections_pass_through_final_normalisation_and_single_confirmation_section(tmp_path: Path) -> None:
    class StructuralRevisionLLM(FakeLLM):
        def generate(self, prompt: str, system_prompt: str = "") -> str:
            if "只输出修订后的" in system_prompt:
                return "1\\. **\\*\\*设计建议\\*\\***：保留来源。[S1]\n\\- 第一阶段\n### 4.3 待确认项\n- 园区面积需确认。"
            return super().generate(prompt, system_prompt)
    output, _calls = _generate(tmp_path, StructuralRevisionLLM())
    text = output.read_text(encoding="utf-8")
    assert "### 4.3 待确认项" not in text
    assert text.count("## 待确认项") == 1
    assert "### 园区边界与负荷" in text and "- 园区面积【待确认】" in text
    assert "1. **设计建议**：保留来源。[来源: 历史方案.pdf, 第 12 页]" in text
    assert "- 第一阶段" in text
    assert "[S1]" not in text and "## 来源与依据\n\n- 历史方案.pdf：第 12 页" in text


def test_model_owned_chinese_numbered_confirmation_content_heading_is_removed(tmp_path: Path) -> None:
    class ChineseConfirmationHeading(FakeLLM):
        def generate(self, prompt: str, system_prompt: str = "") -> str:
            if "当前章节：项目概述" in prompt:
                return "### 十一、待确认内容\n园区面积需确认。"
            return super().generate(prompt, system_prompt)
    output, _calls = _generate(tmp_path, ChineseConfirmationHeading())
    text = output.read_text(encoding="utf-8")
    assert "### 十一、待确认内容" not in text
    assert text.count("## 待确认项") == 1
    assert "### 园区边界与负荷" in text and "- 园区面积【待确认】" in text


def test_nested_missing_output_parents_create_single_markdown(tmp_path: Path) -> None:
    class NoImageLLM(FakeLLM):
        def generate(self, prompt: str, system_prompt: str = "") -> str:
            if "当前章节：系统配置" in prompt:
                return "系统容量为430kWh。[S1]"
            return super().generate(prompt, system_prompt)
    output = tmp_path / "a" / "b" / "c" / "proposal.md"
    calls: list[list[str]] = []
    generate_markdown_proposal("需求", output, llm=NoImageLLM(), retriever=_retriever(calls), image_root=_image_root(tmp_path))
    assert output.is_file()
    assert sorted(path.name for path in output.parent.iterdir()) == ["proposal.md"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(PLAN, "工商业储能方案"), (f"```json\n{PLAN}\n```", "工商业储能方案"), (f"说明如下：\n{PLAN}\n以上。", "工商业储能方案")],
)
def test_plan_json_accepts_json_fences_and_explanatory_wrappers(raw: str, expected: str) -> None:
    assert _plan_from(raw).title == expected


@pytest.mark.parametrize("raw", ["", "不是 JSON", '{"title":"x"}', '{"title":"x","sections":[]}', PLAN.replace('"implementation"', '"overview"')])
def test_plan_json_rejects_invalid_missing_empty_or_duplicate_sections(raw: str) -> None:
    with pytest.raises(ValueError):
        _plan_from(raw)


def test_stage_errors_preserve_cause_for_planning_retrieval_writing_and_write(tmp_path: Path, monkeypatch) -> None:
    class PlanningFailure:
        def generate(self, *_args, **_kwargs): raise RuntimeError("planner unavailable")
    with pytest.raises(ProposalGenerationError, match="planning") as planning:
        generate_markdown_proposal("需求", tmp_path / "plan.md", llm=PlanningFailure(), retriever=_retriever([]), image_root=_image_root(tmp_path))
    assert isinstance(planning.value.__cause__, RuntimeError)

    def failing_retriever(_queries): raise OSError("retrieval offline")
    with pytest.raises(ProposalGenerationError, match="retrieval:overview"):
        generate_markdown_proposal("需求", tmp_path / "retrieve.md", llm=FakeLLM(), retriever=failing_retriever, image_root=_image_root(tmp_path))

    class WritingFailure(FakeLLM):
        def generate(self, prompt: str, system_prompt: str = "") -> str:
            if "当前章节：项目概述" in prompt: raise RuntimeError("writer unavailable")
            return super().generate(prompt, system_prompt)
    with pytest.raises(ProposalGenerationError, match="writing:overview"):
        generate_markdown_proposal("需求", tmp_path / "write.md", llm=WritingFailure(), retriever=_retriever([]), image_root=_image_root(tmp_path))

    monkeypatch.setattr(markdown_proposal, "_write", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(ProposalGenerationError, match="markdown_write"):
        generate_markdown_proposal("需求", tmp_path / "disk" / "proposal.md", llm=FakeLLM(), retriever=_retriever([]), image_root=_image_root(tmp_path))


def test_failed_plan_format_repair_keeps_initial_error_context(tmp_path: Path) -> None:
    class InvalidPlan:
        def generate(self, *_args, **_kwargs): return "not json"
    with pytest.raises(ProposalGenerationError, match="initial format error") as error:
        generate_markdown_proposal("需求", tmp_path / "proposal.md", llm=InvalidPlan(), retriever=_retriever([]), image_root=_image_root(tmp_path))
    assert error.value.stage == "planning"
    assert isinstance(error.value.__cause__, ValueError)


def test_pre_retrieval_overview_is_bounded_portable_and_planning_receives_it(tmp_path: Path) -> None:
    hits = [SearchHit(source_file=f"C:/private/cache/source-{i}.pdf", page_number=i, title="标题", content="正文" * 200,
                      distance=0.1, images=[]) for i in range(30)]
    overview = markdown_proposal._evidence_overview(hits, max_pages=12, max_chars=1200)
    assert overview.count("- 文件：") <= 12 and len(overview) <= 1200
    assert "C:/private" not in overview and "cache/" not in overview

    class Planner(FakeLLM):
        def generate(self, prompt, system_prompt=""):
            if "规划助手" in system_prompt:
                assert "证据概览" in prompt and "source-0.pdf" in prompt
            if "审查完整" in system_prompt:
                return '{"issues":[]}'
            return super().generate(prompt, system_prompt)
    output = tmp_path / "proposal.md"
    generate_markdown_proposal("建设工商业储能", output, llm=Planner(), retriever=lambda _q: QueryResult("", [], hits, "", []), image_root=_image_root(tmp_path))
    assert output.exists()


def test_pre_retrieval_empty_or_error_still_plans_and_records_warning(tmp_path: Path) -> None:
    class EmptyPlanner(FakeLLM):
        def generate(self, prompt, system_prompt=""):
            if "规划助手" in system_prompt:
                assert "证据概览" not in prompt
            if "审查完整" in system_prompt:
                return '{"issues":[]}'
            if "只输出当前章节正文" in system_prompt:
                return "资料不足【待确认】。"
            return super().generate(prompt, system_prompt)
    empty = lambda _q: QueryResult("", [], [], "", [])
    result = generate_markdown_proposal("需求", tmp_path / "empty.md", llm=EmptyPlanner(), retriever=empty, image_root=_image_root(tmp_path), return_result=True)
    assert result.path.exists() and not any("pre_retrieval" in warning for warning in result.warnings)

    calls = 0
    def flaky(queries):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("temporary")
        return QueryResult("", queries, [], "", [])
    result = generate_markdown_proposal("需求", tmp_path / "fallback.md", llm=EmptyPlanner(), retriever=flaky, image_root=_image_root(tmp_path), return_result=True)
    assert result.path.exists() and any("pre_retrieval" in warning for warning in result.warnings)


def test_five_section_call_metrics_and_cross_section_revision(tmp_path: Path) -> None:
    plan = {"title": "五章", "sections": [{"section_id": f"s{i}", "heading": f"第{i}章", "level": 2,
            "writing_goal": f"职责{i}", "retrieval_queries": [f"q{i}"]} for i in range(1, 6)]}
    class Five(FakeLLM):
        def __init__(self, issues): super().__init__(); self.issues = issues
        def generate(self, prompt, system_prompt=""):
            self.calls.append((system_prompt, prompt))
            if "规划助手" in system_prompt: return json.dumps(plan, ensure_ascii=False)
            if "审查完整" in system_prompt: return json.dumps({"issues": self.issues}, ensure_ascii=False)
            if "只输出修订后的" in system_prompt: return "修订内容【待确认】。"
            assert "受控全局文档上下文" in prompt
            return "章节内容【待确认】。"
    empty = lambda queries: QueryResult("", queries, [], "", [])
    base = generate_markdown_proposal("需求", tmp_path / "five.md", llm=Five([]), retriever=empty, image_root=_image_root(tmp_path), return_result=True)
    assert base.metrics["total_generation_llm_calls"] == 7
    issues = [{"affected_section_ids": ["s2", "s4"], "issue_type": "cross_section_repetition", "description": "重复", "revision_instruction": "去重", "severity": "warning"}]
    revised = generate_markdown_proposal("需求", tmp_path / "revised.md", llm=Five(issues), retriever=empty, image_root=_image_root(tmp_path), return_result=True)
    assert revised.metrics["section_revision_calls"] == 2 and revised.metrics["total_generation_llm_calls"] == 9
    assert revised.metrics["embedding_api_calls"] == "unknown"


def test_nonfatal_quality_warning_keeps_markdown_and_pass_has_no_notice(tmp_path: Path) -> None:
    class NoEvidence(FakeLLM):
        def generate(self, prompt, system_prompt=""):
            if "审查完整" in system_prompt:
                return '{"issues":[]}'
            if "只输出当前章节正文" in system_prompt:
                return "资料不足【待确认】。"
            return super().generate(prompt, system_prompt)
    request = "方案应包含：\n1. 不存在标题"
    result = generate_markdown_proposal(request, tmp_path / "draft.md", llm=NoEvidence(), retriever=lambda q: QueryResult("", q, [], "", []), image_root=_image_root(tmp_path), return_result=True)
    assert result.quality_status == "DRAFT_WITH_WARNINGS" and result.path.exists()
    assert result.path.read_text(encoding="utf-8").startswith("> ⚠️") and result.warnings


def test_confirmation_checklist_groups_specific_items_and_keeps_electricity_price_independent() -> None:
    grouped = markdown_proposal._confirmation_checklist(
        "园区面积、负荷曲线、变压器容量、配电电压、光伏可安装面积、设备容量、投资预算、电价、并网条件及建设周期待确认。", []
    )
    items = [item for _heading, category in grouped for item in category]
    assert "峰、平、谷电价及基本电费计费方式【待确认】" in items
    assert all(len(item) < 30 for item in items)
    assert len(items) == len(set(items))


def test_global_image_budget_deduplicates_and_warns_when_preferred_is_unselected(tmp_path: Path) -> None:
    root = _image_root(tmp_path)
    sections = {}
    for index in range(6):
        image = root / "images" / f"i{index}.png"
        image.write_bytes(b"png")
        evidence = markdown_proposal.EvidenceItem(
            "S1", f"source-{index}.pdf", index + 1, "正文", [{"path": f"images/i{index}.png", "caption": f"图{index}"}], "页标题"
        )
        plan = markdown_proposal.SectionPlan(f"s{index}", f"章节{index}", 2, "目标", ["q"], "preferred")
        sections[plan.section_id] = markdown_proposal._SectionDraft(plan, "内容 [IMG1]", [evidence], markdown_proposal._image_catalog([evidence]))
    warnings, telemetry = [], {"candidate_images": [], "selected_images": [], "copied_images": [], "removed_images": [], "removal_reasons": []}
    markdown_proposal._apply_image_budget(sections, root, warnings, telemetry)
    assert len(telemetry["candidate_images"]) == 6
    assert len(telemetry["selected_images"]) == 5
    assert any("预算上限" in item["reason"] for item in telemetry["removed_images"])
    assert any("preferred" not in warning and "未插入图片" in warning for warning in warnings)


def test_read_only_diagnosis_keeps_original_and_reports_missing_images(tmp_path: Path) -> None:
    proposal = tmp_path / "proposal.md"
    original = "# 标题\n\n## 正文\n\n内容【待确认】。\n\n## 来源与依据\n\n（本方案没有使用可引用资料。）\n"
    proposal.write_text(original, encoding="utf-8")
    diagnosis = markdown_proposal.diagnose_markdown_proposal(proposal)
    assert proposal.read_text(encoding="utf-8") == original
    text = diagnosis.read_text(encoding="utf-8")
    assert "最终 Markdown 图片链接：0 张" in text and "无法回溯" in text


def test_uncaptioned_image_uses_page_heading_once_and_is_not_discarded() -> None:
    item = markdown_proposal.EvidenceItem("S1", "source.pdf", 1, "### 源网荷储总体架构\n正文", [{"path": "assets/a.png", "caption": ""}, {"path": "assets/b.png", "caption": ""}], "")
    catalog = markdown_proposal._image_catalog([item])
    assert list(catalog) == ["IMG1"]
    assert markdown_proposal._caption(item, item.images[0]) == "源网荷储总体架构"


def test_confirmation_checklist_always_contains_all_business_categories() -> None:
    grouped = markdown_proposal._confirmation_checklist("", ["变压器容量【待确认】"])
    assert [heading for heading, _items in grouped] == [item[0] for item in markdown_proposal._CONFIRMATION_CATEGORIES]
    assert all(items for _heading, items in grouped)


def test_code_fallback_selects_three_preferred_images_when_llm_selects_none(tmp_path: Path) -> None:
    root = _image_root(tmp_path)
    for index in range(3):
        (root / "images" / f"preferred-{index}.png").write_bytes(b"png")
    plan = {"title": "图片兜底", "sections": [
        {"section_id": f"s{index}", "heading": heading, "level": 2, "writing_goal": heading,
         "retrieval_queries": [f"q{index}"], "image_intent": "preferred"}
        for index, heading in enumerate(["总体架构", "源网荷储与配电数字化", "虚拟电厂调度"])
    ]}
    class FallbackImages(FakeLLM):
        def generate(self, prompt, system_prompt=""):
            if "规划助手" in system_prompt:
                return json.dumps(plan, ensure_ascii=False)
            if "审查完整" in system_prompt:
                return '{"issues":[]}'
            return "章节内容。[S1]"  # Deliberately no IMG marker.
    def retrieve(queries):
        index = int(queries[0][-1]) if queries[0].startswith("q") else 0
        hit = SearchHit("source.pdf", index + 1, f"第{index + 1}页架构", "正文", 0.1,
                        [{"path": f"images/preferred-{index}.png", "caption": ""}])
        return QueryResult("", queries, [hit], "", [])
    result = generate_markdown_proposal("需求", tmp_path / "proposal.md", llm=FallbackImages(), retriever=retrieve, image_root=root, return_result=True)
    text = result.path.read_text(encoding="utf-8")
    assert text.count("![](") == 0  # Captions are rendered inside the image alt text.
    assert text.count("](assets/image-") == 3
    assert len(result.metrics["images"]["candidate_images"]) == 3
    assert len(result.metrics["images"]["selected_images"]) == len(result.metrics["images"]["copied_images"]) == 3


@pytest.mark.parametrize("model_selected", [1, 2])
def test_code_fallback_tops_up_one_or_two_model_selected_images_to_three(tmp_path: Path, model_selected: int) -> None:
    root = _image_root(tmp_path)
    for index in range(3):
        (root / "images" / f"topup-{index}.png").write_bytes(b"png")
    plan = {"title": "图片补选", "sections": [
        {"section_id": f"s{index}", "heading": heading, "level": 2, "writing_goal": heading,
         "retrieval_queries": [f"t{index}"], "image_intent": "preferred"}
        for index, heading in enumerate(["总体架构", "源网荷储与配电数字化", "虚拟电厂调度"])
    ]}
    class TopUp(FakeLLM):
        def generate(self, prompt, system_prompt=""):
            if "规划助手" in system_prompt:
                return json.dumps(plan, ensure_ascii=False)
            if "审查完整" in system_prompt:
                return '{"issues":[]}'
            match = re.search(r"当前章节：(.+)", prompt)
            index = ["总体架构", "源网荷储与配电数字化", "虚拟电厂调度"].index(match.group(1)) if match else 0
            return "章节内容。[S1]" + (" [IMG1]" if index < model_selected else "")
    def retrieve(queries):
        index = int(queries[0][-1]) if queries[0].startswith("t") else 0
        return QueryResult("", queries, [SearchHit("source.pdf", index + 1, f"第{index + 1}页架构", "正文", 0.1,
            [{"path": f"images/topup-{index}.png", "caption": ""}])], "", [])
    result = generate_markdown_proposal("需求", tmp_path / "proposal.md", llm=TopUp(), retriever=retrieve, image_root=root, return_result=True)
    assert len(result.metrics["images"]["selected_images"]) == 3
    assert len(result.metrics["images"]["copied_images"]) == 3
    text = result.path.read_text(encoding="utf-8")
    assert text.count("](assets/image-") == 3 and result.quality_status == "PASS"


def test_preferred_section_coverage_beats_three_higher_scored_images_in_one_section(tmp_path: Path) -> None:
    root = _image_root(tmp_path)
    sections = {}
    for section_id, count in (("s2", 3), ("s3", 1), ("s4", 1)):
        images = []
        for index in range(count):
            path = root / "images" / f"{section_id}-{index}.png"
            path.write_bytes(b"png")
            images.append({"path": f"images/{section_id}-{index}.png", "caption": f"{section_id} 图{index}"})
        evidence = markdown_proposal.EvidenceItem("S1", f"{section_id}.pdf", 1, "正文", images, section_id)
        plan = markdown_proposal.SectionPlan(section_id, section_id, 2, section_id, ["q"], "preferred")
        catalog = markdown_proposal._image_catalog([evidence])
        body = "正文 " + (" ".join(f"[{image_id}]" for image_id in catalog) if section_id == "s2" else "")
        sections[section_id] = markdown_proposal._SectionDraft(plan, body, [evidence], catalog)
    telemetry, warnings = {"removed_images": []}, []
    markdown_proposal._apply_image_budget(sections, root, warnings, telemetry)
    chosen = telemetry["selected_images"]
    assert {item["intended_section_id"] for item in chosen} == {"s2", "s3", "s4"}
    assert next(item for item in chosen if item["section_id"] == "s2")["selected_by"] == "LLM"
    assert {item["selected_by"] for item in chosen if item["section_id"] != "s2"} == {"code_fallback"}


def test_rendered_image_under_wrong_h2_is_a_canonical_placement_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "image.png"
    source.write_bytes(b"png")
    plan = markdown_proposal.ProposalPlan("标题", [
        markdown_proposal.SectionPlan("s2", "标题（2）", 2, "目标", ["q"], "preferred"),
        markdown_proposal.SectionPlan("s3", "标题（3）", 2, "目标", ["q"], "preferred"),
        markdown_proposal.SectionPlan("s4", "标题（4）", 2, "目标", ["q"], "preferred"),
    ])
    asset = markdown_proposal._ImageAsset("s3", "IMG1", "code_fallback", source, "assets/image-001.png", "图", markdown_proposal.EvidenceItem("S1", "x.pdf", 1, "", []))
    markdown = "# 标题\n\n## 1. 标题（2）\n\n![图](assets/image-001.png)\n\n## 2. 标题（3）\n"
    report = markdown_proposal._image_placement_report(markdown, plan, {("s3", "IMG1"): asset})
    assert report["image_placement_mismatches"] == [{
        "relative_path": "assets/image-001.png", "image_id": "IMG1",
        "intended_section_id": "s3", "actual_section_id": "s2", "selected_by": "code_fallback",
    }]


def test_strict_image_link_counter_rejects_escaped_parentheses() -> None:
    markdown = "![有效](assets/image-001.png)\n![无效]\\(assets/image-002.png)\n"
    assert markdown_proposal._strict_image_links(markdown) == ["assets/image-001.png"]


def test_code_fallback_is_placed_after_its_same_page_evidence_paragraph() -> None:
    item = markdown_proposal.EvidenceItem(
        "S1", "source.pdf", 8, "", [{"path": "images/a.png", "caption": "调度架构图"}], "调度",
    )
    draft = markdown_proposal._SectionDraft(
        markdown_proposal.SectionPlan("sec_(vpp)", "虚拟电厂（VPP）", 2, "调度", ["q"], "preferred"),
        "前段 [S1]。\n\n后段不引用。", [item], markdown_proposal._image_catalog([item]),
    )
    placed = markdown_proposal._place_fallback_image(draft, "IMG1", item, item.images[0])
    assert placed.raw_body == "前段 [S1]。\n\n[IMG1]\n\n后段不引用。"


def test_code_fallback_without_same_page_evidence_stays_at_own_section_end() -> None:
    item = markdown_proposal.EvidenceItem(
        "S1", "source.pdf", 8, "", [{"path": "images/a.png", "caption": "调度架构图"}], "调度",
    )
    other = markdown_proposal.EvidenceItem("S2", "other.pdf", 9, "", [], "其他")
    draft = markdown_proposal._SectionDraft(
        markdown_proposal.SectionPlan("sec_4[carbon]", "能碳（底座）", 2, "底座", ["q"], "preferred"),
        "本章正文 [S2]。", [item, other], markdown_proposal._image_catalog([item]),
    )
    placed = markdown_proposal._place_fallback_image(draft, "IMG1", item, item.images[0])
    assert placed.raw_body == "本章正文 [S2]。\n\n[IMG1]"


def test_final_markdown_structure_uses_raw_standard_tokens_only(tmp_path: Path) -> None:
    markdown = "# 标题\n\n## 章节\n\n### 小节\n\n- 项\n\n![图](assets/image-001.png)\n"
    assert _quality_gate(markdown, tmp_path / "proposal.md", {}, "") == []
    assert markdown_proposal._strict_image_links(markdown) == ["assets/image-001.png"]


def test_three_images_under_one_h2_cannot_pass_image_quality(tmp_path: Path) -> None:
    root = _image_root(tmp_path)
    images = []
    for index in range(3):
        path = root / "images" / f"one-h2-{index}.png"
        path.write_bytes(b"png")
        images.append({"path": f"images/one-h2-{index}.png", "caption": f"图{index}"})
    evidence = markdown_proposal.EvidenceItem("S1", "source.pdf", 1, "正文", images, "架构")
    catalog = markdown_proposal._image_catalog([evidence])
    section = markdown_proposal.SectionPlan("stable(sec-2)", "标题（2）", 2, "目标", ["q"], "preferred")
    draft = markdown_proposal._SectionDraft(
        section, "\n\n".join(f"[{image_id}]" for image_id in catalog), [evidence], catalog,
        selected_images={image_id: "LLM" for image_id in catalog},
    )
    plan = markdown_proposal.ProposalPlan("标题", [section])
    _markdown, _assets, warnings, placement = markdown_proposal._final_markdown(
        plan, {section.section_id: draft}, image_root=root, output_path=tmp_path / "proposal.md", request="",
    )
    assert len(placement["final_images_by_section"][section.section_id]) == 3
    assert "图片章节覆盖不足：三张或以上图片全部位于同一章节" in warnings


def test_fallback_prefers_caption_matching_its_own_section_theme() -> None:
    section = markdown_proposal.SectionPlan("stable-carbon", "能碳管理数字化底座", 2, "能碳管理", ["q"], "preferred")
    item = markdown_proposal.EvidenceItem("S1", "source.pdf", 1, "", [], "")
    carbon = {"caption": "能碳平台", "path": "images/carbon.png"}
    unrelated = {"caption": "虚拟电厂调度", "path": "images/vpp.png"}
    assert markdown_proposal._fallback_image_relevance(section, item, carbon) > markdown_proposal._fallback_image_relevance(section, item, unrelated)
