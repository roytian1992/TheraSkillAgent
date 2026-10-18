"""Serve all workspaces and domain APIs in one process and one port."""

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from domains.theraskill.runtime import service_specs
from .paths import resource_path
from .local_services import get_services, set_services, LocalServices


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

    from .task_runtime import TaskEngine
    task_engine = TaskEngine()

    services = LocalServices(transport=transport) if transport is not None else get_services()

    @asynccontextmanager
    async def lifespan(app):
        import asyncio
        set_services(services)
        try:
            await asyncio.to_thread(services.start)
            yield
        finally:
            from .resource_jobs import cancel_running_jobs
            cancel_running_jobs()
            task_engine.close()
            await asyncio.to_thread(services.close)

    app = FastAPI(title="TheraSkillAgent Workbench", lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"status": "ok", "application": "TheraSkillAgent", "deployment": "single_process",
                "modules": list(services.clients)}

    async def proxy(request: Request, service: str, path: str = ""):
        # The browser can select only our named services, never arbitrary URLs.
        service = service.removeprefix("theraskill-")
        spec = service_specs().get(service)
        if spec is None:
            raise HTTPException(404, "Unknown service")
        if "\\" in path or any(part == ".." for part in path.split("/")):
            raise HTTPException(400, "Invalid service path")
        target = "/" + path.lstrip("/")
        if request.url.query:
            target += "?" + request.url.query
        headers = {key: value for key, value in request.headers.items() if key.lower() not in _HOP_HEADERS}
        try:
            upstream = await services.arequest(
                service, request.method, target, headers=headers, content=await request.body(),
            )
        except httpx.RequestError:
            raise HTTPException(503, f"{service} module unavailable; restart TheraSkillAgent") from None
        # httpx decodes the body; don't forward the original compression headers.
        response_headers = {key: value for key, value in upstream.headers.items()
                            if key.lower() not in _HOP_HEADERS | {"content-encoding"}}
        return Response(upstream.content, status_code=upstream.status_code, headers=response_headers)

    from .resource_api import router as resource_router
    app.include_router(resource_router)
    from .overview import create_router as overview_router
    app.include_router(overview_router(services=services), prefix="/api")

    from .task_api import create_router as task_router
    app.include_router(task_router(task_engine))

    methods = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    app.add_api_route("/api/plugins/{service}/{path:path}", proxy, methods=methods)
    app.add_api_route("/api/{service}/{path:path}", proxy, methods=methods)
    for route, name in UI_APPS.items():
        app.mount(route or "/", StaticFiles(directory=assets_root / name / "dist", html=True), name=name)
    return app
