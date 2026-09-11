from pathlib import Path

import pytest

from src.markdown_images import image_blocks, image_syntax_errors, normalise_caption, normalise_delivered_image_blocks, normalise_internal_image_markers
from src.render_pptx import RenderPptxError, build_slide_plan
from src.render_word import render_word


def test_internal_wrappers_and_inline_marker_become_standalone_blocks():
    text = "前文 ![说明][IMG1] 后文\n![说明] [IMG2]\n正文[IMG3]尾文"
    value = normalise_internal_image_markers(text, {"IMG1", "IMG2", "IMG3"})
    assert "![说明]" not in value
    assert value.count("[IMG") == 3
    assert "前文" in value and "后文" in value and "正文" in value and "尾文" in value


def test_caption_is_one_line_literal_and_code_fences_are_ignored():
    assert normalise_caption("a\n![bad](x.png) [b]") == "a b"
    text = "```\n![literal](assets/x.png)\n```\n\n![ok](assets/y.png)\n"
    assert [item.asset_path for item in image_blocks(text)] == ["assets/y.png"]
    assert not image_syntax_errors(text)


def test_orphan_is_not_silently_removed_but_known_delivery_run_is_repaired():
    broken = "![a]\n![b]\n![b]![b](assets/image-003.jpg)\n"
    repaired = normalise_delivered_image_blocks(broken)
    assert repaired == "![b](assets/image-003.jpg)\n"
    assert image_syntax_errors("正文 ![a]\n") == ["第 1 行：存在没有路径的孤立图片图注"]


def test_ppt_sidecar_rejects_inline_image(tmp_path: Path):
    (tmp_path / "assets").mkdir(); (tmp_path / "assets" / "x.png").write_bytes(b"x")
    proposal = tmp_path / "proposal.md"; proposal.write_text("# T\n\n## A\n正文 ![x](assets/x.png)\n", encoding="utf-8")
    (tmp_path / "proposal.sources.json").write_text('{"sources": []}', encoding="utf-8")
    with pytest.raises(RenderPptxError, match="第 4 行"):
        build_slide_plan(proposal)


def test_word_embeds_every_shared_contract_image_block(tmp_path: Path):
    from PIL import Image
    assets = tmp_path / "assets"; assets.mkdir()
    paths = []
    for number in range(1, 6):
        path = assets / f"image-{number:03d}.png"; Image.new("RGB", (8, 8), "blue").save(path)
        paths.append(f"assets/{path.name}")
    proposal = tmp_path / "proposal.md"
    proposal.write_text("# T\n\n## A\n\n" + "\n\n".join(f"![图 {i}]({path})" for i, path in enumerate(paths, 1)), encoding="utf-8")
    report = __import__("json").loads(render_word(proposal, tmp_path / "proposal.docx").read_text(encoding="utf-8"))
    assert [report[key] for key in ("markdown_image_path_count", "standalone_image_block_count", "embedded_image_count", "unique_asset_count")] == [5] * 4
