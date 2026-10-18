"""Small, dependency-free timing collector for retrieval reports."""
from __future__ import annotations

import time
import threading
from contextlib import contextmanager


class RetrievalTimings:
    def __init__(self):
        self.started = time.perf_counter()
        self.stages = {}
        self._lock = threading.Lock()

    @contextmanager
    def stage(self, name):
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            with self._lock:
                self.stages[name] = self.stages.get(name, 0.0) + elapsed

    def call(self, name, function, *args, **kwargs):
        with self.stage(name):
            return function(*args, **kwargs)

    def snapshot(self):
        return {'total_ms': round((time.perf_counter() - self.started) * 1000, 3),
                'stages_ms': {name: round(value, 3) for name, value in self.stages.items()}}
