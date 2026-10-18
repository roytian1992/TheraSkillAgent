"""Stateful simulation session domain service."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from theraskill_agent.paths import active_release_id

from .artifact_mapping import canonical_json_sha256, read_json
from .contracts import (
    ActorRole,
    EventType,
    PrivatePatientState,
    PrivateTherapistState,
    PublicSessionView,
    ReviewPublicView,
    RoleAssignment,
    ScenarioPublicView,
    SessionEvent,
    SessionFeedback,
    SessionMode,
    SessionSnapshot,
    SessionLanguage,
    SessionStatus,
    SimulationSession,
    TranscriptTurn,
)
from .compatibility import build_reviewer_runtime_report
from .engines import PatientEngine, ReviewerEngine, ReviewResult, TherapistEngine
from .lifecycle import transition_session
from .persona_compiler import (
    effective_disclosure_nodes,
    effective_initially_disclosed_fact_ids,
    public_marital_status,
)
from .repository import SQLiteSessionRepository
from .scenarios import ScenarioRegistry
from .visibility import ExportScope, export_snapshot


class SessionOperationError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _role_assignment(mode: SessionMode) -> RoleAssignment:
    mapping = {
        SessionMode.HUMAN_THERAPIST_VS_PATIENT: RoleAssignment(
            human_roles=[ActorRole.HUMAN_THERAPIST],
            agent_roles=[ActorRole.PATIENT_AGENT],
        ),
        SessionMode.HUMAN_CLIENT_ROLE_VS_THERAPIST: RoleAssignment(
            human_roles=[ActorRole.HUMAN_CLIENT_ROLE],
            agent_roles=[ActorRole.THERAPIST_AGENT],
        ),
        SessionMode.DUAL_AGENT_RESEARCH: RoleAssignment(
            agent_roles=[ActorRole.PATIENT_AGENT, ActorRole.THERAPIST_AGENT]
        ),
        SessionMode.REVIEW_ONLY: RoleAssignment(agent_roles=[ActorRole.REVIEWER_AGENT]),
    }
    return mapping[mode]


def _scenario_public(row: dict[str, Any]) -> ScenarioPublicView:
    return ScenarioPublicView(
        scenario_id=row["scenario_id"],
        scenario_family=row["scenario_family"],
        case_id=row.get("case_id"),
        case_family=row.get("case_family"),
        difficulty_band=row.get("difficulty_band"),
        risk_band=row.get("risk_band"),
        scenario_brief=row.get("scenario_brief"),
        scenario_tags=list(row.get("scenario_tags") or []),
        recommended_turn_budget=row.get("recommended_turn_budget"),
        split_labels=dict(row.get("split_labels") or {}),
        learner_role_card=dict(row.get("learner_role_card") or {}),
    )


class SimulationSessionService:
    def __init__(
        self,
        *,
        repository: SQLiteSessionRepository,
        scenario_registry: ScenarioRegistry,
        patient_engine: PatientEngine | None = None,
        therapist_engine: TherapistEngine | None = None,
        reviewer_engine: ReviewerEngine | None = None,
        runtime_version: str = "theraskill_simulation_session_core_v1",
        knowledge_release_id: str | None = None,
        persona_dir: str | Path | None = None,
        review_job_stale_seconds: float = 900.0,
    ) -> None:
        self.repository = repository
        self.scenario_registry = scenario_registry
        self.patient_engine = patient_engine
        self.therapist_engine = therapist_engine
        self.reviewer_engine = reviewer_engine
        self.runtime_version = runtime_version
        self.knowledge_release_id = knowledge_release_id or active_release_id()
        self._role_card_cache: dict[str, tuple[int, dict[str, Any]]] = {}
        self.persona_dir = Path(persona_dir).resolve() if persona_dir else (
            self.scenario_registry.registry_path.parent.parent / "personas" / "v2"
        )
        self.review_job_stale_seconds = max(30.0, float(review_job_stale_seconds))
        self.repository.recover_stale_review_jobs(self.review_job_stale_seconds)

    def _resolve_case_bundle_path(self, value: Any) -> Path:
        path = Path(str(value))
        if path.is_absolute():
            return path
        return (self.scenario_registry.registry_path.parent.parent / path).resolve()

    @staticmethod
    def _deep_merge_overlay(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in overlay.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = SimulationSessionService._deep_merge_overlay(merged[key], value)
            else:
                merged[key] = value
        return merged

    @staticmethod
    def _english_persona_from_case_bundle(case_bundle: dict[str, Any]) -> dict[str, Any]:
        """Build a bounded English persona context from the manually translated case seed."""
        authoritative = case_bundle.get("authoritative_layer") or {}
        visible = authoritative.get("visible_profile") or {}
        hidden = authoritative.get("hidden_profile") or {}
        continuity = authoritative.get("continuity_seed") or {}
        demographic = visible.get("demographic_profile") or {}
        case_id = str(case_bundle.get("case_id") or "")
        nodes: list[dict[str, Any]] = []

        def add_node(content: Any, sensitivity: str, index: int) -> None:
            text = " ".join(str(content or "").split())
            if not text:
                return
            fact_id = f"fact_en_{case_id.replace('-', '')[:12]}_{index:02d}"
            nodes.append({
                "fact_id": fact_id, "content": text, "sensitivity": sensitivity,
                "default_response_mode": "answer" if sensitivity == "surface" else "hint" if sensitivity == "moderate" else "withhold",
                "allowed_state_bands": ["surface", "moderate", "deep"] if sensitivity == "surface" else ["moderate", "deep"] if sensitivity == "moderate" else ["deep"],
                "facilitating_conditions": ["supportive validation", "clear and non-judgmental questions"],
                "blocking_conditions": ["pressure", "moral judgment"],
            })

        for index, item in enumerate(continuity.get("stable_identity_facts") or [], start=1):
            add_node(item, "surface", index)
        for index, item in enumerate(hidden.get("surface_presenting_story") or [], start=20):
            add_node(item, "surface", index)
        for index, item in enumerate(hidden.get("latent_core_conflicts") or [], start=40):
            add_node(item, "moderate", index)
        for index, item in enumerate(hidden.get("withheld_or_unspoken_concerns") or [], start=60):
            add_node(item, "sensitive", index)
        return {
            "schema_version": "theraskill_patient_persona_v2_en_runtime_overlay",
            "language": "en", "case_id": case_id, "persona_id": f"persona::en_case_overlay::{case_id}",
            "immutable_identity": {"demographic_profile": demographic, "display_name": "Patient", "facts": [
                {"fact_id": n["fact_id"], "content": n["content"]} for n in nodes if n["sensitivity"] == "surface"
            ]},
            "initial_state": dict((case_bundle.get("runtime_state_seed") or {}).get("initial_session_state") or {}),
            "communication_profile": {
                "response_length": "brief to moderate, matching the current conversation",
                "vocabulary_and_abstraction": "use clear, natural English appropriate to the patient's age",
                "narrative_organization": "answer the immediate question before adding context",
                "initiative_level": "respond to the therapist and volunteer only what feels safe",
                "language": "en",
                "emotional_expression": [
                    {"content": str(item)} for item in visible.get("emotional_profile") or []
                ],
                "hesitation_and_avoidance": [
                    {"content": str(item)} for item in hidden.get("withheld_or_unspoken_concerns") or []
                ],
                "advice_response_style": [
                    {"content": str(item)} for item in visible.get("help_seeking_style") or []
                ],
            },
            "psychological_model": {
                "presenting_problems": [
                    {"content": str(item)} for item in visible.get("presenting_problem_profile") or []
                ],
                "risk_watchpoints": [
                    {"content": str(item)} for item in continuity.get("risk_watchpoints") or []
                ],
            },
            "disclosure_graph": {"nodes": nodes},
            "behavioral_constitution": {"principles": [
                {"principle_id": "en_identity_consistency", "category": "identity_consistency", "when": "any turn", "then": "keep stable facts consistent", "unless": "the patient is uncertain or declines to answer"},
                {"principle_id": "en_disclosure_pacing", "category": "disclosure_behavior", "when": "sensitive content is approached", "then": "disclose gradually after sufficient safety", "unless": "the patient clearly volunteers it"},
                {"principle_id": "en_risk_boundary", "category": "risk_expression_boundary", "when": "risk is discussed", "then": "stay within documented evidence and preserve uncertainty", "unless": "acute safety evidence is explicit"},
            ]},
        }

    def _load_case_bundle(
        self,
        scenario_row: dict[str, Any],
        *,
        language: str = "zh",
    ) -> tuple[dict[str, Any], dict[str, Any] | None, Path | None]:
        case_bundle = read_json(self._resolve_case_bundle_path(scenario_row["case_bundle_path"]))
        explicit_path = scenario_row.get("patient_persona_v2_path")
        case_id = str(scenario_row.get("case_id") or case_bundle.get("case_id") or "")
        if language == "en" and case_id:
            overlay_path = self.scenario_registry.registry_path.parent.parent / "locales" / "en" / "cases" / f"{case_id}.json"
            if overlay_path.exists():
                overlay = read_json(overlay_path)
                case_bundle = self._deep_merge_overlay(
                    case_bundle,
                    {
                        key: value
                        for key, value in overlay.items()
                        if key not in {"schema_version", "language", "source_language", "case_id"}
                    },
                )
                persona = self._english_persona_from_case_bundle(case_bundle)
                runtime_bundle = {**case_bundle, "_patient_persona_v2": persona}
                return runtime_bundle, persona, None
        persona_path = Path(str(explicit_path)) if explicit_path else self.persona_dir / f"{case_id}.json"
        if not case_id or not persona_path.exists():
            return case_bundle, None, None
        persona = read_json(persona_path)
        runtime_bundle = {**case_bundle, "_patient_persona_v2": persona}
        return runtime_bundle, persona, persona_path

    @staticmethod
    def _age_band(value: Any) -> str:
        text = str(value or "").strip()
        try:
            age = int(float(text))
        except (TypeError, ValueError):
            return text or "未说明"
        if age < 18:
            return "未成年"
        if age < 25:
            return "18-24岁"
        if age < 35:
            return "25-34岁"
        if age < 50:
            return "35-49岁"
        if age < 65:
            return "50-64岁"
        return "65岁及以上"

    def learner_role_card_for_case(self, case_id: str, scenario_row: dict[str, Any] | None = None) -> dict[str, Any]:
        persona_path = self.persona_dir / f"{case_id}.json"
        if not persona_path.exists():
            return {}
        modified_ns = persona_path.stat().st_mtime_ns
        cached = self._role_card_cache.get(case_id)
        if cached and cached[0] == modified_ns:
            card = dict(cached[1])
            if scenario_row and scenario_row.get("scenario_brief"):
                card["current_context"] = scenario_row["scenario_brief"]
            return card
        persona = read_json(persona_path)
        identity = persona.get("immutable_identity") or {}
        demographics = identity.get("demographic_profile") or {}
        effective_nodes = effective_disclosure_nodes(persona)
        surface_contents = {
            str(node.get("content"))
            for node in effective_nodes
            if node.get("sensitivity") == "surface" and node.get("content")
        }
        forbidden_contents = {
            str(node.get("content"))
            for node in effective_nodes
            if node.get("sensitivity") != "surface" and node.get("content")
        }
        marital_status = public_marital_status(demographics.get("marital_status"))
        if marital_status in forbidden_contents:
            marital_status = "未说明"
        presenting = [
            str(item.get("content"))
            for item in (persona.get("psychological_model") or {}).get("presenting_problems") or []
            if item.get("content") in surface_contents
        ][:3]
        contract = persona.get("training_contract") or {}
        scenario = scenario_row or {}
        card = {
            "display_name": identity.get("display_name") or "模拟来访者",
            "age_band": self._age_band(demographics.get("age")),
            "gender": demographics.get("gender") or "未说明",
            "occupation": demographics.get("occupation") or "未说明",
            "marital_status": marital_status,
            "surface_presenting_problems": presenting,
            "current_context": contract.get("learner_visible_case_brief") or "",
            "training_objectives": list(contract.get("training_objectives") or [])[:4],
        }
        self._role_card_cache[case_id] = (modified_ns, dict(card))
        if scenario.get("scenario_brief"):
            card["current_context"] = scenario["scenario_brief"]
        return card

    @staticmethod
    def _instructor_persona_reveal(persona: dict[str, Any]) -> dict[str, Any]:
        nodes = (persona.get("disclosure_graph") or {}).get("nodes") or []
        hidden_nodes = [node for node in nodes if node.get("sensitivity") in {"moderate", "sensitive"}]
        return {
            "schema_version": "theraskill_instructor_persona_reveal_v1",
            "persona_id": persona.get("persona_id"),
            "reference_identity": persona.get("immutable_identity") or {},
            "hidden_fact_nodes": [
                {
                    "fact_id": node.get("fact_id"),
                    "content": node.get("content"),
                    "sensitivity": node.get("sensitivity"),
                    "facilitating_conditions": node.get("facilitating_conditions") or [],
                    "blocking_conditions": node.get("blocking_conditions") or [],
                }
                for node in hidden_nodes
            ],
            "behavioral_principles": (persona.get("behavioral_constitution") or {}).get("principles") or [],
            "training_contract": persona.get("training_contract") or {},
            "quality": persona.get("quality") or {},
        }

    def _event(
        self,
        session_id: str,
        sequence: int,
        event_type: EventType,
        actor: ActorRole,
        *,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        retrieved_unit_ids: list[str] | None = None,
        model_config_id: str | None = None,
        prompt_release_id: str | None = None,
    ) -> SessionEvent:
        return SessionEvent(
            event_id=f"evt_{uuid.uuid4().hex}",
            session_id=session_id,
            sequence=sequence,
            event_type=event_type,
            actor=actor,
            payload=payload or {},
            idempotency_key=idempotency_key,
            retrieved_unit_ids=retrieved_unit_ids or [],
            model_config_id=model_config_id,
            prompt_release_id=prompt_release_id,
        )

    def _snapshot(
        self,
        session: SimulationSession,
        public: PublicSessionView,
        private_patient: PrivatePatientState | None,
        private_therapist: PrivateTherapistState | None,
        sequence: int,
    ) -> SessionSnapshot:
        payload = {
            "session": session.model_dump(mode="json"),
            "public": public.model_dump(mode="json"),
            "private_patient": private_patient.model_dump(mode="json") if private_patient else None,
            "private_therapist": private_therapist.model_dump(mode="json") if private_therapist else None,
            "sequence": sequence,
        }
        checksum = canonical_json_sha256(payload)
        return SessionSnapshot(
            snapshot_id=f"snap_{session.session_id}_{sequence:08d}_{checksum[:12]}",
            session=session,
            through_event_sequence=sequence,
            public_view=public,
            private_patient_state=private_patient,
            private_therapist_state=private_therapist,
            checksum=checksum,
        )

    def create_session(
        self,
        *,
        scenario_id: str,
        mode: SessionMode | str = SessionMode.HUMAN_THERAPIST_VS_PATIENT,
        language: SessionLanguage | str = SessionLanguage.ZH,
        session_id: str | None = None,
        parent_session_id: str | None = None,
        source_session_id: str | None = None,
    ) -> SessionSnapshot:
        resolved_mode = SessionMode(mode)
        resolved_language = SessionLanguage(language)
        source_snapshot = None
        if resolved_mode == SessionMode.REVIEW_ONLY:
            if not source_session_id:
                raise SessionOperationError("review_only mode requires source_session_id")
            source_snapshot = self.repository.get_latest_snapshot(source_session_id)
            if source_session_id == session_id:
                raise SessionOperationError("review source cannot be the new session")
            if SessionStatus(source_snapshot.session.status) not in {
                SessionStatus.COMPLETED,
                SessionStatus.TERMINATED,
                SessionStatus.FAILED,
            }:
                raise SessionOperationError("review source session must be terminal")
            if str(source_snapshot.public_view.scenario.scenario_id) != str(scenario_id):
                raise SessionOperationError("review source and scenario_id must match")
        scenario_row = self.scenario_registry.get(scenario_id, language=str(language))
        scenario = _scenario_public(scenario_row)
        now = utc_now()
        resolved_id = session_id or f"sim_{uuid.uuid4().hex}"
        session = SimulationSession(
            session_id=resolved_id,
            mode=resolved_mode,
            language=resolved_language,
            scenario=scenario,
            role_assignment=_role_assignment(resolved_mode),
            created_at=now,
            updated_at=now,
            runtime_version=self.runtime_version,
            knowledge_release_id=self.knowledge_release_id,
            metadata={
                "case_bundle_path": scenario_row.get("case_bundle_path"),
                "scenario_registry_path": str(self.scenario_registry.registry_path),
                "language": resolved_language.value,
                **({
                    "source_session_id": source_session_id,
                    "source_snapshot_id": source_snapshot.snapshot_id,
                    "review_source_incomplete_state": bool(source_snapshot.session.metadata.get("review_source_incomplete_state")) or any(
                        (source_snapshot.session.metadata.get(name) or {}).get("status") in {"pending", "failed"}
                        for name in ("patient_state_update", "therapist_memory_update")
                    ),
                } if source_snapshot is not None else {}),
            },
        )
        session = transition_session(session, SessionStatus.ACTIVE)
        private_patient = None
        private_therapist = None
        transcript: list[TranscriptTurn] = list(source_snapshot.public_view.transcript) if source_snapshot else []
        if resolved_mode in {
            SessionMode.HUMAN_THERAPIST_VS_PATIENT,
            SessionMode.DUAL_AGENT_RESEARCH,
        }:
            case_bundle, persona, persona_path = self._load_case_bundle(
                scenario_row,
                language=resolved_language.value,
            )
            authoritative = case_bundle.get("authoritative_layer") or {}
            hidden = authoritative.get("hidden_profile") or {}
            runtime_seed = case_bundle.get("runtime_state_seed") or {}
            raw_source_unit_ids = authoritative.get("source_unit_ids") or []
            evidence = list(
                raw_source_unit_ids.values()
                if isinstance(raw_source_unit_ids, dict)
                else raw_source_unit_ids
            )
            for key in (
                "patient_protocol_unit_ids",
                "state_transition_hint_unit_ids",
                "reviewer_protocol_unit_ids",
            ):
                evidence.extend((case_bundle.get("runtime_priors") or {}).get(key) or [])
            if persona:
                scenario = scenario.model_copy(
                    update={
                        "learner_role_card": self.learner_role_card_for_case(
                            str(persona.get("case_id") or ""), scenario_row
                        )
                    }
                )
                nodes = (persona.get("disclosure_graph") or {}).get("nodes") or []
                sensitive_nodes = [node for node in nodes if node.get("sensitivity") == "sensitive"]
                hidden_nodes = [node for node in nodes if node.get("sensitivity") in {"moderate", "sensitive"}]
                principles = (persona.get("behavioral_constitution") or {}).get("principles") or []
                initial_disclosed_fact_ids = effective_initially_disclosed_fact_ids(persona)
                private_patient = PrivatePatientState(
                    persona_id=persona.get("persona_id"),
                    persona_schema_version=persona.get("schema_version"),
                    disclosed_fact_ids=initial_disclosed_fact_ids,
                    latent_concerns=[str(node.get("content")) for node in sensitive_nodes if node.get("content")],
                    withheld_information=[str(node.get("content")) for node in hidden_nodes if node.get("content")],
                    disclosure_conditions=list(dict.fromkeys(
                        str(item)
                        for node in hidden_nodes
                        for item in node.get("facilitating_conditions") or []
                        if item
                    )),
                    rupture_sensitivity=[
                        str(item.get("when"))
                        for item in principles
                        if item.get("category") == "alliance_and_rupture" and item.get("when")
                    ],
                    risk_signals_not_yet_disclosed=[
                        str(item.get("content"))
                        for item in (persona.get("psychological_model") or {}).get("risk_watchpoints") or []
                        if item.get("content")
                    ],
                    runtime_state=dict(persona.get("initial_state") or runtime_seed.get("initial_session_state") or {}),
                    working_memory={
                        "schema_version": "theraskill_patient_conversation_memory_v1",
                        "source_seed_policy": "withheld_by_persona_context_firewall",
                        "disclosed_fact_ids": initial_disclosed_fact_ids,
                        "longitudinal_problem_summary": [],
                        "unresolved_threads": [],
                        "report_dialogue_alignment": [],
                    },
                    evidence_unit_ids=list(dict.fromkeys(str(item) for item in evidence if item)),
                )
                session = session.model_copy(
                    update={
                        "scenario": scenario,
                        "metadata": {
                            **session.metadata,
                            "patient_persona_v2_path": str(persona_path) if persona_path else None,
                            "patient_persona_id": persona.get("persona_id"),
                        }
                    }
                )
            else:
                private_patient = PrivatePatientState(
                    latent_concerns=list(hidden.get("latent_core_conflicts") or []),
                    withheld_information=list(hidden.get("withheld_or_unspoken_concerns") or []),
                    disclosure_conditions=list(hidden.get("disclosure_conditions") or []),
                    rupture_sensitivity=list(hidden.get("rupture_triggers") or []),
                    risk_signals_not_yet_disclosed=list(
                        (authoritative.get("continuity_seed") or {}).get("risk_watchpoints") or []
                    ),
                    runtime_state=dict(runtime_seed.get("initial_session_state") or {}),
                    working_memory=dict(runtime_seed.get("episodic_memory_seed") or {}),
                    evidence_unit_ids=list(dict.fromkeys(str(item) for item in evidence if item)),
                )
            if parent_session_id:
                parent = self.repository.get_latest_snapshot(parent_session_id)
                if SessionStatus(parent.session.status) not in {
                    SessionStatus.COMPLETED,
                    SessionStatus.TERMINATED,
                    SessionStatus.FAILED,
                }:
                    raise SessionOperationError("parent session must be terminal before carryover")
                if parent.session.scenario.case_id != scenario.case_id:
                    raise SessionOperationError("parent session carryover requires the same case_id")
                if parent.private_patient_state is None:
                    raise SessionOperationError("parent session has no patient state to carry over")
                carry_fields = (
                    "alliance_state",
                    "disclosure_state",
                    "resistance_state",
                    "readiness_state",
                    "risk_state",
                    "felt_understanding",
                    "unfinished_threads",
                )
                carried_runtime = dict(private_patient.runtime_state)
                carried_runtime.update(
                    {
                        key: parent.private_patient_state.runtime_state[key]
                        for key in carry_fields
                        if key in parent.private_patient_state.runtime_state
                    }
                )
                private_patient = private_patient.model_copy(
                    update={
                        "disclosed_fact_ids": list(parent.private_patient_state.disclosed_fact_ids),
                        "runtime_state": carried_runtime,
                        "working_memory": dict(parent.private_patient_state.working_memory),
                        "trust_alliance_readiness": dict(
                            parent.private_patient_state.trust_alliance_readiness
                        ),
                    }
                )
                session = session.model_copy(
                    update={
                        "metadata": {
                            **session.metadata,
                            "parent_session_id": parent_session_id,
                            "carryover_policy": "explicit_same_case_terminal_parent_v1",
                        }
                    }
                )
            seed = runtime_seed.get("initial_transcript_seed") or (
                authoritative.get("continuity_seed") or {}
            ).get("seed_transcript") or scenario_row.get("seed_transcript") or []
            transcript = [
                TranscriptTurn(
                    turn_id=f"turn_{index:04d}",
                    turn_index=index,
                    role=str(row.get("role")),
                    text=str(row.get("text")),
                )
                for index, row in enumerate(seed, start=1)
                if row.get("role") in {"patient", "therapist"} and row.get("text")
            ]
        if resolved_mode in {
            SessionMode.HUMAN_CLIENT_ROLE_VS_THERAPIST,
            SessionMode.DUAL_AGENT_RESEARCH,
        }:
            private_therapist = PrivateTherapistState()
        if resolved_mode == SessionMode.REVIEW_ONLY:
            completed_session = transition_session(session, SessionStatus.COMPLETED)
            public = PublicSessionView(
                session_id=resolved_id,
                mode=resolved_mode,
                language=resolved_language,
                status=SessionStatus.COMPLETED,
                scenario=scenario,
                current_turn_index=len(transcript),
                transcript=transcript,
                progress={
                    "source_session_id": source_session_id,
                    "review_only": 1,
                    "recommended_turn_budget": scenario.recommended_turn_budget,
                },
                review=ReviewPublicView(status="pending", summary={}, findings=[]),
                created_at=now,
                updated_at=completed_session.updated_at,
            )
            events = [
                self._event(resolved_id, 0, EventType.SESSION_CREATED, ActorRole.SYSTEM),
                self._event(
                    resolved_id,
                    1,
                    EventType.SCENARIO_INITIALIZED,
                    ActorRole.SYSTEM,
                    payload={
                        "scenario_id": scenario_id,
                        "mode": resolved_mode.value,
                        "source_session_id": source_session_id,
                    },
                    retrieved_unit_ids=(source_snapshot.private_patient_state.evidence_unit_ids
                        if source_snapshot and source_snapshot.private_patient_state else []),
                ),
                self._event(resolved_id, 2, EventType.SESSION_COMPLETED, ActorRole.SYSTEM,
                            payload={"source_session_id": source_session_id}),
                self._event(resolved_id, 3, EventType.REVIEW_REQUESTED, ActorRole.SYSTEM,
                            payload={"source_session_id": source_session_id}),
            ]
            session = completed_session.model_copy(
                update={"metadata": {**completed_session.metadata, "review_generation": {"status": "pending"}}}
            )
            snapshot = self._snapshot(
                session, public,
                source_snapshot.private_patient_state if source_snapshot else None,
                source_snapshot.private_therapist_state if source_snapshot else None,
                events[-1].sequence
            )
            self.repository.create(snapshot, events)
            self.repository.enqueue_review_job(resolved_id)
            return snapshot
        public = PublicSessionView(
            session_id=resolved_id,
            mode=resolved_mode,
            language=resolved_language,
            status=SessionStatus.ACTIVE,
            scenario=scenario,
            current_turn_index=len(transcript),
            transcript=transcript,
            progress={
                "recommended_turn_budget": scenario.recommended_turn_budget,
                "agent_response_count": sum(row.role == "patient" for row in transcript),
            },
            created_at=now,
            updated_at=now,
        )
        events = [
            self._event(resolved_id, 0, EventType.SESSION_CREATED, ActorRole.SYSTEM),
            self._event(
                resolved_id,
                1,
                EventType.SCENARIO_INITIALIZED,
                ActorRole.SYSTEM,
                payload={
                    "scenario_id": scenario_id,
                    "mode": resolved_mode.value,
                    "parent_session_id": parent_session_id,
                },
                retrieved_unit_ids=(private_patient.evidence_unit_ids if private_patient else []),
            ),
        ]
        snapshot = self._snapshot(
            session, public, private_patient, private_therapist, events[-1].sequence
        )
        self.repository.create(snapshot, events)
        return snapshot

    def get_snapshot(self, session_id: str) -> SessionSnapshot:
        return self.repository.get_latest_snapshot(session_id)

    def list_public_sessions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return [
            snapshot.public_view.model_dump(mode="json")
            for snapshot in self.repository.list_latest_snapshots(limit=limit)
        ]

    def delete_session(self, session_id: str) -> None:
        self.repository.delete(session_id)

    def generate_patient_opening(
        self,
        session_id: str,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        cached = self.repository.get_idempotency_result(session_id, idempotency_key)
        if cached is not None:
            return cached
        snapshot = self.repository.get_latest_snapshot(session_id)
        if SessionStatus(snapshot.session.status) != SessionStatus.ACTIVE:
            raise SessionOperationError(f"session is not active: {snapshot.session.status}")
        if SessionMode(snapshot.session.mode) != SessionMode.HUMAN_THERAPIST_VS_PATIENT:
            raise SessionOperationError("patient opening is only available in therapist training mode")
        if snapshot.public_view.transcript:
            raise SessionOperationError("patient opening is only available before the first turn")
        if self.patient_engine is None or not hasattr(self.patient_engine, "generate_opening"):
            raise SessionOperationError("patient opening engine is not configured")
        if snapshot.private_patient_state is None:
            raise SessionOperationError("patient private state is missing")

        scenario_row = self.scenario_registry.get(
            snapshot.session.scenario.scenario_id,
            language=str(snapshot.session.language),
        )
        case_bundle, _, _ = self._load_case_bundle(
            scenario_row,
            language=str(snapshot.session.language),
        )
        result = self.patient_engine.generate_opening(
            scenario=scenario_row,
            case_bundle=case_bundle,
            transcript=snapshot.public_view.transcript,
            private_state=snapshot.private_patient_state,
            language=str(snapshot.session.language),
        )
        now = utc_now()
        opening_turn = TranscriptTurn(
            turn_id="turn_0001",
            turn_index=1,
            role="patient",
            text=result.text,
            created_at=now,
            metadata={"surface_role": "patient_opening"},
        )
        public = snapshot.public_view.model_copy(
            update={
                "transcript": [opening_turn],
                "current_turn_index": 1,
                "updated_at": now,
                "progress": {
                    **snapshot.public_view.progress,
                    "agent_response_count": 1,
                },
            }
        )
        session = snapshot.session.model_copy(
            update={
                "current_turn_index": 1,
                "updated_at": now,
                "model_config_id": result.model_config_id,
                "prompt_release_id": result.prompt_release_id,
                "metadata": {
                    **snapshot.session.metadata,
                    "opening_actor": "patient_agent",
                    "latest_runtime_trace": result.runtime_trace,
                },
            }
        )
        prior_sequence = snapshot.through_event_sequence
        events = [
            self._event(
                session_id,
                prior_sequence + 1,
                EventType.AGENT_TURN_GENERATED,
                ActorRole.PATIENT_AGENT,
                payload={"turn": opening_turn.model_dump(mode="json")},
                idempotency_key=idempotency_key,
                retrieved_unit_ids=result.retrieved_unit_ids,
                model_config_id=result.model_config_id,
                prompt_release_id=result.prompt_release_id,
            ),
            self._event(
                session_id,
                prior_sequence + 2,
                EventType.PATIENT_STATE_UPDATED,
                ActorRole.PATIENT_AGENT,
                payload={
                    "state_checksum": canonical_json_sha256(
                        (result.patient_state or snapshot.private_patient_state).model_dump(
                            mode="json"
                        )
                    )
                },
            ),
        ]
        next_snapshot = self._snapshot(
            session,
            public,
            result.patient_state or snapshot.private_patient_state,
            snapshot.private_therapist_state,
            events[-1].sequence,
        )
        response = {
            "schema_version": "theraskill_simulation_patient_opening_result_v1",
            "session_id": session_id,
            "agent_turn": opening_turn.model_dump(mode="json"),
            "snapshot_id": next_snapshot.snapshot_id,
            "through_event_sequence": next_snapshot.through_event_sequence,
        }
        self.repository.commit(
            next_snapshot,
            events,
            expected_prior_sequence=prior_sequence,
            idempotency_key=idempotency_key,
            idempotency_result=response,
        )
        return response

    def submit_turn(
        self,
        session_id: str,
        *,
        text: str,
        actor: ActorRole | str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        cached = self.repository.get_idempotency_result(session_id, idempotency_key)
        if cached is not None:
            return cached
        snapshot = self.repository.get_latest_snapshot(session_id)
        session = snapshot.session
        if SessionStatus(session.status) != SessionStatus.ACTIVE:
            raise SessionOperationError(f"session is not active: {session.status}")
        resolved_actor = ActorRole(actor)
        resolved_mode = SessionMode(session.mode)
        if resolved_mode == SessionMode.HUMAN_CLIENT_ROLE_VS_THERAPIST:
            return self._submit_human_client_turn(
                snapshot,
                text=text,
                actor=resolved_actor,
                idempotency_key=idempotency_key,
            )
        if resolved_mode != SessionMode.HUMAN_THERAPIST_VS_PATIENT:
            raise SessionOperationError(
                "interactive submit_turn is currently implemented for human_therapist_vs_patient; "
                "other modes require their corresponding engine adapter"
            )
        if resolved_actor != ActorRole.HUMAN_THERAPIST:
            raise SessionOperationError("this mode accepts turns from human_therapist")
        pending_update = session.metadata.get("patient_state_update") or {}
        if pending_update.get("status") == "pending":
            raise SessionOperationError("patient state update is still running")
        if pending_update.get("status") == "failed":
            raise SessionOperationError("patient state update failed; retry or restart the session")
        if self.patient_engine is None:
            raise SessionOperationError("patient engine is not configured")
        clean_text = " ".join(text.split())
        if not clean_text:
            raise ValueError("turn text cannot be empty")
        if snapshot.private_patient_state is None:
            raise SessionOperationError("patient private state is missing")
        scenario_row = self.scenario_registry.get(
            session.scenario.scenario_id,
            language=str(session.language),
        )
        case_bundle, _, _ = self._load_case_bundle(
            scenario_row,
            language=str(session.language),
        )
        result = self.patient_engine.generate_turn(
            scenario=scenario_row,
            case_bundle=case_bundle,
            transcript=snapshot.public_view.transcript,
            private_state=snapshot.private_patient_state,
            therapist_utterance=clean_text,
            language=str(session.language),
        )
        deferred_update = result.runtime_trace.get("deferred_update")
        deferred_update_id = result.runtime_trace.get("deferred_update_id")
        is_deferred = isinstance(deferred_update, dict) and bool(deferred_update_id)
        now = utc_now()
        next_index = snapshot.public_view.current_turn_index
        human_turn = TranscriptTurn(
            turn_id=f"turn_{next_index + 1:04d}",
            turn_index=next_index + 1,
            role="therapist",
            text=clean_text,
            created_at=now,
        )
        agent_turn = TranscriptTurn(
            turn_id=f"turn_{next_index + 2:04d}",
            turn_index=next_index + 2,
            role="patient",
            text=result.text,
            created_at=now,
        )
        public = snapshot.public_view.model_copy(
            update={
                "transcript": snapshot.public_view.transcript + [human_turn, agent_turn],
                "current_turn_index": next_index + 2,
                "updated_at": now,
                "progress": {
                    **snapshot.public_view.progress,
                    "agent_response_count": int(
                        snapshot.public_view.progress.get("agent_response_count") or 0
                    )
                    + 1,
                    "state_update_pending": 1 if is_deferred else 0,
                },
            }
        )
        session = session.model_copy(
            update={
                "current_turn_index": next_index + 2,
                "updated_at": now,
                "model_config_id": result.model_config_id or session.model_config_id,
                "prompt_release_id": result.prompt_release_id or session.prompt_release_id,
                "metadata": {
                    **session.metadata,
                    "latest_runtime_trace": result.runtime_trace,
                    "patient_state_update": {
                        "status": "pending" if is_deferred else "complete",
                        "update_id": deferred_update_id,
                    },
                },
            }
        )
        prior_sequence = snapshot.through_event_sequence
        events = [
            self._event(
                session_id,
                prior_sequence + 1,
                EventType.HUMAN_TURN_SUBMITTED,
                resolved_actor,
                payload={"turn": human_turn.model_dump(mode="json")},
                idempotency_key=idempotency_key,
            ),
            self._event(
                session_id,
                prior_sequence + 2,
                EventType.AGENT_TURN_GENERATED,
                ActorRole.PATIENT_AGENT,
                payload={"turn": agent_turn.model_dump(mode="json")},
                retrieved_unit_ids=result.retrieved_unit_ids,
                model_config_id=result.model_config_id,
                prompt_release_id=result.prompt_release_id,
            ),
        ]
        if not is_deferred:
            events.append(
                self._event(
                    session_id,
                    prior_sequence + 3,
                    EventType.PATIENT_STATE_UPDATED,
                    ActorRole.PATIENT_AGENT,
                    payload={
                        "state_checksum": canonical_json_sha256(
                            (result.patient_state or snapshot.private_patient_state).model_dump(
                                mode="json"
                            )
                        )
                    },
                )
            )
        next_snapshot = self._snapshot(
            session,
            public,
            result.patient_state or snapshot.private_patient_state,
            snapshot.private_therapist_state,
            events[-1].sequence,
        )
        response = {
            "schema_version": "theraskill_simulation_submit_turn_result_v1",
            "session_id": session_id,
            "human_turn": human_turn.model_dump(mode="json"),
            "agent_turn": agent_turn.model_dump(mode="json"),
            "snapshot_id": next_snapshot.snapshot_id,
            "through_event_sequence": next_snapshot.through_event_sequence,
            "deferred_update_id": deferred_update_id,
            "state_update_pending": is_deferred,
        }
        self.repository.commit(
            next_snapshot,
            events,
            expected_prior_sequence=prior_sequence,
            idempotency_key=idempotency_key,
            idempotency_result=response,
        )
        return response

    def finalize_patient_turn(
        self,
        session_id: str,
        *,
        deferred_update_id: str,
    ) -> None:
        snapshot = self.repository.get_latest_snapshot(session_id)
        update_meta = snapshot.session.metadata.get("patient_state_update") or {}
        if update_meta.get("update_id") != deferred_update_id:
            return
        if update_meta.get("status") != "pending":
            return
        if self.patient_engine is None or not hasattr(self.patient_engine, "finalize_turn"):
            raise SessionOperationError("deferred patient state engine is not configured")
        if snapshot.private_patient_state is None:
            raise SessionOperationError("patient private state is missing")
        foreground_trace = snapshot.session.metadata.get("latest_runtime_trace") or {}
        deferred_update = foreground_trace.get("deferred_update")
        if not isinstance(deferred_update, dict):
            raise SessionOperationError("deferred patient state payload is missing")
        scenario_row = self.scenario_registry.get(
            snapshot.session.scenario.scenario_id,
            language=str(snapshot.session.language),
        )
        case_bundle, _, _ = self._load_case_bundle(
            scenario_row,
            language=str(snapshot.session.language),
        )

        try:
            result = self.patient_engine.finalize_turn(
                case_bundle=case_bundle,
                transcript=snapshot.public_view.transcript,
                private_state=snapshot.private_patient_state,
                deferred_update=deferred_update,
                language=str(snapshot.session.language),
            )
        except Exception as exc:
            self._mark_patient_update_failed(
                snapshot,
                deferred_update_id=deferred_update_id,
                error=f"{type(exc).__name__}: {exc}",
            )
            return

        now = utc_now()
        public = snapshot.public_view.model_copy(
            update={
                "updated_at": now,
                "progress": {
                    **snapshot.public_view.progress,
                    "state_update_pending": 0,
                },
            }
        )
        turn_records = list(snapshot.session.metadata.get("patient_turn_records") or [])
        turn_records.append(
            {
                "turn_index": snapshot.public_view.current_turn_index // 2,
                "therapist_utterance": deferred_update.get("therapist_utterance"),
                "therapist_move_interpretation": deferred_update.get(
                    "therapist_move_interpretation"
                ),
                "disclosure_decision": deferred_update.get("disclosure_decision"),
                "patient_turn_result": deferred_update.get("patient_turn_result"),
                "state_update_result": result.runtime_trace.get("state_update_result"),
                "memory_update_result": result.runtime_trace.get("memory_update_result"),
            }
        )
        session = snapshot.session.model_copy(
            update={
                "updated_at": now,
                "prompt_release_id": result.prompt_release_id,
                "metadata": {
                    **snapshot.session.metadata,
                    "latest_runtime_trace": {
                        **foreground_trace,
                        "deferred_update": None,
                        **result.runtime_trace,
                    },
                    "patient_turn_records": turn_records,
                    "patient_state_update": {
                        "status": "complete",
                        "update_id": deferred_update_id,
                    },
                },
            }
        )
        prior_sequence = snapshot.through_event_sequence
        event = self._event(
            session_id,
            prior_sequence + 1,
            EventType.PATIENT_STATE_UPDATED,
            ActorRole.PATIENT_AGENT,
            payload={
                "status": "complete",
                "update_id": deferred_update_id,
                "state_checksum": canonical_json_sha256(
                    (result.patient_state or snapshot.private_patient_state).model_dump(
                        mode="json"
                    )
                ),
            },
            model_config_id=result.model_config_id,
            prompt_release_id=result.prompt_release_id,
        )
        next_snapshot = self._snapshot(
            session,
            public,
            result.patient_state or snapshot.private_patient_state,
            snapshot.private_therapist_state,
            event.sequence,
        )
        self.repository.commit(
            next_snapshot,
            [event],
            expected_prior_sequence=prior_sequence,
        )

    def _mark_patient_update_failed(
        self,
        snapshot: SessionSnapshot,
        *,
        deferred_update_id: str,
        error: str,
    ) -> None:
        now = utc_now()
        public = snapshot.public_view.model_copy(
            update={
                "updated_at": now,
                "progress": {
                    **snapshot.public_view.progress,
                    "state_update_pending": 0,
                    "state_update_failed": 1,
                },
            }
        )
        session = snapshot.session.model_copy(
            update={
                "updated_at": now,
                "metadata": {
                    **snapshot.session.metadata,
                    "patient_state_update": {
                        "status": "failed",
                        "update_id": deferred_update_id,
                        "error": error[:1000],
                    },
                },
            }
        )
        prior_sequence = snapshot.through_event_sequence
        event = self._event(
            session.session_id,
            prior_sequence + 1,
            EventType.PATIENT_STATE_UPDATED,
            ActorRole.PATIENT_AGENT,
            payload={
                "status": "failed",
                "update_id": deferred_update_id,
                "error": error[:1000],
            },
        )
        next_snapshot = self._snapshot(
            session,
            public,
            snapshot.private_patient_state,
            snapshot.private_therapist_state,
            event.sequence,
        )
        self.repository.commit(
            next_snapshot,
            [event],
            expected_prior_sequence=prior_sequence,
        )

    def _submit_human_client_turn(
        self,
        snapshot: SessionSnapshot,
        *,
        text: str,
        actor: ActorRole,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if actor != ActorRole.HUMAN_CLIENT_ROLE:
            raise SessionOperationError("this mode accepts turns from human_client_role")
        if self.therapist_engine is None:
            raise SessionOperationError("therapist engine is not configured")
        if snapshot.private_therapist_state is None:
            raise SessionOperationError("therapist private state is missing")
        pending_update = snapshot.session.metadata.get("therapist_memory_update") or {}
        if pending_update.get("status") == "pending":
            raise SessionOperationError("therapist memory update is still running")
        if pending_update.get("status") == "failed":
            raise SessionOperationError("therapist memory update failed; retry or restart the session")
        clean_text = " ".join(text.split())
        if not clean_text:
            raise ValueError("turn text cannot be empty")
        scenario_row = self.scenario_registry.get(
            snapshot.session.scenario.scenario_id,
            language=str(snapshot.session.language),
        )
        next_index = snapshot.public_view.current_turn_index
        result = self.therapist_engine.generate_turn(
            session_id=snapshot.session.session_id,
            turn_index=next_index + 2,
            scenario=scenario_row,
            transcript=snapshot.public_view.transcript,
            private_state=snapshot.private_therapist_state,
            patient_utterance=clean_text,
            language=str(snapshot.session.language),
        )
        deferred_update = result.runtime_trace.get("deferred_update")
        deferred_update_id = result.runtime_trace.get("deferred_update_id")
        is_deferred = isinstance(deferred_update, dict) and bool(deferred_update_id)
        now = utc_now()
        human_turn = TranscriptTurn(
            turn_id=f"turn_{next_index + 1:04d}",
            turn_index=next_index + 1,
            role="patient",
            text=clean_text,
            created_at=now,
        )
        agent_turn = TranscriptTurn(
            turn_id=f"turn_{next_index + 2:04d}",
            turn_index=next_index + 2,
            role="therapist",
            text=result.text,
            created_at=now,
        )
        public = snapshot.public_view.model_copy(
            update={
                "transcript": snapshot.public_view.transcript + [human_turn, agent_turn],
                "current_turn_index": next_index + 2,
                "updated_at": now,
                "progress": {
                    **snapshot.public_view.progress,
                    "agent_response_count": int(
                        snapshot.public_view.progress.get("agent_response_count") or 0
                    )
                    + 1,
                    "therapist_memory_update_pending": 1 if is_deferred else 0,
                },
            }
        )
        next_private = PrivateTherapistState.model_validate(
            result.therapist_state or snapshot.private_therapist_state.model_dump()
        )
        session = snapshot.session.model_copy(
            update={
                "current_turn_index": next_index + 2,
                "updated_at": now,
                "model_config_id": result.model_config_id,
                "prompt_release_id": result.prompt_release_id,
                "metadata": {
                    **snapshot.session.metadata,
                    "latest_therapist_runtime_trace": result.runtime_trace,
                    "therapist_memory_update": {
                        "status": "pending" if is_deferred else "complete",
                        "update_id": deferred_update_id,
                    },
                },
            }
        )
        prior_sequence = snapshot.through_event_sequence
        events = [
            self._event(
                session.session_id,
                prior_sequence + 1,
                EventType.HUMAN_TURN_SUBMITTED,
                actor,
                payload={"turn": human_turn.model_dump(mode="json")},
                idempotency_key=idempotency_key,
            ),
            self._event(
                session.session_id,
                prior_sequence + 2,
                EventType.AGENT_TURN_GENERATED,
                ActorRole.THERAPIST_AGENT,
                payload={"turn": agent_turn.model_dump(mode="json")},
                retrieved_unit_ids=result.retrieved_unit_ids,
                model_config_id=result.model_config_id,
                prompt_release_id=result.prompt_release_id,
            ),
        ]
        if not is_deferred:
            events.append(
                self._event(
                    session.session_id,
                    prior_sequence + 3,
                    EventType.THERAPIST_MEMORY_UPDATED,
                    ActorRole.THERAPIST_AGENT,
                    payload={
                        "state_checksum": canonical_json_sha256(next_private.model_dump(mode="json"))
                    },
                )
            )
        next_snapshot = self._snapshot(
            session,
            public,
            snapshot.private_patient_state,
            next_private,
            events[-1].sequence,
        )
        response = {
            "schema_version": "theraskill_simulation_submit_turn_result_v1",
            "session_id": session.session_id,
            "human_turn": human_turn.model_dump(mode="json"),
            "agent_turn": agent_turn.model_dump(mode="json"),
            "snapshot_id": next_snapshot.snapshot_id,
            "through_event_sequence": next_snapshot.through_event_sequence,
            "deferred_update_id": deferred_update_id,
            "therapist_memory_update_pending": is_deferred,
        }
        self.repository.commit(
            next_snapshot,
            events,
            expected_prior_sequence=prior_sequence,
            idempotency_key=idempotency_key,
            idempotency_result=response,
        )
        return response

    def finalize_therapist_turn(
        self,
        session_id: str,
        *,
        deferred_update_id: str,
    ) -> None:
        snapshot = self.repository.get_latest_snapshot(session_id)
        update_meta = snapshot.session.metadata.get("therapist_memory_update") or {}
        if update_meta.get("update_id") != deferred_update_id or update_meta.get("status") != "pending":
            return
        if self.therapist_engine is None or not hasattr(self.therapist_engine, "finalize_turn"):
            raise SessionOperationError("deferred therapist memory engine is not configured")
        if snapshot.private_therapist_state is None:
            raise SessionOperationError("therapist private state is missing")
        foreground_trace = snapshot.session.metadata.get("latest_therapist_runtime_trace") or {}
        deferred_update = foreground_trace.get("deferred_update")
        if not isinstance(deferred_update, dict):
            raise SessionOperationError("deferred therapist memory payload is missing")
        try:
            result = self.therapist_engine.finalize_turn(
                private_state=snapshot.private_therapist_state,
                deferred_update=deferred_update,
                language=str(snapshot.session.language),
            )
        except Exception as exc:
            self._mark_therapist_update_failed(
                snapshot,
                deferred_update_id=deferred_update_id,
                error=f"{type(exc).__name__}: {exc}",
            )
            return

        now = utc_now()
        next_private = PrivateTherapistState.model_validate(
            result.therapist_state or snapshot.private_therapist_state.model_dump(mode="json")
        )
        public = snapshot.public_view.model_copy(
            update={
                "updated_at": now,
                "progress": {
                    **snapshot.public_view.progress,
                    "therapist_memory_update_pending": 0,
                },
            }
        )
        session = snapshot.session.model_copy(
            update={
                "updated_at": now,
                "metadata": {
                    **snapshot.session.metadata,
                    "latest_therapist_runtime_trace": {
                        **foreground_trace,
                        "deferred_update": None,
                        **result.runtime_trace,
                    },
                    "therapist_memory_update": {
                        "status": "complete",
                        "update_id": deferred_update_id,
                    },
                },
            }
        )
        prior_sequence = snapshot.through_event_sequence
        event = self._event(
            session_id,
            prior_sequence + 1,
            EventType.THERAPIST_MEMORY_UPDATED,
            ActorRole.THERAPIST_AGENT,
            payload={
                "status": "complete",
                "update_id": deferred_update_id,
                "state_checksum": canonical_json_sha256(next_private.model_dump(mode="json")),
            },
            model_config_id=result.model_config_id,
            prompt_release_id=result.prompt_release_id,
        )
        next_snapshot = self._snapshot(
            session,
            public,
            snapshot.private_patient_state,
            next_private,
            event.sequence,
        )
        self.repository.commit(
            next_snapshot,
            [event],
            expected_prior_sequence=prior_sequence,
        )

    def _mark_therapist_update_failed(
        self,
        snapshot: SessionSnapshot,
        *,
        deferred_update_id: str,
        error: str,
    ) -> None:
        now = utc_now()
        public = snapshot.public_view.model_copy(
            update={
                "updated_at": now,
                "progress": {
                    **snapshot.public_view.progress,
                    "therapist_memory_update_pending": 0,
                    "therapist_memory_update_failed": 1,
                },
            }
        )
        session = snapshot.session.model_copy(
            update={
                "updated_at": now,
                "metadata": {
                    **snapshot.session.metadata,
                    "therapist_memory_update": {
                        "status": "failed",
                        "update_id": deferred_update_id,
                        "error": error[:1000],
                    },
                },
            }
        )
        prior_sequence = snapshot.through_event_sequence
        event = self._event(
            session.session_id,
            prior_sequence + 1,
            EventType.THERAPIST_MEMORY_UPDATED,
            ActorRole.THERAPIST_AGENT,
            payload={"status": "failed", "update_id": deferred_update_id, "error": error[:1000]},
        )
        next_snapshot = self._snapshot(
            session,
            public,
            snapshot.private_patient_state,
            snapshot.private_therapist_state,
            event.sequence,
        )
        self.repository.commit(
            next_snapshot,
            [event],
            expected_prior_sequence=prior_sequence,
        )

    def run_dual_cycle(
        self,
        session_id: str,
        *,
        idempotency_key: str,
        initial_therapist_utterance: str | None = None,
    ) -> dict[str, Any]:
        cached = self.repository.get_idempotency_result(session_id, idempotency_key)
        if cached is not None:
            return cached
        snapshot = self.repository.get_latest_snapshot(session_id)
        session = snapshot.session
        if SessionMode(session.mode) != SessionMode.DUAL_AGENT_RESEARCH:
            raise SessionOperationError("run_dual_cycle requires dual_agent_research mode")
        if SessionStatus(session.status) != SessionStatus.ACTIVE:
            raise SessionOperationError(f"session is not active: {session.status}")
        if self.patient_engine is None or self.therapist_engine is None:
            raise SessionOperationError("patient and therapist engines are both required")
        if snapshot.private_patient_state is None or snapshot.private_therapist_state is None:
            raise SessionOperationError("dual private role state is incomplete")

        transcript = list(snapshot.public_view.transcript)
        new_turns: list[TranscriptTurn] = []
        current_index = snapshot.public_view.current_turn_index
        if initial_therapist_utterance:
            if transcript:
                raise SessionOperationError(
                    "initial therapist utterance is only allowed for the first dual cycle"
                )
            therapist_input = " ".join(initial_therapist_utterance.split())
            opening_turn = TranscriptTurn(
                turn_id=f"turn_{current_index + 1:04d}",
                turn_index=current_index + 1,
                role="therapist",
                text=therapist_input,
                created_at=utc_now(),
                metadata={"surface_role": "scenario_protocol_opening"},
            )
            new_turns.append(opening_turn)
            patient_history = transcript
        else:
            if not transcript or transcript[-1].role != "therapist":
                raise SessionOperationError(
                    "a later dual cycle requires the prior therapist continuation"
                )
            therapist_input = transcript[-1].text
            patient_history = transcript[:-1]

        scenario_row = self.scenario_registry.get(
            session.scenario.scenario_id,
            language=str(session.language),
        )
        patient_result = self.patient_engine.generate_turn(
            scenario=scenario_row,
            case_bundle=read_json(self._resolve_case_bundle_path(scenario_row["case_bundle_path"])),
            transcript=patient_history,
            private_state=snapshot.private_patient_state,
            therapist_utterance=therapist_input,
            language=str(session.language),
        )
        patient_index = current_index + len(new_turns) + 1
        patient_turn = TranscriptTurn(
            turn_id=f"turn_{patient_index:04d}",
            turn_index=patient_index,
            role="patient",
            text=patient_result.text,
            created_at=utc_now(),
        )
        new_turns.append(patient_turn)

        therapist_history = transcript + new_turns[:-1]
        therapist_result = self.therapist_engine.generate_turn(
            session_id=session_id,
            turn_index=patient_index + 1,
            scenario=scenario_row,
            transcript=therapist_history,
            private_state=snapshot.private_therapist_state,
            patient_utterance=patient_result.text,
            language=str(session.language),
        )
        therapist_turn = TranscriptTurn(
            turn_id=f"turn_{patient_index + 1:04d}",
            turn_index=patient_index + 1,
            role="therapist",
            text=therapist_result.text,
            created_at=utc_now(),
        )
        new_turns.append(therapist_turn)
        next_patient = patient_result.patient_state or snapshot.private_patient_state
        next_therapist = PrivateTherapistState.model_validate(
            therapist_result.therapist_state
            or snapshot.private_therapist_state.model_dump(mode="json")
        )
        now = utc_now()
        cycle_count = int(snapshot.public_view.progress.get("dual_cycle_count") or 0) + 1
        public = snapshot.public_view.model_copy(
            update={
                "transcript": transcript + new_turns,
                "current_turn_index": patient_index + 1,
                "updated_at": now,
                "progress": {
                    **snapshot.public_view.progress,
                    "dual_cycle_count": cycle_count,
                    "agent_response_count": cycle_count,
                },
            }
        )
        prior_traces = list(session.metadata.get("dual_cycle_traces") or [])
        session = session.model_copy(
            update={
                "current_turn_index": patient_index + 1,
                "updated_at": now,
                "model_config_id": therapist_result.model_config_id
                or patient_result.model_config_id,
                "prompt_release_id": "dual::patient+therapist::20260711",
                "metadata": {
                    **session.metadata,
                    "dual_cycle_traces": prior_traces
                    + [
                        {
                            "cycle": cycle_count,
                            "patient": patient_result.runtime_trace,
                            "therapist": therapist_result.runtime_trace,
                        }
                    ],
                },
            }
        )
        sequence = snapshot.through_event_sequence
        events: list[SessionEvent] = []
        if initial_therapist_utterance:
            sequence += 1
            events.append(
                self._event(
                    session_id,
                    sequence,
                    EventType.AGENT_TURN_GENERATED,
                    ActorRole.THERAPIST_AGENT,
                    payload={"turn": new_turns[0].model_dump(mode="json")},
                    prompt_release_id="scenario_protocol_opening_v1",
                )
            )
        sequence += 1
        events.append(
            self._event(
                session_id,
                sequence,
                EventType.AGENT_TURN_GENERATED,
                ActorRole.PATIENT_AGENT,
                payload={"turn": patient_turn.model_dump(mode="json")},
                retrieved_unit_ids=patient_result.retrieved_unit_ids,
                model_config_id=patient_result.model_config_id,
                prompt_release_id=patient_result.prompt_release_id,
            )
        )
        sequence += 1
        events.append(
            self._event(
                session_id,
                sequence,
                EventType.PATIENT_STATE_UPDATED,
                ActorRole.PATIENT_AGENT,
                payload={"state_checksum": canonical_json_sha256(next_patient.model_dump(mode="json"))},
            )
        )
        sequence += 1
        events.append(
            self._event(
                session_id,
                sequence,
                EventType.AGENT_TURN_GENERATED,
                ActorRole.THERAPIST_AGENT,
                payload={"turn": therapist_turn.model_dump(mode="json")},
                retrieved_unit_ids=therapist_result.retrieved_unit_ids,
                model_config_id=therapist_result.model_config_id,
                prompt_release_id=therapist_result.prompt_release_id,
            )
        )
        sequence += 1
        events.append(
            self._event(
                session_id,
                sequence,
                EventType.THERAPIST_MEMORY_UPDATED,
                ActorRole.THERAPIST_AGENT,
                payload={"state_checksum": canonical_json_sha256(next_therapist.model_dump(mode="json"))},
            )
        )
        next_snapshot = self._snapshot(
            session,
            public,
            next_patient,
            next_therapist,
            events[-1].sequence,
        )
        response = {
            "schema_version": "theraskill_simulation_dual_cycle_result_v1",
            "session_id": session_id,
            "cycle": cycle_count,
            "new_turns": [turn.model_dump(mode="json") for turn in new_turns],
            "snapshot_id": next_snapshot.snapshot_id,
            "through_event_sequence": next_snapshot.through_event_sequence,
        }
        self.repository.commit(
            next_snapshot,
            events,
            expected_prior_sequence=snapshot.through_event_sequence,
            idempotency_key=idempotency_key,
            idempotency_result=response,
        )
        return response

    def _change_status(
        self,
        session_id: str,
        target: SessionStatus,
        event_type: EventType,
        *,
        reason: str | None = None,
    ) -> SessionSnapshot:
        snapshot = self.repository.get_latest_snapshot(session_id)
        session = transition_session(snapshot.session, target)
        public = snapshot.public_view.model_copy(
            update={"status": target, "updated_at": session.updated_at}
        )
        event = self._event(
            session_id,
            snapshot.through_event_sequence + 1,
            event_type,
            ActorRole.SYSTEM,
            payload={"reason": reason} if reason else {},
        )
        next_snapshot = self._snapshot(
            session,
            public,
            snapshot.private_patient_state,
            snapshot.private_therapist_state,
            event.sequence,
        )
        self.repository.commit(
            next_snapshot,
            [event],
            expected_prior_sequence=snapshot.through_event_sequence,
        )
        return next_snapshot

    def pause_session(self, session_id: str) -> SessionSnapshot:
        self._assert_deferred_updates_ready(session_id)
        return self._change_status(
            session_id, SessionStatus.PAUSED, EventType.SESSION_PAUSED
        )

    def resume_session(self, session_id: str) -> SessionSnapshot:
        return self._change_status(
            session_id, SessionStatus.ACTIVE, EventType.SESSION_RESUMED
        )

    def complete_session(
        self, session_id: str, *, defer_review: bool = False
    ) -> SessionSnapshot:
        self._assert_deferred_updates_ready(session_id, allow_failed=True)
        snapshot = self.repository.get_latest_snapshot(session_id)
        runtime_trace = snapshot.session.metadata.get("latest_runtime_trace") or {}
        reviewer = ((runtime_trace.get("reviewer_result") or {}).get("result") or {})
        summary = ((runtime_trace.get("session_longitudinal_summary") or {}).get("result") or {})
        review_gate = reviewer.get("review_gate") or {}
        completed = self._change_status(
            session_id, SessionStatus.COMPLETED, EventType.SESSION_COMPLETED
        )
        if self._review_precondition(completed) is not None:
            return self.review_session(session_id)
        external_review = None
        if (
            defer_review
            and not (reviewer or summary)
            and self.reviewer_engine is not None
        ):
            return self._mark_review_pending(completed)
        if not (reviewer or summary) and self.reviewer_engine is not None:
            external_review = self.reviewer_engine.review(completed)
            reviewer = {"review_gate": {"gate_status": external_review.public_review.gate_status}}
            summary = external_review.public_review.summary
        if reviewer or summary:
            review_view = (
                external_review.public_review
                if external_review is not None
                else ReviewPublicView(
                    status="complete",
                    gate_status=review_gate.get("gate_status"),
                    summary=summary.get("session_longitudinal_summary") or summary,
                    findings=list(reviewer.get("findings") or []),
                )
            )
            public = completed.public_view.model_copy(
                update={
                    "review": review_view
                }
            )
            reviewed_session = completed.session
            if external_review is not None:
                reviewed_session = completed.session.model_copy(
                    update={
                        "model_config_id": external_review.model_config_id
                        or completed.session.model_config_id,
                        "metadata": {
                            **completed.session.metadata,
                            "latest_reviewer_runtime_trace": external_review.runtime_trace,
                        },
                    }
                )
            event = self._event(
                session_id,
                completed.through_event_sequence + 1,
                EventType.REVIEW_GENERATED,
                ActorRole.REVIEWER_AGENT,
                payload={"gate_status": review_view.gate_status},
                model_config_id=(external_review.model_config_id if external_review else None),
                prompt_release_id=(external_review.prompt_release_id if external_review else None),
            )
            reviewed_snapshot = self._snapshot(
                reviewed_session,
                public,
                completed.private_patient_state,
                completed.private_therapist_state,
                event.sequence,
            )
            self.repository.commit(
                reviewed_snapshot,
                [event],
                expected_prior_sequence=completed.through_event_sequence,
            )
            return reviewed_snapshot
        return completed

    def _mark_review_pending(self, snapshot: SessionSnapshot) -> SessionSnapshot:
        now = utc_now()
        public = snapshot.public_view.model_copy(
            update={
                "review": ReviewPublicView(
                    status="pending",
                    summary={},
                    findings=[],
                ),
                "updated_at": now,
            }
        )
        session = snapshot.session.model_copy(
            update={
                "updated_at": now,
                "metadata": {
                    **snapshot.session.metadata,
                    "review_generation": {"status": "pending"},
                },
            }
        )
        event = self._event(
            session.session_id,
            snapshot.through_event_sequence + 1,
            EventType.REVIEW_REQUESTED,
            ActorRole.SYSTEM,
        )
        next_snapshot = self._snapshot(
            session,
            public,
            snapshot.private_patient_state,
            snapshot.private_therapist_state,
            event.sequence,
        )
        self.repository.commit(
            next_snapshot,
            [event],
            expected_prior_sequence=snapshot.through_event_sequence,
        )
        self.repository.enqueue_review_job(snapshot.session.session_id)
        return next_snapshot

    def generate_pending_review(self, session_id: str, *, job_id: str | None = None) -> None:
        snapshot = self.repository.get_latest_snapshot(session_id)
        if snapshot.public_view.review is None:
            return
        if snapshot.public_view.review.status != "pending":
            if job_id:
                self.repository.finish_review_job(job_id, status="complete")
            return
        try:
            self.review_session(session_id)
            if job_id:
                self.repository.finish_review_job(job_id, status="complete")
        except Exception as exc:
            self._mark_review_failed(
                self.repository.get_latest_snapshot(session_id),
                error=f"{type(exc).__name__}: {exc}",
            )
            if job_id:
                self.repository.finish_review_job(
                    job_id, status="failed", error=f"{type(exc).__name__}: {exc}"
                )

    def process_one_pending_review(self) -> bool:
        jobs = self.repository.list_pending_review_jobs()
        if not jobs:
            return False
        job = self.repository.claim_review_job(str(jobs[0]["job_id"]))
        if job is None:
            return True
        self.generate_pending_review(str(job["session_id"]), job_id=str(job["job_id"]))
        return True

    def _mark_review_failed(self, snapshot: SessionSnapshot, *, error: str) -> None:
        now = utc_now()
        public = snapshot.public_view.model_copy(
            update={
                "review": ReviewPublicView(
                    status="failed",
                    summary={},
                    findings=[{"code": "review_generation_failed", "message": error[:1000]}],
                ),
                "updated_at": now,
            }
        )
        session = snapshot.session.model_copy(
            update={
                "updated_at": now,
                "metadata": {
                    **snapshot.session.metadata,
                    "review_generation": {
                        "status": "failed",
                        "error": error[:1000],
                    },
                },
            }
        )
        event = self._event(
            session.session_id,
            snapshot.through_event_sequence + 1,
            EventType.REVIEW_GENERATED,
            ActorRole.REVIEWER_AGENT,
            payload={"status": "failed", "error": error[:1000]},
        )
        next_snapshot = self._snapshot(
            session,
            public,
            snapshot.private_patient_state,
            snapshot.private_therapist_state,
            event.sequence,
        )
        self.repository.commit(
            next_snapshot,
            [event],
            expected_prior_sequence=snapshot.through_event_sequence,
        )

    def terminate_session(self, session_id: str, *, reason: str) -> SessionSnapshot:
        self._assert_deferred_updates_ready(session_id, allow_failed=True)
        return self._change_status(
            session_id,
            SessionStatus.TERMINATED,
            EventType.SESSION_TERMINATED,
            reason=reason,
        )

    def _assert_deferred_updates_ready(self, session_id: str, *, allow_failed: bool = False) -> None:
        snapshot = self.repository.get_latest_snapshot(session_id)
        update = snapshot.session.metadata.get("patient_state_update") or {}
        if update.get("status") == "pending":
            raise SessionOperationError("patient state update is still running")
        if update.get("status") == "failed" and not allow_failed:
            raise SessionOperationError("patient state update failed; session cannot continue")
        therapist_update = snapshot.session.metadata.get("therapist_memory_update") or {}
        if therapist_update.get("status") == "pending":
            raise SessionOperationError("therapist memory update is still running")
        if therapist_update.get("status") == "failed" and not allow_failed:
            raise SessionOperationError("therapist memory update failed; session cannot continue")

    @staticmethod
    def _review_precondition(snapshot: SessionSnapshot) -> ReviewPublicView | None:
        roles = {turn.role for turn in snapshot.public_view.transcript if turn.text.strip()}
        incomplete = bool(snapshot.session.metadata.get("review_source_incomplete_state")) or any(
            (snapshot.session.metadata.get(name) or {}).get("status") in {"failed", "pending"}
            for name in ("patient_state_update", "therapist_memory_update")
        )
        if {"patient", "therapist"} <= roles and not incomplete:
            return None
        return ReviewPublicView(
            status="not_evaluable",
            gate_status="incomplete_state" if incomplete else "insufficient_evidence",
            summary={"transcript_turn_count": len(snapshot.public_view.transcript)},
            findings=[{
                "code": "incomplete_state" if incomplete else "insufficient_evidence",
                "message": "State or memory update is incomplete. Retry the failed update before assessment."
                if incomplete else "A substantive patient turn and therapist turn are required for assessment.",
            }],
        )

    def retry_deferred_update(self, session_id: str) -> dict[str, str]:
        snapshot = self.repository.get_latest_snapshot(session_id)
        if snapshot.session.status not in {SessionStatus.ACTIVE, SessionStatus.PAUSED}:
            raise SessionOperationError("resume or continue an active session before retrying its update")
        for key, pending_key, failed_key, event_type in (
            ("patient_state_update", "state_update_pending", "state_update_failed", EventType.PATIENT_STATE_UPDATED),
            ("therapist_memory_update", "therapist_memory_update_pending", "therapist_memory_update_failed", EventType.THERAPIST_MEMORY_UPDATED),
        ):
            update = snapshot.session.metadata.get(key) or {}
            if update.get("status") == "pending":
                raise SessionOperationError("state update is already running")
            if update.get("status") != "failed":
                continue
            now = utc_now()
            next_update = {**update, "status": "pending", "error": None}
            session = snapshot.session.model_copy(update={"updated_at": now, "metadata": {**snapshot.session.metadata, key: next_update}})
            public = snapshot.public_view.model_copy(update={"updated_at": now, "progress": {**snapshot.public_view.progress, pending_key: 1, failed_key: 0}})
            event = self._event(session_id, snapshot.through_event_sequence + 1, event_type, ActorRole.SYSTEM,
                                payload={"status": "retry_requested", "update_id": update["update_id"]})
            restored = self._snapshot(session, public, snapshot.private_patient_state, snapshot.private_therapist_state, event.sequence)
            self.repository.commit(restored, [event], expected_prior_sequence=snapshot.through_event_sequence)
            return {"kind": key, "update_id": update["update_id"]}
        raise SessionOperationError("no failed update to retry")

    def retry_review(self, session_id: str) -> SessionSnapshot:
        snapshot = self.repository.get_latest_snapshot(session_id)
        if snapshot.session.status not in {SessionStatus.COMPLETED, SessionStatus.TERMINATED, SessionStatus.FAILED}:
            raise SessionOperationError("session must be terminal before review")
        if self._review_precondition(snapshot) is not None:
            return self.review_session(session_id)
        if snapshot.public_view.review and snapshot.public_view.review.status == "pending":
            return snapshot
        return self._mark_review_pending(snapshot)

    def review_session(self, session_id: str) -> SessionSnapshot:
        snapshot = self.repository.get_latest_snapshot(session_id)
        if SessionStatus(snapshot.session.status) not in {
            SessionStatus.COMPLETED,
            SessionStatus.TERMINATED,
            SessionStatus.FAILED,
        }:
            raise SessionOperationError("session must be terminal before review")
        precondition = self._review_precondition(snapshot)
        if precondition is not None:
            result = ReviewResult(public_review=precondition)
        else:
            if self.reviewer_engine is None:
                raise SessionOperationError("reviewer engine is not configured")
            result = self.reviewer_engine.review(snapshot)
        now = utc_now()
        public = snapshot.public_view.model_copy(
            update={"review": result.public_review, "updated_at": now}
        )
        session = snapshot.session.model_copy(
            update={
                "updated_at": now,
                "model_config_id": result.model_config_id or snapshot.session.model_config_id,
                "metadata": {
                    **snapshot.session.metadata,
                    "latest_reviewer_runtime_trace": result.runtime_trace,
                    "review_generation": {"status": "complete"},
                },
            }
        )
        event = self._event(
            session_id,
            snapshot.through_event_sequence + 1,
            EventType.REVIEW_GENERATED,
            ActorRole.REVIEWER_AGENT,
            payload={"gate_status": result.public_review.gate_status},
            model_config_id=result.model_config_id,
            prompt_release_id=result.prompt_release_id,
        )
        next_snapshot = self._snapshot(
            session,
            public,
            snapshot.private_patient_state,
            snapshot.private_therapist_state,
            event.sequence,
        )
        self.repository.commit(
            next_snapshot,
            [event],
            expected_prior_sequence=snapshot.through_event_sequence,
        )
        return next_snapshot

    def export_session(
        self, session_id: str, *, scope: ExportScope | str = ExportScope.PUBLIC
    ) -> dict[str, Any]:
        snapshot = self.repository.get_latest_snapshot(session_id)
        resolved_scope = ExportScope(scope)
        events = self.repository.list_events(session_id)
        payload = export_snapshot(snapshot, resolved_scope)
        payload["event_count"] = len(events)
        if resolved_scope == ExportScope.RESEARCHER:
            payload["events"] = [event.model_dump(mode="json") for event in events]
        persona_path = snapshot.session.metadata.get("patient_persona_v2_path")
        terminal = SessionStatus(snapshot.session.status) in {
            SessionStatus.COMPLETED,
            SessionStatus.TERMINATED,
            SessionStatus.FAILED,
        }
        if (
            resolved_scope in {ExportScope.INSTRUCTOR, ExportScope.RESEARCHER}
            and persona_path
            and (terminal or resolved_scope == ExportScope.RESEARCHER)
        ):
            resolved_persona_path = Path(str(persona_path))
            if resolved_persona_path.exists():
                persona = read_json(resolved_persona_path)
                payload["instructor_persona_reveal"] = self._instructor_persona_reveal(persona)
                if resolved_scope == ExportScope.RESEARCHER:
                    payload["reference_patient_persona"] = persona
        payload["export_checksum"] = canonical_json_sha256(payload)
        return payload

    def export_reviewer_runtime_report(self, session_id: str) -> dict[str, Any]:
        return build_reviewer_runtime_report(
            self.repository.get_latest_snapshot(session_id)
        )

    def submit_feedback(
        self,
        session_id: str,
        *,
        submitted_by_role: ActorRole | str,
        rating: int | None = None,
        dimension_ratings: dict[str, int] | None = None,
        comment: str | None = None,
        consent_to_research: bool = False,
        feedback_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionFeedback:
        if feedback_id:
            existing = self.repository.get_feedback(feedback_id)
            if existing:
                return existing
        snapshot = self.repository.get_latest_snapshot(session_id)
        if SessionStatus(snapshot.session.status) not in {
            SessionStatus.COMPLETED,
            SessionStatus.TERMINATED,
            SessionStatus.FAILED,
        }:
            raise SessionOperationError("feedback is accepted after the session is terminal")
        if (rating is not None or dimension_ratings) and self._review_precondition(snapshot) is not None:
            raise SessionOperationError("this session is not evaluable; submit a comment without training ratings")
        feedback = SessionFeedback(
            feedback_id=feedback_id or f"feedback_{uuid.uuid4().hex}",
            session_id=session_id,
            submitted_by_role=ActorRole(submitted_by_role),
            rating=rating,
            dimension_ratings=dimension_ratings or {},
            comment=comment,
            consent_to_research=consent_to_research,
            metadata=metadata or {},
        )
        return self.repository.add_feedback(feedback)

    def list_feedback(
        self, *, session_id: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        return [
            item.model_dump(mode="json")
            for item in self.repository.list_feedback(session_id=session_id, limit=limit)
        ]
