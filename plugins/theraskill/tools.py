"""Native TheraSkillAgent tools for the platform boundary.

The first slice owns request normalization, runtime release discovery, and a
stable result envelope. Domain service calls are kept behind this boundary so
the next migration phase can replace the current FastAPI/CLI entry points
without changing the stable tool schema.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from .artifacts import ArtifactStore, ArtifactStoreError
from .audit import record as record_audit
from .contracts import TheraSkillRequest, TheraSkillResult
from .service import call_service, service_config
from .retrieval import RetrievalError, search_knowledge
from .extraction_plan import ExtractionPlanError, plan_with_active_model, suggest_schema, validate_plan
from domains.theraskill.provider_config import ProviderConfigError, public_provider_info
from theraskill_agent.workflows import SkillWorkflow, WorkflowStore


ACTION_OPERATIONS = {
    "support": {
        "create_session", "get_session", "submit_message", "retry_response", "complete_session",
        "update_plan", "safety_action", "safety_check_in", "list_safety_resources",
    },
    "simulation": {
        "list_scenarios", "create_session", "create_review_session", "get_session", "submit_turn", "complete_session", "generate_review", "run_dual_cycle",
    },
    "response_review": {
        "list_queue", "get_review", "submit_decision",
    },
    "contribution": {"capture", "withdraw", "propose_memory"},
    "extraction": {"design", "validate_plan", "start", "status", "preview", "cancel", "handoff"},
    "transcript": {"inspect", "analyze", "review", "package"},
}

APP_TARGETS = {
    "support": ("theraskill.support", "TheraSkill Support", "/theraskill/support", "session"),
    "simulation": ("theraskill.simulation", "TheraSkill Simulation", "/theraskill/simulation", "session"),
    "response_review": ("theraskill.expert", "TheraSkill Expert Studio", "/theraskill/expert", "queue"),
    "contribution": ("theraskill.expert", "TheraSkill Expert Studio", "/theraskill/expert", "queue"),
    "extraction": ("theraskill.expert", "TheraSkill Expert Studio", "/theraskill/expert", "governance"),
}

APPROVAL_OPERATIONS = {
    ("response_review", "submit_decision"),
}

REQUIRED_OPERATION_FIELDS = {
    ("support", "get_session"): ("session_id",),
    ("support", "submit_message"): ("session_id", "text"),
    ("support", "retry_response"): ("session_id",),
    ("support", "complete_session"): ("session_id",),
    ("support", "update_plan"): ("session_id",),
    ("support", "safety_action"): ("session_id", "action"),
    ("support", "safety_check_in"): ("session_id", "safe_now"),
    ("simulation", "create_session"): ("scenario_id",),
    ("simulation", "create_review_session"): ("source_session_id", "scenario_id"),
    ("simulation", "get_session"): ("session_id",),
    ("simulation", "submit_turn"): ("session_id", "actor", "text"),
    ("simulation", "complete_session"): ("session_id",),
    ("simulation", "generate_review"): ("session_id",),
    ("simulation", "run_dual_cycle"): ("session_id",),
    ("response_review", "get_review"): ("contribution_id",),
    ("response_review", "submit_decision"): ("contribution_id", "decision"),
    ("contribution", "capture"): (
        "contribution_type", "consent_snapshot_id", "origin", "target",
    ),
    ("contribution", "withdraw"): ("contribution_id",),
    ("contribution", "propose_memory"): ("memory_type", "content"),
    ("extraction", "design"): ("goal",),
    ("extraction", "validate_plan"): ("plan",),
    ("extraction", "start"): ("plan",),
    ("extraction", "status"): ("job_id",),
    ("extraction", "preview"): ("job_id",),
    ("extraction", "cancel"): ("job_id",),
    ("extraction", "handoff"): ("job_id",),
    ("transcript", "inspect"): ("source_path",),
    ("transcript", "analyze"): ("source_path",),
    ("transcript", "review"): ("source_path",),
    ("transcript", "package"): ("source_path", "output_dir"),
}


def _request(action: str, args: Any, runtime: dict[str, Any] | None = None) -> TheraSkillRequest:
    payload = dict(args) if isinstance(args, dict) else {}
    payload["action"] = action
    runtime = runtime or {}
    stable_user_id = str(runtime.get("user_id_alt") or runtime.get("user_id") or "").strip()
    if stable_user_id:
        payload["actor"] = stable_user_id
    request = TheraSkillRequest.from_mapping(payload)
    record_audit(request, "received")
    return request


def _failure(request: TheraSkillRequest, code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    record_audit(request, "failed", error=f"{code}: {message}")
    return TheraSkillResult.failure(
        request.action,
        code,
        message,
        request_id=request.request_id,
        details=details,
    ).to_dict()


def _handle_context(args: dict[str, Any], **runtime: Any) -> dict[str, Any]:
    request = _request("context", args, runtime)
    from theraskill_agent.resources import active_index
    selected = active_index() if request.release_id == "current" else None
    if selected:
        from theraskill_agent.platform import THERASKILL_PLATFORM
        result = TheraSkillResult.success(request.action, {"index": selected[1], **THERASKILL_PLATFORM.runtime_snapshot()}, request_id=request.request_id).to_dict()
        record_audit(request, "completed")
        return result
    try:
        manifest = ArtifactStore().load_manifest(request.release_id)
    except ArtifactStoreError as exc:
        return _failure(request, "artifact_release_unavailable", str(exc))
    try:
        provider_info = {
            "llm": public_provider_info("llm"),
            "embedding": public_provider_info("embedding"),
        }
    except ProviderConfigError as exc:
        return _failure(request, "provider_config_invalid", str(exc))
    result = TheraSkillResult.success(
        request.action,
        {
            "profile": request.profile,
            "domain": request.domain,
            "scope": request.scope,
            "release_id": manifest.release_id,
            "capabilities": [
                "knowledge_search",
                "support",
                "simulation",
                "response_review",
                "contribution",
                "extraction",
                "transcript",
            ],
            "providers": provider_info,
        },
        request_id=request.request_id,
        provenance={"artifact_release_id": manifest.release_id},
    ).to_dict()
    record_audit(request, "completed")
    return result


def _handle_knowledge_search(args: dict[str, Any], **runtime: Any) -> dict[str, Any]:
    request = _request("knowledge_search", args, runtime)
    if not request.query:
        return _failure(request, "query_required", "knowledge search requires a non-empty query")
    if request.release_id == "current":
        from .retrieval import search_managed_knowledge
        try:
            managed = search_managed_knowledge(request.query, skill=str(request.parameters.get("skill") or "emotional-support"), limit=int(request.parameters.get("limit", 8)))
            if managed is not None:
                record_audit(request, "completed")
                return TheraSkillResult.success(request.action, managed, request_id=request.request_id,
                    provenance={"index_id": managed["release_id"], "knowledge_sha256": managed["knowledge_sha256"]},
                    warnings=tuple(managed.get("warnings", []))).to_dict()
        except (OSError, ValueError, RuntimeError) as exc:
            return _failure(request, "index_unavailable", str(exc))
    try:
        manifest = ArtifactStore().load_manifest(request.release_id)
    except ArtifactStoreError as exc:
        return _failure(request, "artifact_release_unavailable", str(exc))
    try:
        data = search_knowledge(
            request.query,
            skill=str(request.parameters.get("skill") or "emotional-support"),
            release_id=request.release_id,
            limit=int(request.parameters.get("limit", 8)),
        )
        retrieved_ids = [item.get("retrieval_id") for item in data["matches"] if item.get("retrieval_id")]
        result = TheraSkillResult.success(
            request.action,
            data,
            request_id=request.request_id,
            provenance={
                "artifact_release_id": manifest.release_id,
                "retrieval_mode": data.get("retrieval_mode", "unknown"),
                "retrieved_unit_ids": retrieved_ids,
                "model_version": manifest.model_version,
                "prompt_version": manifest.prompt_version,
                "retrieval_version": manifest.retrieval_version,
            },
            warnings=tuple(data.get("warnings") or ()),
        ).to_dict()
        record_audit(request, "completed")
        return result
    except RetrievalError:
        pass
    config = service_config("knowledge_search")
    if config is None:
        return _failure(request, "retrieval_service_unavailable", "knowledge search service is not configured", {"release_id": manifest.release_id})
    return _call_configured_service(config, request, manifest.release_id)


_EXTRACTION_DESIGN_ROLES = {"agent", "researcher", "knowledge_curator", "expert", "admin"}
_EXTRACTION_MUTATION_ROLES = {"researcher", "knowledge_curator", "admin"}


def _extraction_role(args: dict[str, Any], runtime: dict[str, Any]) -> str:
    parameters = args.get("parameters") if isinstance(args, dict) else {}
    parameters = parameters if isinstance(parameters, dict) else {}
    return str(
        parameters.get("role")
        or runtime.get("role")
        or runtime.get("user_role")
        or "agent"
    ).strip().lower()


def _handle_extraction(args: dict[str, Any], **runtime: Any) -> dict[str, Any]:
    request = _request("extraction", args, runtime)
    operation = str(request.parameters.get("operation") or "").strip()
    if operation not in ACTION_OPERATIONS["extraction"]:
        return _failure(request, "unsupported_operation", "extraction requires one of: design, validate_plan, start, status, preview, cancel")
    role = _extraction_role(args, runtime)
    # Propagate the resolved runtime role to the service adapter even when the
    # caller supplied it through runtime metadata rather than tool args.
    request.parameters["role"] = role
    if operation in {"design", "validate_plan"} and role not in _EXTRACTION_DESIGN_ROLES:
        return _failure(request, "forbidden_role", "dynamic extraction planning requires an approved research or governance role")
    if operation in {"start", "cancel", "handoff"} and role not in _EXTRACTION_MUTATION_ROLES:
        return _failure(request, "forbidden_role", "starting or cancelling extraction requires researcher, knowledge_curator or admin role")
    missing = [field for field in REQUIRED_OPERATION_FIELDS[("extraction", operation)] if request.parameters.get(field) in (None, "", {})]
    if missing:
        return _failure(request, "invalid_request", f"{operation} requires: {', '.join(missing)}")
    if operation == "design":
        goal = str(request.parameters.get("goal") or "")
        language = str(request.parameters.get("language") or "auto")
        planner = str(request.parameters.get("planner") or "heuristic").strip().lower()
        if planner == "model":
            try:
                normalized = plan_with_active_model(
                    goal,
                    request.parameters.get("sample_records") or [],
                    source=request.parameters.get("source") or {},
                    language=language,
                )
            except Exception as exc:  # noqa: BLE001 - return typed tool failure
                return _failure(request, "planner_failed", f"model planner failed: {exc}")
        else:
            schema = request.parameters.get("target_schema") or suggest_schema(goal, language)
            plan = {
                "goal": goal,
                "target_schema": schema,
                "source": request.parameters.get("source") or {},
                "unit_definition": request.parameters.get("unit_definition") or "one source record",
                "language": language,
                "model_profile": request.parameters.get("model_profile") or "default",
                "status": "draft",
            }
            try:
                normalized = validate_plan(plan)
            except ExtractionPlanError as exc:
                return _failure(request, "invalid_plan", str(exc))
        record_audit(request, "completed")
        workflow_id = request.workflow_id or str(request.parameters.get("workflow_id") or normalized.get("plan_id") or request.request_id)
        interaction = {
            "kind": "form",
            "workflow_id": workflow_id,
            "title": "Review extraction plan",
            "prompt": "Review the proposed extraction goal, unit definition, source and target schema. Edit any field, then validate before starting the job.",
            "schema": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "unit_definition": {"type": "string"},
                    "language": {"type": "string", "enum": ["auto", "zh", "en", "mixed"]},
                    "target_schema": {"type": "object"},
                    "quality_rules": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["goal", "target_schema"],
            },
            "value": normalized,
            "actions": ["edit", "validate", "cancel"],
            "resume": {
                "operation": "validate_plan",
                "workflow_id": workflow_id,
                "plan_id": normalized.get("plan_id"),
            },
        }
        workflow = SkillWorkflow(
            workflow_id=workflow_id,
            kind="knowledge_extraction",
            session_id=request.session_id,
            run_id=request.run_id,
            state={"plan": normalized, "actor_role": role},
        )
        workflow.wait_for("needs_input", interaction)
        WorkflowStore().save(workflow)
        return TheraSkillResult.success(
            request.action,
            {"operation": operation, "plan": normalized, "requires_review": True},
            request_id=request.request_id,
            provenance={"plan_version": normalized.get("schema_version"), "actor_role": role, "workflow_id": workflow_id},
            next_action="review_extraction_plan",
            interaction=interaction,
        ).to_dict()
    if operation == "validate_plan":
        try:
            normalized = validate_plan(request.parameters.get("plan") or {})
        except ExtractionPlanError as exc:
            return _failure(request, "invalid_plan", str(exc))
        record_audit(request, "completed")
        workflow_id = request.workflow_id or str(request.parameters.get("workflow_id") or normalized.get("plan_id") or request.request_id)
        interaction = {
            "kind": "approval",
            "workflow_id": workflow_id,
            "title": "Approve extraction start",
            "prompt": "The extraction plan passed local validation. Approve starting the asynchronous extraction job.",
            "actions": ["approve", "cancel"],
            "resume": {"operation": "start", "workflow_id": workflow_id, "plan": normalized},
        }
        existing = WorkflowStore().get(workflow_id) or SkillWorkflow(
            workflow_id=workflow_id,
            kind="knowledge_extraction",
            session_id=request.session_id,
            run_id=request.run_id,
        )
        existing.state = {"plan": normalized, "actor_role": role}
        existing.wait_for("needs_approval", interaction)
        WorkflowStore().save(existing)
        return TheraSkillResult.success(
            request.action,
            {"operation": operation, "plan": normalized, "valid": True, "ready_for_approval": True},
            request_id=request.request_id,
            provenance={"plan_version": normalized.get("schema_version"), "actor_role": role, "workflow_id": workflow_id},
            next_action="approve_extraction_start",
            interaction=interaction,
        ).to_dict()
    # Long-running extraction is handled by the dedicated worker service. The
    # platform tool remains a typed, auditable boundary and never writes a
    # production release directly.
    if operation in {"start", "handoff"} and not request.approval_id:
        return _failure(request, "approval_required", f"{operation} requires approval_id")
    config = service_config("extraction")
    if config is None:
        return _failure(request, "service_not_configured", "dynamic extraction service is not configured")
    result = _call_configured_service(config, request)
    if operation in {"start", "status", "preview", "handoff", "cancel"}:
        workflow_id = request.workflow_id or str(request.parameters.get("workflow_id") or request.parameters.get("job_id") or request.request_id)
        workflow = WorkflowStore().get(workflow_id) or SkillWorkflow(
            workflow_id=workflow_id,
            kind="knowledge_extraction",
            session_id=request.session_id,
            run_id=request.run_id,
        )
        upstream = result.get("data", {}).get("upstream", {}) if isinstance(result, dict) else {}
        job_status = str(upstream.get("status") or "") if isinstance(upstream, dict) else ""
        if operation == "start":
            workflow.state.update({"plan": request.parameters.get("plan"), "job_id": upstream.get("job_id")})
            workflow.wait_for("pending", {"kind": "progress", "workflow_id": workflow_id, "title": "Extraction in progress", "prompt": "The extraction job is running. Poll status or continue later.", "actions": ["status", "cancel"]})
        elif operation == "status" and job_status in {"completed", "partial"}:
            workflow.state.update({"job_id": request.parameters.get("job_id"), "last_status": upstream})
            workflow.wait_for("needs_input", {"kind": "review", "workflow_id": workflow_id, "title": "Review extraction preview", "prompt": "Inspect the candidate preview before handing it to governance.", "actions": ["preview", "handoff", "cancel"], "resume": {"operation": "preview", "workflow_id": workflow_id, "job_id": request.parameters.get("job_id")}})
        elif operation == "handoff" and result.get("status") == "completed":
            workflow.finish(state={"job_id": request.parameters.get("job_id"), "handoff": upstream})
        elif operation == "cancel" and result.get("status") == "completed":
            workflow.finish(status="cancelled", state={"job_id": request.parameters.get("job_id")})
        WorkflowStore().save(workflow)
        if isinstance(result, dict):
            result.setdefault("provenance", {})["workflow_id"] = workflow_id
            result["workflow"] = workflow.to_dict()
    return result


def _handle_transcript(args: dict[str, Any], **runtime: Any) -> dict[str, Any]:
    """Normalize and screen a dialogue file without overwriting its source."""

    request = _request("transcript", args, runtime)
    operation = str(request.parameters.get("operation") or "").strip()
    if operation not in ACTION_OPERATIONS["transcript"]:
        return _failure(request, "unsupported_operation", "transcript requires inspect, analyze, review or package")
    missing = [field for field in REQUIRED_OPERATION_FIELDS[("transcript", operation)] if request.parameters.get(field) in (None, "", {})]
    if missing:
        return _failure(request, "invalid_request", f"{operation} requires: {', '.join(missing)}")
    try:
        from theraskill_agent.transcript import analyze_transcript, build_bundle, load_transcript

        source_path = str(request.parameters.get("source_path"))
        overrides = request.parameters.get("speaker_role_overrides") or {}
        document, raw_text = load_transcript(
            source_path,
            language=str(request.parameters.get("language") or "auto"),
            speaker_role_overrides=overrides if isinstance(overrides, dict) else None,
        )
        workflow_id = request.workflow_id or str(request.parameters.get("workflow_id") or document.document_id)
        store = WorkflowStore()
        workflow = store.get(workflow_id) or SkillWorkflow(
            workflow_id=workflow_id,
            kind="transcript_review",
            session_id=request.session_id,
            run_id=request.run_id,
        )
        workflow.state.update({"source_path": source_path, "document_id": document.document_id, "source_sha256": document.raw_text_sha256})
        sample = [turn.to_dict() for turn in document.turns[:5]]
        if operation == "inspect":
            interaction = {
                "kind": "form",
                "workflow_id": workflow_id,
                "title": "Review transcript interpretation",
                "prompt": "Confirm the detected language and speaker-role mapping before analysis. Raw text remains preserved.",
                "schema": {
                    "type": "object",
                    "properties": {
                        "language": {"type": "string", "enum": ["auto", "zh", "en", "mixed"]},
                        "speaker_role_overrides": {"type": "object", "additionalProperties": {"type": "string"}},
                    },
                },
                "value": {"language": document.language, "speaker_labels": sorted({turn.speaker_label for turn in document.turns}), "sample_turns": sample},
                "actions": ["confirm", "edit", "cancel"],
                "resume": {"operation": "analyze", "workflow_id": workflow_id, "source_path": source_path},
            }
            workflow.wait_for("needs_input", interaction)
            store.save(workflow)
            return TheraSkillResult.success(
                request.action,
                {"operation": operation, "document": document.to_dict(), "sample_turns": sample, "workflow": workflow.to_dict()},
                request_id=request.request_id,
                provenance={"workflow_id": workflow_id, "source_sha256": document.raw_text_sha256},
                next_action="review_transcript_interpretation",
                interaction=interaction,
            ).to_dict()
        analysis = analyze_transcript(document)
        if operation == "analyze":
            workflow.state["analysis"] = analysis
            interaction = {
                "kind": "review",
                "workflow_id": workflow_id,
                "title": "Run transcript skill reviews",
                "prompt": "The deterministic transcript analysis is ready. Run the recommended risk and therapeutic-process skills, preserving turn-level evidence.",
                "actions": ["run_reviews", "package", "cancel"],
                "resume": {"operation": "review", "workflow_id": workflow_id, "source_path": source_path, "skills": analysis["recommended_skills"], "mode": "plan"},
            }
            workflow.wait_for("needs_input", interaction)
            store.save(workflow)
            return TheraSkillResult.success(
                request.action,
                {"operation": operation, "document": document.to_dict(), "analysis": analysis, "requires_model_review": True, "workflow": workflow.to_dict()},
                request_id=request.request_id,
                provenance={"workflow_id": workflow_id, "source_sha256": document.raw_text_sha256},
                next_action="run_transcript_skill_reviews",
                interaction=interaction,
            ).to_dict()
        if operation == "review":
            from theraskill_agent.reviews import build_review_plan, run_skill_reviews

            skills = request.parameters.get("skills")
            skills = skills if isinstance(skills, list) else analysis.get("recommended_skills")
            mode = str(request.parameters.get("mode") or "plan").strip().lower()
            max_turns = request.parameters.get("max_turns")
            max_turns = int(max_turns) if max_turns not in (None, "") else None
            if mode == "plan":
                reviews = {"mode": "plan", "document_id": document.document_id, "plans": build_review_plan(document, analysis, skills=skills, max_turns=max_turns)}
                interaction = {
                    "kind": "approval",
                    "workflow_id": workflow_id,
                    "title": "Approve transcript model reviews",
                    "prompt": "Review the selected skills and evidence-preserving prompts before any provider call.",
                    "actions": ["run_reviews", "edit", "cancel"],
                    "resume": {"operation": "review", "workflow_id": workflow_id, "source_path": source_path, "skills": skills, "mode": "model"},
                }
                workflow.state["review_plan"] = reviews
                workflow.wait_for("needs_approval", interaction)
            elif mode == "model":
                reviews = run_skill_reviews(
                    document,
                    analysis,
                    skills=skills,
                    provider_profile=request.parameters.get("provider_profile"),
                    max_turns=max_turns,
                )
                workflow.state["reviews"] = reviews
                workflow.finish(state=workflow.state)
            else:
                return _failure(request, "invalid_request", "transcript.review mode must be plan or model")
            store.save(workflow)
            payload = {"operation": operation, "document": document.to_dict(), "analysis": analysis, "reviews": reviews, "workflow": workflow.to_dict()}
            return TheraSkillResult.success(
                request.action,
                payload,
                request_id=request.request_id,
                provenance={"workflow_id": workflow_id, "source_sha256": document.raw_text_sha256, "review_mode": mode},
                next_action="review_transcript_results" if mode == "model" else "approve_transcript_reviews",
                interaction=workflow.interaction or None,
            ).to_dict()
        artifacts = build_bundle(
            document,
            analysis,
            str(request.parameters.get("output_dir")),
            include_raw=not bool(request.parameters.get("exclude_raw")),
            raw_text=raw_text,
            reviews=workflow.state.get("reviews") or workflow.state.get("review_plan"),
        )
        workflow.finish(state={**workflow.state, "artifacts": artifacts})
        store.save(workflow)
        return TheraSkillResult.success(
            request.action,
            {"operation": operation, "document": document.to_dict(), "analysis": analysis, "artifacts": artifacts, "workflow": workflow.to_dict()},
            request_id=request.request_id,
            provenance={"workflow_id": workflow_id, "source_sha256": document.raw_text_sha256},
            next_action="open_transcript_bundle",
        ).to_dict()
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        return _failure(request, "transcript_processing_failed", str(exc))


def _find_identifier(body: Any, key: str) -> str:
    if isinstance(body, dict):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        for nested_key in ("session", "public_view", "data", "upstream"):
            nested = body.get(nested_key)
            found = _find_identifier(nested, key)
            if found:
                return found
    return ""


def _result_view(action: str, request: TheraSkillRequest, body: Any) -> dict[str, Any]:
    app_id, label, base_path, default_view = APP_TARGETS[action]
    context: dict[str, str] = {}
    session_id = request.session_id or _find_identifier(body, "session_id")
    if action in {"support", "simulation"} and session_id:
        context["session_id"] = session_id
    contribution_id = _find_identifier(body, "contribution_id")
    if action in {"response_review", "contribution"} and contribution_id:
        context["contribution_id"] = contribution_id
    job_id = request.parameters.get("job_id") or _find_identifier(body, "job_id")
    if action == "extraction" and job_id:
        context["job_id"] = str(job_id)
    query = {"view": default_view, **context}
    path = f"{base_path}?{urlencode(query)}"
    return {
        "app_id": app_id,
        "label": label,
        "view": default_view,
        "context": context,
        "path": path,
        "markdown": f"[{label}]({path})",
    }


def _call_configured_service(config, request: TheraSkillRequest, release_id: str | None = None) -> dict[str, Any]:
    try:
        status_code, body = call_service(config, request)
    except Exception as exc:
        return _failure(request, "service_unreachable", str(exc), {"service": config.name, "base_url": config.base_url})
    if status_code >= 400:
        return _failure(request, "service_error", f"{config.name} returned HTTP {status_code}", {"response": body})
    upstream_provenance = body.get("provenance", {}) if isinstance(body, dict) else {}
    if not isinstance(upstream_provenance, dict):
        upstream_provenance = {}
    retrieved_ids = upstream_provenance.get("retrieved_unit_ids")
    if retrieved_ids is None and isinstance(body, dict):
        retrieved_ids = body.get("retrieved_unit_ids") or body.get("retrieved_ids") or []
    provenance = {
        "service": config.name,
        "service_root": config.base_url,
        "artifact_release_id": release_id or request.release_id,
        "release_id": release_id or request.release_id,
        "retrieved_unit_ids": list(retrieved_ids or []),
        **upstream_provenance,
    }
    app_view = (
        _result_view(request.action, request, body)
        if request.action in APP_TARGETS
        else {}
    )
    result = TheraSkillResult.success(
        request.action,
        {"upstream": body},
        request_id=request.request_id,
        provenance=provenance,
        next_action="open_app" if app_view else None,
        view=app_view,
    ).to_dict()
    record_audit(request, "completed")
    return result


def _service_action(action: str, args: dict[str, Any], runtime: dict[str, Any] | None = None) -> dict[str, Any]:
    request = _request(action, args, runtime)
    operation = str(request.parameters.get("operation") or "").strip()
    if request.parameters.get("requires_approval") and not request.approval_id:
        return _failure(request, "approval_required", "this action requires approval_id")
    if operation not in ACTION_OPERATIONS[action]:
        return _failure(
            request,
            "unsupported_operation",
            f"{action} requires one of: {', '.join(sorted(ACTION_OPERATIONS[action]))}",
        )
    missing = []
    for field in REQUIRED_OPERATION_FIELDS.get((action, operation), ()):
        value = request.session_id if field == "session_id" else request.parameters.get(field)
        if value is None or value == "" or value == {}:
            missing.append(field)
    if missing:
        return _failure(
            request,
            "invalid_request",
            f"{operation} requires: {', '.join(missing)}",
        )
    if (
        (action, operation) in APPROVAL_OPERATIONS
        or request.parameters.get("requires_approval")
    ) and not request.approval_id:
        return _failure(request, "approval_required", f"{operation} requires approval_id")
    config = service_config(action)
    if config is None:
        return _failure(request, "service_not_configured", f"{action} service is not configured")
    return _call_configured_service(config, request)


THERASKILL_CONTEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "profile": {"type": "string", "description": "TheraSkill runtime profile."},
        "scope": {"type": "string", "description": "Actor scope for the context request."},
        "release_id": {"type": "string", "description": "Installed release id or current."},
        "session_id": {"type": "string"},
        "approval_id": {"type": "string", "description": "Approval record for a high-risk action."},
    },
}

THERASKILL_KNOWLEDGE_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Knowledge retrieval query."},
        "profile": {"type": "string"},
        "scope": {"type": "string"},
        "release_id": {"type": "string"},
        "session_id": {"type": "string"},
        "approval_id": {"type": "string"},
        "parameters": {
            "type": "object",
            "properties": {
                "skill": {
                    "type": "string",
                    "enum": [
                        "emotional-support",
                        "risk-triage",
                        "cognitive-reframing-support",
                        "rebt-belief-work-support",
                        "synthetic-dialogue-builder",
                        "therapy-response-reviewer",
                    ],
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "additionalProperties": False,
        },
    },
    "required": ["query"],
}

_COMMON_ACTION_PROPERTIES = {
    "profile": {"type": "string"},
    "scope": {"type": "string"},
    "release_id": {"type": "string"},
    "session_id": {"type": "string"},
    "approval_id": {"type": "string"},
    "idempotency_key": {"type": "string"},
    "workflow_id": {"type": "string", "description": "Resumable TheraSkill workflow id."},
}

THERASKILL_SUPPORT_SCHEMA = {
    "type": "object",
    "properties": {
        **_COMMON_ACTION_PROPERTIES,
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": sorted(ACTION_OPERATIONS["support"])},
                "text": {"type": "string", "minLength": 1, "maxLength": 12000},
            },
            "required": ["operation"],
            "additionalProperties": False,
        },
    },
    "required": ["parameters"],
    "additionalProperties": False,
}

THERASKILL_SIMULATION_SCHEMA = {
    "type": "object",
    "properties": {
        **_COMMON_ACTION_PROPERTIES,
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": sorted(ACTION_OPERATIONS["simulation"])},
                "scenario_id": {"type": "string"},
                "mode": {"type": "string", "enum": ["human_therapist_vs_patient", "human_client_role_vs_therapist", "dual_agent_research", "review_only"]},
                "source_session_id": {"type": "string"},
                "language": {"type": "string", "enum": ["zh", "en", "mixed"]},
                "actor": {"type": "string", "enum": [
                    "human_therapist", "human_client_role", "patient_agent",
                    "therapist_agent", "reviewer_agent", "instructor", "researcher", "system",
                ]},
                "text": {"type": "string", "minLength": 1, "maxLength": 12000},
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["operation"],
            "additionalProperties": False,
        },
    },
    "required": ["parameters"],
    "additionalProperties": False,
}

THERASKILL_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        **_COMMON_ACTION_PROPERTIES,
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": sorted(ACTION_OPERATIONS["response_review"])},
                "contribution_id": {"type": "string"},
                "decision": {"type": "object"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["operation"],
            "additionalProperties": False,
        },
    },
    "required": ["parameters"],
    "additionalProperties": False,
}

THERASKILL_CONTRIBUTION_SCHEMA = {
    "type": "object",
    "properties": {
        **_COMMON_ACTION_PROPERTIES,
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": sorted(ACTION_OPERATIONS["contribution"])},
                "contribution_id": {"type": "string"},
                "contribution_type": {"type": "string", "enum": ["response", "session", "annotation", "knowledge", "correction"]},
                "consent_snapshot_id": {"type": "string"},
                "payload": {"type": "object"},
                "origin": {"type": "object"},
                "target": {"type": "object"},
                "context": {"type": "object"},
                "provenance": {"type": "object"},
                "memory_type": {"type": "string"},
                "content": {"type": "string", "maxLength": 2000},
                "sensitivity_level": {"type": "string", "enum": ["standard", "sensitive"]},
                "ttl_days": {"type": "integer", "minimum": 1, "maximum": 365},
            },
            "required": ["operation"],
            "additionalProperties": False,
        },
    },
    "required": ["parameters"],
    "additionalProperties": False,
}

THERASKILL_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        **_COMMON_ACTION_PROPERTIES,
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": sorted(ACTION_OPERATIONS["extraction"])},
                "role": {"type": "string", "enum": ["agent", "researcher", "knowledge_curator", "expert", "admin"]},
                "goal": {"type": "string", "minLength": 1, "maxLength": 4000},
                "source": {"type": "object"},
                "target_schema": {"type": "object"},
                "plan": {"type": "object"},
                "sample_records": {"type": "array", "maxItems": 5, "items": {"type": "object"}},
                "planner": {"type": "string", "enum": ["heuristic", "model"]},
                "unit_definition": {"type": "string", "maxLength": 1000},
                "language": {"type": "string", "enum": ["auto", "zh", "en", "mixed"]},
                "model_profile": {"type": "string"},
                "job_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100000},
                "requires_approval": {"type": "boolean"},
            },
            "required": ["operation"],
            "additionalProperties": False,
        },
    },
    "required": ["parameters"],
    "additionalProperties": False,
}

THERASKILL_TRANSCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        **_COMMON_ACTION_PROPERTIES,
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": sorted(ACTION_OPERATIONS["transcript"])},
                "source_path": {"type": "string", "minLength": 1},
                "output_dir": {"type": "string"},
                "language": {"type": "string", "enum": ["auto", "zh", "en", "mixed"]},
                "speaker_role_overrides": {"type": "object", "additionalProperties": {"type": "string"}},
                "exclude_raw": {"type": "boolean"},
                "mode": {"type": "string", "enum": ["plan", "model"]},
                "skills": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                "provider_profile": {"type": "string"},
                "max_turns": {"type": "integer", "minimum": 1, "maximum": 10000},
            },
            "required": ["operation", "source_path"],
            "additionalProperties": False,
        },
    },
    "required": ["parameters"],
    "additionalProperties": False,
}


def check_theraskill_requirements() -> bool:
    """Keep the platform toolset visible; handlers report runtime readiness."""
    return True


def _handle_resources(args: dict[str, Any], **runtime: Any) -> dict[str, Any]:
    from theraskill_agent import resource_jobs, resources
    from theraskill_agent.text import redact_secrets
    request = _request("resources", args, runtime)
    params = dict(request.parameters)
    operation = params.pop("operation", "list")
    try:
        if operation == "list":
            result = resources.list_resources()
        elif operation in {"status", "cancel"}:
            result = (resource_jobs.get_job if operation == "status" else resource_jobs.cancel_job)(params["job_id"])
        else:
            result = resource_jobs.submit_job(operation, params)
        record_audit(request, "completed")
        return TheraSkillResult.success(request.action, result, request_id=request.request_id).to_dict()
    except (OSError, ValueError, KeyError) as exc:
        return _failure(request, "resource_error", redact_secrets(str(exc)))


THERASKILL_RESOURCES_SCHEMA = {
    "type": "object", "properties": {"parameters": {
        "type": "object", "properties": {
            "operation": {"type": "string", "enum": ["list", "plan", "build", "verify", "search", "install", "activate", "rollback", "status", "cancel"]},
            **{key: {"type": "string"} for key in ("knowledge", "index_id", "index", "embedding_profile", "archive", "catalog", "package", "sha256", "job_id", "query")},
            "mode": {"type": "string", "enum": ["lexical", "hybrid"]},
            "resume": {"type": "boolean"}, "mapping": {"type": "object"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        }, "required": ["operation"], "additionalProperties": False,
    }}, "required": ["parameters"], "additionalProperties": False,
}


TOOL_DEFINITIONS = (
    ("theraskill_resources", THERASKILL_RESOURCES_SCHEMA, _handle_resources, "📚", "Manage knowledge packages and resumable index jobs without another service port."),
    ("theraskill_context", THERASKILL_CONTEXT_SCHEMA, _handle_context, "🧭", "Inspect the active TheraSkill profile and runtime release."),
    ("theraskill_knowledge_search", THERASKILL_KNOWLEDGE_SEARCH_SCHEMA, _handle_knowledge_search, "🔎", "Search the active TheraSkill knowledge release."),
    ("theraskill_support", THERASKILL_SUPPORT_SCHEMA, lambda args, **runtime: _service_action("support", args, runtime), "💬", "Create or continue a consent-aware TheraSkill support session."),
    ("theraskill_simulation", THERASKILL_SIMULATION_SCHEMA, lambda args, **runtime: _service_action("simulation", args, runtime), "🎭", "Create, continue, or review a TheraSkill simulation session."),
    ("theraskill_response_review", THERASKILL_REVIEW_SCHEMA, lambda args, **runtime: _service_action("response_review", args, runtime), "🧪", "Prepare or complete an authorized TheraSkill expert review."),
    ("theraskill_contribution", THERASKILL_CONTRIBUTION_SCHEMA, lambda args, **runtime: _service_action("contribution", args, runtime), "📝", "Capture or manage a consent-bound TheraSkill contribution."),
    ("theraskill_extraction", THERASKILL_EXTRACTION_SCHEMA, _handle_extraction, "🧬", "Design, validate, and manage a governed dynamic knowledge extraction job."),
    ("theraskill_transcript", THERASKILL_TRANSCRIPT_SCHEMA, _handle_transcript, "📄", "Normalize, analyze, evaluate and package patient/clinician dialogue transcripts."),
)
