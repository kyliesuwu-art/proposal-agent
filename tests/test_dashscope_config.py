"""Offline DashScope configuration and diagnostic tests; no network calls."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import src.config as config
from src.adapters import llm_client


def test_explicit_project_env_file_is_loaded_without_cwd_guessing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("DASHSCOPE_API_KEY=test-key\n", encoding="utf-8")
    monkeypatch.setattr(config, "PROJECT_ENV_FILE", env_file)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)

    assert config.load_project_environment() is True
    assert config.dashscope_settings()["base_url"] == config.DEFAULT_DASHSCOPE_BASE_URL


def test_missing_key_is_clear_without_echoing_any_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(config, "PROJECT_ENV_FILE", tmp_path / "missing.env")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    with pytest.raises(config.DashScopeConfigurationError, match="DASHSCOPE_API_KEY") as error:
        config.dashscope_settings()
    assert "test-key" not in str(error.value)


def test_client_uses_unified_settings_and_safe_connection_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(llm_client, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(
        llm_client,
        "dashscope_settings",
        lambda: {
            "api_key": "test-secret-value",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": "qwen-test",
            "timeout_seconds": "12",
        },
    )
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-secret-value")
    client = llm_client.LLMClient()

    assert captured["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert client.connection_settings == {
        "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-test",
        "timeout_seconds": 12.0,
    }
    cause = RuntimeError("failed with test-secret-value")
    error = ConnectionError("outer failure")
    error.__cause__ = cause
    report = client.connection_error_diagnostics(error)
    assert report["error_type"] == "ConnectionError"
    assert "test-secret-value" not in repr(report)
    assert report["cause_chain"][1]["type"] == "RuntimeError"


def test_generate_once_logs_only_safe_connection_diagnostics(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class ExplodingCompletions:
        def create(self, **_kwargs: object) -> object:
            import httpx
            from openai import APIConnectionError

            error = APIConnectionError(message="network", request=httpx.Request("POST", "https://example.invalid"))
            error.__cause__ = RuntimeError("test-secret-value")
            raise error

    class FakeOpenAI:
        def __init__(self, **_kwargs: object) -> None:
            self.chat = type("Chat", (), {"completions": ExplodingCompletions()})()

    monkeypatch.setattr(llm_client, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(
        llm_client,
        "dashscope_settings",
        lambda: {
            "api_key": "test-secret-value",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": "qwen-test",
            "timeout_seconds": "12",
        },
    )
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-secret-value")
    client = llm_client.LLMClient()

    with pytest.raises(Exception, match="network"):
        client.generate_once("hello")
    output = capsys.readouterr().out
    assert "LLM connection diagnostic" in output
    assert "test-secret-value" not in output
    assert "endpoint" in output


def test_invalid_base_url_is_rejected_before_client_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "safe-test-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/api/v1")
    with pytest.raises(config.DashScopeConfigurationError, match="compatible-mode/v1"):
        config.dashscope_settings()
