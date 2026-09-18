"""Validated manifest consumer and atomic portable Markdown bundler."""
from __future__ import annotations
from dataclasses import dataclass
import json, shutil, tempfile, zipfile
from pathlib import Path
from .image_inspector import inspect_image, sha256_file
from .markdown_parser import extract_markdown_images, normalize_reference, replace_rejected_markdown_images, rewrite_markdown_image_references
from .models import AssetStatus, AssetValidationProfile
from .safe_resolver import PathSafetyError, resolve_task_image

class BundleError(ValueError): pass
_USABLE={AssetStatus.VALID.value,AssetStatus.LOW_RESOLUTION.value,AssetStatus.DUPLICATE.value}
_EXT={"PNG":"png","JPEG":"jpg","GIF":"gif","BMP":"bmp","TIFF":"tiff","WEBP":"webp"}
def load_manifest(path):
 try:data=json.loads(Path(path).read_text(encoding="utf-8"))
 except (OSError,json.JSONDecodeError) as e:raise BundleError("MANIFEST_INVALID") from e
 validate_manifest_schema(data);return data
def validate_manifest_schema(data):
 if data.get("schema_version")!="image-asset-manifest/v1" or not isinstance(data.get("assets"),list):raise BundleError("MANIFEST_SCHEMA_INCOMPATIBLE")
 if any(not all(k in a for k in ("asset_id","normalized_relative_path","status","referenced","reference_count")) for a in data["assets"]):raise BundleError("MANIFEST_SCHEMA_INCOMPATIBLE")
@dataclass
class ValidatedAssetSet:
 manifest:dict;task_root:Path;valid_assets:list[dict];rejected_assets:list[dict]
 def resolve_asset_by_id(self,id):return next((a for a in self.manifest["assets"] if a["asset_id"]==id),None)
 def resolve_asset_by_reference(self,ref):return next((a for a in self.manifest["assets"] if a.get("normalized_relative_path")==normalize_reference(ref)),None)
def build_validated_asset_set(manifest,task_root):
 validate_manifest_schema(manifest);root=Path(task_root).resolve(strict=True)
 return ValidatedAssetSet(manifest,root,[a for a in manifest["assets"] if a.get("referenced") and a["status"] in _USABLE],[a for a in manifest["assets"] if a.get("referenced") and a["status"] not in _USABLE])
def _verify(a,root):
 try:p=resolve_task_image(root,a["normalized_relative_path"]).path
 except PathSafetyError:return root,"ASSET_UNSAFE_SINCE_MANIFEST"
 if not p.exists():return p,"ASSET_MISSING_SINCE_MANIFEST"
 i=inspect_image(p,AssetValidationProfile())
 return (p,None) if not i.get("error") and i.get("sha256")==a.get("sha256") and i.get("detected_format")==a.get("detected_format") else (p,"ASSET_CHANGED_SINCE_MANIFEST")
def _zip_tree(bundle,destination):
 with zipfile.ZipFile(destination,"x",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
  for p in sorted(bundle.rglob("*"),key=lambda x:x.relative_to(bundle).as_posix()):
   if p.is_file():
    i=zipfile.ZipInfo(p.relative_to(bundle).as_posix(),(1980,1,1,0,0,0));i.compress_type=zipfile.ZIP_DEFLATED;i.external_attr=0o100644<<16;z.writestr(i,p.read_bytes(),compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
@dataclass(frozen=True)
class BundleResult:bundle_dir:Path;zip_path:Path|None;overall_status:str;rejected:list[dict]
def materialize_asset_bundle(markdown_path,task_root,manifest_path,output_dir,*,mode="strict",create_zip=False):
 if mode not in {"strict","permissive"}:raise BundleError("MODE_INVALID")
 out=Path(output_dir)
 if out.exists():raise BundleError("OUTPUT_ALREADY_EXISTS")
 m=load_manifest(manifest_path);aset=build_validated_asset_set(m,task_root);md=Path(markdown_path).resolve(strict=True)
 if sha256_file(md)!=m.get("source_markdown_sha256"):raise BundleError("MARKDOWN_CHANGED_SINCE_MANIFEST")
 rejected=list(aset.rejected_assets);verified=[]
 for a in aset.valid_assets:
  p,r=_verify(a,aset.task_root)
  if r:rejected.append({**a,"status":r,"reason_code":r,"message":"Asset excluded after manifest revalidation."})
  else:verified.append((a,p))
 if rejected and mode=="strict":raise BundleError(rejected[0].get("reason_code") or rejected[0]["status"])
 out.parent.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix=f".{out.name}-",dir=out.parent))
 try:
  bundle=stage/"bundle";(bundle/"assets").mkdir(parents=True);canonical={};refs={};mapping=[]
  for a,p in sorted(verified,key=lambda x:x[0]["asset_id"]):
   owner=canonical.setdefault(a["sha256"],a);pack=f"assets/{owner['asset_id']}.{_EXT[owner['detected_format']]}";refs[a["normalized_relative_path"]]=pack
   mapping.append({"markdown_reference":a["normalized_relative_path"],"asset_id":a["asset_id"],"packaged_relative_path":pack,"sha256":a["sha256"],"decoded_format":a["detected_format"],"byte_size":a["file_size"],"status":a["status"],"warnings":a["warnings"],"handling":"packaged"})
  for r in rejected:mapping.append({"markdown_reference":r.get("normalized_relative_path"),"asset_id":r.get("asset_id"),"packaged_relative_path":None,"sha256":r.get("sha256"),"status":r["status"],"reason_code":r.get("reason_code",r["status"]),"handling":"replaced_with_text"})
  for owner in canonical.values():
   source=next(p for a,p in verified if a["asset_id"]==owner["asset_id"]);target=bundle/f"assets/{owner['asset_id']}.{_EXT[owner['detected_format']]}";shutil.copyfile(source,target);i=inspect_image(target,AssetValidationProfile())
   if i.get("error") or i.get("sha256")!=owner["sha256"] or i.get("detected_format")!=owner["detected_format"]:raise BundleError("COPIED_ASSET_VERIFICATION_FAILED")
  text=replace_rejected_markdown_images(rewrite_markdown_image_references(md.read_text(encoding="utf-8"),refs),{r["normalized_relative_path"]:r.get("reason_code",r["status"]) for r in rejected});(bundle/"approved.md").write_text(text,encoding="utf-8")
  shutil.copyfile(manifest_path,bundle/"assets_manifest.json")
  payload={"schema_version":"portable-markdown-asset-bundle/v2","source_markdown_filename":"approved.md","source_markdown_sha256":sha256_file(md),"asset_manifest_sha256":sha256_file(Path(manifest_path)),"mode":mode,"reference_count":m["summary"]["total_references"],"unique_asset_count":len(aset.valid_assets),"packaged_asset_count":len(canonical),"rejected_asset_count":len(rejected),"markdown_reference_mapping":sorted(mapping,key=lambda x:(x["markdown_reference"] or "",x["asset_id"] or "")),"overall_status":"PASS_WITH_WARNINGS" if rejected else "PASS"};(bundle/"bundle_manifest.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
  if any(not (bundle/x.reference).is_file() for x in extract_markdown_images(text)):raise BundleError("BUNDLE_REFERENCE_VERIFICATION_FAILED")
  if create_zip:_zip_tree(bundle,stage/"approved_assets_bundle.zip")
  stage.replace(out);return BundleResult(out/"bundle",out/"approved_assets_bundle.zip" if create_zip else None,payload["overall_status"],rejected)
 except Exception:
  if stage.exists():shutil.rmtree(stage)
  raise
