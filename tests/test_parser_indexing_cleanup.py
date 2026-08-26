"""Parser 索引清洗回归测试：只使用内存 zip、临时目录和 fake collection。"""

import io
import json
import zipfile

import pytest

from src import pipeline
from src.adapters import parser as parser_module
from src.adapters.parser import MinerUParser
from src.adapters.vector_store import VectorStore


def _render_page(blocks: list[dict]) -> tuple[str, str, list[dict]]:
    """调用页面纯渲染函数，不创建 MinerU 客户端。"""
    with zipfile.ZipFile(io.BytesIO(), "w") as zf:
        return MinerUParser._render_page(blocks, zf, "sample.pdf", 1)


def _page_from_blocks(blocks: list[dict], monkeypatch: pytest.MonkeyPatch, tmp_path) -> dict:
    """通过内存 MinerU 结果 zip 构造页面，图片目录固定到 pytest 临时目录。"""
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as zf:
        zf.writestr("content_list.json", json.dumps(blocks, ensure_ascii=False))
        zf.writestr("images/page.png", b"fake-image")
    monkeypatch.setattr(parser_module, "_IMAGES_DIR", tmp_path / "images")
    return MinerUParser._split_into_pages(data.getvalue(), "sample.pdf")[0]


@pytest.mark.parametrize(
    "body",
    [
        "<table><tr><th>项目</th><th>数值</th></tr><tr><td>容量</td><td>100</td></tr></table>",
        list("<table><tr><th>项目</th><th>数值</th></tr><tr><td>容量</td><td>100</td></tr></table>"),
    ],
)
def test_table_body_string_or_character_list_becomes_markdown(body) -> None:
    _, content, _ = _render_page([{"type": "table", "table_caption": "参数", "table_body": body}])

    assert "[表格] 参数" in content
    assert "| 项目 | 数值 |" in content
    assert "| 容量 | 100 |" in content


def test_table_rowspan_and_colspan_text_are_not_lost() -> None:
    body = """
    <table>
      <tr><th>区域</th><th>数值</th></tr>
      <tr><td rowspan='2'>华东</td><td>A</td></tr>
      <tr><td>B</td></tr>
      <tr><td colspan='2'>合计说明</td></tr>
    </table>
    """
    _, content, _ = _render_page([{"type": "table", "table_body": body}])

    assert "| 华东 | A |" in content
    assert "| 华东 | B |" in content
    assert "合计说明" in content


def test_table_entities_newlines_and_pipes_are_normalized() -> None:
    body = "<table><tr><td>A&amp;B\nC | D</td><td>&nbsp;值</td></tr></table>"
    _, content, _ = _render_page([{"type": "table", "table_body": body}])

    assert "A&B C \\| D" in content
    assert "值" in content


def test_empty_and_two_column_key_value_tables() -> None:
    _, empty_content, _ = _render_page([{"type": "table", "table_body": "<table></table>"}])
    _, key_value_content, _ = _render_page(
        [{"type": "table", "table_body": "<table><tr><td>电压</td><td>400V</td></tr><tr><td>频率</td><td>50Hz</td></tr></table>"}]
    )

    assert "|" not in empty_content
    assert "| 电压 | 400V |" in key_value_content
    assert "| 频率 | 50Hz |" in key_value_content


def test_malformed_table_falls_back_to_plain_text(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenTableParser:
        def feed(self, _: str) -> None:
            raise ValueError("malformed")

        def get_grid(self) -> list[list[str]]:
            return []

    monkeypatch.setattr(parser_module, "_TableTextParser", BrokenTableParser)
    _, content, _ = _render_page(
        [{"type": "table", "table_body": "<table><tr><td>保留<b>文本</b></td></tr>"}]
    )

    assert "保留 文本" in content


def test_page_keeps_text_tables_and_image_fallback_in_source_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    blocks = [
        {"type": "text", "text": "正文"},
        {"type": "table", "table_caption": "表一", "table_body": "<table><tr><td>A</td></tr></table>"},
        {"type": "image", "img_path": "images/page.png", "image_caption": "原始图片说明"},
        {"type": "table", "table_caption": "表二", "table_body": "<table><tr><td>B</td></tr></table>"},
    ]
    page = _page_from_blocks(blocks, monkeypatch, tmp_path)

    assert page["content"].index("正文") < page["content"].index("[表格] 表一")
    assert page["content"].index("[表格] 表一") < page["content"].index("[表格] 表二")
    assert page["images"] == [{"path": str(tmp_path / "images" / "sample.pdf" / "slide_1_0.png"), "caption": "原始图片说明"}]
    assert page["raw_blocks"] == blocks


def test_toc_page_is_marked_not_indexable_and_parameter_table_is_not(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    toc_blocks = [
        {"type": "text", "text_level": 0, "text": "目录"},
        {"type": "text", "text": "第一章……1\n第二章……3\n第三章……8"},
    ]
    parameter_blocks = [
        {"type": "text", "text_level": 0, "text": "设备参数"},
        {"type": "table", "table_body": "<table><tr><td>数量</td><td>100</td></tr><tr><td>单价</td><td>200</td></tr></table>"},
    ]

    toc_page = _page_from_blocks(toc_blocks, monkeypatch, tmp_path)
    parameter_page = _page_from_blocks(parameter_blocks, monkeypatch, tmp_path)

    assert toc_page["indexable"] is False
    assert toc_page["slide_number"] == 1
    assert parameter_page["indexable"] is True


def test_multiple_toc_anchors_are_a_toc_page() -> None:
    blocks = [{"type": "text", "text": '<a id="_Toc1"></a><a id="_Toc2"></a>'}]
    assert MinerUParser._is_toc_page(blocks, "普通标题", "普通正文") is True


class _FakeCollection:
    def __init__(self) -> None:
        self.add_calls: list[dict] = []
        self.update_calls: list[dict] = []

    def add(self, **kwargs) -> None:
        self.add_calls.append(kwargs)

    def update(self, **kwargs) -> None:
        self.update_calls.append(kwargs)


def test_non_indexable_pages_do_not_reach_vector_add() -> None:
    store = VectorStore.__new__(VectorStore)
    store._collection = _FakeCollection()
    pages = [{"slide_number": 1, "title": "目录", "content": "", "source_file": "a.pdf", "images": [], "indexable": False}]

    assert store.add_slides(pages, file_hash="hash") == 0
    assert store._collection.add_calls == []


@pytest.mark.parametrize(
    ("generated_caption", "expected_caption"),
    [("视觉说明", "视觉说明"), ("", "MinerU 说明")],
)
def test_image_caption_uses_vision_result_or_mineru_fallback_once(
    monkeypatch: pytest.MonkeyPatch, generated_caption: str, expected_caption: str
) -> None:
    class FakeLLM:
        def generate_slide_context(self, *_args) -> dict:
            return {}

        def generate_image_captions(self, _slide) -> list[str]:
            return [generated_caption]

    class FakeStore:
        def __init__(self) -> None:
            self.updated_pages: list[dict] = []
            self.updated_contexts: list[dict] = []

        def update_slide_contexts(self, pages, contexts) -> None:
            self.updated_pages = pages
            self.updated_contexts = contexts

    monkeypatch.setattr(pipeline, "LLMClient", FakeLLM)
    store = FakeStore()
    page = {
        "slide_number": 1,
        "title": "图片页",
        "content": "正文",
        "source_file": "sample.pdf",
        "images": [{"path": "unused.png", "caption": "MinerU 说明"}],
    }

    pipeline._annotate_slides(store, [page])

    assert page["images"][0]["caption"] == expected_caption
    assert store.updated_pages == [page]

    vector_store = VectorStore.__new__(VectorStore)
    vector_store._collection = _FakeCollection()
    vector_store.update_slide_contexts([page], [{}])
    document = vector_store._collection.update_calls[0]["documents"][0]
    assert document.count("[图片]") == 1
    assert expected_caption in document


def test_image_caption_keeps_mineru_fallback_when_vision_call_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingLLM:
        def generate_slide_context(self, *_args) -> dict:
            return {}

        def generate_image_captions(self, _slide) -> list[str]:
            raise RuntimeError("vision unavailable")

    class FakeStore:
        def __init__(self) -> None:
            self.updated_pages: list[dict] = []

        def update_slide_contexts(self, pages, _contexts) -> None:
            self.updated_pages = pages

    monkeypatch.setattr(pipeline, "LLMClient", FailingLLM)
    store = FakeStore()
    page = {
        "slide_number": 1,
        "title": "图片页",
        "content": "正文",
        "source_file": "sample.pdf",
        "images": [{"path": "unused.png", "caption": "MinerU 说明"}],
    }

    pipeline._annotate_slides(store, [page])

    assert store.updated_pages == [page]
    assert page["images"][0]["caption"] == "MinerU 说明"
