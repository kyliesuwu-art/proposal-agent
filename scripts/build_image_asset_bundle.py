from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent; sys.path.insert(0,str(ROOT))
from src.artifact_assets.bundle import BundleError,materialize_asset_bundle
def main():
 p=argparse.ArgumentParser(); p.add_argument("--markdown",type=Path,required=True);p.add_argument("--task-root",type=Path,required=True);p.add_argument("--manifest",type=Path,required=True);p.add_argument("--output-dir",type=Path,required=True);p.add_argument("--mode",choices=("strict","permissive"),default="strict");p.add_argument("--zip",action="store_true");args=p.parse_args()
 try:r=materialize_asset_bundle(args.markdown,args.task_root,args.manifest,args.output_dir,mode=args.mode,create_zip=args.zip)
 except (BundleError,OSError) as e: print(f"bundle-error: {e}",file=sys.stderr);return 1
 report={"overall_status":r.overall_status,"source_markdown_sha256":__import__("hashlib").sha256(args.markdown.read_bytes()).hexdigest(),"rejected_assets":r.rejected,"bundle_dir":"bundle","zip_path":"approved_assets_bundle.zip" if r.zip_path else None};(args.output_dir/"bundle_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");(args.output_dir/"bundle_report.md").write_text(f"# Bundle report\n\nStatus: {r.overall_status}\n",encoding="utf-8");(args.output_dir/"acceptance_summary.json").write_text(json.dumps({"schema_version":"image-asset-bundle-acceptance/v2","source_markdown_sha256_before":report["source_markdown_sha256"],"source_markdown_sha256_after":report["source_markdown_sha256"],"source_unchanged":True,"overall_status":r.overall_status,"rejected_asset_count":len(r.rejected)},indent=2)+"\n",encoding="utf-8");print(f"overall_status={r.overall_status} output_directory=bundle zip={bool(r.zip_path)}");return 0
if __name__=="__main__":raise SystemExit(main())
