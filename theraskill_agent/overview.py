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


def create_router(*, services=None) -> APIRouter:
    router = APIRouter()

    @router.get("/overview")
    async def overview() -> dict[str, Any]:
        config = load_config_readonly()
        if services is None:
            from .local_services import get_services
            host = get_services()
        else:
            host = services
        async def probe_local(name):
            name = 'expert' if name == 'response_review' else name
            try:
                health, metrics = await asyncio.gather(host.arequest(name, 'GET', '/health'), host.arequest(name, 'GET', '/metrics'))
                return {'base_url': '/api/' + name, 'transport': 'inprocess',
                        'status': 'online' if health.is_success else 'degraded',
                        'health': health.json(), 'metrics': metrics.json() if metrics.is_success else None}
            except (httpx.HTTPError, ValueError) as exc:
                return {'status': 'offline', 'error': str(exc), 'transport': 'inprocess'}
        probes = await asyncio.gather(*(probe_local(name) for name, _, _ in SERVICES.values()))
        service_states = {
            key: {"label": label, "app_path": app_path, **probe}
            for key, (_, label, app_path), probe in zip(SERVICES, SERVICES.values(), probes)
        }
        model = cfg_get(config, "model", default={})
        model = model if isinstance(model, dict) else {}
        return {
            "schema_version": "theraskill_hub_overview_v1",
            "platform": THERASKILL_PLATFORM.runtime_snapshot(),
            "services": service_states,
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
