#!/usr/bin/env python3
"""Transactionally materialize verified enrichment patches into persona v2 artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
for path in (COMMON_DIR, PROJECT_ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from io_utils import file_sha256, read_json, write_json, write_jsonl  # noqa: E402
from simulation_surface_registry import (  # noqa: E402
    SIMULATION_DISCLOSURE_NODE_V1_SCHEMA_PATH,
    SIMULATION_PATIENT_PERSONA_V2_SCHEMA_PATH,
    SIMULATION_PERSONA_ENRICHMENT_OUTPUT_PATH,
    SIMULATION_PERSONA_V2_DIR,
    SIMULATION_PERSONA_V2_INDEX_PATH,
    SIMULATION_PERSONA_V2_MANIFEST_PATH,
    SIMULATION_PERSONA_V2_QA_PATH,
    SIMULATION_PERSONA_V2_SUMMARY_PATH,
)
from theraskill_simulation.persona_compiler import (  # noqa: E402
    effective_disclosure_nodes,
    effective_initially_disclosed_fact_ids,
)


FORBIDDEN_SYNTHETIC_CLINICAL_TERMS = (
    "诊断", "共病", "自杀", "自伤", "暴力", "虐待", "创伤", "住院", "药物", "违法"
)
R2_WARNING_SUFFIX = "_requires_R2_enrichment"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--persona-dir", default=str(SIMULATION_PERSONA_V2_DIR))
    parser.add_argument("--patches", default=str(SIMULATION_PERSONA_ENRICHMENT_OUTPUT_PATH))
    parser.add_argument("--persona-schema", default=str(SIMULATION_PATIENT_PERSONA_V2_SCHEMA_PATH))
    parser.add_argument("--disclosure-schema", default=str(SIMULATION_DISCLOSURE_NODE_V1_SCHEMA_PATH))
    parser.add_argument("--staging-dir")
    parser.add_argument("--index", default=str(SIMULATION_PERSONA_V2_INDEX_PATH))
    parser.add_argument("--manifest", default=str(SIMULATION_PERSONA_V2_MANIFEST_PATH))
    parser.add_argument("--qa-report", default=str(SIMULATION_PERSONA_V2_QA_PATH))
    parser.add_argument("--summary", default=str(SIMULATION_PERSONA_V2_SUMMARY_PATH))
    parser.add_argument("--expected-personas", type=int, default=3134)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--overwrite-staging", action="store_true")
    return parser.parse_args()


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def clean_list(value: Any) -> list[str]:
    values = value if isinstance(value, list) else ([] if value in (None, "") else [value])
    return list(dict.fromkeys(text for item in values if (text := clean_text(item))))


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_{hashlib.sha1(chr(10).join(parts).encode('utf-8')).hexdigest()[:16]}"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def normalize_source_path(path: str) -> str:
    path = clean_text(path)
    if not path:
        return "llm_enrichment.bounded_inference"
    if path.startswith(("authoritative_layer.", "runtime_state_seed.")):
        return path
    if path.startswith(("visible_profile.", "hidden_profile.", "continuity_seed.", "patient_formulation.", "report_dialogue_bridge.")):
        return f"authoritative_layer.{path}"
    return f"authoritative_layer.{path}"


def source_unit_ids(persona: dict[str, Any]) -> list[str]:
    raw = (persona.get("provenance") or {}).get("source_unit_ids") or {}
    return clean_list(raw.values() if isinstance(raw, dict) else raw)


def make_fact(case_id: str, field: str, item: dict[str, Any], unit_ids: list[str]) -> dict[str, Any]:
    content = clean_text(item.get("content"))
    evidence_paths = clean_list(item.get("evidence_paths"))
    return {
        "fact_id": stable_id("fact", case_id, field, content),
        "content": content,
        "origin": item.get("origin"),
        "confidence": item.get("confidence"),
        "source_paths": clean_list([normalize_source_path(path) for path in evidence_paths] or [f"llm_enrichment.{field}"]),
        "source_unit_ids": unit_ids,
        "evidence_spans": evidence_paths,
    }


def merge_by_content(existing: list[dict[str, Any]], additions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = list(existing)
    seen = {clean_text(item.get("content")) for item in result}
    for item in additions:
        content = clean_text(item.get("content"))
        if content and content not in seen:
            result.append(item)
            seen.add(content)
    return result


def make_principle(case_id: str, item: dict[str, Any], unit_ids: list[str]) -> dict[str, Any]:
    paths = clean_list(item.get("evidence_paths"))
    return {
        "principle_id": stable_id(
            "principle", case_id, clean_text(item.get("category")), clean_text(item.get("when")), clean_text(item.get("then"))
        ),
        "category": item.get("category"),
        "when": clean_text(item.get("when")),
        "then": clean_text(item.get("then")),
        "unless": clean_text(item.get("unless")),
        "origin": item.get("origin"),
        "source_paths": clean_list([normalize_source_path(path) for path in paths] or ["llm_enrichment.behavioral_principles"]),
        "source_unit_ids": unit_ids,
    }


def apply_patch(persona: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    patch = row["patch"]
    case_id = persona["case_id"]
    unit_ids = source_unit_ids(persona)
    communication = persona["communication_profile"]
    patch_communication = patch["communication_profile"]
    for field in ("response_length", "vocabulary_and_abstraction", "narrative_organization", "initiative_level"):
        communication[field] = clean_text(patch_communication[field])
    for field in ("emotional_expression", "hesitation_and_avoidance", "advice_response_style", "natural_variation_examples"):
        additions = [make_fact(case_id, f"communication_profile.{field}", item, unit_ids) for item in patch_communication[field]]
        communication[field] = merge_by_content(communication[field], additions)

    event_ids = {event["fact"]["content"] for event in persona["life_history"]["events"]}
    for item in patch["life_history_events"]:
        if item["content"] in event_ids:
            continue
        fact = make_fact(case_id, "life_history.events", item, unit_ids)
        persona["life_history"]["events"].append(
            {
                "event_id": stable_id("event", case_id, item["relative_time"], item["event_type"], item["content"]),
                "relative_time": item["relative_time"],
                "event_type": item["event_type"],
                "fact": fact,
            }
        )
        event_ids.add(item["content"])

    relationship_facts = [
        make_fact(case_id, "social_world.relationship_nodes", item, unit_ids)
        for item in patch["relationship_additions"]
    ]
    persona["social_world"]["relationship_nodes"] = merge_by_content(
        persona["social_world"]["relationship_nodes"], relationship_facts
    )

    nodes_by_content = {node["content"]: node for node in persona["disclosure_graph"]["nodes"]}
    for adjustment in patch["disclosure_adjustments"]:
        node = nodes_by_content.get(adjustment["fact_content"])
        if not node:
            continue
        node["sensitivity"] = adjustment["sensitivity"]
        node["facilitating_conditions"] = clean_list(
            [*node["facilitating_conditions"], *adjustment["facilitating_conditions"]]
        )
        node["blocking_conditions"] = clean_list(
            [*node["blocking_conditions"], *adjustment["blocking_conditions"]]
        )
        node["allowed_state_bands"] = {
            "surface": ["surface", "moderate", "deep"],
            "moderate": ["moderate", "deep"],
            "sensitive": ["deep"],
        }[node["sensitivity"]]
        node["default_response_mode"] = {
            "surface": "answer", "moderate": "hint", "sensitive": "withhold"
        }[node["sensitivity"]]

    principles = [make_principle(case_id, item, unit_ids) for item in patch["behavioral_principles"]]
    for trigger in patch["rupture_triggers"]:
        principles.append(
            make_principle(
                case_id,
                {
                    "category": "alliance_and_rupture",
                    "when": trigger["content"],
                    "then": "回应变得更谨慎、简短或回避，并降低进一步披露意愿",
                    "unless": "治疗师准确承认影响并进行关系修复",
                    "origin": trigger["origin"],
                    "evidence_paths": trigger["evidence_paths"],
                },
                unit_ids,
            )
        )
    existing_ids = {item["principle_id"] for item in persona["behavioral_constitution"]["principles"]}
    persona["behavioral_constitution"]["principles"].extend(
        principle for principle in principles if principle["principle_id"] not in existing_ids
    )

    persona["disclosure_graph"]["nodes"] = effective_disclosure_nodes(persona)
    persona["disclosure_graph"]["initially_disclosed_fact_ids"] = (
        effective_initially_disclosed_fact_ids(persona)
    )

    enrichment_provenance = row["provenance"]
    persona["provenance"]["enrichment"] = {
        "schema_version": row["schema_version"],
        "verified_patch_sha256": canonical_sha256(patch),
        "model": enrichment_provenance["model"],
        "model_base_url": enrichment_provenance["model_base_url"],
        "enrichment_prompt_sha256": enrichment_provenance["enrichment_prompt_sha256"],
        "verify_prompt_sha256": enrichment_provenance["verify_prompt_sha256"],
        "quality_attempts": enrichment_provenance["quality_attempts"],
        "input_compaction": enrichment_provenance["input_compaction"],
        "enrichment_strategy": enrichment_provenance.get("enrichment_strategy", "standard"),
    }
    persona["quality"]["migration_stage"] = "R2_verified_llm_enrichment"
    persona["quality"]["enrichment_status"] = "complete"
    persona["quality"]["warnings"] = [
        warning for warning in persona["quality"]["warnings"] if not warning.endswith(R2_WARNING_SUFFIX)
    ]
    if patch_communication["natural_variation_examples"]:
        persona["quality"]["synthetic_fill_fields"] = clean_list(
            [*persona["quality"]["synthetic_fill_fields"], "communication_profile.natural_variation_examples"]
        )
    return persona


def directory_digest(rows: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda item: item["case_id"]):
        digest.update(f"{row['case_id']}\t{row['sha256']}\n".encode("utf-8"))
    return digest.hexdigest()


def collect_facts(value: Any, facts: dict[str, dict[str, Any]]) -> None:
    if isinstance(value, dict):
        if value.get("fact_id") and value.get("origin") and value.get("content"):
            facts[str(value["fact_id"])] = value
        for child in value.values():
            collect_facts(child, facts)
    elif isinstance(value, list):
        for child in value:
            collect_facts(child, facts)


def patch_completeness_issues(persona: dict[str, Any], row: dict[str, Any]) -> list[str]:
    patch = row.get("patch") or {}
    communication = patch.get("communication_profile") or {}
    issues = []
    for field in ("response_length", "vocabulary_and_abstraction", "narrative_organization", "initiative_level"):
        if not clean_text(communication.get(field)):
            issues.append(f"communication_profile.{field}.empty")
    if not communication.get("natural_variation_examples"):
        issues.append("communication_profile.natural_variation_examples.empty")
    if len(patch.get("behavioral_principles") or []) < 2:
        issues.append("behavioral_principles.too_few")
    warnings = set((persona.get("quality") or {}).get("warnings") or [])
    if "rupture_triggers_require_R2_enrichment" in warnings and not patch.get("rupture_triggers"):
        issues.append("rupture_triggers.required_target_empty")
    if (
        warnings
        & {
            "interpersonal_context_requires_R2_enrichment",
            "relationship_context_requires_R2_enrichment",
        }
        and not patch.get("relationship_additions")
    ):
        issues.append("relationship_additions.required_target_empty")
    return issues


def main() -> int:
    args = parse_args()
    persona_dir = Path(args.persona_dir).resolve()
    staging_dir = Path(args.staging_dir).resolve() if args.staging_dir else persona_dir.parent / ".v2_r2_staging"
    persona_paths = sorted(persona_dir.glob("*.json"))
    patch_rows = read_jsonl(Path(args.patches))
    existing_index_rows = read_jsonl(Path(args.index)) if Path(args.index).exists() else []
    existing_index = {str(row.get("case_id")): row for row in existing_index_rows}
    patches_by_case = {row.get("case_id"): row for row in patch_rows}
    persona_case_ids = {path.stem for path in persona_paths}
    issues: list[dict[str, Any]] = []
    if len(persona_paths) != args.expected_personas:
        issues.append(
            {
                "issue": "persona_count_mismatch",
                "expected": args.expected_personas,
                "actual": len(persona_paths),
            }
        )
    if len(patches_by_case) != len(patch_rows):
        issues.append({"issue": "duplicate_patch_case_id", "rows": len(patch_rows), "unique": len(patches_by_case)})
    if set(patches_by_case) != persona_case_ids:
        issues.append(
            {
                "issue": "patch_persona_coverage_mismatch",
                "missing_patches": sorted(persona_case_ids - set(patches_by_case))[:100],
                "unknown_patches": sorted(set(patches_by_case) - persona_case_ids)[:100],
            }
        )
    nonpass_patch_cases = [
        str(row.get("case_id"))
        for row in patch_rows
        if (row.get("verification") or {}).get("verdict") != "pass"
    ]
    if nonpass_patch_cases:
        issues.append(
            {
                "issue": "nonpass_verified_patch",
                "count": len(nonpass_patch_cases),
                "case_ids": nonpass_patch_cases[:100],
            }
        )
    for source_path in persona_paths:
        case_id = source_path.stem
        if case_id not in patches_by_case:
            continue
        completeness = patch_completeness_issues(read_json(source_path), patches_by_case[case_id])
        if completeness:
            issues.append(
                {"issue": "incomplete_verified_patch", "case_id": case_id, "details": completeness}
            )
    if issues:
        raise ValueError(json.dumps(issues, ensure_ascii=False))

    if staging_dir.exists():
        if not args.overwrite_staging:
            raise FileExistsError(f"staging exists; pass --overwrite-staging: {staging_dir}")
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    persona_schema = read_json(args.persona_schema)
    disclosure_schema = read_json(args.disclosure_schema)
    registry = Registry().with_resource(
        disclosure_schema["$id"], Resource.from_contents(disclosure_schema)
    )
    validator = Draft202012Validator(persona_schema, registry=registry)
    index_rows: list[dict[str, Any]] = []
    origin_counter: Counter[str] = Counter()
    sensitivity_counter: Counter[str] = Counter()
    unsupported_synthetic = 0
    unique_facts: dict[str, dict[str, Any]] = {}
    source_snapshot_match_count = 0
    enrichment_provenance_count = 0

    for source_path in persona_paths:
        persona = apply_patch(read_json(source_path), patches_by_case[source_path.stem])
        errors = sorted(validator.iter_errors(persona), key=lambda error: list(error.path))
        if errors:
            issues.append(
                {
                    "issue": "schema_validation_failed",
                    "case_id": source_path.stem,
                    "errors": [
                        {"path": ".".join(map(str, error.path)), "message": error.message}
                        for error in errors[:20]
                    ],
                }
            )
            continue
        snapshot = (persona.get("legacy_compatibility") or {}).get("source_bundle_snapshot")
        if canonical_sha256(snapshot) == (persona.get("provenance") or {}).get("source_bundle_content_sha256"):
            source_snapshot_match_count += 1
        else:
            issues.append({"issue": "lossless_source_snapshot_mismatch", "case_id": source_path.stem})
        if (persona.get("provenance") or {}).get("enrichment", {}).get("verified_patch_sha256") == canonical_sha256(
            patches_by_case[source_path.stem]["patch"]
        ):
            enrichment_provenance_count += 1
        else:
            issues.append({"issue": "enrichment_provenance_mismatch", "case_id": source_path.stem})
        for node in persona["disclosure_graph"]["nodes"]:
            sensitivity_counter[node["sensitivity"]] += 1
        case_facts: dict[str, dict[str, Any]] = {}
        collect_facts(persona, case_facts)
        unique_facts.update(case_facts)
        for fact in case_facts.values():
            if fact["origin"] == "synthetic_fill" and any(
                term in fact["content"] for term in FORBIDDEN_SYNTHETIC_CLINICAL_TERMS
            ):
                unsupported_synthetic += 1
                issues.append(
                    {
                        "issue": "unsupported_synthetic_clinical_content",
                        "case_id": source_path.stem,
                        "fact_id": fact["fact_id"],
                    }
                )
        output_path = staging_dir / source_path.name
        write_json(output_path, persona, overwrite=True)
        index_rows.append(
            {
                "schema_version": "theraskill_patient_persona_v2_index_entry_v1",
                "persona_id": persona["persona_id"],
                "case_id": persona["case_id"],
                "path": str(persona_dir / source_path.name),
                "sha256": file_sha256(output_path),
                "source_bundle_path": persona["provenance"]["source_bundle_path"],
                "source_bundle_sha256": persona["provenance"]["source_bundle_sha256"],
                "scenario_count": int(
                    (existing_index.get(persona["case_id"]) or {}).get("scenario_count") or 0
                ),
                "disclosure_node_count": len(persona["disclosure_graph"]["nodes"]),
                "principle_count": len(persona["behavioral_constitution"]["principles"]),
                "warnings": persona["quality"]["warnings"],
            }
        )

    expected = len(persona_paths)
    origin_counter.update(fact["origin"] for fact in unique_facts.values())
    checks = {
        "persona_count_matches_expected": expected == args.expected_personas,
        "patch_count_matches_personas": len(patch_rows) == expected,
        "unique_patch_case_ids": len(patches_by_case) == expected,
        "all_personas_schema_valid": len(index_rows) == expected,
        "all_personas_enrichment_complete": len(index_rows) == expected,
        "all_source_snapshots_lossless": source_snapshot_match_count == expected,
        "all_enrichment_provenance_complete": enrichment_provenance_count == expected,
        "unsupported_synthetic_clinical_content_zero": unsupported_synthetic == 0,
    }
    status = "complete" if all(checks.values()) and not issues else "incomplete"
    if status != "complete":
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise ValueError(json.dumps({"status": status, "checks": checks, "issues": issues[:20]}, ensure_ascii=False))

    if args.publish:
        previous_dir = persona_dir.parent / ".v2_r1_previous"
        if previous_dir.exists():
            shutil.rmtree(previous_dir)
        persona_dir.rename(previous_dir)
        staging_dir.rename(persona_dir)
        shutil.rmtree(previous_dir)

    created_at = datetime.now(timezone.utc).isoformat()
    write_jsonl(args.index, index_rows, overwrite=True, sort_keys=True)
    summary = {
        "schema_version": "theraskill_patient_persona_v2_summary_v1",
        "created_at": created_at,
        "status": status,
        "migration_stage": "R2_verified_llm_enrichment",
        "source_bundle_count": expected,
        "persona_count": len(index_rows),
        "schema_valid_count": len(index_rows),
        "verified_enrichment_count": len(patch_rows),
        "disclosure_sensitivity_distribution": dict(sorted(sensitivity_counter.items())),
        "fact_origin_distribution": dict(sorted(origin_counter.items())),
        "directory_sha256": directory_digest(index_rows),
        "issues_count": len(issues),
    }
    write_json(args.summary, summary, overwrite=True)
    qa = {
        "schema_version": "theraskill_patient_persona_v2_qa_v1",
        "created_at": created_at,
        "status": status,
        "migration_stage": "R2_verified_llm_enrichment",
        "checks": checks,
        "counts": {
            "personas": expected,
            "patches": len(patch_rows),
            "schema_valid": len(index_rows),
            "unsupported_synthetic_clinical_content": unsupported_synthetic,
            "source_snapshot_match": source_snapshot_match_count,
            "enrichment_provenance_complete": enrichment_provenance_count,
        },
        "issues": issues,
        "issues_count": len(issues),
    }
    write_json(args.qa_report, qa, overwrite=True)
    manifest = {
        "schema_version": "theraskill_patient_persona_v2_manifest_v1",
        "created_at": created_at,
        "status": status,
        "migration_stage": "R2_verified_llm_enrichment",
        "source": {
            "persona_r1_count": expected,
            "verified_patches": {"path": str(args.patches), "sha256": file_sha256(args.patches), "row_count": len(patch_rows)},
        },
        "schemas": {
            "patient_persona_v2": {"path": str(args.persona_schema), "sha256": file_sha256(args.persona_schema)},
            "disclosure_node_v1": {"path": str(args.disclosure_schema), "sha256": file_sha256(args.disclosure_schema)},
        },
        "output": {
            "published": args.publish,
            "persona_dir": str(persona_dir if args.publish else staging_dir),
            "persona_count": len(index_rows),
            "directory_sha256": directory_digest(index_rows),
            "index": {"path": str(args.index), "sha256": file_sha256(args.index)},
            "summary": {"path": str(args.summary), "sha256": file_sha256(args.summary)},
        },
        "issues_count": len(issues),
    }
    write_json(args.manifest, manifest, overwrite=True)
    print(json.dumps({"status": status, "persona_count": len(index_rows), "published": args.publish}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
