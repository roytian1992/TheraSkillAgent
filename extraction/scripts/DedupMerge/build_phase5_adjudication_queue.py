#!/usr/bin/env python3
"""Build Phase 5 adjudication queues for clinical risk and therapy process.

This is deterministic queue preparation only. It does not call an LLM and does
not create merge candidates or final merged knowledge units.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "_common"))

from io_utils import file_sha256, iter_jsonl, write_json  # noqa: E402


FAMILY_CONFIG = {
    "clinical_risk_assessment": {
        "component_family": "risk_assessment",
        "components": "outputs/knowledge/dedup/graphs/full_candidate_components/risk_assessment.candidate_components.jsonl",
        "refinement": "outputs/knowledge/dedup/graphs/oversized_component_refinement/risk_assessment.oversized_refinement.jsonl",
        "output_dir": "outputs/knowledge/dedup/adjudication_queues/clinical_risk_assessment",
        "max_adjudication_size": 100,
        "queue_prefix": "riskq",
        "queue_schema": "theraskill_clinical_risk_assessment_adjudication_queue_v1",
        "deferred_schema": "theraskill_clinical_risk_assessment_deferred_component_v1",
    },
    "therapy_process": {
        "component_family": "therapy_process",
        "components": "outputs/knowledge/dedup/graphs/full_candidate_components/therapy_process.candidate_components.jsonl",
        "refinement": "outputs/knowledge/dedup/graphs/oversized_component_refinement/therapy_process.oversized_refinement.jsonl",
        "output_dir": "outputs/knowledge/dedup/adjudication_queues/therapy_process",
        "max_adjudication_size": 200,
        "queue_prefix": "procq",
        "queue_schema": "theraskill_therapy_process_adjudication_queue_v1",
        "deferred_schema": "theraskill_therapy_process_deferred_component_v1",
    },
}

DEFAULT_TEXT_VIEW = "outputs/knowledge/dedup/indexes/unit_text_views/all_units.text_view.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", choices=sorted(FAMILY_CONFIG), required=True)
    parser.add_argument("--components")
    parser.add_argument("--refinement")
    parser.add_argument("--text-view", default=DEFAULT_TEXT_VIEW)
    parser.add_argument("--output-dir")
    parser.add_argument("--report")
    parser.add_argument("--manifest")
    parser.add_argument("--qa-report")
    parser.add_argument("--max-adjudication-size", type=int)
    parser.add_argument("--evidence-per-item", type=int, default=12)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(payload: Any, length: int = 20) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:length]


def preview(value: Any, limit: int) -> str:
    text = str(value or "").replace("\u0000", " ").strip()
    return " ".join(text.split())[:limit]


def counter_dict(counter: Counter) -> dict[str, int]:
    return {str(key): int(value) for key, value in counter.most_common()}


def load_components(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    stats = {"rows": 0, "member_refs": 0}
    for _line_no, row in iter_jsonl(path, with_line_numbers=True):
        rows.append(row)
        stats["rows"] += 1
        stats["member_refs"] += len(row.get("member_unit_ids") or [])
    return rows, stats


def load_refinement(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    by_parent: dict[str, dict[str, Any]] = {}
    stats = {
        "rows": 0,
        "parent_members": 0,
        "subcomponents": 0,
        "subcomponent_members": 0,
        "residual_oversized_subcomponents": 0,
    }
    if not path.exists():
        return by_parent, stats
    for _line_no, row in iter_jsonl(path, with_line_numbers=True):
        parent_id = str(row.get("parent_component_id"))
        by_parent[parent_id] = row
        stats["rows"] += 1
        stats["parent_members"] += int(row.get("parent_member_count") or 0)
        for sub in row.get("subcomponents") or []:
            stats["subcomponents"] += 1
            stats["subcomponent_members"] += len(sub.get("member_unit_ids") or [])
            if sub.get("still_oversized_after_refinement"):
                stats["residual_oversized_subcomponents"] += 1
    return by_parent, stats


def load_text_rows(path: Path, needed_ids: set[str]) -> tuple[dict[str, dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    rows: dict[str, dict[str, Any]] = {}
    stats = {"scanned_rows": 0, "loaded_rows": 0, "parse_errors": 0}
    issues: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            stats["scanned_rows"] += 1
            try:
                row = json.loads(line)
            except Exception as exc:  # noqa: BLE001
                stats["parse_errors"] += 1
                issues.append({"issue": "text_view_parse_error", "line": line_no, "error": str(exc)})
                continue
            unit_id = row.get("unit_id")
            if unit_id in needed_ids:
                rows[str(unit_id)] = row
                stats["loaded_rows"] += 1
    missing = sorted(needed_ids - set(rows))
    if missing:
        issues.append({"issue": "missing_text_view_rows", "count": len(missing), "examples": missing[:10]})
    return rows, stats, issues


def component_metadata(component: dict[str, Any]) -> dict[str, Any]:
    return {
        "component_id": component.get("component_id"),
        "dedup_family": component.get("dedup_family"),
        "dataset_distribution": component.get("dataset_distribution") or {},
        "knowledge_type_distribution": component.get("knowledge_type_distribution") or {},
        "evidence_tier_distribution": component.get("evidence_tier_distribution") or {},
        "source_construction_type_distribution": component.get("source_construction_type_distribution") or {},
        "min_edge_cosine_similarity": component.get("min_edge_cosine_similarity"),
        "max_edge_cosine_similarity": component.get("max_edge_cosine_similarity"),
        "edge_count": component.get("edge_count"),
        "cross_dataset_component": component.get("cross_dataset_component"),
    }


def item_text_quality(text_row: dict[str, Any]) -> dict[str, Any]:
    fields = text_row.get("dedup_key_fields") or {}
    text = preview(fields.get("text") or fields.get("process_text") or fields.get("question") or fields.get("context"), 4000)
    label = preview(fields.get("label") or fields.get("response_function") or fields.get("method_text"), 1000)
    dedup_text = preview(text_row.get("dedup_text"), 5000)
    return {
        "text": text,
        "label": label,
        "dedup_text": dedup_text,
        "semantic_chars": len(text) + len(label) + len(dedup_text),
    }


def evidence_record(unit_id: str, text_row: dict[str, Any], family: str) -> dict[str, Any]:
    quality = item_text_quality(text_row)
    fields = text_row.get("dedup_key_fields") or {}
    record = {
        "unit_id": unit_id,
        "dataset": text_row.get("dataset"),
        "knowledge_type": text_row.get("knowledge_type"),
        "evidence_tier": text_row.get("evidence_tier"),
        "source_construction_type": text_row.get("source_construction_type"),
        "dedup_text_preview": quality["dedup_text"][:1400],
        "dedup_text_sha256": text_row.get("dedup_text_sha256"),
        "risk_or_safety_flags": text_row.get("risk_or_safety_flags") or [],
        "license_constraints": text_row.get("license_constraints") or [],
    }
    if family == "clinical_risk_assessment":
        record.update({
            "clinical_text_preview": quality["text"][:1000],
            "label_preview": quality["label"][:400],
        })
    else:
        record.update({
            "process_text_preview": (fields.get("process_text") or quality["dedup_text"])[:1400],
        })
    return record


def select_evidence(member_ids: list[str], text_rows: dict[str, dict[str, Any]], family: str, limit: int) -> list[dict[str, Any]]:
    scored = []
    for unit_id in member_ids:
        row = text_rows.get(unit_id) or {}
        quality = item_text_quality(row)
        signature = (
            str(row.get("dataset") or ""),
            str(row.get("knowledge_type") or ""),
            str((row.get("dedup_key_fields") or {}).get("label") or ""),
            "+".join(row.get("risk_or_safety_flags") or []),
        )
        score = (min(quality["semantic_chars"], 3000), unit_id)
        scored.append((signature, score, unit_id, row))
    selected: list[tuple[str, dict[str, Any]]] = []
    used_signatures = set()
    for signature, score, unit_id, row in sorted(scored, key=lambda item: (-item[1][0], item[1][1])):
        if signature in used_signatures:
            continue
        selected.append((unit_id, row))
        used_signatures.add(signature)
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        selected_ids = {unit_id for unit_id, _row in selected}
        for _signature, score, unit_id, row in sorted(scored, key=lambda item: (-item[1][0], item[1][1])):
            if unit_id in selected_ids:
                continue
            selected.append((unit_id, row))
            selected_ids.add(unit_id)
            if len(selected) >= limit:
                break
    return [evidence_record(unit_id, row, family) for unit_id, row in selected]


def text_quality_summary(member_ids: list[str], text_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    dataset_counter = Counter()
    knowledge_counter = Counter()
    flags = Counter()
    labels = Counter()
    semantic_text_members = 0
    for unit_id in member_ids:
        row = text_rows.get(unit_id) or {}
        dataset_counter[str(row.get("dataset") or "missing")] += 1
        knowledge_counter[str(row.get("knowledge_type") or "missing")] += 1
        quality = item_text_quality(row)
        if quality["semantic_chars"] >= 8:
            semantic_text_members += 1
        for flag in row.get("risk_or_safety_flags") or ["none"]:
            flags[str(flag)] += 1
        label = (row.get("dedup_key_fields") or {}).get("label")
        if label:
            labels[str(label)] += 1
    return {
        "semantic_text_members": semantic_text_members,
        "dataset_distribution": counter_dict(dataset_counter),
        "knowledge_type_distribution": counter_dict(knowledge_counter),
        "clinical_content_flag_distribution": counter_dict(flags),
        "label_distribution": counter_dict(labels),
    }


def expansion_items(component: dict[str, Any], refinement_by_parent: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    component_id = str(component.get("component_id"))
    refinement = refinement_by_parent.get(component_id)
    if not refinement:
        return [{
            "source_kind": "full_candidate_component",
            "source_component_id": component_id,
            "item_id": component_id,
            "member_unit_ids": [str(unit_id) for unit_id in component.get("member_unit_ids") or []],
            "refinement_key": None,
            "still_oversized_after_refinement": False,
        }]
    items = []
    for sub in refinement.get("subcomponents") or []:
        items.append({
            "source_kind": "oversized_refined_subcomponent",
            "source_component_id": component_id,
            "item_id": sub.get("subcomponent_id"),
            "member_unit_ids": [str(unit_id) for unit_id in sub.get("member_unit_ids") or []],
            "refinement_key": sub.get("refinement_key") or {},
            "refinement_key_sha256": sub.get("refinement_key_sha256"),
            "still_oversized_after_refinement": bool(sub.get("still_oversized_after_refinement")),
        })
    return items


def defer_reason(family: str, item: dict[str, Any], quality: dict[str, Any], max_size: int) -> str | None:
    member_count = len(item.get("member_unit_ids") or [])
    if member_count <= 1:
        return "singleton_no_adjudication_needed"
    if item.get("still_oversized_after_refinement") or member_count > max_size:
        if family == "clinical_risk_assessment":
            return "clinical_label_consistency_review_required"
        return "generic_no_sequence_or_structure_refinement_required"
    if quality["semantic_text_members"] == 0:
        return "insufficient_text_for_adjudication"
    return None


def adjudication_instruction(family: str) -> dict[str, Any]:
    if family == "clinical_risk_assessment":
        return {
            "task": "Decide whether these clinical risk, symptom severity, or assessment units are duplicate/near-duplicate knowledge records, or whether label/severity/source evidence differences require splitting.",
            "must_check": [
                "clinical label consistency",
                "risk domain or severity consistency",
                "dataset/source-construction compatibility",
                "whether generated explanations are evidence or only rationale hints",
            ],
            "not_allowed": "Do not create final merged knowledge or change clinical labels here.",
        }
    return {
        "task": "Decide whether these therapy-process units describe the same reusable process pattern, or whether state/move/response sequence differences require splitting.",
        "must_check": [
            "client state signature",
            "therapist move and move function",
            "client response/transition",
            "generic or no-sequence templates",
            "source evidence compatibility",
        ],
        "not_allowed": "Do not create final merged knowledge or rewrite therapeutic process semantics here.",
    }


def main() -> int:
    args = parse_args()
    config = FAMILY_CONFIG[args.family]
    components_path = Path(args.components or config["components"])
    refinement_path = Path(args.refinement or config["refinement"])
    text_view_path = Path(args.text_view)
    output_dir = Path(args.output_dir or config["output_dir"])
    report_path = Path(args.report or f"outputs/knowledge/dedup/reports/{args.family}_adjudication_queue_report.json")
    manifest_path = Path(args.manifest or f"outputs/knowledge/dedup/manifests/{args.family}_adjudication_queue_manifest.json")
    qa_path = Path(args.qa_report or f"outputs/knowledge/dedup/manifests/{args.family}_adjudication_queue_qa.json")
    max_size = int(args.max_adjudication_size or config["max_adjudication_size"])

    queue_path = output_dir / f"{args.family}.adjudication_queue.jsonl"
    deferred_path = output_dir / f"{args.family}.deferred_components.jsonl"
    singleton_path = output_dir / f"{args.family}.singleton_components.jsonl"
    all_outputs = [queue_path, deferred_path, singleton_path, report_path, manifest_path, qa_path]
    if any(path.exists() for path in all_outputs) and not args.overwrite:
        existing = [str(path) for path in all_outputs if path.exists()]
        raise FileExistsError(f"outputs exist; pass --overwrite: {existing}")
    output_dir.mkdir(parents=True, exist_ok=True)

    components, component_stats = load_components(components_path)
    refinement_by_parent, refinement_stats = load_refinement(refinement_path)
    all_items = []
    for component in components:
        for item in expansion_items(component, refinement_by_parent):
            item["component_metadata"] = component_metadata(component)
            all_items.append(item)
    needed_ids = {unit_id for item in all_items for unit_id in item["member_unit_ids"]}
    text_rows, text_stats, issues = load_text_rows(text_view_path, needed_ids)

    member_accounting: Counter[str] = Counter()
    counters = Counter()
    queue_by_dataset = Counter()
    queue_by_knowledge_type = Counter()
    deferred_by_reason = Counter()
    singleton_by_knowledge_type = Counter()

    with queue_path.open("w", encoding="utf-8") as queue_out, \
        deferred_path.open("w", encoding="utf-8") as deferred_out, \
        singleton_path.open("w", encoding="utf-8") as singleton_out:
        for item in all_items:
            member_ids = item["member_unit_ids"]
            quality = text_quality_summary(member_ids, text_rows)
            reason = defer_reason(args.family, item, quality, max_size)
            base = {
                "source_kind": item["source_kind"],
                "source_component_id": item["source_component_id"],
                "source_item_id": item["item_id"],
                "member_count": len(member_ids),
                "member_unit_ids": member_ids,
                "component_metadata": item["component_metadata"],
                "refinement_key": item.get("refinement_key"),
                "refinement_key_sha256": item.get("refinement_key_sha256"),
                "text_quality_summary": quality,
                "raw_units_unchanged": True,
                "not_final_merged_unit": True,
                "final_merge_output": False,
                "formal_merge_candidate": False,
            }
            for unit_id in member_ids:
                member_accounting[unit_id] += 1

            if reason == "singleton_no_adjudication_needed":
                record = {
                    "schema_version": f"theraskill_{args.family}_singleton_component_v1",
                    "status": "singleton_no_adjudication_needed",
                    **base,
                }
                singleton_out.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
                counters["singleton_rows"] += 1
                counters["singleton_members"] += len(member_ids)
                singleton_by_knowledge_type.update(quality["knowledge_type_distribution"])
                continue
            if reason:
                record = {
                    "schema_version": config["deferred_schema"],
                    "status": "deferred_not_final_merge",
                    "defer_reason": reason,
                    **base,
                }
                deferred_out.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
                counters["deferred_rows"] += 1
                counters["deferred_members"] += len(member_ids)
                deferred_by_reason[reason] += 1
                continue

            queue_id = config["queue_prefix"] + "_" + stable_hash({
                "family": args.family,
                "source_item_id": item["item_id"],
                "member_unit_ids": member_ids,
            })
            record = {
                "schema_version": config["queue_schema"],
                "queue_item_id": queue_id,
                "status": "queued_for_cluster_level_llm_adjudication",
                "dedup_family": args.family,
                "adjudication_instruction": adjudication_instruction(args.family),
                **base,
                "evidence_examples": select_evidence(member_ids, text_rows, args.family, args.evidence_per_item),
            }
            queue_out.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            counters["queue_rows"] += 1
            counters["queue_members"] += len(member_ids)
            queue_by_dataset.update(quality["dataset_distribution"])
            queue_by_knowledge_type.update(quality["knowledge_type_distribution"])

    duplicate_assignments = sum(1 for count in member_accounting.values() if count != 1)
    missing_accounting = len(needed_ids - set(member_accounting))
    if duplicate_assignments:
        issues.append({"issue": "duplicate_member_accounting", "count": duplicate_assignments})
    if missing_accounting:
        issues.append({"issue": "missing_member_accounting", "count": missing_accounting})

    status = "complete" if not issues else "complete_with_issues"
    report = {
        "schema_version": "theraskill_phase5_adjudication_queue_report_v1",
        "created_at": utc_now(),
        "status": status,
        "dedup_family": args.family,
        "inputs": {
            "components": {"path": str(components_path), "sha256": file_sha256(components_path), **component_stats},
            "refinement": {"path": str(refinement_path), "sha256": file_sha256(refinement_path), **refinement_stats},
            "text_view": {"path": str(text_view_path), "sha256": file_sha256(text_view_path), **text_stats},
        },
        "outputs": {
            "queue": {"path": str(queue_path), "sha256": file_sha256(queue_path)},
            "deferred": {"path": str(deferred_path), "sha256": file_sha256(deferred_path)},
            "singletons": {"path": str(singleton_path), "sha256": file_sha256(singleton_path)},
        },
        "parameters": {
            "max_adjudication_size": max_size,
            "evidence_per_item": args.evidence_per_item,
            "residual_oversized_policy": "defer_with_explicit_reason_before_llm",
        },
        "counts": {
            "input_components": len(components),
            "expanded_items": len(all_items),
            "input_member_refs": sum(len(item["member_unit_ids"]) for item in all_items),
            "unique_member_ids": len(needed_ids),
            **{key: int(value) for key, value in counters.items()},
            "member_accounted_once": sum(1 for count in member_accounting.values() if count == 1),
        },
        "distributions": {
            "queue_by_dataset": counter_dict(queue_by_dataset),
            "queue_by_knowledge_type": counter_dict(queue_by_knowledge_type),
            "deferred_by_reason": counter_dict(deferred_by_reason),
            "singleton_by_knowledge_type": counter_dict(singleton_by_knowledge_type),
        },
        "issues_count": len(issues),
        "issues": issues,
        "raw_units_unchanged": True,
        "final_merge_output": False,
        "formal_merge_candidates_output": False,
    }
    write_json(report_path, report)
    write_json(manifest_path, report)
    write_json(qa_path, report)
    print(json.dumps({
        "status": status,
        "family": args.family,
        "counts": report["counts"],
        "distributions": report["distributions"],
        "issues_count": len(issues),
        "report": str(report_path),
    }, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
