from __future__ import annotations
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.artifact_evaluation.models import EvaluationStatus
from src.artifact_evaluation.report_writer import write_report
from src.artifact_evaluation.runner import evaluate
def main()->int:
 p=argparse.ArgumentParser(description="Read-only deterministic artifact evaluator")
 p.add_argument("--profile",required=True,choices=("internal-source","client-delivery"));p.add_argument("--markdown");p.add_argument("--docx");p.add_argument("--pptx");p.add_argument("--pdf");p.add_argument("--output-dir",required=True);p.add_argument("--expected-source-hash");p.add_argument("--strict",action="store_true");p.add_argument("--json-name",default="evaluation_report.json");p.add_argument("--markdown-name",default="evaluation_report.md");a=p.parse_args()
 try: report=evaluate(profile=a.profile,markdown=a.markdown,docx=a.docx,pptx=a.pptx,pdf=a.pdf,expected_source_hash=a.expected_source_hash)
 except ValueError as exc:p.error(str(exc))
 paths=write_report(report,a.output_dir,json_name=a.json_name,markdown_name=a.markdown_name);print(f"{report.overall_status.value}: {len(report.artifact_reports)} artifact(s); reports: {paths[0]}, {paths[1]}")
 return 1 if report.overall_status is EvaluationStatus.FAIL or (a.strict and report.overall_status is EvaluationStatus.PASS_WITH_WARNINGS) else 0
if __name__=="__main__":raise SystemExit(main())
