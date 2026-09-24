"""File-only, immutable primitives for PPT producer checkpoints."""
from __future__ import annotations
import hashlib, json, os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

SCHEMA_VERSION="ppt-generation-checkpoint/v1"
MANIFEST_NAME="ppt_generation_checkpoints.json"
_REQUIRED=("schema_version","source_task_id","source_job_id","approved_md_sha256","assets_manifest_sha256","producer_compatibility_version","prompt_contract_version","scene_schema_version","total_slide_count","model_max_calls","ark_attempts_used","created_at","global","pages")
class ResumeRejected(RuntimeError): pass
def sha256(path:Path)->str: return hashlib.sha256(path.read_bytes()).hexdigest()
def _link(path:Path)->bool:
 try: s=path.lstat()
 except FileNotFoundError: return False
 return path.is_symlink() or getattr(path,"is_junction",lambda:False)() or bool(getattr(s,"st_file_attributes",0)&0x0400)
def _inside(path:Path,root:Path)->Path:
 if _link(path): raise ResumeRejected("checkpoint path is a link or reparse point")
 try: value=path.resolve(strict=True); value.relative_to(root.resolve(strict=True))
 except (FileNotFoundError,ValueError) as e: raise ResumeRejected("checkpoint path escapes source Job root") from e
 return value
def _atomic(path:Path,value:dict)->None:
 path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_name(path.name+".tmp")
 try:
  with tmp.open("w",encoding="utf-8",newline="\n") as f: json.dump(value,f,ensure_ascii=False,sort_keys=True,indent=2); f.flush(); os.fsync(f.fileno())
  os.replace(tmp,path)
 finally:
  if tmp.exists(): tmp.unlink()
def atomic_json_checkpoint(path:Path,value:dict)->None: _atomic(path,value)
@dataclass(frozen=True)
class ResumeState:
 global_direction:Path|None; page_paths:tuple[tuple[int,Path],...]; next_page:int; historical_calls_used:int; remaining_budget:int
def _validate(manifest:dict)->None:
 if not isinstance(manifest,dict) or set(manifest)!=(set(_REQUIRED)) : raise ResumeRejected("checkpoint manifest has unknown or missing fields")
 if manifest["schema_version"]!=SCHEMA_VERSION or not isinstance(manifest["pages"],list) or not isinstance(manifest["global"],dict): raise ResumeRejected("checkpoint manifest schema is invalid")
 for key in _REQUIRED[:-3]:
  if not manifest[key] and key not in {"ark_attempts_used"}: raise ResumeRejected("checkpoint manifest field is empty")
class CheckpointStore:
 def __init__(self,job_root:Path,*,task_id:str,job_id:str,approved_sha256:str,assets_manifest_sha256:str,compatibility:dict,model_max_calls:int)->None:
  self.root=job_root.resolve(); self.path=self.root/MANIFEST_NAME; self.base={"schema_version":SCHEMA_VERSION,"source_task_id":task_id,"source_job_id":job_id,"approved_md_sha256":approved_sha256,"assets_manifest_sha256":assets_manifest_sha256,"producer_compatibility_version":compatibility["producer"],"prompt_contract_version":compatibility["prompt"],"scene_schema_version":compatibility["scene_schema"],"total_slide_count":compatibility["pages"],"model_max_calls":model_max_calls,"ark_attempts_used":0,"created_at":datetime.now(timezone.utc).isoformat(),"global":{},"pages":[]}
 def load(self)->dict:
  if not self.path.exists(): return dict(self.base)
  if _link(self.path) or not self.path.is_file(): raise ResumeRejected("checkpoint manifest is unsafe")
  try: data=json.loads(self.path.read_text(encoding="utf-8"))
  except Exception as e: raise ResumeRejected("checkpoint manifest is unreadable") from e
  _validate(data); return data
 def record(self,*,stage:str,canonical_path:Path,page:int|None,calls_used:int)->None:
  if stage not in {"global_art_direction","page_scene_graph"}: raise ValueError("unsupported checkpoint stage")
  target=_inside(canonical_path,self.root)
  if not target.is_file(): raise ResumeRejected("canonical checkpoint is not a regular file")
  data=self.load(); entry={"path":str(target.relative_to(self.root)),"sha256":sha256(target),"page":page}
  if stage=="global_art_direction":
   if page is not None: raise ValueError("global checkpoint must not have a page")
   data["global"]=entry
  else:
   if not isinstance(page,int) or page<1 or page>data["total_slide_count"]: raise ValueError("invalid page checkpoint")
   data["pages"]=[x for x in data["pages"] if x["page"]!=page]+[entry]; data["pages"].sort(key=lambda x:x["page"])
  data["ark_attempts_used"]=max(data["ark_attempts_used"],calls_used); _atomic(self.path,data)
def recompute_call_budget(root:Path,limit:int)->int:
 path=root/"ark_calls.json"
 if not path.exists(): return 0
 try: values=json.loads(path.read_text(encoding="utf-8"))
 except Exception as e: raise ResumeRejected("ark call record is unreadable") from e
 if not isinstance(values,list): raise ResumeRejected("ark call record is invalid")
 used=sum(1 for x in values if isinstance(x,dict) and x.get("network_request_started") is True)
 if any(not isinstance(x,dict) for x in values) or used>limit: raise ResumeRejected("ark call record is invalid")
 return used
def load_checkpoint_manifest(parent_root:Path)->dict:
 if _link(parent_root) or not parent_root.is_dir(): raise ResumeRejected("source Job root is unsafe")
 path=parent_root.resolve()/MANIFEST_NAME
 if not path.exists() or _link(path) or not path.is_file(): raise ResumeRejected("source Job has no checkpoint manifest")
 try: data=json.loads(path.read_text(encoding="utf-8"))
 except Exception as exc: raise ResumeRejected("checkpoint manifest is unreadable") from exc
 _validate(data); return data
def inspect_resume_source(*,parent_root:Path,expected:dict,validate_global:Callable[[dict],None],validate_page:Callable[[int,dict],None])->ResumeState:
 if _link(parent_root) or not parent_root.is_dir(): raise ResumeRejected("source Job root is unsafe")
 root=parent_root.resolve(); path=root/MANIFEST_NAME
 if not path.exists(): raise ResumeRejected("source Job has no checkpoint manifest")
 probe=CheckpointStore(root,task_id=expected.get("source_task_id",expected.get("task_id","")),job_id=expected.get("source_job_id",expected.get("job_id","")),approved_sha256=expected["approved_md_sha256"],assets_manifest_sha256=expected["assets_manifest_sha256"],compatibility=expected.get("compatibility",{"producer":expected.get("producer_compatibility_version",""),"prompt":expected.get("prompt_contract_version",""),"scene_schema":expected.get("scene_schema_version",""),"pages":expected.get("total_slide_count",expected.get("pages",20))}),model_max_calls=expected["model_max_calls"])
 data=probe.load()
 for key in ("source_task_id","approved_md_sha256","assets_manifest_sha256","producer_compatibility_version","prompt_contract_version","scene_schema_version","total_slide_count","model_max_calls"):
  wanted=probe.base[key]
  if data.get(key)!=wanted: raise ResumeRejected("checkpoint compatibility mismatch: "+key)
 def read(entry):
  if set(entry)!={"path","sha256","page"} or Path(entry["path"]).is_absolute() or ".." in Path(entry["path"]).parts: raise ResumeRejected("checkpoint path is invalid")
  file=_inside(root/entry["path"],root)
  if not file.is_file() or sha256(file)!=entry["sha256"]: raise ResumeRejected("checkpoint hash mismatch")
  try: return file,json.loads(file.read_text(encoding="utf-8"))
  except Exception as e: raise ResumeRejected("checkpoint JSON is invalid") from e
 global_path=None
 if data["global"]:
  global_path,value=read(data["global"]); validate_global(value)
 elif data["pages"]: raise ResumeRejected("pages require Global checkpoint")
 pages=[]
 for number in range(1,data["total_slide_count"]+1):
  entry=next((x for x in data["pages"] if x["page"]==number),None)
  if not entry: break
  source,value=read(entry); validate_page(number,value); pages.append((number,source))
 used=recompute_call_budget(root,data["model_max_calls"])
 return ResumeState(global_path,tuple(pages),len(pages)+1,used,data["model_max_calls"]-used)
def import_resume_state(*,parent_root:Path,child_root:Path,expected:dict,validate_global:Callable[[dict],None],validate_page:Callable[[int,dict],None])->ResumeState:
 state=inspect_resume_source(parent_root=parent_root,expected=expected,validate_global=validate_global,validate_page=validate_page)
 global_path=None
 if state.global_direction:
  global_path=child_root/"global_art_direction.json"; _atomic(global_path,json.loads(state.global_direction.read_text(encoding="utf-8")))
 pages=[]
 for number,source in state.page_paths:
  target=child_root/"scene_graphs"/source.name; _atomic(target,json.loads(source.read_text(encoding="utf-8"))); pages.append((number,target))
 return ResumeState(global_path,tuple(pages),state.next_page,state.historical_calls_used,state.remaining_budget)