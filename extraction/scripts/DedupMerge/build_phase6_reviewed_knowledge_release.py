#!/usr/bin/env python3
"""Build Phase 6 full-coverage reviewed knowledge release.

This script reads the full text view plus dedup registries and writes a
reviewed-release layer with full accounting. It does not reuse withdrawn
sampled merge candidates and does not write outputs/knowledge/merge/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "_common"))

from io_utils import file_sha256, iter_jsonl, write_json  # noqa: E402


DEFAULT_TEXT_VIEW = "outputs/knowledge/dedup/indexes/unit_text_views/all_units.text_view.jsonl"
DEFAULT_OUTPUT_DIR = "outputs/knowledge/phase6_release"
DEFAULT_REPORT = "outputs/knowledge/phase6_release/manifests/phase6_reviewed_knowledge_release_report.json"
DEFAULT_MANIFEST = "outputs/knowledge/phase6_release/manifests/phase6_reviewed_knowledge_release_manifest.json"
DEFAULT_QA = "outputs/knowledge/phase6_release/manifests/phase6_reviewed_knowledge_release_qa.json"


REVIEWED_REGISTRIES = {
    "style_response": "outputs/knowledge/dedup/registries/style_response_reviewed_clusters/style_response.reviewed_cluster_registry.jsonl",
    "qa_psychoeducation": "outputs/knowledge/dedup/registries/qa_psychoeducation_reviewed_clusters/qa_psychoeducation.reviewed_cluster_registry.jsonl",
    "clinical_risk_assessment": "outputs/knowledge/dedup/registries/clinical_risk_assessment_reviewed_clusters/clinical_risk_assessment.reviewed_cluster_registry.jsonl",
    "therapy_process": "outputs/knowledge/dedup/registries/therapy_process_reviewed_clusters/therapy_process.reviewed_cluster_registry.jsonl",
    "method_rubric": "outputs/knowledge/dedup/registries/method_rubric_registry/method_rubric.registry.jsonl",
    "synthetic_construction": "outputs/knowledge/dedup/registries/synthetic_construction_registry/synthetic_construction.registry.jsonl",
}


EXCLUDED_REGISTRIES = {
    "style_response": "outputs/knowledge/dedup/registries/style_response_reviewed_clusters/style_response.excluded_from_reviewed_cluster_registry.jsonl",
    "qa_psychoeducation": "outputs/knowledge/dedup/registries/qa_psychoeducation_reviewed_clusters/qa_psychoeducation.excluded_from_reviewed_cluster_registry.jsonl",
    "clinical_risk_assessment": "outputs/knowledge/dedup/registries/clinical_risk_assessment_reviewed_clusters/clinical_risk_assessment.excluded_from_reviewed_cluster_registry.jsonl",
    "therapy_process": "outputs/knowledge/dedup/registries/therapy_process_reviewed_clusters/therapy_process.excluded_from_reviewed_cluster_registry.jsonl",
}


SOURCE_PROVENANCE_REGISTRY = "outputs/knowledge/dedup/registries/source_provenance_registry/source_provenance.registry.jsonl"


FAMILY_ALIASES = {
    "risk_assessment": "clinical_risk_assessment",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text-view", default=DEFAULT_TEXT_VIEW)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--qa-report", default=DEFAULT_QA)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(prefix: str, *parts: Any) -> str:
    raw = "::".join(str(part) for part in parts)
    return prefix + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def canonical_family(family: str | None) -> str:
    value = str(family or "unknown")
    return FAMILY_ALIASES.get(value, value)


def text_view_card(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "unit_id": row.get("unit_id"),
        "dataset": row.get("dataset"),
        "knowledge_type": row.get("knowledge_type"),
        "dedup_family": canonical_family(row.get("dedup_family")),
        "language": row.get("language"),
        "evidence_tier": row.get("evidence_tier"),
        "source_construction_type": row.get("source_construction_type"),
        "license_constraints": row.get("license_constraints") or [],
        "risk_or_safety_flags": row.get("risk_or_safety_flags") or [],
        "dedup_text_sha256": row.get("dedup_text_sha256"),
        "dedup_text_preview": str(row.get("dedup_text") or "")[:1000],
        "dedup_key_fields": row.get("dedup_key_fields") or {},
        "raw_file": row.get("raw_file"),
        "raw_line_no": row.get("raw_line_no"),
    }


def add_text_distribution(counter: Counter[str], value: Any) -> None:
    if isinstance(value, list):
        if not value:
            counter["__empty_list__"] += 1
        for item in value:
            counter[str(item)] += 1
    elif value in (None, ""):
        counter["__missing__"] += 1
    else:
        counter[str(value)] += 1


def distributions(member_unit_ids: list[str], text_rows: dict[str, dict[str, Any]]) -> dict[str, dict[str, int]]:
    dataset: Counter[str] = Counter()
    knowledge_type: Counter[str] = Counter()
    evidence_tier: Counter[str] = Counter()
    source_type: Counter[str] = Counter()
    language: Counter[str] = Counter()
    license_constraints: Counter[str] = Counter()
    missing = 0
    for unit_id in member_unit_ids:
        row = text_rows.get(unit_id)
        if not row:
            missing += 1
            continue
        add_text_distribution(dataset, row.get("dataset"))
        add_text_distribution(knowledge_type, row.get("knowledge_type"))
        add_text_distribution(evidence_tier, row.get("evidence_tier"))
        add_text_distribution(source_type, row.get("source_construction_type"))
        add_text_distribution(language, row.get("language"))
        add_text_distribution(license_constraints, row.get("license_constraints") or [])
    result = {
        "dataset_distribution": dict(dataset.most_common()),
        "knowledge_type_distribution": dict(knowledge_type.most_common()),
        "evidence_tier_distribution": dict(evidence_tier.most_common()),
        "source_construction_type_distribution": dict(source_type.most_common()),
        "language_distribution": dict(language.most_common()),
        "license_constraints_distribution": dict(license_constraints.most_common()),
    }
    if missing:
        result["missing_text_view_members"] = {"__missing__": missing}
    return result


def retrieval_policy(kind: str, family: str, row: dict[str, Any]) -> dict[str, Any]:
    if kind == "provenance_only" or family == "source_inventory":
        return {
            "retrieval_eligible": False,
            "embedding_eligible": False,
            "default_route": "provenance_lookup_only",
        }
    if kind == "deferred_accounting":
        return {
            "retrieval_eligible": False,
            "embedding_eligible": False,
            "default_route": "accounting_only_requires_review",
        }
    if family == "clinical_risk_assessment":
        return {
            "retrieval_eligible": True,
            "embedding_eligible": True,
            "default_route": "clinical_reviewed_cluster_retrieval_with_label_guard",
        }
    if family == "synthetic_construction":
        return {
            "retrieval_eligible": True,
            "embedding_eligible": True,
            "default_route": "synthetic_construction_method_retrieval",
        }
    return {
        "retrieval_eligible": True,
        "embedding_eligible": True,
        "default_route": "reviewed_cluster_retrieval" if kind == "reviewed_cluster" else "raw_passthrough_retrieval",
    }


def content_from_registry(family: str, row: dict[str, Any], text_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    representative_unit_id = row.get("representative_unit_id") or row.get("unit_id")
    representative = text_rows.get(str(representative_unit_id or ""))
    label = (
        row.get("canonical_pattern_label")
        or row.get("canonical_topic_label")
        or row.get("canonical_label")
        or row.get("registry_kind")
        or row.get("registry_status")
    )
    summary = (
        row.get("shared_intent")
        or row.get("shared_answer_intent")
        or row.get("shared_question_or_need")
        or row.get("shared_pattern")
        or row.get("source_faithfulness_notes")
        or row.get("dedup_text_preview")
    )
    return {
        "summary": summary,
        "canonical_label": label,
        "source_faithfulness_notes": row.get("source_faithfulness_notes"),
        "representative_text_ref": None
        if not representative
        else {
            "unit_id": representative.get("unit_id"),
            "raw_file": representative.get("raw_file"),
            "raw_line_no": representative.get("raw_line_no"),
            "dedup_text_sha256": representative.get("dedup_text_sha256"),
            "dedup_text_preview": str(representative.get("dedup_text") or "")[:1000],
        },
        "registry_fields": {
            key: row.get(key)
            for key in [
                "registry_status",
                "registry_source_stage",
                "confidence",
                "clinical_boundary_notes",
                "shared_question_or_need",
                "shared_answer_intent",
                "shared_intent",
                "shared_pattern",
                "next_review_gate",
                "before_final_merge_requires",
            ]
            if key in row
        },
    }


def member_ids_from_registry_row(row: dict[str, Any]) -> list[str]:
    member_unit_ids = list(row.get("member_unit_ids") or [])
    if not member_unit_ids and row.get("unit_id"):
        member_unit_ids = [str(row["unit_id"])]
    return member_unit_ids


def reviewed_release_record(family: str, row: dict[str, Any], source_file: str, text_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    member_unit_ids = member_ids_from_registry_row(row)
    representative_unit_id = row.get("representative_unit_id") or (member_unit_ids[0] if member_unit_ids else row.get("unit_id"))
    registry_id = row.get("registry_cluster_id") or row.get("registry_id")
    release_type = "raw_passthrough" if len(member_unit_ids) <= 1 and family == "method_rubric" else "reviewed_cluster"
    if family == "synthetic_construction":
        release_type = "reviewed_cluster" if len(member_unit_ids) > 1 else "raw_passthrough"
    record = {
        "schema_version": "theraskill_reviewed_knowledge_release_unit_v1",
        "release_unit_id": stable_id("rku_", family, release_type, registry_id or representative_unit_id, ",".join(sorted(member_unit_ids))),
        "release_unit_type": release_type,
        "dedup_family": family,
        "canonical_knowledge_type": canonical_release_knowledge_type(family, release_type),
        "representative_unit_id": representative_unit_id,
        "member_unit_ids": member_unit_ids,
        "member_count": len(member_unit_ids),
        **distributions(member_unit_ids, text_rows),
        "retrieval_policy": retrieval_policy(release_type, family, row),
        "content": content_from_registry(family, row, text_rows),
        "provenance": {
            "raw_units_unchanged": True,
            "source_registry_file": source_file,
            "source_registry_id": registry_id,
            "source_stage": row.get("registry_source_stage") or row.get("registry_status"),
            "phase6_policy": "full_coverage_accounting_release_v1",
        },
        "quality": {
            "registry_status": row.get("registry_status"),
            "confidence": row.get("confidence"),
            "not_final_merged_unit_source_flag": row.get("not_final_merged_unit"),
        },
        "final_release_unit": True,
        "raw_units_unchanged": True,
    }
    return record


def canonical_release_knowledge_type(family: str, release_type: str) -> str:
    if release_type == "provenance_only":
        return "source_provenance_pointer"
    if release_type == "deferred_accounting":
        return f"{family}_deferred_accounting"
    if release_type == "raw_passthrough":
        return f"{family}_raw_passthrough"
    return f"reviewed_{family}_cluster"


def excluded_accounting_record(family: str, row: dict[str, Any], source_file: str, text_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    member_unit_ids = list(row.get("member_unit_ids") or [])
    source_id = row.get("source_queue_item_id") or row.get("source_component_id") or row.get("source_item_id") or row.get("excluded_status")
    return {
        "schema_version": "theraskill_reviewed_knowledge_release_unit_v1",
        "release_unit_id": stable_id("rku_", family, "deferred_accounting", source_id, ",".join(sorted(member_unit_ids))),
        "release_unit_type": "deferred_accounting",
        "dedup_family": family,
        "canonical_knowledge_type": canonical_release_knowledge_type(family, "deferred_accounting"),
        "representative_unit_id": member_unit_ids[0] if member_unit_ids else None,
        "member_unit_ids": member_unit_ids,
        "member_count": len(member_unit_ids),
        **distributions(member_unit_ids, text_rows),
        "retrieval_policy": retrieval_policy("deferred_accounting", family, row),
        "content": {
            "summary": row.get("excluded_reason"),
            "canonical_label": row.get("excluded_status"),
            "source_faithfulness_notes": row.get("source_faithfulness_notes"),
            "representative_text_ref": None,
            "registry_fields": {
                "excluded_status": row.get("excluded_status"),
                "excluded_reason": row.get("excluded_reason"),
                "source_stage": row.get("source_stage"),
            },
        },
        "provenance": {
            "raw_units_unchanged": True,
            "source_registry_file": source_file,
            "source_registry_id": source_id,
            "source_stage": row.get("source_stage"),
            "phase6_policy": "full_coverage_accounting_release_v1",
        },
        "quality": {
            "promotion_status": "not_promoted_requires_review_or_singleton_passthrough",
            "excluded_status": row.get("excluded_status"),
            "excluded_reason": row.get("excluded_reason"),
        },
        "final_release_unit": False,
        "raw_units_unchanged": True,
    }


def raw_passthrough_record(unit_id: str, row: dict[str, Any]) -> dict[str, Any]:
    family = canonical_family(row.get("dedup_family"))
    card = text_view_card(row)
    return {
        "schema_version": "theraskill_reviewed_knowledge_release_unit_v1",
        "release_unit_id": stable_id("rku_", family, "raw_passthrough", unit_id),
        "release_unit_type": "raw_passthrough",
        "dedup_family": family,
        "canonical_knowledge_type": canonical_release_knowledge_type(family, "raw_passthrough"),
        "representative_unit_id": unit_id,
        "member_unit_ids": [unit_id],
        "member_count": 1,
        "dataset_distribution": {str(row.get("dataset") or "unknown"): 1},
        "knowledge_type_distribution": {str(row.get("knowledge_type") or "unknown"): 1},
        "evidence_tier_distribution": {str(row.get("evidence_tier") or "unknown"): 1},
        "source_construction_type_distribution": {str(row.get("source_construction_type") or "unknown"): 1},
        "language_distribution": {str(row.get("language") or "unknown"): 1},
        "license_constraints_distribution": {"__empty_list__": 1} if not row.get("license_constraints") else {str(x): 1 for x in row.get("license_constraints") or []},
        "retrieval_policy": retrieval_policy("raw_passthrough", family, row),
        "content": {
            "summary": str(row.get("dedup_text") or "")[:1000],
            "canonical_label": row.get("knowledge_type"),
            "source_faithfulness_notes": None,
            "representative_text_ref": card,
            "registry_fields": {},
        },
        "provenance": {
            "raw_units_unchanged": True,
            "source_registry_file": None,
            "source_registry_id": None,
            "source_stage": "raw_text_view_passthrough",
            "phase6_policy": "full_coverage_accounting_release_v1",
        },
        "quality": {
            "promotion_status": "raw_passthrough_not_clustered",
        },
        "final_release_unit": True,
        "raw_units_unchanged": True,
    }


def provenance_only_record(row: dict[str, Any], source_file: str, text_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    unit_id = row.get("unit_id")
    family = "source_inventory"
    source_text = text_rows.get(str(unit_id or ""))
    member_unit_ids = [unit_id] if unit_id else []
    return {
        "schema_version": "theraskill_reviewed_knowledge_release_unit_v1",
        "release_unit_id": stable_id("rku_", family, "provenance_only", row.get("registry_id") or unit_id),
        "release_unit_type": "provenance_only",
        "dedup_family": family,
        "canonical_knowledge_type": canonical_release_knowledge_type(family, "provenance_only"),
        "representative_unit_id": unit_id,
        "member_unit_ids": member_unit_ids,
        "member_count": len(member_unit_ids),
        **distributions(member_unit_ids, text_rows),
        "retrieval_policy": retrieval_policy("provenance_only", family, row),
        "content": {
            "summary": row.get("source_pointer"),
            "canonical_label": "source_sample_inventory_pointer",
            "source_faithfulness_notes": "provenance pointer only; not content knowledge",
            "representative_text_ref": None if not source_text else text_view_card(source_text),
            "registry_fields": {
                "registry_status": row.get("registry_status"),
                "source_pointer": row.get("source_pointer"),
                "default_retrieval_route": row.get("default_retrieval_route"),
                "default_embedding_enabled": row.get("default_embedding_enabled"),
            },
        },
        "provenance": {
            "raw_units_unchanged": True,
            "source_registry_file": source_file,
            "source_registry_id": row.get("registry_id"),
            "source_stage": "source_provenance_registry",
            "phase6_policy": "full_coverage_accounting_release_v1",
        },
        "quality": {
            "promotion_status": "provenance_only_not_content_knowledge",
        },
        "final_release_unit": True,
        "raw_units_unchanged": True,
    }


def write_jsonl_stream(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("w", encoding="utf-8")


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    units_path = output_dir / "units" / "phase6_reviewed_knowledge_units.jsonl"
    accounting_path = output_dir / "accounting" / "phase6_member_accounting.jsonl"
    all_paths = [units_path, accounting_path, Path(args.report), Path(args.manifest), Path(args.qa_report)]
    if any(path.exists() for path in all_paths) and not args.overwrite:
        existing = [str(path) for path in all_paths if path.exists()]
        raise FileExistsError(f"outputs exist; pass --overwrite: {existing}")

    text_rows: dict[str, dict[str, Any]] = {}
    family_counter: Counter[str] = Counter()
    dataset_counter: Counter[str] = Counter()
    raw_file_counter: Counter[str] = Counter()
    for _, row in iter_jsonl(args.text_view, with_line_numbers=True):
        unit_id = str(row.get("unit_id") or "")
        if not unit_id:
            continue
        row["dedup_family"] = canonical_family(row.get("dedup_family"))
        text_rows[unit_id] = row
        family_counter[str(row.get("dedup_family") or "unknown")] += 1
        dataset_counter[str(row.get("dataset") or "unknown")] += 1
        raw_file_counter[str(row.get("raw_file") or "unknown")] += 1

    assigned: dict[str, str] = {}
    assigned_type: dict[str, str] = {}
    duplicate_assignments: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    release_type_counter: Counter[str] = Counter()
    release_family_counter: Counter[str] = Counter()
    release_member_counter: Counter[str] = Counter()
    retrieval_counter: Counter[str] = Counter()
    embedding_counter: Counter[str] = Counter()
    output_rows = 0

    def record_assignment(unit_id: str, release_id: str, release_type: str) -> None:
        if unit_id in assigned:
            duplicate_assignments.append(
                {
                    "unit_id": unit_id,
                    "first_release_unit_id": assigned[unit_id],
                    "second_release_unit_id": release_id,
                    "second_release_type": release_type,
                }
            )
        assigned[unit_id] = release_id
        assigned_type[unit_id] = release_type

    with write_jsonl_stream(units_path) as unit_handle:
        # Promotion/reviewed cluster units.
        for family, path in REVIEWED_REGISTRIES.items():
            source = Path(path)
            if not source.exists():
                issues.append({"issue": "missing_reviewed_registry", "family": family, "path": path})
                continue
            for _, row in iter_jsonl(source, with_line_numbers=True):
                rec = reviewed_release_record(family, row, path, text_rows)
                unit_handle.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
                output_rows += 1
                release_type_counter[rec["release_unit_type"]] += 1
                release_family_counter[family] += 1
                release_member_counter[family] += int(rec.get("member_count") or 0)
                retrieval_counter[str(rec["retrieval_policy"]["retrieval_eligible"])] += 1
                embedding_counter[str(rec["retrieval_policy"]["embedding_eligible"])] += 1
                for member_id in rec["member_unit_ids"]:
                    record_assignment(str(member_id), rec["release_unit_id"], rec["release_unit_type"])

        # Deferred/excluded accounting rows for reviewed families.
        for family, path in EXCLUDED_REGISTRIES.items():
            source = Path(path)
            if not source.exists():
                issues.append({"issue": "missing_excluded_registry", "family": family, "path": path})
                continue
            for _, row in iter_jsonl(source, with_line_numbers=True):
                rec = excluded_accounting_record(family, row, path, text_rows)
                unit_handle.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
                output_rows += 1
                release_type_counter[rec["release_unit_type"]] += 1
                release_family_counter[family] += 1
                release_member_counter[family] += int(rec.get("member_count") or 0)
                retrieval_counter[str(rec["retrieval_policy"]["retrieval_eligible"])] += 1
                embedding_counter[str(rec["retrieval_policy"]["embedding_eligible"])] += 1
                for member_id in rec["member_unit_ids"]:
                    record_assignment(str(member_id), rec["release_unit_id"], rec["release_unit_type"])

        # Source provenance-only rows.
        if not Path(SOURCE_PROVENANCE_REGISTRY).exists():
            issues.append({"issue": "missing_source_provenance_registry", "path": SOURCE_PROVENANCE_REGISTRY})
        else:
            for _, row in iter_jsonl(SOURCE_PROVENANCE_REGISTRY, with_line_numbers=True):
                rec = provenance_only_record(row, SOURCE_PROVENANCE_REGISTRY, text_rows)
                unit_handle.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
                output_rows += 1
                release_type_counter[rec["release_unit_type"]] += 1
                release_family_counter["source_inventory"] += 1
                release_member_counter["source_inventory"] += int(rec.get("member_count") or 0)
                retrieval_counter[str(rec["retrieval_policy"]["retrieval_eligible"])] += 1
                embedding_counter[str(rec["retrieval_policy"]["embedding_eligible"])] += 1
                for member_id in rec["member_unit_ids"]:
                    record_assignment(str(member_id), rec["release_unit_id"], rec["release_unit_type"])

        # Raw passthrough for all units not assigned by cluster/deferred/source registries.
        for unit_id in sorted(set(text_rows) - set(assigned)):
            row = text_rows[unit_id]
            rec = raw_passthrough_record(unit_id, row)
            unit_handle.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            output_rows += 1
            family = str(rec["dedup_family"])
            release_type_counter[rec["release_unit_type"]] += 1
            release_family_counter[family] += 1
            release_member_counter[family] += 1
            retrieval_counter[str(rec["retrieval_policy"]["retrieval_eligible"])] += 1
            embedding_counter[str(rec["retrieval_policy"]["embedding_eligible"])] += 1
            record_assignment(unit_id, rec["release_unit_id"], rec["release_unit_type"])

    missing_assignments = sorted(set(text_rows) - set(assigned))
    extra_assignments = sorted(set(assigned) - set(text_rows))
    if duplicate_assignments:
        issues.append({"issue": "member_assigned_multiple_times", "count": len(duplicate_assignments), "examples": duplicate_assignments[:20]})
    if missing_assignments:
        issues.append({"issue": "raw_units_missing_from_phase6_accounting", "count": len(missing_assignments), "examples": missing_assignments[:20]})
    if extra_assignments:
        issues.append({"issue": "phase6_members_not_in_text_view", "count": len(extra_assignments), "examples": extra_assignments[:20]})

    source_inventory_embedding = 0
    clinical_deferred_retrieval = 0
    assignment_type_counter = Counter(assigned_type.values())
    for unit_id, row in text_rows.items():
        family = canonical_family(row.get("dedup_family"))
        if family == "source_inventory" and assigned_type.get(unit_id) != "provenance_only":
            issues.append({"issue": "source_inventory_not_provenance_only", "unit_id": unit_id, "assignment_type": assigned_type.get(unit_id)})
            break
    # Stream-read output for policy checks without retaining all rows.
    for _, rec in iter_jsonl(units_path, with_line_numbers=True):
        if rec.get("dedup_family") == "source_inventory" and rec.get("retrieval_policy", {}).get("embedding_eligible") is not False:
            source_inventory_embedding += 1
        if rec.get("dedup_family") == "clinical_risk_assessment" and rec.get("release_unit_type") == "deferred_accounting" and rec.get("retrieval_policy", {}).get("retrieval_eligible") is not False:
            clinical_deferred_retrieval += 1
    if source_inventory_embedding:
        issues.append({"issue": "source_inventory_embedding_enabled", "count": source_inventory_embedding})
    if clinical_deferred_retrieval:
        issues.append({"issue": "clinical_deferred_retrieval_enabled", "count": clinical_deferred_retrieval})

    with write_jsonl_stream(accounting_path) as handle:
        for unit_id in sorted(assigned):
            row = text_rows.get(unit_id, {})
            handle.write(
                json.dumps(
                    {
                        "schema_version": "theraskill_phase6_member_accounting_v1",
                        "unit_id": unit_id,
                        "release_unit_id": assigned[unit_id],
                        "release_unit_type": assigned_type[unit_id],
                        "dedup_family": canonical_family(row.get("dedup_family")),
                        "dataset": row.get("dataset"),
                        "knowledge_type": row.get("knowledge_type"),
                        "raw_file": row.get("raw_file"),
                        "raw_line_no": row.get("raw_line_no"),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )

    status = "complete" if not issues else "incomplete"
    report = {
        "schema_version": "theraskill_phase6_reviewed_knowledge_release_report_v1",
        "created_at": utc_now(),
        "status": status,
        "phase6_policy": "full_coverage_accounting_release_v1",
        "inputs": {
            "text_view": {"path": args.text_view, "rows": len(text_rows), "sha256": file_sha256(args.text_view)},
            "reviewed_registries": {family: {"path": path, "sha256": file_sha256(path)} for family, path in REVIEWED_REGISTRIES.items()},
            "excluded_registries": {family: {"path": path, "sha256": file_sha256(path)} for family, path in EXCLUDED_REGISTRIES.items()},
            "source_provenance_registry": {"path": SOURCE_PROVENANCE_REGISTRY, "sha256": file_sha256(SOURCE_PROVENANCE_REGISTRY)},
        },
        "outputs": {
            "release_units": {"path": str(units_path), "rows": output_rows, "sha256": file_sha256(units_path)},
            "member_accounting": {"path": str(accounting_path), "rows": len(assigned), "sha256": file_sha256(accounting_path)},
        },
        "counts": {
            "raw_text_view_units": len(text_rows),
            "accounted_unique_units": len(assigned),
            "release_unit_rows": output_rows,
            "duplicate_assignment_count": len(duplicate_assignments),
            "missing_assignment_count": len(missing_assignments),
            "extra_assignment_count": len(extra_assignments),
            "release_type_distribution": dict(release_type_counter.most_common()),
            "assignment_type_distribution": dict(assignment_type_counter.most_common()),
            "release_family_row_distribution": dict(release_family_counter.most_common()),
            "release_family_member_distribution": dict(release_member_counter.most_common()),
            "raw_text_view_family_distribution": dict(family_counter.most_common()),
            "raw_text_view_dataset_distribution": dict(dataset_counter.most_common()),
            "retrieval_eligible_row_distribution": dict(retrieval_counter.most_common()),
            "embedding_eligible_row_distribution": dict(embedding_counter.most_common()),
        },
        "policy_checks": {
            "raw_units_unchanged": True,
            "old_outputs_knowledge_merge_used": False,
            "full_coverage_accounting": len(assigned) == len(text_rows) and not duplicate_assignments,
            "source_inventory_embedding_enabled_rows": source_inventory_embedding,
            "clinical_deferred_retrieval_enabled_rows": clinical_deferred_retrieval,
        },
        "issues_count": len(issues),
        "issues": issues,
    }
    write_json(args.report, report)
    write_json(args.manifest, report)
    write_json(args.qa_report, report)
    print(json.dumps({"status": status, "issues_count": len(issues), "counts": report["counts"], "outputs": report["outputs"]}, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
