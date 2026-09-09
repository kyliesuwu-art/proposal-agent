import json
import sqlite3
import zipfile

from src.cache_block_audit import audit_cache, write_audit


def test_read_only_audit_counts_realistic_blocks_and_reprocess_plan(tmp_path) -> None:
    zips = tmp_path / "zips"; zips.mkdir()
    with zipfile.ZipFile(zips / "sample.pdf.zip", "w") as archive:
        archive.writestr("content_list.json", json.dumps([
            {"type": "aside_text", "page_idx": 0, "text": "private text"},
            {"type": "chart", "page_idx": 1, "img_path": "images/a.png", "content": "private"},
            {"type": "equation", "page_idx": 1, "text": "x=1"},
            {"type": "index", "page_idx": 2, "list_items": ["A"]},
            {"type": "unknown_future", "page_idx": 2, "secret": "no report body"},
        ], ensure_ascii=False))
    manifest = tmp_path / "manifest.sqlite3"
    with sqlite3.connect(manifest) as db:
        db.execute("CREATE TABLE cache_documents (status TEXT, page_count INTEGER)")
        db.execute("INSERT INTO cache_documents VALUES ('completed', 3)")
    report = audit_cache(zips, manifest_path=manifest)
    assert report["zip_total"] == report["parsed_zip_total"] == 1
    assert report["block_types"]["chart"]["count"] == 1
    assert report["block_types"]["unknown_future"]["samples"][0]["field_names"] == ["page_idx", "secret", "type"]
    assert "private" not in json.dumps(report, ensure_ascii=False)
    assert report["reprocess_plan"]["affected_document_count"] == 1
    json_path, markdown_path = write_audit(report, tmp_path / "output")
    assert json_path.is_file() and markdown_path.is_file()
