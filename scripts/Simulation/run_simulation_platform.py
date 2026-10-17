#!/usr/bin/env python3
"""Serve or call the TheraSkill Simulation Platform API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
for path in (PROJECT_ROOT, SRC_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import httpx  # noqa: E402

from services.simulation_api import SimulationAPISettings, create_app  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--role", default="human_therapist")
    parser.add_argument("--token", default=os.environ.get("THERASKILL_SIMULATION_API_TOKEN"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument(
        "--database",
        default=str(PROJECT_ROOT / "work/simulation_platform/sessions.sqlite"),
    )
    serve.add_argument(
        "--model-config",
        default=str(PROJECT_ROOT / "configs/theraskill/retrieval.yaml"),
    )
    serve.add_argument("--no-llm", action="store_true")

    scenarios = subparsers.add_parser("list-scenarios")
    scenarios.add_argument("--family")
    scenarios.add_argument("--split-value", default="test")
    scenarios.add_argument("--limit", type=int, default=20)

    create = subparsers.add_parser("create")
    create.add_argument("--scenario-id", required=True)
    create.add_argument("--mode", default="human_therapist_vs_patient")
    create.add_argument("--session-id")
    create.add_argument("--parent-session-id")

    get = subparsers.add_parser("get")
    get.add_argument("--session-id", required=True)

    turn = subparsers.add_parser("turn")
    turn.add_argument("--session-id", required=True)
    turn.add_argument("--text", required=True)
    turn.add_argument("--idempotency-key")

    for name in ("pause", "resume", "complete", "review"):
        command = subparsers.add_parser(name)
        command.add_argument("--session-id", required=True)

    terminate = subparsers.add_parser("terminate")
    terminate.add_argument("--session-id", required=True)
    terminate.add_argument("--reason", required=True)

    export = subparsers.add_parser("export")
    export.add_argument("--session-id", required=True)
    export.add_argument("--scope", choices=["public", "instructor", "researcher"])
    reviewer_export = subparsers.add_parser("export-reviewer-runtime")
    reviewer_export.add_argument("--session-id", required=True)
    return parser.parse_args()


def headers(args: argparse.Namespace) -> dict[str, str]:
    result = {"X-Simulation-Role": args.role}
    if args.token:
        result["Authorization"] = f"Bearer {args.token}"
    return result


def request_json(
    args: argparse.Namespace,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_headers = headers(args)
    request_headers.update(extra_headers or {})
    with httpx.Client(base_url=args.base_url, timeout=660) as client:
        response = client.request(
            method, path, json=body, headers=request_headers, params=params
        )
    response.raise_for_status()
    return response.json()


def main() -> int:
    args = parse_args()
    if args.command == "serve":
        import uvicorn

        settings = SimulationAPISettings(
            database_path=Path(args.database),
            model_config_path=Path(args.model_config),
            api_token=args.token,
            initialize_llm_engine=not args.no_llm,
        )
        uvicorn.run(create_app(settings), host=args.host, port=args.port)
        return 0

    if args.command == "list-scenarios":
        result = request_json(
            args,
            "GET",
            "/scenarios",
            params={
                "family": args.family,
                "split_value": args.split_value,
                "limit": args.limit,
            },
        )
    elif args.command == "create":
        result = request_json(
            args,
            "POST",
            "/sessions",
            body={
                "scenario_id": args.scenario_id,
                "mode": args.mode,
                "session_id": args.session_id,
                "parent_session_id": args.parent_session_id,
            },
        )
    elif args.command == "get":
        result = request_json(args, "GET", f"/sessions/{args.session_id}")
    elif args.command == "turn":
        result = request_json(
            args,
            "POST",
            f"/sessions/{args.session_id}/turns",
            body={"text": args.text, "actor": args.role},
            extra_headers={
                "Idempotency-Key": args.idempotency_key or f"cli-{uuid.uuid4().hex}"
            },
        )
    elif args.command in {"pause", "resume", "complete", "review"}:
        result = request_json(
            args, "POST", f"/sessions/{args.session_id}/{args.command}"
        )
    elif args.command == "terminate":
        result = request_json(
            args,
            "POST",
            f"/sessions/{args.session_id}/terminate",
            body={"reason": args.reason},
        )
    elif args.command == "export":
        result = request_json(
            args,
            "GET",
            f"/sessions/{args.session_id}/export",
            params={"scope": args.scope} if args.scope else None,
        )
    elif args.command == "export-reviewer-runtime":
        result = request_json(
            args,
            "GET",
            f"/sessions/{args.session_id}/reviewer-runtime-report",
        )
    else:
        raise AssertionError(args.command)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
