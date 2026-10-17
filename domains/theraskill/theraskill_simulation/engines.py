"""Runtime engine protocols and the existing LLM patient-runtime adapter."""

from __future__ import annotations

import sys
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .compatibility import build_reviewer_runtime_report
from .contracts import (
    PrivatePatientState,
    PrivateTherapistState,
    ReviewPublicView,
    SessionSnapshot,
    TranscriptTurn,
)
from .persona_compiler import PersonaCompiler, runtime_bundle_with_context


def _subprocess_environment(project_root: Path) -> dict[str, str]:
    """Keep the repository package importable in direct Python child processes."""
    environment = os.environ.copy()
    project_path = str(project_root)
    environment["PYTHONPATH"] = project_path + os.pathsep + environment.get("PYTHONPATH", "")
    return environment


@dataclass
class AgentTurnResult:
    text: str
    patient_state: PrivatePatientState | None = None
    therapist_state: dict[str, Any] | None = None
    runtime_trace: dict[str, Any] = field(default_factory=dict)
    retrieved_unit_ids: list[str] = field(default_factory=list)
    model_config_id: str | None = None
    prompt_release_id: str | None = None


class PatientEngine(Protocol):
    def generate_opening(
        self,
        *,
        scenario: dict[str, Any],
        case_bundle: dict[str, Any],
        transcript: list[TranscriptTurn],
        private_state: PrivatePatientState,
        language: str = "zh",
    ) -> AgentTurnResult: ...

    def generate_turn(
        self,
        *,
        scenario: dict[str, Any],
        case_bundle: dict[str, Any],
        transcript: list[TranscriptTurn],
        private_state: PrivatePatientState,
        therapist_utterance: str,
        language: str = "zh",
    ) -> AgentTurnResult: ...


class TherapistEngine(Protocol):
    def generate_turn(
        self,
        *,
        session_id: str,
        turn_index: int,
        scenario: dict[str, Any],
        transcript: list[TranscriptTurn],
        private_state: PrivateTherapistState,
        patient_utterance: str,
        language: str = "zh",
    ) -> AgentTurnResult: ...


@dataclass
class ReviewResult:
    public_review: ReviewPublicView
    runtime_trace: dict[str, Any] = field(default_factory=dict)
    model_config_id: str | None = None
    prompt_release_id: str | None = None


class ReviewerEngine(Protocol):
    def review(self, snapshot: SessionSnapshot) -> ReviewResult: ...


class ExistingLLMPatientEngine:
    """Adapt the released multi-stage patient pipeline for interactive turns."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        extraction_config: str | Path,
        model_config: str | Path,
        parse_retries: int | None = None,
        max_prompt_tokens: int | None = None,
        max_output_tokens: int | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        simulation_scripts = self.project_root / "scripts/Simulation"
        common_scripts = self.project_root / "scripts/_common"
        for path in (simulation_scripts, common_scripts):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))
        from run_patient_scenario_execution import load_runtime_stack

        self._load_runtime_stack = load_runtime_stack
        self.extraction_config = Path(extraction_config).resolve()
        self.model_config = Path(model_config).resolve()
        self.parse_retries = parse_retries
        self.max_prompt_tokens = max_prompt_tokens
        self.max_output_tokens = max_output_tokens
        self.runtime_stacks = {"zh": load_runtime_stack(
            extraction_config,
            model_config,
            self.project_root / "prompts/Simulation/session_longitudinal_summary_zh.md",
            self.project_root / "prompts/Simulation/patient_session_runtime_reviewer_zh.md",
            language="zh",
            parse_retries_override=parse_retries,
            max_prompt_tokens_override=max_prompt_tokens,
            max_output_tokens_override=max_output_tokens,
        )}
        self.runtime_stack = self.runtime_stacks["zh"]

    def _runtime_for(self, language: str) -> dict[str, Any]:
        resolved = language if language in {"zh", "en"} else "zh"
        if resolved not in self.runtime_stacks:
            self.runtime_stacks[resolved] = self._load_runtime_stack(
                self.extraction_config,
                self.model_config,
                self.project_root / "prompts/Simulation/session_longitudinal_summary_zh.md",
                self.project_root / "prompts/Simulation/patient_session_runtime_reviewer_zh.md",
                language=resolved,
                parse_retries_override=self.parse_retries,
                max_prompt_tokens_override=self.max_prompt_tokens,
                max_output_tokens_override=self.max_output_tokens,
            )
        return self.runtime_stacks[resolved]

    def generate_turn(
        self,
        *,
        scenario: dict[str, Any],
        case_bundle: dict[str, Any],
        transcript: list[TranscriptTurn],
        private_state: PrivatePatientState,
        therapist_utterance: str,
        language: str = "zh",
    ) -> AgentTurnResult:
        from patient_longitudinal_runtime import run_session

        transcript_seed = [
            {"role": row.role, "text": row.text}
            for row in transcript
            if row.role in {"patient", "therapist"}
        ]
        runtime = self._runtime_for(language)
        result = run_session(
            runtime["client"],
            runtime["prompts"],
            case_bundle,
            private_state.runtime_state,
            private_state.working_memory,
            transcript_seed,
            [therapist_utterance],
            parse_retries=int(runtime["parse_retries"]),
            max_prompt_tokens=int(runtime["max_prompt_tokens"]),
        )
        (
            next_state,
            next_memory,
            turn_records,
            next_transcript,
            step_audits,
            summary,
            reviewer,
        ) = result
        patient_text = ""
        for row in reversed(next_transcript):
            if row.get("role") == "patient":
                patient_text = str(row.get("text") or "")
                break
        if not patient_text:
            raise RuntimeError("released patient runtime returned no patient utterance")
        evidence_ids = list(private_state.evidence_unit_ids)
        for key in (
            "patient_protocol_unit_ids",
            "state_transition_hint_unit_ids",
            "reviewer_protocol_unit_ids",
        ):
            evidence_ids.extend((case_bundle.get("runtime_priors") or {}).get(key) or [])
        evidence_ids = list(dict.fromkeys(str(item) for item in evidence_ids if item))
        next_private = private_state.model_copy(
            update={
                "runtime_state": next_state,
                "working_memory": next_memory,
                "trust_alliance_readiness": {
                    key: next_state.get(key)
                    for key in (
                        "alliance_state",
                        "disclosure_state",
                        "readiness_state",
                        "resistance_state",
                        "felt_understanding",
                    )
                },
                "evidence_unit_ids": evidence_ids,
            }
        )
        return AgentTurnResult(
            text=patient_text,
            patient_state=next_private,
            runtime_trace={
                "turn_records": turn_records,
                "step_prompt_audits": step_audits,
                "session_longitudinal_summary": summary,
                "reviewer_result": reviewer,
            },
            retrieved_unit_ids=evidence_ids,
            model_config_id=(runtime.get("model_config_meta") or {}).get("model"),
            prompt_release_id="released_patient_multistage_prompts_20260711",
        )


class DeferredLLMPatientEngine:
    """Return patient speech before deferred state and memory writeback finishes."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        extraction_config: str | Path,
        model_config: str | Path,
        parse_retries: int = 1,
        max_prompt_tokens: int = 12000,
        max_output_tokens: int = 1800,
        fast_mode: bool = True,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.extraction_config = Path(extraction_config).resolve()
        self.model_config = Path(model_config).resolve()
        self.parse_retries = parse_retries
        self.max_prompt_tokens = max_prompt_tokens
        self.max_output_tokens = max_output_tokens
        self.fast_mode = fast_mode
        simulation_scripts = self.project_root / "scripts/Simulation"
        common_scripts = self.project_root / "scripts/_common"
        for path in (simulation_scripts, common_scripts):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))
        from run_patient_scenario_execution import load_runtime_stack

        self._load_runtime_stack = load_runtime_stack
        self.runtime_stacks = {"zh": load_runtime_stack(
            extraction_config,
            model_config,
            self.project_root / "prompts/Simulation/session_longitudinal_summary_zh.md",
            self.project_root / "prompts/Simulation/patient_session_runtime_reviewer_zh.md",
            language="zh",
            parse_retries_override=parse_retries,
            max_prompt_tokens_override=max_prompt_tokens,
            max_output_tokens_override=max_output_tokens,
        )}
        self.opening_prompts = {
            "zh": (self.project_root / "prompts/Simulation/patient_opening_zh.md").read_text(encoding="utf-8")
        }
        self.fast_turn_prompts = {
            "zh": (self.project_root / "prompts/Simulation/patient_fast_turn_zh.md").read_text(encoding="utf-8"),
        }
        self.persona_compiler = PersonaCompiler()

    def _runtime_for(self, language: str) -> dict[str, Any]:
        resolved = language if language in {"zh", "en"} else "zh"
        if resolved not in self.runtime_stacks:
            self.runtime_stacks[resolved] = self._load_runtime_stack(
                self.extraction_config,
                self.model_config,
                self.project_root / "prompts/Simulation/session_longitudinal_summary_zh.md",
                self.project_root / "prompts/Simulation/patient_session_runtime_reviewer_zh.md",
                language=resolved,
                parse_retries_override=self.parse_retries,
                max_prompt_tokens_override=self.max_prompt_tokens,
                max_output_tokens_override=self.max_output_tokens,
            )
        return self.runtime_stacks[resolved]

    def _opening_for(self, language: str) -> str:
        resolved = language if language in {"zh", "en"} else "zh"
        if resolved not in self.opening_prompts:
            path = self.project_root / "prompts/Simulation" / f"patient_opening_{resolved}.md"
            if not path.exists():
                path = self.project_root / "prompts/Simulation/patient_opening_zh.md"
            self.opening_prompts[resolved] = path.read_text(encoding="utf-8")
        return self.opening_prompts[resolved]

    def _fast_turn_prompt_for(self, language: str) -> str:
        resolved = language if language in {"zh", "en"} else "zh"
        if resolved not in self.fast_turn_prompts:
            path = self.project_root / "prompts/Simulation" / f"patient_fast_turn_{resolved}.md"
            if not path.exists():
                path = self.project_root / "prompts/Simulation/patient_fast_turn_zh.md"
            self.fast_turn_prompts[resolved] = path.read_text(encoding="utf-8")
        return self.fast_turn_prompts[resolved]

    @staticmethod
    def _evidence_ids(
        private_state: PrivatePatientState, case_bundle: dict[str, Any]
    ) -> list[str]:
        evidence_ids = list(private_state.evidence_unit_ids)
        for key in (
            "patient_protocol_unit_ids",
            "state_transition_hint_unit_ids",
            "reviewer_protocol_unit_ids",
        ):
            evidence_ids.extend((case_bundle.get("runtime_priors") or {}).get(key) or [])
        return list(dict.fromkeys(str(item) for item in evidence_ids if item))

    @staticmethod
    def _enforce_disclosure_contract(disclosure_result: dict[str, Any], compiled: Any) -> None:
        decision = disclosure_result.setdefault("disclosure_decision", {})
        allowed_nodes = compiled.runtime_context.get("allowed_fact_nodes") or []
        allowed_by_id = {str(node.get("fact_id")): node for node in allowed_nodes if node.get("fact_id")}
        allowed_by_content = {
            str(node.get("content")): str(node.get("fact_id"))
            for node in allowed_nodes
            if node.get("content") and node.get("fact_id")
        }
        requested_ids = [str(item) for item in decision.get("disclose_fact_ids") or []]
        requested_facts = [str(item) for item in decision.get("disclose_facts") or []]
        mapped_ids = [allowed_by_content[item] for item in requested_facts if item in allowed_by_content]
        valid_ids = list(dict.fromkeys(item for item in [*requested_ids, *mapped_ids] if item in allowed_by_id))
        invalid_ids = [item for item in requested_ids if item not in allowed_by_id]
        invalid_facts = [item for item in requested_facts if item not in allowed_by_content]
        decision["disclose_fact_ids"] = valid_ids
        decision["disclose_facts"] = [str(allowed_by_id[item]["content"]) for item in valid_ids]
        decision["keep_withheld_fact_ids"] = list(compiled.forbidden_fact_ids)
        disclosure_result["contract_validation"] = {
            "invalid_requested_fact_ids": invalid_ids,
            "invalid_requested_fact_contents": invalid_facts,
            "context_firewall_pass": not invalid_ids and not invalid_facts,
        }

    @staticmethod
    def _response_contract(
        turn_result: dict[str, Any],
        disclosure_result: dict[str, Any],
        compiled: Any | None,
    ) -> dict[str, Any]:
        decision = disclosure_result.get("disclosure_decision") or {}
        allowed = set(compiled.allowed_fact_ids if compiled else [])
        requested_used = [str(item) for item in turn_result.get("used_fact_ids") or decision.get("disclose_fact_ids") or []]
        used = list(dict.fromkeys(item for item in requested_used if not compiled or item in allowed))
        requested_principles = [str(item) for item in turn_result.get("applied_principle_ids") or []]
        active_principles = set(compiled.active_principle_ids if compiled else [])
        applied = list(
            dict.fromkeys(
                item for item in requested_principles if not compiled or item in active_principles
            )
        )
        plan = turn_result.get("patient_turn_plan") or {}
        validation = disclosure_result.get("contract_validation") or {}
        flags = []
        if validation.get("invalid_requested_fact_ids") or validation.get("invalid_requested_fact_contents"):
            flags.append("disclosure_controller_requested_fact_outside_compiled_context")
        if compiled and set(requested_used) - allowed:
            flags.append("patient_realizer_reported_fact_outside_compiled_context")
        return {
            "used_fact_ids": used,
            "applied_principle_ids": applied,
            "realized_disclosure_level": plan.get("disclosure_mode") or decision.get("disclosure_depth") or "unknown",
            "surface_emotion": plan.get("dominant_emotion") or [],
            "withheld_fact_ids": list(compiled.forbidden_fact_ids if compiled else []),
            "uncertainty_or_hallucination_flags": flags,
        }

    def generate_opening(
        self,
        *,
        scenario: dict[str, Any],
        case_bundle: dict[str, Any],
        transcript: list[TranscriptTurn],
        private_state: PrivatePatientState,
        language: str = "zh",
    ) -> AgentTurnResult:
        from run_patient_single_session_loop import (
            run_json_step,
            trim_case_bundle_for_runtime,
        )
        runtime = self._runtime_for(language)
        opening_prompt = self._opening_for(language)
        persona = case_bundle.get("_patient_persona_v2")
        compiled = self.persona_compiler.compile_for_move(persona) if isinstance(persona, dict) else None
        runtime_bundle = runtime_bundle_with_context(case_bundle, compiled) if compiled else case_bundle
        payload = {
            "task": "generate_patient_opening",
            "scenario": {
                key: scenario.get(key)
                for key in (
                    "scenario_id",
                    "scenario_family",
                    "case_family",
                    "difficulty_band",
                    "risk_band",
                    "scenario_brief",
                    "scenario_tags",
                )
            },
            "patient_case_bundle": trim_case_bundle_for_runtime(runtime_bundle),
            "current_session_state": private_state.runtime_state,
            "patient_state_memory": private_state.working_memory,
        }
        started = time.monotonic()
        result, prompt_audit = run_json_step(
            runtime["client"],
            opening_prompt,
            payload,
            parse_retries=int(runtime["parse_retries"]),
            max_prompt_tokens=int(runtime["max_prompt_tokens"]),
        )
        patient_text = " ".join(str(result.get("patient_utterance") or "").split())
        if not patient_text:
            raise RuntimeError("patient opening runtime returned no patient utterance")
        evidence_ids = self._evidence_ids(private_state, case_bundle)
        return AgentTurnResult(
            text=patient_text,
            patient_state=private_state,
            runtime_trace={
                "runtime_mode": "patient_opening_v1",
                "latency_seconds": round(time.monotonic() - started, 3),
                "prompt_audit": prompt_audit,
                "evidence_basis": result.get("evidence_basis") or [],
                "persona_compiler": compiled.decision_trace if compiled else {"policy": "legacy_v1_bundle"},
                "response_contract": {
                    "used_fact_ids": [],
                    "applied_principle_ids": [],
                    "realized_disclosure_level": "surface",
                    "withheld_fact_ids": compiled.forbidden_fact_ids if compiled else [],
                    "uncertainty_or_hallucination_flags": [],
                },
            },
            retrieved_unit_ids=evidence_ids,
            model_config_id=(runtime.get("model_config_meta") or {}).get("model"),
            prompt_release_id="patient_opening_v1",
        )

    def generate_turn(
        self,
        *,
        scenario: dict[str, Any],
        case_bundle: dict[str, Any],
        transcript: list[TranscriptTurn],
        private_state: PrivatePatientState,
        therapist_utterance: str,
        language: str = "zh",
    ) -> AgentTurnResult:
        from run_patient_single_session_loop import (
            build_disclosure_payload,
            build_move_payload,
            build_turn_payload,
            run_json_step,
        )
        runtime = self._runtime_for(language)
        if self.fast_mode:
            return self._generate_fast_turn(
                scenario=scenario,
                case_bundle=case_bundle,
                transcript=transcript,
                private_state=private_state,
                therapist_utterance=therapist_utterance,
                language=language,
                runtime=runtime,
            )
        transcript_rows = [
            {"role": row.role, "text": row.text}
            for row in transcript
            if row.role in {"patient", "therapist"}
        ]
        started = time.monotonic()
        persona = case_bundle.get("_patient_persona_v2")
        move_compiled = self.persona_compiler.compile_for_move(persona) if isinstance(persona, dict) else None
        move_bundle = runtime_bundle_with_context(case_bundle, move_compiled) if move_compiled else case_bundle
        move_result, move_audit = run_json_step(
            runtime["client"],
            runtime["prompts"]["move"],
            build_move_payload(
                move_bundle, private_state.runtime_state, transcript_rows, therapist_utterance
            ),
            parse_retries=int(runtime["parse_retries"]),
            max_prompt_tokens=int(runtime["max_prompt_tokens"]),
        )
        turn_compiled = (
            self.persona_compiler.compile_for_turn(
                persona,
                private_state.runtime_state,
                private_state.disclosed_fact_ids,
                move_result,
            )
            if isinstance(persona, dict)
            else None
        )
        turn_bundle = runtime_bundle_with_context(case_bundle, turn_compiled) if turn_compiled else case_bundle
        disclosure_result, disclosure_audit = run_json_step(
            runtime["client"],
            runtime["prompts"]["disclosure"],
            build_disclosure_payload(
                turn_bundle,
                private_state.runtime_state,
                private_state.working_memory,
                transcript_rows,
                therapist_utterance,
                move_result,
            ),
            parse_retries=int(runtime["parse_retries"]),
            max_prompt_tokens=int(runtime["max_prompt_tokens"]),
        )
        if turn_compiled:
            self._enforce_disclosure_contract(disclosure_result, turn_compiled)
        turn_result, turn_audit = run_json_step(
            runtime["client"],
            runtime["prompts"]["turn"],
            build_turn_payload(
                turn_bundle,
                private_state.runtime_state,
                private_state.working_memory,
                transcript_rows,
                therapist_utterance,
                disclosure_result,
            ),
            parse_retries=int(runtime["parse_retries"]),
            max_prompt_tokens=int(runtime["max_prompt_tokens"]),
        )
        patient_text = " ".join(
            str(turn_result.get("patient_utterance") or "").split()
        )
        if not patient_text:
            raise RuntimeError("deferred patient runtime returned no patient utterance")
        evidence_ids = self._evidence_ids(private_state, case_bundle)
        response_contract = self._response_contract(turn_result, disclosure_result, turn_compiled)
        next_private = private_state.model_copy(
            update={
                "disclosed_fact_ids": list(
                    dict.fromkeys([*private_state.disclosed_fact_ids, *response_contract["used_fact_ids"]])
                )
            }
        )
        deferred_update_id = f"patient-update-{time.time_ns()}"
        return AgentTurnResult(
            text=patient_text,
            patient_state=next_private,
            runtime_trace={
                "runtime_mode": "foreground_response_deferred_state_v1",
                "foreground_latency_seconds": round(time.monotonic() - started, 3),
                "deferred_update_id": deferred_update_id,
                "deferred_update": {
                    "therapist_utterance": therapist_utterance,
                    "therapist_move_interpretation": move_result,
                    "disclosure_decision": disclosure_result,
                    "patient_turn_result": turn_result,
                    "foreground_prompt_audits": {
                        "move": move_audit,
                        "disclosure": disclosure_audit,
                        "turn": turn_audit,
                    },
                    "persona_compiler_trace": turn_compiled.decision_trace if turn_compiled else {"policy": "legacy_v1_bundle"},
                    "response_contract": response_contract,
                },
                "persona_compiler": turn_compiled.decision_trace if turn_compiled else {"policy": "legacy_v1_bundle"},
                "response_contract": response_contract,
            },
            retrieved_unit_ids=evidence_ids,
            model_config_id=(runtime.get("model_config_meta") or {}).get("model"),
            prompt_release_id="patient_foreground_response_v1",
        )

    def _generate_fast_turn(
        self,
        *,
        scenario: dict[str, Any],
        case_bundle: dict[str, Any],
        transcript: list[TranscriptTurn],
        private_state: PrivatePatientState,
        therapist_utterance: str,
        language: str,
        runtime: dict[str, Any],
    ) -> AgentTurnResult:
        from run_patient_single_session_loop import run_json_step, trim_case_bundle_for_runtime

        started = time.monotonic()
        persona = case_bundle.get("_patient_persona_v2")
        compiled = None
        if isinstance(persona, dict):
            compiled = self.persona_compiler.compile_for_turn(
                persona,
                private_state.runtime_state,
                private_state.disclosed_fact_ids,
                {"validation_signal": "unknown", "challenge_level": "low"},
            )
        runtime_bundle = runtime_bundle_with_context(case_bundle, compiled) if compiled else case_bundle
        payload = {
            "scenario": {key: scenario.get(key) for key in (
                "scenario_id", "scenario_family", "case_family", "difficulty_band",
                "risk_band", "scenario_brief", "scenario_tags",
            )},
            "patient_case_bundle": trim_case_bundle_for_runtime(runtime_bundle),
            "current_session_state": private_state.runtime_state,
            "patient_state_memory": private_state.working_memory,
            "recent_transcript": [{"role": row.role, "text": row.text} for row in transcript[-6:]],
            "therapist_utterance": therapist_utterance,
        }
        result, prompt_audit = run_json_step(
            runtime["client"],
            self._fast_turn_prompt_for(language),
            payload,
            parse_retries=0,
            max_prompt_tokens=int(runtime["max_prompt_tokens"]),
        )
        patient_text = " ".join(str(result.get("patient_utterance") or "").split())
        if not patient_text:
            raise RuntimeError("fast patient runtime returned no patient utterance")
        plan = result.get("patient_turn_plan") or {}
        hidden_delta = result.get("hidden_state_delta") or {}
        next_private = private_state.model_copy(update={
            "disclosed_fact_ids": list(dict.fromkeys([
                *private_state.disclosed_fact_ids,
                *[str(item) for item in (result.get("used_fact_ids") or []) if item],
            ])),
        })
        evidence_ids = self._evidence_ids(private_state, case_bundle)
        return AgentTurnResult(
            text=patient_text,
            patient_state=next_private,
            runtime_trace={
                "runtime_mode": "patient_fast_response_v1",
                "fast_mode": True,
                "latency_seconds": round(time.monotonic() - started, 3),
                "prompt_audit": prompt_audit,
                "response_contract": {
                    "used_fact_ids": [str(item) for item in result.get("used_fact_ids") or [] if item],
                    "applied_principle_ids": [],
                    "realized_disclosure_level": plan.get("disclosure_mode") or "unknown",
                    "withheld_fact_ids": compiled.forbidden_fact_ids if compiled else [],
                    "uncertainty_or_hallucination_flags": [],
                },
                "hidden_state_delta": hidden_delta,
            },
            retrieved_unit_ids=evidence_ids,
            model_config_id=(runtime.get("model_config_meta") or {}).get("model"),
            prompt_release_id="patient_fast_response_v1",
        )

    def finalize_turn(
        self,
        *,
        case_bundle: dict[str, Any],
        transcript: list[TranscriptTurn],
        private_state: PrivatePatientState,
        deferred_update: dict[str, Any],
        language: str = "zh",
    ) -> AgentTurnResult:
        from run_patient_single_session_loop import (
            apply_memory_update_result,
            build_memory_update_payload,
            build_state_payload,
            calibrate_state_result_risk,
            run_json_step,
        )
        runtime = self._runtime_for(language)
        started = time.monotonic()
        therapist_utterance = str(deferred_update["therapist_utterance"])
        move_result = deferred_update.get("therapist_move_interpretation") or {}
        disclosure_result = deferred_update.get("disclosure_decision") or {}
        turn_result = deferred_update.get("patient_turn_result") or {}
        persona = case_bundle.get("_patient_persona_v2")
        compiled = (
            self.persona_compiler.compile_for_turn(
                persona,
                private_state.runtime_state,
                private_state.disclosed_fact_ids,
                move_result,
            )
            if isinstance(persona, dict)
            else None
        )
        runtime_bundle = runtime_bundle_with_context(case_bundle, compiled) if compiled else case_bundle
        state_result, state_audit = run_json_step(
            runtime["client"],
            runtime["prompts"]["state"],
            build_state_payload(
                runtime_bundle,
                private_state.runtime_state,
                private_state.working_memory,
                therapist_utterance,
                move_result,
                disclosure_result,
                turn_result,
            ),
            parse_retries=int(runtime["parse_retries"]),
            max_prompt_tokens=int(runtime["max_prompt_tokens"]),
        )
        state_result, guardrail_applied = calibrate_state_result_risk(
            private_state.runtime_state,
            therapist_utterance,
            disclosure_result,
            turn_result,
            state_result,
        )
        next_state = state_result.get("updated_session_state") or private_state.runtime_state
        transcript_rows = [
            {"role": row.role, "text": row.text}
            for row in transcript
            if row.role in {"patient", "therapist"}
        ]
        memory_result, memory_audit = run_json_step(
            runtime["client"],
            runtime["prompts"]["memory"],
            build_memory_update_payload(
                runtime_bundle,
                private_state.working_memory,
                transcript_rows,
                therapist_utterance,
                str(turn_result.get("patient_utterance") or ""),
                move_result,
                disclosure_result,
                state_result,
            ),
            parse_retries=int(runtime["parse_retries"]),
            max_prompt_tokens=int(runtime["max_prompt_tokens"]),
        )
        next_memory, memory_fallback_used = apply_memory_update_result(
            private_state.working_memory, state_result, memory_result
        )
        if private_state.persona_id:
            next_memory["schema_version"] = "theraskill_patient_conversation_memory_v1"
            next_memory["source_seed_policy"] = "withheld_by_persona_context_firewall"
            next_memory["disclosed_fact_ids"] = list(private_state.disclosed_fact_ids)
        next_private = private_state.model_copy(
            update={
                "runtime_state": next_state,
                "working_memory": next_memory,
                "trust_alliance_readiness": {
                    key: next_state.get(key)
                    for key in (
                        "alliance_state",
                        "disclosure_state",
                        "readiness_state",
                        "resistance_state",
                        "felt_understanding",
                    )
                },
            }
        )
        return AgentTurnResult(
            text="",
            patient_state=next_private,
            runtime_trace={
                "deferred_latency_seconds": round(time.monotonic() - started, 3),
                "state_update_result": state_result,
                "memory_update_result": memory_result,
                "memory_update_fallback_used": memory_fallback_used,
                "risk_guardrail_applied": guardrail_applied,
                "deferred_prompt_audits": {
                    "state": state_audit,
                    "memory": memory_audit,
                },
            },
            model_config_id=(runtime.get("model_config_meta") or {}).get("model"),
            prompt_release_id="patient_deferred_state_memory_v1",
        )


class CommandBackedLLMTherapistEngine:
    """Call the released therapist memory + turn pipeline behind the service boundary."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        extraction_config: str | Path,
        model_config: str | Path,
        work_root: str | Path,
        persona_tag: str = "情绪",
        memory_group_id: str = "target::01_1_to_5_history_pairs",
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.extraction_config = Path(extraction_config).resolve()
        self.model_config = Path(model_config).resolve()
        self.work_root = Path(work_root).resolve()
        self.persona_tag = persona_tag
        self.memory_group_id = memory_group_id
        self.adapter_script = (
            self.project_root
            / "scripts/Simulation/run_therapist_simulator_hermes_adapter.py"
        )

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _ensure_therapist_surfaces(
        self,
        turn_root: Path,
        scenario: dict[str, Any],
        patient_utterance: str,
    ) -> dict[str, Path]:
        """Use released therapist evidence when available, otherwise create a bounded runtime seed.

        The installed release keeps reviewed knowledge in a compact registry and does not ship the
        development-time CPsyCounD JSONL shards. Client-role training still needs a small, explicit
        evidence bundle, so the adapter can run without depending on checkout-local build artifacts.
        """
        units_root = self.project_root / "outputs/knowledge/units"
        defaults = {
            "persona_jsonl": units_root / "PsyDTCorpus.persona_pattern_bundle.jsonl",
            "memory_jsonl": units_root / "SoulChat-R1.memory_pattern_bundle.jsonl",
            "trajectory_jsonl": units_root / "CPsyCounD.case_trajectory.jsonl",
            "formulation_jsonl": units_root / "CPsyCounD.case_formulation_pattern.jsonl",
            "action_sequence_jsonl": units_root / "CPsyCounD.action_sequence_pattern.jsonl",
        }
        if all(path.exists() for path in defaults.values()):
            return {}

        root = turn_root / "therapist_surfaces"
        root.mkdir(parents=True, exist_ok=True)
        scenario_id = str(scenario.get("scenario_id") or "runtime_scenario")
        source_id = f"runtime::{scenario_id}"
        case_id = str(scenario.get("case_id") or scenario_id)
        brief = " ".join(str(scenario.get("scenario_brief") or "当前训练场景").split())
        utterance = " ".join(str(patient_utterance or "").split())

        def write_rows(name: str, rows: list[dict[str, Any]]) -> Path:
            path = root / name
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )
            return path

        source = {"source_id": source_id, "case_id": case_id, "dataset": "runtime_scenario_seed"}
        paths = {
            "persona_jsonl": write_rows("persona.jsonl", [{
                "unit_id": f"runtime_persona_{scenario_id}",
                "knowledge_type": "therapist_persona",
                "source": {"tag": self.persona_tag},
                "content": {
                    "role": "支持性探索训练中的治疗师",
                    "principles": ["先承接当前困扰", "使用清晰开放的澄清", "避免诊断化和过度承诺"],
                },
            }]),
            "memory_jsonl": write_rows("memory.jsonl", [{
                "unit_id": f"runtime_memory_{scenario_id}",
                "knowledge_type": "memory_pattern",
                "source": {"group_id": self.memory_group_id},
                "content": {
                    "patterns": ["以当前来访者话语为锚点，先承接再澄清。"],
                    "boundary": "不以泛化案例证据覆盖当前会话事实",
                },
            }]),
            "trajectory_jsonl": write_rows("trajectory.jsonl", [{
                "unit_id": f"runtime_trajectory_{scenario_id}",
                "knowledge_type": "case_trajectory",
                "source": source,
                "content": {"case_trajectory": {
                    "overall_process_summary": f"围绕{brief}进行早期支持性探索。",
                    "main_move_sequence": [{
                        "order": 1,
                        "move_types": ["validation", "open_question"],
                        "move_functions": ["承接情绪", "澄清当前困扰"],
                        "process_role": "engagement",
                    }],
                    "turning_points": [],
                }},
            }]),
            "formulation_jsonl": write_rows("formulation.jsonl", [{
                "unit_id": f"runtime_formulation_{scenario_id}",
                "knowledge_type": "case_formulation_pattern",
                "source": source,
                "content": {"case_formulation_pattern": {
                    "presenting_problem": [brief],
                    "maintaining_factors": [],
                    "protective_factors": [],
                    "client_goals": [],
                    "suggested_focus": ["澄清当前情绪和具体情境"],
                }},
            }]),
            "action_sequence_jsonl": write_rows("action_sequence.jsonl", [{
                "unit_id": f"runtime_actions_{scenario_id}",
                "knowledge_type": "action_sequence_pattern",
                "source": source,
                "content": {"action_sequence_pattern": {"action_sequences": [{
                    "sequence_name": "runtime_supportive_exploration",
                    "intended_process_goal": "建立安全感并澄清当前困扰",
                    "move_sequence": [{
                        "order": 1,
                        "move_types": ["validation", "open_question"],
                        "move_functions": ["承接", "澄清"],
                        "surface_summary": utterance[:160],
                    }],
                    "observed_client_changes": [],
                }]}},
            }]),
        }
        return paths

    def generate_turn(
        self,
        *,
        session_id: str,
        turn_index: int,
        scenario: dict[str, Any],
        transcript: list[TranscriptTurn],
        private_state: PrivateTherapistState,
        patient_utterance: str,
        language: str = "zh",
    ) -> AgentTurnResult:
        turn_root = self.work_root / session_id / f"turn_{turn_index:04d}"
        runtime_path = turn_root / "platform_runtime_input.json"
        transcript_rows = [
            {"role": row.role, "text": row.text}
            for row in transcript
            if row.role in {"patient", "therapist"}
        ] + [{"role": "patient", "text": patient_utterance}]
        entry_state = dict(scenario.get("entry_state") or {})
        patient_texts = [
            row["text"] for row in transcript_rows if row.get("role") == "patient"
        ]
        patient_memory = {
            "demographics_if_known": [],
            "presenting_problems": list(entry_state.get("topic_focus") or []),
            "current_emotion": list(entry_state.get("emotion_state") or []),
            "current_cognition": patient_texts[-3:],
            "current_interpersonal_concerns": list(
                entry_state.get("unfinished_threads") or []
            ),
            "alliance_state": entry_state.get("alliance_state") or "forming",
            "disclosure_depth": entry_state.get("disclosure_state") or "surface",
            "resistance_signals": [],
            "readiness_level": entry_state.get("readiness_state")
            or "ready_for_guided_exploration",
            "risk_state": {
                "level": entry_state.get("risk_state") or scenario.get("risk_band") or "unknown",
                "signals": [],
                "change": "unknown",
            },
            "explicit_goals": [],
            "hidden_concerns": [],
            "salient_prior_events": [],
            "unresolved_threads": list(entry_state.get("unfinished_threads") or []),
            "last_therapist_move_impact": [],
            "memory_confidence": "medium",
        }
        runtime_report = {
            "schema_version": "theraskill_simulation_platform_therapist_input_v1",
            "status": "active",
            "scenario": scenario,
            "transcript": transcript_rows,
            "turn_records": [],
            "final_session_state": entry_state,
            "final_memory_seed": patient_memory,
        }
        self._write_json(runtime_path, runtime_report)
        memory_seed_path = turn_root / "therapist_memory_seed.json"
        memory_seed = private_state.case_formulation_memory or {
            "working_formulation": [
                f"当前主要议题：{item}"
                for item in patient_memory.get("presenting_problems", [])[:3]
            ],
            "stage_hypothesis": "engagement",
            "top_priorities": ["承接当前困扰", "澄清具体情境", "维持非评判性氛围"],
            "ruled_out_or_deferred_paths": ["暂不做过度诊断", "暂不推动过快行动承诺"],
            "selected_response_strategy": ["validation", "open_question"],
            "rationale_summary": ["首轮先建立联盟并基于当前话语继续探索。"],
            "resource_map": {},
            "risk_watchpoints": [],
            "progress_markers": [],
            "repair_needs": [],
            "memory_confidence": "low",
        }
        self._write_json(
            memory_seed_path,
            {
                "schema_version": "theraskill_therapist_case_memory_seed_v1",
                "status": "complete",
                "result": {"therapist_case_memory": memory_seed},
                "input_summary": {},
            },
        )
        surface_paths = self._ensure_therapist_surfaces(turn_root, scenario, patient_utterance)
        command = [
            sys.executable,
            str(self.adapter_script),
            "run-turn",
            "--runtime-report",
            str(runtime_path),
            "--extraction-config",
            str(self.extraction_config),
            "--model-config",
            str(self.model_config),
            "--output-root",
            str(turn_root / "released_therapist_runtime"),
            "--persona-tag",
            self.persona_tag,
            "--memory-group-id",
            self.memory_group_id,
            "--therapist-memory-seed-report",
            str(memory_seed_path),
            "--language",
            str(language),
            "--overwrite",
        ]
        for flag, key in (
            ("--persona-jsonl", "persona_jsonl"),
            ("--memory-jsonl", "memory_jsonl"),
            ("--trajectory-jsonl", "trajectory_jsonl"),
            ("--formulation-jsonl", "formulation_jsonl"),
            ("--action-sequence-jsonl", "action_sequence_jsonl"),
        ):
            if key in surface_paths:
                command.extend([flag, str(surface_paths[key])])
        started = time.monotonic()
        process = subprocess.run(
            command,
            cwd=str(self.project_root),
            env=_subprocess_environment(self.project_root),
            text=True,
            capture_output=True,
            check=False,
        )
        if process.returncode != 0:
            raise RuntimeError(
                "released therapist runtime failed: "
                + (process.stderr.strip() or process.stdout.strip())
            )
        try:
            output = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("therapist adapter returned non-JSON output") from exc
        digest = output.get("review_digest") or {}
        utterance = str(digest.get("therapist_utterance") or "").strip()
        if not utterance:
            raise RuntimeError("released therapist runtime returned no therapist utterance")
        memory_report_path = Path(output["therapist_case_memory_report_path"])
        turn_report_path = Path(output["therapist_turn_report_path"])
        memory_report = json.loads(memory_report_path.read_text(encoding="utf-8"))
        turn_report = json.loads(turn_report_path.read_text(encoding="utf-8"))
        memory = (memory_report.get("result") or {}).get("therapist_case_memory") or {}
        turn_result = turn_report.get("result") or {}
        next_private = PrivateTherapistState(
            case_formulation_memory=private_state.case_formulation_memory or memory,
            candidate_strategies=[
                {"strategy": item}
                for item in (memory.get("selected_response_strategy") or [])
            ],
            selected_strategy={
                "stage_target": digest.get("stage_target") or [],
                "response_intent": digest.get("response_intent") or [],
                "primary_move_types": digest.get("primary_move_types") or [],
            },
            retrieved_policy_evidence=[
                str(value)
                for value in (memory_report.get("input_summary") or {}).values()
                if isinstance(value, str) and value.startswith("ku_")
            ],
            internal_planning={
                "self_check": turn_result.get("self_check") or {},
                "boundary_notes": digest.get("boundary_notes") or [],
            },
        )
        return AgentTurnResult(
            text=utterance,
            therapist_state=next_private.model_dump(mode="json"),
            runtime_trace={
                "language": language,
                "foreground_latency_seconds": round(time.monotonic() - started, 3),
                "runtime_input_path": str(runtime_path),
                "therapist_bundle_path": output.get("bundle_path"),
                "therapist_case_memory_report_path": str(memory_report_path),
                "therapist_turn_report_path": str(turn_report_path),
                "review_digest": digest,
                "execution_timing": output.get("execution_timing") or {},
                "deferred_update_id": f"therapist-memory-{time.time_ns()}",
                "deferred_update": {
                    "patient_memory_report_path": output.get("patient_memory_report_path"),
                    "selected_case_source_id": output.get("selected_case_source_id"),
                    "surface_paths": {key: str(value) for key, value in surface_paths.items()},
                    "output_path": str(
                        turn_root / "released_therapist_runtime" / "therapist_case_memory_deferred.json"
                    ),
                },
            },
            retrieved_unit_ids=next_private.retrieved_policy_evidence,
            model_config_id=(turn_report.get("model_config") or {}).get("model"),
            prompt_release_id="released_therapist_memory_and_turn_prompts_20260711",
        )

    def finalize_turn(
        self,
        *,
        private_state: PrivateTherapistState,
        deferred_update: dict[str, Any],
        language: str = "zh",
    ) -> AgentTurnResult:
        output_path = Path(str(deferred_update["output_path"]))
        command = [
            sys.executable,
            str(self.project_root / "scripts/Simulation/run_therapist_case_memory_update.py"),
            "--extraction-config",
            str(self.extraction_config),
            "--model-config",
            str(self.model_config),
            "--patient-memory-report",
            str(deferred_update["patient_memory_report_path"]),
            "--persona-tag",
            self.persona_tag,
            "--memory-group-id",
            self.memory_group_id,
            "--case-source-id",
            str(deferred_update["selected_case_source_id"]),
            "--prompt",
            str(self.project_root / "prompts/Simulation" / f"therapist_case_memory_update_{language}.md"),
            "--output",
            str(output_path),
            "--overwrite",
        ]
        for flag, key in (
            ("--persona-jsonl", "persona_jsonl"),
            ("--memory-jsonl", "memory_jsonl"),
            ("--trajectory-jsonl", "trajectory_jsonl"),
            ("--formulation-jsonl", "formulation_jsonl"),
            ("--action-sequence-jsonl", "action_sequence_jsonl"),
        ):
            value = (deferred_update.get("surface_paths") or {}).get(key)
            if value:
                command.extend([flag, str(value)])
        started = time.monotonic()
        process = subprocess.run(
            command,
            cwd=str(self.project_root),
            env=_subprocess_environment(self.project_root),
            text=True,
            capture_output=True,
            check=False,
        )
        if process.returncode != 0:
            raise RuntimeError(
                "deferred therapist memory update failed: "
                + (process.stderr.strip() or process.stdout.strip())
            )
        report = json.loads(output_path.read_text(encoding="utf-8"))
        memory = (report.get("result") or {}).get("therapist_case_memory") or {}
        if not memory:
            raise RuntimeError("deferred therapist memory update returned no memory")
        next_private = private_state.model_copy(
            update={
                "case_formulation_memory": memory,
                "candidate_strategies": [
                    {"strategy": item}
                    for item in (memory.get("selected_response_strategy") or [])
                ],
            }
        )
        return AgentTurnResult(
            text="",
            therapist_state=next_private.model_dump(mode="json"),
            runtime_trace={
                "deferred_memory_latency_seconds": round(time.monotonic() - started, 3),
                "therapist_case_memory_report_path": str(output_path),
            },
            model_config_id=(report.get("model_config") or {}).get("model"),
            prompt_release_id="therapist_deferred_case_memory_v1",
        )


class CommandBackedLLMSimulationReviewer:
    """Run the released longitudinal summary and reviewer gate for a platform session."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        extraction_config: str | Path,
        model_config: str | Path,
        work_root: str | Path,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.extraction_config = Path(extraction_config).resolve()
        self.model_config = Path(model_config).resolve()
        self.work_root = Path(work_root).resolve()
        self.adapter_script = (
            self.project_root
            / "scripts/Simulation/run_simulation_reviewer_hermes_adapter.py"
        )

    def review(self, snapshot: SessionSnapshot) -> ReviewResult:
        review_root = self.work_root / snapshot.session.session_id
        runtime_path = review_root / "platform_runtime_report.json"
        runtime_path.parent.mkdir(parents=True, exist_ok=True)
        runtime_path.write_text(
            json.dumps(
                build_reviewer_runtime_report(snapshot),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        output_root = review_root / "released_reviewer_runtime"
        command = [
            sys.executable,
            str(self.adapter_script),
            "run-review",
            "--session-input",
            str(runtime_path),
            "--extraction-config",
            str(self.extraction_config),
            "--model-config",
            str(self.model_config),
            "--output-root",
            str(output_root),
            "--language",
            str(snapshot.session.language),
            "--overwrite",
        ]
        process = subprocess.run(
            command,
            cwd=str(self.project_root),
            env=_subprocess_environment(self.project_root),
            text=True,
            capture_output=True,
            check=False,
        )
        if process.returncode != 0:
            raise RuntimeError(
                "released simulation reviewer failed: "
                + (process.stderr.strip() or process.stdout.strip())
            )
        try:
            output = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("simulation reviewer returned non-JSON output") from exc
        digest = output.get("review_digest") or {}
        review_gate = digest.get("review_gate") or {}
        summary = {
            "key_turning_points": digest.get("key_turning_points") or [],
            "active_unresolved_threads": digest.get("active_unresolved_threads") or [],
            "risk_watchpoints": digest.get("risk_watchpoints") or [],
            "recommended_actions": digest.get("recommended_actions") or [],
        }
        return ReviewResult(
            public_review=ReviewPublicView(
                status=str(output.get("status") or "complete"),
                gate_status=review_gate.get("gate_status"),
                summary=summary,
                findings=list(digest.get("finding_digest") or []),
            ),
            runtime_trace={
                "language": str(snapshot.session.language),
                "runtime_report_path": str(runtime_path),
                "reviewer_bundle_path": output.get("bundle_path"),
                "review_digest": digest,
            },
            model_config_id=(output.get("model_config") or {}).get("model") or "hermes-default",
            prompt_release_id="released_session_summary_and_reviewer_prompts_20260711",
        )
