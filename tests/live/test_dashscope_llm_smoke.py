"""Opt-in DashScope LLM smoke test (billable, disabled by default).

Enable only for an explicit, budgeted acceptance run:

    DASHSCOPE_LIVE_APPROVED=1 DASHSCOPE_API_KEY=... uv run python -m pytest tests/live -q

The test issues exactly one minimal chat completion through LLMClient.generate_once
and never opens the formal RAG databases or writes outside the pytest temp area.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("DASHSCOPE_LIVE_APPROVED") != "1",
    reason="set DASHSCOPE_LIVE_APPROVED=1 for the billable DashScope smoke run",
)


@pytest.fixture
def llm_client():
    from src.config import load_project_environment

    load_project_environment()
    if not os.environ.get("DASHSCOPE_API_KEY", "").strip():
        pytest.skip("DASHSCOPE_API_KEY is not configured")
    from src.adapters.llm_client import LLMClient

    return LLMClient()


def test_single_chat_completion_returns_text_without_credential_leak(llm_client, monkeypatch) -> None:
    calls = {"count": 0}
    create = llm_client._client.chat.completions.create

    def counting_create(*args, **kwargs):
        calls["count"] += 1
        return create(*args, **kwargs)

    monkeypatch.setattr(llm_client._client.chat.completions, "create", counting_create)

    answer = llm_client.generate_once("Reply with the single word: ok")

    assert calls["count"] == 1
    assert isinstance(answer, str) and answer.strip()
    secret = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    assert secret not in answer
    for name in ("api_key", "headers"):
        assert name not in answer.lower()
