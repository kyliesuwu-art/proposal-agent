# main.py
"""方案知识库的命令行入口。

本文件只负责解析命令行参数和调用业务模块；检索、生成、渲染等具体逻辑分别在
``pipeline.py``、``markdown_proposal.py``、``render_word.py`` 和 ``render_pptx.py`` 中。

核心业务命令：
    # 查询知识库，将检索结果输出为 Markdown。
    uv run python src/main.py query "需求描述" [--output outputs/query.md]

    # 使用 RAG 检索和大模型生成方案 Markdown。
    uv run python src/main.py proposal "需求描述" --output outputs/proposal.md [--debug] [--run-log outputs/proposal.run.log]
    uv run python src/main.py generate "需求描述" --formats md docx pptx [--output-dir outputs/generate-run]

    # 将已有方案 Markdown 渲染为 Word。
    uv run python src/main.py render-word --input outputs/proposal.md --output outputs/proposal.docx

    # 从方案 Markdown 生成适合演示的中间 Markdown 和幻灯片计划。
    uv run python src/main.py build-slides --input outputs/proposal.md --output outputs/slides.md [--sources outputs/proposal.sources.json] [--mode briefing] [--max-slides 15]

    # 将方案或中间 Markdown 渲染为 PPTX；可复用 build-slides 生成的计划。
    uv run python src/main.py render-pptx --input outputs/slides.md --output outputs/proposal.pptx [--plan outputs/slides.slide-plan.json]

数据库维护命令：
    # 只读检查 MinerU 缓存目录，不打开目标数据库。
    uv run python src/main.py ingest-cache-dir <缓存目录> --db <候选数据库目录> --dry-run

    # 将缓存恢复并分批写入明确指定的候选数据库。
    uv run python src/main.py ingest-cache-dir <缓存目录> --db <候选数据库目录> --resume --limit 10 --batch-size 32

    # 只读生成受影响文档的定向重建预检报告；当前不执行实际重建。
    uv run python src/main.py reindex-affected --cache-dir <缓存目录> --source-db <源数据库目录> --target-db <候选数据库目录> --plan <计划文件> --dry-run

诊断命令：
    # 检查已有方案 Markdown，并输出可移植的诊断报告。
    uv run python src/main.py proposal-diagnose outputs/proposal.md [--output outputs/proposal.diagnosis.md]

    # 检查 PPTX 几何布局；未提供页面渲染图时，视觉检查会标记为受阻。
    uv run python src/main.py audit-pptx --input outputs/proposal.pptx --plan outputs/slides.slide-plan.json --output-dir outputs/pptx-audit
"""

import sys
import traceback
import json
from datetime import datetime
from pathlib import Path

# 加载 .env 环境变量（MINERU_TOKEN 等）
# 确保以脚本方式执行时也能 import src 下的模块。
_BOOTSTRAP_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BOOTSTRAP_PROJECT_ROOT))

from src.config import load_project_environment

load_project_environment()

from src import pipeline
from src.adapters.parser import SUPPORTED_EXTENSIONS
from src.adapters.llm_client import LLMClient
from src.markdown_proposal import ProposalGenerationError, ProposalQualityError, ProposalWriteError, diagnose_markdown_proposal, generate_markdown_proposal
from src.render_word import RenderWordError, render_word
from src.render_pptx import RenderPptxError, render_pptx, write_pptx_layout_audit
from src.application import GenerateRequest, GenerateWorkflowError, ProposalWorkflow


def _write_proposal_request(output_path: Path, request: str, *, run_log: Path, debug: bool) -> Path:
    """在方案成功写入后，保存用户需求和可复现运行元数据。

    输入是方案输出路径、用户需求和运行日志路径；返回写出的 JSON 文件路径。
    """
    path = output_path.with_name("proposal.request.json")
    path.write_text(json.dumps({
        "request": request,
        "generated_at": datetime.now().astimezone().isoformat(),
        "output_file": output_path.name,
        "sources_file": "proposal.sources.json",
        "run_log": run_log.name,
        "debug": debug,
        "cli_arguments": {"debug": debug, "run_log": run_log.name},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _write_proposal_run_log(path: Path, *, status: str, stage: str, **details: object) -> None:
    """记录方案生成的阶段状态，供失败诊断和后续追踪使用。

    日志只保存状态和结构化元数据，不保存提示词或凭据；由 ``proposal`` 分支在关键阶段调用。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().isoformat()
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        previous = {}
    event = {"status": status, "stage": stage, "updated_at": timestamp, **details}
    events = previous.get("events", []) if isinstance(previous, dict) else []
    if not isinstance(events, list):
        events = []
    payload = {**previous, **event, "events": [*events, event]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _ingest_path(path_str: str) -> None:
    """将单个资料文件或目录中的受支持文件交给 ``pipeline.ingest`` 入库。

    目录输入会逐个调用 ``pipeline.ingest`` 并汇总成功和失败项；当前 CLI 尚未注册此旧辅助函数。
    """
    path = Path(path_str)
    if not path.exists():
        print(f"路径不存在: {path}")
        sys.exit(1)

    if path.is_dir():
        files = sorted(
            p for p in path.rglob("*")
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        )
        if not files:
            print(f"目录下没有找到支持的文件（{', '.join(sorted(SUPPORTED_EXTENSIONS))}）: {path}")
            return

        print(f"共找到 {len(files)} 个文件，开始批量入库...\n")
        succeeded: list[str] = []
        failed: list[tuple[str, str]] = []

        for i, f in enumerate(files, 1):
            print(f"[{i}/{len(files)}] {f.name}")
            try:
                pipeline.ingest(str(f))
                succeeded.append(f.name)
            except Exception as e:
                print(f"  [FAIL] 失败: {e}")
                failed.append((f.name, str(e)))
            print()

        print("=" * 40)
        print(f"批量入库完成：成功 {len(succeeded)} / 失败 {len(failed)}（共 {len(files)}）")
        if failed:
            print("失败列表：")
            for name, err in failed:
                print(f"  - {name}: {err}")
    else:
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            print(f"不支持的文件格式: {path.suffix}（目前支持 {', '.join(sorted(SUPPORTED_EXTENSIONS))}）")
            sys.exit(1)
        pipeline.ingest(str(path))


def _ingest_cache_dir_dry_run(path_str: str, target_db: str) -> None:
    """只读检查 MinerU 缓存目录，并输出候选入库报告。

    调用 ``cache_batch.inspect_cache_directory``；目标数据库仅作为报告中的计划目标，不会被打开。
    """
    from src.cache_batch import inspect_cache_directory
    import json

    try:
        report = inspect_cache_directory(path_str, target_db=target_db)
    except ValueError as exc:
        print(str(exc))
        sys.exit(1)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def _ingest_cache_dir_resume(path_str: str, target_db: str, limit: int, batch_size: int) -> None:
    """从缓存恢复资料并分批写入指定的候选数据库。

    调用 ``CacheBatchIngestor.ingest``，参数 ``limit`` 和 ``batch_size`` 控制本次处理范围和批大小。
    """
    from src.cache_batch import CacheBatchIngestor
    import json

    ingestor = CacheBatchIngestor(path_str, target_db)
    try:
        print(json.dumps(ingestor.ingest(limit=limit, batch_size=batch_size), ensure_ascii=False, indent=2))
    finally:
        ingestor.close()


def _proposal_workflow() -> ProposalWorkflow:
    """Compose the unified workflow from the existing production functions.

    Keeping construction here lets the CLI remain the composition root while the
    workflow itself stays testable with fakes and owns no provider-specific code.
    """
    from src.render_pptx import build_slides_markdown

    return ProposalWorkflow(
        llm_factory=LLMClient,
        retriever=pipeline.retrieve_evidence,
        markdown_generator=generate_markdown_proposal,
        word_renderer=render_word,
        slides_builder=build_slides_markdown,
        pptx_renderer=render_pptx,
        run_log_writer=_write_proposal_run_log,
        request_writer=_write_proposal_request,
    )


def main() -> None:
    """解析 CLI 命令并将请求分发到对应业务模块。

    该函数只处理参数、错误展示和结果打印；具体业务由各分支导入或调用的模块完成。
    """
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1].lower()

    # 【帮助】不执行任何业务，只显示当前实际支持的命令说明。
    if command in {"-h", "--help"}:
        print(__doc__)
        return

    # 【缓存入库】先只读检查，或在显式 --resume 时将 MinerU 缓存恢复到候选数据库。
    if command == "ingest-cache-dir":
        if len(sys.argv) == 6 and sys.argv[3] == "--db" and sys.argv[5] == "--dry-run":
            _ingest_cache_dir_dry_run(sys.argv[2], sys.argv[4])
        elif len(sys.argv) == 10 and sys.argv[3] == "--db" and sys.argv[5] == "--resume" and sys.argv[6] == "--limit" and sys.argv[8] == "--batch-size":
            _ingest_cache_dir_resume(sys.argv[2], sys.argv[4], int(sys.argv[7]), int(sys.argv[9]))
        else:
            print("用法: python main.py ingest-cache-dir <缓存目录> --db <候选库目录> --dry-run | --resume --limit <数量> --batch-size <数量>")
            sys.exit(1)

    # 【定向重建预检】验证计划和数据库路径；当前版本只支持不写库的 --dry-run。
    elif command == "reindex-affected":
        import argparse
        parser = argparse.ArgumentParser(prog="python src/main.py reindex-affected")
        parser.add_argument("--cache-dir", required=True)
        parser.add_argument("--source-db", required=True)
        parser.add_argument("--target-db", required=True)
        parser.add_argument("--plan", required=True)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--resume", action="store_true")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--batch-size", type=int, default=32)
        args = parser.parse_args(sys.argv[2:])
        if args.dry_run == args.resume:
            parser.error("必须且只能指定 --dry-run 或 --resume")
        from src.targeted_reindex import dry_run
        if args.resume:
            parser.error("实际定向重处理需要单独授权；本版本只提供不会创建候选库的 --dry-run")
        try:
            print(json.dumps(dry_run(args.plan, args.source_db, args.target_db, limit=args.limit), ensure_ascii=False, indent=2))
        except ValueError as exc:
            parser.error(str(exc))

    # 【知识库查询】将用户问题交给 pipeline 检索，并把结构化结果渲染为 Markdown。
    elif command == "query":
        if len(sys.argv) < 3:
            print("用法: python main.py query \"需求描述\" [--output <路径>]")
            sys.exit(1)
        output_path = None
        extra_args = sys.argv[3:]
        if extra_args:
            if len(extra_args) != 2 or extra_args[0] != "--output":
                print("用法: python main.py query \"需求描述\" [--output <路径>]")
                sys.exit(1)
            output_path = Path(extra_args[1])

        # pipeline 返回 QueryResult；再由同一模块统一转换为可查看、可保存的 Markdown。
        result = pipeline.query_rag(sys.argv[2])
        markdown = pipeline.render_markdown(result)
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(markdown, encoding="utf-8")
            print(f"已写入 Markdown：{output_path.resolve()}")
        print(markdown)

    # 【方案诊断】只读检查已生成的 Markdown，输出独立的诊断报告。
    elif command == "proposal-diagnose":
        if len(sys.argv) not in {3, 5} or (len(sys.argv) == 5 and sys.argv[3] != "--output"):
            print('用法: python main.py proposal-diagnose <proposal.md> [--output <proposal.diagnosis.md>]', file=sys.stderr)
            sys.exit(1)
        try:
            output = diagnose_markdown_proposal(Path(sys.argv[2]), Path(sys.argv[4]) if len(sys.argv) == 5 else None)
        except Exception as exc:  # noqa: BLE001
            print(f"方案诊断失败：{type(exc).__name__}: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"已写入诊断：{output.resolve()}")

    # 【生成 Word】消费已有方案 Markdown，离线渲染 DOCX，不重新检索或调用模型。
    elif command == "render-word":
        if len(sys.argv) != 6 or sys.argv[2] != "--input" or sys.argv[4] != "--output":
            print("用法: python src/main.py render-word --input <proposal.md> --output <proposal.docx>", file=sys.stderr); sys.exit(1)
        try: report = render_word(sys.argv[3], sys.argv[5])
        except RenderWordError as exc: print(f"Word 渲染失败: {exc}", file=sys.stderr); sys.exit(1)
        print(f"已写入 DOCX: {Path(sys.argv[5]).resolve()}\n报告: {report}")
    # 【生成 PPT】消费 Markdown 和可选幻灯片计划，离线渲染为 PPTX。
    elif command == "render-pptx":
        import argparse
        parser = argparse.ArgumentParser(prog="python src/main.py render-pptx")
        parser.add_argument("--input", required=True)
        parser.add_argument("--output", required=True)
        parser.add_argument("--mode", choices=("briefing", "faithful", "presentation"), default="presentation")
        parser.add_argument("--max-slides", type=int, default=15, help="Hard cap for briefing; presentation uses it as a continuation warning threshold")
        parser.add_argument("--sources")
        parser.add_argument("--plan", help="Use an existing proposal.slide-plan.json produced by build-slides")
        try:
            args = parser.parse_args(sys.argv[2:])
            report = render_pptx(args.input, args.output, mode=args.mode, max_slides=args.max_slides, sources_path=args.sources, plan_path=args.plan)
        except RenderPptxError as exc:
            print(f"PPTX render failed: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"PPTX: {Path(args.output).resolve()}\nSlide plan: {Path(args.output).with_suffix('.slide-plan.json')}\nReport: {report}")
    # 【构建幻灯片计划】先生成演示用 Markdown 与 slide-plan，供人工检查或 render-pptx 复用。
    elif command == "build-slides":
        import argparse
        from src.render_pptx import build_slides_markdown
        parser = argparse.ArgumentParser(prog="python src/main.py build-slides", description="Build presentation Markdown and a slide plan. briefing creates an evidence-traceable summary under a hard slide cap.")
        parser.add_argument("--input", required=True); parser.add_argument("--output", required=True)
        parser.add_argument("--sources"); parser.add_argument("--mode", choices=("briefing", "presentation", "faithful"), default="briefing")
        parser.add_argument("--max-slides", type=int, default=15, help="Hard total-slide cap in briefing mode; includes cover, agenda, content, and references")
        args = parser.parse_args(sys.argv[2:])
        output, plan, warnings = build_slides_markdown(args.input, args.output, mode=args.mode, max_slides=args.max_slides, sources_path=args.sources)
        print(f"Slides Markdown: {output}\nSlide plan: {plan}\nWarnings: {json.dumps(warnings, ensure_ascii=False)}")
    # 【PPT 布局诊断】读取 PPTX 与 slide-plan，输出几何布局审计报告。
    elif command == "audit-pptx":
        import argparse
        parser = argparse.ArgumentParser(prog="python src/main.py audit-pptx", description="Write PPTX geometry audit. It reports visual rendering as BLOCKED until a renderer produces page PNGs.")
        parser.add_argument("--input", required=True); parser.add_argument("--plan", required=True); parser.add_argument("--output-dir", required=True)
        args = parser.parse_args(sys.argv[2:])
        try:
            json_path, md_path = write_pptx_layout_audit(args.input, args.plan, args.output_dir)
        except RenderPptxError as exc:
            parser.error(str(exc))
        print(f"Visual audit JSON: {json_path}\nVisual audit Markdown: {md_path}")
    elif command == "generate":
        import argparse
        parser = argparse.ArgumentParser(
            prog="python src/main.py generate",
            description="Generate Markdown first, then optionally render DOCX and PPTX from that same Markdown.",
        )
        parser.add_argument("request")
        parser.add_argument("--formats", required=True, nargs="+", choices=("md", "docx", "pptx"))
        parser.add_argument("--output-dir", help="Directory for proposal.md and requested artifacts")
        parser.add_argument("--run-log")
        parser.add_argument("--debug", action="store_true")
        parser.add_argument("--ppt-mode", choices=("briefing", "faithful", "presentation"), default="presentation")
        parser.add_argument("--max-slides", type=int, default=15)
        args = parser.parse_args(sys.argv[2:])
        output_dir = Path(args.output_dir) if args.output_dir else Path("outputs") / f"generate-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
        try:
            result = _proposal_workflow().run(GenerateRequest(
                request=args.request,
                output_dir=output_dir,
                formats=tuple(args.formats),
                debug=args.debug,
                run_log_path=Path(args.run_log) if args.run_log else None,
                ppt_mode=args.ppt_mode,
                max_slides=args.max_slides,
            ))
        except (GenerateWorkflowError, ValueError) as exc:
            stage = exc.stage if isinstance(exc, GenerateWorkflowError) else "input_validation"
            cause = exc.cause if isinstance(exc, GenerateWorkflowError) else exc
            print(f"统一生成失败 [{stage}] {type(cause).__name__}: {cause}", file=sys.stderr)
            if args.debug:
                traceback.print_exception(exc, file=sys.stderr)
            sys.exit(1)
        print(f"Markdown: {result.markdown_path.resolve()}\nRun log: {result.run_log_path.resolve()}\nQuality: {result.quality_status}")
        if result.word_path:
            print(f"DOCX: {result.word_path.resolve()}\nWord report: {result.word_report_path}")
        if result.pptx_path:
            print(f"Slides Markdown: {result.slides_markdown_path.resolve()}\nSlide plan: {result.slide_plan_path.resolve()}\nPPTX: {result.pptx_path.resolve()}\nPPTX report: {result.pptx_report_path}")
    # 【生成方案】用户需求先转为检索计划，再以检索证据为依据生成并校验 Markdown。
    elif command == "proposal":
        if len(sys.argv) == 3 and sys.argv[2] in {"-h", "--help"}:
            print('用法: python main.py proposal "需求描述" --output <proposal.md> [--debug]')
            return
        if len(sys.argv) < 5 or sys.argv[3] != "--output":
            print('用法: python main.py proposal "需求描述" --output <proposal.md> [--debug] [--run-log <proposal.run.log>]', file=sys.stderr)
            sys.exit(1)
        output_path = Path(sys.argv[4])
        extras = sys.argv[5:]
        debug = "--debug" in extras
        if extras.count("--debug") > 1 or any(value not in {"--debug", "--run-log"} and (index == 0 or extras[index - 1] != "--run-log") for index, value in enumerate(extras)) or extras.count("--run-log") > 1:
            print('用法: python main.py proposal "需求描述" --output <proposal.md> [--debug] [--run-log <proposal.run.log>]', file=sys.stderr)
            sys.exit(1)
        run_log = output_path.with_name("proposal.run.log")
        if "--run-log" in extras:
            index = extras.index("--run-log")
            if index + 1 >= len(extras):
                print('用法: python main.py proposal "需求描述" --output <proposal.md> [--debug] [--run-log <proposal.run.log>]', file=sys.stderr)
                sys.exit(1)
            run_log = Path(extras[index + 1])
        # 先写入本地运行日志，使配置或远程调用失败时也能定位停在了哪个阶段。
        _write_proposal_run_log(run_log, status="RUNNING", stage="initializing", output_file=output_path.name)
        try:
            try:
                # 初始化大模型客户端；连接配置会写入日志，但不记录密钥。
                llm = LLMClient()
                _write_proposal_run_log(run_log, status="RUNNING", stage="planning", provider="dashscope_openai_compatible", **llm.connection_settings)
            except Exception as exc:  # noqa: BLE001
                raise ProposalGenerationError("planning", exc) from exc
            def progress(stage: str, details: dict) -> None:
                # 方案生成模块在规划、检索、写作等阶段回调此函数，持续更新本地运行日志。
                _write_proposal_run_log(run_log, status="RUNNING", stage=stage, **details)
            # 生成模块负责方案编排；retriever 将它需要的查询交回 pipeline 取得知识库证据。
            result = generate_markdown_proposal(
                sys.argv[2], output_path, llm=llm,
                retriever=lambda queries: pipeline.retrieve_evidence(queries),
                return_result=True, progress=progress,
            )
        except Exception as exc:  # CLI boundary: preserve a clear configuration/service error.
            stage = exc.stage if isinstance(exc, ProposalGenerationError) else "unknown"
            details: dict[str, object] = {"error_type": type(exc).__name__}
            if isinstance(exc, ProposalGenerationError):
                details["cause_type"] = type(exc.cause).__name__
                if exc.stage in {"quality_gate", "scope_gate"} and isinstance(exc.cause, ProposalQualityError):
                    # The quality gate only raises deterministic rule messages.
                    # Keep the complete reason alongside its structured snapshot.
                    details["quality_gate_error_message"] = str(exc.cause)
                if isinstance(exc.cause, ProposalWriteError):
                    details["write_operation"] = exc.cause.operation
                    details["write_cause_type"] = type(exc.cause.cause).__name__
            _write_proposal_run_log(run_log, status="FATAL", stage=stage, **details)
            print(f"方案生成失败 [{stage}] {type(exc).__name__}: {exc}", file=sys.stderr)
            if debug:
                traceback.print_exception(exc, file=sys.stderr)
            sys.exit(1)
        print(f"已写入 Markdown：{result.path.resolve()}")
        print(f"质量状态：{result.quality_status}；通过项：{result.metrics['quality_passed_checks']}；warning：{len(result.warnings)}；DOCX：未生成")
        for warning in result.warnings[:5]:
            print(f"  - {warning}")
        # 成功后保存质量摘要和请求元数据，便于复盘本次已交付的方案。
        _write_proposal_run_log(run_log, status=result.quality_status, stage="complete", warnings=result.warnings, metrics=result.metrics)
        _write_proposal_request(output_path, sys.argv[2], run_log=run_log, debug=debug)

    else:
        print(f"未知命令: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
