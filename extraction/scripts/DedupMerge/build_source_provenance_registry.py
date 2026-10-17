#!/usr/bin/env python3
"""Build a full source-inventory provenance registry under dedup/.

Source inventory rows are provenance-only pointers for synthetic or large
source corpora. They are not comparable therapeutic knowledge content and must
not be promoted into content merge candidates.
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

from io_utils import file_sha256, iter_jsonl, read_json, write_json  # noqa: E402


DEFAULT_TEXT_VIEW = "outputs/knowledge/dedup/indexes/unit_text_views/all_units.text_view.jsonl"
DEFAULT_TEXT_VIEW_QA = "outputs/knowledge/dedup/manifests/unit_text_view_qa.json"
DEFAULT_OUTPUT_DIR = "outputs/knowledge/dedup/registries/source_provenance_registry"
DEFAULT_REPORT = "outputs/knowledge/dedup/reports/source_provenance_registry_report.json"
DEFAULT_MANIFEST = "outputs/knowledge/dedup/manifests/source_provenance_registry_manifest.json"
DEFAULT_QA = "outputs/knowledge/dedup/manifests/source_provenance_registry_qa.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text-view", default=DEFAULT_TEXT_VIEW)
    parser.add_argument("--text-view-qa", default=DEFAULT_TEXT_VIEW_QA)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--qa-report", default=DEFAULT_QA)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(unit_id: str) -> str:
    return "sourceprov_" + hashlib.sha256(unit_id.encode("utf-8")).hexdigest()[:20]


def provenance_record(row: dict[str, Any]) -> dict[str, Any]:
    unit_id = str(row.get("unit_id") or "")
    key_fields = row.get("dedup_key_fields") or {}
    return {
        "schema_version": "theraskill_source_provenance_registry_v1",
        "registry_id": stable_id(unit_id),
        "registry_status": "provenance_only_registered_not_content_merge",
        "dedup_family": "source_inventory",
        "registry_kind": "source_sample_inventory_pointer",
        "unit_id": unit_id,
        "dataset": row.get("dataset"),
        "knowledge_type": row.get("knowledge_type"),
        "language": row.get("language"),
        "evidence_tier": row.get("evidence_tier"),
        "source_construction_type": row.get("source_construction_type"),
        "source_pointer": key_fields.get("raw_pointer"),
        "dedup_key_fields": key_fields,
        "license_constraints": row.get("license_constraints") or [],
        "risk_or_safety_flags": row.get("risk_or_safety_flags") or [],
        "dedup_text_sha256": row.get("dedup_text_sha256"),
        "dedup_text_preview": str(row.get("dedup_text") or "")[:1000],
        "raw_file": row.get("raw_file"),
        "raw_line_no": row.get("raw_line_no"),
        "default_retrieval_route": "provenance_lookup_only",
        "default_embedding_enabled": False,
        "content_comparable": False,
        "content_merge_candidate": False,
        "raw_units_unchanged": True,
        "final_merge_output": False,
        "formal_merge_candidate": False,
    }


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    registry_path = output_dir / "source_provenance.registry.jsonl"
    summary_path = output_dir / "source_provenance.registry_summary.json"
    all_paths = [registry_path, summary_path, Path(args.report), Path(args.manifest), Path(args.qa_report)]
    if any(path.exists() for path in all_paths) and not args.overwrite:
        existing = [str(path) for path in all_paths if path.exists()]
        raise FileExistsError(f"outputs exist; pass --overwrite: {existing}")

    text_view_qa = read_json(args.text_view_qa)
    family_distribution = text_view_qa.get("family_distribution") or {}
    expected_rows = int(family_distribution.get("source_inventory") or 0)

    output_dir.mkdir(parents=True, exist_ok=True)
    issues: list[dict[str, Any]] = []
    unit_ids: set[str] = set()
    registry_ids: set[str] = set()
    source_pointer_seen: Counter[str] = Counter()
    dataset_counter: Counter[str] = Counter()
    knowledge_type_counter: Counter[str] = Counter()
    raw_file_counter: Counter[str] = Counter()
    evidence_tier_counter: Counter[str] = Counter()
    source_type_counter: Counter[str] = Counter()
    language_counter: Counter[str] = Counter()
    license_counter: Counter[str] = Counter()
    status_counter: Counter[str] = Counter()
    rows_written = 0

    with registry_path.open("w", encoding="utf-8") as handle:
        for text_view_line_no, row in iter_jsonl(args.text_view, with_line_numbers=True):
            if row.get("dedup_family") != "source_inventory":
                continue
            record = provenance_record(row)
            unit_id = record["unit_id"]
            if not unit_id:
                issues.append({"issue": "missing_unit_id", "text_view_line_no": text_view_line_no})
                continue
            if unit_id in unit_ids:
                issues.append({"issue": "duplicate_unit_id", "unit_id": unit_id, "text_view_line_no": text_view_line_no})
            unit_ids.add(unit_id)
            registry_id = record["registry_id"]
            if registry_id in registry_ids:
                issues.append({"issue": "duplicate_registry_id", "registry_id": registry_id, "unit_id": unit_id})
            registry_ids.add(registry_id)
            if record.get("content_comparable") is not False or record.get("content_merge_candidate") is not False:
                issues.append({"issue": "source_inventory_marked_as_content_comparable", "unit_id": unit_id})
            if record.get("final_merge_output") is not False or record.get("formal_merge_candidate") is not False:
                issues.append({"issue": "registry_row_has_merge_output_flag", "unit_id": unit_id})

            dataset_counter[str(record.get("dataset") or "unknown")] += 1
            knowledge_type_counter[str(record.get("knowledge_type") or "unknown")] += 1
            raw_file_counter[str(record.get("raw_file") or "unknown")] += 1
            evidence_tier_counter[str(record.get("evidence_tier") or "unknown")] += 1
            source_type_counter[str(record.get("source_construction_type") or "unknown")] += 1
            language_counter[str(record.get("language") or "unknown")] += 1
            status_counter[str(record.get("registry_status") or "unknown")] += 1
            pointer = record.get("source_pointer")
            if pointer:
                source_pointer_seen[str(record.get("dataset") or "unknown") + "::" + str(pointer)] += 1
            else:
                issues.append({"issue": "missing_source_pointer", "unit_id": unit_id})
            licenses = record.get("license_constraints") or []
            if licenses:
                for item in licenses:
                    license_counter[str(item)] += 1
            else:
                license_counter["__empty_list__"] += 1

            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            rows_written += 1

    duplicate_source_pointers = [pointer for pointer, count in source_pointer_seen.items() if count > 1]
    if duplicate_source_pointers:
        issues.append(
            {
                "issue": "duplicate_dataset_scoped_source_pointer",
                "count": len(duplicate_source_pointers),
                "examples": duplicate_source_pointers[:20],
            }
        )
    if expected_rows and rows_written != expected_rows:
        issues.append(
            {
                "issue": "registry_rows_do_not_match_text_view_qa_source_inventory_count",
                "registry_rows": rows_written,
                "text_view_qa_source_inventory": expected_rows,
            }
        )
    if rows_written != len(unit_ids):
        issues.append({"issue": "registry_rows_do_not_match_unique_unit_ids", "registry_rows": rows_written, "unique_unit_ids": len(unit_ids)})

    status = "complete" if not issues else "incomplete"
    summary = {
        "schema_version": "theraskill_source_provenance_registry_summary_v1",
        "created_at": utc_now(),
        "status": status,
        "registry_rows": rows_written,
        "unique_unit_ids": len(unit_ids),
        "unique_registry_ids": len(registry_ids),
        "unique_dataset_scoped_source_pointers": len(source_pointer_seen),
        "dataset_distribution": dict(dataset_counter.most_common()),
        "knowledge_type_distribution": dict(knowledge_type_counter.most_common()),
        "raw_file_distribution": dict(raw_file_counter.most_common()),
        "evidence_tier_distribution": dict(evidence_tier_counter.most_common()),
        "source_construction_type_distribution": dict(source_type_counter.most_common()),
        "language_distribution": dict(language_counter.most_common()),
        "license_constraints_distribution": dict(license_counter.most_common()),
        "registry_status_distribution": dict(status_counter.most_common()),
        "source_inventory_policy": {
            "provenance_only": True,
            "content_comparable": False,
            "default_embedding_enabled": False,
            "content_merge_candidate": False,
        },
        "raw_units_unchanged": True,
        "final_merge_output": False,
        "formal_merge_candidates_output": False,
    }
    write_json(summary_path, summary)

    report = {
        "schema_version": "theraskill_source_provenance_registry_report_v1",
        "created_at": utc_now(),
        "status": status,
        "inputs": {
            "text_view": {"path": args.text_view, "sha256": file_sha256(args.text_view)},
            "text_view_qa": {"path": args.text_view_qa, "sha256": file_sha256(args.text_view_qa)},
        },
        "outputs": {
            "registry": {"path": str(registry_path), "rows": rows_written, "sha256": file_sha256(registry_path)},
            "summary": {"path": str(summary_path), "sha256": file_sha256(summary_path)},
        },
        "counts": {
            "registry_rows": rows_written,
            "unique_unit_ids": len(unit_ids),
            "unique_registry_ids": len(registry_ids),
            "unique_dataset_scoped_source_pointers": len(source_pointer_seen),
            "text_view_qa_source_inventory": expected_rows,
        },
        "dataset_distribution": dict(dataset_counter.most_common()),
        "knowledge_type_distribution": dict(knowledge_type_counter.most_common()),
        "raw_file_distribution": dict(raw_file_counter.most_common()),
        "evidence_tier_distribution": dict(evidence_tier_counter.most_common()),
        "source_construction_type_distribution": dict(source_type_counter.most_common()),
        "language_distribution": dict(language_counter.most_common()),
        "license_constraints_distribution": dict(license_counter.most_common()),
        "registry_status_distribution": dict(status_counter.most_common()),
        "issues_count": len(issues),
        "issues": issues,
        "raw_units_unchanged": True,
        "final_merge_output": False,
        "formal_merge_candidates_output": False,
    }
    write_json(args.report, report)
    write_json(args.manifest, report)
    write_json(args.qa_report, report)
    print(json.dumps({"status": status, "issues_count": len(issues), "counts": report["counts"], "outputs": report["outputs"]}, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
