from __future__ import annotations
import re
from .common import check
from .models import ArtifactReport, CheckResult, EvaluationStatus, Severity

NUMBERS=re.compile(r"\b\d+(?:\.\d+)?\s*(?:kW|MW|kWh|MWh|%|元|万元|年|小时)\b",re.I)
def evaluate_cross(reports:list[ArtifactReport],expected_source_hash:str|None=None)->list[CheckResult]:
 by={item.artifact_type:item for item in reports};checks=[]
 if expected_source_hash:
  source=by.get("markdown")
  if not source: checks.append(check("CROSS_SOURCE_HASH",EvaluationStatus.NOT_RUN,Severity.INFO,"cross","source hash needs Markdown input"))
  elif source.sha256!=expected_source_hash: checks.append(check("CROSS_SOURCE_HASH",EvaluationStatus.FAIL,Severity.ERROR,"cross","expected source hash mismatch",metric_value=source.sha256,expected_value=expected_source_hash))
  else: checks.append(check("CROSS_SOURCE_HASH",EvaluationStatus.PASS,Severity.INFO,"cross","expected source hash matches"))
 # Cross-artifact content analysis is intentionally bounded: metrics are evidence, not semantic claims.
 if len(by)<2: checks.append(check("CROSS_COVERAGE",EvaluationStatus.NOT_RUN,Severity.INFO,"cross","requires at least two artifact types"))
 else: checks.append(check("CROSS_COVERAGE",EvaluationStatus.PASS,Severity.INFO,"cross","multiple artifact inputs recorded",metric_value=sorted(by)))
 return checks
