import hashlib,json,os,subprocess,sys
from pathlib import Path
from src.artifact_assets.manifest_builder import build_manifest
from src.wecom.ppt_checkpoint import CheckpointStore
ROOT=Path(__file__).resolve().parents[1]
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def test_cli_rebuilds_child_manifest_and_real_producer_resumes(tmp_path):
 task=tmp_path/'task';task.mkdir(); approved=task/'approved.md';approved.write_text('# Approved\n')
 source=tmp_path/'source-job';source.mkdir(); manifest=build_manifest(approved,task);(source/'assets_manifest.json').write_text(json.dumps(manifest))
 global_={'design_intent':'i','visual_personality':'v','color_system':{'primary':'#123456'},'typography_hierarchy':'t','spacing_rhythm':'s','image_treatment':'i','diagram_language':'d','brand_motif':'m','page_families':{'x':[1]},'density_strategy':'d','do_not_use':['x'],'page_directions':{'1':'x'}}
 scene={'background':{'color':'#FFFFFF'},'elements':[{'id':'title','type':'text','text':'Title','x':1,'y':1,'w':8,'h':1,'font_size':24,'z_order':1}]}
 g=source/'global_art_direction.json';g.write_text(json.dumps(global_)); cp=CheckpointStore(source,task_id='task-1',job_id='source-job',approved_sha256=sha(approved),assets_manifest_sha256=sha(source/'assets_manifest.json'),compatibility={'producer':'run_ppt_v4_ark_full20/v1','prompt':'v4-scene-prompt/v1','scene_schema':'v4-scene-graph/v1','pages':20},model_max_calls=26);cp.record(stage='global_art_direction',canonical_path=g,page=None,calls_used=7)
 for n,name in enumerate(('G','TOC','BACKGROUND','I'),1):
  p=source/'scene_graphs'/f'page_{n:02}_{name}.json';p.parent.mkdir(exist_ok=True);p.write_text(json.dumps(scene));cp.record(stage='page_scene_graph',canonical_path=p,page=n,calls_used=7)
 (source/'ark_calls.json').write_text(json.dumps([{'network_request_started':True}]*7))
 harness=tmp_path/'h.py'; harness.write_text(r"""import importlib.util,json,os,sys
from pathlib import Path
r=Path(os.environ['PPT_TEST_ROOT']);s=importlib.util.spec_from_file_location('v',r/'scripts'/'run_ppt_v4_ark_full20.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
scene={'background':{'color':'#FFFFFF'},'elements':[{'id':'title','type':'text','text':'Title','x':1,'y':1,'w':8,'h':1,'font_size':24,'z_order':1}]}
m.configure_production_visual_resources=lambda _:None;m.v4.PAD.ask=lambda messages,purpose,**k:(json.dumps(scene),{})
a=sys.argv;get=lambda n:Path(a[a.index(n)+1]);out=get('--output-dir');png=out/'x.png';png.write_bytes(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xcf\xc0\x00\x00\x04\x01\x01\x00\x18\xdd\x8d\xb1\x00\x00\x00\x00IEND\xaeB`\x82');sd=get('--scene-dir');m.v4.draw=lambda _:png;m.v4.render=lambda _:((sd/'assets').mkdir(),(sd/'assets'/'comking_official_logo.png').write_bytes(png.read_bytes()),[png]*20)[2];m.v4.contact=lambda *x:None;m.v4.page_png=lambda *x:png;m.v4.image_part=lambda *x:[]
m.produce(get('--input-md'),out,get('--scene-dir'),get('--visual-reference-root'),get('--manifest-path'),int(a[a.index('--model-max-calls')+1]),get('--resume-from-job-root'),skip_ai_review=True)
""")
 out=tmp_path/'child-job'; producer=[sys.executable,str(harness),'--produce','--input-md','${INPUT_MD}','--output-dir','${OUTPUT_DIR}','--scene-dir','${SCENE_DIR}','--visual-reference-root','${VISUAL_REFERENCE_ROOT}','--manifest-path','${OUTPUT_DIR}/producer_manifest.json','--model-max-calls','${MODEL_MAX_CALLS}','--skip-ai-review']
 cmd=[sys.executable,str(ROOT/'scripts'/'run_ppt_production.py'),'--input-md',str(approved),'--task-root',str(task),'--output-dir',str(out),'--output-pptx',str(out/'proposal.pptx'),'--run-log',str(out/'run.json'),'--enable-model','--model-scene-command-json',json.dumps(producer),'--model-max-calls','26','--visual-reference-root',str(task),'--task-id','task-1','--job-id','child-job','--resume-from-job-root',str(source),'--target-slides','20']
 done=subprocess.run(cmd,cwd=ROOT,env={**os.environ,'PPT_MODEL_LIVE_APPROVED':'1','PPT_TASK_ID':'task-1','PPT_JOB_ID':'child-job','PPT_TEST_ROOT':str(ROOT)},text=True,capture_output=True);assert done.returncode==0,done.stderr
 result=json.loads((out/'producer_manifest.json').read_text());assert result['historical_calls_used']==7 and result['model_calls_used']==23 and result['critic_status']==result['revision_status']=='NOT_RUN';assert len(list((out/'generated_scene_graph').glob('*.json')))==20;assert not (source/'scene_graphs'/'page_05_H.json').exists()
