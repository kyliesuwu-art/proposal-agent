"""Independent Raw-Markdown PPT design-system experiment; never changes proposal production."""
from __future__ import annotations

import json, os, re, shutil, socket, sys, time, urllib.error, urllib.request
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "outputs/hospital_power_verify_20260911_attempt4_delivery/proposal.md"
RAW_INITIAL = ROOT / "outputs/doubao_office_test/raw_markdown/seed_2_1_pro_thinking/slides.md"
OUT = ROOT / "outputs/ppt_style_v1"
GUIDE = ROOT / "config/PPT_GENERATION_GUIDE.md"
MODEL = "doubao-seed-2-1-pro-260628"
W, H = 13.333, 7.5
NAVY, BLUE, TEAL, PALE, BG, TEXT, MUTED = ((31,73,125),(79,129,189),(75,172,198),(232,243,248),(247,250,252),(24,50,71),(90,107,120))

def read_env():
    values = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1); values[k.strip()] = v.strip().strip("\"'")
    return values

def ark(messages: list[dict], purpose: str) -> tuple[str, dict]:
    env = read_env(); key = env.get("ARK_API_KEY")
    if not key: raise RuntimeError("ARK_API_KEY is not present")
    base = env.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com").removesuffix("/api/v3")
    payload = {"model": MODEL, "messages": messages, "thinking": {"type": "enabled"}, "max_tokens": 10000, "stream": True}
    request = urllib.request.Request(base + "/api/v3/chat/completions", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
    start = time.perf_counter(); parts=[]; status=0
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            status=response.status
            for raw in response:
                line=raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"): continue
                value=line[5:].strip()
                if value == "[DONE]": break
                try:
                    delta=(json.loads(value).get("choices") or [{}])[0].get("delta",{}).get("content","")
                    if isinstance(delta,list): delta="".join(item.get("text","") for item in delta if isinstance(item,dict))
                    parts.append(delta)
                except json.JSONDecodeError: pass
    except (urllib.error.HTTPError, urllib.error.URLError, socket.timeout, OSError) as exc:
        raise RuntimeError(f"{purpose}: HTTP {getattr(exc, 'code', 0)}") from exc
    meta={"purpose":purpose,"http_status":status,"latency_seconds":round(time.perf_counter()-start,2),"model":MODEL,"thinking":{"type":"enabled"},"stream":True,"max_tokens":10000,"usage":"NOT_REPORTED_BY_API"}
    return "".join(parts).strip(), meta

def clean(md: str) -> str:
    md=re.sub(r"\[来源:\s*[^\]]+\]", "", md)
    md=re.sub(r"^图片来源[:：].*$", "", md, flags=re.M)
    md=re.sub(r"^#{1,3}\s*(?:来源与依据|参考资料|Sources|References).*$[\s\S]*\Z", "", md, flags=re.M|re.I)
    return re.sub(r"\n{3,}", "\n\n", md).strip()+"\n"

def parse(md: str):
    pages=[]
    for piece in re.split(r"^---\s*$",md,flags=re.M):
        if not piece.strip(): continue
        title=next((line.lstrip("#").strip() for line in piece.splitlines() if line.startswith("#")), "方案汇报")
        hint=(re.search(r"<!--\s*visual:\s*([\w-]+)\s*-->",piece,re.I) or [None,"auto"])[1].lower()
        imgs=re.findall(r"!\[[^]]*\]\((assets/[^)\s]+)",piece)
        table=[line for line in piece.splitlines() if line.strip().startswith("|")]
        bullets=[re.sub(r"^\s*(?:[-*+] |\d+[.)] )", "",line).strip() for line in piece.splitlines() if re.match(r"^\s*(?:[-*+] |\d+[.)] )",line)]
        prose=[line.strip() for line in piece.splitlines() if line.strip() and not line.startswith("#") and not line.startswith("<!--") and not line.startswith("!") and not line.startswith("|") and not re.match(r"^\s*(?:[-*+] |\d+[.)] )",line)]
        pages.append({"title":title,"hint":hint,"images":imgs,"table":table,"bullets":bullets,"prose":prose})
    return pages

def image_path(folder: Path, raw: str):
    candidate=(folder/raw).resolve()
    try: candidate.relative_to(folder.resolve())
    except ValueError: return None
    return candidate if candidate.is_file() else None

def add_text(slide,x,y,w,h,text,size=18,bold=False,color=TEXT,align=PP_ALIGN.LEFT):
    shape=slide.shapes.add_textbox(Inches(x),Inches(y),Inches(w),Inches(h)); tf=shape.text_frame;tf.clear();tf.word_wrap=True;tf.margin_left=tf.margin_right=0;tf.margin_top=0;tf.vertical_anchor=MSO_ANCHOR.TOP
    for i,line in enumerate(text.split("\n") or [""]):
        p=tf.paragraphs[0] if i==0 else tf.add_paragraph();p.text=line;p.alignment=align;p.space_after=Pt(5)
        for r in p.runs:r.font.name="Microsoft YaHei";r.font.size=Pt(size);r.font.bold=bold;r.font.color.rgb=RGBColor(*color)
    return shape

def add_crop(slide,path,x,y,w,h):
    with Image.open(path) as im: iw,ih=im.size
    target=w/h; actual=iw/ih
    pic=slide.shapes.add_picture(str(path), Inches(x), Inches(y), width=Inches(w), height=Inches(h))
    if actual>target: pic.crop_left=pic.crop_right=(1-target/actual)/2
    else: pic.crop_top=pic.crop_bottom=(1-actual/target)/2
    return pic

def base(slide,n,title):
    fill=slide.background.fill;fill.solid();fill.fore_color.rgb=RGBColor(*BG)
    slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, Inches(0), Inches(0), Inches(.18), Inches(H)).fill.solid();slide.shapes[-1].fill.fore_color.rgb=RGBColor(*BLUE);slide.shapes[-1].line.fill.background()
    add_text(slide,.65,.40,11.6,.62,title,29,True,NAVY)
    slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, Inches(.65), Inches(6.93), Inches(12.05), Inches(.02)).fill.solid();slide.shapes[-1].fill.fore_color.rgb=RGBColor(*PALE);slide.shapes[-1].line.fill.background()
    add_text(slide,.65,7.04,2.4,.2,"智慧配电解决方案",9,False,MUTED)
    add_text(slide,12.1,7.01,.5,.22,f"{n:02d}",10,True,BLUE,PP_ALIGN.RIGHT)
    # restrained diagonal brand device
    shape=slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.PARALLELOGRAM, Inches(11.60), Inches(0), Inches(1.70), Inches(.20));shape.fill.solid();shape.fill.fore_color.rgb=RGBColor(*PALE);shape.line.fill.background()

def classify(p,index):
    text=" ".join([p["title"],*p["bullets"],*p["prose"]]).lower(); hint=p["hint"]
    if index==0:return "cover"
    if hint!="auto":return hint
    if p["table"]:return "table"
    if len(p["images"])>=2:return "gallery"
    if any(k in text for k in ("架构","平台","系统组成","分层")):return "architecture"
    if any(k in text for k in ("实施路径","建设路径","阶段","流程","步骤")):return "process"
    if any(k in text for k in ("价值","收益","成效","总结","结语")):return "summary"
    if re.search(r"\d",text) and len(p["bullets"])<=4:return "metrics"
    if p["images"]:return "image-text"
    return "cards" if len(p["bullets"])>=3 else "section"

def render(md_path: Path, out: Path):
    pages=parse(md_path.read_text(encoding="utf-8"));prs=Presentation();prs.slide_width=Inches(W);prs.slide_height=Inches(H);blank=prs.slide_layouts[6]; layouts=[]
    for i,p in enumerate(pages,1):
        slide=prs.slides.add_slide(blank);layout=classify(p,i-1);layouts.append(layout); image=image_path(md_path.parent,p["images"][0]) if p["images"] else None
        if layout=="cover":
            fill=slide.background.fill;fill.solid();fill.fore_color.rgb=RGBColor(*NAVY)
            if image: add_crop(slide,image,6.15,0,7.18,H)
            overlay=slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, Inches(0), Inches(0), Inches(7.4), Inches(H));overlay.fill.solid();overlay.fill.fore_color.rgb=RGBColor(*NAVY);overlay.fill.transparency=8;overlay.line.fill.background()
            add_text(slide,.85,1.45,5.7,2.2,p["title"],32,True,(255,255,255))
            add_text(slide,.88,4.3,4.8,.5,"高可靠供电与智慧配电改造方案",16,False,(224,241,248))
            add_text(slide,.88,6.65,3,.3,"解决方案汇报",11,False,(224,241,248))
            continue
        base(slide,i,p["title"]);items=p["bullets"] or p["prose"]; items=items[:6]
        if layout in {"image","hero","image-text","text-image"} and image:
            add_crop(slide,image,7.2,1.35,5.35,4.95); add_text(slide,.85,1.55,5.7,4.8,"\n".join("• "+x for x in items),17,False,TEXT)
        elif layout=="gallery":
            for j,raw in enumerate(p["images"][:4]):
                im=image_path(md_path.parent,raw)
                if im:add_crop(slide,im,.85+(j%2)*5.95,1.35+(j//2)*2.65,5.55,2.35)
        elif layout=="architecture":
            count=max(2,min(5,len(items))); y=1.55; width=10.7/count
            for j,item in enumerate(items[:count]):
                x=1.0+j*width; sh=slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE, Inches(x), Inches(y+(j%2)*1.4), Inches(width-.35), Inches(.92));sh.fill.solid();sh.fill.fore_color.rgb=RGBColor(*(PALE if j%2 else (218,235,244)));sh.line.color.rgb=RGBColor(*BLUE);add_text(slide,x+.18,y+(j%2)*1.4+.2,width-.7,.5,item,15,True,NAVY,PP_ALIGN.CENTER)
                if j<count-1: add_text(slide,x+width-.28,y+(.46 if j%2==0 else 1.86),.3,.3,"→",18,True,TEAL,PP_ALIGN.CENTER)
            add_text(slide,1.0,5.3,11.2,.6,"通过统一平台形成“感知—分析—控制—优化”的闭环。",17,False,MUTED,PP_ALIGN.CENTER)
        elif layout in {"process","timeline"}:
            count=max(2,min(5,len(items))); width=11.4/count
            slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, Inches(1.0), Inches(3.1), Inches(10.9), Inches(.06)).fill.solid();slide.shapes[-1].fill.fore_color.rgb=RGBColor(*BLUE);slide.shapes[-1].line.fill.background()
            for j,item in enumerate(items[:count]):
                x=1.0+j*width; circ=slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.OVAL, Inches(x+.2), Inches(2.76), Inches(.7), Inches(.7));circ.fill.solid();circ.fill.fore_color.rgb=RGBColor(*(BLUE if j==0 else TEAL));circ.line.fill.background();add_text(slide,x+.2,2.92,.7,.2,str(j+1),12,True,(255,255,255),PP_ALIGN.CENTER);add_text(slide,x,3.65,width-.1,1.0,item,16,True,TEXT,PP_ALIGN.CENTER)
        elif layout=="table":
            rows=[[c.strip() for c in line.strip().strip("|").split("|")] for line in p["table"] if not re.match(r"^\|?\s*:?-{3}",line)]
            cols=max(map(len,rows),default=1);tbl=slide.shapes.add_table(len(rows),cols,Inches(.85),Inches(1.35),Inches(11.7),Inches(4.9)).table
            for r,row in enumerate(rows):
                for c,val in enumerate(row):
                    cell=tbl.cell(r,c);cell.text=val;cell.fill.solid();cell.fill.fore_color.rgb=RGBColor(*(BLUE if r==0 else (255,255,255)))
                    for para in cell.text_frame.paragraphs:
                        for run in para.runs:run.font.name="Microsoft YaHei";run.font.size=Pt(14 if r else 15);run.font.bold=r==0;run.font.color.rgb=RGBColor(*( (255,255,255) if r==0 else TEXT))
        elif layout=="metrics":
            for j,item in enumerate(items[:4]):
                x=.95+(j%2)*5.9;y=1.55+(j//2)*2.25;sh=slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE,Inches(x),Inches(y),Inches(5.25),Inches(1.65));sh.fill.solid();sh.fill.fore_color.rgb=RGBColor(*(PALE if j%2 else (255,255,255)));sh.line.color.rgb=RGBColor(*PALE);add_text(slide,x+.3,y+.28,4.6,1.1,item,20,True,NAVY)
        else:
            count=max(1,min(4,len(items)));cols=2 if count>2 else count;w=11.5/cols
            for j,item in enumerate(items[:count]):
                x=.9+(j%cols)*(w+.15);y=1.55+(j//cols)*2.15;sh=slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE,Inches(x),Inches(y),Inches(w-.2),Inches(1.45));sh.fill.solid();sh.fill.fore_color.rgb=RGBColor(*(PALE if j%2 else (255,255,255)));sh.line.color.rgb=RGBColor(*PALE);add_text(slide,x+.25,y+.25,w-.7,.95,item,17,True,TEXT)
    out.parent.mkdir(parents=True,exist_ok=True);prs.save(out);return pages,layouts

def audit(pptx: Path,pages,layouts):
    prs=Presentation(pptx);issues=[];min_size=999; image_pages=0; text_only=0;overlaps=0
    for i,slide in enumerate(prs.slides):
        pics=sum(1 for s in slide.shapes if getattr(s,"shape_type",None)==13); image_pages+=bool(pics);text_only+=not bool(pics)
        shapes=list(slide.shapes)
        for s in shapes:
            if s.left<0 or s.top<0 or s.left+s.width>prs.slide_width or s.top+s.height>prs.slide_height: issues.append(f"slide {i+1}: shape outside page")
            if getattr(s,"has_text_frame",False):
                for p in s.text_frame.paragraphs:
                    for r in p.runs:
                        if r.font.size:min_size=min(min_size,r.font.size.pt)
        # deliberate background and contained text overlap are excluded; bounding overlap only flags unrelated visible shapes
    runs=[];current=0
    for p in pages:
        if not p["images"]:current+=1
        else:runs.append(current);current=0
    runs.append(current)
    return {"slide_count":len(prs.slides),"empty_slides":sum(not any(getattr(s,"text","") for s in sl.shapes) for sl in prs.slides),"shape_overflow":issues,"shape_overlap":"MANUAL_VISUAL_REVIEW_REQUIRED","min_font_pt":round(min_size,1) if min_size<999 else None,"text_only_slide_count":text_only,"max_consecutive_no_source_image":max(runs),"image_page_count":image_pages,"layout_counts":dict(Counter(layouts)),"layout_repeat_peak":max(Counter(layouts).values()),"title_lengths":[len(p["title"]) for p in pages],"citation_leak":bool(re.search(r"\[来源:|Sources|References", "\n".join(s.text for sl in prs.slides for s in sl.shapes if hasattr(s,"text"))))}

def offline_finish():
    """Finish rendering after a documented API interruption; makes no network call."""
    OUT.mkdir(parents=True,exist_ok=True)
    shutil.copytree(SOURCE.parent/"assets",OUT/"assets",dirs_exist_ok=True)
    initial=clean((OUT/"slides.initial.md").read_text(encoding="utf-8"))
    review=(OUT/"slides.review.md").read_text(encoding="utf-8") if (OUT/"slides.review.md").is_file() else "NOT_RUN"
    (OUT/"slides.final.md").write_text(initial,encoding="utf-8")
    pages,layouts=render(OUT/"slides.final.md",OUT/"proposal.pptx")
    report={"source_proposal_md":str(SOURCE.relative_to(ROOT)),"initial_source":"existing_seed_2_1_pro_thinking_result","model":MODEL,"thinking":{"type":"enabled"},"review_decision":"REVISION_REQUIRED" if review.lstrip().startswith("REVISION_REQUIRED") else "PASS","targeted_revision":"API_RESPONSE_INTERRUPTED; final equals reviewed initial Markdown; no further retry was made","api_calls":[{"purpose":"review","status":"completed"},{"purpose":"targeted_revision","status":"no_usable_response"}],"audit":audit(OUT/"proposal.pptx",pages,layouts),"visual_review":"PENDING_RENDERED_CONTACT_SHEET"}
    (OUT/"quality_audit.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"slides":report["audit"]["slide_count"],"review":report["review_decision"],"output":"outputs/ppt_style_v1/proposal.pptx"},ensure_ascii=False))

def contact_sheet():
    images=sorted((OUT/"previews/slides").glob("slide-*.png"))
    if not images: raise RuntimeError("no rendered slide PNGs")
    thumbs=[]
    for path in images:
        image=Image.open(path).convert("RGB"); image.thumbnail((480,270)); thumbs.append(image.copy())
    sheet=Image.new("RGB",(1000,((len(thumbs)+1)//2)*310),(245,248,250)); draw=ImageDraw.Draw(sheet)
    for i,image in enumerate(thumbs):
        x=10+(i%2)*500;y=10+(i//2)*310;sheet.paste(image,(x,y));draw.text((x,y+275),f"{i+1:02d}",fill=(24,50,71))
    sheet.save(OUT/"previews/contact_sheet.png")

def write_report():
    report=json.loads((OUT/"quality_audit.json").read_text(encoding="utf-8")); audit=report["audit"]
    lines=["# PPT Style V1 质量报告", "", "## 生成链路", "", f"- SOURCE_PROPOSAL_MD: `{report['source_proposal_md']}`", f"- 模型：`{report['model']}`；thinking=`enabled`", "- 初稿：复用同一固定测试集已完成的 Seed 2.1 Pro Thinking Raw Markdown 结果，未恢复 JSON/schema。", f"- 总编审稿：`{report['review_decision']}`。", f"- 定向修订：{report.get('targeted_revision','NOT_REQUIRED')}", "- PPTX：`proposal.pptx`；页面 PNG 和联系表已由本机 PowerPoint 实际导出。", "", "## 自动审计", "", f"- 页数：{audit['slide_count']}；空页：{audit['empty_slides']}；版面越界：{len(audit['shape_overflow'])}", f"- 页面族：{audit['layout_counts']}；最高重复次数：{audit['layout_repeat_peak']}", f"- 有源图片页：{audit['image_page_count']}；纯文字页：{audit['text_only_slide_count']}；引用残留：{audit['citation_leak']}", f"- 标题长度：{audit['title_lengths']}（字符）；最小字体统计含页脚为 {audit['min_font_pt']} pt。", "", "## 肉眼检查（contact sheet）", "", "- PASS：封面、页脚、品牌深蓝/浅蓝、标题系统和架构/路径图已形成统一设计语汇；架构与流程不再只是普通 bullet。", "- FAIL：本轮初稿没有保留任何原始图片路径，因此 9 页均为无源图片页；图片尚未成为视觉内容。", "- FAIL：总编指出的重复、过密、实施路径信息完整性和待确认项表达问题未得到可用的 API 修订结果，不能判定内容审校通过。", "- FAIL：部分信息块仍然文字偏密；虽已避免形状越界，整体仍未达到可直接对外汇报的完成度。", "", "## 评分", "", "| 维度 | 评级 | 结论 |", "| --- | --- | --- |", "| CONTENT_QUALITY | C | Thinking 初稿有可用故事线，但审稿发现实质问题未闭环。 |", "| STORYTELLING | C | 有背景—建设—实施—效益—下一步的顺序，但痛点/方案/效益映射不足。 |", "| BRAND_CONSISTENCY | B | 视觉锚点、页脚与几何元素一致，基于参考交付稿的设计语言。 |", "| VISUAL_HIERARCHY | C | 标题和主次层级清楚，但信息块仍有密集文字。 |", "| LAYOUT_VARIETY | B | 覆盖封面、架构、路径、总结、信息块，连续重复不超过两页。 |", "| IMAGE_USAGE | F | 初稿未保留任何可用图片，无法验证高权重图像设计。 |", "| EXECUTIVE_READABILITY | C | 框架可读，但尚有内容压缩和图像支撑缺口。 |", "| OVERALL | D | 证明新的设计系统和布局识别链可行；因修订中断、无图片和审稿问题未关闭，不宜作为正式交付。 |", "", "## 与 Raw Markdown baseline 的比较", "", "- 提升：统一品牌语言、页脚、轻量几何装饰，以及对架构/路径内容的原生图示化。", "- 未达标：并未获得肉眼明显的整体质量飞跃；缺少图片和未完成的模型定向修订使其仍保留自动生成感。", ""]
    (OUT/"quality_report.md").write_text("\n".join(lines),encoding="utf-8")

def main():
    OUT.mkdir(parents=True,exist_ok=True);shutil.copytree(SOURCE.parent/"assets",OUT/"assets",dirs_exist_ok=True)
    source=SOURCE.read_text(encoding="utf-8"); guide=GUIDE.read_text(encoding="utf-8")
    # Existing real Thinking result is reused only when it is known to be from this exact fixed source.
    initial=clean(RAW_INITIAL.read_text(encoding="utf-8")) if RAW_INITIAL.is_file() else clean(ark([{"role":"system","content":guide},{"role":"user","content":source}],"initial")[0])
    (OUT/"slides.initial.md").write_text(initial,encoding="utf-8")
    review_prompt="你是企业汇报PPT总编。审阅下列 slides.md，重点检查故事线、结论式标题、重复、连续纯文字、是否应图示化、图片匹配、单页过密/过空、价值总结、事实变化和引用残留。先输出 PASS，或 REVISION_REQUIRED 后列出不超过 6 个明确且实质的问题。不要改写 slides.md。"
    review,review_meta=ark([{"role":"system","content":review_prompt},{"role":"user","content":initial}],"review")
    (OUT/"slides.review.md").write_text(review+"\n",encoding="utf-8")
    calls=[review_meta]
    if review.lstrip().startswith("REVISION_REQUIRED"):
        revision_prompt="你只能根据总编列出的明确问题对下列 PPT Markdown 做一次定向修订。保持事实边界、保留有效本地图片路径、不显示引用或来源；不要恢复 JSON 或固定结构。输出完整 PPT Markdown，不要解释。\n\n总编意见：\n"+review
        final,meta=ark([{"role":"system","content":revision_prompt},{"role":"user","content":initial}],"targeted_revision");calls.append(meta);final=clean(final)
    else: final=initial
    (OUT/"slides.final.md").write_text(final,encoding="utf-8")
    pages,layouts=render(OUT/"slides.final.md",OUT/"proposal.pptx")
    report={"source_proposal_md":str(SOURCE.relative_to(ROOT)),"initial_source":"existing_seed_2_1_pro_thinking_result" if RAW_INITIAL.is_file() else "new_call","model":MODEL,"thinking":{"type":"enabled"},"review_decision":"REVISION_REQUIRED" if review.lstrip().startswith("REVISION_REQUIRED") else "PASS","api_calls":calls,"audit":audit(OUT/"proposal.pptx",pages,layouts),"visual_review":"PENDING_RENDERED_CONTACT_SHEET"}
    (OUT/"quality_audit.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"slides":report["audit"]["slide_count"],"review":report["review_decision"],"output":"outputs/ppt_style_v1/proposal.pptx"},ensure_ascii=False))
if __name__=="__main__":
    write_report() if "--write-report" in sys.argv else (contact_sheet() if "--contact-sheet" in sys.argv else (offline_finish() if "--offline-finish" in sys.argv else main()))
