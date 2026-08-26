"""项目运行路径配置：默认固定在仓库根目录，可用环境变量覆盖。"""

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def resolve_project_path(value: str | Path) -> Path:
    """将绝对路径原样保留，将相对路径固定解析为相对项目根目录。"""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _path_from_env(name: str, default: str) -> Path:
    """读取路径环境变量；未设置时使用项目根目录下的默认目录。"""
    return resolve_project_path(os.environ.get(name, default))


CHROMA_PATH = _path_from_env("CHROMA_PATH", "chroma_db")
IMAGES_DIR = _path_from_env("IMAGES_DIR", "images")
DEBUG_ZIPS_DIR = _path_from_env("DEBUG_ZIPS_DIR", "debug_zips")
DEBUG_MINERU_RESULT_DIR = _path_from_env(
    "DEBUG_MINERU_RESULT_DIR", "debug_mineru_result"
)
INGEST_MANIFEST_PATH = _path_from_env("INGEST_MANIFEST_PATH", "ingest_manifest.csv")
