"""Validated manifest consumer and deterministic portable Markdown bundler."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json, shutil, tempfile, zipfile
from pathlib import Path
from .image_inspector import inspect_image, sha256_file
from .markdown_parser import rewrite_markdown_image_references, normalize_reference
from .models import AssetStatus, AssetValidationProfile
from .safe_resolver import PathSafetyError, resolve_task_image

class BundleError(ValueError): pass
_USABLE={AssetStatus.VALID.value,AssetStatus.LOW_RESOLUTION.value,AssetStatus.DUPLICATE.value}
_EXT={"PNG":"png","JPEG":"jpg","GIF":"gif","BMP":"bmp","TIFF":"tiff","WEBP":"webp"}

def load_manifest(path: str|Path)->dict:
    try: data=json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc: raise BundleError("MANIFEST_INVALID: unreadable JSON") from exc
    validate_manifest_schema(data); return data
def validate_manifest_schema(data:dict)->None:
    if data.get("schema_version")!="image-asset-manifest/v1" or not isinstance(data.get("assets"),list): raise BundleError("MANIFEST_SCHEMA_INCOMPATIBLE")
    for asset in data["assets"]:
        if not all(k in asset for k in ("asset_id","normalized_relative_path","status","referenced","reference_count")): raise BundleError("MANIFEST_SCHEMA_INCOMPATIBLE")

@dataclass
class ValidatedAssetSet:
    manifest:dict; task_root:Path; valid_assets:list[dict]; rejected_assets:list[dict]
    def resolve_asset_by_id(self, asset_id:str): return next((x for x in self.manifest["assets"] if x["asset_id"]==asset_id),None)
    def resolve_asset_by_reference(self, reference:str):
        key=normalize_reference(reference)
        return next((x for x in self.manifest["assets"] if x.get("normalized_relative_path")==key),None)

def build_validated_asset_set(manifest:dict, task_root:str|Path)->ValidatedAssetSet:
    validate_manifest_schema(manifest); root=Path(task_root).resolve(strict=True)
    valid=[a for a in manifest["assets"] if a["status"] in _USABLE and a.get("referenced")]
    rejected=[a for a in manifest["assets"] if a.get("referenced") and a["status"] not in _USABLE]
    return ValidatedAssetSet(manifest,root,valid,rejected)

def _verify(asset:dict, root:Path)->tuple[Path,str|None]:
    try: source=resolve_task_image(root,asset["normalized_relative_path"]).path
    except PathSafetyError: return root,"ASSET_UNSAFE_SINCE_MANIFEST"
    if not source.exists(): return source,"ASSET_MISSING_SINCE_MANIFEST"
    if source.is_symlink(): return source,"ASSET_UNSAFE_SINCE_MANIFEST"
    info=inspect_image(source,AssetValidationProfile())
    if info.get("error") or info.get("sha256")!=asset.get("sha256") or info.get("detected_format")!=asset.get("detected_format"): return source,"ASSET_CHANGED_SINCE_MANIFEST"
    return source,None

@dataclass(frozen=True)
class BundleResult: bundle_dir:Path; zip_path:Path|None; overall_status:str; rejected:list[dict]

def _zip_tree(bundle:Path, destination:Path):
    with zipfile.ZipFile(destination,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for path in sorted(bundle.rglob("*"),key=lambda p:p.relative_to(bundle).as_posix()):
            if path.is_file():
                entry=zipfile.ZipInfo(path.relative_to(bundle).as_posix(),(1980,1,1,0,0,0)); entry.compress_type=zipfile.ZIP_DEFLATED; entry.external_attr=0o100644<<16
                archive.writestr(entry,path.read_bytes(),compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)

def materialize_asset_bundle(markdown_path, task_root, manifest_path, output_dir, *, mode="strict", create_zip=False)->BundleResult:
    if mode not in {"strict","permissive"}: raise BundleError("MODE_INVALID")
    manifest=load_manifest(manifest_path); aset=build_validated_asset_set(manifest,task_root); md=Path(markdown_path).resolve(strict=True); source_hash=sha256_file(md)
    if source_hash!=manifest.get("source_markdown_sha256"): raise BundleError("MARKDOWN_CHANGED_SINCE_MANIFEST")
    rejected=list(aset.rejected_assets); verified=[]
    for asset in aset.valid_assets:
        source,reason=_verify(asset,aset.task_root)
        if reason: rejected.append({**asset,"status":reason,"reason_code":reason,"message":"Asset changed or became unsafe after manifest generation."})
        else: verified.append((asset,source))
    if rejected and mode=="strict": raise BundleError(rejected[0].get("reason_code") or rejected[0]["status"])
    out=Path(output_dir); final=out/"bundle"
    if final.exists(): raise BundleError("OUTPUT_ALREADY_EXISTS")
    out.mkdir(parents=True,exist_ok=True); temp=Path(tempfile.mkdtemp(prefix=".bundle-",dir=out))
    try:
        canonical={}; replacements={}; mappings=[]
        for asset,source in sorted(verified,key=lambda x:x[0]["asset_id"]):
            owner=canonical.setdefault(asset["sha256"],asset); ext=_EXT[asset["detected_format"]]; packaged=f"assets/{owner['asset_id']}.{ext}"
            replacements[asset["normalized_relative_path"]]=packaged
            mappings.append({"markdown_reference":asset["normalized_relative_path"],"asset_id":asset["asset_id"],"packaged_relative_path":packaged,"sha256":asset["sha256"],"decoded_format":asset["detected_format"],"byte_size":asset["file_size"],"status":asset["status"],"warnings":asset["warnings"]})
        (temp/"assets").mkdir();
        for digest,owner in canonical.items(): shutil.copyfile(next(src for a,src in verified if a["asset_id"]==owner["asset_id"]),temp/f"assets/{owner['asset_id']}.{_EXT[owner['detected_format']]}")
        rewritten=rewrite_markdown_image_references(md.read_text(encoding="utf-8"),replacements); (temp/"approved.md").write_text(rewritten,encoding="utf-8")
        shutil.copyfile(manifest_path,temp/"assets_manifest.json")
        bundle={"schema_version":"portable-markdown-asset-bundle/v2","source_markdown_filename":"approved.md","source_markdown_sha256":source_hash,"asset_manifest_sha256":sha256_file(Path(manifest_path)),"mode":mode,"reference_count":manifest["summary"]["total_references"],"unique_asset_count":len(aset.valid_assets),"packaged_asset_count":len(canonical),"rejected_asset_count":len(rejected),"markdown_reference_mapping":mappings,"overall_status":"PASS_WITH_WARNINGS" if rejected else "PASS"}
        (temp/"bundle_manifest.json").write_text(json.dumps(bundle,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
        temp.replace(final); zip_path=out/"approved_assets_bundle.zip" if create_zip else None
        if zip_path: _zip_tree(final,zip_path)
        return BundleResult(final,zip_path,bundle["overall_status"],rejected)
    except Exception:
        if temp.exists(): shutil.rmtree(temp)
        raise
