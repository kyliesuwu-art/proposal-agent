import json
from pathlib import Path

from src.main import _write_proposal_request, _write_proposal_run_log


def test_successful_proposal_request_metadata_is_reproducible_and_non_secret(tmp_path: Path):
    output = tmp_path / "proposal.md"
    request_path = _write_proposal_request(
        output,
        "工业园区绿色智慧能源与虚拟电厂方案，保留全部约束。",
        run_log=tmp_path / "proposal.run.log",
        debug=False,
    )
    data = json.loads(request_path.read_text(encoding="utf-8"))
    assert request_path.name == "proposal.request.json"
    assert data["request"].startswith("工业园区")
    assert data["output_file"] == "proposal.md"
    assert data["sources_file"] == "proposal.sources.json"
    assert data["run_log"] == "proposal.run.log"
    assert data["debug"] is False
    assert data["cli_arguments"] == {"debug": False, "run_log": "proposal.run.log"}
    assert "T" in data["generated_at"]
    assert "api" not in " ".join(data).lower()


def test_run_log_is_written_at_stage_boundaries_without_request_or_secret(tmp_path: Path):
    path = tmp_path / "proposal.run.log"
    _write_proposal_run_log(path, status="RUNNING", stage="planning", provider="dashscope_openai_compatible", model="qwen-test", timeout_seconds=300)
    _write_proposal_run_log(path, status="RUNNING", stage="model_call", event="model_call_completed", purpose="review_calls", elapsed_seconds=1.2)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "RUNNING" and data["stage"] == "model_call"
    assert data["model"] == "qwen-test" and "updated_at" in data
    assert [event["stage"] for event in data["events"]] == ["planning", "model_call"]
    assert "key" not in " ".join(data).lower() and "authorization" not in " ".join(data).lower()


def test_run_log_retains_structured_quality_gate_failure_details(tmp_path: Path):
    path = tmp_path / "proposal.run.log"
    _write_proposal_run_log(
        path, status="RUNNING", stage="quality_gate", event="quality_gate_checks",
        checked_artifact="in_memory_final_markdown_prepublication:proposal.md",
        draft_character_count=42, heading_count=2, citation_count=1, figure_count=0,
        pending_confirmation_count=1,
        quality_gate_checks=[{"rule": "role_and_scope_errors", "expected": 0, "actual": 1, "passed": False}],
        quality_gate_failed_checks=[{"rule": "role_and_scope_errors", "expected": 0, "actual": 1, "passed": False}],
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    event = data["events"][0]
    assert event["quality_gate_failed_checks"] == [{"rule": "role_and_scope_errors", "expected": 0, "actual": 1, "passed": False}]
    assert event["checked_artifact"] == "in_memory_final_markdown_prepublication:proposal.md"
