"""Secret-safe direct provider discovery and optional live smoke checks."""

from __future__ import annotations

from typing import Any

from .text import redact_secrets


def list_providers() -> dict[str, dict[str, Any]]:
    from domains.theraskill.provider_config import load_provider_registry, public_provider_info

    registry = load_provider_registry()
    result: dict[str, dict[str, Any]] = {}
    for kind in ("llm", "embedding"):
        info = public_provider_info(kind)
        info["active"] = (registry.get("active") or {}).get(kind)
        result[kind] = info
    return result


def check_provider(kind: str, *, live: bool = False) -> dict[str, Any]:
    from domains.theraskill.provider_config import resolve_embedding_config, resolve_model_config

    if kind not in {"llm", "embedding"}:
        raise ValueError(f"unsupported provider kind: {kind}")
    resolved = resolve_model_config() if kind == "llm" else resolve_embedding_config()
    safe = {
        "kind": kind,
        "model": resolved.get("model") or resolved.get("name"),
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

            config = dict(resolved)
            config.update({
                "temperature": 0,
                "max_output_tokens": 8,
                "retry_attempts": 0,
                "timeout_seconds": min(int(config.get("timeout_seconds", 30)), 30),
            })
            response = OpenAICompatibleClient(config).chat(
                "Return only the word OK.",
                {"smoke_test": "Respond with OK only."},
                force_json_response=False,
            )
            safe["response_preview"] = str(response).strip()[:80]
        else:
            from domains.theraskill.theraskill_llm import OpenAICompatibleEmbeddingClient

            config = dict(resolved)
            config.update({"retry_attempts": 0, "timeout_seconds": min(int(config.get("timeout_seconds", 30)), 30)})
            vector = OpenAICompatibleEmbeddingClient(config).embed(["theraskill smoke test"])[0]
            safe["vector_dimensions"] = len(vector)
        safe["status"] = "ok"
    except Exception as exc:  # noqa: BLE001 - report a stable CLI result
        safe["status"] = "error"
        safe["error"] = redact_secrets(f"{type(exc).__name__}: {exc}")
    return safe
