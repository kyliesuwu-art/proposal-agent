from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.run_ppt_visual_calibration_v4_ark as v4


def global_direction() -> dict:
    return {"design_intent": "x", "visual_personality": "x", "color_system": {"primary": "x"}, "typography_hierarchy": "x", "spacing_rhythm": "x", "image_treatment": "x", "diagram_language": "x", "brand_motif": "x", "page_families": {"overview": "x"}, "density_strategy": "x", "do_not_use": ["x"], "page_directions": {"1": "x"}}


def fake_ask(responses: list[str], calls: list[tuple]):
    def ask(messages, purpose, *, max_tokens):
        calls.append((messages, purpose, max_tokens))
        return responses.pop(0), {"elapsed_seconds": 0, "ttft_seconds": 0, "purpose": purpose}
    return ask


@pytest.fixture(autouse=True)
def isolated_v4(tmp_path, monkeypatch):
    monkeypatch.setattr(v4, "OUT", tmp_path)
    v4.configure_model_call_budget(None)
    yield
    v4.configure_model_call_budget(None)


def test_first_valid_json_uses_one_budgeted_call(monkeypatch, tmp_path):
    calls: list[tuple] = []
    monkeypatch.setattr(v4.PAD, "ask", fake_ask([json.dumps({"ok": True})], calls))
    v4.configure_model_call_budget(2)
    value, _ = v4.ask_json(call_id="global", system="x", content=[], raw_file=tmp_path / "global_raw.json")
    records = json.loads((tmp_path / "ark_calls.json").read_text(encoding="utf-8"))
    assert value == {"ok": True} and len(calls) == 1 and v4.MODEL_CALL_BUDGET.used == 1
    assert records[0]["attempt"] == 1 and records[0]["success"] is True
    assert records[0]["response_path"] == "global_attempt_01_raw.json"


@pytest.mark.parametrize("raw", ["```json\n{\"ok\": true}\n```", "\ufeff  \n {\"ok\": true} \n"])
def test_safe_transport_normalization_does_not_retry(monkeypatch, tmp_path, raw):
    calls: list[tuple] = []
    monkeypatch.setattr(v4.PAD, "ask", fake_ask([raw], calls))
    v4.configure_model_call_budget(2)
    assert v4.ask_json(call_id="global", system="x", content=[], raw_file=tmp_path / "global_raw.json")[0] == {"ok": True}
    assert len(calls) == 1 and v4.MODEL_CALL_BUDGET.used == 1


def test_invalid_then_valid_preserves_both_attempts_and_retries(monkeypatch, tmp_path):
    calls: list[tuple] = []
    monkeypatch.setattr(v4.PAD, "ask", fake_ask(['{"ok": true "missing": false}', '{"ok": true}'], calls))
    v4.configure_model_call_budget(2)
    value, _ = v4.ask_json(call_id="global", system="system", content=[], raw_file=tmp_path / "global_raw.json")
    records = json.loads((tmp_path / "ark_calls.json").read_text(encoding="utf-8"))
    assert value == {"ok": True} and len(calls) == 2 and v4.MODEL_CALL_BUDGET.used == 2
    assert (tmp_path / "global_attempt_01_raw.json").is_file() and (tmp_path / "global_attempt_02_raw.json").is_file()
    assert records[0]["success"] is False and records[0]["json_line"] == 1 and records[0]["json_column"]
    assert records[1]["success"] is True and "previous response was not valid JSON" in calls[1][0][0]["content"]


def test_two_invalid_responses_fail_with_stage_attempt_and_location(monkeypatch, tmp_path):
    calls: list[tuple] = []
    monkeypatch.setattr(v4.PAD, "ask", fake_ask(['{"ok": true "x": 1}', '{"ok": true "x": 2}'], calls))
    v4.configure_model_call_budget(2)
    with pytest.raises(v4.ModelJsonContractError, match="global failed after 2 HTTP attempts") as exc_info:
        v4.ask_json(call_id="global", system="x", content=[], raw_file=tmp_path / "global_raw.json")
    assert exc_info.value.line == 1 and exc_info.value.column
    assert len(calls) == 2 and len(json.loads((tmp_path / "ark_calls.json").read_text(encoding="utf-8"))) == 2


def test_schema_invalid_then_valid_retries_with_parse_success_recorded(monkeypatch, tmp_path):
    calls: list[tuple] = []
    monkeypatch.setattr(v4.PAD, "ask", fake_ask([json.dumps({"design_intent": "x"}), json.dumps(global_direction())], calls))
    v4.configure_model_call_budget(2)
    value, _ = v4.ask_json(call_id="global", system="x", content=[], raw_file=tmp_path / "global_raw.json", validator=v4.validate_global_art_direction)
    records = json.loads((tmp_path / "ark_calls.json").read_text(encoding="utf-8"))
    assert value == global_direction() and len(calls) == 2
    assert records[0]["parse_success"] is True and records[0]["error_type"] == "ModelJsonContractError"


def test_budget_rejects_retry_before_second_network_call(monkeypatch, tmp_path):
    calls: list[tuple] = []
    monkeypatch.setattr(v4.PAD, "ask", fake_ask(['{"ok": true "x": 1}'], calls))
    v4.configure_model_call_budget(1)
    with pytest.raises(RuntimeError, match="budget exhausted"):
        v4.ask_json(call_id="global", system="x", content=[], raw_file=tmp_path / "global_raw.json")
    assert len(calls) == 1 and v4.MODEL_CALL_BUDGET.used == 1
    records = json.loads((tmp_path / "ark_calls.json").read_text(encoding="utf-8"))
    assert len(records) == 2 and records[1]["network_request_started"] is False


def test_unique_top_level_object_extraction_is_string_aware():
    assert v4.parse_model_json('explanation {"text": "brace { remains text"} trailing') == {"text": "brace { remains text"}
    with pytest.raises(v4.ModelJsonContractError, match="multiple top-level"):
        v4.parse_model_json('{"a": 1} and {"b": 2}')