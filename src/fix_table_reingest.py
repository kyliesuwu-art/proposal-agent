"""一次性脚本：清空已入库的指定文件记录，为重新 ingest 做准备。

修复表格解析 bug 后，已入库的旧文件需要重新解析才能让修复生效。
因为哈希值不会变（文件本身没改），必须先删掉 Chroma 中的旧记录，
再重新 ingest 才能触发完整的重新解析流程。

用法：
    uv run python src/fix_table_reingest.py
"""

import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.adapters.vector_store import VectorStore

# 需要重新 ingest 的文件（根据 test.py 输出）
FILES_TO_CLEAR = [
    "大型风电场站数字化运维方案.pptx",
    "康晋电气工商业光储充一体化解决方案.pptx",
    "康晋电气源网荷储一体化项目方案.pptx",
]


def main() -> None:
    store = VectorStore()
    total_deleted = 0

    for fname in FILES_TO_CLEAR:
        deleted = store.delete_source(fname)
        print(f"  已删除 [{fname}]: {deleted} 个 slide")
        total_deleted += deleted

    print(f"\n共删除 {total_deleted} 个 slide")
    print(f"库中剩余: {store.count()} 个 slide")
    print("\n接下来对每个文件运行 ingest 即可：")
    for fname in FILES_TO_CLEAR:
        print(f'  uv run python src/main.py ingest "files/{fname}"')


if __name__ == "__main__":
    main()
