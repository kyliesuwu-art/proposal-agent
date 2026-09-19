import subprocess
import sys
from pathlib import Path

from PIL import Image


SCRIPT = Path(__file__).parents[1] / "scripts" / "build_image_asset_manifest.py"


def test_cli_non_strict_warning_is_success_and_strict_is_failure(tmp_path: Path):
    Image.new("RGB", (10, 10)).save(tmp_path / "tiny.png")
    markdown = tmp_path / "approved.md"; markdown.write_text("![](tiny.png)", encoding="utf-8")
    command = [sys.executable, str(SCRIPT), "--markdown", str(markdown), "--task-root", str(tmp_path), "--output-dir", str(tmp_path / "report"), "--deterministic"]
    assert subprocess.run(command, cwd=SCRIPT.parents[1], capture_output=True, text=True).returncode == 0
    assert (tmp_path / "report" / "acceptance_summary.json").is_file()
    assert subprocess.run([*command, "--strict"], cwd=SCRIPT.parents[1], capture_output=True, text=True).returncode == 1
