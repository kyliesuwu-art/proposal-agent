"""Embedding client checks use fake responses and never contact DashScope."""

from types import SimpleNamespace

import pytest

from src.adapters import vector_store
from src import config
from src.hybrid_v2 import LocalHashEmbeddingFunction


def test_embedding_client_rejects_missing_key_before_remote_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    monkeypatch.setattr(config, "PROJECT_ENV_FILE", tmp_path / "missing.env")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setattr(vector_store, "OpenAI", lambda **_kwargs: pytest.fail("OpenAI constructed"))

    embedding = vector_store.DashScopeEmbeddingFunction()
    with pytest.raises(config.DashScopeConfigurationError, match="DASHSCOPE_API_KEY"):
        embedding(["sample"])


@pytest.mark.parametrize("indices", [[1, 0], [0], [0, 0], [0, 1, 2]])
def test_embedding_response_requires_one_unique_vector_per_input(
    monkeypatch: pytest.MonkeyPatch, indices: list[int],
) -> None:
    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.embeddings = self

        def create(self, **_kwargs):
            return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=[float(i)]) for i in indices])

    monkeypatch.setattr(vector_store, "dashscope_settings", lambda: {
        "api_key": "offline-test-key",
        "base_url": "https://example.invalid/compatible-mode/v1",
        "timeout_seconds": "12",
    })
    monkeypatch.setattr(vector_store, "OpenAI", FakeOpenAI)
    embedding = vector_store.DashScopeEmbeddingFunction()
    assert embedding.name() == "dashscope-text-embedding-v3"
    assert embedding.get_config() == {"model": "text-embedding-v3"}
    assert captured == {}
    expected_client_settings = {
        "api_key": "offline-test-key",
        "base_url": "https://example.invalid/compatible-mode/v1",
        "timeout": 12.0,
        "max_retries": 0,
    }
    if indices == [1, 0]:
        assert [item.index for item in embedding._embed_batch_with_retry(["first", "second"])] == [0, 1]
    else:
        with pytest.raises(ValueError, match="数量或序号"):
            embedding._embed_batch_with_retry(["first", "second"])
    assert captured == expected_client_settings


def test_embedding_config_round_trip_contains_no_credentials() -> None:
    local = LocalHashEmbeddingFunction(dimensions=32)
    assert local.name() == "local-hash-offline"
    assert LocalHashEmbeddingFunction.build_from_config(local.get_config()).dimensions == 32
    with pytest.raises(ValueError, match="维度"):
        LocalHashEmbeddingFunction(dimensions=0)
    with pytest.raises(ValueError, match="不支持"):
        vector_store.DashScopeEmbeddingFunction.build_from_config({"model": "unknown"})
