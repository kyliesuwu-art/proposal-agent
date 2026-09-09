import json
from pathlib import Path
import pytest
from src.targeted_reindex import dry_run

def test_dry_run_is_zero_write_and_rejects_unsafe_target(tmp_path):
    plan = {"reprocess_legacy": [{"document_id": "a", "estimated_pages": 2, "estimated_image_candidates": 1}], "failed_retry_candidates": [{}] * 5, "duplicates": [{}] * 9}
    path = tmp_path / "plan.json"; path.write_text(json.dumps(plan), encoding="utf8")
    result = dry_run(path, tmp_path / "source", tmp_path / "target", limit=1)
    assert result["selected"] == 1 and result["database_writes"] == result["network_calls"] == 0
    with pytest.raises(ValueError, match="不得相同"):
        dry_run(path, tmp_path / "source", tmp_path / "source")
    (tmp_path / "exists").mkdir()
    with pytest.raises(ValueError, match="已存在"):
        dry_run(path, tmp_path / "source", tmp_path / "exists")
