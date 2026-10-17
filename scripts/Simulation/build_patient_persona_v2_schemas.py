#!/usr/bin/env python3
"""Build versioned JSON Schemas for patient persona v2 and disclosure nodes."""

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
    SIMULATION_DISCLOSURE_NODE_V1_SCHEMA_PATH,
    SIMULATION_PATIENT_PERSONA_V2_SCHEMA_PATH,
    SIMULATION_PERSONA_V2_SCHEMA_MANIFEST_PATH,
    SIMULATION_PERSONA_V2_SCHEMA_QA_PATH,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--persona-schema", default=str(SIMULATION_PATIENT_PERSONA_V2_SCHEMA_PATH))
    parser.add_argument("--disclosure-schema", default=str(SIMULATION_DISCLOSURE_NODE_V1_SCHEMA_PATH))
    parser.add_argument("--manifest", default=str(SIMULATION_PERSONA_V2_SCHEMA_MANIFEST_PATH))
    parser.add_argument("--qa-report", default=str(SIMULATION_PERSONA_V2_SCHEMA_QA_PATH))
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def string_array() -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}, "uniqueItems": True}


def fact_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "fact_id",
            "content",
            "origin",
            "confidence",
            "source_paths",
            "source_unit_ids",
            "evidence_spans",
        ],
        "properties": {
            "fact_id": {"type": "string", "minLength": 1},
            "content": {"type": "string", "minLength": 1},
            "origin": {
                "type": "string",
                "enum": ["evidence_derived", "bounded_inference", "synthetic_fill"],
            },
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "source_paths": string_array(),
            "source_unit_ids": string_array(),
            "evidence_spans": string_array(),
        },
    }


def disclosure_node_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://theraskill.local/schemas/simulation/disclosure_node_v1.schema.json",
        "title": "TheraSkill Patient Disclosure Node v1",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "fact_id",
            "content",
            "sensitivity",
            "origin",
            "confidence",
            "prerequisite_fact_ids",
            "facilitating_conditions",
            "blocking_conditions",
            "allowed_state_bands",
            "default_response_mode",
            "source_paths",
            "source_unit_ids",
            "evidence_spans",
        ],
        "properties": {
            "schema_version": {"const": "theraskill_disclosure_node_v1"},
            "fact_id": {"type": "string", "minLength": 1},
            "content": {"type": "string", "minLength": 1},
            "sensitivity": {"type": "string", "enum": ["surface", "moderate", "sensitive"]},
            "origin": {
                "type": "string",
                "enum": ["evidence_derived", "bounded_inference", "synthetic_fill"],
            },
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "prerequisite_fact_ids": string_array(),
            "facilitating_conditions": string_array(),
            "blocking_conditions": string_array(),
            "allowed_state_bands": {
                "type": "array",
                "items": {"type": "string", "enum": ["surface", "moderate", "deep"]},
                "uniqueItems": True,
                "minItems": 1,
            },
            "default_response_mode": {
                "type": "string",
                "enum": ["answer", "hint", "deflect", "withhold"],
            },
            "source_paths": string_array(),
            "source_unit_ids": string_array(),
            "evidence_spans": string_array(),
        },
    }


def persona_schema() -> dict[str, Any]:
    fact = fact_schema()
    strings = string_array()
    section_with_facts = {
        "type": "object",
        "additionalProperties": False,
        "required": ["facts"],
        "properties": {"facts": {"type": "array", "items": fact}},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://theraskill.local/schemas/simulation/patient_persona_v2.schema.json",
        "title": "TheraSkill Patient Persona v2",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "persona_id",
            "case_id",
            "immutable_identity",
            "social_world",
            "life_history",
            "psychological_model",
            "communication_profile",
            "disclosure_graph",
            "behavioral_constitution",
            "initial_state",
            "episodic_memory_seed",
            "training_contract",
            "provenance",
            "quality",
            "legacy_compatibility",
        ],
        "properties": {
            "schema_version": {"const": "theraskill_patient_persona_v2"},
            "persona_id": {"type": "string", "minLength": 1},
            "case_id": {"type": "string", "minLength": 1},
            "immutable_identity": {
                "type": "object",
                "additionalProperties": False,
                "required": ["display_name", "demographic_profile", "facts"],
                "properties": {
                    "display_name": {"type": "string", "minLength": 1},
                    "demographic_profile": {"type": "object"},
                    "facts": {"type": "array", "items": fact},
                },
            },
            "social_world": {
                "type": "object",
                "additionalProperties": False,
                "required": ["relationship_nodes", "support_resources", "relationship_tensions"],
                "properties": {
                    "relationship_nodes": {"type": "array", "items": fact},
                    "support_resources": {"type": "array", "items": fact},
                    "relationship_tensions": {"type": "array", "items": fact},
                },
            },
            "life_history": {
                "type": "object",
                "additionalProperties": False,
                "required": ["events", "prior_counseling_progress", "unresolved_events"],
                "properties": {
                    "events": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["event_id", "relative_time", "event_type", "fact"],
                            "properties": {
                                "event_id": {"type": "string", "minLength": 1},
                                "relative_time": {"type": "string", "minLength": 1},
                                "event_type": {"type": "string", "minLength": 1},
                                "fact": fact,
                            },
                        },
                    },
                    "prior_counseling_progress": {"type": "array", "items": fact},
                    "unresolved_events": {"type": "array", "items": fact},
                },
            },
            "psychological_model": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "presenting_problems",
                    "triggers_and_context",
                    "maintaining_factors",
                    "protective_factors",
                    "goals",
                    "risk_watchpoints",
                    "coping_patterns",
                    "tensions_and_contradictions",
                    "framework_overlays",
                ],
                "properties": {
                    key: {"type": "array", "items": fact}
                    for key in (
                        "presenting_problems",
                        "triggers_and_context",
                        "maintaining_factors",
                        "protective_factors",
                        "goals",
                        "risk_watchpoints",
                        "coping_patterns",
                        "tensions_and_contradictions",
                    )
                }
                | {"framework_overlays": {"type": "object"}},
            },
            "communication_profile": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "cognitive_style",
                    "emotional_expression",
                    "help_seeking_style",
                    "readiness_style",
                    "response_length",
                    "vocabulary_and_abstraction",
                    "narrative_organization",
                    "initiative_level",
                    "hesitation_and_avoidance",
                    "advice_response_style",
                    "natural_variation_examples",
                ],
                "properties": {
                    key: {"type": "array", "items": fact}
                    for key in (
                        "cognitive_style",
                        "emotional_expression",
                        "help_seeking_style",
                        "readiness_style",
                        "hesitation_and_avoidance",
                        "advice_response_style",
                        "natural_variation_examples",
                    )
                }
                | {
                    "response_length": {"type": "string"},
                    "vocabulary_and_abstraction": {"type": "string"},
                    "narrative_organization": {"type": "string"},
                    "initiative_level": {"type": "string"},
                },
            },
            "disclosure_graph": {
                "type": "object",
                "additionalProperties": False,
                "required": ["nodes", "initially_disclosed_fact_ids"],
                "properties": {
                    "nodes": {"type": "array", "items": {"$ref": "disclosure_node_v1.schema.json"}},
                    "initially_disclosed_fact_ids": strings,
                },
            },
            "behavioral_constitution": {
                "type": "object",
                "additionalProperties": False,
                "required": ["principles"],
                "properties": {
                    "principles": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "principle_id",
                                "category",
                                "when",
                                "then",
                                "unless",
                                "origin",
                                "source_paths",
                                "source_unit_ids",
                            ],
                            "properties": {
                                "principle_id": {"type": "string", "minLength": 1},
                                "category": {
                                    "type": "string",
                                    "enum": [
                                        "identity_consistency",
                                        "disclosure_behavior",
                                        "alliance_and_rupture",
                                        "resistance_and_readiness",
                                        "communication_style",
                                        "risk_expression_boundary",
                                    ],
                                },
                                "when": {"type": "string", "minLength": 1},
                                "then": {"type": "string", "minLength": 1},
                                "unless": {"type": "string"},
                                "origin": {
                                    "type": "string",
                                    "enum": ["evidence_derived", "bounded_inference", "synthetic_fill"],
                                },
                                "source_paths": strings,
                                "source_unit_ids": strings,
                            },
                        },
                    }
                },
            },
            "initial_state": {"type": "object"},
            "episodic_memory_seed": {"type": "object"},
            "training_contract": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "learner_visible_case_brief",
                    "learner_level",
                    "training_objectives",
                    "expected_challenge",
                    "success_criteria",
                    "hidden_fact_ids",
                    "instructor_reveal_fact_ids",
                    "runtime_priors",
                ],
                "properties": {
                    "learner_visible_case_brief": {"type": "string"},
                    "learner_level": {"type": "string"},
                    "training_objectives": strings,
                    "expected_challenge": {"type": "string"},
                    "success_criteria": strings,
                    "hidden_fact_ids": strings,
                    "instructor_reveal_fact_ids": strings,
                    "runtime_priors": {"type": "object"},
                },
            },
            "provenance": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "migration_policy",
                    "source_bundle_path",
                    "source_bundle_sha256",
                    "source_bundle_content_sha256",
                    "source_id",
                    "source_unit_ids",
                    "source_alignment",
                ],
                "properties": {
                    "migration_policy": {"const": "deterministic_lossless_projection_v1_to_v2"},
                    "source_bundle_path": {"type": "string", "minLength": 1},
                    "source_bundle_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "source_bundle_content_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "source_id": {"type": "string", "minLength": 1},
                    "source_unit_ids": {"type": "object"},
                    "source_alignment": {"type": "object"},
                    "enrichment": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "schema_version",
                            "verified_patch_sha256",
                            "model",
                            "model_base_url",
                            "enrichment_prompt_sha256",
                            "verify_prompt_sha256",
                            "quality_attempts",
                            "input_compaction",
                        ],
                        "properties": {
                            "schema_version": {"const": "theraskill_patient_persona_v2_enrichment_v1"},
                            "verified_patch_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                            "model": {"type": "string", "minLength": 1},
                            "model_base_url": {"type": "string", "minLength": 1},
                            "enrichment_prompt_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                            "verify_prompt_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                            "quality_attempts": {"type": "integer", "minimum": 1},
                            "input_compaction": {"type": "object"},
                            "enrichment_strategy": {
                                "type": "string",
                                "enum": ["standard", "conflict_aware_conservative"],
                            },
                        },
                    },
                },
            },
            "quality": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "status",
                    "migration_stage",
                    "enrichment_status",
                    "warnings",
                    "synthetic_fill_fields",
                ],
                "properties": {
                    "status": {"type": "string", "enum": ["complete", "incomplete"]},
                    "migration_stage": {
                        "type": "string",
                        "enum": ["R1_deterministic_projection", "R2_verified_llm_enrichment"],
                    },
                    "enrichment_status": {"type": "string", "enum": ["not_started", "complete"]},
                    "warnings": strings,
                    "synthetic_fill_fields": strings,
                },
            },
            "legacy_compatibility": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source_bundle_snapshot"],
                "properties": {"source_bundle_snapshot": {"type": "object"}},
            },
        },
    }


def main() -> int:
    args = parse_args()
    persona_path = Path(args.persona_schema)
    disclosure_path = Path(args.disclosure_schema)
    write_json(disclosure_path, disclosure_node_schema(), overwrite=args.overwrite)
    write_json(persona_path, persona_schema(), overwrite=args.overwrite)

    issues: list[str] = []
    try:
        from jsonschema import Draft202012Validator

        Draft202012Validator.check_schema(disclosure_node_schema())
        Draft202012Validator.check_schema(persona_schema())
    except Exception as exc:  # pragma: no cover - surfaced in QA
        issues.append(f"schema_validation_error: {type(exc).__name__}: {exc}")

    status = "complete" if not issues else "incomplete"
    created_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "schema_version": "theraskill_patient_persona_v2_schemas_manifest_v1",
        "created_at": created_at,
        "status": status,
        "schemas": {
            "patient_persona_v2": {"path": str(persona_path), "sha256": file_sha256(persona_path)},
            "disclosure_node_v1": {"path": str(disclosure_path), "sha256": file_sha256(disclosure_path)},
        },
    }
    qa = {
        "schema_version": "theraskill_patient_persona_v2_schemas_qa_v1",
        "created_at": created_at,
        "status": status,
        "draft": "2020-12",
        "schema_count": 2,
        "issues": issues,
        "issues_count": len(issues),
    }
    write_json(args.manifest, manifest, overwrite=args.overwrite)
    write_json(args.qa_report, qa, overwrite=args.overwrite)
    print(json.dumps({"status": status, "schema_count": 2, "issues_count": len(issues)}, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
