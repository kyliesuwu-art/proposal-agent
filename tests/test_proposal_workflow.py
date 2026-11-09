from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import main
from src.application import GenerateRequest, GenerateWorkflowError, ProposalWorkflow
from src.markdown_proposal import ProposalGenerationError


class FakeLLM:
    connection_settings = {"model": "fake"}


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.calls: list[str] = []

    def log(self, path: Path, *, status: str, stage: str, **details: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"status": status, "stage": stage, **details}
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.calls.append(stage)

    def request_metadata(self, output: Path, request: str, *, run_log: Path, debug: bool) -> Path:
        path = output.with_name("proposal.request.json")
        path.write_text(json.dumps({"request": request, "run_log": run_log.name, "debug": debug}), encoding="utf-8")
        return path

    def markdown(self, request: str, output: Path, **kwargs: object):
        self.calls.append("markdown")
        assert request == "医院光伏储能方案"
        assert callable(kwargs["retriever"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("# 方案\n\n## 范围\n\n正文", encoding="utf-8")
        output.with_name("proposal.sources.json").write_text('{"sources": []}', encoding="utf-8")
        return SimpleNamespace(path=output, quality_status="PASS", warnings=[], metrics={"quality_passed_checks": 10})

    def retriever(self, _queries: list[str]):
        self.calls.append("retrieve")
        return object()

    def word(self, source: Path, target: Path) -> Path:
        self.calls.append("word")
        assert source.name == "proposal.md"
        target.write_bytes(b"docx")
        report = target.with_suffix(".render_report.json")
        report.write_text("{}", encoding="utf-8")
        return report

    def slides(self, source: Path, target: Path, **kwargs: object) -> tuple[Path, Path, list[str]]:
        self.calls.append("slides")
        assert source.name == "proposal.md"
        assert kwargs["sources_path"].name == "proposal.sources.json"
        target.write_text("# Slides", encoding="utf-8")
        plan = target.with_name("proposal.slide-plan.json")
        plan.write_text("{}", encoding="utf-8")
        return target, plan, []

    def pptx(self, source: Path, target: Path, **kwargs: object) -> Path:
        self.calls.append("pptx")
        assert source.name == "slides.md"
        assert kwargs["plan_path"].name == "proposal.slide-plan.json"
        target.write_bytes(b"pptx")
        report = target.with_suffix(".render_report.json")
        report.write_text("{}", encoding="utf-8")
        return report

    def workflow(self) -> ProposalWorkflow:
        return ProposalWorkflow(
            llm_factory=FakeLLM,
            retriever=self.retriever,
            markdown_generator=self.markdown,
            word_renderer=self.word,
            slides_builder=self.slides,
            pptx_renderer=self.pptx,
            run_log_writer=self.log,
            request_writer=self.request_metadata,
        )


@pytest.mark.parametrize(
    ("formats", "expected"),
    [
        (("md",), {"markdown"}),
        (("docx",), {"markdown", "word"}),
        (("pptx",), {"markdown", "slides", "pptx"}),
        (("md", "docx"), {"markdown", "word"}),
        (("md", "pptx"), {"markdown", "slides", "pptx"}),
        (("md", "docx", "pptx"), {"markdown", "word", "slides", "pptx"}),
    ],
)
def test_workflow_generates_requested_artifacts_without_replacing_existing_implementations(tmp_path: Path, formats: tuple[str, ...], expected: set[str]) -> None:
    harness = Harness(tmp_path)
    result = harness.workflow().run(GenerateRequest("医院光伏储能方案", tmp_path / "delivery", formats))

    assert result.markdown_path.is_file()
    assert result.request_metadata_path.is_file()
    assert result.run_log_path.is_file()
    assert expected.issubset(set(harness.calls))
    assert (result.word_path is not None) is ("docx" in formats)
    assert (result.pptx_path is not None) is ("pptx" in formats)


@pytest.mark.parametrize("formats", [(), ("pdf",)])
def test_workflow_rejects_invalid_formats_before_calling_services(tmp_path: Path, formats: tuple[str, ...]) -> None:
    harness = Harness(tmp_path)
    with pytest.raises(ValueError):
        harness.workflow().run(GenerateRequest("医院光伏储能方案", tmp_path / "delivery", formats))
    assert not harness.calls


@pytest.mark.parametrize(
    ("proposal_request", "max_slides"),
    [("   ", 15), ("医院光伏储能方案", 0)],
)
def test_workflow_rejects_invalid_input_before_calling_services(tmp_path: Path, proposal_request: str, max_slides: int) -> None:
    harness = Harness(tmp_path)
    with pytest.raises(ValueError):
        harness.workflow().run(GenerateRequest(proposal_request, tmp_path / "delivery", ("md",), max_slides=max_slides))
    assert not harness.calls


def test_workflow_reports_llm_and_retrieval_failures_by_stage(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    workflow = harness.workflow()
    workflow._llm_factory = lambda: (_ for _ in ()).throw(RuntimeError("provider unavailable"))
    with pytest.raises(GenerateWorkflowError, match="planning"):
        workflow.run(GenerateRequest("医院光伏储能方案", tmp_path / "llm", ("md",)))

    def retrieval_failure(*_args: object, **_kwargs: object):
        raise ProposalGenerationError("retrieval:section_01", RuntimeError("store unavailable"))

    workflow = harness.workflow()
    workflow._markdown_generator = retrieval_failure
    with pytest.raises(GenerateWorkflowError, match="retrieval:section_01"):
        workflow.run(GenerateRequest("医院光伏储能方案", tmp_path / "retrieval", ("md",)))


@pytest.mark.parametrize("failure_stage", ["word", "pptx"])
def test_workflow_preserves_markdown_when_later_artifact_stage_fails(tmp_path: Path, failure_stage: str) -> None:
    harness = Harness(tmp_path)
    workflow = harness.workflow()
    if failure_stage == "word":
        workflow._word_renderer = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("word failed"))
        formats = ("md", "docx")
        expected_stage = "word_render"
    else:
        workflow._pptx_renderer = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("pptx failed"))
        formats = ("md", "pptx")
        expected_stage = "pptx_render"

    output = tmp_path / failure_stage
    with pytest.raises(GenerateWorkflowError) as raised:
        workflow.run(GenerateRequest("医院光伏储能方案", output, formats))
    assert raised.value.stage == expected_stage
    assert (output / "proposal.md").is_file()
    assert (output / "proposal.run.log").is_file()


def test_workflow_rejects_existing_managed_artifacts_before_initializing_services(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    output = tmp_path / "delivery"
    output.mkdir()
    (output / "proposal.md").write_text("old proposal", encoding="utf-8")

    with pytest.raises(ValueError, match="已有托管产物"):
        harness.workflow().run(GenerateRequest("医院光伏储能方案", output, ("md",)))
    assert not harness.calls


def test_workflow_rejects_retry_in_partial_delivery_directory_and_allows_new_directory(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    workflow = harness.workflow()
    workflow._word_renderer = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("word failed"))
    partial = tmp_path / "partial"
    with pytest.raises(GenerateWorkflowError, match="word_render"):
        workflow.run(GenerateRequest("医院光伏储能方案", partial, ("docx",)))
    assert (partial / "proposal.md").is_file()

    with pytest.raises(ValueError, match="已有托管产物"):
        harness.workflow().run(GenerateRequest("医院光伏储能方案", partial, ("docx",)))
    assert harness.workflow().run(GenerateRequest("医院光伏储能方案", tmp_path / "retry", ("docx",))).word_path


def test_workflow_reports_unwritable_output_directory_without_initializing_services(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("file", encoding="utf-8")

    with pytest.raises(GenerateWorkflowError) as raised:
        harness.workflow().run(GenerateRequest("医院光伏储能方案", blocked, ("md",)))
    assert raised.value.stage == "output_initialization"
    assert not harness.calls


def test_generate_cli_builds_a_request_without_opening_real_services(monkeypatch, tmp_path: Path, capsys) -> None:
    captured: dict[str, GenerateRequest] = {}

    class FakeWorkflow:
        def run(self, request: GenerateRequest):
            captured["request"] = request
            request.output_dir.mkdir(parents=True, exist_ok=True)
            markdown = request.output_dir / "proposal.md"
            run_log = request.output_dir / "proposal.run.log"
            metadata = request.output_dir / "proposal.request.json"
            for path in (markdown, run_log, metadata):
                path.write_text("{}", encoding="utf-8")
            return SimpleNamespace(
                markdown_path=markdown, run_log_path=run_log, request_metadata_path=metadata,
                quality_status="PASS", word_path=None, word_report_path=None,
                pptx_path=None, pptx_report_path=None, slides_markdown_path=None, slide_plan_path=None,
            )

    monkeypatch.setattr(main, "_proposal_workflow", lambda: FakeWorkflow())
    monkeypatch.setattr(sys, "argv", [
        "main.py", "generate", "医院光伏储能方案", "--formats", "md", "docx", "--output-dir", str(tmp_path),
    ])
    main.main()

    assert captured["request"].formats == ("md", "docx")
    assert captured["request"].output_dir == tmp_path
    assert "Markdown:" in capsys.readouterr().out
