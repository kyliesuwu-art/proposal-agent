# main.py
"""方案知识库 CLI 入口：解析命令行参数，分发到 pipeline.py 里的业务逻辑。

用法：
    uv run python src/main.py ingest <pptx_path>    # 解析 PPTX 并入库
    uv run python src/main.py query "需求描述"       # 检索相关 slide 并生成方案初稿
    uv run python src/main.py annotate <source_file> # 为源文件进行标注/打标签
    uv run python src/main.py status                 # 查看库状态
"""

import sys
from pathlib import Path

# 加载 .env 环境变量（MINERU_TOKEN 等）
from dotenv import load_dotenv
load_dotenv()

# 确保能 import src 下的模块
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import pipeline


def main() -> None:
    """CLI 入口：解析命令，分发到对应函数。"""
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == "ingest":
        if len(sys.argv) < 3:
            print("用法: python main.py ingest <pptx_path>")
            sys.exit(1)
        pipeline.ingest(sys.argv[2])

    elif command == "query":
        if len(sys.argv) < 3:
            print("用法: python main.py query \"需求描述\"")
            sys.exit(1)
        pipeline.query(sys.argv[2])

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