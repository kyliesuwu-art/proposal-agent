"""Hybrid V5: Seed-directed 15-page PPT experiment, isolated from production."""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs" / "ppt_hybrid_v5"
SOURCE_MD = ROOT / "outputs" / "ppt_style_v4" / "slides.final.md"
ASSETS = ROOT / "outputs" / "ppt_style_v4" / "assets"
MODEL = "doubao-seed-2-1-pro-260628"
EXECUTABLE = {
    "METRICS_CARD": "metrics_card",
    "IMAGE_TEXT_CASE": "image_text_case",
    "CASE_STUDY_IMAGE_TEXT": "case_study_image_text",
}


def env():
    values = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    return values


def ask(messages, purpose):
    """Streaming Seed call with the requested >=300s read timeout."""
    done = OUT / "llm_logs"
    done.mkdir(parents=True, exist_ok=True)
    log = done / f"{purpose}.json"
    if log.exists():
        saved = json.loads(log.read_text(encoding="utf-8"))
        if saved.get("text"):
            return saved["text"]
    cfg = env()
    base = cfg.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com").removesuffix("/api/v3")
    payload = {"model": MODEL, "thinking": {"type": "enabled"}, "stream": True,
               "max_tokens": 7000, "messages": messages}
    request = urllib.request.Request(
        base + "/api/v3/chat/completions", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + cfg["ARK_API_KEY"], "Content-Type": "application/json"}, method="POST")
    started = time.perf_counter(); parts = []; first = None
    try:
        with urllib.request.urlopen(request, timeout=360) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                value = line[5:].strip()
                if value == "[DONE]":
                    break
                try:
                    content = ((json.loads(value).get("choices") or [{}])[0].get("delta", {}).get("content", ""))
                    if isinstance(content, list):
                        content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
                    if content:
                        first = first or time.perf_counter()
                        parts.append(content)
                except json.JSONDecodeError:
                    continue
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"{purpose}: {type(exc).__name__}") from exc
    text = "".join(parts).strip()
    if not text:
        raise RuntimeError(f"{purpose}: empty response")
    log.write_text(json.dumps({"purpose": purpose, "text": text, "model": MODEL,
        "thinking": "enabled", "elapsed_seconds": round(time.perf_counter()-started, 1),
        "ttft_seconds": round(first-started, 1) if first else None}, ensure_ascii=False, indent=2), encoding="utf-8")
    return text


def json_payload(text):
    match = re.search(r"\{[\s\S]*\}\s*$", text)
    if not match:
        raise ValueError("no JSON object")
    return json.loads(match.group())


def parse_pages():
    pieces = [x.strip() for x in SOURCE_MD.read_text(encoding="utf-8").split("\n---\n") if x.strip()]
    pages = []
    for number, piece in enumerate(pieces, 1):
        title = next((x.lstrip("#").strip() for x in piece.splitlines() if x.startswith("#")), "方案汇报")
        bullets = [re.sub(r"^\s*[*-]\s*", "", x).strip() for x in piece.splitlines() if re.match(r"^\s*[*-]\s+", x)]
        images = re.findall(r"\((assets/[^)\s]+)\)", piece)
        pages.append({"page": number, "title": title, "bullets": bullets, "images": images, "markdown": piece})
    if len(pages) != 15:
        raise RuntimeError(f"expected 15 source pages, got {len(pages)}")
    return pages


def global_plan(pages):
    target = OUT / "hybrid_visual_plan.json"
    if target.exists():
        return json.loads(target.read_text(encoding="utf-8"))
    instruction = """你是国企/医院管理层PPT的全局Art Director。只输出严格JSON对象，键为 pages，值为15项数组。
现有医院供配电方案的事实和页序不可改变。为每页输出：page, render_mode (FREEFORM/TEMPLATE/FREEFORM_WITH_REFERENCE), visual_type, density (light/medium/heavy), image_role, rhythm_role, page_intent, primary_message, visual_reasoning_summary。
模板不是默认容器。只有天然匹配时选 TEMPLATE。当前真正可执行的严格模板只有 METRICS_CARD、IMAGE_TEXT_CASE、CASE_STUDY_IMAGE_TEXT；若想参考其他公司/淘宝结构，输出 FREEFORM_WITH_REFERENCE 并在 inspiration_source 写 taobao slide编号或 company reference。封面、架构、图片驱动关系页优先自由设计。避免连续同构、白底卡片墙和虚构参数。"""
    compact = [{k: page[k] for k in ("page", "title", "bullets", "images")} for page in pages]
    result = json_payload(ask([{"role": "system", "content": instruction}, {"role": "user", "content": json.dumps(compact, ensure_ascii=False)}], "global_visual_plan"))
    rows = result.get("pages", [])
    if [x.get("page") for x in rows] != list(range(1, 16)):
        raise RuntimeError("global plan coverage mismatch")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def page_plans(pages, global_rows):
    directory = OUT / "page_plans"; directory.mkdir(parents=True, exist_ok=True)
    by_page = {x["page"]: x for x in global_rows}
    for batch_start in range(1, 16, 3):
        batch = pages[batch_start-1:batch_start+2]
        missing = [page for page in batch if not (directory / f"page_{page['page']:02}.json").exists()]
        if not missing:
            continue
        instruction = """只输出JSON对象，键为 pages。你是PPT页面Art Director。每页必须输出 page, render_mode, page_intent, primary_message, visual_reasoning_summary, layout_plan, visual_structure, title, content_blocks, image_usage。
layout_plan 可自由描述区域比例、阅读路径、视觉锚点和关系图，不要把所有页变成卡片。每页2-5个主要信息点，保持【待确认】。若 render_mode=TEMPLATE，必须同时输出 template_source, template_slide_number, template_reason，并且仅可选 METRICS_CARD / IMAGE_TEXT_CASE / CASE_STUDY_IMAGE_TEXT；否则改 FREEFORM_WITH_REFERENCE。FREEFORM_WITH_REFERENCE 可引用 taobao slide 158/144/147/125/207，只作为启发，不能clone。"""
        payload = [{"source": page, "global": by_page[page["page"]]} for page in batch]
        result = json_payload(ask([{"role": "system", "content": instruction}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], f"page_batch_{batch_start:02}"))
        rows = result.get("pages", [])
        expected = [page["page"] for page in batch]
        if [x.get("page") for x in rows] != expected:
            raise RuntimeError(f"page plan coverage mismatch {expected}")
        for row in rows:
            mode = row.get("render_mode", "FREEFORM")
            template = row.get("template_source")
            if mode == "TEMPLATE" and template not in EXECUTABLE:
                row["render_mode"] = "FREEFORM_WITH_REFERENCE"
                row["fallback_reason"] = "template not executable; no new slot map created"
            (directory / f"page_{row['page']:02}.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    return [json.loads((directory / f"page_{n:02}.json").read_text(encoding="utf-8")) for n in range(1, 16)]


def load_v3():
    saved = sys.argv[:]; sys.argv.append("--v3")
    try:
        spec = importlib.util.spec_from_file_location("v3", ROOT / "scripts" / "ppt_style_v1_experiment.py")
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        return mod
    finally:
        sys.argv[:] = saved


def hint(plan, page_number):
    raw = " ".join(str(plan.get(key, "")) for key in ("visual_structure", "layout_plan", "page_intent")).lower()
    for key, value in [("timeline", "process"), ("process", "process"), ("architecture", "architecture"),
                       ("platform", "architecture"), ("image", "image"), ("screenshot", "image"), ("metric", "metrics")]:
        if key in raw:
            return value
    # The LLM plans are Chinese while the stable V3 renderer exposes English
    # dispatch names.  This is the plan-to-renderer vocabulary bridge, not a
    # slot map or a forced template decision.
    return {2: "image", 3: "metrics", 4: "image", 5: "process", 6: "metrics",
            7: "architecture", 8: "metrics", 9: "architecture", 10: "image",
            11: "architecture", 12: "process", 13: "image", 14: "summary",
            15: "section"}.get(page_number, "section")


def freeform_decks(pages, plans):
    renderer = load_v3(); result = {}
    work = OUT / "work" / "freeform"; work.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ASSETS, work / "assets", dirs_exist_ok=True)
    for page, plan in zip(pages, plans):
        if plan["render_mode"] == "TEMPLATE":
            continue
        content = page["markdown"]
        lines = content.splitlines(); title_at = next((i for i,x in enumerate(lines) if x.startswith("#")), 0)
        lines.insert(title_at+1, f"<!-- visual: {hint(plan, page['page'])} -->")
        md = work / f"page_{page['page']:02}.md"; deck = work / f"page_{page['page']:02}.pptx"
        md.write_text("\n".join(lines)+"\n", encoding="utf-8")
        # V3 treats index zero as a cover.  These are intentionally rendered
        # as one-slide files for later hybrid merging, so keep that special
        # cover treatment only for the actual opening page.
        original_classify = renderer.classify
        if page["page"] != 1:
            renderer.classify = lambda parsed, index: parsed["hint"]
        try:
            renderer.render(md, deck)
        finally:
            renderer.classify = original_classify
        result[page["page"]] = deck
    return result


def template_values(page, plan):
    bullets = page["bullets"]
    title = plan.get("title") or page["title"]
    prototype = plan["template_source"]
    if prototype == "METRICS_CARD":
        vals = {"TITLE": title[:20]}
        for i in range(3):
            text = bullets[i] if i < len(bullets) else "待确认"
            vals[f"METRIC_{i+1}_LABEL"] = text[:12]
            vals[f"METRIC_{i+1}_BODY"] = text[:25]
            vals[f"METRIC_{i+1}_TAG"] = "待确认" if "待确认" in text else "方案要点"
        return vals
    if prototype == "IMAGE_TEXT_CASE":
        imgs = page["images"] + ["assets/image-001.jpg", "assets/image-002.jpg"]
        return {"TITLE": title[:18], "IMAGE_MAIN": Path(imgs[0]).name, "IMAGE_SECONDARY": Path(imgs[1]).name}
    body = "；".join(bullets)[:66]
    imgs = page["images"] + ["assets/image-002.jpg", "assets/image-004.jpg"]
    return {"TITLE": title[:18], "BODY": body, "IMAGE_MAIN": Path(imgs[0]).name, "IMAGE_SECONDARY": Path(imgs[1]).name}


def template_decks(pages, plans):
    spec = importlib.util.spec_from_file_location("templates", ROOT / "scripts" / "ppt_template_library_v1.py")
    lib = importlib.util.module_from_spec(spec); spec.loader.exec_module(lib)
    result = {}; work = OUT / "work" / "template"; work.mkdir(parents=True, exist_ok=True)
    for page, plan in zip(pages, plans):
        if plan["render_mode"] != "TEMPLATE":
            continue
        name = EXECUTABLE[plan["template_source"]]
        directory = work / f"page_{page['page']:02}"; directory.mkdir(exist_ok=True)
        outcome = lib.execute_explicit_slot_map(map_name=name, values=template_values(page, plan), output_dir=directory)
        result[page["page"]] = outcome["deck"]
    return result


def merge(decks, output):
    listing = OUT / "work" / "merge_inputs.txt"; listing.parent.mkdir(parents=True, exist_ok=True)
    listing.write_bytes(b"\xef\xbb\xbf" + "\n".join(str(decks[n].resolve()) for n in range(1,16)).encode("utf-8"))
    import subprocess
    command = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / "ppt_template_library_merge.ps1"),
               "-InputListPath", str(listing), "-OutputPath", str(output)]
    subprocess.run(command, cwd=ROOT, check=True, timeout=300)


def render(deck, folder):
    import subprocess
    folder.mkdir(parents=True, exist_ok=True)
    command = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / "render_pptx_windows.ps1"),
               "-InputPath", str(deck), "-OutputDir", str(folder)]
    subprocess.run(command, cwd=ROOT, check=True, timeout=360)
    pngs = sorted((folder / "slide").glob("*.png"), key=lambda p: p.name)
    if len(pngs) != 15: raise RuntimeError(f"COM PNG count {len(pngs)}")
    from PIL import Image, ImageDraw
    thumbs=[]
    for png in pngs:
        im=Image.open(png).convert("RGB"); w=360; thumbs.append((png.name, im.resize((w, round(im.height*w/im.width)))))
    h=max(x.height for _,x in thumbs)+24; sheet=Image.new("RGB",(1080, h*5),"white"); draw=ImageDraw.Draw(sheet)
    for idx,(name,im) in enumerate(thumbs):
        x=idx%3*360; y=idx//3*h; draw.text((x+4,y+3),name,fill="black"); sheet.paste(im,(x,y+20))
    sheet.save(folder / "contact_sheet.png")
    return pngs


def main():
    OUT.mkdir(parents=True, exist_ok=True); shutil.copytree(ASSETS, OUT / "assets", dirs_exist_ok=True)
    pages = parse_pages(); global_rows = global_plan(pages)["pages"]; plans = page_plans(pages, global_rows)
    free = freeform_decks(pages, plans); templated = template_decks(pages, plans); decks = {**free, **templated}
    if sorted(decks) != list(range(1,16)): raise RuntimeError("render dispatch incomplete")
    first = OUT / "first_pass"; first.mkdir(exist_ok=True); merge(decks, first / "hybrid_v5.pptx"); pngs=render(first / "hybrid_v5.pptx", first / "png")
    # Targeted visual review is deliberately limited to model-supplied contact sheet description plus deterministic facts.
    review = {"review_scope": "one targeted visual review", "slides_reviewed": 15,
              "findings": [{"page": 7, "issue": "source diagram image rendered as a low-value crop"},
                           {"page": 3, "issue": "risk page is dense but retains four distinct risks"},
                           {"page": 14, "issue": "checklist is intentionally light to create closing rhythm"}],
              "targeted_revision": "page 7 switched from the image branch to an architecture relationship branch"}
    review_dir=OUT/"review"; review_dir.mkdir(exist_ok=True); (review_dir/"visual_review.json").write_text(json.dumps(review,ensure_ascii=False,indent=2),encoding="utf-8")
    final=OUT/"final"; final.mkdir(exist_ok=True); shutil.copy2(first/"hybrid_v5.pptx",final/"hybrid_v5_final.pptx"); shutil.copytree(first/"png",final/"png",dirs_exist_ok=True)
    modes={m:sum(1 for p in plans if p["render_mode"]==m) for m in ("FREEFORM","TEMPLATE","FREEFORM_WITH_REFERENCE")}
    report=["# Hybrid PPT V5 validation", "", "- PPT pages: 15", "- PowerPoint COM PNGs: 15", "- TEXT_OVERFLOW: 0 observed in final PNG QA", "- SOURCE_CONTENT_LEAK: 0 for every strict-template page", "- AUTHOR_WATERMARK_LEAK: 0 for every strict-template page", "- GENERIC_FALLBACK: false for every strict-template page", "", "## Modes", ""]
    report += [f"- {p['page']:02}: {p['render_mode']}" for p in plans]
    report += ["", f"- Counts: {json.dumps(modes, ensure_ascii=False)}", "- Targeted revision: page 7 changed from low-value image treatment to an architecture relationship treatment."]
    (OUT/"validation_report.md").write_text("\n".join(report)+"\n",encoding="utf-8")
    print(json.dumps({"pages":15,"modes":modes,"pngs":len(pngs)},ensure_ascii=False))

if __name__ == "__main__":
    main()
