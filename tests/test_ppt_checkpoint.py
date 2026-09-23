import json
from pathlib import Path
import pytest
from src.wecom.ppt_checkpoint import CheckpointStore, ResumeRejected, import_resume_state, recompute_call_budget

def make(tmp, pages=(1,2), calls=None):
 root=tmp/'parent'; root.mkdir(parents=True); (root/'global.json').write_text('{"g":1}'); (root/'scenes').mkdir()
 store=CheckpointStore(root,task_id='task',job_id='job',approved_sha256='a',assets_manifest_sha256='m',compatibility={'producer':'p','prompt':'q','scene_schema':'s','pages':20},model_max_calls=26)
 store.record(stage='global_art_direction',canonical_path=root/'global.json',page=None,calls_used=1)
 for n in pages:
  f=root/'scenes'/f'page_{n:02}.json';f.write_text(json.dumps({'page':n}));store.record(stage='page_scene_graph',canonical_path=f,page=n,calls_used=n+1)
 (root/'ark_calls.json').write_text(json.dumps(calls if calls is not None else [{'network_request_started':True} for _ in range(3)]))
 return root

def expected(): return {'source_task_id':'task','source_job_id':'ignored','approved_md_sha256':'a','assets_manifest_sha256':'m','producer_compatibility_version':'p','prompt_contract_version':'q','scene_schema_version':'s','total_slide_count':20,'model_max_calls':26}
def state(root, child): return import_resume_state(parent_root=root,child_root=child,expected=expected(),validate_global=lambda x:None,validate_page=lambda n,x:None)
def test_atomic_manifest_and_contiguous_import(tmp_path):
 root=make(tmp_path,(1,2,10)); child=tmp_path/'child'; got=state(root,child)
 assert got.next_page==3 and [n for n,_ in got.page_paths]==[1,2] and (child/'global_art_direction.json').is_file()
 assert not list(root.rglob('*.tmp')) and not (child/'scene_graphs'/'page_10.json').exists()
def test_temporary_or_missing_manifest_is_not_checkpoint(tmp_path):
 root=tmp_path/'old';root.mkdir();(root/'ppt_generation_checkpoints.json.tmp').write_text('{}')
 with pytest.raises(ResumeRejected): state(root,tmp_path/'child')
def test_hash_json_identity_and_escape_rejected(tmp_path):
 root=make(tmp_path); manifest=root/'ppt_generation_checkpoints.json'; data=json.loads(manifest.read_text());data['pages'][0]['path']='../escape.json';manifest.write_text(json.dumps(data))
 with pytest.raises(ResumeRejected): state(root,tmp_path/'child')
def test_manifest_and_canonical_tampering_rejected(tmp_path):
 root=make(tmp_path); (root/'scenes'/'page_01.json').write_text('{')
 with pytest.raises(ResumeRejected): state(root,tmp_path/'child')
 root=make(tmp_path/'two'); data=json.loads((root/'ppt_generation_checkpoints.json').read_text());data.pop('approved_md_sha256');(root/'ppt_generation_checkpoints.json').write_text(json.dumps(data))
 with pytest.raises(ResumeRejected): state(root,tmp_path/'child2')
def test_compatibility_and_budget_recomputed(tmp_path):
 root=make(tmp_path,calls=[{'network_request_started':True},{'network_request_started':True,'success':False},{'network_request_started':False}]); got=state(root,tmp_path/'child')
 assert got.historical_calls_used==2 and got.remaining_budget==24 and recompute_call_budget(root,26)==2
 bad=expected();bad['prompt_contract_version']='other'
 with pytest.raises(ResumeRejected): import_resume_state(parent_root=root,child_root=tmp_path/'other',expected=bad,validate_global=lambda x:None,validate_page=lambda n,x:None)
def test_source_is_unchanged_and_validator_runs(tmp_path):
 root=make(tmp_path); before={p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}; seen=[]
 import_resume_state(parent_root=root,child_root=tmp_path/'child',expected=expected(),validate_global=lambda x:seen.append('g'),validate_page=lambda n,x:seen.append(n))
 assert before=={p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()} and seen==['g',1,2]