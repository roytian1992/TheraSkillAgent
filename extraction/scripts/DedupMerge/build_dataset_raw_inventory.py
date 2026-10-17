#!/usr/bin/env python3
"""Build a dataset-level inventory for raw TheraSkill knowledge units.

This is a deterministic bookkeeping step. It does not deduplicate, merge,
drop, sample, or rewrite any raw unit.
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

from io_utils import file_sha256, write_json, write_text  # noqa: E402


DEFAULT_INPUT = "outputs/knowledge/dedup/indexes/unit_text_views/all_units.text_view.jsonl"
DEFAULT_JSON = "outputs/knowledge/dedup/reports/dataset_raw_knowledge_inventory.json"
DEFAULT_MD = "outputs/knowledge/dedup/reports/dataset_raw_knowledge_inventory.md"
DEFAULT_MANIFEST = "outputs/knowledge/dedup/manifests/dataset_raw_inventory_manifest.json"
DEFAULT_QA = "outputs/knowledge/dedup/manifests/dataset_raw_inventory_qa.json"

FAMILY_ALIASES = {
    "risk_assessment": "clinical_risk_assessment",
}

COMPARABLE_FAMILIES = {
    "style_response",
    "qa_psychoeducation",
    "risk_assessment",
    "therapy_process",
    "method_rubric",
    "synthetic_construction",
}

PROVENANCE_ONLY_FAMILIES = {"source_inventory", "source_route_audit"}

FAMILY_STRATEGY = {
    "style_response": "full exact duplicate clustering plus full bucketed near-duplicate candidate graph; no sampling-as-final",
    "qa_psychoeducation": "full exact duplicate clustering plus full semantic candidate graph; preserve license constraints and source references",
    "risk_assessment": "clinical risk assessment knowledge; merge only after clinical label/severity consistency review, not because of data-quality prohibition",
    "therapy_process": "full exact duplicate clustering plus structure-aware candidate graph; filter generic/no-sequence templates before any final merge",
    "method_rubric": "keep as method/rubric registry; exact duplicates can be clustered, semantic merge requires manual/LLM evidence review",
    "synthetic_construction": "keep as construction-method knowledge; exact duplicates can be clustered, near-duplicates should preserve dataset/method provenance",
    "source_inventory": "provenance inventory only; do not content-merge",
    "source_route_audit": "governance/routing record only; do not content-merge",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--json-output", default=DEFAULT_JSON)
    parser.add_argument("--md-output", default=DEFAULT_MD)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--qa-report", default=DEFAULT_QA)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def counter_to_dict(counter: Counter, limit: int | None = None) -> dict[str, int]:
    items = counter.most_common(limit)
    return {str(key): int(value) for key, value in items}


def add_counter(counter: Counter, value: Any) -> None:
    if isinstance(value, list):
        if not value:
            counter["__empty_list__"] += 1
        for item in value:
            counter[str(item)] += 1
    elif value is None or value == "":
        counter["__missing__"] += 1
    else:
        counter[str(value)] += 1


def family_display_name(family: str) -> str:
    alias = FAMILY_ALIASES.get(family)
    if alias:
        return f"{family} (alias: {alias})"
    return family


def dataset_strategy(family_counts: Counter) -> dict[str, Any]:
    content_families = sorted(f for f in family_counts if f in COMPARABLE_FAMILIES)
    provenance_families = sorted(f for f in family_counts if f in PROVENANCE_ONLY_FAMILIES)
    if not content_families:
        route = "provenance_only"
        final_merge_policy = "do not content-merge; use only for source accounting and later routing decisions"
    else:
        route = "raw_knowledge_full_coverage_dedup"
        final_merge_policy = "final merge requires full candidate coverage per family; sampled LLM output cannot be promoted"
    return {
        "route": route,
        "content_families": content_families,
        "provenance_only_families": provenance_families,
        "should_generate_exact_duplicate_clusters": bool(content_families),
        "should_generate_near_duplicate_candidates": any(
            f in {"style_response", "qa_psychoeducation", "risk_assessment", "therapy_process"}
            for f in content_families
        ),
        "requires_cluster_level_adjudication": any(
            f in {"style_response", "qa_psychoeducation", "risk_assessment", "therapy_process", "synthetic_construction", "method_rubric"}
            for f in content_families
        ),
        "final_merge_policy": final_merge_policy,
        "family_strategies": {family_display_name(f): FAMILY_STRATEGY.get(f, "needs explicit strategy") for f in content_families + provenance_families},
    }


def build_inventory(input_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    overall = {
        "input_rows": 0,
        "parse_errors": 0,
        "dataset_distribution": Counter(),
        "knowledge_type_distribution": Counter(),
        "dedup_family_distribution": Counter(),
        "display_family_distribution": Counter(),
        "raw_file_distribution": Counter(),
        "language_distribution": Counter(),
    }
    datasets: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, Any]] = []

    def dataset_bucket(dataset: str) -> dict[str, Any]:
        if dataset not in datasets:
            datasets[dataset] = {
                "dataset": dataset,
                "raw_unit_count": 0,
                "comparable_unit_count": 0,
                "provenance_only_unit_count": 0,
                "knowledge_type_distribution": Counter(),
                "dedup_family_distribution": Counter(),
                "display_family_distribution": Counter(),
                "raw_file_distribution": Counter(),
                "language_distribution": Counter(),
                "evidence_tier_distribution": Counter(),
                "source_construction_type_distribution": Counter(),
                "license_constraints_distribution": Counter(),
                "clinical_or_safety_flag_distribution": Counter(),
                "missing_unit_id": 0,
                "missing_dedup_text_sha256": 0,
            }
        return datasets[dataset]

    with input_path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            overall["input_rows"] += 1
            try:
                row = json.loads(line)
            except Exception as exc:
                overall["parse_errors"] += 1
                issues.append({"issue": "parse_error", "line": line_no, "error": str(exc)})
                continue

            dataset = str(row.get("dataset") or "__missing_dataset__")
            family = str(row.get("dedup_family") or "__missing_family__")
            display_family = family_display_name(family)
            bucket = dataset_bucket(dataset)

            bucket["raw_unit_count"] += 1
            if family in COMPARABLE_FAMILIES:
                bucket["comparable_unit_count"] += 1
            if family in PROVENANCE_ONLY_FAMILIES:
                bucket["provenance_only_unit_count"] += 1
            if not row.get("unit_id"):
                bucket["missing_unit_id"] += 1
                issues.append({"issue": "missing_unit_id", "line": line_no, "dataset": dataset})
            if family in COMPARABLE_FAMILIES and not row.get("dedup_text_sha256"):
                bucket["missing_dedup_text_sha256"] += 1
                issues.append({"issue": "missing_dedup_text_sha256", "line": line_no, "dataset": dataset, "unit_id": row.get("unit_id")})

            for target in (overall, bucket):
                add_counter(target["knowledge_type_distribution"], row.get("knowledge_type"))
                add_counter(target["dedup_family_distribution"], family)
                add_counter(target["display_family_distribution"], display_family)
                add_counter(target["raw_file_distribution"], row.get("raw_file"))
                add_counter(target["language_distribution"], row.get("language"))
            overall["dataset_distribution"][dataset] += 1
            add_counter(bucket["evidence_tier_distribution"], row.get("evidence_tier"))
            add_counter(bucket["source_construction_type_distribution"], row.get("source_construction_type"))
            add_counter(bucket["license_constraints_distribution"], row.get("license_constraints"))
            add_counter(bucket["clinical_or_safety_flag_distribution"], row.get("risk_or_safety_flags"))

    dataset_records: list[dict[str, Any]] = []
    for dataset, bucket in sorted(datasets.items(), key=lambda item: (-item[1]["raw_unit_count"], item[0])):
        family_counts = bucket["dedup_family_distribution"]
        record = {
            "dataset": dataset,
            "raw_unit_count": bucket["raw_unit_count"],
            "comparable_unit_count": bucket["comparable_unit_count"],
            "provenance_only_unit_count": bucket["provenance_only_unit_count"],
            "knowledge_type_distribution": counter_to_dict(bucket["knowledge_type_distribution"]),
            "dedup_family_distribution": counter_to_dict(bucket["dedup_family_distribution"]),
            "display_family_distribution": counter_to_dict(bucket["display_family_distribution"]),
            "raw_file_distribution": counter_to_dict(bucket["raw_file_distribution"]),
            "language_distribution": counter_to_dict(bucket["language_distribution"]),
            "evidence_tier_distribution": counter_to_dict(bucket["evidence_tier_distribution"]),
            "source_construction_type_distribution": counter_to_dict(bucket["source_construction_type_distribution"]),
            "license_constraints_distribution": counter_to_dict(bucket["license_constraints_distribution"]),
            "clinical_or_safety_flag_distribution": counter_to_dict(bucket["clinical_or_safety_flag_distribution"]),
            "missing_unit_id": bucket["missing_unit_id"],
            "missing_dedup_text_sha256": bucket["missing_dedup_text_sha256"],
            "dedup_and_merge_strategy": dataset_strategy(family_counts),
        }
        dataset_records.append(record)

    overall_payload = {
        "input_rows": overall["input_rows"],
        "parse_errors": overall["parse_errors"],
        "dataset_count": len(dataset_records),
        "dataset_distribution": counter_to_dict(overall["dataset_distribution"]),
        "knowledge_type_distribution": counter_to_dict(overall["knowledge_type_distribution"]),
        "dedup_family_distribution": counter_to_dict(overall["dedup_family_distribution"]),
        "display_family_distribution": counter_to_dict(overall["display_family_distribution"]),
        "raw_file_distribution_top100": counter_to_dict(overall["raw_file_distribution"], limit=100),
        "language_distribution": counter_to_dict(overall["language_distribution"]),
    }

    inventory = {
        "schema_version": "theraskill_dataset_raw_inventory_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete" if not issues else "incomplete",
        "input": {"path": str(input_path), "sha256": file_sha256(input_path)},
        "terminology": {
            "risk_assessment": "legacy dedup_family name for clinical risk assessment / symptom severity knowledge; it is not a data-quality issue and not a prohibition signal",
            "quality_consistency_issue": "use this wording for data quality problems, schema inconsistencies, or label conflicts",
        },
        "overall": overall_payload,
        "datasets": dataset_records,
        "issues_count": len(issues),
        "issues": issues[:500],
        "raw_units_unchanged": True,
        "sampled_outputs_are_not_final": True,
    }
    return inventory, issues


def render_markdown(inventory: dict[str, Any]) -> str:
    lines = [
        "# Dataset Raw Knowledge Inventory",
        "",
        f"Created at: `{inventory['created_at']}`",
        "",
        "This report is deterministic bookkeeping only. It does not deduplicate, merge, drop, sample, or rewrite raw knowledge units.",
        "",
        "Terminology: `risk_assessment` is a legacy family name for clinical risk assessment / symptom severity knowledge. Data-quality problems are called `quality_consistency_issue`.",
        "",
        "## Overall",
        "",
        f"- Input rows: `{inventory['overall']['input_rows']}`",
        f"- Dataset count: `{inventory['overall']['dataset_count']}`",
        f"- Issues count: `{inventory['issues_count']}`",
        "",
        "## Dataset Counts And Strategy",
        "",
        "| Dataset | Raw units | Comparable | Provenance-only | Main families | Strategy route |",
        "|---|---:|---:|---:|---|---|",
    ]
    for record in inventory["datasets"]:
        families = "; ".join(f"{k}: {v}" for k, v in record["display_family_distribution"].items())
        lines.append(
            f"| `{record['dataset']}` | {record['raw_unit_count']} | {record['comparable_unit_count']} | "
            f"{record['provenance_only_unit_count']} | {families} | `{record['dedup_and_merge_strategy']['route']}` |"
        )
    lines.extend([
        "",
        "## Family Strategy",
        "",
    ])
    for family, strategy in FAMILY_STRATEGY.items():
        lines.append(f"- `{family_display_name(family)}`: {strategy}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    paths = [Path(args.json_output), Path(args.md_output), Path(args.manifest), Path(args.qa_report)]
    existing = [str(path) for path in paths if path.exists()]
    if existing and not args.overwrite:
        raise FileExistsError(f"output exists; pass --overwrite: {existing}")

    input_path = Path(args.input)
    inventory, issues = build_inventory(input_path)
    write_json(args.json_output, inventory, sort_keys=False)
    write_text(args.md_output, render_markdown(inventory))

    manifest = {
        "schema_version": "theraskill_dataset_raw_inventory_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": inventory["status"],
        "input": inventory["input"],
        "outputs": {
            "json": {"path": args.json_output, "sha256": file_sha256(args.json_output)},
            "markdown": {"path": args.md_output, "sha256": file_sha256(args.md_output)},
        },
        "counts": inventory["overall"],
        "issues_count": len(issues),
        "raw_units_unchanged": True,
    }
    qa = {
        "schema_version": "theraskill_dataset_raw_inventory_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": inventory["status"],
        "input_rows": inventory["overall"]["input_rows"],
        "dataset_count": inventory["overall"]["dataset_count"],
        "parse_errors": inventory["overall"]["parse_errors"],
        "missing_unit_id_total": sum(record["missing_unit_id"] for record in inventory["datasets"]),
        "missing_dedup_text_sha256_total": sum(record["missing_dedup_text_sha256"] for record in inventory["datasets"]),
        "issues_count": len(issues),
        "issues": issues[:500],
        "raw_units_unchanged": True,
    }
    write_json(args.manifest, manifest, sort_keys=False)
    write_json(args.qa_report, qa, sort_keys=False)
    print(json.dumps({
        "status": inventory["status"],
        "input_rows": inventory["overall"]["input_rows"],
        "dataset_count": inventory["overall"]["dataset_count"],
        "issues_count": len(issues),
        "json_output": args.json_output,
        "md_output": args.md_output,
    }, ensure_ascii=False, indent=2))
    return 0 if inventory["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
