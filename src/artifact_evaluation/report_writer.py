from __future__ import annotations
import json
import os
from pathlib import Path
from .models import EvaluationReport

def write_report(report:EvaluationReport,output_dir:str|Path,*,json_name:str="evaluation_report.json",markdown_name:str="evaluation_report.md")->tuple[Path,Path]:
 out=Path(output_dir);out.mkdir(parents=True,exist_ok=True); payload=report.payload();json_path=out/json_name;md_path=out/markdown_name
 def canonical(path: Path) -> str: return os.path.normcase(str(path.resolve(strict=False)))
 inputs={canonical(Path(value)) for value in report.inputs.values()}
 if canonical(json_path)==canonical(md_path) or canonical(json_path) in inputs or canonical(md_path) in inputs: raise ValueError("report output path collides with an input artifact")
 json_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
 lines=["# Artifact Evaluation Report","",f"- Status: `{report.overall_status.value}`",f"- Profile: `{report.profile}`",""]
 for artifact in report.artifact_reports:
  lines.extend([f"## {artifact.artifact_type}","",f"- Status: `{artifact.status.value}`",f"- Path: `{artifact.path}`",f"- SHA-256: `{artifact.sha256 or 'NOT_RUN'}`"])
  for item in artifact.checks:
   if item.status is not item.status.PASS: lines.append(f"- `{item.code}`: {item.status.value} — {item.message}")
  lines.append("")
 lines.extend(["## Limitations",""]+[f"- {x}" for x in report.limitations])
 md_path.write_text("\n".join(lines)+"\n",encoding="utf-8");return json_path,md_path
