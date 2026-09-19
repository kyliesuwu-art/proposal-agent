import hashlib
import pytest
from src.artifact_evaluation.models import EvaluationReport,EvaluationStatus
from src.artifact_evaluation.report_writer import write_report

def report(path): return EvaluationReport("v","now","internal-source",{"markdown":str(path)},[],[],EvaluationStatus.PASS,{},[])
def test_report_cannot_overwrite_input_or_each_other(tmp_path):
    source=tmp_path/"proposal.md";source.write_text("original",encoding="utf8");before=hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(ValueError):write_report(report(source),tmp_path,json_name="proposal.md")
    with pytest.raises(ValueError):write_report(report(source),tmp_path,json_name="same.json",markdown_name="same.json")
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
def test_report_independent_output_succeeds(tmp_path):
    source=tmp_path/"proposal.md";source.write_text("x",encoding="utf8");out=tmp_path/"out";a,b=write_report(report(source),out);assert a.is_file() and b.is_file()
