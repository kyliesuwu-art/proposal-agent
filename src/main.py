# main.py
"""方案知识库 CLI 入口：解析命令行参数，分发到 pipeline.py 里的业务逻辑。

用法：
    uv run python src/main.py ingest-cache-dir <缓存目录> [--db runtime_data/word_test_db] --dry-run
    uv run python src/main.py ingest-cache-dir <缓存目录> [--db runtime_data/word_test_db] --resume --limit 10 --batch-size 32
    uv run python src/main.py proposal "需求描述" --output outputs/proposal.md
    uv run python src/main.py proposal-diagnose outputs/proposal.md
    uv run python src/main.py annotate <source_file> # 为源文件进行标注/打标签
    uv run python src/main.py status                 # 查看库状态
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
from src.markdown_proposal import ProposalGenerationError, ProposalWriteError, diagnose_markdown_proposal, generate_markdown_proposal
from src.render_word import RenderWordError, render_word
from src.render_pptx import RenderPptxError, render_pptx, write_pptx_layout_audit


def _write_proposal_request(output_path: Path, request: str, *, run_log: Path, debug: bool) -> Path:
    """Persist reproducibility metadata only after a successful proposal delivery."""
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
    """Persist prompt-free, credential-free progress before and after remote work."""
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
    """支持传入单个文件，也支持传入目录（自动遍历目录下所有受支持格式的文件）。"""
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
    """Read-only directory inspection; deliberately does not open the target DB."""
    from src.cache_batch import inspect_cache_directory
    import json

    try:
        report = inspect_cache_directory(path_str, target_db=target_db)
    except ValueError as exc:
        print(str(exc))
        sys.exit(1)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def _ingest_cache_dir_resume(path_str: str, target_db: str, limit: int, batch_size: int) -> None:
    from src.cache_batch import CacheBatchIngestor
    import json

    ingestor = CacheBatchIngestor(path_str, target_db)
    try:
        print(json.dumps(ingestor.ingest(limit=limit, batch_size=batch_size), ensure_ascii=False, indent=2))
    finally:
        ingestor.close()


def main() -> None:
    """CLI 入口：解析命令，分发到对应函数。"""
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1].lower()

    if command in {"-h", "--help"}:
        print(__doc__)
        return

    if command == "ingest-cache-dir":
        if len(sys.argv) == 6 and sys.argv[3] == "--db" and sys.argv[5] == "--dry-run":
            _ingest_cache_dir_dry_run(sys.argv[2], sys.argv[4])
        elif len(sys.argv) == 10 and sys.argv[3] == "--db" and sys.argv[5] == "--resume" and sys.argv[6] == "--limit" and sys.argv[8] == "--batch-size":
            _ingest_cache_dir_resume(sys.argv[2], sys.argv[4], int(sys.argv[7]), int(sys.argv[9]))
        else:
            print("用法: python main.py ingest-cache-dir <缓存目录> --db <候选库目录> --dry-run | --resume --limit <数量> --batch-size <数量>")
            sys.exit(1)

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

        result = pipeline.query_rag(sys.argv[2])
        markdown = pipeline.render_markdown(result)
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(markdown, encoding="utf-8")
            print(f"已写入 Markdown：{output_path.resolve()}")
        print(markdown)

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

    elif command == "render-word":
        if len(sys.argv) != 6 or sys.argv[2] != "--input" or sys.argv[4] != "--output":
            print("用法: python src/main.py render-word --input <proposal.md> --output <proposal.docx>", file=sys.stderr); sys.exit(1)
        try: report = render_word(sys.argv[3], sys.argv[5])
        except RenderWordError as exc: print(f"Word 渲染失败: {exc}", file=sys.stderr); sys.exit(1)
        print(f"已写入 DOCX: {Path(sys.argv[5]).resolve()}\n报告: {report}")
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
        _write_proposal_run_log(run_log, status="RUNNING", stage="initializing", output_file=output_path.name)
        try:
            try:
                llm = LLMClient()
                _write_proposal_run_log(run_log, status="RUNNING", stage="planning", provider="dashscope_openai_compatible", **llm.connection_settings)
            except Exception as exc:  # noqa: BLE001
                raise ProposalGenerationError("planning", exc) from exc
            def progress(stage: str, details: dict) -> None:
                _write_proposal_run_log(run_log, status="RUNNING", stage=stage, **details)
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
        _write_proposal_run_log(run_log, status=result.quality_status, stage="complete", warnings=result.warnings, metrics=result.metrics)
        _write_proposal_request(output_path, sys.argv[2], run_log=run_log, debug=debug)

    else:
        print(f"未知命令: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
