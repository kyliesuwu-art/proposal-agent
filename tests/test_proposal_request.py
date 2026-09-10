import json
from pathlib import Path

from src.main import _write_proposal_request


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
