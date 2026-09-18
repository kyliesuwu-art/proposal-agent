from pathlib import Path

import pytest

from src.artifact_assets.safe_resolver import PathSafetyError, resolve_task_image


@pytest.mark.parametrize("reference", ["C:/x.png", "\\\\server\\share\\x.png", "../x.png", "file:///x.png", "https://x/y.png", "NUL.png"])
def test_rejects_unsafe_references(tmp_path: Path, reference: str):
    with pytest.raises(PathSafetyError):
        resolve_task_image(tmp_path, reference)


def test_resolves_safe_backslash_path(tmp_path: Path):
    path = tmp_path / "assets" / "中文 图.png"
    path.parent.mkdir(); path.write_bytes(b"x")
    resolved = resolve_task_image(tmp_path, "assets\\中文 图.png")
    assert resolved.normalized_relative_path == "assets/中文 图.png"
    assert resolved.path == path


def test_rejects_directory_and_symlink_escape(tmp_path: Path):
    (tmp_path / "dir.png").mkdir()
    with pytest.raises(PathSafetyError): resolve_task_image(tmp_path, "dir.png")
    outside = tmp_path.parent / "outside.png"; outside.write_bytes(b"x")
    link = tmp_path / "escape.png"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink unavailable on this Windows runner")
    with pytest.raises(PathSafetyError): resolve_task_image(tmp_path, "escape.png")
