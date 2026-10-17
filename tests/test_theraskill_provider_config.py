from __future__ import annotations

from domains.theraskill.provider_config import (
    load_provider_registry,
    public_provider_info,
    resolve_embedding_config,
    resolve_model_config,
)
from domains.theraskill.theraskill_llm import merged_embedding_config, merged_model_config
from theraskill_agent.platform import THERASKILL_PLATFORM
from theraskill_agent.provider_smoke import check_profile


def test_registry_contains_local_and_remote_profiles_without_embedded_secret() -> None:
    registry = load_provider_registry()
    assert {"local_qwen", "deepseek_v4_pro"} <= set(registry["profiles"])
    assert {"local_bge", "text_embedding_3_large"} <= set(registry["profiles"])
    serialized = str(registry)
    assert "sk-" not in serialized


def test_profile_secret_is_resolved_from_environment_and_redacted(monkeypatch) -> None:
    monkeypatch.setenv("THERASKILL_REMOTE_API_KEY", "test-secret")
    monkeypatch.setenv("THERASKILL_LLM_PROFILE", "deepseek_v4_pro")
    resolved = resolve_model_config()
    assert resolved["name"] == "deepseek-v4-pro"
    assert resolved["api_key"] == "test-secret"
    public = public_provider_info("llm")
    assert public["api_key"] == "<configured>"
    assert "THERASKILL_REMOTE_API_KEY" not in str(public)


def test_provider_check_does_not_treat_secret_name_as_credential(monkeypatch) -> None:
    monkeypatch.setenv("THERASKILL_REMOTE_API_KEY", "")
    result = check_profile("llm", "deepseek_v4_pro")
    assert result["credential_configured"] is False


def test_local_provider_file_overrides_llm_and_embedding_independently(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("THERASKILL_AGENT_ROOT", str(tmp_path))
    registry_dir = tmp_path / "configs" / "theraskill"
    registry_dir.mkdir(parents=True)
    (registry_dir / "providers.yaml").write_text(
        """
active:
  llm: llm
  embedding: embedding
profiles:
  llm:
    kind: llm
    name: base-llm
    base_url: http://base-llm/v1
  embedding:
    kind: embedding
    name: base-embedding
    base_url: http://base-embedding/v1
""",
        encoding="utf-8",
    )
    (registry_dir / "providers.local.yaml").write_text(
        """
profiles:
  llm:
    base_url: https://llm.example/v1
    api_key: llm-secret
  embedding:
    base_url: https://embedding.example/v1
    endpoint: https://embedding.example/v1/embeddings
    api_key: embedding-secret
""",
        encoding="utf-8",
    )
    registry = load_provider_registry()
    assert registry["_local_path"].endswith("providers.local.yaml")
    llm = resolve_model_config({"profile": "llm"})
    embedding = resolve_embedding_config({"profile": "embedding"})
    assert (llm["base_url"], llm["api_key"]) == ("https://llm.example/v1", "llm-secret")
    assert (embedding["endpoint"], embedding["api_key"]) == ("https://embedding.example/v1/embeddings", "embedding-secret")


def test_active_profiles_override_legacy_domain_yaml(monkeypatch) -> None:
    monkeypatch.setenv("THERASKILL_LLM_PROFILE", "deepseek_v4_pro")
    monkeypatch.setenv("THERASKILL_EMBEDDING_PROFILE", "text_embedding_3_large")
    model = merged_model_config(
        {"extraction": {"model": {"name": "legacy", "base_url": "http://legacy"}}},
        {"model": {"name": "old-local", "base_url": "http://old-local"}},
    )
    embedding = merged_embedding_config(
        {"retrieval": {"embedding": {"enabled": True, "name": "old-bge", "endpoint": "http://old"}}},
        {},
    )
    assert model["name"] == "deepseek-v4-pro"
    assert embedding["name"] == "text-embedding-3-large"
    assert embedding["endpoint"].endswith("/v1/embeddings")


def test_hermes_platform_kernel_exposes_capabilities_and_secret_free_status() -> None:
    snapshot = THERASKILL_PLATFORM.runtime_snapshot()
    assert snapshot["platform_id"] == "theraskill"
    assert {"support", "simulation", "response_review", "contribution", "extraction"} <= set(snapshot["capabilities"])
    assert "services" in snapshot
    assert "providers" in snapshot
    assert "api_key" not in str(snapshot["providers"]).lower() or "<configured>" in str(snapshot["providers"])
