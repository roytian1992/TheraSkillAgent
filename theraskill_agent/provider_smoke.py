"""Secret-safe provider discovery and optional live smoke checks."""

from __future__ import annotations

from typing import Any

from .text import redact_secrets


def list_profiles() -> dict[str, list[dict[str, Any]]]:
    from domains.theraskill.provider_config import load_provider_registry, public_provider_info, resolve_profile

    registry = load_provider_registry()
    result: dict[str, list[dict[str, Any]]] = {"llm": [], "embedding": []}
    for name, raw in sorted((registry.get("profiles") or {}).items()):
        kind = str((raw or {}).get("kind", "llm"))
        if kind not in result:
            continue
        info = public_provider_info(kind, name)
        try:
            active_name = str(resolve_profile(kind).get("profile") or "")
        except Exception:
            active_name = str((registry.get("active") or {}).get(kind) or "")
        info["active"] = active_name == name
        # An ``api_key_env`` entry only names where a credential should come
        # from.  It must not be reported as configured until resolution found
        # a non-empty value in the process/runtime environment.
        info["credential_configured"] = bool(
            (public_provider_info(kind, name).get("api_key") == "<configured>")
        )
        result[kind].append(info)
    return result


def check_profile(kind: str, profile: str | None = None, *, live: bool = False) -> dict[str, Any]:
    from domains.theraskill.provider_config import resolve_embedding_config, resolve_model_config, resolve_profile

    resolved = resolve_profile(kind, profile)
    safe = {
        "kind": kind,
        "profile": resolved.get("profile"),
        "model": resolved.get("name"),
        "provider": resolved.get("provider"),
        "base_url": resolved.get("base_url") or resolved.get("endpoint"),
        "credential_configured": bool(resolved.get("api_key")),
        "live": bool(live),
        "status": "configured",
    }
    if not live:
        safe["message"] = "configuration loaded; network was not contacted"
        return safe
    try:
        if kind == "llm":
            from domains.theraskill.theraskill_llm import OpenAICompatibleClient

            config = resolve_model_config({"profile": resolved["profile"]})
            config.update({"temperature": 0, "max_output_tokens": 8, "retry_attempts": 0, "timeout_seconds": min(int(config.get("timeout_seconds", 30)), 30)})
            response = OpenAICompatibleClient(config).chat(
                "Return only the word OK.",
                {"smoke_test": "Respond with OK only."},
                force_json_response=False,
            )
            safe["response_preview"] = str(response).strip()[:80]
        else:
            from domains.theraskill.theraskill_llm import OpenAICompatibleEmbeddingClient

            config = resolve_embedding_config({"profile": resolved["profile"]})
            config.update({"retry_attempts": 0, "timeout_seconds": min(int(config.get("timeout_seconds", 30)), 30)})
            vector = OpenAICompatibleEmbeddingClient(config).embed(["theraskill smoke test"])[0]
            safe["vector_dimensions"] = len(vector)
        safe["status"] = "ok"
    except Exception as exc:  # noqa: BLE001 - report a stable CLI result
        safe["status"] = "error"
        safe["error"] = redact_secrets(f"{type(exc).__name__}: {exc}")
    return safe
