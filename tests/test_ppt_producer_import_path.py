from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRODUCER = (PROJECT_ROOT / "scripts" / "run_ppt_v4_ark_full20.py").resolve()


@pytest.mark.parametrize("cwd_name", ["repository", "outside", "artifact-job"])
def test_v4_producer_absolute_script_imports_src_from_any_cwd(tmp_path: Path, cwd_name: str) -> None:
    cwd = PROJECT_ROOT if cwd_name == "repository" else tmp_path / cwd_name
    cwd.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, str(PRODUCER), "--help"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "ModuleNotFoundError" not in completed.stderr
    assert "--produce" in completed.stdout


def test_v4_producer_imports_src_from_its_own_worktree(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONIOENCODING"] = "utf-8"
    probe = (
        "import importlib.util,sys;"
        f"p={str(PRODUCER)!r};"
        "s=importlib.util.spec_from_file_location('producer_probe',p);"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
        "print(sys.modules['src'].__file__)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], cwd=tmp_path, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert Path(completed.stdout.strip()).resolve().is_relative_to(PROJECT_ROOT / "src")

def test_v4_producer_bootstraps_its_own_integration_root() -> None:
    source = PRODUCER.read_text(encoding="utf-8")
    bootstrap = "PROJECT_ROOT = Path(__file__).resolve().parents[1]"
    src_import = "from src.artifact_assets.bundle import build_validated_asset_set, load_manifest"
    assert bootstrap in source
    assert source.index(bootstrap) < source.index(src_import)