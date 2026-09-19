import json
from pathlib import Path

from PIL import Image

from src.artifact_assets.manifest_builder import build_manifest


def _image(path: Path, size=(640, 480), fmt="PNG"):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "navy").save(path, format=fmt)


def test_manifest_tracks_valid_missing_duplicates_and_repeat_references(tmp_path: Path):
    _image(tmp_path / "assets" / "one.png")
    _image(tmp_path / "assets" / "copy.png")
    markdown = tmp_path / "approved.md"
    markdown.write_text("![one](assets/one.png)\n![again](assets/one.png)\n![copy](assets/copy.png)\n![missing](none.png)\n", encoding="utf-8")
    manifest = build_manifest(markdown, tmp_path, include_unreferenced=False, deterministic=True)
    assert manifest["summary"]["total_references"] == 4
    assert manifest["summary"]["unique_assets"] == 3
    assert next(item for item in manifest["assets"] if item["normalized_relative_path"] == "assets/one.png")["reference_count"] == 2
    assert manifest["summary"]["missing_assets"] == 1
    assert manifest["summary"]["duplicate_assets"] == 1
    assert json.dumps(manifest, ensure_ascii=False) == json.dumps(build_manifest(markdown, tmp_path, deterministic=True), ensure_ascii=False)


def test_manifest_inspects_empty_corrupt_mismatch_low_res_and_unreferenced(tmp_path: Path):
    _image(tmp_path / "tiny.png", (20, 20))
    _image(tmp_path / "wrong.jpg", fmt="PNG")
    (tmp_path / "empty.png").write_bytes(b"")
    (tmp_path / "bad.png").write_bytes(b"not-image")
    _image(tmp_path / "unused.png")
    markdown = tmp_path / "approved.md"
    markdown.write_text("\n".join(f"![]({name})" for name in ("tiny.png", "wrong.jpg", "empty.png", "bad.png")), encoding="utf-8")
    manifest = build_manifest(markdown, tmp_path, include_unreferenced=True, deterministic=True)
    statuses = {item["normalized_relative_path"]: item["status"] for item in manifest["assets"]}
    assert statuses["tiny.png"] == "LOW_RESOLUTION"
    assert statuses["wrong.jpg"] == "EXTENSION_MISMATCH"
    assert statuses["empty.png"] == "EMPTY_FILE"
    assert statuses["bad.png"] == "CORRUPT"
    assert manifest["summary"]["unreferenced_assets"] == 1
    assert all(str(tmp_path) not in json.dumps(item, ensure_ascii=False) for item in manifest["assets"])


def test_manifest_rejects_unsafe_and_unsupported_and_preserves_markdown_digest(tmp_path: Path):
    (tmp_path / "vector.svg").write_text("<svg/>", encoding="utf-8")
    markdown = tmp_path / "approved.md"
    markdown.write_text("![](C:/private.png)\n![](vector.svg)\n", encoding="utf-8")
    before = markdown.read_bytes()
    manifest = build_manifest(markdown, tmp_path, deterministic=True)
    assert markdown.read_bytes() == before
    assert manifest["source_markdown_sha256"] == __import__("hashlib").sha256(before).hexdigest()
    assert {item["status"] for item in manifest["assets"]} == {"UNSAFE_PATH", "UNSUPPORTED_FORMAT"}
