"""Challenge Probe5: LLM-only layout decision, then strict shape-id execution.

This isolated experiment never invokes legacy full15 / generic placement code.
"""
from __future__ import annotations
import json, os, socket, sys, time, urllib.request, subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'scripts'))
import ppt_template_library_v1 as lib
OUT=ROOT/'outputs/ppt_template_library_v1/challenge_probe5'
MODEL='doubao-seed-2-1-pro-260628'
PAGES=[
 ('03','核心医疗负荷供电风险','手术室、ICU、急诊等一级负荷供电中断将直接威胁患者安全。运行线路和开关设备老化可能导致绝缘失效、温升异常和起火。人工巡检响应滞后且存在盲区。园区总供电容量、峰值负荷与设备台账待现场核实。','CAPABILITY_CARDS','卡片页只留下标题或少量勾选项，四类风险没有进入独立内容区，页面空洞。'),
 ('06','储能与应急供电保障','适配医院场景选用电化学储能，兼顾应急保障与日常调峰。优先考虑磷酸铁锂能量型或容量型路线，匹配长时后备需求。采用模块化、智能组串和主动均衡设计，保障运行安全。配置智慧BMS与AI热管理，支持充放电策略控制。具体容量和配置待现场条件深化。','CAPABILITY_CARDS','卡片模板容量与三项技术说明不匹配，原页成为空白骨架。'),
 ('10','AI预警与运维协同能力','部署AI预警训练运维模型，支持自然语言交互，自动分析告警并匹配规程处置方案。关联时序数据定位异常根因，缩短故障响应时间。结合边缘计算实现快速预警，支撑后勤人员制定应急策略。打通集中与移动运维通道，降低人工巡检工作量。','PLATFORM_SCREENSHOT','截图页保留源项目视觉，医院图片和内容关系弱，说明区未承载完整能力。'),
 ('11','安全合规与数据保护','系统采用国产化技术路线，面向医疗行业数据安全管理要求。数据采集、传输、存储、分析需满足适用的网络安全等级保护要求。平台与医院既有后勤、信息系统的接口标准，待相关部门确认后落地。','CAPABILITY_CARDS','能力卡片复用过多，且合规边界和待确认条件没有被完整呈现。'),
 ('14','项目落地前的五类参数确认','负荷边界：核实园区总供电容量、峰值负荷、典型日负荷曲线。并网条件：确认院内配电拓扑和电网公司接入要求。空间资源：明确储能设备、传感装置的安装空间与改造范围。市场规则：核实当地峰平谷电价和基本电费政策。管理要求：确认投资预算、等保标准和数据接口规范。','BENEFIT_COMPARISON','五类待确认参数被放进比较页，信息被压缩成空白或不相关的效益表达。'),
]
def env():
 d={}
 for line in (ROOT/'.env').read_text(encoding='utf-8').splitlines():
  if '=' in line and not line.lstrip().startswith('#'):
   k,v=line.split('=',1);d[k.strip()]=v.strip().strip('"').strip("'")
 return d
def strict_maps():
 maps=[]
 for p in sorted((ROOT/'outputs/ppt_template_library_v1/v2/slot_maps').glob('*.json')):
  if p.name=='source_shape_inventory.json':continue
  m=json.loads(p.read_text(encoding='utf-8'))
  maps.append({'prototype':m['prototype'],'map_name':p.stem,'strict_writer_available':True,'slots':[{'slot_id':s['slot_id'],'type':s['type'],'required':s['required'],'max_chars':s.get('max_chars')} for s in m['slots'] if s['editable']]})
 return maps
def catalog():
 raw=json.loads((ROOT/'outputs/ppt_template_library_v1/catalog/layout_catalog.json').read_text(encoding='utf-8'))
 executable={x['prototype'] for x in strict_maps()}
 return [{'prototype':x['prototype'],'best_for':x['best_for'],'content_capacity':x['content_capacity'],'style_family':x['style_family'],'strict_writer_available':x['prototype'] in executable} for x in raw]
def call(page):
 key=env()['ARK_API_KEY']; base=env().get('ARK_BASE_URL','https://ark.cn-beijing.volces.com').removesuffix('/api/v3')
 body={'page':{'number':page[0],'title':page[1],'full_content':page[2]},'previous_selection':{'prototype':page[3],'observed_powerpoint_problem':page[4]},'available_hospital_images':['image-001.jpg','image-002.jpg','image-004.jpg'],'prototype_catalog':catalog(),'strict_slot_map_and_capacity_summaries':strict_maps(),'company_visual_requirements':'继承真实公司PPT的Logo、页眉页脚、字体、品牌装饰、裁切和阴影；不重绘。','task':'独立判断上一版prototype是否合适。仅可选择strict_writer_available=true的prototype；如果其他catalog原型才真正适合或全部不适合，返回NO_SUITABLE_PROTOTYPE。不要为了凑页选择。若可选，按slot map输出每个可编辑slot的完整内容，图片槽写可用文件名；压缩演示语言但不编造事实、数字，保留待确认边界。'}
 payload={'model':MODEL,'thinking':{'type':'enabled'},'stream':True,'max_tokens':2200,'messages':[{'role':'system','content':'你是企业PPT设计师。必须独立复核上一页模板选择并给出可审计结果。只输出合法JSON：{"previous_prototype_assessment":"suitable|not_suitable","selected_prototype":"...|NO_SUITABLE_PROTOTYPE","selection_reason":"...","switch_reason":"...","compression_notes":["..."],"slot_content":{"SLOT":"..."}}。不得编造事实或数字。'},{'role':'user','content':json.dumps(body,ensure_ascii=False)}]}
 req=urllib.request.Request(base+'/api/v3/chat/completions',data=json.dumps(payload,ensure_ascii=False).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
 started=time.perf_counter();first=None;events=0;chunks=[];done=False;status=None
 try:
  with urllib.request.urlopen(req,timeout=310) as r:
   status=r.status
   for raw in r:
    line=raw.decode('utf-8','replace').strip()
    if not line.startswith('data:'):continue
    events+=1; data=line[5:].strip()
    if data=='[DONE]':done=True;break
    try:
     evt=json.loads(data); text=evt.get('choices',[{}])[0].get('delta',{}).get('content','')
     if isinstance(text,list):text=''.join(x.get('text','') for x in text if isinstance(x,dict))
     if text:
      if first is None:first=time.perf_counter()-started
      chunks.append(text)
    except json.JSONDecodeError:pass
 except socket.timeout:
  return None,{'http_status':status,'ttft_seconds':None,'total_elapsed_seconds':round(time.perf_counter()-started,2),'sse_event_count':events,'done':False,'chars':0,'parse_status':'TIMEOUT_BEFORE_TTFT'}
 raw=''.join(chunks);meta={'http_status':status,'ttft_seconds':round(first,2) if first else None,'total_elapsed_seconds':round(time.perf_counter()-started,2),'sse_event_count':events,'done':done,'chars':len(raw)}
 try:return json.loads(raw.strip().removeprefix('```json').removesuffix('```').strip()),{**meta,'parse_status':'PASS' if done else 'NO_DONE'}
 except json.JSONDecodeError:return None,{**meta,'parse_status':'JSON_PARSE_FAIL'}
def main():
 OUT.mkdir(parents=True,exist_ok=True);decks=[];summary=[];traces=[]
 valid={x['prototype']:x['map_name'] for x in strict_maps()}
 for page in PAGES:
  no=page[0]; plan_path=OUT/f'page_{no}_plan.json'; page_dir=OUT/f'page_{no}'
  if plan_path.exists():
   plan=json.loads(plan_path.read_text(encoding='utf-8'))
   if plan.get('_call_metrics',{}).get('parse_status')=='PASS':
    summary.append(plan)
    if (page_dir/'slide.pptx').exists(): decks.append(page_dir/'slide.pptx')
    continue
  record={'page_number':no,'model':MODEL,'thinking':'enabled','stream':True,'status':'STARTED'};(OUT/f'page_{no}_request.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
  plan,meta=call(page);record.update(meta);(OUT/f'page_{no}_request.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
  if plan is None:return
  plan.update({'page_number':no,'original_content':page[2],'previous_prototype':page[3],'previous_problem':page[4],'_call_metrics':meta});plan_path.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8');summary.append(plan)
  selected=plan.get('selected_prototype')
  if selected not in valid:
   # An explicit NO_SUITABLE_PROTOTYPE is a valid decision outcome.  Keep
   # evaluating the remaining challenge pages; never substitute a template.
   continue
  page_dir.mkdir(exist_ok=True); result=lib.execute_explicit_slot_map(map_name=valid[selected],values=plan.get('slot_content',{}),output_dir=page_dir);decks.append(result['deck']);traces.extend([{'slide':no,**x} for x in result['trace']])
 if len(decks) != len(PAGES):
  (OUT/'plans.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
  (OUT/'validation_report.md').write_text('# Challenge Probe5\n\n- At least one page returned NO_SUITABLE_PROTOTYPE. No substituted or partial PPTX was generated.\n',encoding='utf-8')
  return
 (OUT/'merge_inputs.txt').write_bytes(b'\xef\xbb\xbf'+'\n'.join(str(x.resolve()) for x in decks).encode())
 deck=OUT/'challenge_probe5.pptx';subprocess.run(['powershell','-ExecutionPolicy','Bypass','-File',str(ROOT/'scripts/ppt_template_library_merge.ps1'),'-InputListPath',str(OUT/'merge_inputs.txt'),'-OutputPath',str(deck)],cwd=ROOT,check=True,timeout=300)
 pngs=lib.render_with_powerpoint(deck,OUT/'rendered')
 for i,p in enumerate(pngs,1):lib.shutil.copy2(p,OUT/f'slide-{i:02}.png')
 lib.contact_sheet(pngs,OUT/'contact_sheet.png',cols=3)
 (OUT/'plans.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');(OUT/'slot_write_trace.json').write_text(json.dumps(traces,ensure_ascii=False,indent=2),encoding='utf-8')
 (OUT/'validation_report.md').write_text('# Challenge Probe5\n\n- Each page used a single serial Seed thinking call.\n- No legacy generic writer path was called.\n- Strict shape-id slot write only; review rendered slides for content/template fit.\n',encoding='utf-8')
if __name__=='__main__':main()
