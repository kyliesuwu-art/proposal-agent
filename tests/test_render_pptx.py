import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image
from pptx import Presentation

from src.render_pptx import RenderPptxError, build_slide_plan, render_pptx


def _fixture(tmp_path: Path, *, with_image=True, sources=True) -> tuple[Path, Path | None]:
    assets = tmp_path / "assets"; assets.mkdir()
    if with_image: Image.new("RGB", (200, 100), "blue").save(assets / "system.png")
    image = "\n![系统架构](assets/system.png)\n图片来源：[来源: 手册.pdf, 第 10 页]\n" if with_image else ""
    markdown = tmp_path / "proposal.md"
    markdown.write_text("""# 园区智慧能源方案

## 总体架构

### 建设范围

- 配置 10 MW 光伏与 20 MWh 储能[来源: 手册.pdf, 第 10 页]
- 接入 10 kV 配电数据[来源: 规范.pdf, 第 7 页]

系统支持预测、决策、调度闭环，容量需现场确认。[来源: 手册.pdf, 第 10 页]
""" + image + """
## 实施计划

| 阶段 | 工作 | 周期 |
| --- | --- | --- |
| 一期 | 勘察 | 2 周 |

## 来源与依据

- 手册.pdf：第 10 页
""", encoding="utf-8")
    source = tmp_path / "proposal.sources.json"
    if sources: source.write_text(json.dumps({"sources": [{"source_id": "S1", "filename": "手册.pdf", "pages": [10], "chunk_id": "private"}, {"source_id": "S2", "filename": "规范.pdf", "pages": [7]}]}, ensure_ascii=False), encoding="utf-8")
    return markdown, source if sources else None


def test_plan_contains_cover_sections_tables_images_and_sources(tmp_path):
    markdown, _ = _fixture(tmp_path)
    plan, warnings = build_slide_plan(markdown, mode="presentation", max_slides=3)
    assert plan["slides"][0]["layout"] == "title"
    assert any(s["layout"] == "table" for s in plan["slides"])
    assert any(s["figure_ids"] for s in plan["slides"])
    assert plan["slides"][-1]["layout"] == "sources"
    assert plan["sources"]["S1"]["filename"] == "手册.pdf"
    assert not warnings or all("maximum" in x for x in warnings)


def test_render_valid_pptx_report_and_reopen(tmp_path):
    markdown, _ = _fixture(tmp_path)
    output = tmp_path / "proposal.pptx"
    report_path = render_pptx(markdown, output, mode="faithful")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["errors"] == []
    with zipfile.ZipFile(output) as archive:
        assert "[Content_Types].xml" in archive.namelist()
        assert "ppt/presentation.xml" in archive.namelist()
        assert any(n.startswith("ppt/media/") for n in archive.namelist())
    assert len(Presentation(output).slides) == report["slide_count"]


def test_overflow_does_not_delete_content(tmp_path):
    markdown, _ = _fixture(tmp_path, with_image=False)
    markdown.write_text(markdown.read_text(encoding="utf-8").replace("系统支持预测、决策、调度闭环，容量需现场确认。", "。".join(f"第{i}项工程参数为 {i} kW" for i in range(18)) + "。"), encoding="utf-8")
    plan, warnings = build_slide_plan(markdown, max_slides=2)
    assert any("maximum" in warning for warning in warnings)
    assert any("（续）" in slide["title"] for slide in plan["slides"])
    assert "第17项工程参数为 17 kW" in "\n".join(sum((s["bullets"] for s in plan["slides"]), []))


def test_missing_sources_and_images_warn_without_http(tmp_path):
    markdown, _ = _fixture(tmp_path, with_image=False, sources=False)
    plan, warnings = build_slide_plan(markdown)
    assert plan["sources"] and any("derived" in warning for warning in warnings)
    markdown.write_text(markdown.read_text(encoding="utf-8") + "\n![bad](assets/nope.png)\n", encoding="utf-8")
    _, warnings = build_slide_plan(markdown)
    assert any("skipped missing" in warning for warning in warnings)


def test_invalid_explicit_sources_json_warns(tmp_path):
    markdown, _ = _fixture(tmp_path, sources=False)
    invalid = tmp_path / "bad.json"; invalid.write_text("{", encoding="utf-8")
    _, warnings = build_slide_plan(markdown, sources_path=invalid)
    assert any("invalid sources JSON" in warning for warning in warnings)
