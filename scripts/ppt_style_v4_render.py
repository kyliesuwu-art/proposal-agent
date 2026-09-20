"""Render the completed V4 visual plan without changing any production path."""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs/ppt_style_v4"


def load_v3_renderer():
    # Reuse the tested Markdown parser, inline sanitizer, image crop and audit code.
    old_argv = sys.argv[:]
    sys.argv.append("--v3")
    try:
        spec = importlib.util.spec_from_file_location("ppt_style_v3_renderer", ROOT / "scripts/ppt_style_v1_experiment.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(module)
        return module
    finally:
        sys.argv[:] = old_argv


def apply_visual_intent(markdown: str) -> str:
    """Add only renderer hints; source facts, wording and page order remain unchanged."""
    pieces = [piece.strip() for piece in markdown.split("\n---\n") if piece.strip()]
    # These are renderer-executable translations of the Art Director plans, not a content schema.
    hints = {
        2: "image", 3: "metrics", 4: "image", 5: "process", 7: "architecture",
        8: "metrics", 9: "architecture", 10: "image", 11: "architecture", 12: "process",
        13: "image", 14: "summary", 15: "section",
    }
    rendered = []
    for number, piece in enumerate(pieces, 1):
        hint = hints.get(number)
        if hint and "<!-- visual:" not in piece:
            lines = piece.splitlines()
            title_at = next((i for i, line in enumerate(lines) if line.startswith("#")), 0)
            lines.insert(title_at + 1, f"<!-- visual: {hint} -->")
            piece = "\n".join(lines)
        rendered.append(piece)
    return "\n---\n".join(rendered) + "\n"


def main() -> None:
    plan = json.loads((OUT / "visual_plan.json").read_text(encoding="utf-8"))
    if [item["slide"] for item in plan["plan"]] != list(range(1, 16)):
        raise RuntimeError("visual plan is not 15/15")
    raw = (OUT / "slides.final.md").read_text(encoding="utf-8")
    render_input = OUT / "render_input.md"
    render_input.write_text(apply_visual_intent(raw), encoding="utf-8")
    renderer = load_v3_renderer()
    pages, layouts = renderer.render(render_input, OUT / "proposal.before_visual_review.pptx")
    shutil.copy2(OUT / "proposal.before_visual_review.pptx", OUT / "proposal.pptx")
    audit = renderer.audit(OUT / "proposal.pptx", pages, layouts)
    report = {
        "visual_plan_coverage": "15/15",
        "renderer": "V4 plan-to-hints adapter + existing tested renderer primitives",
        "ai_visual_review": "NOT_AVAILABLE",
        "visual_revision": "NOT_RUN_BY_SCOPE",
        "audit": audit,
        "layouts": layouts,
    }
    (OUT / "quality_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    a = audit
    lines = [
        "# PPT Style V4 质量报告", "",
        "- SCOPE: 独立实验；未修改正式 proposal pipeline、RAG、数据库、Word pipeline 或 `.env`。",
        "- ART_DIRECTOR: 3 个 5 页批次均已完成并合并为 15/15 visual plan。",
        "- AI_VISUAL_REVIEW: `NOT_AVAILABLE`（本轮没有可靠的图片输入能力证据）。",
        "- VISUAL_REVISION: `NOT_RUN_BY_SCOPE`（按本轮 P0，先交付 Art Director + renderer 第一版）。", "",
        "## 确定性门禁", "",
        f"- SLIDE_COUNT / EMPTY_SLIDES: {a['slide_count']} / {a['empty_slides']}",
        f"- MARKDOWN_LEAK / CITATION_LEAK: {int(a['markdown_syntax_leak'])} / {int(a['citation_leak'])}",
        f"- SHAPE_OVERFLOW / TEXT_OVERFLOW: {len(a['shape_overflow'])} / {len(a['text_overflow'])}",
        f"- VISUAL_COLLISION: {len(a['visual_collision'])}（当前检测同时报出了形状与其内嵌文本框；没有 PNG 时不得据此声称零碰撞）",
        f"- MIN_BODY_FONT: {a['min_body_font_pt']} pt（未达到 16 pt 目标）",
        f"- IMAGE_DRIVEN_PAGES / SELECTED_IMAGES: {a['image_page_count']} / {a['selected_image_count']}",
        f"- LAYOUT_COUNTS: {a['layout_counts']}",
        "",
        "## 可观察性", "",
        "- PowerPoint COM：本机未注册。",
        "- LibreOffice：已检测到，但两次隔离 profile 的 PPTX→PDF 导出均在有限等待窗口内无产物且被停止。",
        "- 因此 `previews/slide-*.png` 与 `contact_sheet.png` 未生成；不能把本报告当作渲染后视觉验收。",
        "",
        "## 诚实状态", "",
        "- V4 Art Director 分批、可续跑、计划合并与 PPTX 渲染：`PASS`。",
        "- PowerPoint 实际 PNG 与人工视觉对照：`BLOCKED_BY_LOCAL_RENDER_BACKEND`。",
        "- 综合评分：`DRAFT_WITH_WARNINGS`；在拿到真实 PNG 前，不对 V3→V4 的肉眼提升或 B-/B 等级作结论。",
    ]
    (OUT / "quality_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"slides": audit["slide_count"], "layouts": audit["layout_counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
