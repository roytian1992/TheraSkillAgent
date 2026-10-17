"""TheraSkillAgent provider profiles for all domain model calls.

The platform has two model classes (generation and embeddings), but both use
the same provider profile contract.  A profile contains only non-secret
connection metadata; credentials are resolved from the process environment or
the active runtime environment file at call time.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Mapping

import yaml

from theraskill_agent.paths import resource_path
from theraskill_agent.config import cfg_get, load_config_readonly, load_env


_SECRET_KEYS = {"api_key", "token", "authorization", "password", "secret"}


class ProviderConfigError(ValueError):
    """Raised when a configured TheraSkill provider cannot be resolved."""


def _active_env() -> dict[str, str]:
    """Return environment values, including values from the active runtime."""
    values = dict(os.environ)
    try:
        for key, value in load_env().items():
            values.setdefault(str(key), str(value))
    except Exception:
        # A standalone worker may run before the runtime has created .env. Explicit
        # process environment variables still work in that case.
        pass
    return values


def load_provider_registry(path: str | Path | None = None) -> dict[str, Any]:
    """Load the profile registry and validate its top-level shape."""
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
        raise ProviderConfigError(f"provider registry does not exist: {registry_path}")
    raw = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict) or not isinstance(raw.get("profiles"), dict):
        raise ProviderConfigError(f"provider registry has no profiles mapping: {registry_path}")
    raw["_path"] = str(registry_path)
    return raw


def _active_profile(kind: str, registry: Mapping[str, Any]) -> str:
    env_name = "THERASKILL_LLM_PROFILE" if kind == "llm" else "THERASKILL_EMBEDDING_PROFILE"
    env_value = os.environ.get(env_name, "").strip()
    if env_value:
        return env_value
    try:
        config_value = cfg_get(
            load_config_readonly(), "platforms", "theraskill", "providers",
            f"{kind}_profile", default=None,
        )
    except Exception:
        config_value = None
    if config_value:
        return str(config_value).strip()
    defaults = registry.get("active") or {}
    return str(defaults.get(kind) or "").strip()


def configured_profile(kind: str) -> str | None:
    """Return an explicit runtime selection, excluding registry defaults."""
    env_name = "THERASKILL_LLM_PROFILE" if kind == "llm" else "THERASKILL_EMBEDDING_PROFILE"
    env_value = os.environ.get(env_name, "").strip()
    if env_value:
        return env_value
    try:
        value = cfg_get(
            load_config_readonly(), "platforms", "theraskill", "providers",
            f"{kind}_profile", default=None,
        )
    except Exception:
        value = None
    return str(value).strip() if value else None


def _resolve_secret(config: dict[str, Any]) -> None:
    env_name = str(config.get("api_key_env") or "").strip()
    if env_name:
        value = _active_env().get(env_name)
        if value:
            config["api_key"] = value


def _normalize_profile(kind: str, profile_name: str, profile: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(profile))
    result["profile"] = profile_name
    result.setdefault("provider", "openai-compatible")
    if kind == "llm":
        if not result.get("name"):
            raise ProviderConfigError(f"LLM profile {profile_name!r} has no model name")
        if str(result.get("provider")).lower() not in {"hermes", "hermes-native", "runtime", "host", "agent"} and not result.get("base_url"):
            raise ProviderConfigError(f"LLM profile {profile_name!r} has no base_url")
    else:
        if not result.get("name"):
            raise ProviderConfigError(f"embedding profile {profile_name!r} has no model name")
        if not result.get("base_url") and not result.get("endpoint"):
            raise ProviderConfigError(f"embedding profile {profile_name!r} has no base_url or endpoint")
    _resolve_secret(result)
    return result


def resolve_profile(
    kind: str,
    profile_name: str | None = None,
    *,
    registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one named or active profile, including its secret at runtime."""
    if kind not in {"llm", "embedding"}:
        raise ProviderConfigError(f"unsupported provider kind: {kind}")
    registry = registry or load_provider_registry()
    selected = str(profile_name or _active_profile(kind, registry)).strip()
    if not selected:
        raise ProviderConfigError(f"no active {kind} provider profile configured")
    profiles = registry.get("profiles") or {}
    raw = profiles.get(selected)
    if not isinstance(raw, Mapping):
        raise ProviderConfigError(f"unknown {kind} provider profile: {selected}")
    return _normalize_profile(kind, selected, raw)


def _resolve_explicit(config: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(config))
    _resolve_secret(result)
    # A direct api_key_env is deliberately allowed to be missing here. The
    # client can still be used against local endpoints that do not authenticate;
    # remote endpoints will return a clear HTTP error from the provider.
    return result


def resolve_model_config(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Resolve a model mapping or the active TheraSkillAgent LLM profile."""
    config = config or {}
    profile = config.get("profile") or config.get("provider_profile")
    if not profile and configured_profile("llm"):
        profile = configured_profile("llm")
    if profile:
        resolved = resolve_profile("llm", str(profile))
        resolved.update({k: v for k, v in config.items() if k not in {"profile", "provider_profile"} and v is not None})
        _resolve_secret(resolved)
        return resolved
    if config:
        return _resolve_explicit(config)
    return resolve_profile("llm")


def resolve_embedding_config(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Resolve an embedding mapping or the active TheraSkillAgent profile."""
    config = config or {}
    profile = config.get("profile") or config.get("provider_profile")
    if not profile and configured_profile("embedding"):
        profile = configured_profile("embedding")
    if profile:
        resolved = resolve_profile("embedding", str(profile))
        resolved.update({k: v for k, v in config.items() if k not in {"profile", "provider_profile"} and v is not None})
        _resolve_secret(resolved)
        return resolved
    if config:
        return _resolve_explicit(config)
    return resolve_profile("embedding")


def public_provider_info(kind: str, profile_name: str | None = None) -> dict[str, Any]:
    """Return UI/audit-safe provider metadata with all secrets redacted."""
    resolved = resolve_profile(kind, profile_name)
    safe = {}
    for key, value in resolved.items():
        if key.lower() in _SECRET_KEYS or key.lower().endswith("_key"):
            if value:
                safe[key] = "<configured>"
            continue
        safe[key] = value
    safe.pop("api_key_env", None)
    return safe
