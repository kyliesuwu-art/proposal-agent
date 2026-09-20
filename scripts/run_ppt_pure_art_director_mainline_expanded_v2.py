"""Local V2 expansion of the approved Pure Art Director mainline candidate.

This is intentionally a deterministic editorial assembly: it reuses 15
approved scenes, adds six evidence-bounded pages, and makes no model call.
"""
from __future__ import annotations
import json, re, shutil, subprocess
from copy import deepcopy
from pathlib import Path
from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.util import Inches
from run_ppt_pure_art_director_editorial import ROOT, SOURCE, PAD

INPUT = SOURCE / "mainline_candidate"
OUT = SOURCE / "mainline_expanded_v2"
ORDER = ["G", "TOC", "BACKGROUND", "I", "H", "ARCH", "RELIABILITY", "A", "J", "B", "C", "K", "L", "D", "ORGANIZATION", "N", "F", "M", "E", "CLOSE"]
PROJECT = "医院园区智慧配电改造方案"

def tx(i, x,y,w,h,text,size=18,color="#1F2937",weight="normal",align="left",z=2):
    return {"id":i,"type":"text","x":x,"y":y,"w":w,"h":h,"text":text,"font_size":size,"font_weight":weight,"alignment":align,"color":color,"z_order":z}
def rect(i,x,y,w,h,fill="#EAF2FA",z=1): return {"id":i,"type":"rounded_rectangle","x":x,"y":y,"w":w,"h":h,"fill":fill,"z_order":z}
def dot(i,x,y,fill="#005EB8"): return {"id":i,"type":"circle","x":x,"y":y,"w":.14,"h":.14,"fill":fill,"z_order":2}
def standard(title, subtitle, blocks):
    e=[tx("title",.65,.82,11.9,.55,title,30,"#1F497D","bold"),tx("subtitle",.67,1.48,11.3,.35,subtitle,17,"#526577")]
    for n,(head,body) in enumerate(blocks):
        y=2.18+n*1.27; e += [rect(f"card{n}",.75,y,11.8,.96,"#F4F8FC"),dot(f"dot{n}",1.05,y+.26),tx(f"head{n}",1.35,y+.16,2.7,.28,head,18,"#005EB8","bold"),tx(f"body{n}",4.0,y+.14,7.8,.45,body,17)]
    return {"background":"#FFFFFF","elements":e}
def replace(scene, ident, **values):
    for e in scene["elements"]:
        if e.get("id")==ident: e.update(values)

def new_scenes():
    return {
      "TOC": {"background":"#FFFFFF","elements":[tx("kicker",.7,.9,2,.3,"汇报目录",16,"#005EB8","bold"),tx("title",.7,1.3,6,.7,"从供电风险到可落地的改造路径",32,"#1F497D","bold"),
        *sum(([rect(f"r{i}",1.0,2.35+i*.9,10.9,.62,"#F4F8FC"),tx(f"n{i}",1.25,2.48+i*.9,.8,.3,f"0{i+1}",22,"#005EB8","bold"),tx(f"t{i}",2.2,2.48+i*.9,7.8,.3,t,20,"#1F497D","bold"),tx(f"s{i}",9.2,2.5+i*.9,2.2,.25,s,15,"#607080","right")] for i,(t,s) in enumerate([("项目理解与建设目标","风险与原则"),("总体方案与关键能力","供电、感知、平台"),("实施路径与保障","组织与安全"),("投资测算与预期价值","边界与下一步")])),[])]},
      "BACKGROUND": standard("项目背景与建设必要性","连续诊疗任务要求以可核查的供电保障与数字化运维作为改造基础",[("连续性要求","重要医疗负荷对供电连续性要求高，具体负荷边界需现场校核"),("运行风险","设备老化、人工巡检盲区与异常发现滞后会放大运行风险"),("建设方向","通过感知、预警与辅助决策，把处置重心前移至异常早期"),("深化前提","容量、负荷曲线、设备台账与接口条件将在勘察阶段确认")]),
      "ARCH": {"background":"#FFFFFF","elements":[tx("title",.65,.82,12,.55,"总体方案架构",30,"#1F497D","bold"),tx("sub",.67,1.48,11.6,.3,"高可靠供电、感知采集、储能保障、平台决策与安全运维形成闭环",17,"#526577"),
        *sum(([rect(f"layer{i}",1.0,2.05+i*.82,11.2,.58,c),tx(f"l{i}",1.35,2.20+i*.82,3.2,.25,h,19,"#005EB8","bold"),tx(f"d{i}",4.4,2.20+i*.82,7.2,.25,d,17,"#334155")] for i,(h,d,c) in enumerate([("高可靠供电与设备升级","重要负荷分级、配电保护与备用保障", "#EAF2FA"),("智能感知与边缘采集","电力参数、设备状态与动环数据统一接入", "#F6FAFD"),("储能及应急保障","削峰、后备与可控资源协调", "#EAF2FA"),("智慧能源管理与辅助决策","预判、调度、执行、溯源闭环", "#F6FAFD"),("运维管理与安全合规","在线监测、实施控制与深化校核", "#EAF2FA")])),[])]},
      "RELIABILITY": standard("高可靠供电保障架构","以负荷分级和多电源协同为原则，具体接线、容量与切换策略将在初步设计阶段校核",[("重要负荷分级","按重要性识别核心医疗负荷、重要保障负荷与一般可调负荷"),("正常与备用","在既有配电体系基础上评估正常电源、备用电源与后备电源关系"),("应急与调控","储能、柴发、UPS、EPS 等可选保障资源按现场条件统筹论证"),("保护与质量","配电保护、电能质量与切换逻辑纳入现场勘察和方案深化")]),
      "ORGANIZATION": standard("医疗秩序保障与项目实施组织","分阶段实施配合勘察、评审、施工、验收与移交，减少对正常医疗秩序的影响",[("前期校核","后勤、基建、信息等相关部门共同确认台账、边界与接口"),("窗口管理","根据现场条件制定施工组织、停送电与应急预案，并履行评审确认"),("联调验收","设备安装后开展联调、培训、验收与资料移交"),("持续运维","将巡检、告警处置与维护计划纳入后续运维管理")]),
      "CLOSE": {"background":"#FFFFFF","elements":[tx("thanks",2.0,2.5,9.3,.7,"感谢聆听",38,"#1F497D","bold","center"),tx("more",2.0,3.42,9.3,.38,"期待进一步交流",21,"#526577","center"),rect("line",4.78,4.25,3.75,.025,"#005EB8")]}
    }

def edit_existing(key, scene):
    s=deepcopy(scene)
    if key=="G":
        # Prevent the title word “智慧” from being isolated.
        biggest=max((e for e in s["elements"] if e.get("type")=="text"),key=lambda e:e.get("font_size",0)); biggest.update({"w":8.0,"h":1.25,"font_size":34})
    elif key=="H":
        for e in s["elements"]:
            if e.get("type")=="image": e.update({"x":6.2,"y":1.05,"w":5.8,"h":5.28,"crop":"contain"})
    elif key=="C":
        replace(s,"title",text="多维安全预警与在线辅助值守")
        for e in s["elements"]:
            if e.get("type")=="text" and "24" in e.get("text",""): e["text"]=e["text"].replace("24小时无人值守","24小时在线监测与辅助值守")
    elif key=="L":
        s=standard("负荷优先级与动态调控","在不编造容量与具体科室的前提下，形成可在深化阶段校核的调控关系",[("核心医疗负荷","极端工况下优先保障连续供电"),("重要保障负荷","按现场策略参与应急保障与分级调控"),("一般可调负荷","在限电或故障场景中按策略逐步压减"),("动态平衡","统筹储能、后备电源与柔性负荷实现就地功率平衡")])
    elif key=="N":
        s=standard("运行安全、数据安全与实施控制","按照适用规范和项目管理要求进行设计，接口、安全措施与合规性在深化阶段完成校核",[("运行安全","围绕配电保护、动环监测、告警处置与应急预案开展设计"),("数据安全","对数据采集、传输、存储与访问控制进行分层设计"),("实施控制","通过勘察、评审、测试、验收与移交形成过程留痕")])
    elif key=="M":
        s=standard("投资收益测算框架","同类案例仅作参考，本项目配置、报价和收益将在现场参数确认后独立测算",[("收益来源","峰谷电价差、负荷管理与运维效率等因素共同影响"),("测算输入","负荷曲线、电价政策、充放电效率、衰减率与设备选型"),("案例参考","500kW/1000kWh、约66万元为同类案例参考，并非本项目配置或报价"),("项目输出","形成储能容量、投资、收益与回收期的专项测算结论")])
    elif key=="E":
        s={"background":"#FFFFFF","elements":[{"id":"evidence_strip","type":"image","x":.75,"y":1.05,"w":11.8,"h":2.92,"image_source":"assets/image-005.png","crop":"contain"},tx("title",.75,4.28,11.5,.45,"预期价值与下一步工作",28,"#1F497D","bold"),tx("value",.75,4.95,5.6,.7,"可靠性、运维效率与节能降碳效益均以同类案例为参考，实际效果需独立测算。",18,"#334155"),tx("next",6.75,4.95,5.4,.7,"建议完成现场勘察、参数收集、方案深化与专项投资测算四项工作。",18,"#334155"),tx("note",.75,6.22,11.7,.25,"案例数字不构成本项目承诺；具体配置、投资和效益均待确认。",16,"#607080","normal","center")]}
    return s

def chrome(slide,n,total,source_shape,cover=False):
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb=RGBColor(*PAD.BG)
    bar=slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE,0,0, Inches(.13), Inches(PAD.H)); bar.fill.solid(); bar.fill.fore_color.rgb=RGBColor(*PAD.BLUE); bar.line.fill.background()
    clone=deepcopy(source_shape._element); slide.shapes._spTree.insert_element_before(clone,"p:extLst"); logo=next(x for x in slide.shapes if x.name=="brand_wordmark"); logo.left,logo.top,logo.width,logo.height=Inches(.62),Inches(.17),Inches(1.45 if cover else 1.35),Inches(.25)
    line=slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, Inches(.65), Inches(6.91), Inches(12.05), Inches(.02)); line.fill.solid(); line.fill.fore_color.rgb=RGBColor(*PAD.LIGHT_BLUE); line.line.fill.background()
    PAD.add_text(slide,tx("footer_project",.65,7.00,4.8,.22,PROJECT,10,"#405466")); PAD.add_text(slide,tx("footer_page",11.72,6.99,.92,.22,f"{n:02} / {total:02}",10,"#1F497D","bold","right"))

def main():
    OUT.mkdir(parents=True,exist_ok=True); (OUT/"scene_graphs").mkdir(exist_ok=True)
    existing={p.stem.split("_")[1]:json.loads(p.read_text(encoding="utf-8")) for p in (INPUT/"scene_graphs").glob("page_*_scene.json")}
    new=new_scenes(); scenes={}; mapping=[]
    for n,key in enumerate(ORDER,1):
        source=existing.get(key); kind="NEW" if source is None else ("LOCAL_EDIT" if key in {"G","H","C","L","N","M","E"} else "KEEP")
        scene=new[key] if key in new else edit_existing(key,source)
        scenes[key]=scene; (OUT/"scene_graphs"/f"page_{n:02}_{key}.json").write_text(json.dumps(scene,ensure_ascii=False,indent=2),encoding="utf-8")
        mapping.append({"final_page":n,"source":key if source else None,"status":kind,"scene_graph":f"scene_graphs/page_{n:02}_{key}.json"})
    prs=Presentation(); prs.slide_width=Inches(PAD.W); prs.slide_height=Inches(PAD.H); blank=prs.slide_layouts[6]
    source_p=Presentation(INPUT/"pure_art_director_full15_mainline_candidate.pptx"); logo=next(x for x in source_p.slides[0].shapes if x.name=="brand_wordmark")
    for n,key in enumerate(ORDER,1):
        slide=prs.slides.add_slide(blank); chrome(slide,n,len(ORDER),logo,key=="G"); positions={}
        for raw in sorted(scenes[key]["elements"],key=lambda x:float(x.get("z_order",x.get("z",10)))):
            e=PAD.clamp(dict(raw)); typ=e.get("type")
            if typ in {"text","number","caption"}: shape=PAD.add_text(slide,e)
            elif typ=="image": shape=PAD.add_image(slide,e)
            elif typ in {"rectangle","rounded_rectangle","circle","callout"}: shape=PAD.add_shape(slide,e)
            elif typ in {"line","arrow"}: shape=PAD.add_line(slide,e,positions)
            else: continue
            positions[e["id"]]=shape
    deck=OUT/"pure_art_director_mainline_expanded_v2.pptx"; prs.save(deck)
    pages=OUT/"pages"; subprocess.run(["powershell","-ExecutionPolicy","Bypass","-File",str(ROOT/"scripts"/"render_pptx_windows.ps1"),"-InputPath",str(deck),"-OutputDir",str(pages)],cwd=ROOT,check=True,timeout=420)
    cmd=f"$a=New-Object -ComObject PowerPoint.Application;$a.Visible=$true;$p=$a.Presentations.Open('{deck.resolve()}', $true,$true,$false);$p.SaveAs('{(OUT/'pure_art_director_mainline_expanded_v2.pdf').resolve()}',32);$p.Close();$a.Quit()"; subprocess.run(["powershell","-NoProfile","-Command",cmd],cwd=ROOT,check=True,timeout=420)
    pngs=sorted((pages/"slide").glob("*.png"),key=lambda p:int(re.search(r"(\d+)$",p.stem).group(1))); first=Image.open(pngs[0]).convert("RGB"); w,g,h=310,10,24; ht=round(first.height*w/first.width); sheet=Image.new("RGB",(w*4+g*3,(ht+h)*5),"white"); pen=ImageDraw.Draw(sheet)
    for i,p in enumerate(pngs): r,c=divmod(i,4); x,y=c*(w+g),r*(ht+h); pen.text((x+3,y+3),f"{i+1:02} | {ORDER[i]}",fill=PAD.TEXT); sheet.paste(Image.open(p).convert("RGB").resize((w,ht)),(x,y+h))
    sheet.save(OUT/"contact_sheet.png")
    manifest={"input":"outputs/ppt_pure_art_director/full15/mainline_candidate/pure_art_director_full15_mainline_candidate.pptx","pages":mapping,"ark_calls":0,"low_res_source":{"H":"assets/image-001.jpg (537x489)","E":"assets/image-005.png (1476x366)"}}
    (OUT/"mainline_expanded_v2_manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    (OUT/"validation_report.md").write_text("# Mainline Expanded V2 validation\n\n- Ark calls: 0\n- PowerPoint COM pages: 20\n- LOW_RES_SOURCE: H image-001.jpg; display area reduced\n- LOW_RES_SOURCE: E image-005.png; displayed as proportional evidence strip\n",encoding="utf-8")
if __name__=="__main__": main()
