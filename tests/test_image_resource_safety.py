from pathlib import Path

import pytest
from PIL import Image

from src.artifact_assets.image_inspector import inspect_image
from src.artifact_assets.models import AssetValidationProfile


def static(path: Path, size=(4, 3), fmt="PNG"):
    Image.new("RGB", size, "navy").save(path, format=fmt)


def animated(path: Path, frames=2, size=(4, 3), fmt="GIF"):
    images = [Image.new("RGB", size, (i, 0, 0)) for i in range(frames)]
    images[0].save(path, format=fmt, save_all=True, append_images=images[1:], loop=0)


def test_static_boundaries_and_dimensions(tmp_path: Path):
    path = tmp_path / "x.png"; static(path, (4, 3))
    assert "error" not in inspect_image(path, AssetValidationProfile(max_width_px=4, max_height_px=3, max_pixels=12))
    assert inspect_image(path, AssetValidationProfile(max_width_px=3))["error"] == "IMAGE_DIMENSION_EXCEEDED"
    assert inspect_image(path, AssetValidationProfile(max_height_px=2))["error"] == "IMAGE_DIMENSION_EXCEEDED"
    assert inspect_image(path, AssetValidationProfile(max_pixels=11))["error"] == "IMAGE_FRAME_PIXELS_EXCEEDED"


def test_gif_frame_count_and_cumulative_limits(tmp_path: Path):
    path = tmp_path / "animated.gif"; animated(path, frames=3, size=(4, 3))
    good = inspect_image(path, AssetValidationProfile(max_frames=3, max_pixels=12, max_cumulative_frame_pixels=36))
    assert good["frame_count"] == 3 and good["cumulative_frame_pixels"] == 36
    assert inspect_image(path, AssetValidationProfile(max_frames=2))["error"] == "IMAGE_FRAME_COUNT_EXCEEDED"
    assert inspect_image(path, AssetValidationProfile(max_cumulative_frame_pixels=35))["error"] == "IMAGE_CUMULATIVE_PIXELS_EXCEEDED"


def test_multipage_tiff_is_checked_as_frames(tmp_path: Path):
    path = tmp_path / "multi.tiff"; animated(path, frames=2, size=(3, 3), fmt="TIFF")
    assert inspect_image(path, AssetValidationProfile(max_frames=1))["error"] == "IMAGE_FRAME_COUNT_EXCEEDED"
