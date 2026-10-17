#!/usr/bin/env python3
"""Check that Hermes' configured model matches the local OpenAI endpoint.

This is intentionally a read-only contract check.  It prevents a common
deployment failure where ``config.yaml`` names one model while the vLLM
process on the configured port serves another model.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml


SCHEMA_VERSION = "theraskill_model_contract_v1"


def _load_config(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"config must be a mapping: {path}")
    model = payload.get("model")
    if not isinstance(model, dict):
        raise ValueError(f"config has no model mapping: {path}")
    return payload


def _models_url(base_url: str) -> str:
    value = base_url.rstrip("/")
    return f"{value}/models" if value.endswith("/v1") else f"{value}/v1/models"


def _dotenv_value(path: Path, key: str) -> str:
    """Read one simple KEY=value entry without exposing the file contents."""
    if not key or not path.exists():
        return ""
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() != key:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            return value
    except OSError:
        return ""
    return ""


def _fetch_models(url: str, api_key: str, timeout: float) -> tuple[int, list[dict[str, Any]]]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
        rows = body.get("data", []) if isinstance(body, dict) else []
        if not isinstance(rows, list):
            raise ValueError("/v1/models response has a non-list data field")
        return int(response.status), [row for row in rows if isinstance(row, dict)]


def check_model_contract(
    config_path: Path,
    *,
    timeout: float = 5.0,
    expected_model: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Return a JSON-serializable model contract report."""

    failures: list[str] = []
    config_error: str | None = None
    configured_model = ""
    provider = ""
    base_url = ""
    api_mode = ""
    api_key_env = ""
    try:
        config = _load_config(config_path)
        model = config["model"]
        configured_model = str(model.get("default") or "").strip()
        provider = str(model.get("provider") or "").strip()
        base_url = str(model.get("base_url") or "").strip()
        api_mode = str(model.get("api_mode") or "").strip()
        api_key_env = str(model.get("api_key_env") or "").strip()
    except (OSError, ValueError, yaml.YAMLError, KeyError) as exc:
        config_error = str(exc)
        failures.append(f"invalid Hermes model config: {exc}")

    target_model = str(expected_model or configured_model).strip()
    observed: list[dict[str, Any]] = []
    endpoint = _models_url(base_url) if base_url else ""
    fetch_error: str | None = None
    if not base_url:
        failures.append("model.base_url is empty")
    if not configured_model:
        failures.append("model.default is empty")
    if target_model and configured_model and target_model != configured_model:
        failures.append(
            f"expected model differs from config: expected={target_model!r} configured={configured_model!r}"
        )
    if endpoint:
        token = api_key if api_key is not None else os.getenv(api_key_env, "")
        if not token:
            token = _dotenv_value(config_path.parent / ".env", api_key_env)
        try:
            status, observed = _fetch_models(endpoint, token, timeout)
            if status != 200:
                failures.append(f"model endpoint returned HTTP {status}")
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
            fetch_error = str(exc)
            failures.append(f"model endpoint check failed: {exc}")

    observed_ids = [str(row.get("id") or "") for row in observed if row.get("id")]
    if target_model and observed and target_model not in observed_ids:
        failures.append(
            f"configured model is not served: configured={target_model!r} observed={observed_ids!r}"
        )
    if len(observed_ids) != 1:
        failures.append(f"expected exactly one local model, observed={observed_ids!r}")

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "passed" if not failures else "failed",
        "config_path": str(config_path),
        "config": {
            "provider": provider,
            "model": configured_model,
            "base_url": base_url,
            "api_mode": api_mode,
            "api_key_env": api_key_env,
        },
        "endpoint": endpoint,
        "expected_model": target_model,
        "observed_models": observed,
        "observed_model_ids": observed_ids,
        "config_error": config_error,
        "fetch_error": fetch_error,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Hermes HERMES_HOME/config.yaml")
    parser.add_argument("--expected-model", help="Optional model name to require in addition to config.yaml")
    parser.add_argument("--api-key-env", help="Override the API key environment variable from config.yaml")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()
    api_key = os.getenv(args.api_key_env, "") if args.api_key_env else None
    result = check_model_contract(
        args.config,
        timeout=args.timeout,
        expected_model=args.expected_model,
        api_key=api_key,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
