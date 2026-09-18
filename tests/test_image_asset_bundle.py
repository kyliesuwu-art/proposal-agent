import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from src.artifact_assets.bundle import BundleError, build_validated_asset_set, load_manifest, materialize_asset_bundle
from src.artifact_assets.manifest_builder import build_manifest
from src.artifact_assets.markdown_parser import extract_markdown_images


def image(path: Path, size=(640, 480), fmt="PNG"):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "navy").save(path, format=fmt)


def prepared(tmp_path: Path, text: str):
    image(tmp_path / "输入 图.png")
    image(tmp_path / "copy.png")
    md = tmp_path / "approved.md"; md.write_text(text, encoding="utf-8")
    manifest = build_manifest(md, tmp_path, deterministic=True)
    manifest_path = tmp_path / "assets_manifest.json"; manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return md, manifest_path


def test_bundle_rewrites_only_real_images_and_deduplicates(tmp_path: Path):
    md, manifest_path = prepared(tmp_path, '![甲](输入 图.png "title")\n![乙](copy.png)\n[link](输入 图.png)\n```md\n![fake](输入 图.png)\n```\n')
    (tmp_path / "copy.png").write_bytes((tmp_path / "输入 图.png").read_bytes())
    manifest_path.write_text(json.dumps(build_manifest(md, tmp_path, deterministic=True)), encoding="utf-8")
    original = hashlib.sha256(md.read_bytes()).hexdigest()
    result = materialize_asset_bundle(md, tmp_path, manifest_path, tmp_path / "out", mode="strict", create_zip=True)
    bundled = result.bundle_dir / "approved.md"
    assert hashlib.sha256(md.read_bytes()).hexdigest() == original
    text = bundled.read_text(encoding="utf-8")
    assert '![甲](assets/' in text and '"title")' in text
    assert "[link](输入 图.png)" in text and "![fake](输入 图.png)" in text
    refs = extract_markdown_images(text)
    assert all((result.bundle_dir / item.reference).is_file() for item in refs)
    assert len(list((result.bundle_dir / "assets").iterdir())) == 1
    assert result.zip_path and result.zip_path.is_file()


def test_manifest_tampering_missing_and_incompatible_schema_fail_strict_atomically(tmp_path: Path):
    md, manifest_path = prepared(tmp_path, "![](输入 图.png)\n![](missing.png)\n")
    with pytest.raises(BundleError): materialize_asset_bundle(md, tmp_path, manifest_path, tmp_path / "out", mode="strict")
    assert not (tmp_path / "out" / "bundle").exists()
    bad = json.loads(manifest_path.read_text()); bad["schema_version"] = "nope"; manifest_path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(BundleError, match="SCHEMA"): load_manifest(manifest_path)


def test_changed_source_is_rejected_and_permissive_keeps_unresolved_reference(tmp_path: Path):
    md, manifest_path = prepared(tmp_path, "![](输入 图.png)\n")
    image(tmp_path / "输入 图.png", fmt="JPEG")
    with pytest.raises(BundleError, match="ASSET_CHANGED_SINCE_MANIFEST"):
        materialize_asset_bundle(md, tmp_path, manifest_path, tmp_path / "strict", mode="strict")
    result = materialize_asset_bundle(md, tmp_path, manifest_path, tmp_path / "permissive", mode="permissive")
    assert result.overall_status == "PASS_WITH_WARNINGS"
    assert "输入 图.png" in (result.bundle_dir / "approved.md").read_text(encoding="utf-8")


def test_deterministic_zip_and_bundle_manifest_has_no_absolute_path(tmp_path: Path):
    md, manifest_path = prepared(tmp_path, "![](输入 图.png)\n")
    one = materialize_asset_bundle(md, tmp_path, manifest_path, tmp_path / "one", mode="strict", create_zip=True)
    two = materialize_asset_bundle(md, tmp_path, manifest_path, tmp_path / "two", mode="strict", create_zip=True)
    assert hashlib.sha256(one.zip_path.read_bytes()).digest() == hashlib.sha256(two.zip_path.read_bytes()).digest()
    assert str(tmp_path) not in (one.bundle_dir / "bundle_manifest.json").read_text(encoding="utf-8")


def test_asset_set_exposes_reference_and_asset_resolution(tmp_path: Path):
    md, manifest_path = prepared(tmp_path, "![](输入 图.png)\n")
    assets = build_validated_asset_set(load_manifest(manifest_path), tmp_path)
    asset = assets.resolve_asset_by_reference("输入 图.png")
    assert asset and assets.resolve_asset_by_id(asset["asset_id"]) == asset
