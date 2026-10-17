"""Small dependency-free metrics surface shared by TheraSkill transports."""

from __future__ import annotations

import time
from collections import Counter
from threading import Lock
from typing import Any


class RequestMetrics:
    def __init__(self, service: str) -> None:
        self.service = service
        self._lock = Lock()
        self._requests = Counter()
        self._errors = Counter()
        self._latency_ms: dict[str, list[float]] = {}

    def observe(self, route: str, status_code: int, started: float) -> None:
        with self._lock:
            self._requests[route] += 1
            if status_code >= 400:
                self._errors[route] += 1
            self._latency_ms.setdefault(route, []).append((time.perf_counter() - started) * 1000)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            routes = sorted(set(self._requests) | set(self._errors))
            return {
                "schema_version": "theraskill_metrics_v1",
                "service": self.service,
                "requests_total": sum(self._requests.values()),
                "errors_total": sum(self._errors.values()),
                "routes": {
                    route: {
                        "requests": self._requests[route],
                        "errors": self._errors[route],
                        "p50_latency_ms": round(_percentile(self._latency_ms.get(route, []), 0.50), 2),
                        "p95_latency_ms": round(_percentile(self._latency_ms.get(route, []), 0.95), 2),
                    }
                    for route in routes
                },
            }


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]
