"""Run domain APIs inside this process, without listening sockets.

One event loop owns all domain lifespans and their background workers. Sync
agent tools and async web routes share it; requests still pass through the
same validation, role checks, middleware and exception handlers as the APIs.
"""
from __future__ import annotations

import atexit
import asyncio
import importlib
import threading
from contextlib import AsyncExitStack
from contextvars import ContextVar

import httpx

_default = None
_default_lock = threading.RLock()
_inside_request = ContextVar('theraskill_inside_domain_request', default=False)


class _ResponseApp:
    """Return at response completion, as HTTP does, while background work runs."""
    def __init__(self, app):
        self.app = app
        self.pending = set()

    async def __call__(self, scope, receive, send):
        sent = asyncio.Event()
        async def forward(message):
            await send(message)
            if message['type'] == 'http.response.body' and not message.get('more_body', False):
                sent.set()
        task = asyncio.create_task(self.app(scope, receive, forward))
        self.pending.add(task)
        def finished(task):
            self.pending.discard(task)
            if not task.cancelled():
                task.exception()  # Retrieve post-response failures; domain jobs persist their errors.
        task.add_done_callback(finished)
        notification = asyncio.create_task(sent.wait())
        try:
            await asyncio.wait({task, notification}, return_when=asyncio.FIRST_COMPLETED)
            if task.done():
                await task
        finally:
            notification.cancel()
            if not sent.is_set() and not task.done():
                task.cancel()

    async def close(self):
        if self.pending:
            _, pending = await asyncio.wait(self.pending, timeout=5)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)


class LocalServices:
    def __init__(self, factories=None, *, transport=None):
        self.factories = factories
        self.transport = transport
        self.apps = {}
        self.clients = {}
        self._lock = threading.RLock()
        self._ready = threading.Event()
        self._thread = None
        self._error = None
        self._closed = False

    def start(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("Local services have stopped; restart the application")
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="theraskill-domains", daemon=True)
                self._thread.start()
        if not self._ready.wait(60):
            raise RuntimeError("Local domain startup timed out")
        if self._error is not None:
            raise RuntimeError(f"Local domain startup failed: {self._error}") from self._error
        return self

    def _run(self):
        try:
            asyncio.run(self._serve())
        except BaseException as exc:
            self._error = exc
        finally:
            self._ready.set()

    async def _serve(self):
        self.loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        # Leave thread-pool capacity for nested calls (expert -> support ->
        # governance). Internal requests inherit the context and skip this gate.
        self._entries = asyncio.Semaphore(8)
        from domains.theraskill.runtime import service_specs
        factories = self.factories
        if factories is None:
            factories = {}
            for name, spec in service_specs().items():
                module, factory = spec['module'].split(':')
                factories[name] = getattr(importlib.import_module(module), factory)
        async with AsyncExitStack() as stack:
            for name, factory in factories.items():
                if self.transport is None:
                    app = self.apps[name] = factory()
                    await stack.enter_async_context(app.router.lifespan_context(app))
                    response_app = _ResponseApp(app)
                    stack.push_async_callback(response_app.close)
                    transport = httpx.ASGITransport(app=response_app, raise_app_exceptions=False)
                else:
                    transport = self.transport
                self.clients[name] = await stack.enter_async_context(httpx.AsyncClient(
                    transport=transport, base_url=f"http://{name}.internal", timeout=600,
                    trust_env=False, follow_redirects=False,
                ))
            self._ready.set()
            await self._stop.wait()

    async def _request(self, service, method, path, **kwargs):
        service = service.removeprefix('theraskill-')
        service = 'expert' if service == 'response_review' else service
        if service not in self.clients:
            raise ValueError(f"Unknown domain: {service}")
        if not path.startswith('/') or path.startswith('//') or '\\' in path or '..' in path.split('/'):
            raise ValueError('Invalid domain path')
        if _inside_request.get():
            return await self.clients[service].request(method, path, **kwargs)
        async with self._entries:
            token = _inside_request.set(True)
            try:
                return await self.clients[service].request(method, path, **kwargs)
            finally:
                _inside_request.reset(token)

    def request(self, service, method, path, **kwargs):
        self.start()
        if threading.current_thread() is self._thread:
            raise RuntimeError('Use arequest from the domain event loop')
        future = asyncio.run_coroutine_threadsafe(self._request(service, method, path, **kwargs), self.loop)
        try:
            return future.result(timeout=660)
        except TimeoutError:
            future.cancel()
            raise httpx.ReadTimeout('Local domain request timed out') from None

    async def arequest(self, service, method, path, **kwargs):
        await asyncio.to_thread(self.start)
        if asyncio.get_running_loop() is self.loop:
            return await self._request(service, method, path, **kwargs)
        future = asyncio.run_coroutine_threadsafe(self._request(service, method, path, **kwargs), self.loop)
        return await asyncio.wrap_future(future)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self._thread and self._thread.is_alive():
            self._ready.wait(60)
            if hasattr(self, '_stop') and not self.loop.is_closed():
                self.loop.call_soon_threadsafe(self._stop.set)
            self._thread.join(timeout=30)
        global _default
        with _default_lock:
            if _default is self:
                _default = None


def get_services():
    global _default
    with _default_lock:
        if _default is None:
            _default = LocalServices()
        return _default


def set_services(services):
    """Install the process host before its workers can make internal calls."""
    global _default
    with _default_lock:
        if _default is not None and _default is not services:
            raise RuntimeError('Another local domain host is already active')
        _default = services


class _LocalTransport(httpx.BaseTransport):
    def __init__(self, name):
        self.name = name

    def handle_request(self, request):
        response = get_services().request(
            self.name, request.method, request.url.raw_path.decode('ascii'),
            headers=request.headers, content=request.read(),
        )
        return httpx.Response(response.status_code, headers=response.headers,
                              content=response.content, request=request)


def service_client(base_url, **kwargs):
    """Use an in-process domain, or an explicitly configured external URL."""
    if base_url.startswith('inprocess://'):
        name = base_url.removeprefix('inprocess://').strip('/')
        return httpx.Client(base_url=f'http://{name}.internal', transport=_LocalTransport(name),
                            trust_env=False, **kwargs)
    return httpx.Client(base_url=base_url, **kwargs)


def _shutdown():
    if _default is not None:
        _default.close()


atexit.register(_shutdown)
