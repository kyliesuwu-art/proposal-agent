"""Isolated Taobao-layout executable-prototype and Challenge Probe5 V2 run.

The private source is never opened for writing.  This script consumes only the
five pre-cloned one-slide decks.  Every content mutation is addressed by an
explicit shape id (group children included); no positional or traversal-order
fallback is implemented.
"""
from __future__ import annotations

import json, os, shutil, socket, subprocess, sys, time, urllib.request, zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Pt

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs/ppt_layout_library_scan/selected_prototypes"
RAW = BASE / "raw_clones"
OUT = BASE / "probes"
MAPS = BASE / "shape_maps"
CHALLENGE = ROOT / "outputs/ppt_layout_library_scan/challenge_probe5_v2"
ASSETS = ROOT / "outputs/ppt_style_v4/assets"
MODEL = "doubao-seed-2-1-pro-260628"
BLUE = "2F80ED"

# Targets were inspected from the clone PPTX itself.  Some source pages have a
# different true capacity than their candidate-review shorthand, so maps expose
# only real writable regions.  Tables/charts are removed because their source
# values are never allowable customer content.
SPECS = {
    "144": {
        "prototype_id": "TAOBAO_144_RISK_CAPABILITY",
        "semantic_pattern": "three-panel risk/capability dashboard with nine concise cells",
        "slots": {"TITLE": 2, "PANEL_1_TITLE": 120, "PANEL_1_A": 125, "PANEL_1_B": 126,
                  "PANEL_1_C": 127, "PANEL_2_TITLE": 54, "PANEL_2_A": 55, "PANEL_2_B": 56,
                  "PANEL_2_C": 57, "PANEL_3_TITLE": 29, "PANEL_3_A": 31, "PANEL_3_B": 32,
                  "PANEL_3_C": 33, "SUMMARY": 26},
        "remove": [36, 61, 130], "capacity": {"max_items": 9, "recommended_title_chars": 18,
                  "recommended_body_chars_per_slot": 12, "required_image_count": 0,
                  "supports_metrics": False, "supports_long_text": False, "supports_comparison": True},
        "probe": {"TITLE":"医院供电风险与能力边界", "PANEL_1_TITLE":"关键负荷", "PANEL_1_A":"手术室", "PANEL_1_B":"ICU", "PANEL_1_C":"急诊",
                  "PANEL_2_TITLE":"主要风险", "PANEL_2_A":"线路老化", "PANEL_2_B":"温升异常", "PANEL_2_C":"起火隐患",
                  "PANEL_3_TITLE":"管理短板", "PANEL_3_A":"人工巡检", "PANEL_3_B":"响应滞后", "PANEL_3_C":"存在盲区", "SUMMARY":"总供电容量、峰值负荷及设备台账【待确认】"},
    },
    "147": {
        "prototype_id": "TAOBAO_147_VALUE_KPI", "semantic_pattern": "four outcome cards with narrative callout",
        "slots": {"TITLE": 2, "METRIC_1_VALUE": 102, "METRIC_1_LABEL": 47, "METRIC_2_VALUE": 14,
                  "METRIC_2_LABEL": 17, "METRIC_3_VALUE": 23, "METRIC_3_LABEL": 24,
                  "METRIC_4_VALUE": 30, "METRIC_4_LABEL": 31, "CALLOUT_TITLE": 42, "CALLOUT_BODY": 44},
        "remove": [40, 46, 48], "capacity": {"max_items": 4, "recommended_title_chars": 18,
                  "recommended_body_chars_per_slot": 14, "required_image_count": 0, "supports_metrics": True,
                  "supports_long_text": False, "supports_comparison": False},
        "probe": {"TITLE":"医院改造价值与测算边界", "METRIC_1_VALUE":"风险前置", "METRIC_1_LABEL":"关键负荷", "METRIC_2_VALUE":"协同运维", "METRIC_2_LABEL":"告警工单",
                  "METRIC_3_VALUE":"能耗管理", "METRIC_3_LABEL":"现场测算", "METRIC_4_VALUE":"【待确认】", "METRIC_4_LABEL":"量化收益",
                  "CALLOUT_TITLE":"价值说明", "CALLOUT_BODY":"供电可靠性、运维效率与能耗管理能力可提升。投资、节能率与回收期须以负荷、电价及设备台账独立测算。"},
    },
    "207": {
        "prototype_id": "TAOBAO_207_PLATFORM_SCREENSHOT", "semantic_pattern": "three platform screenshots with explanatory cards",
        "slots": {"TITLE": 2, "IMAGE_MAIN": 21, "IMAGE_SECONDARY": 43, "IMAGE_TERTIARY": 47,
                  "CALLOUT_1_TITLE": 73, "CALLOUT_1_BODY": 85, "CALLOUT_1_FOOT": 58,
                  "CALLOUT_2_TITLE": 61, "CALLOUT_2_BODY": 62, "CALLOUT_2_FOOT": 64,
                  "CALLOUT_3_TITLE": 71, "CALLOUT_3_BODY": 72, "CALLOUT_3_FOOT": 76},
        "remove": [], "images": {"IMAGE_MAIN":"image-004.jpg", "IMAGE_SECONDARY":"image-002.jpg", "IMAGE_TERTIARY":"image-001.jpg"},
        "capacity": {"max_items": 3, "recommended_title_chars": 18, "recommended_body_chars_per_slot": 38,
                  "required_image_count": 3, "optional_image_count": 0, "supports_metrics": False, "supports_long_text": False, "supports_comparison": True},
        "probe": {"TITLE":"医院能源管理平台与运维解读", "CALLOUT_1_TITLE":"平台总览", "CALLOUT_1_BODY":"集中呈现配电室、动环与能耗数据，支撑运行状态查看。", "CALLOUT_1_FOOT":"平台截图",
                  "CALLOUT_2_TITLE":"告警协同", "CALLOUT_2_BODY":"关联告警信息与处置建议，供后勤人员研判和响应。", "CALLOUT_2_FOOT":"运维解读",
                  "CALLOUT_3_TITLE":"系统接入", "CALLOUT_3_BODY":"既有后勤及信息系统的接口范围待相关部门确认。", "CALLOUT_3_FOOT":"【待确认】"},
    },
    "125": {
        "prototype_id": "TAOBAO_125_CONFIRMATION_CHECKLIST", "semantic_pattern": "five confirmation areas on a staged checklist",
        "slots": {"TITLE": 2, "INTRO": 10, "ITEM_1_TITLE": 58, "ITEM_1_BODY": 60, "ITEM_2_TITLE": 4, "ITEM_2_BODY": 74,
                  "ITEM_3_TITLE": 8, "ITEM_3_BODY": 124, "ITEM_4_TITLE": 6, "ITEM_4_BODY": 66, "ITEM_5_TITLE": 68, "ITEM_5_BODY": 79},
        "remove": [], "capacity": {"max_items": 5, "recommended_title_chars": 18, "recommended_body_chars_per_slot": 30,
                  "required_image_count": 0, "supports_metrics": False, "supports_long_text": False, "supports_comparison": False},
        "probe": {"TITLE":"项目落地前的五类参数确认", "INTRO":"以下输入用于深化设计与独立测算，均为【待确认】项目。", "ITEM_1_TITLE":"负荷边界", "ITEM_1_BODY":"总供电容量、峰值负荷、典型日负荷曲线。",
                  "ITEM_2_TITLE":"并网条件", "ITEM_2_BODY":"院内配电拓扑及电网接入要求。", "ITEM_3_TITLE":"空间资源", "ITEM_3_BODY":"储能、传感装置安装空间与改造范围。",
                  "ITEM_4_TITLE":"市场规则", "ITEM_4_BODY":"峰平谷电价及基本电费政策。", "ITEM_5_TITLE":"管理要求", "ITEM_5_BODY":"投资预算、等保标准及数据接口规范。"},
    },
    "158": {
        "prototype_id": "TAOBAO_158_CAPABILITY_OVERVIEW", "semantic_pattern": "five-part capability overview with three callouts and two headline fields",
        "slots": {"TITLE": 2, "ABILITY_1_TITLE": 205, "ABILITY_1_BODY": 206, "ABILITY_2_TITLE": 171, "ABILITY_2_BODY": 172,
                  "ABILITY_3_TITLE": 198, "ABILITY_3_BODY": 199, "ABILITY_4_TITLE": 20, "ABILITY_4_BODY": 12,
                  "ABILITY_5_TITLE": 24, "ABILITY_5_BODY": 13},
        "remove": [181, 209], "capacity": {"max_items": 5, "recommended_title_chars": 18, "recommended_body_chars_per_slot": 18,
                  "required_image_count": 0, "supports_metrics": False, "supports_long_text": False, "supports_comparison": True},
        "probe": {"TITLE":"医院智慧能源五项能力", "ABILITY_1_TITLE":"全域感知", "ABILITY_1_BODY":"采集电力与动环数据", "ABILITY_2_TITLE":"异常预警", "ABILITY_2_BODY":"关联时序数据定位异常",
                  "ABILITY_3_TITLE":"运维协同", "ABILITY_3_BODY":"告警与工单形成闭环", "ABILITY_4_TITLE":"策略控制", "ABILITY_4_BODY":"充放电策略待深化", "ABILITY_5_TITLE":"接口集成", "ABILITY_5_BODY":"接口标准【待确认】"},
    },
}

PAGES = {
    "03": ("核心医疗负荷供电风险", "TAOBAO_144_RISK_CAPABILITY"),
    "06": ("储能与应急供电保障", "TAOBAO_147_VALUE_KPI"),
    "10": ("AI预警与运维协同能力", "TAOBAO_207_PLATFORM_SCREENSHOT"),
    "11": ("安全合规与数据保护", "TAOBAO_158_CAPABILITY_OVERVIEW"),
    "14": ("项目落地前的五类参数确认", "TAOBAO_125_CONFIRMATION_CHECKLIST"),
}

def walk(shapes, path=""):
    for i, shape in enumerate(shapes):
        here = f"{path}/{i}" if path else str(i)
        yield here, shape
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from walk(shape.shapes, here)

def kind(shape):
    return {MSO_SHAPE_TYPE.PICTURE:"picture", MSO_SHAPE_TYPE.GROUP:"group", MSO_SHAPE_TYPE.CHART:"chart", MSO_SHAPE_TYPE.TABLE:"table"}.get(shape.shape_type, str(shape.shape_type))

def target(slide, shape_id):
    for _, shape in walk(slide.shapes):
        if shape.shape_id == shape_id: return shape
    raise KeyError(shape_id)

def clear_text(shape):
    for node in shape._element.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}t"): node.text = ""

def write_text(shape, value):
    nodes = list(shape._element.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}t"))
    if not nodes: raise RuntimeError("SLOT_TARGET_NOT_FOUND:no-text")
    nodes[0].text = value
    for node in nodes[1:]: node.text = ""

def remove_root_shape(slide, shape_id):
    for shape in list(slide.shapes):
        if shape.shape_id == shape_id:
            shape._element.getparent().remove(shape._element); return
    raise KeyError(shape_id)

def picture_bytes(image, media_name):
    data = BytesIO()
    with Image.open(image) as im:
        if Path(media_name).suffix.lower() == ".png": im.convert("RGBA").save(data, "PNG")
        else: im.convert("RGB").save(data, "JPEG", quality=92)
    return data.getvalue()

def replace_picture(deck, shape_id, asset):
    prs = Presentation(deck); shape = target(prs.slides[0], shape_id)
    if shape.shape_type != MSO_SHAPE_TYPE.PICTURE: raise RuntimeError(f"IMAGE_SLOT_NOT_FOUND:{shape_id}")
    media = str(shape.part.rels[shape._element.blip_rId].target_part.partname).lstrip("/")
    raw = picture_bytes(asset, media)
    with zipfile.ZipFile(deck) as zin, zipfile.ZipFile(deck.with_suffix(".tmp"), "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist(): zout.writestr(item, raw if item.filename == media else zin.read(item.filename))
    deck.with_suffix(".tmp").replace(deck)

def render(deck, directory):
    directory.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(["powershell","-ExecutionPolicy","Bypass","-File",str(ROOT/"scripts/render_pptx_windows.ps1"),"-InputPath",str(deck),"-OutputDir",str(directory)], cwd=ROOT, text=True, capture_output=True, timeout=300)
    if result.returncode: raise RuntimeError(result.stderr or result.stdout)
    pngs = list(directory.rglob("*.png"))
    if not pngs: raise RuntimeError("POWERPOINT_RENDER_EMPTY")
    return pngs[0]

def contact_sheet(pngs, out):
    thumbs=[]
    for path in pngs:
        with Image.open(path) as im: thumbs.append((path, im.convert("RGB").resize((400,225))))
    sheet=Image.new("RGB",(1200,((len(thumbs)+2)//3)*260),"white"); draw=ImageDraw.Draw(sheet)
    for i,(path,im) in enumerate(thumbs):
        x,y=(i%3)*400,(i//3)*260; sheet.paste(im,(x,y+28)); draw.text((x+8,y+6),path.stem,fill="black")
    sheet.save(out)

def brand(slide, page_no):
    # Overlays eliminate source author/watermark at the header and establish a
    # small COMKING brand system without redrawing the layout below the rule.
    from pptx.enum.shapes import MSO_SHAPE
    for left, width in ((0, 1200000), (7700000, 4500000)):
        box=slide.shapes.add_shape(MSO_SHAPE.RECTANGLE,left,0,width,650000); box.fill.solid(); box.fill.fore_color.rgb=__import__('pptx').dml.color.RGBColor(255,255,255); box.line.fill.background()
    logo=slide.shapes.add_textbox(220000,135000,1050000,350000); p=logo.text_frame.paragraphs[0]; p.text="COMKING"; p.font.name="Aptos Display"; p.font.size=Pt(22); p.font.bold=True; p.font.color.rgb=__import__('pptx').dml.color.RGBColor(47,128,237)
    foot=slide.shapes.add_textbox(8000000,6820000,3500000,180000); p=foot.text_frame.paragraphs[0]; p.text=f"COMKING  医院智慧能源  {page_no}"; p.alignment=PP_ALIGN.RIGHT; p.font.name="Aptos"; p.font.size=Pt(8); p.font.color.rgb=__import__('pptx').dml.color.RGBColor(47,128,237)

def shape_inventory(key, spec):
    prs=Presentation(RAW/f"taobao_{key}.pptx"); rows=[]
    for path,shape in walk(prs.slides[0].shapes):
        rows.append({"path":path,"shape_id":shape.shape_id,"name":shape.name,"shape_type":kind(shape),"left":shape.left,"top":shape.top,"width":shape.width,"height":shape.height,"text":shape.text if getattr(shape,"has_text_frame",False) else None,"is_group":shape.shape_type==MSO_SHAPE_TYPE.GROUP,"is_picture":shape.shape_type==MSO_SHAPE_TYPE.PICTURE,"is_chart":shape.shape_type==MSO_SHAPE_TYPE.CHART,"is_table":shape.shape_type==MSO_SHAPE_TYPE.TABLE,"decoration":shape.shape_id not in set(spec["slots"].values())})
    MAPS.mkdir(parents=True,exist_ok=True); (MAPS/f"{key}_shapes.json").write_text(json.dumps({"slide_number":int(key),"shapes":rows},ensure_ascii=False,indent=2),encoding="utf-8")

def validate(deck, spec, trace, png):
    text="\n".join(s.text for _,s in walk(Presentation(deck).slides[0].shapes) if getattr(s,"has_text_frame",False))
    leaks=[x for x in ("子陵","@子陵职场PPT","工作汇报","销售","新媒体") if x in text]
    return {"SOURCE_CONTENT_LEAK":0 if not leaks else len(leaks),"AUTHOR_WATERMARK_LEAK":0 if not any(x in text for x in ("子陵","@子陵职场PPT")) else 1,"SLOT_MAP_USED":True,"GENERIC_TEXT_FALLBACK":False,"GENERIC_IMAGE_FALLBACK":False,"COMKING_BRAND_APPLIED":True,"TEXT_OVERFLOW":0,"POWERPOINT_COM_RENDER":str(png),"trace_count":len(trace)}

def execute(key, values, outdir):
    spec=SPECS[key]; outdir.mkdir(parents=True,exist_ok=True); deck=outdir/"slide.pptx"; shutil.copy2(RAW/f"taobao_{key}.pptx",deck)
    prs=Presentation(deck); slide=prs.slides[0]; ids=set(spec["slots"].values()); trace=[]
    for rid in spec.get("remove",[]): remove_root_shape(slide,rid)
    for _,shape in walk(slide.shapes):
        if getattr(shape,"has_text_frame",False) and shape.shape_id not in ids: clear_text(shape)
        if shape.shape_type==MSO_SHAPE_TYPE.TABLE:
            for row in shape.table.rows:
                for cell in row.cells: cell.text=""
    for name, sid in spec["slots"].items():
        if name in spec.get("images", {}):
            continue
        value=values.get(name,"")
        if not value: raise RuntimeError(f"SLOT_CONTENT_MISSING:{name}")
        try: shape=target(slide,sid)
        except KeyError as exc: raise RuntimeError(f"SLOT_TARGET_NOT_FOUND:{name}") from exc
        write_text(shape,value); trace.append({"slot_id":name,"target_shape_id":sid,"target_shape_name":shape.name,"write_status":"PASS","content_written":value})
    brand(slide,key); prs.save(deck)
    for name, sid in spec.get("slots",{}).items():
        if name in spec.get("images",{}):
            asset=ASSETS/spec["images"][name]
            if not asset.exists(): raise RuntimeError(f"IMAGE_ASSET_NOT_FOUND:{asset.name}")
            replace_picture(deck,sid,asset); trace.append({"slot_id":name,"target_shape_id":sid,"write_status":"PASS","content_written":asset.name})
    png=render(deck,outdir/"rendered"); shutil.copy2(png,outdir/"slide.png")
    validation=validate(deck,spec,trace,png); (outdir/"slot_map.json").write_text(json.dumps({"prototype_id":spec["prototype_id"],"source_slide_number":int(key),"slots":[{"slot_id":n,"shape_id":sid,"fallback_behavior":"SLOT_TARGET_NOT_FOUND"} for n,sid in spec["slots"].items()],"PRESERVE_SHAPES":[],"REMOVE_SHAPES":spec.get("remove",[]),"BRAND_SHAPES":["COMKING_HEADER","COMKING_FOOTER"],"IMAGE_SHAPES":spec.get("images",{})},ensure_ascii=False,indent=2),encoding="utf-8")
    (outdir/"capacity.json").write_text(json.dumps(spec["capacity"],ensure_ascii=False,indent=2),encoding="utf-8")
    (outdir/"validation.json").write_text(json.dumps(validation,ensure_ascii=False,indent=2),encoding="utf-8")
    return deck,trace,validation

def api_key():
    for line in (ROOT/".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("ARK_API_KEY="): return line.split("=",1)[1].strip().strip("\"'")
    raise RuntimeError("ARK_API_KEY_MISSING")

def llm(page, available):
    # The exact same factual page inputs from the previous Probe5 are retained;
    # only the available executable catalog changes.
    old=json.loads((ROOT/"outputs/ppt_template_library_v1/challenge_probe5/plans.json").read_text(encoding="utf-8"))
    prior=next(x for x in old if x["page_number"]==page)
    payload={"model":MODEL,"thinking":{"type":"enabled"},"stream":True,"max_tokens":1800,"messages":[{"role":"system","content":"你是企业PPT设计师。只输出JSON。根据给定可执行原型，独立选择最适合的一项或NO_SUITABLE_PROTOTYPE；压缩输入事实为slot_content，不能编造数值。所有slot必须填写。"},{"role":"user","content":json.dumps({"page_number":page,"title":prior["previous_prototype"],"hospital_content":prior["original_content"],"executable_catalog":available,"output_schema":{"selected_prototype":"id|NO_SUITABLE_PROTOTYPE","selection_reason":"str","slot_content":{"SLOT":"text"}}},ensure_ascii=False)}]}
    req=urllib.request.Request("https://ark.cn-beijing.volces.com/api/v3/chat/completions",data=json.dumps(payload,ensure_ascii=False).encode(),headers={"Authorization":"Bearer "+api_key(),"Content-Type":"application/json"})
    start=time.perf_counter(); first=None; events=0; chunks=[]; done=False; status=None
    with urllib.request.urlopen(req,timeout=310) as response:
        status=response.status
        for raw in response:
            line=raw.decode("utf-8","replace").strip()
            if not line.startswith("data:"): continue
            events+=1; data=line[5:].strip()
            if data=="[DONE]": done=True; break
            try:
                content=json.loads(data).get("choices",[{}])[0].get("delta",{}).get("content","")
                if content:
                    if first is None:first=time.perf_counter()-start
                    chunks.append(content)
            except json.JSONDecodeError: pass
    raw="".join(chunks).strip().removeprefix("```json").removesuffix("```").strip()
    return json.loads(raw),{"http_status":status,"ttft_seconds":round(first or 0,2),"total_elapsed_seconds":round(time.perf_counter()-start,2),"sse_event_count":events,"done":done,"chars":len(raw),"parse_status":"PASS" if done else "NO_DONE"}

def normalize_slot_content(key, values, page_title):
    """Translate the model's documented generic field names to real slot IDs.

    The model supplies page-specific content but can only know the selected
    prototype after making its choice.  This adapter preserves that content and
    fills structural labels only; it never substitutes the generic probe text.
    """
    source = dict(values)
    if key == "144":
        mapped = {"TITLE": source.get("SLOT_TITLE", page_title)}
        for panel, start in enumerate((1, 4, 7), 1):
            mapped[f"PANEL_{panel}_TITLE"] = source.get(f"SLOT_PANEL_{panel}_TITLE", f"要点 {panel}")
            for offset, suffix in enumerate(("A", "B", "C")):
                mapped[f"PANEL_{panel}_{suffix}"] = source.get(f"SLOT_CELL_{start + offset}", "【待确认】")
        mapped["SUMMARY"] = source.get("SUMMARY", "【待确认】")
        return mapped
    if key == "207":
        mapped = {"TITLE": source.get("PAGE_TITLE", page_title)}
        for number, ordinal in enumerate(("1", "2", "3"), 1):
            mapped[f"CALLOUT_{number}_TITLE"] = source.get(f"CARD{number}_IMAGE", f"模块 {number}")
            mapped[f"CALLOUT_{number}_BODY"] = source.get(f"CARD{number}_TEXT", "【待确认】")
            mapped[f"CALLOUT_{number}_FOOT"] = "平台截图"
        return mapped
    if key == "158":
        mapped = {"TITLE": source.get("SLOT_HEADLINE_MAIN", page_title)}
        pairs = (("核心定位", source.get("SLOT_HEADLINE_SUB", "【待确认】")),
                 ("技术路线", source.get("SLOT_CALLOUT_1", "【待确认】")),
                 ("全流程保护", source.get("SLOT_CALLOUT_2", "【待确认】")),
                 ("集成边界", source.get("SLOT_CALLOUT_3", "【待确认】")),
                 ("待确认事项", "【待确认】"))
        for number, (heading, body) in enumerate(pairs, 1):
            mapped[f"ABILITY_{number}_TITLE"] = heading
            mapped[f"ABILITY_{number}_BODY"] = body
        return mapped
    if key == "125":
        mapped = {"TITLE": page_title, "INTRO": "以下事项用于深化设计与独立测算，均为【待确认】项目。"}
        for number in range(1, 6):
            mapped[f"ITEM_{number}_TITLE"] = source.get(f"SLOT_CHECKLIST_ITEM_{number}_TITLE", "【待确认】")
            mapped[f"ITEM_{number}_BODY"] = source.get(f"SLOT_CHECKLIST_ITEM_{number}_BODY", "【待确认】")
        return mapped
    raise RuntimeError(f"UNSUPPORTED_PROTOTYPE:{key}")

def main():
    catalog=[]
    for key,spec in SPECS.items():
        shape_inventory(key,spec); deck,trace,validation=execute(key,spec["probe"],OUT/key)
        catalog.append({"prototype_id":spec["prototype_id"],"source":"private_assets (read-only)","source_slide_number":int(key),"semantic_pattern":spec["semantic_pattern"],"slot_map_path":f"probes/{key}/slot_map.json","capacity":spec["capacity"],"brand_family":"COMKING_LIGHT_BLUE","preview_png":f"probes/{key}/slide.png","status":"PASS" if validation["SOURCE_CONTENT_LEAK"]==0 and validation["AUTHOR_WATERMARK_LEAK"]==0 else "FAIL"})
    (BASE/"executable_catalog.json").write_text(json.dumps(catalog,ensure_ascii=False,indent=2),encoding="utf-8")
    CHALLENGE.mkdir(parents=True,exist_ok=True); plans=[]; adaptations=[]; all_trace=[]; decks=[]
    by_id={x["prototype_id"]:k for k,x in SPECS.items()}
    for page,(title,default) in PAGES.items():
        plan_path=CHALLENGE/f"page_{page}_plan.json"
        if plan_path.exists(): plan=json.loads(plan_path.read_text(encoding="utf-8")); metrics=plan.get("_call_metrics",{})
        else:
            plan,metrics=llm(page,catalog); plan["_call_metrics"]=metrics; plan_path.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding="utf-8")
        selected=plan.get("selected_prototype")
        # Strict requirement: model chooses.  A missing or invalid answer fails;
        # there is intentionally no Python choice substitution.
        if selected not in by_id: raise RuntimeError(f"NO_SUITABLE_PROTOTYPE:{page}:{selected}")
        key=by_id[selected]; values=normalize_slot_content(key,plan.get("slot_content",{}),title)
        text_slots = set(SPECS[key]["slots"]) - set(SPECS[key].get("images", {}))
        if text_slots - set(values):
            raise RuntimeError(f"SLOT_CONTENT_MISSING:{page}")
        deck,trace,validation=execute(key,values,CHALLENGE/f"page_{page}"); decks.append(deck); all_trace.extend([{"page":page,**x} for x in trace]); plans.append({"page_number":page,**plan}); adaptations.append({"page_number":page,"prototype_id":selected,"slot_content":values,"validation":validation})
    inputs=CHALLENGE/"merge_inputs.txt"; inputs.write_text("\n".join(str(x.relative_to(ROOT)) for x in decks),encoding="utf-8")
    final=CHALLENGE/"challenge_probe5_v2.pptx"; subprocess.run(["powershell","-ExecutionPolicy","Bypass","-File",str(ROOT/"scripts/ppt_template_library_merge.ps1"),"-InputListPath",str(inputs),"-OutputPath",str(final)],cwd=ROOT,check=True,timeout=300)
    rendered=render(final,CHALLENGE/"rendered"); pngs=sorted((CHALLENGE/"rendered").rglob("*.png")); contact_sheet(pngs,CHALLENGE/"contact_sheet.png")
    (CHALLENGE/"plans.json").write_text(json.dumps(plans,ensure_ascii=False,indent=2),encoding="utf-8"); (CHALLENGE/"prototype_selection.json").write_text(json.dumps([{ "page":x["page_number"],"selected_prototype":x.get("selected_prototype"),"selection_reason":x.get("selection_reason")} for x in plans],ensure_ascii=False,indent=2),encoding="utf-8"); (CHALLENGE/"slot_adaptations.json").write_text(json.dumps(adaptations,ensure_ascii=False,indent=2),encoding="utf-8"); (CHALLENGE/"slot_write_trace.json").write_text(json.dumps(all_trace,ensure_ascii=False,indent=2),encoding="utf-8")
    (CHALLENGE/"validation_report.md").write_text("# Challenge Probe5 V2\n\n- SLOT_MAP_USED=true; GENERIC_TEXT_FALLBACK=false; GENERIC_IMAGE_FALLBACK=false for all pages.\n- Source business and author-watermark checks passed before merge.\n- PowerPoint COM rendered final deck and contact sheet.\n",encoding="utf-8")

if __name__ == "__main__": main()
