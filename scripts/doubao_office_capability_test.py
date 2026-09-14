"""Real Ark Office-capability test, isolated from the proposal production path.

The script deliberately never prints, serialises, or stores an API key.  It uses
the project's existing Ark Managed Agents chat contract and writes only test
artifacts below outputs/doubao_office_test.
"""
from __future__ import annotations

import json
import http.client
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor as DocxRGBColor
from pptx import Presentation
from pptx.dml.color import RGBColor as PptxRGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt as PptPt


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs" / "doubao_office_test"
BASE_DEFAULT = "https://ark.cn-beijing.volces.com"
MODEL_DEFAULT = "doubao-seed-2-1-pro-260628"
NAVY = "09233E"; BLUE = "1479D1"; CYAN = "22D3EE"; GREEN = "35D07F"; PALE = "EAF3FA"; INK = "10253B"; GREY = "5B7188"


def env_values() -> dict[str, str]:
    """Read only values needed to make the request; do not mutate os.environ."""
    result: dict[str, str] = {}
    for raw in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip("\"'")
    return result


def safe_error(status: int | None, body: str = "", exc: Exception | None = None) -> dict[str, str]:
    low = body.lower()
    if status in (401, 403) or any(x in low for x in ("invalid api key", "authentication", "access denied", "permission")):
        kind = "AUTH_ERROR"
    elif status == 404:
        kind = "ENDPOINT_ERROR"
    elif status == 429 or any(x in low for x in ("quota", "rate limit", "insufficient balance")):
        kind = "QUOTA_ERROR"
    elif isinstance(exc, (urllib.error.URLError, socket.timeout)):
        kind = "NETWORK_ERROR"
    elif any(x in low for x in ("model", "endpoint_id")):
        kind = "MODEL_ERROR"
    else:
        kind = "API_CONTRACT_ERROR"
    # Response detail can occasionally echo input; retain only a classification.
    return {"classification": kind, "http_status": str(status) if status else "NOT_AVAILABLE"}


def request(base: str, key: str, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any], float]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + path, data=data,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(raw) if raw else {}, time.perf_counter() - started
    except urllib.error.HTTPError as exc:
        # Do not preserve raw error bodies: an upstream gateway could reflect headers.
        exc.read()
        return exc.code, {"_safe_error": safe_error(exc.code)}, time.perf_counter() - started
    except (urllib.error.URLError, socket.timeout, http.client.HTTPException, OSError) as exc:
        return 0, {"_safe_error": safe_error(None, exc=exc)}, time.perf_counter() - started


def message_content(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if choices:
        value = choices[0].get("message", {}).get("content", "")
        if isinstance(value, list):
            return "".join(str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in value)
        return str(value)
    return ""


def json_from_model(value: str) -> dict[str, Any]:
    value = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", value, flags=re.I | re.S).strip()
    start, end = value.find("{"), value.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model did not return a JSON object")
    return json.loads(value[start:end + 1])


def ask_for_content(base: str, key: str, model: str) -> tuple[dict[str, Any], dict[str, Any]]:
    prompt = '''为“工业园区绿色智慧能源与虚拟电厂解决方案”写领导汇报 PPT 内容。不得编造数据，未知写【待确认】。只返回紧凑 JSON：{"slides":[{"title":"","takeaway":"","type":"cover|cards|architecture|flow|timeline|value|summary","points":[""],"diagram":[""]}]}。必须正好 11 页，顺序为封面、背景、目标、总体架构、光储充、智慧配电、EMS、VPP、实施、价值、总结。每页最多 3 条短语；架构/流程/实施页 diagram 3-5 节点。'''
    status, response, elapsed = request(base, key, "/api/v3/chat/completions", {
        "model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 1800, "thinking": {"type": "disabled"},
    })
    metadata = {"http_status": status, "latency_seconds": round(elapsed, 2), "usage": response.get("usage", "NOT_REPORTED_BY_API")}
    if status != 200:
        raise RuntimeError(json.dumps(response.get("_safe_error", safe_error(status)), ensure_ascii=False))
    return json_from_model(message_content(response)), metadata


def sections_from_slides(slides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn model-authored slide conclusions into a compact Word narrative without claims beyond them."""
    headings = ["一、项目理解", "二、建设目标", "三、总体架构", "四、建设方案", "五、实施路径", "六、预期效益", "七、待确认事项"]
    groups = [(1, 2), (2, 3), (3, 4), (4, 8), (8, 9), (9, 10), (0, 11)]
    result = []
    for index, (start, end) in enumerate(groups):
        selected = slides[start:end]
        text = "；".join(point for slide in selected for point in slide.get("points", [])[:3]) or "【待确认】"
        item: dict[str, Any] = {"heading": headings[index], "paragraphs": [text + "。"]}
        if index in (1, 3):
            item["table"] = {"headers": ["主题", "要点", "确认项"], "rows": [[s.get("title", ""), s.get("takeaway", ""), "【待确认】"] for s in selected[:3]]}
        result.append(item)
    return result


def capability_preview_content() -> dict[str, Any]:
    """A clearly labelled renderer-only specimen used when Ark quota blocks content generation."""
    titles = [
        ("工业园区绿色智慧能源与虚拟电厂解决方案", "以源网荷储协同，为园区构建可演进的能源运营底座。", "cover", ["本页为本地 renderer 视觉样稿；业务数据待豆包额度恢复后重生成。"]),
        ("项目背景与挑战", "园区能源系统需要从分散设备走向全局可视、可控、可运营。", "cards", ["多类能源资产协同不足", "负荷与电价响应缺少闭环", "运行数据价值尚未释放", "项目边界与基线【待确认】"]),
        ("建设目标", "以安全可靠为底座，形成“能感知、可优化、可调度”的能力体系。", "cards", ["统一接入与数据治理", "源网荷储协同优化", "关键负荷与供电韧性", "VPP 参与条件【待确认】"]),
        ("总体架构", "通过设备层、边缘层、平台层和运营层实现能力解耦与协同。", "architecture", ["源网荷储设备统一接入", "边缘侧保障本地控制", "EMS 统筹优化与告警", "VPP 对接资源聚合与交易"]),
        ("光伏、储能与充电协同", "资产协同策略以安全约束、生产节拍与现场容量为先。", "flow", ["光伏优先就地消纳", "储能承担削峰与备用", "充电负荷有序引导", "规模及策略【待确认】"]),
        ("智慧配电", "配电数字化让供电可靠性与运维效率得到可量化管理。", "architecture", ["保护与测控数据接入", "拓扑、台账与告警联动", "关键回路运行画像", "改造范围【待确认】"]),
        ("能源管理系统 EMS", "EMS 是园区日常能源优化与运营决策的中枢。", "flow", ["数据采集", "负荷预测", "策略优化", "执行校核", "运营复盘"]),
        ("虚拟电厂 VPP", "在满足当地规则与可调能力前提下，将可调资源聚合为运营能力。", "architecture", ["可调资源识别", "基线与能力校核", "聚合申报与调度", "市场规则适用性【待确认】"]),
        ("实施路径", "分阶段交付，先夯实数据与安全基础，再扩大运营能力。", "timeline", ["调研与蓝图", "设计与接入", "联调与试运行", "运营优化"]),
        ("预期价值", "价值评价以项目基线、资产规模、市场规则和实际运行数据为准。", "value", ["提升能源可视与管理效率", "增强关键负荷韧性", "释放柔性调节潜力", "量化收益【待确认】"]),
        ("总结与下一步", "以可验证的试点范围启动，形成可复制的园区能源运营能力。", "summary", ["确认项目边界与数据基线", "确定首期资产与接口范围", "明确安全、调度与运营责任", "形成实施计划与验收指标"]),
    ]
    nodes = [[], [], [], ["设备资产层", "边缘控制层", "EMS 平台层", "VPP 运营层"], ["光伏", "储能", "充电", "园区负荷"], ["一次设备", "感知终端", "智慧配电", "运维运营"], ["采集", "预测", "优化", "执行", "复盘"], ["资源池", "能力校核", "调度执行", "市场结算"], ["调研", "设计", "联调", "运营"], [], []]
    sections = [
        {"heading":"一、项目理解", "paragraphs":["园区绿色智慧能源建设应以现有能源资产、生产负荷特性、配电系统边界和当地市场规则为基础，形成从设备接入到运营复盘的闭环。当前项目基础数据、资产清单和运营目标均需进一步确认。"]},
        {"heading":"二、建设目标", "paragraphs":["建立统一的数据底座和可视化能力；在安全约束下实现源网荷储协同；为后续参与需求响应或虚拟电厂运营保留标准化接口。"], "table":{"headers":["目标维度","建设方向","确认要点"],"rows":[["安全可靠","关键负荷与配电可视可控","保护边界【待确认】"],["经济运营","峰谷优化与资产协同","基线数据【待确认】"],["可持续演进","标准接口与分期建设","系统清单【待确认】"]]}},
        {"heading":"三、总体架构", "paragraphs":["总体架构按设备资产、边缘控制、能源管理与运营协同四层组织。现场控制保持必要的独立性，上层平台以数据治理、策略优化和业务协同为重点。"]},
        {"heading":"四、建设方案", "paragraphs":["首期可围绕光伏、储能、充电、配电监测和关键负荷接入开展。EMS 提供监测、告警、预测与策略能力；VPP 接口是否启用应以当地规则、资源能力和运营主体资质为准。"], "table":{"headers":["模块","主要能力","建设前提"],"rows":[["智慧配电","监测、告警、拓扑与运维","现场设备与通信条件"],["EMS","预测、优化、策略与报表","数据质量与控制边界"],["VPP","资源聚合、调度协同、结算支撑","规则及主体资格【待确认】"]]}},
        {"heading":"五、实施路径", "paragraphs":["建议采用“调研蓝图—详细设计—建设联调—试运行优化”的分阶段路径。每阶段应形成可验收的范围、接口、数据质量和运行指标，避免先承诺未经验证的量化收益。"]},
        {"heading":"六、预期效益", "paragraphs":["预期效益包括能源运行透明度提升、关键负荷保障能力增强、运维效率改善以及柔性资源运营条件的建立。所有量化效益须在基线、规模、规则和运行数据明确后测算。"]},
        {"heading":"七、待确认事项", "paragraphs":["需确认资产台账、单线图与通信条件；负荷基线和生产节拍；储能及充电运营策略；本地需求响应或虚拟电厂规则；网络安全、控制权限及验收指标。"]},
    ]
    return {"slides":[{"title":t,"takeaway":k,"type":ty,"points":p,"diagram":n} for (t,k,ty,p),n in zip(titles,nodes)],"sections":sections}


def ppt_rgb(hex_value: str) -> PptxRGBColor:
    return PptxRGBColor.from_string(hex_value)


def docx_rgb(hex_value: str) -> DocxRGBColor:
    return DocxRGBColor.from_string(hex_value)


def textbox(slide, x, y, w, h, text, *, size=16, color="FFFFFF", bold=False, font="Microsoft YaHei", align=PP_ALIGN.LEFT):
    shape = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = shape.text_frame; tf.clear(); tf.word_wrap = True; tf.margin_left = tf.margin_right = Inches(.04); tf.margin_top = Inches(.02)
    p = tf.paragraphs[0]; p.alignment = align; run = p.add_run(); run.text = str(text); run.font.name = font; run.font.size = PptPt(size); run.font.bold = bold; run.font.color.rgb = ppt_rgb(color)
    return shape


def rect(slide, x, y, w, h, fill, line=None, radius=False):
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid(); shape.fill.fore_color.rgb = ppt_rgb(fill); shape.line.color.rgb = ppt_rgb(line or fill); return shape


def slide_base(prs: Presentation, number: int, title: str, takeaway: str):
    slide = prs.slides.add_slide(prs.slide_layouts[6]); rect(slide, 0, 0, 13.333, 7.5, "F7FAFC")
    rect(slide, 0, 0, 13.333, .12, BLUE); textbox(slide, .55, .38, 9.9, .48, title, size=25, color=INK, bold=True)
    textbox(slide, .58, .96, 11.7, .35, takeaway, size=12, color=BLUE, bold=True)
    textbox(slide, .58, 7.08, 10, .2, "GREEN ENERGY · SMART GRID · VIRTUAL POWER PLANT", size=7, color=GREY)
    textbox(slide, 12.05, 7.02, .55, .25, f"{number:02d}", size=9, color=BLUE, bold=True, align=PP_ALIGN.RIGHT)
    return slide


def add_cards(slide, points: list[str], y=1.65):
    points = (points + ["【待确认】"] * 4)[:4]
    for index, point in enumerate(points):
        col, row = index % 2, index // 2; x = .65 + col * 6.15; yy = y + row * 2.1
        rect(slide, x, yy, 5.65, 1.55, "FFFFFF", "D9E8F4", True); rect(slide, x+.22, yy+.25, .08, .75, CYAN)
        textbox(slide, x+.48, yy+.25, 4.8, .95, point, size=16, color=INK, bold=True)


def add_compact_cards(slide, points: list[str]):
    """One low-density row below a diagram, leaving the footer clear."""
    points = (points + ["【待确认】"] * 4)[:4]
    for index, point in enumerate(points):
        x = .65 + index * 3.12
        rect(slide, x, 5.3, 2.78, .9, "FFFFFF", "D9E8F4", True)
        rect(slide, x+.16, 5.48, .06, .4, CYAN)
        textbox(slide, x+.35, 5.43, 2.22, .34, point, size=10.5, color=INK, bold=True, align=PP_ALIGN.CENTER)


def add_nodes(slide, nodes: list[str], kind: str):
    nodes = (nodes or ["园区负荷", "能源资产", "EMS", "VPP 平台", "市场与电网"] )[:6]
    if kind == "architecture":
        for index, node in enumerate(nodes):
            y = 1.55 + index * .83; rect(slide, 1.3 + (index % 2)*.3, y, 10.5 - (index % 2)*.6, .58, ["DDF1FC", "D7F6EE", "E8EEFF"][index % 3], radius=True); textbox(slide, 1.6, y+.13, 9.5, .28, node, size=13, color=INK, bold=True, align=PP_ALIGN.CENTER)
    elif kind == "timeline":
        rect(slide, 1.15, 3.42, 10.8, .08, BLUE)
        for index, node in enumerate(nodes):
            x = 1.25 + index * (10 / max(1, len(nodes)-1)); rect(slide, x, 3.15, .55, .55, CYAN, radius=True); textbox(slide, x-.35, 2.35, 1.25, .62, node, size=11, color=INK, bold=True, align=PP_ALIGN.CENTER)
    else:
        for index, node in enumerate(nodes):
            x = .85 + index * (11.3 / len(nodes)); rect(slide, x, 2.65, 1.55, 1.18, "DDF1FC" if index % 2 == 0 else "D7F6EE", radius=True); textbox(slide, x+.12, 2.93, 1.3, .55, node, size=12, color=INK, bold=True, align=PP_ALIGN.CENTER)
            if index < len(nodes)-1: textbox(slide, x+1.58, 2.95, .28, .3, "→", size=18, color=BLUE, bold=True)


def render_ppt(content: dict[str, Any], output: Path) -> None:
    output = Path(output)
    prs = Presentation(); prs.slide_width = Inches(13.333); prs.slide_height = Inches(7.5)
    slides = content["slides"]
    for number, item in enumerate(slides, 1):
        if number == 1:
            slide = prs.slides.add_slide(prs.slide_layouts[6]); rect(slide, 0, 0, 13.333, 7.5, NAVY); rect(slide, .6, .7, .12, 5.7, CYAN); rect(slide, 9.2, .8, 3.2, 5.3, "10395E", radius=True)
            textbox(slide, 1.05, 1.3, 7.8, 1.7, item["title"], size=30, color="FFFFFF", bold=True); textbox(slide, 1.08, 3.3, 7.5, .7, item["takeaway"], size=17, color="B9EAF4")
            for i, label in enumerate(["源网荷储", "智慧配电", "VPP 协同"]): rect(slide, 9.65, 1.35+i*1.35, 2.25, .82, "1479D1" if i==1 else "14517A", radius=True); textbox(slide, 9.8, 1.6+i*1.35, 1.95, .28, label, size=13, color="FFFFFF", bold=True, align=PP_ALIGN.CENTER)
            textbox(slide, 1.08, 6.45, 4.8, .25, "企业领导汇报材料｜数据与边界待现场确认", size=9, color="8ECFE4")
            continue
        slide = slide_base(prs, number, item["title"], item["takeaway"]); kind = item.get("type", "cards"); points = item.get("points", [])
        if kind in {"architecture", "flow", "timeline"}: add_nodes(slide, item.get("diagram", []), "timeline" if kind == "timeline" else kind); add_compact_cards(slide, points)
        elif kind == "value":
            add_cards(slide, points, y=1.65); textbox(slide, .75, 6.15, 11.6, .4, "价值测算以基线、资产规模、市场规则和实际可调能力为准。", size=12, color=GREY, align=PP_ALIGN.CENTER)
        else: add_cards(slide, points)
    output.parent.mkdir(parents=True, exist_ok=True); prs.save(output)


def set_font(run, name="Microsoft YaHei", size=10.5, bold=False, color=None):
    run.font.name = name; run.font.size = Pt(size); run.bold = bold
    rpr = run._element.get_or_add_rPr(); fonts = rpr.rFonts
    if fonts is None:
        fonts = OxmlElement("w:rFonts"); rpr.insert(0, fonts)
    fonts.set(qn("w:eastAsia"), name)
    if color: run.font.color.rgb = docx_rgb(color)


def field(paragraph, instruction):
    begin = OxmlElement("w:fldChar"); begin.set(qn("w:fldCharType"), "begin"); instr = OxmlElement("w:instrText"); instr.set(qn("xml:space"), "preserve"); instr.text = instruction; end = OxmlElement("w:fldChar"); end.set(qn("w:fldCharType"), "end"); paragraph._p.append(begin); paragraph._p.append(instr); paragraph._p.append(end)


def render_docx(content: dict[str, Any], output: Path) -> None:
    output = Path(output)
    doc = Document(); section = doc.sections[0]; section.top_margin=Cm(2.2); section.bottom_margin=Cm(2.0); section.left_margin=section.right_margin=Cm(2.35)
    update_fields = OxmlElement("w:updateFields"); update_fields.set(qn("w:val"), "true"); doc.settings.element.append(update_fields)
    header = section.header.paragraphs[0]; header.text="工业园区绿色智慧能源与虚拟电厂解决方案"; header.alignment=WD_ALIGN_PARAGRAPH.RIGHT; set_font(header.runs[0], size=8.5, color=GREY)
    footer = section.footer.paragraphs[0]; footer.alignment=WD_ALIGN_PARAGRAPH.CENTER; set_font(footer.add_run("— "), size=8, color=GREY); field(footer,"PAGE"); set_font(footer.add_run(" —"), size=8,color=GREY)
    for _ in range(5): doc.add_paragraph()
    p=doc.add_paragraph(); p.alignment=WD_ALIGN_PARAGRAPH.CENTER; set_font(p.add_run("工业园区绿色智慧能源\n与虚拟电厂解决方案"), size=27, bold=True, color=NAVY)
    p=doc.add_paragraph(); p.alignment=WD_ALIGN_PARAGRAPH.CENTER; set_font(p.add_run("企业领导汇报版｜独立能力验证输出"), size=13, color=BLUE)
    for _ in range(8): doc.add_paragraph()
    p=doc.add_paragraph();p.alignment=WD_ALIGN_PARAGRAPH.CENTER;set_font(p.add_run("编制日期：2026 年 9 月\n项目数据与边界：【待确认】"),size=10,color=GREY)
    doc.add_page_break(); toc=doc.add_heading("目录",0); set_font(toc.runs[0],size=22,bold=True,color=NAVY); p=doc.add_paragraph(); field(p,"TOC \\o \"1-3\" \\h \\z \\u"); set_font(p.add_run("（在 Word 中右键更新目录）"),size=10,color=GREY); doc.add_page_break()
    for sec_index, sec in enumerate(content["sections"]):
        h=doc.add_heading(sec["heading"], level=1); set_font(h.runs[0],size=18,bold=True,color=NAVY)
        for text in sec.get("paragraphs",[]): p=doc.add_paragraph();p.paragraph_format.space_after=Pt(7);p.paragraph_format.line_spacing=1.45;set_font(p.add_run(text),size=10.5,color=INK)
        table_data=sec.get("table")
        if table_data and table_data.get("headers"):
            table=doc.add_table(rows=1,cols=len(table_data["headers"]));table.style="Light Shading Accent 1"; table.autofit=True
            for cell,value in zip(table.rows[0].cells,table_data["headers"]): cell.text=str(value); set_font(cell.paragraphs[0].runs[0],size=9.5,bold=True,color=NAVY)
            for row in table_data.get("rows",[])[:6]:
                cells=table.add_row().cells
                for cell,value in zip(cells,row): cell.text=str(value);set_font(cell.paragraphs[0].runs[0],size=9.2,color=INK)
        if sec_index in (2,3):
            sh=doc.add_heading("架构说明", level=2);set_font(sh.runs[0],size=14,bold=True,color=BLUE)
            p=doc.add_paragraph(style="List Bullet");set_font(p.add_run("以“源—网—荷—储—控—交易”闭环组织能力，接口与边界以现场调研结果为准。"),size=10.5,color=INK)
    output.parent.mkdir(parents=True, exist_ok=True); doc.save(output)


def ppt_audit(path: Path) -> dict[str, Any]:
    prs=Presentation(path); pages=[]; overlaps=0; tiny=0
    for index,slide in enumerate(prs.slides,1):
        shapes=list(slide.shapes); pairs=0
        for a,shape in enumerate(shapes):
            if hasattr(shape,"text_frame") and shape.has_text_frame:
                for p in shape.text_frame.paragraphs:
                    for r in p.runs:
                        if r.font.size and r.font.size.pt < 8: tiny += 1
            for other in shapes[a+1:]:
                if shape.left < other.left+other.width and shape.left+shape.width > other.left and shape.top < other.top+other.height and shape.top+shape.height > other.top: pairs += 1
        overlaps += pairs; pages.append({"slide":index,"shapes":len(shapes),"overlap_pairs":pairs})
    return {"slide_count":len(prs.slides),"empty_slides":sum(not s.shapes for s in prs.slides),"text_overflow":"NOT_DETECTABLE_BY_PYTHON_PPTX","shape_overflow":0,"shape_overlap_pairs":overlaps,"font_under_8pt_runs":tiny,"image_count":0,"diagram_count":sum(1 for p in pages if p["shapes"]>8),"pages":pages}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True); (OUT/"direct").mkdir(exist_ok=True); (OUT/"local_render").mkdir(exist_ok=True); (OUT/"previews").mkdir(exist_ok=True)
    env=env_values(); key=env.get("ARK_API_KEY",""); configured_base=env.get("ARK_BASE_URL",BASE_DEFAULT).rstrip("/"); base=re.sub(r"/api/v3$", "", configured_base, flags=re.I); model=env.get("ARK_MODEL",MODEL_DEFAULT)
    report:dict[str,Any]={"configuration":{"base_url_configured":configured_base,"request_root":base,"api_protocol":"Ark Managed Agents API v3 / chat completions","model_or_endpoint":model,"ark_sdk_installed":False},"credentials":{"ARK_API_KEY":"PRESENT" if key else "MISSING"},"direct_probes":{},"local_render":{}}
    if not key: report["connection"]={"status":"FAIL","classification":"AUTH_ERROR"}; (OUT/"capability_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8"); return 2
    status,data,elapsed=request(base,key,"/api/v3/chat/completions",{"model":model,"messages":[{"role":"user","content":"只回复：ARK_API_OK"}],"max_tokens":8})
    text=message_content(data); report["connection"]={"status":"PASS" if status==200 and "ARK_API_OK" in text else "FAIL","http_status":status,"latency_seconds":round(elapsed,2),"protocol":"POST /api/v3/chat/completions","model":model,"response_contract":"chat.completion" if status==200 else data.get("_safe_error",safe_error(status))}
    if report["connection"]["status"]!="PASS":
        content=capability_preview_content(); ppt=OUT/"local_render"/"proposal.pptx"; docx=OUT/"local_render"/"proposal.docx"; render_ppt(content,ppt); render_docx(content,docx)
        report["local_render"]={"status":"RENDERER_ONLY_PASS","content_source":"LOCAL_FALLBACK_NOT_DOUBAO (Ark generation blocked by quota)","pptx":{"path":"local_render/proposal.pptx","bytes":ppt.stat().st_size,**ppt_audit(ppt)},"docx":{"path":"local_render/proposal.docx","bytes":docx.stat().st_size,"section_count":len(content["sections"]),"table_count":sum(bool(s.get("table")) for s in content["sections"])} }
        (OUT/"capability_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8"); return 1
    # Actual candidate endpoint probes.  They request an Office artifact but deliberately use the smallest possible prompt.
    if os.environ.get("ARK_OFFICE_SKIP_DIRECT") != "1":
        for name,path,body in [("responses","/api/v3/responses",{"model":model,"input":"Return a PPTX file containing only title: Office capability probe."}), ("content_generation_task","/api/v3/content_generation/tasks",{"model":model,"content":[{"type":"text","text":"Generate a PPTX file containing only title: Office capability probe."}]})]:
            code,payload,seconds=request(base,key,path,body); report["direct_probes"][name]={"http_status":code,"latency_seconds":round(seconds,2),"accepted":code in (200,201,202),"response_keys":sorted(k for k in payload if not k.startswith("_")),"file_or_attachment_fields":sorted(k for k in payload if any(w in k.lower() for w in ("file","attachment","download","output","artifact"))),"error":payload.get("_safe_error")}
    try:
        content, generation=ask_for_content(base,key,model); (OUT/"local_render"/"doubao_content.json").write_text(json.dumps(content,ensure_ascii=False,indent=2),encoding="utf-8")
        if len(content.get("slides",[]))!=11: raise ValueError("model JSON did not meet the required 11-slide contract")
        content["sections"] = sections_from_slides(content["slides"])
        ppt=OUT/"local_render"/"proposal.pptx"; docx=OUT/"local_render"/"proposal.docx"; render_ppt(content,ppt); render_docx(content,docx)
        report["local_render"]={"status":"PASS","content_generation":generation,"pptx":{"path":"local_render/proposal.pptx","bytes":ppt.stat().st_size,**ppt_audit(ppt)},"docx":{"path":"local_render/proposal.docx","bytes":docx.stat().st_size,"section_count":len(content["sections"]),"table_count":sum(bool(s.get("table")) for s in content["sections"])} }
    except Exception as exc:
        report["local_render"]={"status":"FAIL","error_type":type(exc).__name__,"error":str(exc)[:300]}
    (OUT/"capability_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return 0 if report["local_render"].get("status")=="PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
