from __future__ import annotations
from datetime import datetime,timezone
from pathlib import Path
from .cross_artifact_evaluator import evaluate_cross
from .docx_evaluator import evaluate_docx
from .markdown_evaluator import evaluate_markdown
from .models import EvaluationReport,EvaluationStatus,aggregate
from .pptx_evaluator import evaluate_pptx
from .profiles import get_profile

def evaluate(*,profile:str,markdown:str|None=None,docx:str|None=None,pptx:str|None=None,pdf:str|None=None,expected_source_hash:str|None=None)->EvaluationReport:
 p=get_profile(profile); reports=[]
 if markdown: reports.append(evaluate_markdown(markdown,p))
 if docx: reports.append(evaluate_docx(docx,p))
 if pptx: reports.append(evaluate_pptx(pptx,p))
 # PDF is deliberately declared unsupported in V1 rather than silently passing it.
 if pdf:
  from .models import ArtifactReport,CheckResult,Severity
  item=ArtifactReport("pdf",str(pdf),None,None,p.name);item.checks=[CheckResult("PDF_EVALUATOR",EvaluationStatus.NOT_RUN,Severity.INFO,"pdf","PDF evaluator is not implemented in V1")];item.status=EvaluationStatus.NOT_RUN;reports.append(item)
 cross=evaluate_cross(reports,expected_source_hash)
 all_checks=[x for report in reports for x in report.checks]+cross
 overall=aggregate(all_checks,required=bool(reports))
 if not reports: overall=EvaluationStatus.FAIL
 return EvaluationReport("artifact-evaluation/v1",datetime.now(timezone.utc).isoformat(),p.name,{k:str(v) for k,v in {"markdown":markdown,"docx":docx,"pptx":pptx,"pdf":pdf}.items() if v},reports,cross,overall,{"artifact_count":len(reports),"warning_count":sum(x.severity.value=="WARNING" for x in all_checks),"error_count":sum(x.severity.value=="ERROR" for x in all_checks)},["Visual rendering checks are NOT_RUN without trusted page images or Office rendering.","V1 does not judge factual correctness or aesthetics."])
