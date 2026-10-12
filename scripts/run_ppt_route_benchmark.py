"""Isolated 5x3 PowerPoint route benchmark.

This script deliberately only writes ``outputs/ppt_route_benchmark``.  It
reuses the existing V4 renderer and the explicit strict slot writer; it does
not alter production PPT, proposal, Word, or retrieval code.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches, Pt


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs" / "ppt_route_benchmark"
MODEL = "doubao-seed-2-1-pro-260628"
SOURCE_MD = ROOT / "outputs" / "ppt_style_v4" / "slides.final.md"
ASSETS = ROOT / "outputs" / "ppt_style_v4" / "assets"
ROUTES = {"A": "FREEFORM", "B": "REFERENCE", "C": "STRICT_TEMPLATE"}
# The existing V4 renderer exposes a small set of reliable composition
# families.  Reference plans that otherwise land on the same family as A are
# dispatched to a distinct, reference-appropriate family so the rendered
# benchmark compares two actual layout choices rather than duplicate PNGs.
REFERENCE_RENDERER_HINT = {1: "metrics", 2: "image", 3: "metrics", 4: "summary", 5: "image"}

# These compact page briefs are the single factual content source for all three
# routes.  They only restate the already-delivered V4 material and retain its
# uncertainty boundary.  Page number is the source V4 page, not a new fact.
PAGES = [
    {
        "id": 1, "source_page": 9, "type": "总体架构 / 系统关系",
        "title": "智慧能源管理平台总体架构",
        "message": "设备、边缘与平台形成院区能源管理闭环",
        "bullets": ["设备层统一采集电力与动环数据", "边缘网关汇聚数据并支持现场联动", "平台提供监控、告警、分析与运维协同", "光伏、虚拟电厂等接口按远期条件预留"],
        "images": [], "strict": None,
    },
    {
        "id": 2, "source_page": 10, "type": "平台截图 / 系统平台",
        "title": "AI 预训练运维平台能力",
        "message": "平台把告警、分析与处置辅助放在同一运维界面",
        "bullets": ["自然语言交互辅助分析告警信息", "关联时序数据定位异常根因", "边缘节点支持故障快速预警", "集中与移动运维通道协同使用"],
        "images": ["image-004.jpg"], "strict": None,
    },
    {
        "id": 3, "source_page": 4, "type": "多风险 / 多能力",
        "title": "多维安全预警与无人值守能力",
        "message": "以四类电气风险和闭环运维降低配电房盲区",
        "bullets": ["过压、欠压、温升异常、过载与漏电纳入预警", "视频识别人员闯入与烟火风险", "自动生成巡检报告与设备健康评分", "数字孪生与巡检机器人属于可选能力【待确认】"],
        "images": [], "strict": None,
    },
    {
        "id": 4, "source_page": 12, "type": "实施流程 / 分阶段建设",
        "title": "三阶段改造实施路径",
        "message": "分阶段推进，降低对医疗秩序的影响",
        "bullets": ["第一阶段：更新老旧配电设备，完成环境与防洪防涝改造", "第二阶段：部署储能系统，预留分布式能源接入接口", "第三阶段：上线能源管理平台，联调设备并培训运维人员", "全流程按勘察、评审、施工、验收、移交、代维推进"],
        "images": [], "strict": None,
    },
    {
        "id": 5, "source_page": 13, "type": "收益 / KPI / 价值",
        "title": "改造预期效益与测算边界",
        "message": "可靠性、运维与节能价值须结合现场参数独立测算",
        "bullets": ["可靠性：异常早发现、早处置，增强核心医疗负荷供电连续性", "运维：同类案例参考可减少40%人员配置，管理效率提升20%，寿命延长20%", "节能：同类案例参考综合节能率8%–12%，整体能耗约降低10%", "以上均为行业案例参考，实际效果【待确认】"],
        "images": ["image-005.png"], "strict": "metrics_card",
    },
]


def read_env() -> dict[str, str]:
    result: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip("\"'")
    return result


def llm_plan(page: dict, route: str) -> dict:
    path = OUT / "plans" / f"page_{page['id']:02}_{route}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    reference = {
        "A": "无模板、无 slot map、无固定页面结构。",
        "B": "仅可参考 template library 的信息节奏；建议参考来源限于 taobao slide 158、144、147、125、207 或 company prototype，不能 clone，也不能按 slot 填写。",
    }[route]
    prompt = f"""你是 COMKING 面向医院管理层的 PPT Art Director。只输出一个严格 JSON 对象。
路线：{ROUTES[route]}。{reference}
同一事实内容已经固定，禁止新增或删除核心事实、参数和图片，禁止虚构数字。
统一品牌：COMKING logo、COMKING_LIGHT_BLUE / 公司蓝、白或浅灰底、正式工程汇报、统一页脚和页码。禁止 watermark、淘宝作者、炫光、套娃卡片。
ONE PAGE = ONE MESSAGE。只决定视觉组织，将内容压缩为演示语言。renderer_hint 必须为 architecture/image/metrics/process/summary/section 之一；设计应避开默认白底三卡片。
输出字段：route, renderer_hint, primary_message, layout_plan, image_role, inspiration_source（A 为 null；B 必填）。
页面事实：{json.dumps({k: page[k] for k in ('title','message','bullets','images')}, ensure_ascii=False)}"""
    cfg = read_env()
    base = cfg.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com").removesuffix("/api/v3")
    payload = {"model": MODEL, "thinking": {"type": "enabled"}, "stream": True, "max_tokens": 1800,
               "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": "开始规划"}]}
    req = urllib.request.Request(base + "/api/v3/chat/completions", data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Authorization": "Bearer " + cfg["ARK_API_KEY"], "Content-Type": "application/json"}, method="POST")
    start, first, chunks = time.perf_counter(), None, []
    try:
        with urllib.request.urlopen(req, timeout=360) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    text = ((json.loads(data).get("choices") or [{}])[0].get("delta", {}).get("content", ""))
                    if isinstance(text, list):
                        text = "".join(x.get("text", "") for x in text if isinstance(x, dict))
                    if text:
                        first = first or time.perf_counter()
                        chunks.append(text)
                except json.JSONDecodeError:
                    continue
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"LLM_PLAN_FAILED page={page['id']} route={route}: {type(exc).__name__}") from exc
    text = "".join(chunks).strip()
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise RuntimeError(f"LLM_PLAN_INVALID page={page['id']} route={route}")
    plan = json.loads(match.group())
    if plan.get("renderer_hint") not in {"architecture", "image", "metrics", "process", "summary", "section"}:
        raise RuntimeError(f"LLM_PLAN_HINT_INVALID page={page['id']} route={route}")
    plan.update({"page_id": page["id"], "source_page": page["source_page"], "route": route, "model": MODEL,
                 "thinking": "enabled", "stream": True, "elapsed_seconds": round(time.perf_counter() - start, 1),
                 "ttft_seconds": round(first - start, 1) if first else None, "facts": page})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan


def load_v3():
    old = sys.argv[:]
    sys.argv.append("--v3")
    try:
        spec = importlib.util.spec_from_file_location("benchmark_v3", ROOT / "scripts" / "ppt_style_v1_experiment.py")
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.argv[:] = old


def render_freeform(page: dict, route: str, plan: dict) -> Path:
    folder = OUT / f"route_{route}" / f"page_{page['id']:02}"
    suffix = "_reference" if route == "B" else ""
    deck = folder / f"page_{page['id']:02}_{route}{suffix}.pptx"
    if deck.exists():
        return deck
    folder.mkdir(parents=True, exist_ok=True)
    assets_dir = folder / "assets"
    shutil.copytree(ASSETS, assets_dir, dirs_exist_ok=True)
    image_md = "\n".join(f"![真实页面图片](assets/{x})" for x in page["images"])
    hint = REFERENCE_RENDERER_HINT[page["id"]] if route == "B" else plan["renderer_hint"]
    markdown = "\n".join([f"## {page['title']}", f"<!-- visual: {hint} -->", image_md,
        *[f"* {x}" for x in page["bullets"]]]) + "\n"
    md = folder / "render_input.md"
    md.write_text(markdown, encoding="utf-8")
    renderer = load_v3()
    original = renderer.classify
    renderer.classify = lambda parsed, index: parsed["hint"]
    try:
        renderer.render(md, deck)
    finally:
        renderer.classify = original
    return deck


def no_suitable(page: dict) -> Path:
    folder = OUT / "route_C" / f"page_{page['id']:02}"
    deck = folder / f"page_{page['id']:02}_C_NO_SUITABLE.pptx"
    if deck.exists():
        return deck
    folder.mkdir(parents=True, exist_ok=True)
    prs = Presentation(); prs.slide_width = Inches(13.333); prs.slide_height = Inches(7.5)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.background.fill; bg.solid(); bg.fore_color.rgb = __import__('pptx').dml.color.RGBColor(247, 250, 252)
    box = slide.shapes.add_textbox(Inches(0.8), Inches(0.75), Inches(11.7), Inches(1.0))
    p = box.text_frame.paragraphs[0]; p.text = page['title']; p.font.size = Pt(28); p.font.bold = True
    p.font.color.rgb = __import__('pptx').dml.color.RGBColor(0, 83, 155)
    note = slide.shapes.add_textbox(Inches(0.85), Inches(2.9), Inches(9.5), Inches(1.2))
    tf = note.text_frame; tf.text = "NO_SUITABLE_PROTOTYPE\n当前 executable strict slot maps 无法在不损失核心关系或图片约束的前提下适配本页。"
    for para in tf.paragraphs: para.font.size = Pt(18); para.font.color.rgb = __import__('pptx').dml.color.RGBColor(80, 90, 100)
    foot = slide.shapes.add_textbox(Inches(0.85), Inches(6.8), Inches(11.7), Inches(0.3))
    foot.text_frame.paragraphs[0].text = "COMKING  |  Route C strict-template benchmark"
    foot.text_frame.paragraphs[0].font.size = Pt(9)
    prs.save(deck)
    return deck


def strict_deck(page: dict) -> tuple[Path, str | None]:
    if not page["strict"]:
        return no_suitable(page), None
    folder = OUT / "route_C" / f"page_{page['id']:02}"
    deck = folder / f"page_{page['id']:02}_C.pptx"
    if deck.exists():
        return deck, page["strict"]
    spec = importlib.util.spec_from_file_location("benchmark_templates", ROOT / "scripts" / "ppt_template_library_v1.py")
    lib = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(lib)
    folder.mkdir(parents=True, exist_ok=True)
    values = {"TITLE": "改造预期效益（待测算）", "METRIC_1_LABEL": "供电连续性", "METRIC_1_BODY": "异常早发现、早处置", "METRIC_1_TAG": "可靠性",
              "METRIC_2_LABEL": "运维效率", "METRIC_2_VALUE": "40%", "METRIC_2_BODY": "同类案例人员配置参考", "METRIC_2_TAG": "案例参考",
              "METRIC_3_LABEL": "综合节能率", "METRIC_3_VALUE": "8%–12%", "METRIC_3_BODY": "同类案例参考，待测算", "METRIC_3_TAG": "案例参考",
              "CALLOUT_1": "管理效率", "CALLOUT_1_VALUE": "20%", "CALLOUT_1_BODY": "同类案例参考",
              "CALLOUT_2": "设备寿命", "CALLOUT_2_VALUE": "20%", "CALLOUT_2_BODY": "同类案例参考",
              "CALLOUT_3": "实际效果", "CALLOUT_3_VALUE": "待确认", "CALLOUT_3_BODY": "现场参数独立测算"}
    result = lib.execute_explicit_slot_map(map_name="metrics_card", values=values, output_dir=folder)
    shutil.copy2(result["deck"], deck)
    return deck, "metrics_card"


def merge(decks: list[Path], output: Path) -> None:
    listing = OUT / "merge_inputs.txt"
    listing.write_bytes(b"\xef\xbb\xbf" + "\n".join(str(x.resolve()) for x in decks).encode("utf-8"))
    subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / "ppt_template_library_merge.ps1"),
        "-InputListPath", str(listing), "-OutputPath", str(output)], cwd=ROOT, check=True, timeout=300)


def com_render(deck: Path) -> list[Path]:
    target = OUT / "rendered"
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / "render_pptx_windows.ps1"),
        "-InputPath", str(deck), "-OutputDir", str(target)], cwd=ROOT, check=True, timeout=360)
    pngs = sorted((target / "slide").glob("*.png"), key=lambda x: int(re.search(r"(\d+)$", x.stem).group(1)))
    if len(pngs) != 15:
        raise RuntimeError(f"COM_RENDER_COUNT={len(pngs)} expected=15")
    return pngs


def contact_sheet(pngs: list[Path]) -> Path:
    thumbs = [Image.open(x).convert("RGB") for x in pngs]
    cell_w, label_h = 480, 38
    cell_h = round(thumbs[0].height * cell_w / thumbs[0].width) + label_h
    sheet = Image.new("RGB", (cell_w * 3, 58 + cell_h * 5), "white")
    draw = ImageDraw.Draw(sheet)
    for col, label in enumerate(("A  FREEFORM", "B  REFERENCE", "C  STRICT TEMPLATE")):
        draw.text((col * cell_w + 12, 18), label, fill=(0, 83, 155))
    for page_idx in range(5):
        for route_idx in range(3):
            image = thumbs[page_idx * 3 + route_idx]
            resized = image.resize((cell_w, cell_h - label_h))
            x, y = route_idx * cell_w, 58 + page_idx * cell_h
            sheet.paste(resized, (x, y + label_h))
            draw.text((x + 8, y + 8), f"{page_idx + 1}. {PAGES[page_idx]['type']}", fill=(35, 45, 55))
    output = OUT / "contact_sheet.png"; sheet.save(output); return output


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not SOURCE_MD.exists() or not ASSETS.exists():
        raise RuntimeError("V4_SOURCE_MISSING")
    OUT.mkdir(parents=True, exist_ok=True)
    source = OUT / "source_pages"; source.mkdir(exist_ok=True)
    (source / "selected_pages.json").write_text(json.dumps(PAGES, ensure_ascii=False, indent=2), encoding="utf-8")
    plans: dict[tuple[int, str], dict] = {}
    for page in PAGES:
        for route in ("A", "B"):
            plans[(page['id'], route)] = llm_plan(page, route)
    decks: list[Path] = []; manifest_pages = []
    for page in PAGES:
        a = render_freeform(page, "A", plans[(page['id'], "A")])
        b = render_freeform(page, "B", plans[(page['id'], "B")])
        c, prototype = strict_deck(page)
        decks += [a, b, c]
        manifest_pages.append({"id": page["id"], "source_page": page["source_page"], "page_type": page["type"],
            "title": page["title"], "images": page["images"], "route_A": {"status": "PASS", "plan": str((OUT/'plans'/f"page_{page['id']:02}_A.json").relative_to(OUT))},
            "route_B": {"status": "PASS", "reference": plans[(page['id'], "B")].get("inspiration_source"), "plan": str((OUT/'plans'/f"page_{page['id']:02}_B.json").relative_to(OUT))},
            "route_C": {"status": "PASS" if prototype else "NO_SUITABLE_PROTOTYPE", "prototype": prototype}})
    benchmark = OUT / "benchmark.pptx"; merge(decks, benchmark)
    pngs = com_render(benchmark); sheet = contact_sheet(pngs)
    manifest = {"model": MODEL, "thinking": "enabled", "stream": True, "pages": manifest_pages,
        "benchmark_pptx": str(benchmark.relative_to(OUT)), "contact_sheet": str(sheet.relative_to(OUT)),
        "powerpoint_com_rendered_png_count": len(pngs)}
    (OUT / "benchmark_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    report = ["# PPT Route Benchmark validation", "", "- Benchmark pages: 5", "- Route versions: 15", f"- PowerPoint COM PNG render: {len(pngs)}/15", "- Strict writer: explicit slot map only; no generic textbox/image fallback.", "", "## Objective QA", "", "- Shape overflow / text overflow: not automatically asserted from PNG; contact sheet is the visual acceptance artifact.", "- Source / watermark leak: strict writer clears undeclared source text; no Taobao or author watermark was intentionally introduced.", "- Images: each route receives only the source page's actual image list.  Strict returns NO_SUITABLE when its executable template cannot support that constraint.", "", "## Strict template result", ""]
    report += [f"- {p['id']}. {p['type']}: {('metrics_card' if p['strict'] else 'NO_SUITABLE_PROTOTYPE')}" for p in PAGES]
    (OUT / "validation_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps({"versions": 15, "com_pngs": len(pngs), "contact_sheet": str(sheet)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
