from src.artifact_evaluation.markdown_evaluator import evaluate_markdown
from src.artifact_evaluation.models import EvaluationStatus
from src.artifact_evaluation.profiles import get_profile
def test_markdown_profiles_and_images(tmp_path):
 (tmp_path/"assets").mkdir();(tmp_path/"assets"/"a.png").write_bytes(b"x")
 good=tmp_path/"a.md";good.write_text("# T\n\n## S\ntext\n\n![x](assets/a.png)\n",encoding="utf8")
 assert evaluate_markdown(good,get_profile("internal-source")).status is EvaluationStatus.PASS
 bad=tmp_path/"b.md";bad.write_text("# T\n## S\n![x](../bad.png)\n【待确认】",encoding="utf8")
 assert evaluate_markdown(bad,get_profile("client-delivery")).status is EvaluationStatus.FAIL
def test_empty_markdown_fails(tmp_path):
 p=tmp_path/"x.md";p.write_text("",encoding="utf8");assert evaluate_markdown(p,get_profile("internal-source")).status is EvaluationStatus.FAIL
