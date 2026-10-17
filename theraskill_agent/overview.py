"""Unified read-only control-plane API for the TheraSkill Dashboard Hub."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter

from .config import cfg_get, load_config_readonly
from .paths import data_root
from .platform import THERASKILL_PLATFORM


SERVICES = {
    "support": ("support", "Support", "/theraskill/support"),
    "simulation": ("simulation", "Simulation", "/theraskill/simulation"),
    "expert": ("response_review", "Expert", "/theraskill/expert"),
    "contribution": ("contribution", "Contribution", None),
    "extraction": ("extraction", "Extraction", None),
}


def _service_config(name: str, config: dict[str, Any]) -> tuple[str, float, str]:
    raw = cfg_get(config, "platforms", "theraskill", "services", name, default={})
    raw = raw if isinstance(raw, dict) else {}
    defaults = {"support": 8878, "simulation": 8876, "response_review": 8879, "contribution": 8881, "extraction": 8891}
    base_url = str(raw.get("base_url") or f"http://127.0.0.1:{defaults[name]}").rstrip("/")
    try:
        timeout = max(1.0, min(float(raw.get("timeout_seconds", 5)), 15.0))
    except (TypeError, ValueError):
        timeout = 5.0
    return base_url, timeout, str(raw.get("token") or "")


async def _probe(name: str, config: dict[str, Any]) -> dict[str, Any]:
    base_url, timeout, token = _service_config(name, config)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    result: dict[str, Any] = {"base_url": base_url, "status": "offline", "health": None, "metrics": None}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            health_response, metrics_response = await asyncio.gather(
                client.get(f"{base_url}/health", headers=headers),
                client.get(f"{base_url}/metrics", headers=headers),
            )
        result["status"] = "online" if health_response.is_success else "degraded"
        result["health"] = health_response.json()
        if metrics_response.is_success:
            result["metrics"] = metrics_response.json()
    except (httpx.HTTPError, ValueError) as exc:
        result["error"] = str(exc)
    return result


def _release_summary() -> dict[str, Any]:
    root = data_root()
    releases = root / "releases"
    current = root / "current"
    if not current.exists():
        candidates = sorted(path for path in releases.iterdir() if path.is_dir()) if releases.exists() else []
        current = candidates[-1] if candidates else None
    evaluation = root / "evaluations" / "manifest.json"
    return {
        "current_release": current.name if current else None,
        "release_available": bool(current),
        "evaluation_manifest": str(evaluation) if evaluation.exists() else None,
        "evaluation_ready": evaluation.exists(),
    }


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/overview")
    async def overview() -> dict[str, Any]:
        config = load_config_readonly()
        probes = await asyncio.gather(*(_probe(name, config) for name, _, _ in SERVICES.values()))
        services = {
            key: {"label": label, "app_path": app_path, **probe}
            for key, (_, label, app_path), probe in zip(SERVICES, SERVICES.values(), probes)
        }
        model = cfg_get(config, "model", default={})
        model = model if isinstance(model, dict) else {}
        return {
            "schema_version": "theraskill_hub_overview_v1",
            "platform": THERASKILL_PLATFORM.runtime_snapshot(),
            "services": services,
            "model": {
                "provider": model.get("provider") or "auto",
                "name": model.get("default") or model.get("model") or "auto",
                "base_url": model.get("base_url") or None,
            },
            "release": _release_summary(),
            "apps": [
                {"id": "support", "label": "Support", "path": "/theraskill/support"},
                {"id": "simulation", "label": "Simulation", "path": "/theraskill/simulation"},
                {"id": "expert", "label": "Expert Studio", "path": "/theraskill/expert"},
                {"id": "extraction", "label": "Knowledge Extraction", "path": "/theraskill/expert?view=governance"},
            ],
        }

    return router


router = create_router()
