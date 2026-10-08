from __future__ import annotations

import http.client
import io
import json
import urllib.error
from email.message import Message

import pytest

import scripts.run_ppt_visual_calibration_v4_ark as v4


def fake_ask(outcomes, calls):
    def ask(messages, purpose, *, max_tokens):
        calls.append((messages, purpose, max_tokens))
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome, {"purpose": purpose, "elapsed_seconds": 0, "ttft_seconds": None, "stream": True}
    return ask


class FakeSseResponse:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def __iter__(self):
        for event in self.events:
            if isinstance(event, BaseException):
                raise event
            yield event

    def read(self):
        raise AssertionError("streaming PAD.ask must not perform a complete response.read()")


def sse_delta(text: str) -> bytes:
    return ("data: " + json.dumps({"choices": [{"delta": {"content": text}}]}) + "\n").encode("utf-8")


def sse_done() -> bytes:
    return b"data: [DONE]\n"


def install_fake_sse(monkeypatch, streams):
    captured = []

    def urlopen(request, timeout):
        captured.append({"request": request, "timeout": timeout})
        return FakeSseResponse(streams.pop(0))

    monkeypatch.setattr(v4.PAD, "read_env", lambda: {"ARK_API_KEY": "fake", "ARK_BASE_URL": "https://example.invalid"})
    monkeypatch.setattr(v4.PAD.urllib.request, "urlopen", urlopen)
    return captured


@pytest.fixture(autouse=True)
def isolated_v4(tmp_path, monkeypatch):
    monkeypatch.setattr(v4, "OUT", tmp_path)
    v4.configure_model_call_budget(None)
    yield
    v4.configure_model_call_budget(None)


def records(tmp_path):
    return json.loads((tmp_path / "ark_calls.json").read_text(encoding="utf-8"))


def incomplete(partial=b""):
    return http.client.IncompleteRead(partial, 1)


def test_pad_ask_uses_streaming_sse_json_response(monkeypatch):
    captured = install_fake_sse(monkeypatch, [[sse_delta('{"ok": '), sse_delta("true}"), sse_done()]])

    raw, meta = v4.PAD.ask([], "fake")

    payload = json.loads(captured[0]["request"].data.decode("utf-8"))
    assert raw == '{"ok": true}'
    assert payload["stream"] is True
    assert payload["response_format"] == {"type": "json_object"}
    assert captured[0]["timeout"] == 360
    assert meta["stream"] is True and meta["stream_completed"] is True
    assert meta["sse_events"] == 2
    assert meta["first_chunk_at"] is not None
    assert meta["first_chunk_latency_seconds"] is not None
    assert meta["last_chunk_at"] is not None


def test_streaming_partial_attempt_is_discarded_before_retry(monkeypatch, tmp_path):
    captured = install_fake_sse(monkeypatch, [
        [sse_delta('{"partial": '), incomplete(b'{"partial": ')],
        [sse_delta('{"complete": true}'), sse_done()],
    ])
    v4.configure_model_call_budget(2)

    value, meta = v4.ask_json(call_id="page_02_design", system="x", content=[], raw_file=tmp_path / "raw.json", retry_delay_seconds=0)

    attempt_records = records(tmp_path)
    assert value == {"complete": True}
    assert len(captured) == 2 and v4.MODEL_CALL_BUDGET.used == 2
    assert meta["stream"] is True and meta["stream_completed"] is True
    assert not (tmp_path / "raw_attempt_01_raw.json").exists()
    assert (tmp_path / "raw_attempt_02_raw.json").read_text(encoding="utf-8") == '{"complete": true}'
    assert attempt_records[0]["stream_completed"] is False
    assert attempt_records[0]["first_chunk_at"] is not None
    assert attempt_records[0]["response_complete"] is False
    assert attempt_records[1]["stream_completed"] is True
    assert attempt_records[1]["response_complete"] is True


def test_two_streaming_interruptions_stop_after_two_total_http_attempts(monkeypatch, tmp_path):
    captured = install_fake_sse(monkeypatch, [
        [sse_delta('{"partial": 1'), incomplete()],
        [sse_delta('{"partial": 2'), incomplete()],
    ])
    v4.configure_model_call_budget(2)

    with pytest.raises(v4.ModelTransportError, match="after 2 HTTP attempts"):
        v4.ask_json(call_id="page", system="x", content=[], raw_file=tmp_path / "raw.json", retry_delay_seconds=0)

    assert len(captured) == 2 and v4.MODEL_CALL_BUDGET.used == 2
    assert len(records(tmp_path)) == 2
    assert all(item["stream_completed"] is False for item in records(tmp_path))


@pytest.mark.parametrize("streams, expected", [
    (
        [[sse_delta('{"partial": '), incomplete()], [sse_delta('{"ok": true "bad": 1}'), sse_done()]],
        v4.ModelJsonContractError,
    ),
    (
        [[sse_delta('{"ok": true "bad": 1}'), sse_done()], [sse_delta('{"partial": '), incomplete()]],
        v4.ModelTransportError,
    ),
])
def test_streaming_json_and_transport_failures_share_two_attempt_limit(monkeypatch, tmp_path, streams, expected):
    captured = install_fake_sse(monkeypatch, streams)
    v4.configure_model_call_budget(2)

    with pytest.raises(expected):
        v4.ask_json(call_id="page", system="x", content=[], raw_file=tmp_path / "raw.json", retry_delay_seconds=0)

    assert len(captured) == 2 and v4.MODEL_CALL_BUDGET.used == 2
    assert len(records(tmp_path)) == 2

def test_transport_failure_then_valid_json_retries_once_and_records_every_attempt(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(v4.PAD, "ask", fake_ask([incomplete(), '{"ok": true}'], calls))
    v4.configure_model_call_budget(2)
    value, _ = v4.ask_json(call_id="page_02_design", system="x", content=[], raw_file=tmp_path / "raw.json", retry_delay_seconds=0)
    attempt_records = records(tmp_path)
    assert value == {"ok": True} and len(calls) == 2 and v4.MODEL_CALL_BUDGET.used == 2
    assert attempt_records[0]["network_request_started"] is True
    assert attempt_records[0]["http_success"] is False
    assert attempt_records[0]["response_complete"] is False
    assert attempt_records[0]["retryable"] is True
    assert attempt_records[0]["response_path"] is None
    assert attempt_records[1]["success"] is True and attempt_records[1]["schema_success"] is True
    assert {"stage", "logical_call_id", "attempt", "started_at", "finished_at", "duration_seconds", "budget_used", "budget_limit", "network_request_started", "http_success", "response_complete", "parse_success", "schema_success", "success", "retryable", "error_type", "error_message", "response_path", "raw_response_complete"} <= set(attempt_records[0])


def test_two_incomplete_reads_stop_after_two_total_http_attempts(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(v4.PAD, "ask", fake_ask([incomplete(), incomplete()], calls))
    v4.configure_model_call_budget(2)
    with pytest.raises(v4.ModelTransportError, match="after 2 HTTP attempts"):
        v4.ask_json(call_id="page", system="x", content=[], raw_file=tmp_path / "raw.json", retry_delay_seconds=0)
    assert len(calls) == 2 and v4.MODEL_CALL_BUDGET.used == 2
    assert len(records(tmp_path)) == 2 and all(not item["success"] for item in records(tmp_path))


@pytest.mark.parametrize("outcomes, expected", [
    ([incomplete(), '{"ok": true "bad": 1}'], v4.ModelJsonContractError),
    (['{"ok": true "bad": 1}', incomplete()], v4.ModelTransportError),
])
def test_json_and_transport_failures_share_one_two_attempt_budget(monkeypatch, tmp_path, outcomes, expected):
    calls = []
    monkeypatch.setattr(v4.PAD, "ask", fake_ask(outcomes, calls))
    v4.configure_model_call_budget(2)
    with pytest.raises(expected):
        v4.ask_json(call_id="page", system="x", content=[], raw_file=tmp_path / "raw.json", retry_delay_seconds=0)
    assert len(calls) == 2 and v4.MODEL_CALL_BUDGET.used == 2 and len(records(tmp_path)) == 2


def test_transport_retry_budget_rejection_happens_before_second_network_call(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(v4.PAD, "ask", fake_ask([incomplete()], calls))
    v4.configure_model_call_budget(1)
    from src.wecom.ppt_failures import PptModelBudgetExceededError
    with pytest.raises(PptModelBudgetExceededError, match="budget exhausted"):
        v4.ask_json(call_id="page", system="x", content=[], raw_file=tmp_path / "raw.json", retry_delay_seconds=0)
    attempt_records = records(tmp_path)
    assert len(calls) == 1 and len(attempt_records) == 2
    assert attempt_records[1]["network_request_started"] is False


@pytest.mark.parametrize("status, should_retry", [(401, False), (403, False), (408, True), (429, True), (500, True), (503, True)])
def test_http_status_retry_policy(monkeypatch, tmp_path, status, should_retry):
    calls = []
    error = urllib.error.HTTPError("https://example.invalid/api?signature=secret", status, "status", None, None)
    outcomes = [error, '{"ok": true}'] if should_retry else [error]
    monkeypatch.setattr(v4.PAD, "ask", fake_ask(outcomes, calls))
    v4.configure_model_call_budget(2)
    if should_retry:
        assert v4.ask_json(call_id="page", system="x", content=[], raw_file=tmp_path / "raw.json", retry_delay_seconds=0)[0] == {"ok": True}
        assert len(calls) == 2
    else:
        with pytest.raises(v4.ModelTransportError):
            v4.ask_json(call_id="page", system="x", content=[], raw_file=tmp_path / "raw.json", retry_delay_seconds=0)
        assert len(calls) == 1
    assert records(tmp_path)[0]["retryable"] is should_retry


def test_transport_error_messages_are_redacted(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("ARK_API_KEY", "super-secret")
    monkeypatch.setattr(v4.PAD, "ask", fake_ask([RuntimeError("Bearer super-secret https://host.invalid/path?signature=secret")], calls))
    with pytest.raises(v4.ModelTransportError):
        v4.ask_json(call_id="page", system="x", content=[], raw_file=tmp_path / "raw.json", retry_delay_seconds=0)
    message = records(tmp_path)[0]["error_message"]
    assert "super-secret" not in message and "signature=secret" not in message


@pytest.mark.parametrize("error", [
    ConnectionResetError(), ConnectionAbortedError(), BrokenPipeError(), TimeoutError(),
    urllib.error.URLError(TimeoutError()),
])
def test_standard_transient_transport_errors_are_retryable(error):
    assert v4.is_retryable_transport_error(error) is True


def _http_error(body: bytes, *, status: int = 403, headers: dict[str, str] | None = None):
    message = Message()
    for name, value in (headers or {}).items():
        message[name] = value
    return urllib.error.HTTPError("https://example.invalid/api", status, "Forbidden", message, io.BytesIO(body))


def test_http_error_json_diagnostic_is_saved_without_changing_retry_or_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(v4.PAD, "OUT", tmp_path)
    key = "sensitive-key"
    monkeypatch.setattr(v4.PAD, "read_env", lambda: {"ARK_API_KEY": key, "ARK_BASE_URL": "https://example.invalid"})
    error = _http_error(b'{"error":{"code":"AccessDenied","type":"permission_error","message":"Bearer sensitive-key denied","request_id":"body-request"}}', headers={"x-request-id": "header-request", "Content-Type": "application/json"})
    monkeypatch.setattr(v4.PAD.urllib.request, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))
    v4.configure_model_call_budget(2)
    with pytest.raises(v4.ModelTransportError):
        v4.ask_json(call_id="page", system="x", content=[], raw_file=tmp_path / "raw.json", retry_delay_seconds=0)
    diagnostic = records(tmp_path)[0]["provider_error_diagnostic"]
    assert v4.MODEL_CALL_BUDGET.used == 1 and records(tmp_path)[0]["retryable"] is False
    assert diagnostic["http_status"] == 403 and diagnostic["provider_error_code"] == "AccessDenied"
    assert diagnostic["provider_error_type"] == "permission_error" and diagnostic["provider_request_id"] == "body-request"
    assert key not in json.dumps(diagnostic)
    assert json.loads((tmp_path / v4.PAD.HTTP_ERROR_DIAGNOSTICS).read_text(encoding="utf-8"))[0]["provider_error_code"] == "AccessDenied"


def test_http_error_diagnostic_handles_non_json_limited_redacted_body(monkeypatch, tmp_path):
    monkeypatch.setattr(v4.PAD, "OUT", tmp_path)
    monkeypatch.setattr(v4.PAD, "read_env", lambda: {"ARK_API_KEY": "key", "ARK_BASE_URL": "https://example.invalid"})
    error = _http_error(b"Authorization: Bearer key " + b"x" * (64 * 1024 + 20), headers={"x-tt-logid": "log-id", "Content-Type": "text/plain"})
    monkeypatch.setattr(v4.PAD.urllib.request, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))
    with pytest.raises(v4.ModelTransportError):
        v4.ask_json(call_id="page", system="x", content=[], raw_file=tmp_path / "raw.json", retry_delay_seconds=0)
    diagnostic = records(tmp_path)[0]["provider_error_diagnostic"]
    assert diagnostic["provider_error_code"] == "UNKNOWN" and diagnostic["provider_request_id"] == "log-id"
    assert diagnostic["body_json_parsed"] is False and diagnostic["body_truncated"] is True
    assert "Bearer key" not in diagnostic["body_summary"]
