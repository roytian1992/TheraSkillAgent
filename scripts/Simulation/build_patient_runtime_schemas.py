#!/usr/bin/env python3
"""Build runtime schema files for the patient simulator stack."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import file_sha256, write_json
from simulation_surface_registry import (
    SIMULATION_RUNTIME_SCHEMA_MANIFEST_PATH,
    SIMULATION_RUNTIME_SCHEMA_OUTPUT_DIR,
    SIMULATION_RUNTIME_SCHEMA_QA_PATH,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = SIMULATION_RUNTIME_SCHEMA_OUTPUT_DIR
DEFAULT_MANIFEST = SIMULATION_RUNTIME_SCHEMA_MANIFEST_PATH
DEFAULT_QA = SIMULATION_RUNTIME_SCHEMA_QA_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--qa-report", default=str(DEFAULT_QA))
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def state_schema() -> dict[str, Any]:
    return {
        "schema_version": "theraskill_patient_state_schema_v1",
        "title": "Patient Dynamic Session State",
        "type": "object",
        "required": [
            "emotion_state",
            "alliance_state",
            "disclosure_state",
            "resistance_state",
            "readiness_state",
            "risk_state",
            "topic_focus",
            "felt_understanding",
            "unfinished_threads",
        ],
        "properties": {
            "emotion_state": {"type": "array", "items": {"type": "string"}},
            "alliance_state": {
                "type": "string",
                "enum": ["pre_session_or_early_contact", "forming", "working", "strained", "fragile", "unknown"],
            },
            "disclosure_state": {
                "type": "string",
                "enum": ["guarded", "minimal", "surface", "surface_to_moderate", "moderate", "deep", "unknown"],
            },
            "resistance_state": {
                "type": "string",
                "enum": ["low", "low_to_moderate", "guarded", "moderate", "high", "unknown"],
            },
            "readiness_state": {
                "type": "string",
                "enum": [
                    "not_ready",
                    "ambivalent",
                    "ambivalent_but_engaged",
                    "ready_for_guided_exploration",
                    "ready_for_exploration",
                    "ready_for_action",
                    "unknown",
                ],
            },
            "risk_state": {
                "type": "string",
                "enum": ["low", "elevated", "high", "none_observed", "unknown"],
            },
            "topic_focus": {"type": "array", "items": {"type": "string"}},
            "felt_understanding": {"type": "string"},
            "unfinished_threads": {"type": "array", "items": {"type": "string"}},
        },
    }


def memory_schema() -> dict[str, Any]:
    return {
        "schema_version": "theraskill_patient_memory_schema_v1",
        "title": "Patient Conversation Memory / Legacy Carryover",
        "type": "object",
        "required": [
            "longitudinal_problem_summary",
            "unresolved_threads",
            "report_dialogue_alignment",
        ],
        "properties": {
            "schema_version": {"type": "string"},
            "source_seed_policy": {"type": "string"},
            "disclosed_fact_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            "stable_identity_facts": {"type": "array", "items": {"type": "string"}},
            "longitudinal_problem_summary": {"type": "array", "items": {"type": "string"}},
            "previously_used_techniques": {"type": "array", "items": {"type": "string"}},
            "prior_counseling_progress": {"type": "array", "items": {"type": "string"}},
            "resource_signals": {"type": "array", "items": {"type": "string"}},
            "unresolved_threads": {"type": "array", "items": {"type": "string"}},
            "report_dialogue_alignment": {"type": "array", "items": {"type": "string"}},
        },
    }


def disclosure_schema() -> dict[str, Any]:
    return {
        "schema_version": "theraskill_patient_disclosure_decision_schema_v1",
        "title": "Patient Disclosure Controller Output",
        "type": "object",
        "required": ["disclosure_decision", "evidence_trace"],
        "properties": {
            "disclosure_decision": {
                "type": "object",
                "required": [
                    "disclose_fact_ids",
                    "disclose_facts",
                    "keep_withheld_fact_ids",
                    "keep_withheld_facts",
                    "disclosure_depth",
                    "emotional_posture",
                    "avoidance_or_deflection",
                    "alliance_shift_estimate",
                    "risk_escalation_flag",
                    "risk_signal_to_surface",
                    "response_constraints",
                    "rationale_visible_to_runtime",
                ],
                "properties": {
                    "disclose_fact_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                    "disclose_facts": {"type": "array", "items": {"type": "string"}},
                    "keep_withheld_fact_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                    "keep_withheld_facts": {"type": "array", "items": {"type": "string"}},
                    "disclosure_depth": {
                        "type": "string",
                        "enum": ["minimal", "surface", "moderate", "deep", "unknown"],
                    },
                    "emotional_posture": {"type": "array", "items": {"type": "string"}},
                    "avoidance_or_deflection": {"type": "array", "items": {"type": "string"}},
                    "alliance_shift_estimate": {
                        "type": "string",
                        "enum": ["increased", "decreased", "unchanged", "unknown"],
                    },
                    "risk_escalation_flag": {"type": "boolean"},
                    "risk_signal_to_surface": {"type": "array", "items": {"type": "string"}},
                    "response_constraints": {"type": "array", "items": {"type": "string"}},
                    "rationale_visible_to_runtime": {"type": "array", "items": {"type": "string"}},
                },
            },
            "evidence_trace": {
                "type": "object",
                "required": [
                    "applied_compiled_persona_evidence",
                    "applied_patient_state_memory",
                    "applied_therapist_move_interpretation",
                    "recent_turn_anchors",
                    "uncertainties",
                ],
                "properties": {
                    "applied_compiled_persona_evidence": {"type": "array", "items": {"type": "string"}},
                    "applied_patient_state_memory": {"type": "array", "items": {"type": "string"}},
                    "applied_therapist_move_interpretation": {"type": "array", "items": {"type": "string"}},
                    "recent_turn_anchors": {"type": "array", "items": {"type": "string"}},
                    "uncertainties": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    }


def turn_output_schema() -> dict[str, Any]:
    return {
        "schema_version": "theraskill_patient_turn_output_schema_v1",
        "title": "Patient Turn Generator Output",
        "type": "object",
        "required": [
            "patient_turn_plan",
            "patient_utterance",
            "used_fact_ids",
            "applied_principle_ids",
            "hidden_state_delta",
            "generation_trace",
        ],
        "properties": {
            "patient_turn_plan": {
                "type": "object",
                "required": [
                    "dominant_emotion",
                    "response_intent",
                    "disclosure_mode",
                    "resistance_or_hesitation",
                    "hidden_info_kept_unspoken",
                    "alignment_with_current_state",
                ],
                "properties": {
                    "dominant_emotion": {"type": "array", "items": {"type": "string"}},
                    "response_intent": {"type": "array", "items": {"type": "string"}},
                    "disclosure_mode": {
                        "type": "string",
                        "enum": ["minimal", "surface", "moderate", "deep", "unknown"],
                    },
                    "resistance_or_hesitation": {"type": "array", "items": {"type": "string"}},
                    "hidden_info_kept_unspoken": {"type": "array", "items": {"type": "string"}},
                    "alignment_with_current_state": {"type": "array", "items": {"type": "string"}},
                },
            },
            "patient_utterance": {"type": "string"},
            "used_fact_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            "applied_principle_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            "hidden_state_delta": {
                "type": "object",
                "required": [
                    "newly_activated_concerns",
                    "trust_shift",
                    "disclosure_pressure_shift",
                    "motivation_shift",
                ],
                "properties": {
                    "newly_activated_concerns": {"type": "array", "items": {"type": "string"}},
                    "trust_shift": {"type": "string", "enum": ["increased", "decreased", "unchanged", "unknown"]},
                    "disclosure_pressure_shift": {
                        "type": "string",
                        "enum": ["increased", "decreased", "unchanged", "unknown"],
                    },
                    "motivation_shift": {"type": "string", "enum": ["increased", "decreased", "unchanged", "unknown"]},
                },
            },
            "generation_trace": {
                "type": "object",
                "required": [
                    "applied_persona_evidence",
                    "applied_patient_state_memory",
                    "applied_disclosure_decision",
                    "recent_turn_anchors",
                    "uncertainties",
                ],
                "properties": {
                    "applied_persona_evidence": {"type": "array", "items": {"type": "string"}},
                    "applied_patient_state_memory": {"type": "array", "items": {"type": "string"}},
                    "applied_disclosure_decision": {"type": "array", "items": {"type": "string"}},
                    "recent_turn_anchors": {"type": "array", "items": {"type": "string"}},
                    "uncertainties": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    }


def response_contract_schema() -> dict[str, Any]:
    return {
        "schema_version": "theraskill_patient_response_contract_schema_v1",
        "title": "Validated Patient Response Contract",
        "type": "object",
        "required": [
            "used_fact_ids",
            "applied_principle_ids",
            "realized_disclosure_level",
            "surface_emotion",
            "withheld_fact_ids",
            "uncertainty_or_hallucination_flags",
        ],
        "properties": {
            "used_fact_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            "applied_principle_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            "realized_disclosure_level": {
                "type": "string",
                "enum": ["minimal", "surface", "moderate", "deep", "unknown"],
            },
            "surface_emotion": {"type": "array", "items": {"type": "string"}},
            "withheld_fact_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            "uncertainty_or_hallucination_flags": {"type": "array", "items": {"type": "string"}},
        },
    }


def reviewer_schema() -> dict[str, Any]:
    return {
        "schema_version": "theraskill_patient_reviewer_schema_v1",
        "title": "Patient Simulator Reviewer Output",
        "type": "object",
        "required": ["review_gate", "findings", "recommended_actions", "review_trace"],
        "properties": {
            "review_gate": {
                "type": "object",
                "required": [
                    "gate_status",
                    "persona_consistency",
                    "memory_faithfulness",
                    "stage_fit",
                    "boundary_compliance",
                    "risk_attention",
                    "repetition_or_degeneration",
                ],
                "properties": {
                    "gate_status": {"type": "string", "enum": ["pass", "soft_warning", "fail"]},
                    "persona_consistency": {"type": "string", "enum": ["pass", "warning", "fail"]},
                    "memory_faithfulness": {"type": "string", "enum": ["pass", "warning", "fail"]},
                    "stage_fit": {"type": "string", "enum": ["pass", "warning", "fail"]},
                    "boundary_compliance": {"type": "string", "enum": ["pass", "warning", "fail"]},
                    "risk_attention": {"type": "string", "enum": ["pass", "warning", "fail"]},
                    "repetition_or_degeneration": {"type": "string", "enum": ["pass", "warning", "fail"]},
                },
            },
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["severity", "category", "evidence", "message"],
                    "properties": {
                        "severity": {"type": "string", "enum": ["info", "warning", "fail"]},
                        "category": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "string"}},
                        "message": {"type": "string"},
                    },
                },
            },
            "recommended_actions": {"type": "array", "items": {"type": "string"}},
            "review_trace": {
                "type": "object",
                "required": ["anchors_used", "uncertainties"],
                "properties": {
                    "anchors_used": {"type": "array", "items": {"type": "string"}},
                    "uncertainties": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    }


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    if output_dir.exists() and any(output_dir.iterdir()) and not args.overwrite:
        raise FileExistsError(f"output dir is not empty: {output_dir}")

    schema_map = {
        "patient_state_schema.json": state_schema(),
        "patient_memory_schema.json": memory_schema(),
        "patient_disclosure_decision_schema.json": disclosure_schema(),
        "patient_turn_output_schema.json": turn_output_schema(),
        "patient_response_contract_schema.json": response_contract_schema(),
        "patient_reviewer_schema.json": reviewer_schema(),
    }

    written: list[dict[str, Any]] = []
    for filename, payload in schema_map.items():
        path = output_dir / filename
        write_json(path, payload)
        written.append({"path": str(path), "sha256": file_sha256(path)})

    issues = []
    try:
        from jsonschema import Draft202012Validator

        for filename, payload in schema_map.items():
            try:
                Draft202012Validator.check_schema(payload)
            except Exception as exc:  # noqa: BLE001
                issues.append({"schema": filename, "error": f"{type(exc).__name__}: {exc}"})
    except Exception as exc:  # noqa: BLE001
        issues.append({"schema": "all", "error": f"validator_unavailable: {type(exc).__name__}: {exc}"})
    status = "complete" if not issues else "failed"
    manifest = {
        "schema_version": "theraskill_patient_runtime_schemas_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "schema_count": len(written),
        "schemas": written,
    }
    write_json(args.manifest, manifest)

    qa = {
        "schema_version": "theraskill_patient_runtime_schemas_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "schema_count": len(written),
        "issues": issues,
        "issues_count": len(issues),
    }
    write_json(args.qa_report, qa)
    print(json.dumps({"status": status, "schema_count": len(written), "issues_count": len(issues)}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
