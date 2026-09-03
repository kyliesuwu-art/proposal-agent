"""Offline tests for the single-file Markdown proposal workflow."""

from pathlib import Path

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

    assert calls == [["项目目标"], ["储能容量"], ["实施要求"]]
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
    assert "*图：储能系统架构图。来源：历史方案.pdf，第12页。*" in text
    assert (output.parent / "assets" / "image-001.png").read_bytes() == b"fake-png"
    assert [path.name for path in (output.parent / "assets").iterdir()] == ["image-001.png"]


def test_image_without_caption_description_or_page_title_is_not_offered() -> None:
    hit = SearchHit(source_file="cache/a.pdf", page_number=1, title="", content="", distance=0.1, images=[{"path": "images/a.png"}])
    assert _image_catalog(markdown_proposal._evidence([hit])) == {}


def test_quality_gate_rejects_visible_citation_without_source_list(tmp_path: Path) -> None:
    markdown = "# 标题\n\n## 1. 正文\n\n事实。[来源: 文件.pdf, 第 1 页]\n\n## 来源与依据\n\n（本方案没有使用可引用资料。）\n"
    with pytest.raises(ProposalQualityError, match="来源列表为空"):
        _quality_gate(markdown, tmp_path / "proposal.md", {}, "")


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


def test_visible_citation_or_image_path_in_raw_revision_is_rejected(tmp_path: Path) -> None:
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


def test_quality_gate_rejects_all_known_final_markdown_violations(tmp_path: Path) -> None:
    markdown = (
        "**# 标题**\n\n1\\. **\\*\\*标签\\*\\***\n\\- 项\n"
        "[S1] [IMG1] cache/path C:\\private\\a /home/private/a\n"
        "### 4.3 待确认项\n- 【待确认】。\n## 待确认项\n"
        "## 来源与依据\n\n（本方案没有使用可引用资料。）\n"
    )
    with pytest.raises(ProposalQualityError) as error:
        _quality_gate(markdown, tmp_path / "proposal.md", {}, "")
    message = str(error.value)
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
    with pytest.raises(ProposalQualityError) as error:
        _quality_gate(markdown, tmp_path / "proposal.md", {}, request)
    assert "正文残留待确认视觉伪标题" in str(error.value)
    assert "待确认项未完整规范覆盖用户明确参数" in str(error.value)


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
    assert "- 园区面积需确认【待确认】" in text
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
    assert "- 园区面积需确认【待确认】" in text


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
