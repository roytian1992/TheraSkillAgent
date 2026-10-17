"""FastAPI middleware for the bounded TheraSkill metrics endpoint."""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from .observability import RequestMetrics


class MetricsMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, metrics: RequestMetrics):
        super().__init__(app)
        self.metrics = metrics

    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            self.metrics.observe(request.url.path, status_code, started)
