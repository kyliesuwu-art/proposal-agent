# main.py
"""方案知识库 CLI 入口：解析命令行参数，分发到 pipeline.py 里的业务逻辑。

用法：
    uv run python src/main.py ingest <文件或目录路径> # 解析并入库（单个文件或整个目录批量）
    uv run python src/main.py query "需求描述" [--output <路径>] # 检索并输出 Markdown
    uv run python src/main.py ingest-v2 <文件或目录> --source-root <样例目录> --test-db <可删除目录>
    uv run python src/main.py query-v2 "精确参数问题" --test-db <可删除目录>
    uv run python src/main.py annotate <source_file> # 为源文件进行标注/打标签
    uv run python src/main.py status                 # 查看库状态
"""

import sys
from pathlib import Path

# 加载 .env 环境变量（MINERU_TOKEN 等）
from dotenv import load_dotenv
load_dotenv()

# 确保以脚本方式执行时也能 import src 下的模块。
_BOOTSTRAP_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BOOTSTRAP_PROJECT_ROOT))

from src.config import PROJECT_ROOT

from src import pipeline
from src.adapters.parser import SUPPORTED_EXTENSIONS


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


def _ingest_v2_path(path_str: str, source_root: str, test_db: str) -> None:
    """V2 batch helper: its source root and disposable store are always explicit."""
    path = Path(path_str)
    if not path.exists():
        print(f"路径不存在: {path}")
        sys.exit(1)
    files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS)
    if not files:
        print("指定的 V2 样例目录中没有支持的文件")
        return
    for file_path in files:
        outcome = pipeline.ingest_v2(file_path, source_root=source_root, test_db=test_db)
        print(f"V2 {outcome['status']}: {outcome['source_key']} — {outcome['message']}")


def main() -> None:
    """CLI 入口：解析命令，分发到对应函数。"""
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == "ingest":
        if len(sys.argv) < 3:
            print("用法: python main.py ingest <文件或目录路径>")
            sys.exit(1)
        _ingest_path(sys.argv[2])

    elif command == "ingest-v2":
        if len(sys.argv) != 7 or sys.argv[3] != "--source-root" or sys.argv[5] != "--test-db":
            print("用法: python main.py ingest-v2 <文件或目录> --source-root <样例目录> --test-db <可删除目录>")
            sys.exit(1)
        _ingest_v2_path(sys.argv[2], sys.argv[4], sys.argv[6])

    elif command == "query-v2":
        if len(sys.argv) != 5 or sys.argv[3] != "--test-db":
            print("用法: python main.py query-v2 \"精确参数问题\" --test-db <可删除目录>")
            sys.exit(1)
        print(pipeline.render_markdown(pipeline.query_v2(sys.argv[2], test_db=sys.argv[4])))

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

        result = pipeline.query(sys.argv[2])
        markdown = pipeline.render_markdown(result)
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(markdown, encoding="utf-8")
            print(f"已写入 Markdown：{output_path.resolve()}")
        print(markdown)

    elif command == "annotate":
        if len(sys.argv) < 3:
            print("用法: python main.py annotate <source_file>")
            sys.exit(1)
        pipeline.annotate(sys.argv[2])

    elif command == "status":
        pipeline.status()

    else:
        print(f"未知命令: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
