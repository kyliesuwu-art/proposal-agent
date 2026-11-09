"""A thin orchestration layer over the established proposal capabilities.

The workflow deliberately owns no RAG algorithm, prompt, or rendering rule.  It
coordinates the existing Markdown-first generation path and its optional local
DOCX/PPTX consumers so every completed stage remains available after a later
stage fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol


SUPPORTED_FORMATS = frozenset({"md", "docx", "pptx"})
_MANAGED_OUTPUT_NAMES = frozenset({
    "assets",
    "proposal.md",
    "proposal.sources.json",
    "proposal.request.json",
    "proposal.docx",
    "proposal.render_report.json",
    "slides.md",
    "slides.slide-plan.json",
    "proposal.slide-plan.json",
    "proposal.pptx",
    "proposal.pptx.render_report.json",
    "proposal.run.log",
})


class _ProposalResult(Protocol):
    path: Path
    quality_status: str
    warnings: list[str]
    metrics: dict[str, Any]


class GenerateWorkflowError(RuntimeError):
    """A stage-labelled failure raised by the unified generation workflow."""

    def __init__(self, stage: str, cause: BaseException) -> None:
        self.stage = stage
        self.cause = cause
        super().__init__(f"generate stage {stage} failed: {type(cause).__name__}: {cause}")


@dataclass(frozen=True)
class GenerateRequest:
    """Input contract for one Markdown-first proposal delivery."""

    request: str
    output_dir: Path
    formats: tuple[str, ...]
    debug: bool = False
    run_log_path: Path | None = None
    ppt_mode: str = "presentation"
    max_slides: int = 15


@dataclass(frozen=True)
class GenerateResult:
    """Paths and metadata produced by completed workflow stages."""

    markdown_path: Path
    run_log_path: Path
    request_metadata_path: Path
    quality_status: str
    warnings: tuple[str, ...]
    metrics: dict[str, Any]
    word_path: Path | None = None
    word_report_path: Path | None = None
    slides_markdown_path: Path | None = None
    slide_plan_path: Path | None = None
    pptx_path: Path | None = None
    pptx_report_path: Path | None = None


class ProposalWorkflow:
    """Coordinate existing generation and rendering functions through injected ports."""

    def __init__(
        self,
        *,
        llm_factory: Callable[[], Any],
        retriever: Callable[[list[str]], Any],
        markdown_generator: Callable[..., _ProposalResult | Path],
        word_renderer: Callable[..., Path],
        slides_builder: Callable[..., tuple[Path, Path, list[str]]],
        pptx_renderer: Callable[..., Path],
        run_log_writer: Callable[..., None],
        request_writer: Callable[..., Path],
    ) -> None:
        self._llm_factory = llm_factory
        self._retriever = retriever
        self._markdown_generator = markdown_generator
        self._word_renderer = word_renderer
        self._slides_builder = slides_builder
        self._pptx_renderer = pptx_renderer
        self._run_log_writer = run_log_writer
        self._request_writer = request_writer

    @staticmethod
    def _formats(values: Iterable[str]) -> tuple[str, ...]:
        formats = tuple(dict.fromkeys(value.strip().lower() for value in values if value.strip()))
        unsupported = set(formats) - SUPPORTED_FORMATS
        if not formats:
            raise ValueError("至少需要一个输出格式：md、docx 或 pptx")
        if unsupported:
            raise ValueError(f"不支持的输出格式：{', '.join(sorted(unsupported))}")
        return formats

    def _fatal(self, run_log: Path, stage: str, cause: BaseException) -> GenerateWorkflowError:
        try:
            self._run_log_writer(
                run_log,
                status="FATAL",
                stage=stage,
                error_type=type(cause).__name__,
            )
        except OSError:
            # The original error remains more actionable when the output path
            # itself is unavailable, so never replace it with a log failure.
            pass
        return GenerateWorkflowError(stage, cause)

    @staticmethod
    def _validate_output_dir(output_dir: Path) -> None:
        """Reject prior managed artifacts before constructing an LLM client.

        A unified run writes multiple correlated files.  Reusing a directory
        after a partial failure could otherwise combine a new proposal.md with
        an old DOCX or PPTX and make the stale artifact look current.
        """
        occupied = sorted(name for name in _MANAGED_OUTPUT_NAMES if (output_dir / name).exists())
        if occupied:
            raise ValueError(f"输出目录包含已有托管产物：{', '.join(occupied)}；请使用新的空目录")

    def run(self, request: GenerateRequest) -> GenerateResult:
        """Generate Markdown first, then consume it for explicitly requested artifacts."""
        formats = self._formats(request.formats)
        if not request.request.strip():
            raise ValueError("方案需求不能为空")
        if request.max_slides < 1:
            raise ValueError("max_slides 必须为正数")

        output_dir = Path(request.output_dir)
        self._validate_output_dir(output_dir)
        markdown_path = output_dir / "proposal.md"
        run_log = Path(request.run_log_path) if request.run_log_path else output_dir / "proposal.run.log"
        try:
            if output_dir.exists() and not output_dir.is_dir():
                raise NotADirectoryError(f"输出目录不是目录：{output_dir}")
            self._run_log_writer(run_log, status="RUNNING", stage="initializing", output_file=markdown_path.name)
        except OSError as exc:
            raise GenerateWorkflowError("output_initialization", exc) from exc

        try:
            llm = self._llm_factory()
            self._run_log_writer(
                run_log,
                status="RUNNING",
                stage="planning",
                provider="dashscope_openai_compatible",
                **getattr(llm, "connection_settings", {}),
            )
        except Exception as exc:  # noqa: BLE001 - boundary normalises provider errors.
            raise self._fatal(run_log, "planning", exc) from exc

        def progress(stage: str, details: dict[str, Any]) -> None:
            self._run_log_writer(run_log, status="RUNNING", stage=stage, **details)

        try:
            generated = self._markdown_generator(
                request.request,
                markdown_path,
                llm=llm,
                retriever=self._retriever,
                return_result=True,
                progress=progress,
            )
            generated_path = Path(getattr(generated, "path", generated))
            quality_status = str(getattr(generated, "quality_status", "PASS"))
            warnings = tuple(getattr(generated, "warnings", ()))
            metrics = dict(getattr(generated, "metrics", {}))
        except Exception as exc:  # noqa: BLE001 - preserve the completed stage in the log.
            stage = str(getattr(exc, "stage", "markdown_generation"))
            raise self._fatal(run_log, stage, exc) from exc

        try:
            request_metadata = self._request_writer(generated_path, request.request, run_log=run_log, debug=request.debug)
        except Exception as exc:  # noqa: BLE001 - Markdown is retained for diagnosis.
            raise self._fatal(run_log, "request_metadata", exc) from exc
        self._run_log_writer(run_log, status=quality_status, stage="markdown_complete", warnings=list(warnings), metrics=metrics)

        word_path: Path | None = None
        word_report: Path | None = None
        slides_path: Path | None = None
        slide_plan: Path | None = None
        pptx_path: Path | None = None
        pptx_report: Path | None = None

        if "docx" in formats:
            word_path = output_dir / "proposal.docx"
            try:
                word_report = Path(self._word_renderer(generated_path, word_path))
                self._run_log_writer(run_log, status="RUNNING", stage="word_complete", output_file=word_path.name)
            except Exception as exc:  # noqa: BLE001
                raise self._fatal(run_log, "word_render", exc) from exc

        if "pptx" in formats:
            slides_path = output_dir / "slides.md"
            sources_path = generated_path.with_name("proposal.sources.json")
            try:
                slides_path, slide_plan, warnings_from_slides = self._slides_builder(
                    generated_path,
                    slides_path,
                    mode=request.ppt_mode,
                    max_slides=request.max_slides,
                    sources_path=sources_path if sources_path.is_file() else None,
                )
                warnings = (*warnings, *warnings_from_slides)
                self._run_log_writer(run_log, status="RUNNING", stage="slides_complete", output_file=slides_path.name)
            except Exception as exc:  # noqa: BLE001
                raise self._fatal(run_log, "slides_build", exc) from exc
            pptx_path = output_dir / "proposal.pptx"
            try:
                pptx_report = Path(self._pptx_renderer(
                    slides_path,
                    pptx_path,
                    mode=request.ppt_mode,
                    max_slides=request.max_slides,
                    sources_path=sources_path if sources_path.is_file() else None,
                    plan_path=slide_plan,
                ))
                self._run_log_writer(run_log, status="RUNNING", stage="pptx_complete", output_file=pptx_path.name)
            except Exception as exc:  # noqa: BLE001
                raise self._fatal(run_log, "pptx_render", exc) from exc

        self._run_log_writer(run_log, status=quality_status, stage="complete", warnings=list(warnings), metrics=metrics)
        return GenerateResult(
            markdown_path=generated_path,
            run_log_path=run_log,
            request_metadata_path=Path(request_metadata),
            quality_status=quality_status,
            warnings=warnings,
            metrics=metrics,
            word_path=word_path,
            word_report_path=word_report,
            slides_markdown_path=slides_path,
            slide_plan_path=slide_plan,
            pptx_path=pptx_path,
            pptx_report_path=pptx_report,
        )
