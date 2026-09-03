"""离线路径配置测试：不得连接现有 Chroma 或调用外部服务。"""

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PATH_ENV_NAMES = (
    "RAG_DB_PATH",
    "IMAGES_DIR",
    "DEBUG_ZIPS_DIR",
    "DEBUG_MINERU_RESULT_DIR",
    "INGEST_MANIFEST_PATH",
)


def _config_paths_from(cwd: Path) -> dict[str, str]:
    """在指定 cwd 的独立 Python 进程中读取配置，不导入 Chroma。"""
    script = (
        "import json, sys; "
        f"sys.path.insert(0, {str(PROJECT_ROOT)!r}); "
        "from src.config import RAG_DB_PATH, IMAGES_DIR, DEBUG_ZIPS_DIR, "
        "DEBUG_MINERU_RESULT_DIR, INGEST_MANIFEST_PATH; "
        "print(json.dumps({\"rag_db\": str(RAG_DB_PATH), \"images\": str(IMAGES_DIR), "
        "\"debug_zips\": str(DEBUG_ZIPS_DIR), \"debug_mineru\": str(DEBUG_MINERU_RESULT_DIR), "
        "\"manifest\": str(INGEST_MANIFEST_PATH)}))"
    )
    env = os.environ.copy()
    for name in PATH_ENV_NAMES:
        env.pop(name, None)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_default_paths_are_identical_from_root_and_src_cwd() -> None:
    """默认路径始终锚定项目根目录，而不是调用进程 cwd。"""
    from_root = _config_paths_from(PROJECT_ROOT)
    from_src = _config_paths_from(PROJECT_ROOT / "src")

    assert from_root == from_src
    assert from_root == {
        "rag_db": str(PROJECT_ROOT / "runtime_data" / "word_test_db"),
        "images": str(PROJECT_ROOT / "images"),
        "debug_zips": str(PROJECT_ROOT / "debug_zips"),
        "debug_mineru": str(PROJECT_ROOT / "debug_mineru_result"),
        "manifest": str(PROJECT_ROOT / "ingest_manifest.csv"),
    }


def test_path_environment_variables_override_defaults_from_pytest_tmpdir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """环境变量可覆盖路径，且相对覆盖仍固定相对项目根目录。"""
    import src.config as config

    monkeypatch.setenv("RAG_DB_PATH", str(tmp_path / "isolated-rag"))
    monkeypatch.setenv("IMAGES_DIR", "test-runtime/images")
    monkeypatch.setenv("DEBUG_ZIPS_DIR", str(tmp_path / "zips"))
    monkeypatch.setenv("DEBUG_MINERU_RESULT_DIR", "test-runtime/mineru")
    monkeypatch.setenv("INGEST_MANIFEST_PATH", str(tmp_path / "manifest.csv"))
    config = importlib.reload(config)

    assert config.RAG_DB_PATH == (tmp_path / "isolated-rag").resolve()
    assert config.IMAGES_DIR == (PROJECT_ROOT / "test-runtime/images").resolve()
    assert config.DEBUG_ZIPS_DIR == (tmp_path / "zips").resolve()
    assert config.DEBUG_MINERU_RESULT_DIR == (PROJECT_ROOT / "test-runtime/mineru").resolve()
    assert config.INGEST_MANIFEST_PATH == (tmp_path / "manifest.csv").resolve()

    for name in PATH_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    importlib.reload(config)
