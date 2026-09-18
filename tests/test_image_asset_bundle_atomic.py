from pathlib import Path
import json
import pytest
from PIL import Image
from src.artifact_assets.bundle import BundleError, materialize_asset_bundle
from src.artifact_assets.manifest_builder import build_manifest
from src.artifact_assets.markdown_parser import extract_markdown_images

def setup_bundle(tmp_path: Path):
    Image.new("RGB", (640, 480)).save(tmp_path / "ok.png")
    md=tmp_path / "approved.md"; md.write_text("![ok](ok.png)\n![bad](missing.png)\n",encoding="utf-8")
    manifest=tmp_path / "manifest.json"; manifest.write_text(json.dumps(build_manifest(md,tmp_path,deterministic=True)),encoding="utf-8")
    return md,manifest

def test_permissive_is_self_contained(tmp_path):
    md,m=setup_bundle(tmp_path); r=materialize_asset_bundle(md,tmp_path,m,tmp_path/"out",mode="permissive")
    text=(r.bundle_dir/"approved.md").read_text(encoding="utf-8")
    assert "missing.png" not in text and "MISSING" in text
    assert all((r.bundle_dir/x.reference).is_file() for x in extract_markdown_images(text))

def test_failures_and_existing_target_are_atomic(tmp_path,monkeypatch):
    md,m=setup_bundle(tmp_path)
    with pytest.raises(BundleError): materialize_asset_bundle(md,tmp_path,m,tmp_path/"strict",mode="strict")
    assert not (tmp_path/"strict").exists()
    out=tmp_path/"existing";out.mkdir();(out/"keep").write_text("ok")
    with pytest.raises(BundleError): materialize_asset_bundle(md,tmp_path,m,out,mode="permissive")
    assert (out/"keep").read_text()=="ok"
