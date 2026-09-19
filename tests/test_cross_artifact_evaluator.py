from src.artifact_evaluation.cross_artifact_evaluator import evaluate_cross
from src.artifact_evaluation.models import ArtifactReport,EvaluationStatus
def test_cross_hash_and_optional_coverage():
 item=ArtifactReport("markdown","x","abc",1,"internal-source")
 assert evaluate_cross([item],"abc")[0].status is EvaluationStatus.PASS
 assert evaluate_cross([item],"def")[0].status is EvaluationStatus.FAIL
