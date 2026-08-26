"""debug zip 拟入库预览的纯离线测试。"""

import json
import zipfile

from scripts.preview_index_text import render_preview_markdown, write_preview


def _debug_zip(tmp_path, blocks: list[dict], name: str = "sample.pdf.zip"):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("content_list.json", json.dumps(blocks, ensure_ascii=False))
    return path


def test_preview_renders_normal_page_and_writes_report(tmp_path) -> None:
    archive = _debug_zip(tmp_path, [{"page_idx": 0, "type": "text", "text_level": 0, "text": "普通页"}, {"page_idx": 0, "type": "text", "text": "正文内容"}])

    output = write_preview(archive, tmp_path / "preview.md")
    report = output.read_text(encoding="utf-8")

    assert "indexable：`true`" in report
    assert "普通页\n正文内容" in report


def test_preview_marks_toc_page_and_keeps_raw_summary(tmp_path) -> None:
    archive = _debug_zip(tmp_path, [{"page_idx": 0, "type": "text", "text_level": 0, "text": "目录"}, {"page_idx": 0, "type": "text", "text": "第一章……1\n第二章……3\n第三章……8"}])

    report = render_preview_markdown(archive)

    assert "indexable：`false`" in report
    assert "目录页规则：标题或正文包含“目录”" in report
    assert "Raw 摘要" in report
    assert "不入库" in report


def test_preview_renders_table_markdown(tmp_path) -> None:
    archive = _debug_zip(tmp_path, [{"page_idx": 0, "type": "table", "table_caption": "参数", "table_body": "<table><tr><th>项目</th><th>数值</th></tr><tr><td>容量</td><td>100</td></tr></table>"}])

    report = render_preview_markdown(archive)

    assert "### 表格 Markdown" in report
    assert "| 项目 | 数值 |" in report
    assert "| 容量 | 100 |" in report


def test_preview_renders_image_caption_once_in_index_text(tmp_path) -> None:
    archive = _debug_zip(tmp_path, [{"page_idx": 0, "type": "text", "text": "图片页"}, {"page_idx": 0, "type": "image", "img_path": "images/a.png", "image_caption": "MinerU 图片说明"}])

    report = render_preview_markdown(archive)

    assert report.count("[图片] MinerU 图片说明") == 2  # index_text + caption section
    assert "`images/sample.pdf/slide_1_0.png`" in report


def test_preview_skips_unknown_blocks_without_failing(tmp_path) -> None:
    archive = _debug_zip(
        tmp_path,
        [{"page_idx": 0, "type": "unknown_layout", "text": "不支持的布局"}],
    )

    report = render_preview_markdown(archive)

    assert "indexable：`true`" in report
