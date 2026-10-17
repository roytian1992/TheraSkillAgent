from __future__ import annotations

from domains.theraskill.provider_config import (
    ProviderConfigError,
    load_provider_registry,
    public_provider_info,
    resolve_embedding_config,
    resolve_model_config,
)
from domains.theraskill.theraskill_llm import merged_embedding_config, merged_model_config
from theraskill_agent.platform import THERASKILL_PLATFORM
from theraskill_agent.provider_smoke import check_provider, list_providers


def test_registry_uses_only_direct_blocks_without_embedded_secret() -> None:
    registry = load_provider_registry()
    assert set(registry) >= {"active", "llm", "embedding"}
    assert "profiles" not in registry
    assert registry["active"] == {"llm": "local_llm", "embedding": "local_embedding"}
    assert registry["llm"]["model"]
    assert registry["embedding"]["model"]
    assert "sk-" not in str(registry)


def test_direct_config_secret_is_resolved_and_redacted(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("THERASKILL_AGENT_ROOT", str(tmp_path))
    registry_dir = tmp_path / "configs" / "theraskill"
    registry_dir.mkdir(parents=True)
    (registry_dir / "providers.yaml").write_text(
        """
active:
  llm: local_llm
  embedding: local_embedding
llm:
  model: configured-chat
  base_url: https://chat.example/v1
  api_key: test-secret
embedding:
  model: configured-vector
  base_url: https://vector.example/v1
  endpoint: https://vector.example/v1/embeddings
  api_key: vector-secret
""",
        encoding="utf-8",
    )
    resolved = resolve_model_config()
    assert resolved["name"] == "configured-chat"
    assert resolved["api_key"] == "test-secret"
    public = public_provider_info("llm")
    assert public["api_key"] == "<configured>"
    assert "test-secret" not in str(public)


def test_provider_check_reports_direct_credential_state(monkeypatch) -> None:
    monkeypatch.delenv("THERASKILL_LOCAL_API_KEY", raising=False)
    result = check_provider("llm")
    assert result["credential_configured"] is False
    assert set(list_providers()) == {"llm", "embedding"}


def test_local_provider_file_overrides_llm_and_embedding_independently(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("THERASKILL_AGENT_ROOT", str(tmp_path))
    registry_dir = tmp_path / "configs" / "theraskill"
    registry_dir.mkdir(parents=True)
    (registry_dir / "providers.yaml").write_text(
        """
active:
  llm: local_llm
  embedding: local_embedding
llm:
  model: base-llm
  base_url: http://base-llm/v1
embedding:
  model: base-embedding
  base_url: http://base-embedding/v1
  endpoint: http://base-embedding/v1/embeddings
""",
        encoding="utf-8",
    )
    (registry_dir / "providers.local.yaml").write_text(
        """
llm:
  model: custom-chat
  base_url: https://llm.example/v1
  api_key: llm-secret
embedding:
  model: custom-vector
  base_url: https://embedding.example/v1
  endpoint: https://embedding.example/v1/embeddings
  api_key: embedding-secret
""",
        encoding="utf-8",
    )
    registry = load_provider_registry()
    assert registry["_local_path"].endswith("providers.local.yaml")
    llm = resolve_model_config()
    embedding = resolve_embedding_config()
    assert (llm["name"], llm["api_key"]) == ("custom-chat", "llm-secret")
    assert (embedding["endpoint"], embedding["api_key"]) == (
        "https://embedding.example/v1/embeddings", "embedding-secret"
    )


def test_profiles_are_rejected_instead_of_silently_interpreted(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("THERASKILL_AGENT_ROOT", str(tmp_path))
    registry_dir = tmp_path / "configs" / "theraskill"
    registry_dir.mkdir(parents=True)
    (registry_dir / "providers.yaml").write_text(
        "active: {llm: old, embedding: old}\nprofiles: {old: {name: old}}\n",
        encoding="utf-8",
    )
    try:
        load_provider_registry()
    except ProviderConfigError as exc:
        assert "no longer supported" in str(exc)
    else:  # pragma: no cover - assertion branch is the test
        raise AssertionError("legacy provider profiles were accepted")


def test_active_direct_configuration_controls_domain_model_calls(monkeypatch) -> None:
    model = merged_model_config(
        {"extraction": {"model": {"name": "legacy", "base_url": "http://legacy"}}},
        {"model": {"name": "old-local", "base_url": "http://old-local"}},
    )
    embedding = merged_embedding_config(
        {"retrieval": {"embedding": {"enabled": True}}},
        {},
    )
    assert model["name"] == resolve_model_config()["name"]
    assert embedding["name"] == resolve_embedding_config()["name"]


def test_theraskill_platform_kernel_exposes_secret_free_provider_status() -> None:
    snapshot = THERASKILL_PLATFORM.runtime_snapshot()
    assert snapshot["platform_id"] == "theraskill"
    assert {"support", "simulation", "response_review", "contribution", "extraction"} <= set(snapshot["capabilities"])
    assert "providers" in snapshot
    assert "api_key" not in str(snapshot["providers"]).lower() or "<configured>" in str(snapshot["providers"])
