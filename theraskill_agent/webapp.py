"""Serve the built workspaces with the existing Python runtime, without Node."""

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from domains.theraskill.runtime import service_specs
from .paths import resource_path


UI_APPS = {
    "/theraskill/support": "support_assistant",
    "/theraskill/simulation": "simulation_console",
    "/theraskill/expert": "expert_studio",
    "": "workbench",
}
_HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
                "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length"}


def create_app(*, assets_root: Path | None = None, transport=None) -> FastAPI:
    assets_root = assets_root or resource_path("views/theraskill")
    missing = [name for name in UI_APPS.values() if not (assets_root / name / "dist/index.html").is_file()]
    if missing:
        raise RuntimeError("UI assets missing; run npm ci && npm run build in the source checkout: " + ", ".join(missing))

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=5), trust_env=False,
                                     follow_redirects=False, transport=transport) as client:
            app.state.client = client
            try:
                yield
            finally:
                from .resource_jobs import cancel_running_jobs
                cancel_running_jobs()

    app = FastAPI(title="TheraSkillAgent Workbench", lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"status": "ok", "application": "TheraSkillAgent Workbench"}

    async def proxy(request: Request, service: str, path: str = ""):
        # The browser can select only our named services, never arbitrary URLs.
        service = service.removeprefix("theraskill-")
        spec = service_specs().get(service)
        if spec is None:
            raise HTTPException(404, "Unknown service")
        if "\\" in path or any(part == ".." for part in path.split("/")):
            raise HTTPException(400, "Invalid service path")
        url = httpx.URL(f"http://127.0.0.1:{int(spec['port'])}/{path.lstrip('/')}")
        url = url.copy_with(query=request.url.query.encode("utf-8"))
        headers = {key: value for key, value in request.headers.items() if key.lower() not in _HOP_HEADERS}
        try:
            upstream = await request.app.state.client.request(
                request.method, url, headers=headers, content=await request.body(),
            )
        except httpx.RequestError:
            raise HTTPException(503, f"{service} service unavailable; start it with theraskill-agent service start --service {service}") from None
        # httpx decodes the body; don't forward the original compression headers.
        response_headers = {key: value for key, value in upstream.headers.items()
                            if key.lower() not in _HOP_HEADERS | {"content-encoding"}}
        return Response(upstream.content, status_code=upstream.status_code, headers=response_headers)

    from .resource_api import router as resource_router
    app.include_router(resource_router)
    from .overview import create_router as overview_router
    app.include_router(overview_router(), prefix="/api")

    methods = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    app.add_api_route("/api/plugins/{service}/{path:path}", proxy, methods=methods)
    app.add_api_route("/api/{service}/{path:path}", proxy, methods=methods)
    for route, name in UI_APPS.items():
        app.mount(route or "/", StaticFiles(directory=assets_root / name / "dist", html=True), name=name)
    return app
