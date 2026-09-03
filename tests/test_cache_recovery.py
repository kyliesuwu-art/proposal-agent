"""Offline cache recovery tests; fixtures contain no customer source document."""

from __future__ import annotations

import json
import zipfile

import pytest

from src.cache_recovery import recover_debug_zip
from src.hybrid_v2 import LocalHashEmbeddingFunction


def _cache(path, *, origin: str = "job_origin.pdf") -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(origin, b"cached-origin-placeholder")
        archive.writestr("job_content_list.json", json.dumps([
            {"page_idx": 0, "type": "text", "text_level": 0, "text": "PCS 参数"},
            {"page_idx": 0, "type": "text", "text": "接入 35kV，容量 100MW"},
        ], ensure_ascii=False))


def test_recovery_uses_matching_zip_filename_as_documented_cache_source_key(tmp_path) -> None:
    cache = tmp_path / "项目A.pdf.zip"
    _cache(cache)

    recovered = recover_debug_zip(cache)

    assert recovered.origin_extension == ".pdf"
    assert recovered.identity.source_key == "cache/项目A.pdf"
    assert recovered.source_key_origin == "zip_filename_matched_origin_extension"
    assert recovered.pages[0]["content"] == "接入 35kV，容量 100MW"
    assert recovered.pages[0]["cache_source_key_origin"] == recovered.source_key_origin


def test_recovery_falls_back_without_claiming_unknown_original_name(tmp_path) -> None:
    cache = tmp_path / "opaque-cache.zip"
    _cache(cache, origin="job_origin.docx")

    recovered = recover_debug_zip(cache)

    assert recovered.identity.source_key == "cache/opaque-cache.docx"
    assert recovered.source_key_origin == "safe_cache_filename_fallback"


def test_recovery_rejects_cache_without_required_metadata(tmp_path) -> None:
    cache = tmp_path / "missing.pdf.zip"
    with zipfile.ZipFile(cache, "w") as archive:
        archive.writestr("full.md", "no blocks")
    with pytest.raises(ValueError, match="content_list"):
        recover_debug_zip(cache)


def test_local_embedding_is_deterministic_and_nonempty_for_technical_query() -> None:
    embedding = LocalHashEmbeddingFunction()
    first = embedding(["PCS 35kV 100MW"])[0].tolist()
    assert first == embedding(["PCS 35kV 100MW"])[0].tolist()
    assert any(first)
