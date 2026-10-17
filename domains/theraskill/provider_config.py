"""Direct LLM and embedding configuration for TheraSkillAgent.

The provider file deliberately has one shape and one source of truth:

``active`` selects the two direct blocks, while ``llm`` and ``embedding``
contain the model, endpoint and credential for each service. There is no
provider catalog or model-specific profile registry. A sibling
``providers.local.yaml`` may override either block without changing the
shareable template.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

import yaml

from theraskill_agent.config import cfg_get, load_config_readonly
from theraskill_agent.paths import resource_path


_SECRET_KEYS = {"api_key", "token", "authorization", "password", "secret"}
_KINDS = ("llm", "embedding")


class ProviderConfigError(ValueError):
    """Raised when a direct TheraSkill provider block is invalid."""


def _merge_mappings(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge local settings over the shared direct configuration."""
    merged = copy.deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _merge_mappings(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _read_yaml(path: Path, *, label: str) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ProviderConfigError(f"invalid {label} provider YAML: {path}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ProviderConfigError(f"{label} provider config must be a mapping: {path}")
    return dict(raw)


def load_provider_registry(path: str | Path | None = None) -> dict[str, Any]:
    """Load and validate the direct ``active``/``llm``/``embedding`` file."""
    configured = path
    if configured is None:
        try:
            configured = cfg_get(
                load_config_readonly(),
                "platforms", "theraskill", "providers", "config_path",
                default=None,
            )
        except Exception:
            configured = None
    registry_path = Path(str(configured)).expanduser() if configured else Path("configs/theraskill/providers.yaml")
    if not registry_path.is_absolute():
        registry_path = resource_path(registry_path)
    if not registry_path.exists():
        raise ProviderConfigError(f"provider configuration does not exist: {registry_path}")

    raw = _read_yaml(registry_path, label="provider")
    local_path = registry_path.with_name("providers.local.yaml") if registry_path.name == "providers.yaml" else None
    if local_path and local_path.is_file():
        raw = _merge_mappings(raw, _read_yaml(local_path, label="local provider"))
    normalized = _normalize_registry(raw, registry_path)
    normalized["_path"] = str(registry_path)
    if local_path and local_path.is_file():
        normalized["_local_path"] = str(local_path)
    return normalized


def _normalize_registry(raw: Mapping[str, Any], registry_path: Path) -> dict[str, Any]:
    """Validate the only supported provider schema and normalize model names."""
    if "profiles" in raw:
        raise ProviderConfigError(
            f"provider profiles are no longer supported; configure active/llm/embedding directly: {registry_path}"
        )
    active = raw.get("active")
    if not isinstance(active, Mapping):
        raise ProviderConfigError(f"provider active must be a mapping: {registry_path}")
    normalized: dict[str, Any] = {
        key: copy.deepcopy(value)
        for key, value in raw.items()
        if key not in {"active", "llm", "embedding"}
    }
    normalized["active"] = {}
    for kind in _KINDS:
        active_name = str(active.get(kind) or f"local_{kind}").strip()
        if not active_name:
            raise ProviderConfigError(f"provider active {kind} is empty: {registry_path}")
        block = raw.get(kind)
        if not isinstance(block, Mapping):
            raise ProviderConfigError(f"provider {kind} must be a mapping: {registry_path}")
        normalized["active"][kind] = active_name
        normalized[kind] = _normalize_direct_block(kind, block, registry_path)
    return normalized


def _normalize_direct_block(kind: str, block: Mapping[str, Any], registry_path: Path) -> dict[str, Any]:
    result = copy.deepcopy(dict(block))
    model = str(result.get("model") or result.get("name") or "").strip()
    if not model:
        raise ProviderConfigError(f"provider {kind} model is required: {registry_path}")
    result["model"] = model
    result["name"] = model
    result.setdefault("kind", kind)
    result.setdefault("provider", "openai-compatible")
    if kind == "llm" and not str(result.get("base_url") or "").strip():
        raise ProviderConfigError(f"provider llm base_url is required: {registry_path}")
    if kind == "embedding" and not str(result.get("base_url") or result.get("endpoint") or "").strip():
        raise ProviderConfigError(f"provider embedding base_url or endpoint is required: {registry_path}")
    return result


def _direct_config(kind: str, config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    registry = load_provider_registry()
    block = registry[kind]
    if config:
        rejected = {key for key in ("profile", "provider_profile", "api_key_env") if key in config}
        if rejected:
            names = ", ".join(sorted(rejected))
            raise ProviderConfigError(f"provider profile fields are not supported ({names}); edit providers.yaml directly")
        block = _merge_mappings(block, {key: value for key, value in config.items() if value is not None})
    return _normalize_direct_block(kind, block, Path(str(registry.get("_path", "providers.yaml"))))


def resolve_model_config(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return the configured direct LLM block."""
    return _direct_config("llm", config)


def resolve_embedding_config(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return the configured direct embedding block."""
    return _direct_config("embedding", config)


def public_provider_info(kind: str) -> dict[str, Any]:
    """Return UI/audit-safe direct provider metadata with credentials redacted."""
    if kind not in _KINDS:
        raise ProviderConfigError(f"unsupported provider kind: {kind}")
    resolved = resolve_model_config() if kind == "llm" else resolve_embedding_config()
    safe: dict[str, Any] = {}
    for key, value in resolved.items():
        if key.lower() in _SECRET_KEYS or key.lower().endswith("_key"):
            if value:
                safe[key] = "<configured>"
            continue
        safe[key] = value
    return safe
