#!/usr/bin/env python3
"""Build full candidate connected components from exact and near-duplicate edges.

This is a graph bookkeeping step for the corrected dedup/merge workflow. It
does not merge content, does not sample, and does not promote any component to a
final release unit.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "_common"))

from io_utils import file_sha256, write_json  # noqa: E402


DEFAULT_TEXT_VIEW = "outputs/knowledge/dedup/indexes/unit_text_views/all_units.text_view.jsonl"
DEFAULT_CANDIDATE_ROOT = "outputs/knowledge/dedup/indexes/embedding_candidates"
DEFAULT_EXACT_ROOT = "outputs/knowledge/dedup/clusters/exact_duplicates"
DEFAULT_OUTPUT_DIR = "outputs/knowledge/dedup/graphs/full_candidate_components"
DEFAULT_REPORT = "outputs/knowledge/dedup/reports/full_candidate_components_report.json"
DEFAULT_MANIFEST = "outputs/knowledge/dedup/manifests/full_candidate_components_manifest.json"
DEFAULT_QA = "outputs/knowledge/dedup/manifests/full_candidate_components_qa.json"

COMPARABLE_FAMILIES = {
    "style_response",
    "qa_psychoeducation",
    "risk_assessment",
    "therapy_process",
    "method_rubric",
    "synthetic_construction",
}

FAMILY_ALIASES = {"risk_assessment": "clinical_risk_assessment"}


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}
        self.size: dict[str, int] = {}

    def add(self, item: str) -> None:
        if item not in self.parent:
            self.parent[item] = item
            self.size[item] = 1

    def find(self, item: str) -> str:
        self.add(item)
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != item:
            parent = self.parent[item]
            self.parent[item] = root
            item = parent
        return root

    def union(self, left: str, right: str) -> bool:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return False
        if self.size[left_root] < self.size[right_root]:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        self.size[left_root] += self.size[right_root]
        return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text-view", default=DEFAULT_TEXT_VIEW)
    parser.add_argument("--candidate-root", default=DEFAULT_CANDIDATE_ROOT)
    parser.add_argument("--exact-root", default=DEFAULT_EXACT_ROOT)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--qa-report", default=DEFAULT_QA)
    parser.add_argument("--families", default="", help="Optional comma-separated family filter.")
    parser.add_argument(
        "--emit-singletons",
        action="store_true",
        help="Write singleton components to JSONL. By default singleton coverage is counted in reports but not written row-by-row.",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def split_csv(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def counter_dict(counter: Counter, limit: int | None = None) -> dict[str, int]:
    return {str(k): int(v) for k, v in counter.most_common(limit)}


def display_family(family: str) -> str:
    alias = FAMILY_ALIASES.get(family)
    return f"{family} (alias: {alias})" if alias else family


def family_policy(family: str) -> dict[str, Any]:
    if family == "risk_assessment":
        return {
            "content_category": "clinical_risk_assessment",
            "candidate_component_status": "candidate_component_not_final_merge",
            "before_final_merge_requires": ["clinical_label_consistency_review_required"],
            "terminology_note": "This is clinical risk assessment knowledge, not a data-quality problem and not a prohibition signal.",
        }
    if family == "therapy_process":
        return {
            "content_category": family,
            "candidate_component_status": "candidate_component_not_final_merge",
            "before_final_merge_requires": ["process_structure_consistency_review_required", "generic_no_sequence_filter_required"],
        }
    if family in {"style_response", "qa_psychoeducation"}:
        return {
            "content_category": family,
            "candidate_component_status": "candidate_component_not_final_merge",
            "before_final_merge_requires": ["cluster_level_adjudication_required", "license_and_source_constraint_check_required"],
        }
    return {
        "content_category": family,
        "candidate_component_status": "candidate_component_not_final_merge",
        "before_final_merge_requires": ["cluster_level_adjudication_required", "source_provenance_preservation_required"],
    }


def load_text_view(path: Path, families: set[str]) -> tuple[dict[str, dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    records: dict[str, dict[str, Any]] = {}
    stats: dict[str, Any] = {
        "input_rows": 0,
        "comparable_rows": 0,
        "skipped_rows": 0,
        "parse_errors": 0,
        "duplicate_unit_id_rows": 0,
        "comparable_family_distribution": Counter(),
        "skipped_family_distribution": Counter(),
        "dataset_distribution": Counter(),
    }
    issues: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            stats["input_rows"] += 1
            try:
                row = json.loads(line)
            except Exception as exc:  # noqa: BLE001
                stats["parse_errors"] += 1
                issues.append({"issue": "parse_error", "line": line_no, "error": str(exc)})
                continue
            family = str(row.get("dedup_family") or "__missing_family__")
            if family not in COMPARABLE_FAMILIES or (families and family not in families):
                stats["skipped_rows"] += 1
                stats["skipped_family_distribution"][family] += 1
                continue
            unit_id = row.get("unit_id")
            if not unit_id:
                issues.append({"issue": "missing_unit_id", "line": line_no, "dedup_family": family})
                continue
            if unit_id in records:
                stats["duplicate_unit_id_rows"] += 1
                issues.append({"issue": "duplicate_unit_id", "line": line_no, "unit_id": unit_id})
                continue
            records[unit_id] = {
                "unit_id": unit_id,
                "dedup_family": family,
                "dataset": row.get("dataset"),
                "knowledge_type": row.get("knowledge_type"),
                "language": row.get("language"),
                "evidence_tier": row.get("evidence_tier"),
                "source_construction_type": row.get("source_construction_type"),
                "raw_file": row.get("raw_file"),
                "raw_line_no": row.get("raw_line_no"),
                "dedup_text_sha256": row.get("dedup_text_sha256"),
            }
            stats["comparable_rows"] += 1
            stats["comparable_family_distribution"][family] += 1
            stats["dataset_distribution"][str(row.get("dataset"))] += 1
    return records, stats, issues


def discover_candidate_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.glob("**/candidate_pairs.jsonl"))


def discover_exact_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.glob("*.exact_duplicate_clusters.jsonl"))


def load_edges(
    *,
    records: dict[str, dict[str, Any]],
    candidate_files: list[Path],
    exact_files: list[Path],
    families: set[str],
) -> tuple[dict[str, UnionFind], dict[str, dict[tuple[str, str], dict[str, Any]]], dict[str, Any], list[dict[str, Any]]]:
    ufs: dict[str, UnionFind] = defaultdict(UnionFind)
    edge_stats: dict[str, Any] = {
        "candidate_files": [],
        "exact_files": [],
        "candidate_edge_rows": 0,
        "candidate_edges_used": 0,
        "candidate_edges_skipped": 0,
        "candidate_parse_errors": 0,
        "exact_cluster_rows": 0,
        "exact_edges_used": 0,
        "exact_edges_skipped": 0,
        "exact_parse_errors": 0,
        "candidate_edges_by_family": Counter(),
        "exact_edges_by_family": Counter(),
        "candidate_edges_by_source_file": Counter(),
    }
    edge_summaries: dict[str, dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
    issues: list[dict[str, Any]] = []

    for unit_id, row in records.items():
        ufs[row["dedup_family"]].add(unit_id)

    for path in candidate_files:
        file_stats = {
            "path": str(path),
            "sha256": file_sha256(path),
            "rows": 0,
            "used_edges": 0,
            "skipped_edges": 0,
            "parse_errors": 0,
        }
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                file_stats["rows"] += 1
                edge_stats["candidate_edge_rows"] += 1
                try:
                    row = json.loads(line)
                except Exception as exc:  # noqa: BLE001
                    file_stats["parse_errors"] += 1
                    edge_stats["candidate_parse_errors"] += 1
                    issues.append({"issue": "candidate_parse_error", "path": str(path), "line": line_no, "error": str(exc)})
                    continue
                family = str(row.get("dedup_family") or "__missing_family__")
                if families and family not in families:
                    file_stats["skipped_edges"] += 1
                    edge_stats["candidate_edges_skipped"] += 1
                    continue
                left = row.get("left_unit_id")
                right = row.get("right_unit_id")
                if left not in records or right not in records:
                    file_stats["skipped_edges"] += 1
                    edge_stats["candidate_edges_skipped"] += 1
                    continue
                left_family = records[left]["dedup_family"]
                right_family = records[right]["dedup_family"]
                if family != left_family or family != right_family:
                    issues.append({
                        "issue": "candidate_family_mismatch",
                        "path": str(path),
                        "line": line_no,
                        "edge_family": family,
                        "left_family": left_family,
                        "right_family": right_family,
                        "left_unit_id": left,
                        "right_unit_id": right,
                    })
                    file_stats["skipped_edges"] += 1
                    edge_stats["candidate_edges_skipped"] += 1
                    continue
                pair_key = tuple(sorted((str(left), str(right))))
                current = edge_summaries[family].get(pair_key)
                similarity = row.get("cosine_similarity")
                summary = {
                    "edge_type": "embedding_candidate",
                    "source_file": str(path),
                    "candidate_pair_id": row.get("candidate_pair_id"),
                    "method": row.get("method"),
                    "bucket_id": row.get("bucket_id"),
                    "cosine_similarity": similarity,
                    "same_dataset": row.get("same_dataset"),
                    "same_knowledge_type": row.get("same_knowledge_type"),
                }
                if current is None or float(similarity or 0.0) > float(current.get("cosine_similarity") or 0.0):
                    edge_summaries[family][pair_key] = summary
                ufs[family].union(str(left), str(right))
                file_stats["used_edges"] += 1
                edge_stats["candidate_edges_used"] += 1
                edge_stats["candidate_edges_by_family"][family] += 1
                edge_stats["candidate_edges_by_source_file"][str(path)] += 1
        edge_stats["candidate_files"].append(file_stats)

    for path in exact_files:
        file_stats = {
            "path": str(path),
            "sha256": file_sha256(path),
            "clusters": 0,
            "used_edges": 0,
            "skipped_edges": 0,
            "parse_errors": 0,
        }
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                file_stats["clusters"] += 1
                edge_stats["exact_cluster_rows"] += 1
                try:
                    row = json.loads(line)
                except Exception as exc:  # noqa: BLE001
                    file_stats["parse_errors"] += 1
                    edge_stats["exact_parse_errors"] += 1
                    issues.append({"issue": "exact_parse_error", "path": str(path), "line": line_no, "error": str(exc)})
                    continue
                family = str(row.get("dedup_family") or "__missing_family__")
                if families and family not in families:
                    continue
                members = [str(item) for item in row.get("member_unit_ids") or [] if item in records]
                if len(members) < 2:
                    file_stats["skipped_edges"] += 1
                    edge_stats["exact_edges_skipped"] += 1
                    continue
                rep = members[0]
                for member in members[1:]:
                    pair_key = tuple(sorted((rep, member)))
                    edge_summaries[family][pair_key] = {
                        "edge_type": "exact_duplicate",
                        "source_file": str(path),
                        "exact_cluster_id": row.get("cluster_id") or row.get("exact_group_id"),
                        "dedup_text_sha256": row.get("dedup_text_sha256"),
                        "cosine_similarity": 1.0,
                        "same_dataset": records[rep].get("dataset") == records[member].get("dataset"),
                        "same_knowledge_type": records[rep].get("knowledge_type") == records[member].get("knowledge_type"),
                    }
                    ufs[family].union(rep, member)
                    file_stats["used_edges"] += 1
                    edge_stats["exact_edges_used"] += 1
                    edge_stats["exact_edges_by_family"][family] += 1
        edge_stats["exact_files"].append(file_stats)

    return ufs, edge_summaries, edge_stats, issues


def component_id_for(family: str, member_ids: list[str]) -> str:
    digest = hashlib.sha256(("::".join([family, *member_ids])).encode("utf-8")).hexdigest()[:20]
    return f"candcomp_{family}_{digest}"


def build_components(
    records: dict[str, dict[str, Any]],
    ufs: dict[str, UnionFind],
    edge_summaries: dict[str, dict[tuple[str, str], dict[str, Any]]],
    output_dir: Path,
    *,
    emit_singletons: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    family_members: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    unit_to_component: dict[str, str] = {}
    component_member_count: dict[str, int] = {}
    for unit_id, row in records.items():
        family = row["dedup_family"]
        root = ufs[family].find(unit_id)
        family_members[family][root].append(unit_id)

    for family, roots in family_members.items():
        for members in roots.values():
            sorted_members = sorted(members)
            component_id = component_id_for(family, sorted_members)
            component_member_count[component_id] = len(sorted_members)
            for unit_id in sorted_members:
                unit_to_component[unit_id] = component_id

    def edge_sort_key(edge: dict[str, Any]) -> tuple[int, float, str, str]:
        return (
            0 if edge.get("edge_type") == "exact_duplicate" else 1,
            -float(edge.get("cosine_similarity") or 0.0),
            str(edge.get("left_unit_id") or ""),
            str(edge.get("right_unit_id") or ""),
        )

    def edge_heap_rank(edge: dict[str, Any]) -> tuple[int, float, str, str]:
        return (
            1 if edge.get("edge_type") == "exact_duplicate" else 0,
            float(edge.get("cosine_similarity") or 0.0),
            str(edge.get("left_unit_id") or ""),
            str(edge.get("right_unit_id") or ""),
        )

    edge_accumulators: dict[str, dict[str, Any]] = {}

    def accumulator_for(component_id: str) -> dict[str, Any]:
        if component_id not in edge_accumulators:
            edge_accumulators[component_id] = {
                "edge_count": 0,
                "edge_type_counter": Counter(),
                "source_file_counter": Counter(),
                "min_similarity": None,
                "max_similarity": None,
                "top_edges_heap": [],
                "top_edges_seq": 0,
            }
        return edge_accumulators[component_id]

    def add_component_edge(component_id: str, edge: dict[str, Any]) -> None:
        acc = accumulator_for(component_id)
        acc["edge_count"] += 1
        acc["edge_type_counter"][str(edge.get("edge_type"))] += 1
        acc["source_file_counter"][str(edge.get("source_file"))] += 1
        if edge.get("cosine_similarity") is not None:
            similarity = float(edge["cosine_similarity"])
            acc["min_similarity"] = similarity if acc["min_similarity"] is None else min(acc["min_similarity"], similarity)
            acc["max_similarity"] = similarity if acc["max_similarity"] is None else max(acc["max_similarity"], similarity)
        heap: list[tuple[tuple[int, float, str, str], int, dict[str, Any]]] = acc["top_edges_heap"]
        rank = edge_heap_rank(edge)
        acc["top_edges_seq"] += 1
        heap_item = (rank, int(acc["top_edges_seq"]), edge)
        if len(heap) < 200:
            heapq.heappush(heap, heap_item)
            return
        if rank > heap[0][0]:
            heapq.heapreplace(heap, heap_item)

    for family, edges in edge_summaries.items():
        for (left, right), edge in edges.items():
            left_component = unit_to_component.get(left)
            right_component = unit_to_component.get(right)
            if not left_component or left_component != right_component:
                continue
            add_component_edge(left_component, {"left_unit_id": left, "right_unit_id": right, **edge})

    outputs: dict[str, Any] = {}
    aggregate = {
        "component_count": 0,
        "multi_member_component_count": 0,
        "singleton_component_count": 0,
        "covered_unit_count": 0,
        "multi_member_unit_count": 0,
        "singleton_unit_count": 0,
        "component_count_by_family": Counter(),
        "multi_member_component_count_by_family": Counter(),
        "singleton_component_count_by_family": Counter(),
        "covered_unit_count_by_family": Counter(),
        "multi_member_unit_count_by_family": Counter(),
        "singleton_unit_count_by_family": Counter(),
        "max_component_size_by_family": Counter(),
        "cross_dataset_component_count_by_family": Counter(),
    }
    largest_components: list[dict[str, Any]] = []

    for family in sorted(family_members):
        path = output_dir / f"{family}.candidate_components.jsonl"
        component_count = 0
        written_component_count = 0
        multi_count = 0
        singleton_count = 0
        covered_units = 0
        multi_units = 0
        singleton_units = 0
        cross_dataset_count = 0
        max_size = 0
        with path.open("w", encoding="utf-8") as handle:
            components = []
            for members in family_members[family].values():
                sorted_members = sorted(members)
                components.append(sorted_members)
            components.sort(key=lambda item: (-len(item), item[0]))
            for members in components:
                component_count += 1
                member_count = len(members)
                covered_units += member_count
                max_size = max(max_size, member_count)
                if member_count > 1:
                    multi_count += 1
                    multi_units += member_count
                else:
                    singleton_count += 1
                    singleton_units += member_count
                    if not emit_singletons:
                        continue

                dataset_counter: Counter[str] = Counter()
                knowledge_counter: Counter[str] = Counter()
                language_counter: Counter[str] = Counter()
                evidence_counter: Counter[str] = Counter()
                construction_counter: Counter[str] = Counter()
                raw_file_counter: Counter[str] = Counter()
                for unit_id in members:
                    row = records[unit_id]
                    dataset_counter[str(row.get("dataset"))] += 1
                    knowledge_counter[str(row.get("knowledge_type"))] += 1
                    language_counter[str(row.get("language"))] += 1
                    evidence_counter[str(row.get("evidence_tier"))] += 1
                    construction_counter[str(row.get("source_construction_type"))] += 1
                    raw_file_counter[str(row.get("raw_file"))] += 1
                is_cross_dataset = len(dataset_counter) > 1
                if is_cross_dataset:
                    cross_dataset_count += 1

                component_id = component_id_for(family, members)
                edge_acc = edge_accumulators.get(component_id)
                edge_heap = (edge_acc or {}).get("top_edges_heap", [])
                component_edges = sorted((item[2] for item in edge_heap), key=edge_sort_key)
                edge_count = int((edge_acc or {}).get("edge_count", 0))
                edge_type_counter = (edge_acc or {}).get("edge_type_counter", Counter())
                source_file_counter = (edge_acc or {}).get("source_file_counter", Counter())
                min_similarity = (edge_acc or {}).get("min_similarity")
                max_similarity = (edge_acc or {}).get("max_similarity")

                record = {
                    "schema_version": "theraskill_candidate_component_v1",
                    "component_id": component_id,
                    "dedup_family": family,
                    "display_family": display_family(family),
                    "member_count": member_count,
                    "representative_unit_id": members[0],
                    "member_unit_ids": members,
                    "dataset_distribution": counter_dict(dataset_counter),
                    "knowledge_type_distribution": counter_dict(knowledge_counter),
                    "language_distribution": counter_dict(language_counter),
                    "evidence_tier_distribution": counter_dict(evidence_counter),
                    "source_construction_type_distribution": counter_dict(construction_counter),
                    "raw_file_distribution": counter_dict(raw_file_counter),
                    "cross_dataset_component": is_cross_dataset,
                    "edge_count": edge_count,
                    "edge_type_distribution": counter_dict(edge_type_counter),
                    "edge_source_file_distribution": counter_dict(source_file_counter),
                    "min_edge_cosine_similarity": round(min_similarity, 6) if min_similarity is not None else None,
                    "max_edge_cosine_similarity": round(max_similarity, 6) if max_similarity is not None else None,
                    "candidate_edges_top200": component_edges,
                    "component_policy": family_policy(family),
                    "not_final_merged_unit": True,
                    "raw_units_unchanged": True,
                }
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
                written_component_count += 1
                if member_count > 1:
                    largest_components.append({
                        "component_id": record["component_id"],
                        "dedup_family": family,
                        "display_family": display_family(family),
                        "member_count": member_count,
                        "edge_count": edge_count,
                        "dataset_distribution": record["dataset_distribution"],
                        "knowledge_type_distribution": record["knowledge_type_distribution"],
                        "edge_type_distribution": record["edge_type_distribution"],
                        "min_edge_cosine_similarity": record["min_edge_cosine_similarity"],
                        "max_edge_cosine_similarity": record["max_edge_cosine_similarity"],
                    })

        outputs[family] = {
            "path": str(path),
            "sha256": file_sha256(path),
            "component_count": component_count,
            "written_component_count": written_component_count,
            "emit_singletons": emit_singletons,
            "multi_member_component_count": multi_count,
            "singleton_component_count": singleton_count,
            "covered_unit_count": covered_units,
            "multi_member_unit_count": multi_units,
            "singleton_unit_count": singleton_units,
            "max_component_size": max_size,
            "cross_dataset_component_count": cross_dataset_count,
        }
        aggregate["component_count"] += component_count
        aggregate["multi_member_component_count"] += multi_count
        aggregate["singleton_component_count"] += singleton_count
        aggregate["covered_unit_count"] += covered_units
        aggregate["multi_member_unit_count"] += multi_units
        aggregate["singleton_unit_count"] += singleton_units
        aggregate["component_count_by_family"][family] = component_count
        aggregate["multi_member_component_count_by_family"][family] = multi_count
        aggregate["singleton_component_count_by_family"][family] = singleton_count
        aggregate["covered_unit_count_by_family"][family] = covered_units
        aggregate["multi_member_unit_count_by_family"][family] = multi_units
        aggregate["singleton_unit_count_by_family"][family] = singleton_units
        aggregate["max_component_size_by_family"][family] = max_size
        aggregate["cross_dataset_component_count_by_family"][family] = cross_dataset_count

    aggregate_payload = {
        key: counter_dict(value) if isinstance(value, Counter) else value
        for key, value in aggregate.items()
    }
    largest_components.sort(key=lambda item: item["member_count"], reverse=True)
    return {"outputs": outputs, "aggregate": aggregate_payload}, largest_components[:200]


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    if output_dir.exists() and any(output_dir.glob("*.candidate_components.jsonl")) and not args.overwrite:
        raise FileExistsError(f"output dir has component files: {output_dir}; pass --overwrite")
    if args.overwrite and output_dir.exists():
        for path in output_dir.glob("*.candidate_components.jsonl"):
            path.unlink()

    families = split_csv(args.families)
    records, text_stats, issues = load_text_view(Path(args.text_view), families)
    candidate_files = discover_candidate_files(Path(args.candidate_root))
    exact_files = discover_exact_files(Path(args.exact_root))
    ufs, edge_summaries, edge_stats, edge_issues = load_edges(
        records=records,
        candidate_files=candidate_files,
        exact_files=exact_files,
        families=families,
    )
    issues.extend(edge_issues)
    component_payload, largest_components = build_components(
        records,
        ufs,
        edge_summaries,
        output_dir,
        emit_singletons=args.emit_singletons,
    )

    for key in [
        "comparable_family_distribution",
        "skipped_family_distribution",
        "dataset_distribution",
    ]:
        text_stats[key] = counter_dict(text_stats[key])
    for key in [
        "candidate_edges_by_family",
        "exact_edges_by_family",
        "candidate_edges_by_source_file",
    ]:
        edge_stats[key] = counter_dict(edge_stats[key])

    status = "complete" if not issues else "incomplete"
    report = {
        "schema_version": "theraskill_full_candidate_components_report_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "inputs": {
            "text_view": {"path": args.text_view, "sha256": file_sha256(args.text_view)},
            "candidate_root": args.candidate_root,
            "exact_root": args.exact_root,
        },
        "text_view_stats": text_stats,
        "edge_stats": edge_stats,
        **component_payload,
        "largest_components_top200": largest_components,
        "terminology": {
            "risk_assessment": "legacy dedup_family name for clinical risk assessment / symptom severity knowledge; not a data-quality issue",
            "quality_consistency_issue": "use for schema, label, or data-quality problems",
        },
        "output_policy": {
            "emit_singletons": args.emit_singletons,
            "singleton_coverage": "singletons are fully counted in aggregate/family statistics; by default they are not written row-by-row because they do not require cluster-level adjudication",
            "multi_member_components": "all multi-member components are written to family JSONL files",
        },
        "issues_count": len(issues),
        "issues": issues[:500],
        "not_final_merge_release": True,
        "raw_units_unchanged": True,
    }
    manifest = {
        "schema_version": "theraskill_full_candidate_components_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "inputs": report["inputs"],
        "output_dir": str(output_dir),
        "outputs": component_payload["outputs"],
        "aggregate": component_payload["aggregate"],
        "output_policy": report["output_policy"],
        "report": args.report,
        "qa_report": args.qa_report,
        "issues_count": len(issues),
        "not_final_merge_release": True,
    }
    qa = {
        "schema_version": "theraskill_full_candidate_components_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "text_view_stats": text_stats,
        "edge_stats_summary": {
            "candidate_edge_rows": edge_stats["candidate_edge_rows"],
            "candidate_edges_used": edge_stats["candidate_edges_used"],
            "candidate_edges_skipped": edge_stats["candidate_edges_skipped"],
            "exact_cluster_rows": edge_stats["exact_cluster_rows"],
            "exact_edges_used": edge_stats["exact_edges_used"],
            "exact_edges_skipped": edge_stats["exact_edges_skipped"],
        },
        "aggregate": component_payload["aggregate"],
        "output_policy": report["output_policy"],
        "issues_count": len(issues),
        "issues": issues[:500],
        "not_final_merge_release": True,
        "raw_units_unchanged": True,
    }
    write_json(args.report, report, sort_keys=False)
    write_json(args.manifest, manifest, sort_keys=False)
    write_json(args.qa_report, qa, sort_keys=False)
    print(json.dumps({
        "status": status,
        "issues_count": len(issues),
        "candidate_edge_rows": edge_stats["candidate_edge_rows"],
        "candidate_edges_used": edge_stats["candidate_edges_used"],
        "exact_edges_used": edge_stats["exact_edges_used"],
        "aggregate": component_payload["aggregate"],
        "report": args.report,
    }, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
