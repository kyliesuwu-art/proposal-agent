import json
from src.artifact_evaluation.models import CheckResult,EvaluationStatus,Severity,aggregate
def test_status_aggregation_and_json_serialization():
 warning=CheckResult("W",EvaluationStatus.PASS_WITH_WARNINGS,Severity.WARNING,"x","w")
 skipped=CheckResult("N",EvaluationStatus.NOT_RUN,Severity.INFO,"x","n")
 assert aggregate([warning]) is EvaluationStatus.PASS_WITH_WARNINGS
 assert aggregate([skipped]) is EvaluationStatus.NOT_RUN
 assert json.dumps(warning.payload())
