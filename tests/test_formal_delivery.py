import json
import zipfile
from pathlib import Path

from PIL import Image
from docx import Document

from src.delivery_text import forbidden_delivery_tokens, formal_markdown
from src.render_pptx import RenderPptxError, _validate, render_pptx
from src.render_word import render_word


def _markdown(tmp_path: Path) -> Path:
    assets = tmp_path / "assets"; assets.mkdir()
    Image.new("RGB", (240, 120), "navy").save(assets / "used.png")
    Image.new("RGB", (240, 120), "teal").save(assets / "rejected.png")
    path = tmp_path / "proposal.md"
    path.write_text("""# 方案\n\n## 建设方案\n\n【设计建议】采用分阶段实施路径。[来源: 资料.pdf, 第 2 页]\n\n![有效图](assets/used.png)\n\n![拒绝图](assets/rejected.png)\n\n### 设备范围\n\n容量【待确认】\n\n---\n\n## 来源与依据\n\n- 资料.pdf：第 2 页\n""", encoding="utf-8")
    return path


def test_formal_markdown_preserves_image_blocks_and_consolidates_unknowns():
    text, inputs = formal_markdown("【设计建议】建议采用分期实施。\n容量【待确认】\n![图](assets/a.png)\n")
    assert "建议建议" not in text
    assert "![图](assets/a.png)" in text
    assert "深化设计输入条件" in text
    assert inputs == ["容量"]
    assert "将在" not in "\n".join(inputs)


def test_rejected_word_image_is_consumed_not_written_as_markdown(tmp_path):
    markdown = _markdown(tmp_path)
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({"images": {"assets/used.png": {"use_in_word": True}, "assets/rejected.png": {"use_in_word": False, "reason": "decorative_fragment"}}}), encoding="utf-8")
    output = tmp_path / "final.docx"
    render_word(markdown, output, formal=True, image_decisions_path=decisions)
    document = Document(output)
    visible = "\n".join(p.text for p in document.paragraphs)
    assert not forbidden_delivery_tokens(visible)
    assert len(document.inline_shapes) == 1
    with zipfile.ZipFile(output) as archive:
        assert len([name for name in archive.namelist() if name.startswith("word/media/")]) == 1


def test_formal_ppt_rejects_plan_outside_delivery_page_range(tmp_path):
    markdown = _markdown(tmp_path)
    plan = {"formal_delivery": True, "mode": "briefing", "requested_max_slides": 18, "max_slides": 18,
            "sources": {}, "figures": {}, "slides": [{"slide_id": "slide-001", "layout": "title", "title": "方案", "bullets": [], "figure_ids": [], "source_ids": []}]}
    pptx = tmp_path / "x.pptx"
    # render through a deliberately invalid plan is covered by the validator's
    # public error contract rather than silently accepting a too-short deck.
    from src.render_pptx import _draw
    _draw(plan, markdown, pptx)
    assert "15 to 25" in _validate(pptx, plan, markdown)["errors"][0]

