# main.py
"""方案知识库 CLI 入口：解析命令行参数，分发到 pipeline.py 里的业务逻辑。

用法：
    uv run python src/main.py ingest-cache-dir <缓存目录> [--db runtime_data/word_test_db] --dry-run
    uv run python src/main.py ingest-cache-dir <缓存目录> [--db runtime_data/word_test_db] --resume --limit 10 --batch-size 32
    uv run python src/main.py proposal "需求描述" --output outputs/proposal.md
    uv run python src/main.py annotate <source_file> # 为源文件进行标注/打标签
    uv run python src/main.py status                 # 查看库状态
"""

import sys
import traceback
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
from src.markdown_proposal import ProposalGenerationError, generate_markdown_proposal


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

    elif command == "proposal":
        if len(sys.argv) == 3 and sys.argv[2] in {"-h", "--help"}:
            print('用法: python main.py proposal "需求描述" --output <proposal.md> [--debug]')
            return
        if len(sys.argv) not in {5, 6} or sys.argv[3] != "--output" or (len(sys.argv) == 6 and sys.argv[5] != "--debug"):
            print('用法: python main.py proposal "需求描述" --output <proposal.md> [--debug]', file=sys.stderr)
            sys.exit(1)
        output_path = Path(sys.argv[4])
        debug = len(sys.argv) == 6
        try:
            try:
                llm = LLMClient()
            except Exception as exc:  # noqa: BLE001
                raise ProposalGenerationError("planning", exc) from exc
            generated = generate_markdown_proposal(
                sys.argv[2], output_path, llm=llm,
                retriever=lambda queries: pipeline.retrieve_evidence(queries),
            )
        except Exception as exc:  # CLI boundary: preserve a clear configuration/service error.
            stage = exc.stage if isinstance(exc, ProposalGenerationError) else "unknown"
            print(f"方案生成失败 [{stage}] {type(exc).__name__}: {exc}", file=sys.stderr)
            if debug:
                traceback.print_exception(exc, file=sys.stderr)
            sys.exit(1)
        print(f"已写入 Markdown：{generated.resolve()}")

    else:
        print(f"未知命令: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
