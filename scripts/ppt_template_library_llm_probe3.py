"""Minimal LLM-decides / strict-writer-executes Template Library probe."""
from __future__ import annotations

import json, os, sys, urllib.request, time, socket
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'scripts'))
import ppt_template_library_v1 as lib

OUT=ROOT/'outputs/ppt_template_library_v1/llm_adapter_probe3'
MODEL='doubao-seed-2-1-pro-260628'
PAGES=[
 ('01','平台 / 架构页','建设统一智慧能源管理平台，实现医院园区能源全链路透明。以数字孪生+AI调度为核心，形成预测—调度—执行—溯源闭环；集成调度监控、计量计费、节能分析，预留光伏、虚拟电厂接口；配置管理驾驶舱。', 'ARCHITECTURE'),
 ('02','实施流程页','分三阶段平稳推进改造：第一阶段完成老旧配电设备更新、配电房环境改造与防洪防渗；第二阶段部署储能并预留分布式能源接口；第三阶段上线智慧能源管理平台，完成联调与人员培训。全过程遵循勘察—评审—施工—验收—移交—代维。', 'PROCESS'),
 ('03','收益 / 指标页','改造完成后可提升可靠性、运维效率与能耗管理能力。具体储能容量、实际投资、回收期、节能率及人员配置收益均需结合负荷、电价、设备台账与现场参数独立测算；行业案例仅作参考。', 'METRIC'),
]

def env():
 d={}
 for line in (ROOT/'.env').read_text(encoding='utf-8').splitlines():
  if '=' in line and not line.lstrip().startswith('#'):
   k,v=line.split('=',1);d[k.strip()]=v.strip().strip('"\'')
 return d

def catalog():
 full=json.loads((ROOT/'outputs/ppt_template_library_v1/layout_catalog.json').read_text(encoding='utf-8'))
 maps={p.stem:json.loads(p.read_text(encoding='utf-8')) for p in (ROOT/'outputs/ppt_template_library_v1/v2/slot_maps').glob('*.json') if p.stem!='source_shape_inventory'}
 return {'catalog':[{k:x.get(k) for k in ('prototype','best_for','slots','content_capacity')} for x in full], 'strict_slot_maps':maps}

def call(page):
 key=env()['ARK_API_KEY'];base=env().get('ARK_BASE_URL','https://ark.cn-beijing.volces.com').removesuffix('/api/v3')
 payload={'model':MODEL,'thinking':{'type':'enabled'},'stream':True,'max_tokens':1800,'messages':[{'role':'system','content':'你是企业PPT版式决策与内容压缩专家。只能根据输入事实，禁止编造数字。选择一个 catalog prototype；若不适合输出 NO_SUITABLE_PROTOTYPE。只输出合法 JSON：{"selected_prototype":"...","selection_reason":"...","slot_content":{"SLOT":"文本或图片文件名"}}。slot_content 只能使用所选严格 slot map 的 slot_id，所有必填文字槽必须有值；保留【待确认】边界。'},{'role':'user','content':json.dumps({'page':{'number':page[0],'kind':page[1],'content':page[2],'visual_priority':page[3]},'available_hospital_images':['image-001.jpg','image-002.jpg','image-004.jpg'],'prototype_catalog_and_slot_maps':catalog(),'company_visual_requirement':'继承真实公司 slide 的 Logo、页眉页脚、品牌装饰、字体、图片裁切；不得重绘。'},ensure_ascii=False)}]}
 req=urllib.request.Request(base+'/api/v3/chat/completions',data=json.dumps(payload,ensure_ascii=False).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
 started=time.perf_counter(); first=None; events=0; chunks=[]; done=False; status=None
 try:
  with urllib.request.urlopen(req,timeout=310) as r:
   status=r.status
   for raw in r:
    line=raw.decode('utf-8','replace').strip()
    if not line.startswith('data:'): continue
    data=line[5:].strip()
    if data=='[DONE]': done=True; break
    events+=1
    try:
     delta=(json.loads(data).get('choices') or [{}])[0].get('delta',{})
     text=delta.get('content','')
     if isinstance(text,list): text=''.join(x.get('text','') for x in text if isinstance(x,dict))
     if text:
      if first is None:first=time.perf_counter()-started
      chunks.append(text)
    except json.JSONDecodeError: pass
 except socket.timeout:
  return None,{'http_status':status,'ttft_seconds':None,'total_elapsed_seconds':round(time.perf_counter()-started,2),'sse_event_count':events,'done':False,'chars':0,'parse_status':'TIMEOUT_BEFORE_TTFT'}
 raw=''.join(chunks); meta={'http_status':status,'ttft_seconds':round(first,2) if first else None,'total_elapsed_seconds':round(time.perf_counter()-started,2),'sse_event_count':events,'done':done,'chars':len(raw)}
 try: return json.loads(raw.strip().removeprefix('```json').removesuffix('```').strip()),{**meta,'parse_status':'PASS' if done else 'NO_DONE'}
 except json.JSONDecodeError: return None,{**meta,'parse_status':'JSON_PARSE_FAIL'}

def main():
 OUT.mkdir(parents=True,exist_ok=True); plans=[]; decks=[]; traces=[]
 for number,kind,content,priority in PAGES:
  # Resume guard for the long-thinking stream: a parsed plan is durable state,
  # so never resend an already successful page request merely to finish a run.
  # This is call-layer caching only; it does not alter the prompt or adapter.
  plan_path=OUT/f'page_{number}_plan.json'
  page_dir=OUT/f'page_{number}'
  if plan_path.exists() and (page_dir/'slide.pptx').exists():
   plan=json.loads(plan_path.read_text(encoding='utf-8'))
   if plan.get('_call_metrics',{}).get('parse_status')=='PASS':
    trace_record=json.loads((page_dir/'slot_write_trace.json').read_text(encoding='utf-8')) if (page_dir/'slot_write_trace.json').exists() else {'trace':[]}
    decks.append(page_dir/'slide.pptx');traces.extend([{'page':number,**x} for x in trace_record.get('trace',[])]);plans.append(plan)
    continue
  request_record={'page_number':number,'kind':kind,'source_content':content,'model':MODEL,'thinking':'enabled','status':'STARTED'}
  (OUT/f'page_{number}_request.json').write_text(json.dumps(request_record,ensure_ascii=False,indent=2),encoding='utf-8')
  plan,meta=call((number,kind,content,priority)); request_record.update(meta); (OUT/f'page_{number}_request.json').write_text(json.dumps(request_record,ensure_ascii=False,indent=2),encoding='utf-8')
  if plan is None: return
  plan['page_number']=number;plan['source_content']=content;plan['_call_metrics']=meta
  (OUT/f'page_{number}_plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
  if plan['selected_prototype']=='NO_SUITABLE_PROTOTYPE': raise RuntimeError(f'NO_SUITABLE_PROTOTYPE {number}')
  map_name=plan['selected_prototype'].lower()
  page_dir.mkdir(exist_ok=True)
  result=lib.execute_explicit_slot_map(map_name=map_name,values=plan['slot_content'],output_dir=page_dir)
  decks.append(result['deck']);traces.extend([{'page':number,**x} for x in result['trace']]);plans.append(plan)
 (OUT/'merge_inputs.txt').write_bytes(b'\xef\xbb\xbf'+'\n'.join(str(x.resolve()) for x in decks).encode())
 deck=OUT/'probe3.pptx';cmd=['powershell','-ExecutionPolicy','Bypass','-File',str(ROOT/'scripts/ppt_template_library_merge.ps1'),'-InputListPath',str(OUT/'merge_inputs.txt'),'-OutputPath',str(deck)]
 import subprocess;subprocess.run(cmd,check=True,timeout=300)
 pngs=lib.render_with_powerpoint(deck,OUT/'rendered')
 for i,p in enumerate(pngs,1): lib.shutil.copy2(p,OUT/f'slide-{i:02}.png')
 lib.contact_sheet(pngs,OUT/'contact_sheet.png',cols=3)
 (OUT/'slot_write_trace.json').write_text(json.dumps(traces,ensure_ascii=False,indent=2),encoding='utf-8')
 (OUT/'validation_report.md').write_text('# LLM Adapter Probe3 validation\n\n- 每页由 Seed 2.1 Pro thinking 独立选择 prototype 和 slot content。\n- 写入仅经 strict slot map executor；无 generic fallback。\n- PowerPoint COM 已实渲。\n',encoding='utf-8')
if __name__=='__main__':main()
