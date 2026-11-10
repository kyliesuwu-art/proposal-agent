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
    """?? CLI???????????????"""
    from src.cli import dispatch
    dispatch(sys.modules[__name__])


if __name__ == "__main__":
    main()
