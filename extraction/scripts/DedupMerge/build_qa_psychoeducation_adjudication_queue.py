#!/usr/bin/env python3
"""Build a full cluster-level adjudication queue for QA/psychoeducation components."""

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

from io_utils import file_sha256, write_json  # noqa: E402

DEFAULT_COMPONENTS = "outputs/knowledge/dedup/graphs/full_candidate_components/qa_psychoeducation.candidate_components.jsonl"
DEFAULT_TEXT_VIEW = "outputs/knowledge/dedup/indexes/unit_text_views/all_units.text_view.jsonl"
DEFAULT_OUTPUT_DIR = "outputs/knowledge/dedup/adjudication_queues/qa_psychoeducation"
DEFAULT_REPORT = "outputs/knowledge/dedup/reports/qa_psychoeducation_adjudication_queue_report.json"
DEFAULT_MANIFEST = "outputs/knowledge/dedup/manifests/qa_psychoeducation_adjudication_queue_manifest.json"
DEFAULT_QA = "outputs/knowledge/dedup/manifests/qa_psychoeducation_adjudication_queue_qa.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--components", default=DEFAULT_COMPONENTS)
    parser.add_argument("--text-view", default=DEFAULT_TEXT_VIEW)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--qa-report", default=DEFAULT_QA)
    parser.add_argument("--max-adjudication-size", type=int, default=220)
    parser.add_argument("--evidence-per-component", type=int, default=12)
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


def load_components(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    stats = {"rows": 0, "member_refs": 0, "parse_errors": 0}
    issues: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception as exc:  # noqa: BLE001
                stats["parse_errors"] += 1
                issues.append({"issue": "component_parse_error", "line": line_no, "error": str(exc)})
                continue
            rows.append(row)
            stats["rows"] += 1
            stats["member_refs"] += len(row.get("member_unit_ids") or [])
    return rows, stats, issues


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


def evidence_quality(row: dict[str, Any]) -> dict[str, Any]:
    fields = row.get("dedup_key_fields") or {}
    question = preview(fields.get("question") or fields.get("input") or fields.get("context"), 4000)
    answer = preview(fields.get("answer") or fields.get("response") or fields.get("output"), 5000)
    structure = preview(fields.get("structure") or fields.get("answer_structure") or fields.get("response_structure"), 3000)
    dedup_text = preview(row.get("dedup_text"), 6000)
    semantic_chars = len(question) + len(answer) + len(structure)
    return {
        "question": question,
        "answer": answer,
        "structure": structure,
        "dedup_text": dedup_text,
        "semantic_chars": semantic_chars,
        "has_question": bool(question),
        "has_answer": bool(answer),
        "has_structure": bool(structure),
        "has_semantic_text": semantic_chars >= 8,
    }


def evidence_record(unit_id: str, row: dict[str, Any], quality: dict[str, Any]) -> dict[str, Any]:
    return {
        "unit_id": unit_id,
        "dataset": row.get("dataset"),
        "knowledge_type": row.get("knowledge_type"),
        "evidence_tier": row.get("evidence_tier"),
        "source_construction_type": row.get("source_construction_type"),
        "question_preview": quality["question"][:700],
        "answer_preview": quality["answer"][:1100],
        "structure_preview": quality["structure"][:900],
        "dedup_text_preview": quality["dedup_text"][:1300],
        "dedup_text_sha256": row.get("dedup_text_sha256"),
        "license_constraints": row.get("license_constraints") or [],
        "clinical_content_flags": row.get("risk_or_safety_flags") or [],
    }


def select_evidence(member_ids: list[str], text_rows: dict[str, dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    candidates = []
    for unit_id in member_ids:
        row = text_rows.get(unit_id) or {}
        quality = evidence_quality(row)
        signature = (
            str(row.get("dataset") or ""),
            str(row.get("knowledge_type") or ""),
            "+".join(row.get("license_constraints") or []),
            "+".join(row.get("risk_or_safety_flags") or []),
        )
        score = (
            1 if quality["has_semantic_text"] else 0,
            1 if quality["has_answer"] else 0,
            min(quality["semantic_chars"], 2000),
            unit_id,
        )
        candidates.append((signature, score, unit_id, row, quality))

    selected: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    used_signatures: set[tuple[str, str, str, str]] = set()
    for signature, score, unit_id, row, quality in sorted(candidates, key=lambda item: (-item[1][0], -item[1][1], -item[1][2], item[1][3])):
        if signature in used_signatures:
            continue
        selected.append((unit_id, row, quality))
        used_signatures.add(signature)
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        already = {unit_id for unit_id, _row, _quality in selected}
        for _signature, score, unit_id, row, quality in sorted(candidates, key=lambda item: (-item[1][0], -item[1][1], -item[1][2], item[1][3])):
            if unit_id in already:
                continue
            selected.append((unit_id, row, quality))
            already.add(unit_id)
            if len(selected) >= limit:
                break
    return [evidence_record(unit_id, row, quality) for unit_id, row, quality in selected]


def component_quality(member_ids: list[str], text_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = [text_rows.get(unit_id) or {} for unit_id in member_ids]
    qualities = [evidence_quality(row) for row in rows]
    return {
        "semantic_text_members": sum(1 for item in qualities if item["has_semantic_text"]),
        "answer_members": sum(1 for item in qualities if item["has_answer"]),
        "question_members": sum(1 for item in qualities if item["has_question"]),
        "structure_members": sum(1 for item in qualities if item["has_structure"]),
        "dataset_distribution": Counter(str(row.get("dataset") or "missing") for row in rows),
        "knowledge_type_distribution": Counter(str(row.get("knowledge_type") or "missing") for row in rows),
        "license_constraint_distribution": Counter(
            constraint
            for row in rows
            for constraint in (row.get("license_constraints") or ["none"])
        ),
        "clinical_content_flag_distribution": Counter(
            flag
            for row in rows
            for flag in (row.get("risk_or_safety_flags") or ["none"])
        ),
    }


def counter_dict(counter: Counter) -> dict[str, int]:
    return {str(key): int(value) for key, value in counter.most_common()}


def defer_reason(component: dict[str, Any], quality: dict[str, Any], max_size: int) -> str | None:
    member_count = int(component.get("member_count") or 0)
    if member_count <= 1:
        return "singleton_no_adjudication_needed"
    if member_count > max_size:
        return "oversized_component_requires_refinement"
    if quality["semantic_text_members"] == 0:
        return "insufficient_text_for_adjudication"
    return None


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    queue_path = output_dir / "qa_psychoeducation.adjudication_queue.jsonl"
    deferred_path = output_dir / "qa_psychoeducation.deferred_components.jsonl"
    if output_dir.exists() and any(output_dir.glob("*.jsonl")) and not args.overwrite:
        raise FileExistsError(f"output dir has jsonl files: {output_dir}; pass --overwrite")
    if args.overwrite and output_dir.exists():
        for path in output_dir.glob("*.jsonl"):
            path.unlink()
    output_dir.mkdir(parents=True, exist_ok=True)

    components, component_stats, issues = load_components(Path(args.components))
    needed_ids = {str(unit_id) for row in components for unit_id in (row.get("member_unit_ids") or [])}
    text_rows, text_stats, text_issues = load_text_rows(Path(args.text_view), needed_ids)
    issues.extend(text_issues)

    counters = Counter()
    queue_by_knowledge_type = Counter()
    queue_by_dataset = Counter()
    deferred_by_reason = Counter()
    member_accounting: Counter[str] = Counter()

    with queue_path.open("w", encoding="utf-8") as queue_out, deferred_path.open("w", encoding="utf-8") as deferred_out:
        for component in components:
            member_ids = [str(unit_id) for unit_id in component.get("member_unit_ids") or []]
            member_count = len(member_ids)
            quality = component_quality(member_ids, text_rows)
            reason = defer_reason(component, quality, args.max_adjudication_size)
            base = {
                "component_id": component.get("component_id"),
                "member_count": member_count,
                "member_unit_ids": member_ids,
                "component_metadata": {
                    "dedup_family": component.get("dedup_family"),
                    "dataset_distribution": component.get("dataset_distribution"),
                    "knowledge_type_distribution": component.get("knowledge_type_distribution"),
                    "evidence_tier_distribution": component.get("evidence_tier_distribution"),
                    "source_construction_type_distribution": component.get("source_construction_type_distribution"),
                    "min_edge_cosine_similarity": component.get("min_edge_cosine_similarity"),
                    "max_edge_cosine_similarity": component.get("max_edge_cosine_similarity"),
                    "edge_count": component.get("edge_count"),
                    "cross_dataset_component": component.get("cross_dataset_component"),
                },
                "text_quality_summary": {
                    "semantic_text_members": quality["semantic_text_members"],
                    "answer_members": quality["answer_members"],
                    "question_members": quality["question_members"],
                    "structure_members": quality["structure_members"],
                    "license_constraint_distribution": counter_dict(quality["license_constraint_distribution"]),
                    "clinical_content_flag_distribution": counter_dict(quality["clinical_content_flag_distribution"]),
                },
                "raw_units_unchanged": True,
                "not_final_merged_unit": True,
            }
            if reason:
                record = {
                    "schema_version": "theraskill_qa_psychoeducation_deferred_component_v1",
                    **base,
                    "defer_reason": reason,
                }
                deferred_out.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
                counters["deferred_rows"] += 1
                counters["deferred_members"] += member_count
                deferred_by_reason[reason] += 1
                for unit_id in member_ids:
                    member_accounting[unit_id] += 1
                continue

            queue_id = "qa_queue_" + stable_hash({"component_id": component.get("component_id"), "member_ids": member_ids})
            evidence = select_evidence(member_ids, text_rows, args.evidence_per_component)
            record = {
                "schema_version": "theraskill_qa_psychoeducation_adjudication_queue_v1",
                "queue_item_id": queue_id,
                **base,
                "evidence_examples": evidence,
            }
            queue_out.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            counters["queue_rows"] += 1
            counters["queue_members"] += member_count
            queue_by_knowledge_type.update(component.get("knowledge_type_distribution") or {})
            queue_by_dataset.update(component.get("dataset_distribution") or {})
            for unit_id in member_ids:
                member_accounting[unit_id] += 1

    duplicate_member_assignments = sum(1 for count in member_accounting.values() if count != 1)
    issues_count = len(issues) + duplicate_member_assignments
    if duplicate_member_assignments:
        issues.append({"issue": "duplicate_or_missing_member_accounting", "count": duplicate_member_assignments})

    report = {
        "schema_version": "theraskill_qa_psychoeducation_adjudication_queue_report_v1",
        "created_at": utc_now(),
        "status": "complete" if issues_count == 0 else "complete_with_issues",
        "inputs": {
            "components": {"path": args.components, "sha256": file_sha256(args.components), **component_stats},
            "text_view": {"path": args.text_view, "sha256": file_sha256(args.text_view), **text_stats},
        },
        "outputs": {
            "queue": {"path": str(queue_path), "sha256": file_sha256(queue_path)},
            "deferred": {"path": str(deferred_path), "sha256": file_sha256(deferred_path)},
        },
        "parameters": {
            "max_adjudication_size": args.max_adjudication_size,
            "evidence_per_component": args.evidence_per_component,
        },
        "counts": {
            "input_components": len(components),
            "input_member_refs": component_stats["member_refs"],
            "unique_member_ids": len(needed_ids),
            **{key: int(value) for key, value in counters.items()},
            "member_accounted_once": sum(1 for count in member_accounting.values() if count == 1),
        },
        "distributions": {
            "queue_by_dataset": counter_dict(queue_by_dataset),
            "queue_by_knowledge_type": counter_dict(queue_by_knowledge_type),
            "deferred_by_reason": counter_dict(deferred_by_reason),
        },
        "issues_count": issues_count,
        "issues": issues,
        "raw_units_unchanged": True,
        "final_merge_output": False,
        "formal_merge_candidates_output": False,
    }
    write_json(args.report, report)
    write_json(args.manifest, report)
    write_json(args.qa_report, report)
    print(json.dumps({"event": "finished", "status": report["status"], "counts": report["counts"], "report": args.report}, ensure_ascii=False, indent=2))
    return 0 if issues_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
