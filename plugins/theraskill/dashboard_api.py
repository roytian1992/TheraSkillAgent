"""Same-origin Dashboard proxy for the TheraSkill service applications."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .service import service_config


_SERVICE_NAMES = {
    "support": "support",
    "simulation": "simulation",
    "expert": "response_review",
}

_FORWARDED_HEADERS = {
    "accept",
    "authorization",
    "content-type",
    "idempotency-key",
    "x-expert-profile",
    "x-expert-role",
    "x-expert-user",
    "x-simulation-role",
    "x-simulation-user",
    "x-support-role",
    "x-support-user",
}


def create_router(app_name: str) -> APIRouter:
    """Create the API mounted below one Dashboard plugin namespace."""
    if app_name not in _SERVICE_NAMES:
        raise ValueError(f"unsupported TheraSkill Dashboard app: {app_name}")

    router = APIRouter()

    @router.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    async def proxy(request: Request, path: str) -> Response:
        service = service_config(_SERVICE_NAMES[app_name])
        if service is None:
            return JSONResponse({"detail": f"{app_name} service is not configured"}, status_code=503)

        target = f"{service.base_url}/{path.lstrip('/')}"
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() in _FORWARDED_HEADERS
        }
        if service.token and "authorization" not in {key.lower() for key in headers}:
            headers["authorization"] = f"Bearer {service.token}"

        try:
            async with httpx.AsyncClient(timeout=service.timeout_seconds) as client:
                upstream = await client.request(
                    request.method,
                    target,
                    params=request.query_params,
                    content=await request.body(),
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            return JSONResponse(
                {"detail": f"TheraSkill {app_name} service unavailable: {exc}"},
                status_code=502,
            )

        content_type = upstream.headers.get("content-type", "")
        if content_type.startswith("application/json"):
            try:
                return JSONResponse(upstream.json(), status_code=upstream.status_code)
            except ValueError:
                pass
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=content_type.split(";", 1)[0] or None,
        )

    return router
