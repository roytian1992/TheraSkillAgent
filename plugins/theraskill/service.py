"""HTTP adapters for the existing TheraSkill internal services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import httpx

from theraskill_agent.config import cfg_get, load_config_readonly

from .contracts import TheraSkillRequest


@dataclass(frozen=True)
class ServiceConfig:
    name: str
    base_url: str
    path: str
    timeout_seconds: float = 30.0
    token: str = ""


_DEFAULTS = {
    "support": ("http://127.0.0.1:8878", "/sessions"),
    "simulation": ("http://127.0.0.1:8876", "/sessions"),
    "response_review": ("http://127.0.0.1:8879", "/reviews/prepare"),
    "contribution": ("http://127.0.0.1:8881", "/v1/reviews/prepare"),
    "knowledge_search": ("", ""),
    "extraction": ("http://127.0.0.1:8891", "/v1/extraction/jobs"),
}


def service_config(name: str, config: Mapping[str, Any] | None = None) -> ServiceConfig | None:
    """Read ``platforms.theraskill.services`` from the project config."""
    if name not in _DEFAULTS:
        return None
    cfg = config if config is not None else load_config_readonly()
    default_url, default_path = _DEFAULTS[name]
    raw = cfg_get(cfg, "platforms", "theraskill", "services", name, default={})
    if not isinstance(raw, Mapping):
        raw = {}
    base_url = str(raw.get("base_url") or default_url).strip().rstrip("/")
    path = str(raw.get("path") or default_path).strip()
    if not base_url:
        return None
    try:
        timeout = float(raw.get("timeout_seconds", 30.0))
    except (TypeError, ValueError):
        timeout = 30.0
    return ServiceConfig(
        name=name,
        base_url=base_url,
        path=path if path.startswith("/") else f"/{path}",
        timeout_seconds=max(0.1, min(timeout, 300.0)),
        token=str(raw.get("token") or "").strip(),
    )


def call_service(config: ServiceConfig, request: TheraSkillRequest) -> tuple[int, Any]:
    """Translate a typed TheraSkillAgent action into a domain API call."""
    params = dict(request.parameters)
    operation = str(params.pop("operation", ""))
    method = "POST"
    path = config.path
    query_params: dict[str, Any] = {}
    payload: dict[str, Any] | None = params
    session_id = request.session_id
    headers = {
        "X-TheraSkill-Request-ID": request.request_id,
        "X-TheraSkill-Action": request.action,
        "X-TheraSkill-Profile": request.profile,
    }
    subject_id = request.actor if request.actor and request.actor != "agent" else "local-user"

    if request.action == "support":
        headers.update({"X-Support-User": subject_id, "X-Support-Role": "user"})
        if operation == "create_session":
            path, payload = "/sessions", {"session_id": session_id or None}
        elif operation == "get_session":
            method, path, payload = "GET", f"/sessions/{session_id}", None
        elif operation == "submit_message":
            path, payload = f"/sessions/{session_id}/messages", {"text": params.get("text")}
        elif operation == "retry_response":
            path, payload = f"/sessions/{session_id}/retry", {}
        elif operation == "complete_session":
            path, payload = f"/sessions/{session_id}/complete", {}
        elif operation == "update_plan":
            method, path, payload = "PATCH", f"/sessions/{session_id}/plan", {
                key: value for key, value in {
                    "goals": params.get("goals"),
                    "action_items": params.get("action_items"),
                    "next_check_in_at": params.get("next_check_in_at"),
                }.items() if value is not None
            }
        elif operation == "safety_action":
            path, payload = f"/sessions/{session_id}/safety/action", {"action": params.get("action")}
        elif operation == "safety_check_in":
            path, payload = f"/sessions/{session_id}/safety/check-in", {
                "safe_now": bool(params.get("safe_now")), "note": params.get("note")
            }
        elif operation == "list_safety_resources":
            method, path, payload = "GET", "/safety/resources", None
            query_params = {"region": params.get("region", "default")}
    elif request.action == "simulation":
        caller_role = str(
            params.get("actor")
            or ("researcher" if operation in {"run_dual_cycle", "create_review_session"} else "human_therapist")
        )
        headers.update({"X-Simulation-User": subject_id, "X-Simulation-Role": caller_role})
        if operation == "list_scenarios":
            method, path, payload = "GET", "/scenarios", None
            query_params = {
                key: value for key, value in {
                    "search": params.get("query"), "language": params.get("language"),
                    "limit": params.get("limit"),
                }.items() if value is not None
            }
        elif operation == "create_session":
            path = "/sessions"
            payload = {
                key: value for key, value in {
                    "scenario_id": params.get("scenario_id"), "mode": params.get("mode"),
                    "language": params.get("language"), "session_id": session_id or None,
                }.items() if value is not None
            }
        elif operation == "create_review_session":
            path = "/sessions"
            payload = {
                key: value for key, value in {
                    "scenario_id": params.get("scenario_id"),
                    "mode": "review_only",
                    "language": params.get("language"),
                    "source_session_id": params.get("source_session_id"),
                    "session_id": session_id or None,
                }.items() if value is not None
            }
        elif operation == "get_session":
            method, path, payload = "GET", f"/sessions/{session_id}", None
        elif operation == "submit_turn":
            path = f"/sessions/{session_id}/turns"
            payload = {"text": params.get("text"), "actor": caller_role}
        elif operation == "complete_session":
            path, payload = f"/sessions/{session_id}/complete", {}
        elif operation == "generate_review":
            path, payload = f"/sessions/{session_id}/review", {}
        elif operation == "run_dual_cycle":
            path = f"/sessions/{session_id}/dual-cycle"
            payload = {
                "initial_therapist_utterance": params.get("initial_therapist_utterance")
            }
    elif request.action == "response_review":
        headers.update({"X-Expert-User": subject_id, "X-Expert-Role": "expert"})
        contribution_id = str(params.get("contribution_id") or "")
        if operation == "prepare_queue":
            path, payload = "/reviews/prepare", {}
        elif operation == "list_queue":
            method, path, payload = "GET", "/reviews/queue", None
            query_params = {"limit": params.get("limit", 50)}
        elif operation == "get_review":
            method, path, payload = "GET", f"/reviews/{contribution_id}", None
        elif operation == "submit_decision":
            path, payload = f"/reviews/{contribution_id}/decision", dict(params.get("decision") or {})
    elif request.action == "contribution":
        contribution_type = str(params.get("contribution_type") or "response")
        role = "expert_reviewer" if contribution_type == "knowledge" else "user"
        headers.update({"X-Contribution-Subject": subject_id, "X-Contribution-Role": role})
        if operation == "capture":
            capture_paths = {
                "response": "/v1/feedback/response", "session": "/v1/feedback/session",
                "annotation": "/v1/annotations/turn", "knowledge": "/v1/contributions/knowledge",
                "correction": "/v1/contributions/correction",
            }
            path = capture_paths[contribution_type]
            payload = {
                "contribution_type": {
                    "response": "user_feedback", "session": "session_feedback",
                    "annotation": "turn_annotation", "knowledge": "knowledge_proposal",
                    "correction": "correction_proposal",
                }[contribution_type],
                "origin": params.get("origin"), "target": params.get("target"),
                "payload": params.get("payload") or {},
                "consent_snapshot_id": params.get("consent_snapshot_id"),
                "context": params.get("context"), "provenance": params.get("provenance") or {},
            }
        elif operation == "withdraw":
            method, path, payload = "DELETE", f"/v1/contributions/{params.get('contribution_id')}", None
        elif operation == "propose_memory":
            path = "/v1/memory"
            payload = {
                key: value for key, value in {
                    "memory_type": params.get("memory_type"), "content": params.get("content"),
                    "sensitivity_level": params.get("sensitivity_level", "standard"),
                    "ttl_days": params.get("ttl_days", 90), "source_session_id": session_id or None,
                }.items() if value is not None
            }
    elif request.action == "extraction":
        headers.update({"X-Extraction-User": subject_id, "X-Extraction-Role": str(params.get("role") or "agent")})
        if operation == "start":
            path, payload = "/v1/extraction/jobs", {
                "plan": params.get("plan") or {},
                "limit": params.get("limit"),
            }
        elif operation == "status":
            method, path, payload = "GET", f"/v1/extraction/jobs/{params.get('job_id')}", None
        elif operation == "preview":
            method, path, payload = "GET", f"/v1/extraction/jobs/{params.get('job_id')}/preview", None
        elif operation == "cancel":
            method, path, payload = "POST", f"/v1/extraction/jobs/{params.get('job_id')}/cancel", {}
        elif operation == "handoff":
            method, path, payload = "POST", f"/v1/extraction/jobs/{params.get('job_id')}/handoff", {}
    if request.idempotency_key:
        headers["Idempotency-Key"] = request.idempotency_key
    if request.approval_id:
        headers["X-TheraSkill-Approval-ID"] = request.approval_id
    if request.run_id:
        headers["X-TheraSkill-Run-ID"] = request.run_id
    headers["X-TheraSkill-Actor"] = request.actor
    headers["X-TheraSkill-Scope"] = request.scope
    if config.token:
        headers["Authorization"] = f"Bearer {config.token}"
    with httpx.Client(timeout=config.timeout_seconds) as client:
        response = client.request(
            method,
            f"{config.base_url}{path}",
            json=payload,
            params=query_params,
            headers=headers,
        )
    try:
        body: Any = response.json()
    except ValueError:
        body = {"text": response.text}
    return response.status_code, body
