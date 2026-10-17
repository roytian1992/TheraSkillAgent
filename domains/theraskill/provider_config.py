"""TheraSkillAgent provider profiles for all domain model calls.

The platform has two model classes (generation and embeddings), but both use
the same provider profile contract. A profile contains shareable connection
metadata; project-local credentials are loaded from ``providers.local.yaml``
first, with process environment values kept as a compatibility fallback.
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
    """Load the profile registry and its optional local override.

    A sibling ``providers.local.yaml`` is merged over the shareable registry
    whenever the selected file is named ``providers.yaml``. This gives each
    LLM and embedding profile its own URL, model and ``api_key``.
    """
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
    try:
        raw = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ProviderConfigError(f"invalid provider registry YAML: {registry_path}: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("profiles"), dict):
        raise ProviderConfigError(f"provider registry has no profiles mapping: {registry_path}")
    local_path = registry_path.with_name("providers.local.yaml") if registry_path.name == "providers.yaml" else None
    if local_path and local_path.is_file():
        try:
            local = yaml.safe_load(local_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ProviderConfigError(f"invalid local provider YAML: {local_path}: {exc}") from exc
        if not isinstance(local, dict):
            raise ProviderConfigError(f"local provider config must be a mapping: {local_path}")
        if "profiles" in local and not isinstance(local["profiles"], dict):
            raise ProviderConfigError(f"local provider profiles must be a mapping: {local_path}")
        local_active, local_override = _normalize_local_override(local)
        raw = _merge_mappings(raw, local_override)
        if local_active:
            raw["_local_active_profiles"] = local_active
    raw["_path"] = str(registry_path)
    if local_path and local_path.is_file():
        raw["_local_path"] = str(local_path)
    return raw


def _merge_mappings(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge local settings while preserving profile defaults."""
    merged = copy.deepcopy(dict(base))
    for key, value in override.items():
        if key == "_path":
            continue
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _merge_mappings(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _normalize_inline_profile(kind: str, profile: Mapping[str, Any], profile_name: str) -> dict[str, Any]:
    """Convert the convenient local ``model``/``url`` keys to profile fields."""
    normalized = copy.deepcopy(dict(profile))
    normalized.pop("profile", None)
    normalized.setdefault("kind", kind)
    if not normalized.get("name") and normalized.get("model"):
        normalized["name"] = normalized.pop("model")
    if not normalized.get("base_url") and normalized.get("url"):
        normalized["base_url"] = normalized.pop("url")
    normalized["profile"] = profile_name
    return normalized


def _normalize_local_override(local: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    """Normalize direct local blocks while retaining legacy profile syntax."""
    override = copy.deepcopy(dict(local))
    active_values = override.get("active")
    if active_values is not None and not isinstance(active_values, Mapping):
        raise ProviderConfigError("local provider active must be a mapping")
    local_active: dict[str, str] = {}
    profiles = override.setdefault("profiles", {})

    # The user-facing form is intentionally model-centric:
    #
    #   llm:
    #     model: vendor-model-id
    #     base_url: https://vendor.example/v1
    #     api_key: ...
    #   embedding: ...
    #
    # Turn these blocks into ordinary profiles so all existing callers keep
    # the same resolution and redaction behavior.
    for kind in ("llm", "embedding"):
        inline = local.get(kind)
        if isinstance(inline, Mapping):
            profile_name = str(inline.get("profile") or f"local_{kind}").strip()
            if not profile_name:
                raise ProviderConfigError(f"local {kind} profile name is empty")
            profiles[profile_name] = _merge_mappings(
                profiles.get(profile_name) if isinstance(profiles.get(profile_name), Mapping) else {},
                _normalize_inline_profile(kind, inline, profile_name),
            )

    if isinstance(active_values, Mapping):
        for kind in ("llm", "embedding"):
            value = active_values.get(kind)
            if isinstance(value, Mapping):
                profile_name = str(value.get("profile") or f"local_{kind}").strip()
                profiles[profile_name] = _merge_mappings(
                    profiles.get(profile_name) if isinstance(profiles.get(profile_name), Mapping) else {},
                    _normalize_inline_profile(kind, value, profile_name),
                )
                local_active[kind] = profile_name
            elif value is not None:
                profile_name = str(value).strip()
                if not profile_name:
                    raise ProviderConfigError(f"local active {kind} profile is empty")
                local_active[kind] = profile_name

    # Do not leave direct blocks containing credentials in the merged registry;
    # the normalized profiles above are the only source of truth.
    override.pop("llm", None)
    override.pop("embedding", None)
    if local_active:
        override["active"] = {
            **(dict(active_values) if isinstance(active_values, Mapping) else {}),
            **local_active,
        }
        for kind, value in list(override["active"].items()):
            if isinstance(value, Mapping):
                override["active"][kind] = local_active.get(kind, f"local_{kind}")
    return local_active, override


def _active_profile(kind: str, registry: Mapping[str, Any]) -> str:
    env_name = "THERASKILL_LLM_PROFILE" if kind == "llm" else "THERASKILL_EMBEDDING_PROFILE"
    env_value = os.environ.get(env_name, "").strip()
    if env_value:
        return env_value
    local_active = registry.get("_local_active_profiles") or {}
    if local_active.get(kind):
        return str(local_active[kind]).strip()
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
        local_active = load_provider_registry().get("_local_active_profiles") or {}
    except ProviderConfigError:
        local_active = {}
    if local_active.get(kind):
        return str(local_active[kind]).strip()
    try:
        value = cfg_get(
            load_config_readonly(), "platforms", "theraskill", "providers",
            f"{kind}_profile", default=None,
        )
    except Exception:
        value = None
    return str(value).strip() if value else None


def _resolve_secret(config: dict[str, Any]) -> None:
    # A project-local api_key is preferred. Environment values remain a
    # backwards-compatible fallback and fill only an empty local placeholder.
    if str(config.get("api_key") or "").strip():
        return
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
