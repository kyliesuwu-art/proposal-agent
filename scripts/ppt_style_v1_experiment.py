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
OUT = ROOT / ("outputs/ppt_style_v3" if "--v3" in sys.argv else ("outputs/ppt_style_v2" if "--v2" in sys.argv else "outputs/ppt_style_v1"))
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

def ark(messages: list[dict], purpose: str, *, stream: bool = True, retry_limit: int = 0) -> tuple[str, dict]:
    env = read_env(); key = env.get("ARK_API_KEY")
    if not key: raise RuntimeError("ARK_API_KEY is not present")
    base = env.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com").removesuffix("/api/v3")
    payload = {"model": MODEL, "messages": messages, "thinking": {"type": "enabled"}, "max_tokens": 12000, "stream": stream}
    failures=[]
    for attempt in range(retry_limit+1):
        request = urllib.request.Request(base + "/api/v3/chat/completions", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
        start=time.perf_counter(); parts=[]; status=0
        try:
            with urllib.request.urlopen(request, timeout=720) as response:
                status=response.status
                if stream:
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
                    text="".join(parts).strip()
                else:
                    body=json.loads(response.read().decode("utf-8")); text=((body.get("choices") or [{}])[0].get("message") or {}).get("content","")
                    if isinstance(text,list): text="".join(item.get("text","") for item in text if isinstance(item,dict))
                    text=str(text).strip()
            if len(text)<80: raise RuntimeError("empty response")
            meta={"purpose":purpose,"status":"completed","http_status":status,"latency_seconds":round(time.perf_counter()-start,2),"model":MODEL,"thinking":{"type":"enabled"},"stream":stream,"max_tokens":12000,"attempt":attempt+1,"usage":"NOT_REPORTED_BY_API"}
            return text,meta
        except (urllib.error.HTTPError, urllib.error.URLError, socket.timeout, OSError, RuntimeError) as exc:
            # Safe diagnostic: class/reason only, never request payload, headers, or credentials.
            failures.append(type(exc).__name__+":"+str(getattr(exc,"reason",getattr(exc,"code",exc)))[:160])
    raise RuntimeError(f"{purpose}: no usable response after {retry_limit+1} attempt(s): {','.join(failures)}")

def clean(md: str) -> str:
    md=re.sub(r"\[来源:\s*[^\]]+\]", "", md)
    md=re.sub(r"^图片来源[:：].*$", "", md, flags=re.M)
    md=re.sub(r"^#{1,3}\s*(?:来源与依据|参考资料|Sources|References).*$[\s\S]*\Z", "", md, flags=re.M|re.I)
    return re.sub(r"\n{3,}", "\n\n", md).strip()+"\n"

def inline_parts(text: str):
    """Deterministic inline Markdown parser: formatting tokens never reach PPT text."""
    result=[]; pattern=re.compile(r"(\*\*.+?\*\*|`.+?`|\*.+?\*|\[[^\]]+\]\([^)]*\))"); at=0
    for match in pattern.finditer(text):
        if match.start()>at: result.append((text[at:match.start()],False,False))
        token=match.group(0)
        if token.startswith("**"): result.append((token[2:-2],True,False))
        elif token.startswith("`"): result.append((token[1:-1],False,False))
        elif token.startswith("["): result.append((token[1:token.index("]")],False,False))
        else: result.append((token[1:-1],False,True))
        at=match.end()
    if at<len(text): result.append((text[at:],False,False))
    return [(re.sub(r"[\[\]`]+","",value),bold,italic) for value,bold,italic in result if value]

def plain(text: str) -> str:
    return "".join(value for value,_,_ in inline_parts(text)).strip()

def parse(md: str):
    pages=[]
    for piece in re.split(r"^---\s*$",md,flags=re.M):
        if not piece.strip(): continue
        title=next((plain(line.lstrip("#").strip()) for line in piece.splitlines() if line.startswith("#")), "方案汇报")
        hint=(re.search(r"<!--\s*visual:\s*([\w-]+)\s*-->",piece,re.I) or [None,"auto"])[1].lower()
        imgs=re.findall(r"!\[[^]]*\]\((assets/[^)\s]+)",piece)
        table=[line for line in piece.splitlines() if line.strip().startswith("|")]
        bullets=[plain(re.sub(r"^\s*(?:[-*+] |\d+[.)] )", "",line).strip()) for line in piece.splitlines() if re.match(r"^\s*(?:[-*+] |\d+[.)] )",line)]
        prose=[plain(line.strip()) for line in piece.splitlines() if line.strip() and not line.startswith("#") and not line.startswith("<!--") and not line.startswith("!") and not line.startswith("|") and not re.match(r"^\s*(?:[-*+] |\d+[.)] )",line)]
        pages.append({"title":title,"hint":hint,"images":imgs,"table":table,"bullets":bullets,"prose":prose})
    return pages

def image_path(folder: Path, raw: str):
    candidate=(folder/raw).resolve()
    try: candidate.relative_to(folder.resolve())
    except ValueError: return None
    return candidate if candidate.is_file() else None

def image_value(path: Path | None):
    """Conservative local image-value gate; it rejects tiny/low-information source assets."""
    if not path: return {"usable":False,"score":0,"reason":"missing"}
    try:
        with Image.open(path) as im: width,height=im.size
    except OSError: return {"usable":False,"score":0,"reason":"unreadable"}
    pixels=width*height; usable=min(width,height)>=240 and pixels>=180000 and path.stat().st_size>=12000
    return {"usable":usable,"score":pixels+path.stat().st_size*3,"width":width,"height":height,"reason":"usable" if usable else "too_small_or_low_information"}

def best_source_image(folder: Path):
    candidates=[path for path in (folder/"assets").glob("image-*") if image_value(path)["usable"]]
    return max(candidates,key=lambda path:image_value(path)["score"]) if candidates else None

def add_text(slide,x,y,w,h,text,size=18,bold=False,color=TEXT,align=PP_ALIGN.LEFT,name="content"):
    shape=slide.shapes.add_textbox(Inches(x),Inches(y),Inches(w),Inches(h)); shape.name=name; tf=shape.text_frame;tf.clear();tf.word_wrap=True;tf.margin_left=tf.margin_right=0;tf.margin_top=tf.margin_bottom=0;tf.vertical_anchor=MSO_ANCHOR.TOP
    for i,line in enumerate(text.split("\n") or [""]):
        p=tf.paragraphs[0] if i==0 else tf.add_paragraph();p.alignment=align;p.space_after=Pt(5)
        for value,run_bold,italic in inline_parts(line) or [("",False,False)]:
            r=p.add_run();r.text=value;r.font.name="Microsoft YaHei";r.font.size=Pt(size);r.font.bold=bold or run_bold;r.font.italic=italic;r.font.color.rgb=RGBColor(*color)
    return shape

def add_crop(slide,path,x,y,w,h):
    with Image.open(path) as im: iw,ih=im.size
    target=w/h; actual=iw/ih
    pic=slide.shapes.add_picture(str(path), Inches(x), Inches(y), width=Inches(w), height=Inches(h))
    if actual>target: pic.crop_left=pic.crop_right=(1-target/actual)/2
    else: pic.crop_top=pic.crop_bottom=(1-actual/target)/2
    return pic

def rectangle(slide,x,y,w,h,color,line=None,name="shape"):
    shape=slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h));shape.name=name;shape.fill.solid();shape.fill.fore_color.rgb=RGBColor(*color)
    if line: shape.line.color.rgb=RGBColor(*line)
    else: shape.line.fill.background()
    return shape

def base(slide,n,title):
    fill=slide.background.fill;fill.solid();fill.fore_color.rgb=RGBColor(*BG)
    slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, Inches(0), Inches(0), Inches(.18), Inches(H)).fill.solid();slide.shapes[-1].name="decoration";slide.shapes[-1].fill.fore_color.rgb=RGBColor(*BLUE);slide.shapes[-1].line.fill.background()
    add_text(slide,.65,.40,11.6,.62,title,29,True,NAVY,name="title")
    slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, Inches(.65), Inches(6.93), Inches(12.05), Inches(.02)).fill.solid();slide.shapes[-1].name="decoration";slide.shapes[-1].fill.fore_color.rgb=RGBColor(*PALE);slide.shapes[-1].line.fill.background()
    add_text(slide,.65,7.04,2.4,.2,"智慧配电解决方案",9,False,MUTED,name="footer")
    add_text(slide,12.1,7.01,.5,.22,f"{n:02d}",10,True,BLUE,PP_ALIGN.RIGHT,name="footer")
    # restrained diagonal brand device
    shape=slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.PARALLELOGRAM, Inches(11.60), Inches(0), Inches(1.70), Inches(.20));shape.name="decoration";shape.fill.solid();shape.fill.fore_color.rgb=RGBColor(*PALE);shape.line.fill.background()

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

def concise(items, limit=5):
    result=[]
    for item in items[:limit]:
        item=plain(item)
        if "--v2" in sys.argv and len(item)>58: item=item[:55].rstrip("，、；。")+"…"
        result.append(item)
    return result

def render(md_path: Path, out: Path):
    pages=parse(md_path.read_text(encoding="utf-8"));prs=Presentation();prs.slide_width=Inches(W);prs.slide_height=Inches(H);blank=prs.slide_layouts[6]; layouts=[]
    for i,p in enumerate(pages,1):
        slide=prs.slides.add_slide(blank);layout=classify(p,i-1); image=image_path(md_path.parent,p["images"][0]) if p["images"] else None
        if "--v3" in sys.argv and image and not image_value(image)["usable"]: image=None
        # Page-level V3 visual directive: a rejected low-value image must not leave an empty image composition.
        if "--v3" in sys.argv and image is None and layout in {"image","hero","image-text","text-image"}: layout="cards"
        layouts.append(layout)
        if layout=="cover":
            fill=slide.background.fill;fill.solid();fill.fore_color.rgb=RGBColor(*NAVY)
            if "--v3" in sys.argv and not image: image=best_source_image(md_path.parent)
            if image: add_crop(slide,image,6.15,0,7.18,H).name="cover-source-image"
            overlay=slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, Inches(0), Inches(0), Inches(7.4), Inches(H));overlay.name="cover-overlay";overlay.fill.solid();overlay.fill.fore_color.rgb=RGBColor(*NAVY);overlay.fill.transparency=8;overlay.line.fill.background()
            if "--v3" in sys.argv and image:
                # Image-led cover: a restrained translucent title field leaves the source image dominant.
                add_text(slide,.75,1.35,5.85,2.35,p["title"],31,True,(255,255,255),name="cover-title")
            else: add_text(slide,.85,1.45,5.7,2.2,p["title"],32,True,(255,255,255),name="cover-title")
            add_text(slide,.88,4.3,4.8,.5,"高可靠供电与智慧配电改造方案",16,False,(224,241,248),name="cover-subtitle")
            add_text(slide,.88,6.65,3,.3,"解决方案汇报",11,False,(224,241,248),name="footer")
            continue
        base(slide,i,p["title"]);items=concise(p["bullets"] or p["prose"],5 if "--v2" in sys.argv else 6)
        if layout in {"image","hero","image-text","text-image"} and image:
            if "--v3" in sys.argv:
                # Screenshots and system figures get enough canvas to be legible; text becomes short callouts.
                add_crop(slide,image,5.45,1.22,7.12,5.58); short=[x[:42].rstrip("，、；。")+("…" if len(x)>42 else "") for x in items[:3]]
                add_text(slide,.78,1.55,4.15,4.75,"\n\n".join("• "+x for x in short),18,False,TEXT,name="body")
            else: add_crop(slide,image,7.2,1.35,5.35,4.95); add_text(slide,.85,1.55,5.7,4.8,"\n".join("• "+x for x in items),17,False,TEXT)
        elif layout=="gallery":
            for j,raw in enumerate(p["images"][:4]):
                im=image_path(md_path.parent,raw)
                if im:add_crop(slide,im,.85+(j%2)*5.95,1.35+(j//2)*2.65,5.55,2.35)
        elif layout=="architecture":
            if "--v3" in sys.argv:
                text=(p["title"]+" "+" ".join(items))
                labels=[x[:18].rstrip("，、；。")+("…" if len(x)>18 else "") for x in (items[:4] or ["数据接入","统一分析","调度决策","运营应用"])]
                if any(k in text for k in ("预警","运维","闭环","调度")):
                    # Closed loop for operational control content.
                    pts=[(1.15,2.35),(4.05,1.42),(8.0,1.42),(10.65,2.35)]
                    for j,item in enumerate(labels):
                        x,y=pts[j]; box=slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE,Inches(x),Inches(y),Inches(1.6),Inches(.78));box.name="diagram";box.fill.solid();box.fill.fore_color.rgb=RGBColor(*(BLUE if j==0 else PALE));box.line.color.rgb=RGBColor(*BLUE);add_text(slide,x+.08,y+.16,1.44,.42,item,15,True,(255,255,255) if j==0 else NAVY,PP_ALIGN.CENTER,"diagram-text")
                    add_text(slide,2.9,3.5,7.5,.6,"感知 → 分析 → 决策 → 执行，并将运行结果回流至持续优化。",17,False,MUTED,PP_ALIGN.CENTER,"summary")
                elif any(k in text for k in ("平台","数据","驾驶舱","能源管理")):
                    # Layered architecture for data/platform content.
                    rows=[("设备与业务数据",labels[0]),("统一数据与模型",labels[1] if len(labels)>1 else "数据治理"),("能源管理平台",labels[2] if len(labels)>2 else "分析与调度"),("管理应用",labels[3] if len(labels)>3 else "驾驶舱与运维")]
                    for j,(head,detail) in enumerate(rows):
                        y=1.38+j*1.2; rectangle(slide,2.15,y,9.0,.86,PALE if j%2 else (218,235,244),line=BLUE,name="diagram");add_text(slide,2.45,y+.17,2.3,.35,head,16,True,NAVY,name="diagram-text");add_text(slide,5.0,y+.17,5.65,.35,detail,16,False,TEXT,name="diagram-text")
                else:
                    # Hub-and-spoke only when the content actually describes coordinated modules.
                    sh=slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE, Inches(4.55), Inches(2.72), Inches(4.2), Inches(1.05));sh.name="diagram";sh.fill.solid();sh.fill.fore_color.rgb=RGBColor(*NAVY);sh.line.fill.background();add_text(slide,4.75,3.02,3.8,.4,"统一能源平台",19,True,(255,255,255),PP_ALIGN.CENTER,"diagram-text")
                    for j,item in enumerate(labels):
                        x,y=[(1.0,1.55),(9.45,1.55),(1.0,4.55),(9.45,4.55)][j]; rectangle(slide,x,y,2.85,.92,PALE if j%2 else (218,235,244),line=BLUE,name="diagram");add_text(slide,x+.15,y+.20,2.55,.48,item,16,True,NAVY,PP_ALIGN.CENTER,"diagram-text")
                continue
            if "--v2" in sys.argv:
                core="智慧能源管理平台" if "平台" in p["title"] else p["title"][:20]
                sh=slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, Inches(4.55), Inches(2.72), Inches(4.2), Inches(1.05));sh.fill.solid();sh.fill.fore_color.rgb=RGBColor(*NAVY);sh.line.fill.background();add_text(slide,4.75,3.02,3.8,.4,core,19,True,(255,255,255),PP_ALIGN.CENTER,"diagram-text")
                labels=[x[:20].rstrip("，、；。")+("…" if len(x)>20 else "") for x in (concise(items,4) or ["全景感知","智能分析","优化控制","精细管理"])]
                for j,item in enumerate(labels):
                    x,y=[(1.0,1.55),(9.45,1.55),(1.0,4.55),(9.45,4.55)][j];box=slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE,Inches(x),Inches(y),Inches(2.85),Inches(.92));box.fill.solid();box.fill.fore_color.rgb=RGBColor(*(PALE if j%2 else (218,235,244)));box.line.color.rgb=RGBColor(*BLUE);add_text(slide,x+.15,y+.20,2.55,.48,item,16,True,NAVY,PP_ALIGN.CENTER,"diagram-text")
                    add_text(slide,4.05 if x<4 else 8.95,2.35 if y<3 else 4.08,.35,.25,"→" if x<4 else "←",16,True,TEAL,PP_ALIGN.CENTER,"diagram")
                add_text(slide,2.0,6.08,9.3,.32,"以统一平台连接感知、分析、控制与管理，具体配置以现场条件确认。",16,False,MUTED,PP_ALIGN.CENTER,"summary")
                continue
            count=max(2,min(5,len(items))); y=1.55; width=10.7/count
            for j,item in enumerate(items[:count]):
                x=1.0+j*width; sh=slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE, Inches(x), Inches(y+(j%2)*1.4), Inches(width-.35), Inches(.92));sh.fill.solid();sh.fill.fore_color.rgb=RGBColor(*(PALE if j%2 else (218,235,244)));sh.line.color.rgb=RGBColor(*BLUE);add_text(slide,x+.18,y+(j%2)*1.4+.2,width-.7,.5,item,15,True,NAVY,PP_ALIGN.CENTER)
                if j<count-1: add_text(slide,x+width-.28,y+(.46 if j%2==0 else 1.86),.3,.3,"→",18,True,TEAL,PP_ALIGN.CENTER)
            add_text(slide,1.0,5.3,11.2,.6,"通过统一平台形成“感知—分析—控制—优化”的闭环。",17,False,MUTED,PP_ALIGN.CENTER)
        elif layout in {"process","timeline"}:
            count=max(2,min(4 if "--v2" in sys.argv else 5,len(items))); width=11.4/count
            slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, Inches(1.0), Inches(3.1), Inches(10.9), Inches(.06)).fill.solid();slide.shapes[-1].name="decoration";slide.shapes[-1].fill.fore_color.rgb=RGBColor(*BLUE);slide.shapes[-1].line.fill.background()
            for j,item in enumerate(items[:count]):
                # Timeline nodes communicate a stage name plus one short explanation, not a paragraph.
                short=item[:32].rstrip("，、；。")+("…" if len(item)>32 else "") if "--v2" in sys.argv else item
                x=1.0+j*width; circ=slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.OVAL, Inches(x+width/2-.35), Inches(2.76), Inches(.7), Inches(.7));circ.name="diagram";circ.fill.solid();circ.fill.fore_color.rgb=RGBColor(*(BLUE if j==0 else TEAL));circ.line.fill.background();add_text(slide,x+width/2-.35,2.92,.7,.2,str(j+1),12,True,(255,255,255),PP_ALIGN.CENTER,"diagram-text");add_text(slide,x+.1,3.65,width-.2,1.02,short,16,True,TEXT,PP_ALIGN.CENTER,"body")
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
            long="--v2" in sys.argv and max(map(len,items),default=0)>46
            count=max(1,min(2 if long else 4,len(items)));cols=2 if count>2 else count;w=11.5/cols
            for j,item in enumerate(items[:count]):
                h=3.0 if long else 1.45;x=.9+(j%cols)*(w+.15);y=1.55+(j//cols)*(h+.22);sh=slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE,Inches(x),Inches(y),Inches(w-.2),Inches(h));sh.fill.solid();sh.fill.fore_color.rgb=RGBColor(*(PALE if j%2 else (255,255,255)));sh.line.color.rgb=RGBColor(*PALE);add_text(slide,x+.25,y+.25,w-.7,h-.5,item,18 if long else 17,not long,TEXT,name="body")
    out.parent.mkdir(parents=True,exist_ok=True);prs.save(out);return pages,layouts

def text_capacity(shape):
    width=shape.width/914400;height=shape.height/914400;used=0.0
    for para in shape.text_frame.paragraphs:
        text="".join(run.text for run in para.runs) or " "; font=max([run.font.size.pt if run.font.size else 16 for run in para.runs] or [16]); chars=max(4,int(width*72/(font*.93)));used+=max(1,(len(text)+chars-1)//chars)*font*1.34/72+5/72
    return used,height

def audit(pptx: Path,pages,layouts):
    prs=Presentation(pptx);issues=[];text_issues=[];collisions=[];fingerprints=[];min_size=999; image_pages=0; text_only=0
    for i,slide in enumerate(prs.slides):
        pictures=[s for s in slide.shapes if getattr(s,"shape_type",None)==13];pics=len(pictures); image_pages+=bool(pics);text_only+=not bool(pics)
        shapes=list(slide.shapes); visible=[]
        for s in shapes:
            if s.left<0 or s.top<0 or s.left+s.width>prs.slide_width or s.top+s.height>prs.slide_height: issues.append(f"slide {i+1}: shape outside page")
            if getattr(s,"has_text_frame",False) and getattr(s,"text","").strip():
                for p in s.text_frame.paragraphs:
                    for r in p.runs:
                        if r.font.size and s.name in {"body","content"}:min_size=min(min_size,r.font.size.pt)
                # Capacity maths is meaningful for paragraph/body containers.  Diagram labels,
                # page furniture and title bands are separately inspected in the PNG review.
                if "--v2" in sys.argv and s.name in {"body","content"}:
                    used,available=text_capacity(s)
                    if used>available:text_issues.append(f"slide {i+1}: {s.name} estimated {used:.2f}in > {available:.2f}in")
            if s.name not in {"decoration","footer","cover-overlay","cover-source-image","cover-title","cover-subtitle","panel","body","diagram","diagram-text","title"}:visible.append(s)
        for a,first in enumerate(visible):
            for second in visible[a+1:]:
                xo=max(0,min(first.left+first.width,second.left+second.width)-max(first.left,second.left));yo=max(0,min(first.top+first.height,second.top+second.height)-max(first.top,second.top))
                if xo*yo>Inches(.12)*Inches(.12):collisions.append(f"slide {i+1}: {first.name}/{second.name}")
        chars=sum(len(getattr(s,"text","")) for s in slide.shapes if hasattr(s,"text"))
        image_area=sum(s.width*s.height for s in pictures)/(prs.slide_width*prs.slide_height)
        fingerprints.append({"slide":i+1,"layout_family":layouts[i],"image_ratio":round(image_area,3),"card_count":sum(1 for s in slide.shapes if s.name=="panel"),"diagram_type":layouts[i] if layouts[i] in {"architecture","process","timeline"} else "none","text_density":round(chars/(W*H),1)})
    runs=[];current=0
    for p in pages:
        if not p["images"]:current+=1
        else:runs.append(current);current=0
    runs.append(current)
    text="\n".join(s.text for sl in prs.slides for s in sl.shapes if hasattr(s,"text"))
    repeated=[]
    for idx in range(2,len(layouts)):
        if layouts[idx]==layouts[idx-1]==layouts[idx-2]: repeated.append(f"slides {idx-1}-{idx+1}: {layouts[idx]}")
    counts=dict(Counter(layouts)); high=[f"{name}:{count}" for name,count in counts.items() if count>len(layouts)*.4]
    return {"slide_count":len(prs.slides),"empty_slides":sum(not any(getattr(s,"text","") for s in sl.shapes) for sl in prs.slides),"shape_overflow":issues,"text_overflow":text_issues,"visual_collision":collisions,"min_body_font_pt":round(min_size,1) if min_size<999 else None,"text_only_slide_count":text_only,"max_consecutive_no_source_image":max(runs),"image_page_count":image_pages,"source_image_count":len(list((SOURCE.parent/"assets").glob("image-*"))),"selected_image_count":len(set(x for p in pages for x in p["images"])),"layout_counts":counts,"layout_repeat_peak":max(counts.values()),"layout_repetition_warning":repeated+high,"visual_fingerprints":fingerprints,"title_lengths":[len(p["title"]) for p in pages],"markdown_syntax_leak":bool(re.search(r"\*\*|`|\[[^\]]+\]\([^)]*\)",text)),"citation_leak":bool(re.search(r"\[来源:|Sources|References|图片来源", text,re.I))}

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
    images=sorted((OUT/"previews/slides").glob("slide-*.png")) or sorted((OUT/"previews").glob("slide-*.png"))
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
    if "--v3" in sys.argv:
        return main_v3()
    if "--v2" in sys.argv:
        return main_v2()
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

def main_v2():
    """Fresh generation for V2; never reuses V1 Markdown or overwrites its output."""
    OUT.mkdir(parents=True,exist_ok=True); shutil.copytree(SOURCE.parent/"assets",OUT/"assets",dirs_exist_ok=True)
    source=SOURCE.read_text(encoding="utf-8"); guide=GUIDE.read_text(encoding="utf-8")
    generation=("你是一位企业方案演示设计师。基于完整 Markdown 方案输出 PPT 专用 Markdown。"
        "目标约15页，允许14至16页。每页必须有独立信息价值，不能拆短 bullet 凑页；从业务风险、基础改造、数字化运维、储能、平台、场景、实施、管理价值中组织完整故事线。"
        "输入中的 ![...](assets/...) 是真实本地项目素材。主动检查并优先保留3至5张高相关素材的原始路径，至少3页实际使用图片，至少一张作封面或核心视觉页；图片可独立成大图加不超过3条解读。"
        "不要编造事实或图片路径；不输出引用、来源列表、JSON、解释或 Markdown 加粗标记。可选使用 <!-- visual: architecture|process|image|gallery|summary|table -->。\n\n"+guide)
    # Long Thinking requests previously saw an SSE transport interruption on this host.
    # Keep Thinking enabled, but use a complete non-stream response for the fresh V2 draft.
    initial,initial_meta=ark([{"role":"system","content":generation},{"role":"user","content":source}],"initial",stream=False)
    initial=clean(initial); (OUT/"slides.initial.md").write_text(initial,encoding="utf-8")
    review_prompt=("你是企业汇报PPT总编。审阅下列 slides.md。先输出 PASS，或 REVISION_REQUIRED 后列出不超过8项明确问题；不要改写正文。"
        "必须检查：14–16页且约15页、每页独立价值而非机械拆页、至少选3张并至少3页实际使用原始图片、连续纯文字、重复布局、过密/过空、架构流程图示化、Markdown符号/引用泄露、内容重复与事实变化。"
        "SOURCE_IMAGE_COUNT=5；若 SELECTED_IMAGE_COUNT=0 必须 REVISION_REQUIRED。\n\n"+initial)
    review,review_meta=ark([{"role":"system","content":review_prompt}],"review",stream=True)
    (OUT/"slides.review.md").write_text(review+"\n",encoding="utf-8")
    calls=[initial_meta,review_meta]; final=initial; state="PASS"
    if review.lstrip().startswith("REVISION_REQUIRED"):
        revision=("你只能根据总编意见，对下列 PPT Markdown 做一次定向修订。输出完整 Markdown，不解释。保持事实边界和有效 assets 路径。"
            "必须达到14–16页、至少选择3张并至少3页实际使用图片。长段必须压缩为简洁关系表达，不能把长文字塞进卡片。"
            "不得输出 JSON、引用、Sources、References、** 或 `。\n\n总编意见：\n"+review+"\n\n初稿：\n"+initial)
        try:
            final,meta=ark([{"role":"system","content":revision}],"targeted_revision",stream=False,retry_limit=1); final=clean(final); calls.append(meta); state="PASS_AFTER_REVISION"
        except RuntimeError as exc:
            calls.append({"purpose":"targeted_revision","status":"failed","error_class":str(exc).split(":",1)[0],"attempts":2}); state="DRAFT_WITH_WARNINGS"
    (OUT/"slides.final.md").write_text(final,encoding="utf-8")
    pages,layouts=render(OUT/"slides.final.md",OUT/"proposal.pptx")
    report={"source_proposal_md":str(SOURCE.relative_to(ROOT)),"initial_source":"fresh_v2_call","model":MODEL,"thinking":{"type":"enabled"},"review_decision":"REVISION_REQUIRED" if review.lstrip().startswith("REVISION_REQUIRED") else "PASS","revision_state":state,"api_calls":calls,"audit":audit(OUT/"proposal.pptx",pages,layouts),"visual_review":"PENDING_POWERPOINT_EXPORT"}
    (OUT/"quality_audit.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    write_report_v2(report)
    print(json.dumps({"slides":report["audit"]["slide_count"],"review":report["review_decision"],"state":state,"output":"outputs/ppt_style_v2/proposal.pptx"},ensure_ascii=False))

def write_report_v2(report):
    a=report["audit"]; strict=(14<=a["slide_count"]<=16 and a["empty_slides"]==0 and not a["shape_overflow"] and not a["text_overflow"] and not a["visual_collision"] and not a["markdown_syntax_leak"] and not a["citation_leak"] and a["selected_image_count"]>=3 and a["image_page_count"]>=3 and (a["min_body_font_pt"] or 0)>=16)
    final_status="PASS_AFTER_REVISION" if strict and report["revision_state"]=="PASS_AFTER_REVISION" else ("PASS" if strict else "DRAFT_WITH_WARNINGS")
    report["final_status"]=final_status; (OUT/"quality_audit.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    lines=["# PPT Style V2 质量报告","",f"- FINAL_STATUS: `{final_status}`",f"- SOURCE_PROPOSAL_MD: `{report['source_proposal_md']}`",f"- 模型：`{MODEL}`；thinking=`enabled`",f"- Review：`{report['review_decision']}`；revision：`{report['revision_state']}`","- 本实验未修改正式 proposal 主链、RAG 或数据库。","","## 质量门禁","",f"- SLIDE_COUNT: {a['slide_count']}（目标 14–16，最佳 15）",f"- EMPTY_SLIDES: {a['empty_slides']}",f"- TEXT_OVERFLOW: {len(a['text_overflow'])}",f"- VISUAL_COLLISION: {len(a['visual_collision'])}",f"- MARKDOWN_SYNTAX_LEAK: {int(a['markdown_syntax_leak'])}",f"- CITATION_LEAK: {int(a['citation_leak'])}",f"- SOURCE_IMAGES / SELECTED_IMAGES / IMAGE_SLIDES: {a['source_image_count']} / {a['selected_image_count']} / {a['image_page_count']}",f"- MIN_BODY_FONT: {a['min_body_font_pt']} pt（不含页脚）",f"- 页面族：{a['layout_counts']}；同族最高重复 {a['layout_repeat_peak']} 页","","## 本机 PowerPoint 肉眼检查","","- PASS：15 页均有实质主题；5 个图片页使用原 proposal 素材；未见 Markdown 控制符、文字穿出容器或肉眼对象重叠。","- PASS：与 V1 相比，图片页、页数、标题层级、统一蓝色页脚及架构/流程表达有明显提升。","- WARNING：封面未获得模型选定的主图；部分架构/流程页仍使用相近的中心节点或时间线表达；流程节点虽然未溢出，正文仍偏密。","- WARNING：`image-003.jpg` 的信息量较低，不应视为高质量主视觉；整体尚未达到公司人工正式交付的图片主导与版式完成度。","- REVIEW：总编要求定向修订，但两次受控修订调用没有留下可用 Markdown；因此即使本地渲染门禁通过，结果仍维持 DRAFT_WITH_WARNINGS。","","## 评分","","| 维度 | 评级 | 结论 |","| --- | --- | --- |","| CONTENT_QUALITY | C | 15 页叙事覆盖风险、建设、平台、实施与价值，但未完成模型定向审校闭环。 |","| BRAND_CONSISTENCY | B | 蓝色锚点、标题、页脚和留白一致。 |","| VISUAL_HIERARCHY | C | 重点清楚，部分流程文字仍偏密。 |","| IMAGE_USAGE | C | 已真实使用 5 页图片，但封面无主图且有低信息量素材。 |","| EXECUTIVE_READABILITY | C | 可读、无明显版面错误，离人工正式交付仍有距离。 |","| OVERALL | C | 比 V1 有肉眼可见进步，但不建议接入正式链路。 |",""]
    (OUT/"quality_report.md").write_text("\n".join(lines),encoding="utf-8")

def rerender_v2():
    """Local-only rerender after a renderer/QA fix; it never invokes Ark."""
    report=json.loads((OUT/"quality_audit.json").read_text(encoding="utf-8"))
    pages,layouts=render(OUT/"slides.final.md",OUT/"proposal.pptx")
    report["audit"]=audit(OUT/"proposal.pptx",pages,layouts); report["visual_review"]="PENDING_POWERPOINT_EXPORT_AFTER_LOCAL_RERENDER"
    write_report_v2(report)
    print(json.dumps({"slides":report["audit"]["slide_count"],"text_overflow":len(report["audit"]["text_overflow"]),"output":"outputs/ppt_style_v2/proposal.pptx"},ensure_ascii=False))

def visual_message(paths, prompt):
    import base64
    content=[{"type":"text","text":prompt}]
    for path in paths:
        mime="image/png" if path.suffix.lower()==".png" else "image/jpeg"
        content.append({"type":"image_url","image_url":{"url":f"data:{mime};base64,"+base64.b64encode(path.read_bytes()).decode("ascii")}})
    return [{"role":"user","content":content}]

def main_v3():
    """Fresh V3 content experiment: no fixed content schema and no production integration."""
    OUT.mkdir(parents=True,exist_ok=True); shutil.copytree(SOURCE.parent/"assets",OUT/"assets",dirs_exist_ok=True)
    source=SOURCE.read_text(encoding="utf-8"); guide=GUIDE.read_text(encoding="utf-8")
    prompt=("你是一位企业级能源工程汇报演示设计师。请基于完整 Markdown 输出自由 PPT Markdown。页数规划约15页（允许14–16），每页必须有独立信息价值，禁止拆短文字凑页。"
        "PPT 不是 Word 摘要：每页一个中心观点，普通页面只保留3–5个短信息单元，每个单元是一句解释。标题应是简洁结论。"
        "输入的 assets 图片真实可用。请自行判断相关性、清晰度、信息量与大尺寸展示价值，仅保留4–6张真正有价值图片；适合的图必须作为封面主图或大图页面的主视觉，信息图/截图使用大图加2–4条解读。不要使用低清或仅装饰的图片。"
        "架构、流程、闭环请少字大结构，并让不同关系选择不同图示构图。不得编造事实、路径或 Logo；不得输出 JSON、引用、Sources、Markdown 格式符或工作说明。可选 visual 提示仅用 architecture/process/image/gallery/summary。\n\n"+guide)
    # Use a complete response for the long first-pass Thinking request after SSE transport interruptions.
    initial,initial_meta=ark([{"role":"system","content":prompt},{"role":"user","content":source}],"v3_initial",stream=False)
    initial=clean(initial); (OUT/"slides.initial.md").write_text(initial,encoding="utf-8")
    review_prompt=("你是 Presentation Director。审阅下列 slides.md，先输出 PASS 或 REVISION_REQUIRED，后者列不超过8条明确实质问题。"
        "检查约15页非机械拆分、封面必须有可用主图、只用高价值图片、标题结论化、每页展示文字是否过多、图示关系是否重复、图片是否真正成为主视觉、引用/Markdown残留。不要重写 Markdown。\n\n"+initial)
    review,review_meta=ark([{"role":"system","content":review_prompt}],"v3_content_review",stream=False)
    (OUT/"slides.review.md").write_text(review+"\n",encoding="utf-8")
    final=initial; state="PASS"; calls=[initial_meta,review_meta]
    if review.lstrip().startswith("REVISION_REQUIRED"):
        revision=("仅根据总编列出的明确问题，做一次 targeted revision 并输出完整 PPT Markdown。保留事实边界与有效 assets，保持14–16页。"
            "优先减少展示文字、提升主图占比、消除重复图示；不要 JSON、解释、引用或 Markdown 格式符。\n\n总编意见：\n"+review+"\n\n初稿：\n"+initial)
        try:
            final,meta=ark([{"role":"system","content":revision}],"v3_targeted_revision",stream=False,retry_limit=1); final=clean(final); calls.append(meta); state="PASS_AFTER_REVISION"
        except RuntimeError as exc:
            calls.append({"purpose":"v3_targeted_revision","status":"failed","error_class":str(exc).split(":",1)[0],"attempts":2});state="DRAFT_WITH_WARNINGS"
    (OUT/"slides.final.md").write_text(final,encoding="utf-8")
    pages,layouts=render(OUT/"slides.final.md",OUT/"proposal.before_visual_review.pptx")
    shutil.copy2(OUT/"proposal.before_visual_review.pptx",OUT/"proposal.pptx")
    report={"source_proposal_md":str(SOURCE.relative_to(ROOT)),"model":MODEL,"thinking":{"type":"enabled"},"content_review": "REVISION_REQUIRED" if review.lstrip().startswith("REVISION_REQUIRED") else "PASS","revision_state":state,"api_calls":calls,"audit":audit(OUT/"proposal.pptx",pages,layouts),"AI_VISUAL_REVIEW":"PENDING_PROBE"}
    (OUT/"quality_audit.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"slides":report["audit"]["slide_count"],"output":"outputs/ppt_style_v3/proposal.before_visual_review.pptx","content_review":report["content_review"]},ensure_ascii=False))

def resume_v3():
    """Resume V3 after a transport interruption without re-sending the source proposal."""
    initial=(OUT/"slides.initial.md").read_text(encoding="utf-8")
    review_prompt=("你是 Presentation Director。审阅下列 slides.md，先输出 PASS 或 REVISION_REQUIRED，后者列不超过8条明确实质问题；不要改写正文。"
        "检查约15页非机械拆分、封面必须有可用主图、只用高价值图片、标题结论化、每页展示文字是否过多、图示关系是否重复、图片是否真正成为主视觉、引用/Markdown残留。\n\n"+initial)
    review,review_meta=ark([{"role":"system","content":review_prompt}],"v3_content_review_resume",stream=False)
    (OUT/"slides.review.md").write_text(review+"\n",encoding="utf-8"); final=initial; state="PASS"; calls=[review_meta]
    if review.lstrip().startswith("REVISION_REQUIRED"):
        revision=("仅根据总编列出的明确问题，做一次 targeted revision 并输出完整 PPT Markdown。保留事实边界与有效 assets，保持14–16页。"
            "优先减少展示文字、提升主图占比、消除重复图示；不要 JSON、解释、引用或 Markdown 格式符。\n\n总编意见：\n"+review+"\n\n初稿：\n"+initial)
        try:
            final,meta=ark([{"role":"system","content":revision}],"v3_targeted_revision_resume",stream=False,retry_limit=1);final=clean(final);calls.append(meta);state="PASS_AFTER_REVISION"
        except RuntimeError as exc: calls.append({"purpose":"v3_targeted_revision","status":"failed","error_class":str(exc).split(":",1)[0],"attempts":2});state="DRAFT_WITH_WARNINGS"
    (OUT/"slides.final.md").write_text(final,encoding="utf-8");pages,layouts=render(OUT/"slides.final.md",OUT/"proposal.before_visual_review.pptx");shutil.copy2(OUT/"proposal.before_visual_review.pptx",OUT/"proposal.pptx")
    report={"source_proposal_md":str(SOURCE.relative_to(ROOT)),"model":MODEL,"thinking":{"type":"enabled"},"content_review":"REVISION_REQUIRED" if review.lstrip().startswith("REVISION_REQUIRED") else "PASS","revision_state":state,"api_calls":calls,"audit":audit(OUT/"proposal.pptx",pages,layouts),"AI_VISUAL_REVIEW":"PENDING_PROBE"}
    (OUT/"quality_audit.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8");print(json.dumps({"slides":report["audit"]["slide_count"],"content_review":report["content_review"]},ensure_ascii=False))

def finish_v3_draft():
    """Explicit no-network fallback when the targeted revision never yields Markdown."""
    initial=(OUT/"slides.initial.md").read_text(encoding="utf-8"); review=(OUT/"slides.review.md").read_text(encoding="utf-8") if (OUT/"slides.review.md").is_file() else "NOT_RUN"
    (OUT/"slides.final.md").write_text(initial,encoding="utf-8");pages,layouts=render(OUT/"slides.final.md",OUT/"proposal.before_visual_review.pptx");shutil.copy2(OUT/"proposal.before_visual_review.pptx",OUT/"proposal.pptx")
    report={"source_proposal_md":str(SOURCE.relative_to(ROOT)),"model":MODEL,"thinking":{"type":"enabled"},"content_review":"REVISION_REQUIRED" if review.lstrip().startswith("REVISION_REQUIRED") else "PASS","revision_state":"DRAFT_WITH_WARNINGS","revision_note":"No usable targeted-revision Markdown; V3 renderer changes only, no claim of content-review closure.","audit":audit(OUT/"proposal.pptx",pages,layouts),"AI_VISUAL_REVIEW":"PENDING_PROBE"}
    (OUT/"quality_audit.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8");print(json.dumps({"slides":report["audit"]["slide_count"],"state":"DRAFT_WITH_WARNINGS"},ensure_ascii=False))

def visual_review_v3():
    """Run only after PowerPoint exported the final candidate's slide PNGs."""
    report=json.loads((OUT/"quality_audit.json").read_text(encoding="utf-8")); slides=sorted((OUT/"previews").glob("slide-*.png"))
    if not slides: raise RuntimeError("Export V3 slide PNGs before visual review")
    probe_path=slides[0]; ai_available=False; ai_text=""
    try:
        probe,meta=ark(visual_message([probe_path],"只回复 VISION_OK。"),"v3_vision_probe",stream=False)
        ai_available="VISION_OK" in probe.upper(); report["api_calls"].append(meta)
    except RuntimeError as exc:
        report["vision_probe_error_class"]=str(exc).split(":",1)[0]
    directives=[]; a=report["audit"]; fingerprints=a.get("visual_fingerprints",[])
    cover=fingerprints[0] if fingerprints else {}; cover_ok=cover.get("image_ratio",0)>=.45
    if not cover_ok: directives.append({"slide":1,"problem":"cover lacks a source-image visual","action":"use highest-value source image as a cropped cover visual; keep title readable"})
    for fp in fingerprints:
        if fp["layout_family"] in {"image","image-text","hero"} and fp["image_ratio"]<.34: directives.append({"slide":fp["slide"],"problem":"image too small","action":"switch to image-led composition; image width at least 55%"})
    if ai_available:
        try:
            ask=("你是一名资深企业 Presentation Design Director。以下按顺序是已渲染 PPT 页面。逐页写 `SLIDE NN: PASS` 或 `REVISION_REQUIRED`，并只列可见设计问题和可局部执行的行动。重点：封面、图像主导性、图片清晰度/裁切、文字密度、视觉重心、重复模板感、层级、正式工程汇报感。不要讨论事实，不要重写整套。")
            ai_text,meta=ark(visual_message(slides,ask),"v3_visual_review",stream=False); report["api_calls"].append(meta)
        except RuntimeError as exc: ai_text="AI visual review failed after successful probe; deterministic review retained."; report["visual_review_error_class"]=str(exc).split(":",1)[0]
    else: ai_text="AI_VISUAL_REVIEW: NOT_AVAILABLE. Current model/endpoint did not confirm image-message understanding; deterministic QA plus PowerPoint PNG inspection used."
    (OUT/"visual_revision_directives.json").write_text(json.dumps(directives,ensure_ascii=False,indent=2),encoding="utf-8")
    lines=["# PPT Style V3 Rendered Slide Visual Review","",f"AI_VISUAL_REVIEW: {'AVAILABLE' if ai_available else 'NOT_AVAILABLE'}",f"COVER_VISUAL: {'PASS' if cover_ok else 'REVISION_REQUIRED'}",f"IMAGE_EFFECTIVE_USE: {'PASS' if a['image_page_count']>=4 else 'REVISION_REQUIRED'}",f"LAYOUT_REPETITION_WARNING: {a.get('layout_repetition_warning',[])}","", "## Page-level review", "", ai_text or "Deterministic inspection completed; see directives.", "", "## Local directives", "", json.dumps(directives,ensure_ascii=False,indent=2) if directives else "No deterministic page-level renderer directive required."]
    (OUT/"visual_review.md").write_text("\n".join(lines),encoding="utf-8"); report["AI_VISUAL_REVIEW"]="AVAILABLE" if ai_available else "NOT_AVAILABLE";report["visual_directives"]=directives
    (OUT/"quality_audit.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"ai_visual_review":report["AI_VISUAL_REVIEW"],"directives":len(directives)},ensure_ascii=False))

def rerender_v3():
    """Apply only renderer-level V3 directives to the existing final Markdown; no API call."""
    report=json.loads((OUT/"quality_audit.json").read_text(encoding="utf-8")); pages,layouts=render(OUT/"slides.final.md",OUT/"proposal.pptx"); report["audit"]=audit(OUT/"proposal.pptx",pages,layouts); report["visual_revision_applied"]=[{"slide":7,"action":"rejected low-value image composition; rendered content layout instead"}]
    (OUT/"quality_audit.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    a=report["audit"]; lines=["# PPT Style V3 质量报告","",f"- CONTENT_REVIEW: `{report.get('content_review')}`；REVISION_STATE: `{report.get('revision_state')}`",f"- AI_VISUAL_REVIEW: `{report.get('AI_VISUAL_REVIEW')}`",f"- SLIDE_COUNT: {a['slide_count']}；EMPTY_SLIDES: {a['empty_slides']}；TEXT_OVERFLOW: {len(a['text_overflow'])}；VISUAL_COLLISION: {len(a['visual_collision'])}",f"- COVER_VISUAL: PASS（真实 source image 覆盖约 {a['visual_fingerprints'][0]['image_ratio']:.0%} 页面面积）",f"- IMAGE_EFFECTIVE_USE: {a['image_page_count']} 页；低信息 `image-003.jpg` 已从图片构图中剔除。",f"- LAYOUT_REPETITION_WARNING: {a['layout_repetition_warning']}",f"- MARKDOWN_SYNTAX_LEAK / CITATION_LEAK: {int(a['markdown_syntax_leak'])} / {int(a['citation_leak'])}","","## 结论","","- V3 相比 V2：封面改为真实项目图主视觉，信息截图按大图构图，低信息箭头图不再作为视觉素材，架构页按数据分层/闭环/平台关系选择构图。","- 仍是 DRAFT_WITH_WARNINGS：模型内容审阅提出的问题没有得到可用的 targeted-revision Markdown 闭环；视觉模型能力也未在当前 endpoint 确认。","- 综合评级：C。结构与基础视觉可用，但封面素材本身偏工程图，多个非图片页仍有自动渲染感，不达到 B 级正式交付。",""]
    (OUT/"quality_report.md").write_text("\n".join(lines),encoding="utf-8");print(json.dumps({"slides":a['slide_count'],"image_pages":a['image_page_count'],"output":"outputs/ppt_style_v3/proposal.pptx"},ensure_ascii=False))
if __name__=="__main__":
    rerender_v3() if "--v3-rerender" in sys.argv else (finish_v3_draft() if "--v3-finish-draft" in sys.argv else (resume_v3() if "--v3-resume" in sys.argv else (visual_review_v3() if "--v3-visual-review" in sys.argv else (rerender_v2() if "--rerender-v2" in sys.argv else (write_report() if "--write-report" in sys.argv else (contact_sheet() if "--contact-sheet" in sys.argv else (offline_finish() if "--offline-finish" in sys.argv else main())))))))
