"""Controlled integration for the unified generate workflow.

The workflow runs against the real production functions composed in
``src.main``: real hybrid RAG retrieval on a disposable on-disk index, the real
Markdown proposal generator driven by a scripted offline LLM, and the real local
Word/PPTX renderers. No DashScope or MinerU service is contacted: embeddings use
the deterministic local hash function already shipped for offline acceptance.
"""

from __future__ import annotations

import functools
import json
import shutil
import zipfile
from pathlib import Path

import pytest
from PIL import Image
from pptx import Presentation
from docx import Document

from src import main, markdown_proposal, pipeline
from src.application import GenerateRequest, GenerateWorkflowError, ProposalWorkflow
from src.domain import DocumentIdentity
from src.hybrid_v2 import HybridTestStore, LocalHashEmbeddingFunction
from src.markdown_proposal import generate_markdown_proposal
from src.render_pptx import build_slides_markdown, render_pptx
from src.render_word import render_word


PLAN = (
    '{"title":"工商业储能方案","sections":['
    '{"section_id":"overview","heading":"项目概述","level":2,"writing_goal":"概述项目","retrieval_queries":["储能系统架构容量"]},'
    '{"section_id":"configuration","heading":"系统配置","level":2,"writing_goal":"说明配置","retrieval_queries":["储能 PCS 430kWh 配置"]},'
    '{"section_id":"implementation","heading":"实施安排","level":2,"writing_goal":"说明实施","retrieval_queries":["实施验收周期"]}]}'
)


class ScriptedLLM:
    """Offline LLM that follows the same system-prompt contracts as production."""

    connection_settings = {"model": "scripted-offline"}

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        if "规划助手" in system_prompt:
            return PLAN
        if "审查完整" in system_prompt:
            return '{"issues":[]}'
        if "当前章节：项目概述" in prompt:
            return "园区建设工商业储能，系统额定容量 430kWh。[S1] [IMG1]"
        if "当前章节：系统配置" in prompt:
            return "PCS 选型与 10kV 并网配置按既有资料执行。[S1]"
        if "当前章节：实施安排" in prompt:
            return "施工周期与验收流程【待确认】。[S1]"
        return "资料不足【待确认】。"


@pytest.fixture
def isolated_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("src.hybrid_v2.DashScopeEmbeddingFunction", LocalHashEmbeddingFunction)
    index_dir = tmp_path / "rag-index"
    store = HybridTestStore(index_dir, LocalHashEmbeddingFunction())
    identity = DocumentIdentity("cache/项目资料/历史方案.pdf", "doc_hist", "hash1", "v1")
    pages = [
        {
            "slide_number": 12,
            "title": "储能系统架构",
            "content": "系统额定容量为430kWh，PCS 型号 ABC-500，配置 10MW 光伏与 20MWh 储能接入 10kV 母线。",
            "images": [{"path": "images/storage.png", "caption": "储能系统架构图"}],
        },
        {"slide_number": 13, "title": "实施安排", "content": "施工周期两周，完成电缆敷设与验收，随后并网调试。"},
    ]
    try:
        assert store.add_version(identity, pages) == 2
    finally:
        store.close()

    knowledge_root = tmp_path / "knowledge"
    image_path = knowledge_root / "images" / "storage.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (240, 160), "navy").save(image_path)
    monkeypatch.setattr(markdown_proposal, "RAG_DB_PATH", knowledge_root)
    return index_dir


def _workflow(index_dir: Path, retrieval_counter: list[int] | None = None) -> ProposalWorkflow:
    retriever = functools.partial(pipeline.retrieve_evidence, db=index_dir)
    if retrieval_counter is not None:
        wrapped = retriever

        def counting_retriever(queries):  # type: ignore[no-untyped-def]
            retrieval_counter.append(len(queries))
            return wrapped(queries)

        retriever = counting_retriever
    return ProposalWorkflow(
        llm_factory=ScriptedLLM,
        retriever=retriever,
        markdown_generator=generate_markdown_proposal,
        word_renderer=render_word,
        slides_builder=build_slides_markdown,
        pptx_renderer=render_pptx,
        run_log_writer=main._write_proposal_run_log,
        request_writer=main._write_proposal_request,
    )


def test_workflow_full_delivery_uses_real_rag_generator_and_renderers(tmp_path: Path, isolated_index: Path) -> None:
    retrieval_counter: list[int] = []
    output = tmp_path / "delivery"
    result = _workflow(isolated_index, retrieval_counter).run(
        GenerateRequest("建设工商业储能", output, ("md", "docx", "pptx"), max_slides=8)
    )

    # Markdown: title, sections, visible citation, confirmation item and sidecar.
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert markdown.startswith("# ")
    heading_lines = [line for line in markdown.splitlines() if line.lstrip().startswith("#")]
    for heading in ("项目概述", "系统配置", "实施安排"):
        assert any(heading in line for line in heading_lines)
    assert "历史方案.pdf" in markdown and "第 12 页" in markdown
    assert "【待确认】" in markdown and "[S1]" not in markdown and "[IMG1]" not in markdown
    assert result.quality_status in {"PASS", "DRAFT_WITH_WARNINGS"}
    sources = json.loads((output / "proposal.sources.json").read_text(encoding="utf-8"))
    assert sources["sources"] and sources["sources"][0]["filename"] == "历史方案.pdf"
    assert 12 in sources["sources"][0]["pages"]
    assert (output / "assets").is_dir() and any((output / "assets").iterdir())

    # The workflow performs no retrieval of its own: the generator design is one
    # pre-retrieval overview plus one evidence call per section (1 + 3 = 4).
    assert retrieval_counter == [1, 1, 1, 1]

    # DOCX: valid OOXML with headings, the embedded image and a PASS report.
    assert result.word_path is not None and result.word_path.is_file()
    docx = Document(result.word_path)
    assert any("项目概述" in paragraph.text for paragraph in docx.paragraphs)
    assert len(docx.inline_shapes) >= 1
    word_report = json.loads(result.word_report_path.read_text(encoding="utf-8"))
    assert word_report["status"] == "PASS"
    with zipfile.ZipFile(result.word_path) as archive:
        assert "[Content_Types].xml" in archive.namelist()
        assert any(name.startswith("word/media/") for name in archive.namelist())

    # Slides plan and PPTX: slide count matches the plan and report, titles survive.
    assert result.slides_markdown_path.is_file() and result.slide_plan_path.is_file()
    assert result.slide_plan_path.name == "slides.slide-plan.json"
    plan = json.loads(result.slide_plan_path.read_text(encoding="utf-8"))
    presentation = Presentation(result.pptx_path)
    pptx_report = json.loads(result.pptx_report_path.read_text(encoding="utf-8"))
    assert pptx_report["status"] == "PASS"
    assert len(presentation.slides) == pptx_report["slide_count"] == len(plan["slides"])
    titles = [shape.text for slide in presentation.slides for shape in slide.shapes if shape.has_text_frame and shape.text.strip()]
    assert titles and all(title.strip() for title in titles)
    with zipfile.ZipFile(result.pptx_path) as archive:
        assert "[Content_Types].xml" in archive.namelist()
        assert any(name.startswith("ppt/media/") for name in archive.namelist())

    log = json.loads(result.run_log_path.read_text(encoding="utf-8"))
    stages = [event["stage"] for event in log["events"]]
    assert {"initializing", "markdown_complete", "word_complete", "slides_complete", "pptx_complete", "complete"} <= set(stages)
    assert result.request_metadata_path.is_file()


def test_md_only_delivery_never_initializes_word_or_pptx(tmp_path: Path, isolated_index: Path) -> None:
    output = tmp_path / "md-only"
    result = _workflow(isolated_index).run(GenerateRequest("建设工商业储能", output, ("md",)))

    assert result.word_path is None and result.pptx_path is None
    assert result.slides_markdown_path is None and result.slide_plan_path is None
    assert not (output / "proposal.docx").exists() and not (output / "proposal.pptx").exists()
    assert not (output / "slides.md").exists()
    assert result.markdown_path.is_file() and (output / "proposal.sources.json").is_file()


def test_llm_failure_is_stage_labelled_without_artifacts(tmp_path: Path, isolated_index: Path) -> None:
    workflow = _workflow(isolated_index)
    workflow._llm_factory = lambda: (_ for _ in ()).throw(RuntimeError("provider unavailable"))
    output = tmp_path / "llm-failure"

    with pytest.raises(GenerateWorkflowError) as raised:
        workflow.run(GenerateRequest("建设工商业储能", output, ("md",)))
    assert raised.value.stage == "planning"
    assert not (output / "proposal.md").exists()
    log = json.loads((output / "proposal.run.log").read_text(encoding="utf-8"))
    assert log["status"] == "FATAL" and log["stage"] == "planning"


def test_rag_failure_keeps_run_log_and_publishes_no_markdown(tmp_path: Path, isolated_index: Path) -> None:
    workflow = _workflow(tmp_path / "missing-index")
    output = tmp_path / "rag-failure"

    with pytest.raises(GenerateWorkflowError) as raised:
        workflow.run(GenerateRequest("建设工商业储能", output, ("md",)))
    assert raised.value.stage.startswith("retrieval:")
    assert not (output / "proposal.md").exists()
    log = json.loads((output / "proposal.run.log").read_text(encoding="utf-8"))
    assert log["status"] == "FATAL"


def test_word_render_failure_preserves_markdown_and_retry_requires_fresh_directory(tmp_path: Path, isolated_index: Path) -> None:
    def broken_markdown(request: str, output: Path, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("# 失败方案\n\n![缺失图片](assets/missing.png)\n", encoding="utf-8")
        return output

    workflow = _workflow(isolated_index)
    workflow._markdown_generator = broken_markdown
    partial_dir = tmp_path / "partial-docx"

    with pytest.raises(GenerateWorkflowError) as raised:
        workflow.run(GenerateRequest("建设工商业储能", partial_dir, ("md", "docx")))
    assert raised.value.stage == "word_render"
    assert (partial_dir / "proposal.md").is_file()
    assert not (partial_dir / "proposal.docx").exists()

    with pytest.raises(ValueError, match="已有托管产物"):
        workflow.run(GenerateRequest("建设工商业储能", partial_dir, ("md", "docx")))

    def valid_markdown(request: str, output: Path, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("# 重试方案\n\n## 章节\n\n重试正文。\n", encoding="utf-8")
        return output

    workflow._markdown_generator = valid_markdown
    retry = workflow.run(GenerateRequest("建设工商业储能", tmp_path / "retry-docx", ("md", "docx")))
    assert retry.word_path.is_file()
    assert Document(retry.word_path).paragraphs


def test_pptx_render_failure_preserves_slides_and_plan(tmp_path: Path, isolated_index: Path) -> None:
    workflow = _workflow(isolated_index)
    real_render = render_pptx

    def broken_pptx(source: Path, target: Path, **kwargs):
        return real_render(source.parent / "missing-slides.md", target, **kwargs)

    workflow._pptx_renderer = broken_pptx
    output = tmp_path / "pptx-failure"

    with pytest.raises(GenerateWorkflowError) as raised:
        workflow.run(GenerateRequest("建设工商业储能", output, ("md", "pptx"), max_slides=8))
    assert raised.value.stage == "pptx_render"
    assert (output / "slides.md").is_file() and (output / "slides.slide-plan.json").is_file()
    assert not (output / "proposal.pptx").exists()
    assert (output / "proposal.md").is_file()


def test_cli_returns_nonzero_on_validation_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    import sys

    class BrokenWorkflow:
        def run(self, request: GenerateRequest):
            raise ValueError("拒绝执行")

    monkeypatch.setattr(main, "_proposal_workflow", BrokenWorkflow)
    monkeypatch.setattr(sys, "argv", ["main.py", "generate", "建设工商业储能", "--formats", "md", "--output-dir", str(tmp_path / "cli")])
    with pytest.raises(SystemExit) as exited:
        main.main()
    assert exited.value.code == 1
    assert "input_validation" in capsys.readouterr().err


@pytest.mark.skipif(shutil.which("soffice") is None, reason="环境无 LibreOffice (soffice)，未做 Office 视觉渲染验收，仅完成 OOXML 结构校验")
def test_visual_layout_requires_office_renderer() -> None:
    pytest.fail("soffice 可用时应在此补充隔离环境的视觉布局验收")

def test_stale_slide_plan_alone_blocks_directory_reuse(tmp_path: Path) -> None:
    output = tmp_path / "stale"
    output.mkdir()
    (output / "slides.slide-plan.json").write_text("{}", encoding="utf-8")
    workflow = _workflow(tmp_path / "unused-index")
    with pytest.raises(ValueError, match="已有托管产物"):
        workflow.run(GenerateRequest("建设工商业储能", output, ("md",)))
