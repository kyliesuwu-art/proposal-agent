"""CLI argument parsing and dispatch; production dependencies come from the entry point."""

import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

from src.application import GenerateRequest, GenerateWorkflowError
from src.markdown_proposal import ProposalGenerationError, ProposalQualityError, ProposalWriteError
from src.render_word import RenderWordError
from src.render_pptx import RenderPptxError


def dispatch(context) -> None:
    """解析 CLI 命令并将请求分发到对应业务模块。

    该函数只处理参数、错误展示和结果打印；具体业务由各分支导入或调用的模块完成。
    """
    # Resolve the legacy composition root at call time so existing imports and
    # test/provider overrides of src.main remain effective.
    pipeline = context.pipeline
    LLMClient = context.LLMClient
    generate_markdown_proposal = context.generate_markdown_proposal
    render_word = context.render_word
    render_pptx = context.render_pptx
    _proposal_workflow = context._proposal_workflow
    _write_proposal_run_log = context._write_proposal_run_log
    _write_proposal_request = context._write_proposal_request
    help_text = context.__doc__
    if len(sys.argv) < 2:
        print(help_text)
        sys.exit(1)

    command = sys.argv[1].lower()

    # 【帮助】不执行任何业务，只显示当前实际支持的命令说明。
    if command in {"-h", "--help"}:
        print(help_text)
        return

    # 【缓存入库】先只读检查，或在显式 --resume 时将 MinerU 缓存恢复到候选数据库。
    if command == "ingest-cache-dir":
        from src.cli_admin import ingest_cache_dir
        ingest_cache_dir(context)

    elif command == "reindex-affected":
        from src.cli_admin import reindex_affected
        reindex_affected(context)

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
        from src.cli_diagnostics import proposal_diagnose
        proposal_diagnose(context)

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
        from src.cli_diagnostics import audit_pptx
        audit_pptx(context)

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
        print(help_text)
        sys.exit(1)
