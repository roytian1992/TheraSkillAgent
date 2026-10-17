"""Process orchestration for the TheraSkillAgent domain services."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .runtime import CODE_ROOT, runtime_environment, service_runtime_root, service_specs


class RuntimeLaunchError(RuntimeError):
    """Raised when a TheraSkill transport cannot be started or stopped."""


def _pid_path(service: str) -> Path:
    return service_runtime_root(service) / "service.pid"


def _read_pid(service: str) -> int | None:
    try:
        return int(_pid_path(service).read_text(encoding="utf-8").strip())
    except (FileNotFoundError, OSError, ValueError):
        return None


def _process(pid: int):
    try:
        import psutil

        process = psutil.Process(pid)
        return process if process.is_running() and process.status() != psutil.STATUS_ZOMBIE else None
    except Exception:
        try:
            os.kill(pid, 0)
        except OSError:
            return None
        return pid


def _port_available(port: int) -> bool:
    """Return whether the local service port can be bound right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", int(port)))
        except OSError:
            return False
    return True


def _wait_for_port(port: int, *, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.25)
            try:
                probe.connect(("127.0.0.1", int(port)))
                return True
            except OSError:
                time.sleep(0.1)
    return False


def status() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for service in service_specs():
        pid = _read_pid(service)
        running = pid is not None and _process(pid) is not None
        if not running and _pid_path(service).exists():
            _pid_path(service).unlink(missing_ok=True)
        result[service] = {"running": running, "pid": pid if running else None}
    return result


def start(*, services: list[str] | None = None) -> dict[str, dict[str, Any]]:
    requested = services or list(service_specs())
    unknown = sorted(set(requested) - set(service_specs()))
    if unknown:
        raise RuntimeLaunchError(f"unknown TheraSkill service(s): {', '.join(unknown)}")
    current = status()
    for service in requested:
        if current[service]["running"]:
            continue
        spec = service_specs()[service]
        port = int(spec["port"])
        if not _port_available(port):
            raise RuntimeLaunchError(
                f"cannot start TheraSkill service {service!r}: "
                f"127.0.0.1:{port} is already in use"
            )
        root = service_runtime_root(service)
        log_path = root / "service.log"
        log_handle = log_path.open("a", encoding="utf-8")
        command = [
            sys.executable,
            "-m",
            "uvicorn",
            str(spec["module"]),
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "info",
        ]
        process = subprocess.Popen(
            command,
            cwd=CODE_ROOT,
            env=runtime_environment(),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log_handle.close()
        _pid_path(service).write_text(str(process.pid), encoding="utf-8")
        current[service] = {"running": True, "pid": process.pid}
        if not _wait_for_port(port):
            try:
                process.terminate()
            except OSError:
                pass
            _pid_path(service).unlink(missing_ok=True)
            raise RuntimeLaunchError(
                f"TheraSkill service {service!r} did not become ready on 127.0.0.1:{port}; "
                f"see {log_path}"
            )
    return status()


def stop(*, services: list[str] | None = None, timeout: float = 10.0) -> dict[str, dict[str, Any]]:
    requested = services or list(service_specs())
    unknown = sorted(set(requested) - set(service_specs()))
    if unknown:
        raise RuntimeLaunchError(f"unknown TheraSkill service(s): {', '.join(unknown)}")
    for service in requested:
        pid = _read_pid(service)
        if pid is None:
            continue
        process = _process(pid)
        if process is None:
            _pid_path(service).unlink(missing_ok=True)
            continue
        try:
            process.terminate() if hasattr(process, "terminate") else os.kill(pid, 15)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and _process(pid) is not None:
                time.sleep(0.1)
            if _process(pid) is not None:
                process.kill() if hasattr(process, "kill") else os.kill(pid, 9)
        finally:
            _pid_path(service).unlink(missing_ok=True)
    return status()
