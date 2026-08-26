"""通用文档模型和文件级身份的纯离线测试。"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from src import pipeline
from src.adapters import parser as parser_module
from src.adapters.parser import MinerUParser
from src.domain.identity import build_document_identity, compute_content_hash, normalize_source_key
from src.domain.models import Page, ParseResult, SourceDocument
from src.query_result import QueryResult


def test_file_level_sha256_and_version_change_only_when_content_changes(tmp_path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    source = root / "方案.pdf"
    source.write_bytes(b"version one")

    first = build_document_identity(source, source_root=root)
    second = build_document_identity(source, source_root=root)
    assert first.content_hash == second.content_hash == compute_content_hash(source)
    assert first.version_id == second.version_id

    source.write_bytes(b"version two")
    changed = build_document_identity(source, source_root=root)
    assert changed.document_id == first.document_id
    assert changed.content_hash != first.content_hash
    assert changed.version_id != first.version_id


def test_source_key_distinguishes_same_filename_in_different_directories(tmp_path) -> None:
    root = tmp_path / "library"
    first_path = root / "项目A" / "方案.pdf"
    second_path = root / "项目B" / "方案.pdf"
    first_path.parent.mkdir(parents=True)
    second_path.parent.mkdir(parents=True)
    first_path.write_bytes(b"same")
    second_path.write_bytes(b"same")

    first = build_document_identity(first_path, source_root=root)
    second = build_document_identity(second_path, source_root=root)
    assert first.source_key == "项目A/方案.pdf"
    assert second.source_key == "项目B/方案.pdf"
    assert first.document_id != second.document_id
    assert str(root) not in first.document_id


def test_source_key_normalizes_windows_and_unix_relative_paths_without_absolute_path() -> None:
    assert normalize_source_key(r"项目A\方案.pdf") == "项目A/方案.pdf"
    assert normalize_source_key("项目A/方案.pdf") == "项目A/方案.pdf"
    with pytest.raises(ValueError, match="source_root"):
        normalize_source_key(r"C:\Users\developer\方案.pdf")


@pytest.mark.parametrize("media_type", ["pdf", "pptx", "docx", "doc"])
def test_generic_models_can_represent_all_supported_document_pages(media_type: str) -> None:
    document = SourceDocument(source_key=f"资料/样本.{media_type}", filename=f"样本.{media_type}", media_type=media_type)
    result = ParseResult(document=document, pages=[Page(page_number=1, title="标题", content="正文")])

    assert result.document.media_type == media_type
    assert result.pages[0].page_number == 1


def test_parse_document_returns_generic_result_while_parse_pptx_keeps_legacy_pages(monkeypatch, tmp_path) -> None:
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"placeholder")
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("content_list.json", json.dumps([
            {"page_idx": 0, "type": "text", "text_level": 0, "text": "标题"},
            {"page_idx": 0, "type": "text", "text": "正文"},
        ], ensure_ascii=False))

    class FakeClient:
        def submit(self, *_args, **_kwargs) -> str:
            return "local-test"

    parser = MinerUParser.__new__(MinerUParser)
    parser._client = FakeClient()
    monkeypatch.setattr(parser_module, "_IMAGES_DIR", tmp_path / "images")
    monkeypatch.setattr(parser, "_poll_until_done", lambda *_args: data.getvalue())

    result = parser.parse_document(source)
    assert isinstance(result, ParseResult)
    assert result.pages[0].page_number == 1
    assert list(result)[0]["slide_number"] == 1

    monkeypatch.setattr(parser, "parse_document", lambda _path: result)
    assert parser.parse_pptx(tmp_path / "compat.pptx") == result.to_legacy_pages()


def test_existing_pipeline_and_query_models_remain_importable_without_services() -> None:
    assert callable(pipeline.ingest)
    assert callable(pipeline.query)
    assert QueryResult.__name__ == "QueryResult"
