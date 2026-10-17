#!/usr/bin/env python3
"""Project patient case bundles into lossless, structured patient persona v2 artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import ensure_output_path, file_sha256, iter_jsonl, read_json, write_json, write_jsonl
from simulation_surface_registry import (
    SIMULATION_DISCLOSURE_NODE_V1_SCHEMA_PATH,
    SIMULATION_PATIENT_PERSONA_V2_SCHEMA_PATH,
    SIMULATION_PERSONA_V2_DIR,
    SIMULATION_PERSONA_V2_INDEX_PATH,
    SIMULATION_PERSONA_V2_MANIFEST_PATH,
    SIMULATION_PERSONA_V2_QA_PATH,
    SIMULATION_PERSONA_V2_SUMMARY_PATH,
    SIMULATION_RUNTIME_PATIENT_CASES_DIR,
    SIMULATION_SCENARIO_REGISTRY_PATH,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ORIGIN_VALUES = {"evidence_derived", "bounded_inference", "synthetic_fill"}
SENSITIVITY_RANK = {"surface": 0, "moderate": 1, "sensitive": 2}
FORBIDDEN_SYNTHETIC_CLINICAL_TERMS = (
    "诊断",
    "共病",
    "自杀",
    "自伤",
    "暴力",
    "虐待",
    "创伤",
    "住院",
    "药物",
    "违法",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", default=str(SIMULATION_RUNTIME_PATIENT_CASES_DIR))
    parser.add_argument("--scenario-registry", default=str(SIMULATION_SCENARIO_REGISTRY_PATH))
    parser.add_argument("--persona-schema", default=str(SIMULATION_PATIENT_PERSONA_V2_SCHEMA_PATH))
    parser.add_argument("--disclosure-schema", default=str(SIMULATION_DISCLOSURE_NODE_V1_SCHEMA_PATH))
    parser.add_argument("--output-dir", default=str(SIMULATION_PERSONA_V2_DIR))
    parser.add_argument("--index", default=str(SIMULATION_PERSONA_V2_INDEX_PATH))
    parser.add_argument("--manifest", default=str(SIMULATION_PERSONA_V2_MANIFEST_PATH))
    parser.add_argument("--qa-report", default=str(SIMULATION_PERSONA_V2_QA_PATH))
    parser.add_argument("--summary", default=str(SIMULATION_PERSONA_V2_SUMMARY_PATH))
    parser.add_argument("--limit-cases", type=int)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def clean_list(value: Any) -> list[str]:
    values = value if isinstance(value, list) else ([] if value in (None, "") else [value])
    seen: set[str] = set()
    result: list[str] = []
    for item in values:
        text = clean_text(item)
        if not text or text in seen or text == "insufficient_evidence":
            continue
        seen.add(text)
        result.append(text)
    return result


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: str) -> str:
    payload = "\n".join(parts)
    return f"{prefix}_{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:16]}"


def source_unit_ids(bundle: dict[str, Any]) -> list[str]:
    raw = ((bundle.get("authoritative_layer") or {}).get("source_unit_ids") or {})
    values = raw.values() if isinstance(raw, dict) else raw
    return clean_list(list(values))


def evidence_spans(layer: dict[str, Any]) -> list[str]:
    return clean_list(layer.get("evidence_spans") or [])


def make_fact(
    case_id: str,
    content: Any,
    source_path: str,
    unit_ids: list[str],
    spans: list[str],
    *,
    origin: str = "evidence_derived",
    confidence: str = "high",
) -> dict[str, Any] | None:
    text = clean_text(content)
    if not text or text == "insufficient_evidence":
        return None
    return {
        "fact_id": stable_id("fact", case_id, text),
        "content": text,
        "origin": origin,
        "confidence": confidence,
        "source_paths": clean_list([source_path]),
        "source_unit_ids": clean_list(unit_ids),
        "evidence_spans": clean_list(spans),
    }


def merge_facts(facts: Iterable[dict[str, Any] | None]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for fact in facts:
        if not fact:
            continue
        key = fact["content"]
        if key not in merged:
            merged[key] = fact
            order.append(key)
            continue
        row = merged[key]
        for field in ("source_paths", "source_unit_ids", "evidence_spans"):
            row[field] = list(dict.fromkeys([*row[field], *fact[field]]))
        if row["origin"] != "evidence_derived" and fact["origin"] == "evidence_derived":
            row["origin"] = "evidence_derived"
            row["confidence"] = fact["confidence"]
    return [merged[key] for key in order]


def facts_for(
    case_id: str,
    layer: dict[str, Any],
    field: str,
    source_path: str,
    unit_ids: list[str],
) -> list[dict[str, Any]]:
    spans = evidence_spans(layer)
    return merge_facts(
        make_fact(case_id, value, f"{source_path}.{field}", unit_ids, spans)
        for value in clean_list(layer.get(field) or [])
    )


def facts_from_values(
    case_id: str,
    values: Iterable[Any],
    source_path: str,
    unit_ids: list[str],
    spans: list[str],
    *,
    origin: str = "evidence_derived",
    confidence: str = "high",
) -> list[dict[str, Any]]:
    return merge_facts(
        make_fact(
            case_id,
            value,
            source_path,
            unit_ids,
            spans,
            origin=origin,
            confidence=confidence,
        )
        for value in values
    )


def scenario_index(path: Path) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for row in iter_jsonl(path):
        case_id = clean_text(row.get("case_id"))
        if case_id:
            index.setdefault(case_id, []).append(row)
    return index


def build_disclosure_nodes(
    case_id: str,
    groups: list[tuple[str, list[dict[str, Any]]]],
    disclosure_conditions: list[str],
    rupture_triggers: list[str],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for sensitivity, facts in groups:
        for fact in facts:
            key = fact["content"]
            node = merged.get(key)
            if node is None:
                node = {
                    "schema_version": "theraskill_disclosure_node_v1",
                    **fact,
                    "sensitivity": sensitivity,
                    "prerequisite_fact_ids": [],
                    "facilitating_conditions": [],
                    "blocking_conditions": [],
                    "allowed_state_bands": ["surface", "moderate", "deep"],
                    "default_response_mode": "answer",
                }
                merged[key] = node
                order.append(key)
            else:
                for field in ("source_paths", "source_unit_ids", "evidence_spans"):
                    node[field] = list(dict.fromkeys([*node[field], *fact[field]]))
                if SENSITIVITY_RANK[sensitivity] > SENSITIVITY_RANK[node["sensitivity"]]:
                    node["sensitivity"] = sensitivity

    for node in merged.values():
        sensitivity = node["sensitivity"]
        if sensitivity == "surface":
            node["allowed_state_bands"] = ["surface", "moderate", "deep"]
            node["default_response_mode"] = "answer"
        elif sensitivity == "moderate":
            node["allowed_state_bands"] = ["moderate", "deep"]
            node["default_response_mode"] = "hint"
            node["facilitating_conditions"] = disclosure_conditions or [
                "治疗师使用与当前主题相关的开放式探索，并维持非评判性回应"
            ]
            node["blocking_conditions"] = rupture_triggers
        else:
            node["allowed_state_bands"] = ["deep"]
            node["default_response_mode"] = "withhold"
            node["facilitating_conditions"] = disclosure_conditions or [
                "已经形成足够信任，且治疗师明确、相关、非诱导地询问该主题"
            ]
            node["blocking_conditions"] = rupture_triggers
    return [merged[key] for key in order]


def make_principle(
    case_id: str,
    category: str,
    when: str,
    then: str,
    unless: str,
    origin: str,
    source_path: str,
    unit_ids: list[str],
) -> dict[str, Any]:
    return {
        "principle_id": stable_id("principle", case_id, category, when, then),
        "category": category,
        "when": when,
        "then": then,
        "unless": unless,
        "origin": origin,
        "source_paths": [source_path],
        "source_unit_ids": list(unit_ids),
    }


def build_principles(
    case_id: str,
    visible: dict[str, Any],
    hidden: dict[str, Any],
    unit_ids: list[str],
) -> list[dict[str, Any]]:
    principles = [
        make_principle(
            case_id,
            "identity_consistency",
            "任何一轮生成患者回应",
            "保持年龄、职业、关系、既往经历和已确认事实一致，不因治疗师暗示而改写硬事实",
            "没有例外；不确定时明确记不清或不愿回答，而不是新增事实",
            "bounded_inference",
            "authoritative_layer.visible_profile",
            unit_ids,
        ),
        make_principle(
            case_id,
            "risk_expression_boundary",
            "治疗师直接、相关且非诱导地进行安全评估，或当前状态表明需要澄清风险",
            "只表达已有证据支持的风险事实；不得为了戏剧性新增自伤、自杀、暴力、创伤、诊断或用药经历",
            "普通主题下仍遵守 disclosure graph 的敏感信息边界",
            "bounded_inference",
            "authoritative_layer.continuity_seed.risk_watchpoints",
            unit_ids,
        ),
    ]
    for value in clean_list(hidden.get("disclosure_conditions") or []):
        principles.append(
            make_principle(
                case_id,
                "disclosure_behavior",
                value,
                "可以逐步增加与当前问题相关的具体信息，但只使用当轮 persona compiler 允许的事实",
                "如果同时出现关系破裂或施压信号，则维持或降低披露",
                "evidence_derived",
                "authoritative_layer.hidden_profile.disclosure_conditions",
                unit_ids,
            )
        )
    for value in clean_list(hidden.get("rupture_triggers") or []):
        principles.append(
            make_principle(
                case_id,
                "alliance_and_rupture",
                value,
                "表现出更谨慎、简短、回避或关系受损的反应，并降低进一步披露意愿",
                "治疗师随后准确承认影响并进行关系修复",
                "evidence_derived",
                "authoritative_layer.hidden_profile.rupture_triggers",
                unit_ids,
            )
        )
    for value in clean_list(hidden.get("trust_building_signals") or []):
        principles.append(
            make_principle(
                case_id,
                "alliance_and_rupture",
                value,
                "信任与合作可以小幅增加，但不要立即完全开放或突然痊愈",
                "当前回应同时包含评判、施压或明显误解",
                "evidence_derived",
                "authoritative_layer.hidden_profile.trust_building_signals",
                unit_ids,
            )
        )
    for value in clean_list(hidden.get("response_shift_conditions") or []):
        principles.append(
            make_principle(
                case_id,
                "resistance_and_readiness",
                value,
                "按证据支持的方向渐进调整准备度、情绪或回应深度",
                "不得跨越多个状态直接进入问题完全解决",
                "evidence_derived",
                "authoritative_layer.hidden_profile.response_shift_conditions",
                unit_ids,
            )
        )
    for value in clean_list(visible.get("help_seeking_style") or []):
        principles.append(
            make_principle(
                case_id,
                "communication_style",
                "患者表达需求、回应提问或评价治疗师建议",
                value,
                "具体措辞应随语境自然变化，不能逐轮复读该描述",
                "evidence_derived",
                "authoritative_layer.visible_profile.help_seeking_style",
                unit_ids,
            )
        )
    return principles


def build_persona(bundle: dict[str, Any], source_path: Path, scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    case_id = clean_text(bundle.get("case_id"))
    layer = bundle.get("authoritative_layer") or {}
    visible = layer.get("visible_profile") or {}
    hidden = layer.get("hidden_profile") or {}
    continuity = layer.get("continuity_seed") or {}
    bridge = layer.get("report_dialogue_bridge") or {}
    formulation = layer.get("patient_formulation") or {}
    unit_ids = source_unit_ids(bundle)
    visible_spans = evidence_spans(visible)
    hidden_spans = evidence_spans(hidden)
    continuity_spans = evidence_spans(continuity)
    bridge_spans = evidence_spans(bridge)

    stable_facts = facts_for(
        case_id,
        continuity,
        "stable_identity_facts",
        "authoritative_layer.continuity_seed",
        unit_ids,
    )
    demographics = dict(visible.get("demographic_profile") or {})
    demographic_facts = facts_from_values(
        case_id,
        [f"{key}: {value}" for key, value in demographics.items() if clean_text(value)],
        "authoritative_layer.visible_profile.demographic_profile",
        unit_ids,
        visible_spans,
    )
    identity_facts = merge_facts([*stable_facts, *demographic_facts])

    interpersonal = facts_for(case_id, visible, "interpersonal_context", "authoritative_layer.visible_profile", unit_ids)
    relationship = facts_for(case_id, continuity, "relationship_context", "authoritative_layer.continuity_seed", unit_ids)
    protective = facts_for(case_id, visible, "protective_factor_profile", "authoritative_layer.visible_profile", unit_ids)
    resources = facts_for(case_id, continuity, "resource_signals", "authoritative_layer.continuity_seed", unit_ids)
    latent_conflicts = facts_for(case_id, hidden, "latent_core_conflicts", "authoritative_layer.hidden_profile", unit_ids)

    history_summary = facts_for(case_id, continuity, "longitudinal_problem_summary", "authoritative_layer.continuity_seed", unit_ids)
    report_history = facts_for(case_id, bridge, "report_only_information", "authoritative_layer.report_dialogue_bridge", unit_ids)
    life_event_facts = merge_facts([*interpersonal, *history_summary, *report_history])
    life_events = [
        {
            "event_id": stable_id("event", case_id, fact["fact_id"]),
            "relative_time": "relative_time_from_source_not_normalized",
            "event_type": "case_history_event",
            "fact": fact,
        }
        for fact in life_event_facts
    ]
    prior_progress = facts_for(case_id, continuity, "prior_counseling_progress", "authoritative_layer.continuity_seed", unit_ids)
    unresolved = facts_for(case_id, continuity, "unresolved_threads", "authoritative_layer.continuity_seed", unit_ids)

    presenting = facts_from_values(
        case_id,
        formulation.get("presenting_problems") or visible.get("presenting_problem_profile") or [],
        "authoritative_layer.patient_formulation.presenting_problems",
        unit_ids,
        visible_spans,
    )
    triggers = facts_from_values(
        case_id,
        formulation.get("trigger_or_context") or visible.get("interpersonal_context") or [],
        "authoritative_layer.patient_formulation.trigger_or_context",
        unit_ids,
        visible_spans,
    )
    maintaining = facts_from_values(
        case_id,
        formulation.get("maintaining_factors") or hidden.get("latent_core_conflicts") or [],
        "authoritative_layer.patient_formulation.maintaining_factors",
        unit_ids,
        hidden_spans,
    )
    protective_model = facts_from_values(
        case_id,
        formulation.get("protective_factors") or visible.get("protective_factor_profile") or [],
        "authoritative_layer.patient_formulation.protective_factors",
        unit_ids,
        visible_spans,
    )
    goals = facts_from_values(
        case_id,
        formulation.get("client_goals") or continuity.get("next_session_entry_points") or [],
        "authoritative_layer.patient_formulation.client_goals",
        unit_ids,
        continuity_spans,
    )
    risks = facts_from_values(
        case_id,
        [*(formulation.get("risk_factors") or []), *(continuity.get("risk_watchpoints") or [])],
        "authoritative_layer.patient_formulation.risk_factors",
        unit_ids,
        [*visible_spans, *continuity_spans],
    )
    coping = facts_from_values(
        case_id,
        [*(hidden.get("memory_update_hooks") or []), *(continuity.get("previously_used_techniques") or [])],
        "authoritative_layer.hidden_profile.memory_update_hooks",
        unit_ids,
        [*hidden_spans, *continuity_spans],
    )
    tensions = facts_from_values(
        case_id,
        [*(hidden.get("latent_core_conflicts") or []), *(bridge.get("mismatch_or_tension_signals") or [])],
        "authoritative_layer.report_dialogue_bridge.mismatch_or_tension_signals",
        unit_ids,
        [*hidden_spans, *bridge_spans],
    )

    cognitive_style = facts_for(case_id, visible, "cognitive_style", "authoritative_layer.visible_profile", unit_ids)
    emotional_style = facts_for(case_id, visible, "emotional_profile", "authoritative_layer.visible_profile", unit_ids)
    help_style = facts_for(case_id, visible, "help_seeking_style", "authoritative_layer.visible_profile", unit_ids)
    readiness_style = facts_for(case_id, visible, "motivation_or_readiness_profile", "authoritative_layer.visible_profile", unit_ids)
    avoidance_style = facts_for(case_id, hidden, "alliance_sensitivity_signals", "authoritative_layer.hidden_profile", unit_ids)
    advice_style = facts_for(case_id, hidden, "response_shift_conditions", "authoritative_layer.hidden_profile", unit_ids)

    surface_story = facts_for(case_id, hidden, "surface_presenting_story", "authoritative_layer.hidden_profile", unit_ids)
    symptoms = facts_for(case_id, visible, "symptom_profile", "authoritative_layer.visible_profile", unit_ids)
    withheld = facts_for(case_id, hidden, "withheld_or_unspoken_concerns", "authoritative_layer.hidden_profile", unit_ids)
    sensitive_risks = facts_for(case_id, continuity, "risk_watchpoints", "authoritative_layer.continuity_seed", unit_ids)
    disclosure_nodes = build_disclosure_nodes(
        case_id,
        [
            ("surface", merge_facts([*identity_facts, *presenting, *surface_story, *emotional_style, *symptoms])),
            ("moderate", merge_facts([*interpersonal, *relationship, *cognitive_style, *help_style, *unresolved, *prior_progress])),
            ("sensitive", merge_facts([*withheld, *latent_conflicts, *sensitive_risks])),
        ],
        clean_list(hidden.get("disclosure_conditions") or []),
        clean_list(hidden.get("rupture_triggers") or []),
    )
    surface_ids = [node["fact_id"] for node in disclosure_nodes if node["sensitivity"] == "surface"]
    hidden_ids = [node["fact_id"] for node in disclosure_nodes if node["sensitivity"] != "surface"]

    scenario = scenarios[0] if scenarios else {}
    training_objectives = clean_list(
        [item for row in scenarios for item in (row.get("success_criteria") or [])]
    )
    warnings: list[str] = []
    if "insufficient_evidence" in (hidden.get("rupture_triggers") or []):
        warnings.append("rupture_triggers_require_R2_enrichment")
    if not interpersonal:
        warnings.append("interpersonal_context_requires_R2_enrichment")
    if not relationship:
        warnings.append("relationship_context_requires_R2_enrichment")

    return {
        "schema_version": "theraskill_patient_persona_v2",
        "persona_id": f"persona::{case_id}",
        "case_id": case_id,
        "immutable_identity": {
            "display_name": f"来访者-{bundle.get('metadata', {}).get('case_index') or case_id[:8]}",
            "demographic_profile": demographics,
            "facts": identity_facts,
        },
        "social_world": {
            "relationship_nodes": merge_facts([*interpersonal, *relationship]),
            "support_resources": merge_facts([*protective, *resources]),
            "relationship_tensions": merge_facts([*relationship, *latent_conflicts]),
        },
        "life_history": {
            "events": life_events,
            "prior_counseling_progress": prior_progress,
            "unresolved_events": unresolved,
        },
        "psychological_model": {
            "presenting_problems": presenting,
            "triggers_and_context": triggers,
            "maintaining_factors": maintaining,
            "protective_factors": protective_model,
            "goals": goals,
            "risk_watchpoints": risks,
            "coping_patterns": coping,
            "tensions_and_contradictions": tensions,
            "framework_overlays": {
                "status": "not_normalized_in_R1",
                "source_techniques": clean_list(continuity.get("previously_used_techniques") or []),
            },
        },
        "communication_profile": {
            "cognitive_style": cognitive_style,
            "emotional_expression": emotional_style,
            "help_seeking_style": help_style,
            "readiness_style": readiness_style,
            "response_length": "not_yet_enriched",
            "vocabulary_and_abstraction": "not_yet_enriched",
            "narrative_organization": "not_yet_enriched",
            "initiative_level": "not_yet_enriched",
            "hesitation_and_avoidance": avoidance_style,
            "advice_response_style": advice_style,
            "natural_variation_examples": [],
        },
        "disclosure_graph": {
            "nodes": disclosure_nodes,
            "initially_disclosed_fact_ids": surface_ids,
        },
        "behavioral_constitution": {
            "principles": build_principles(case_id, visible, hidden, unit_ids),
        },
        "initial_state": dict((bundle.get("runtime_state_seed") or {}).get("initial_session_state") or {}),
        "episodic_memory_seed": dict((bundle.get("runtime_state_seed") or {}).get("episodic_memory_seed") or {}),
        "training_contract": {
            "learner_visible_case_brief": clean_text(scenario.get("scenario_brief")),
            "learner_level": clean_text((bundle.get("metadata") or {}).get("difficulty_band")) or "unspecified",
            "training_objectives": training_objectives,
            "expected_challenge": clean_text(scenario.get("scenario_family")) or "general_patient_interview",
            "success_criteria": training_objectives,
            "hidden_fact_ids": hidden_ids,
            "instructor_reveal_fact_ids": [node["fact_id"] for node in disclosure_nodes],
            "runtime_priors": dict(bundle.get("runtime_priors") or {}),
        },
        "provenance": {
            "migration_policy": "deterministic_lossless_projection_v1_to_v2",
            "source_bundle_path": str(source_path),
            "source_bundle_sha256": str(file_sha256(source_path)),
            "source_bundle_content_sha256": canonical_sha256(bundle),
            "source_id": clean_text(bundle.get("source_id")),
            "source_unit_ids": dict(layer.get("source_unit_ids") or {}),
            "source_alignment": dict(bridge),
        },
        "quality": {
            "status": "complete",
            "migration_stage": "R1_deterministic_projection",
            "enrichment_status": "not_started",
            "warnings": warnings,
            "synthetic_fill_fields": ["immutable_identity.display_name"],
        },
        "legacy_compatibility": {
            "source_bundle_snapshot": bundle,
        },
    }


def directory_digest(rows: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda item: item["case_id"]):
        digest.update(f"{row['case_id']}\t{row['sha256']}\n".encode("utf-8"))
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    bundle_dir = Path(args.bundle_dir).resolve()
    scenario_path = Path(args.scenario_registry).resolve()
    output_dir = Path(args.output_dir).resolve()
    persona_schema_path = Path(args.persona_schema).resolve()
    disclosure_schema_path = Path(args.disclosure_schema).resolve()

    for path in (Path(args.index), Path(args.manifest), Path(args.qa_report), Path(args.summary)):
        ensure_output_path(path, overwrite=args.overwrite)
    if output_dir.exists() and any(output_dir.glob("*.json")) and not args.overwrite:
        raise FileExistsError(f"output directory contains persona files; pass --overwrite: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.overwrite:
        for stale in output_dir.glob("*.json"):
            stale.unlink()

    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    persona_schema = read_json(persona_schema_path)
    disclosure_schema = read_json(disclosure_schema_path)
    registry = Registry().with_resource(
        disclosure_schema["$id"], Resource.from_contents(disclosure_schema)
    )
    validator = Draft202012Validator(persona_schema, registry=registry)
    scenarios_by_case = scenario_index(scenario_path)
    bundle_paths = sorted(bundle_dir.glob("*.json"))
    if args.limit_cases is not None:
        bundle_paths = bundle_paths[: args.limit_cases]

    issues: list[dict[str, Any]] = []
    index_rows: list[dict[str, Any]] = []
    origin_counter: Counter[str] = Counter()
    sensitivity_counter: Counter[str] = Counter()
    warning_counter: Counter[str] = Counter()
    schema_valid_count = 0
    source_snapshot_match_count = 0
    provenance_complete_count = 0
    dangling_prerequisite_count = 0
    unsupported_synthetic_clinical_fact_count = 0
    persona_ids: set[str] = set()

    for source_path in bundle_paths:
        bundle = read_json(source_path)
        case_id = clean_text(bundle.get("case_id"))
        persona = build_persona(bundle, source_path, scenarios_by_case.get(case_id, []))
        validation_errors = sorted(validator.iter_errors(persona), key=lambda error: list(error.path))
        if validation_errors:
            issues.append(
                {
                    "issue": "schema_validation_failed",
                    "case_id": case_id,
                    "errors": [
                        {"path": ".".join(map(str, error.path)), "message": error.message}
                        for error in validation_errors[:20]
                    ],
                }
            )
        else:
            schema_valid_count += 1

        snapshot = (persona.get("legacy_compatibility") or {}).get("source_bundle_snapshot")
        if snapshot == bundle and canonical_sha256(snapshot) == persona["provenance"]["source_bundle_content_sha256"]:
            source_snapshot_match_count += 1
        else:
            issues.append({"issue": "lossless_snapshot_mismatch", "case_id": case_id})

        if (
            persona["provenance"].get("source_bundle_sha256")
            and persona["provenance"].get("source_bundle_content_sha256")
            and persona["provenance"].get("source_unit_ids")
        ):
            provenance_complete_count += 1
        else:
            issues.append({"issue": "incomplete_provenance", "case_id": case_id})

        nodes = persona["disclosure_graph"]["nodes"]
        node_ids = {node["fact_id"] for node in nodes}
        for node in nodes:
            origin_counter[node["origin"]] += 1
            sensitivity_counter[node["sensitivity"]] += 1
            dangling = set(node["prerequisite_fact_ids"]) - node_ids
            if dangling:
                dangling_prerequisite_count += len(dangling)
                issues.append(
                    {
                        "issue": "dangling_disclosure_prerequisite",
                        "case_id": case_id,
                        "fact_id": node["fact_id"],
                        "dangling_ids": sorted(dangling),
                    }
                )
            if node["origin"] == "synthetic_fill" and any(
                term in node["content"] for term in FORBIDDEN_SYNTHETIC_CLINICAL_TERMS
            ):
                unsupported_synthetic_clinical_fact_count += 1
                issues.append(
                    {
                        "issue": "forbidden_synthetic_clinical_fact",
                        "case_id": case_id,
                        "fact_id": node["fact_id"],
                    }
                )
        for fact_group in (
            persona["immutable_identity"]["facts"],
            persona["social_world"]["relationship_nodes"],
            persona["psychological_model"]["presenting_problems"],
        ):
            for fact in fact_group:
                origin_counter[fact["origin"]] += 1
                if fact["origin"] not in ORIGIN_VALUES:
                    issues.append({"issue": "invalid_origin", "case_id": case_id, "fact_id": fact["fact_id"]})
        for warning in persona["quality"]["warnings"]:
            warning_counter[warning] += 1

        persona_id = persona["persona_id"]
        if persona_id in persona_ids:
            issues.append({"issue": "duplicate_persona_id", "case_id": case_id, "persona_id": persona_id})
        persona_ids.add(persona_id)

        output_path = output_dir / f"{case_id}.json"
        write_json(output_path, persona, overwrite=True)
        index_rows.append(
            {
                "schema_version": "theraskill_patient_persona_v2_index_entry_v1",
                "persona_id": persona_id,
                "case_id": case_id,
                "path": str(output_path),
                "sha256": file_sha256(output_path),
                "source_bundle_path": str(source_path),
                "source_bundle_sha256": file_sha256(source_path),
                "scenario_count": len(scenarios_by_case.get(case_id, [])),
                "disclosure_node_count": len(nodes),
                "principle_count": len(persona["behavioral_constitution"]["principles"]),
                "warnings": persona["quality"]["warnings"],
            }
        )

    write_jsonl(args.index, index_rows, overwrite=args.overwrite, sort_keys=True)
    scenario_case_ids = set(scenarios_by_case)
    output_case_ids = {row["case_id"] for row in index_rows}
    missing_scenario_personas = (
        [] if args.limit_cases is not None else sorted(scenario_case_ids - output_case_ids)
    )
    missing_persona_scenarios = sorted(output_case_ids - scenario_case_ids)
    if missing_scenario_personas:
        issues.append({"issue": "scenario_case_missing_persona", "case_ids": missing_scenario_personas[:100]})
    if missing_persona_scenarios:
        issues.append({"issue": "persona_case_missing_scenario", "case_ids": missing_persona_scenarios[:100]})

    expected_count = len(bundle_paths)
    status = "complete" if not issues else "incomplete"
    created_at = datetime.now(timezone.utc).isoformat()
    summary = {
        "schema_version": "theraskill_patient_persona_v2_summary_v1",
        "created_at": created_at,
        "status": status,
        "source_bundle_count": expected_count,
        "persona_count": len(index_rows),
        "schema_valid_count": schema_valid_count,
        "source_snapshot_match_count": source_snapshot_match_count,
        "provenance_complete_count": provenance_complete_count,
        "disclosure_sensitivity_distribution": dict(sorted(sensitivity_counter.items())),
        "fact_origin_distribution": dict(sorted(origin_counter.items())),
        "warning_distribution": dict(sorted(warning_counter.items())),
        "directory_sha256": directory_digest(index_rows),
        "issues_count": len(issues),
    }
    write_json(args.summary, summary, overwrite=args.overwrite)
    manifest = {
        "schema_version": "theraskill_patient_persona_v2_manifest_v1",
        "created_at": created_at,
        "status": status,
        "migration_stage": "R1_deterministic_projection",
        "source": {
            "bundle_dir": str(bundle_dir),
            "bundle_count": expected_count,
            "scenario_registry": str(scenario_path),
            "scenario_registry_sha256": file_sha256(scenario_path),
        },
        "schemas": {
            "patient_persona_v2": {"path": str(persona_schema_path), "sha256": file_sha256(persona_schema_path)},
            "disclosure_node_v1": {"path": str(disclosure_schema_path), "sha256": file_sha256(disclosure_schema_path)},
        },
        "output": {
            "persona_dir": str(output_dir),
            "persona_count": len(index_rows),
            "directory_sha256": directory_digest(index_rows),
            "index": {"path": str(args.index), "sha256": file_sha256(args.index)},
            "summary": {"path": str(args.summary), "sha256": file_sha256(args.summary)},
        },
        "issues_count": len(issues),
    }
    write_json(args.manifest, manifest, overwrite=args.overwrite)
    qa = {
        "schema_version": "theraskill_patient_persona_v2_qa_v1",
        "created_at": created_at,
        "status": status,
        "checks": {
            "input_output_count_equal": len(index_rows) == expected_count,
            "all_personas_schema_valid": schema_valid_count == expected_count,
            "all_source_snapshots_lossless": source_snapshot_match_count == expected_count,
            "all_provenance_complete": provenance_complete_count == expected_count,
            "duplicate_persona_ids_zero": len(persona_ids) == len(index_rows),
            "dangling_disclosure_prerequisites_zero": dangling_prerequisite_count == 0,
            "unsupported_synthetic_clinical_facts_zero": unsupported_synthetic_clinical_fact_count == 0,
            "scenario_registry_persona_coverage_complete": not missing_scenario_personas and not missing_persona_scenarios,
        },
        "counts": {
            "source_bundles": expected_count,
            "personas": len(index_rows),
            "schema_valid": schema_valid_count,
            "source_snapshot_match": source_snapshot_match_count,
            "provenance_complete": provenance_complete_count,
            "dangling_prerequisites": dangling_prerequisite_count,
            "unsupported_synthetic_clinical_facts": unsupported_synthetic_clinical_fact_count,
            "missing_scenario_personas": len(missing_scenario_personas),
            "missing_persona_scenarios": len(missing_persona_scenarios),
        },
        "issues": issues[:1000],
        "issues_count": len(issues),
    }
    write_json(args.qa_report, qa, overwrite=args.overwrite)
    print(
        json.dumps(
            {
                "status": status,
                "source_bundle_count": expected_count,
                "persona_count": len(index_rows),
                "schema_valid_count": schema_valid_count,
                "issues_count": len(issues),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
