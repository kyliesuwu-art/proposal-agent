"""Resumable V4 Art Director batches; independent of production pipeline."""
from __future__ import annotations
import json,re,shutil,time,urllib.request,urllib.error,socket
from pathlib import Path
R=Path(__file__).resolve().parent.parent; O=R/'outputs/ppt_style_v4'; M='doubao-seed-2-1-pro-260628'; N=5
def env():
 d={}
 for x in (R/'.env').read_text(encoding='utf8').splitlines():
  if '=' in x and not x.lstrip().startswith('#'): k,v=x.split('=',1);d[k.strip()]=v.strip().strip('\"\'')
 return d
def call(msg,purpose):
 e=env();u=e.get('ARK_BASE_URL','https://ark.cn-beijing.volces.com').removesuffix('/api/v3')+'/api/v3/chat/completions';b={'model':M,'thinking':{'type':'enabled'},'stream':True,'max_tokens':2400,'messages':msg};logs=[]
 for attempt in (1,2):
  st=time.perf_counter();ttft=None;parts=[];events=0;done=False;status=None;err=None
  try:
   q=urllib.request.Request(u,data=json.dumps(b,ensure_ascii=False).encode(),headers={'Authorization':'Bearer '+e['ARK_API_KEY'],'Content-Type':'application/json'},method='POST')
   with urllib.request.urlopen(q,timeout=300) as r:
    status=r.status
    for raw in r:
     line=raw.decode('utf8','replace').strip()
     if not line.startswith('data:'):continue
     v=line[5:].strip()
     if v=='[DONE]':done=True;break
     try:
      c=(json.loads(v).get('choices')or[{}])[0].get('delta',{}).get('content','')
      if isinstance(c,list):c=''.join(i.get('text','')for i in c if isinstance(i,dict))
      if c:ttft=ttft or time.perf_counter()-st;parts.append(c)
      events+=1
     except json.JSONDecodeError:err='SSE_JSON_PARSE_ERROR';break
  except urllib.error.HTTPError as x:status=x.code;err='HTTP_ERROR'
  except urllib.error.URLError as x:err='NETWORK_ERROR:'+type(x.reason).__name__
  except socket.timeout:err='TIMEOUT'
  meta={'purpose':purpose,'attempt':attempt,'http_status':status,'ttft_seconds':round(ttft,2)if ttft else None,'elapsed_seconds':round(time.perf_counter()-st,2),'sse_events':events,'done_received':done,'response_chars':sum(map(len,parts)),'exception':err,'parse_status':'PASS'if done and parts else'FAIL'};logs.append(meta)
  if meta['parse_status']=='PASS':return ''.join(parts),meta,logs
 raise RuntimeError(json.dumps(logs,ensure_ascii=False))
def arr(t):
 m=re.search(r'\[[\s\S]*\]',t)
 if not m:raise ValueError('NO_JSON_ARRAY')
 return json.loads(m.group())
def main():
 O.mkdir(parents=True,exist_ok=True);(O/'art_director').mkdir(exist_ok=True);src=R/'outputs/hospital_power_verify_20260911_attempt4_delivery';shutil.copytree(src/'assets',O/'assets',dirs_exist_ok=True)
 ps=[x.strip()for x in (R/'outputs/ppt_style_v3/slides.final.md').read_text(encoding='utf8').split('\n---\n')if x.strip()];ts=[next((x[2:].strip()for x in p.splitlines()if x.startswith('##')),'方案')for p in ps]
 for n in ('initial','review','final'):
  p=O/f'slides.{n}.md'
  if not p.exists():p.write_text('\n---\n'.join(ps),encoding='utf8')
 run=[];rp=O/'global_visual_rhythm.json'
 if rp.exists():rh=json.loads(rp.read_text(encoding='utf8'));run.append({'purpose':'global_rhythm','status':'SKIP_COMPLETED'})
 else:
  s=[{'slide':i+1,'title':t,'has_image':'!['in ps[i]}for i,t in enumerate(ts)];t,m,l=call([{'role':'system','content':'仅输出JSON数组，每项slide,rhythm_role,recommended_composition,image_priority。为企业能源PPT建立15页视觉节奏，不改事实。'},{'role':'user','content':json.dumps(s,ensure_ascii=False)}],'global_rhythm');rh={'rhythm':arr(t),'meta':m};rp.write_text(json.dumps(rh,ensure_ascii=False,indent=2),encoding='utf8');run+=l
 guide=(R/'config/PPT_VISUAL_DESIGN_GUIDE.md').read_text(encoding='utf8')
 for k,start in enumerate(range(1,16,N),1):
  end=min(15,start+N-1);p=O/'art_director'/f'batch_{k:02d}.json'
  try:
   old=json.loads(p.read_text(encoding='utf8'));ok=[x['slide']for x in old['plan']]==list(range(start,end+1))
  except Exception:ok=False
  if ok:run.append({'purpose':f'batch_{k:02d}','status':'SKIP_COMPLETED_BATCH'});continue
  local='\n---\n'.join(ps[start-1:end]);user=json.dumps({'range':[start,end],'rhythm':rh['rhythm'],'previous':ts[start-2]if start>1 else None,'next':ts[end]if end<15 else None,'slides':local},ensure_ascii=False)
  prompt='仅输出JSON数组，每项字段slide,composition,primary_visual,image_ratio,image_position,text_position,diagram_structure,card_count,avoid,visual_signature。你是企业PPT Art Director，只决定构图，不改事实/标题/图片路径。图片页55%-75%宽度，避免默认卡片。\n'+guide
  try:
   t,m,l=call([{'role':'system','content':prompt},{'role':'user','content':user}],f'batch_{k:02d}');plan=arr(t)
   if [x.get('slide')for x in plan]!=list(range(start,end+1)):raise ValueError('SLIDE_RANGE_MISMATCH')
   p.write_text(json.dumps({'plan':plan,'meta':m},ensure_ascii=False,indent=2),encoding='utf8');run+=l
  except Exception as x:
   run.append({'purpose':f'batch_{k:02d}','status':'BATCH_FAILED','exception':type(x).__name__});(O/'art_director_run.json').write_text(json.dumps(run,ensure_ascii=False,indent=2),encoding='utf8');raise
 merged=[]
 for k in range(1,4):merged+=json.loads((O/'art_director'/f'batch_{k:02d}.json').read_text(encoding='utf8'))['plan']
 if [x['slide']for x in merged]!=list(range(1,16)):raise RuntimeError('MERGE_INCOMPLETE')
 v={'global_rhythm':rh['rhythm'],'plan':merged};(O/'visual_plan.json').write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf8');(O/'visual_plan.md').write_text('# V4 Visual Plan\n\n```json\n'+json.dumps(v,ensure_ascii=False,indent=2)+'\n```\n',encoding='utf8');run.append({'purpose':'merge','status':'PASS','coverage':'15/15'});(O/'art_director_run.json').write_text(json.dumps(run,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps({'batches':'3/3','coverage':'15/15'},ensure_ascii=False))
if __name__=='__main__':main()
