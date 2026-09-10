"""项目运行路径配置：默认固定在仓库根目录，可用环境变量覆盖。"""

import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class DashScopeConfigurationError(RuntimeError):
    """Raised before a remote request when DashScope configuration is incomplete."""


def load_project_environment(*, override: bool = False) -> bool:
    """Load the repository-root .env without depending on the caller's CWD/frame."""
    if not PROJECT_ENV_FILE.is_file():
        return False
    return load_dotenv(dotenv_path=PROJECT_ENV_FILE, override=override)


def dashscope_settings() -> dict[str, str]:
    """Return validated, non-secret DashScope client settings from the unified config entry."""
    load_project_environment()
    api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise DashScopeConfigurationError(
            "缺少 DASHSCOPE_API_KEY：请在项目根目录 .env 或进程环境中配置。"
        )
    base_url = os.environ.get("DASHSCOPE_BASE_URL", DEFAULT_DASHSCOPE_BASE_URL).strip().rstrip("/")
    if not base_url.endswith("/compatible-mode/v1"):
        raise DashScopeConfigurationError(
            "DASHSCOPE_BASE_URL 必须是以 /compatible-mode/v1 结尾的 OpenAI 兼容端点。"
        )
    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": os.environ.get("DASHSCOPE_MODEL", "qwen3.7-plus").strip() or "qwen3.7-plus",
        "timeout_seconds": os.environ.get("DASHSCOPE_TIMEOUT_SECONDS", "300").strip() or "300",
    }


# All import paths (CLI and direct modules alike) use the same explicit root file.
load_project_environment()


def resolve_project_path(value: str | Path) -> Path:
    """将绝对路径原样保留，将相对路径固定解析为相对项目根目录。"""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _path_from_env(name: str, default: str) -> Path:
    """读取路径环境变量；未设置时使用项目根目录下的默认目录。"""
    return resolve_project_path(os.environ.get(name, default))


# The sole formal RAG storage root.  All Chroma, FTS, sidecar and image paths
# are relative to this root; callers may override it only with RAG_DB_PATH.
RAG_DB_PATH = _path_from_env("RAG_DB_PATH", "runtime_data/word_test_db")
IMAGES_DIR = _path_from_env("IMAGES_DIR", "images")
DEBUG_ZIPS_DIR = _path_from_env("DEBUG_ZIPS_DIR", "debug_zips")
DEBUG_MINERU_RESULT_DIR = _path_from_env(
    "DEBUG_MINERU_RESULT_DIR", "debug_mineru_result"
)
INGEST_MANIFEST_PATH = _path_from_env("INGEST_MANIFEST_PATH", "ingest_manifest.csv")
