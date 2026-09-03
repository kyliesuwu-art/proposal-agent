"""Dry-run cache directory inspection uses only temporary ZIP fixtures."""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from src.cache_batch import CacheBatchIngestor, _safe_member_path, inspect_cache_directory, select_trial_candidates


def _write_cache(path: Path, *, origin: str = "job_origin.pdf", include_image: bool = True) -> None:
    blocks = [
        {"page_idx": 0, "type": "text", "text_level": 0, "text": "标题"},
        {"page_idx": 0, "type": "text", "text": "正文"},
    ]
    if include_image:
        blocks.append({"page_idx": 0, "type": "image", "img_path": "images/a.png"})
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(origin, b"origin")
        archive.writestr("job_content_list.json", json.dumps(blocks, ensure_ascii=False))
        archive.writestr("full.md", "# title")
        archive.writestr("layout.json", "{}")
        if include_image:
            archive.writestr("images/a.png", b"image")


def test_dry_run_reports_recovery_metadata_duplicates_and_never_creates_target(tmp_path: Path) -> None:
    source = tmp_path / "zips"
    source.mkdir()
    first = source / "same.pdf.zip"
    _write_cache(first)
    (source / "copy.pdf.zip").write_bytes(first.read_bytes())
    report = inspect_cache_directory(source, target_db=tmp_path / "candidate")

    assert report["mode"] == "dry-run"
    assert report["zip_total"] == 2
    assert report["recoverable_total"] == 2
    assert report["file_types"] == {".pdf": 2}
    assert len(report["duplicate_zips"]) == 1
    assert not (tmp_path / "candidate").exists()
    assert report["archives"][0]["page_count"] == 1
    assert report["archives"][0]["source_key"]


def test_dry_run_explains_missing_cache_components_and_image_assets(tmp_path: Path) -> None:
    source = tmp_path / "zips"
    source.mkdir()
    with zipfile.ZipFile(source / "broken.docx.zip", "w") as archive:
        archive.writestr("job_origin.docx", b"origin")
        archive.writestr("job_content_list.json", json.dumps([{"page_idx": 0, "type": "image", "img_path": "missing.png"}]))
    report = inspect_cache_directory(source, target_db=tmp_path / "candidate")

    assert report["recoverable_total"] == 0
    assert report["missing"]["full_markdown"] == 1
    assert report["missing"]["layout"] == 1
    assert report["missing"]["referenced_images"] == 1
    assert "图片" in report["failures"][0]["reason"]


class FakeEmbedding:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, input: list[str]) -> list[list[float]]:
        self.calls.append(input)
        return [[float(len(text)), 1.0, 0.0] for text in input]

    def name(self) -> str:
        return "fake_embedding"


def _run(source: Path, target: Path, fake: FakeEmbedding, *, limit: int = 10, batch_size: int = 32) -> dict:
    ingestor = CacheBatchIngestor(source, target, embedding_function=fake)
    try:
        return ingestor.ingest(limit=limit, batch_size=batch_size)
    finally:
        ingestor.close()


def test_resume_deduplicates_assets_paths_and_embedding_cache(tmp_path: Path) -> None:
    source = tmp_path / "zips"
    source.mkdir()
    _write_cache(source / "one.pdf.zip")
    (source / "one_copy.pdf.zip").write_bytes((source / "one.pdf.zip").read_bytes())
    fake = FakeEmbedding()
    first = _run(source, tmp_path / "candidate", fake, batch_size=1)
    assert [item["status"] for item in first["outcomes"]] == ["completed"]
    assert len(fake.calls) == 1
    assert first["embedding_cache"]["misses"] == 1
    assert (tmp_path / "candidate" / "sidecars").is_dir()
    assert list((tmp_path / "candidate" / "assets").rglob("*.png"))

    second_fake = FakeEmbedding()
    second = _run(source, tmp_path / "candidate", second_fake)
    assert second["outcomes"][0]["status"] == "skipped"
    assert second_fake.calls == []
    state = sqlite3.connect(tmp_path / "candidate" / "cache_ingest_manifest.sqlite3")
    aliases = state.execute("SELECT duplicate_of FROM cache_documents WHERE status='duplicate'").fetchall()
    assert aliases and aliases[0][0]
    payload = json.loads(sqlite3.connect(tmp_path / "candidate" / "hybrid_lexical.sqlite3").execute("SELECT payload FROM pages").fetchone()[0])
    assert payload["images"][0]["path"].startswith("assets/")
    assert not Path(payload["images"][0]["path"]).is_absolute()


def test_failure_does_not_stop_batch_and_can_resume(tmp_path: Path) -> None:
    source = tmp_path / "zips"
    source.mkdir()
    _write_cache(source / "bad.pdf.zip")
    _write_cache(source / "good.docx.zip", origin="job_origin.docx")

    class FailsOnce(FakeEmbedding):
        def __call__(self, input: list[str]) -> list[list[float]]:
            if not self.calls:
                self.calls.append(input)
                raise RuntimeError("intentional embedding failure")
            return super().__call__(input)

    first = _run(source, tmp_path / "candidate", FailsOnce())
    assert {item["status"] for item in first["outcomes"]} == {"failed", "completed"}
    resumed = _run(source, tmp_path / "candidate", FakeEmbedding())
    assert {item["status"] for item in resumed["outcomes"]} == {"completed", "skipped"}


def test_candidate_selection_limit_type_coverage_and_path_escape(tmp_path: Path) -> None:
    source = tmp_path / "zips"
    source.mkdir()
    _write_cache(source / "a.pdf.zip")
    _write_cache(source / "b.docx.zip", origin="job_origin.docx")
    _write_cache(source / "c.pptx.zip", origin="job_origin.pptx")
    inspections = [
        __import__("src.cache_batch", fromlist=["inspect_cache_zip"]).inspect_cache_zip(path, root=source)
        for path in source.glob("*.zip")
    ]
    selected = select_trial_candidates(inspections, limit=3)
    assert {item.origin_extension for item in selected} == {".pdf", ".docx", ".pptx"}
    with pytest.raises(ValueError, match="不安全"):
        _safe_member_path("../escape.png")
    with pytest.raises(ValueError, match="不安全"):
        _safe_member_path("C:/escape.png")
    with pytest.raises(ValueError, match="不安全"):
        CacheBatchIngestor(source, Path(__file__).resolve().parents[1] / "v2_test_db", embedding_function=FakeEmbedding())
