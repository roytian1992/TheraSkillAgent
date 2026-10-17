#!/usr/bin/env python3
"""Build external-review-friendly adjudication packets for residual conditioned comparison ties."""

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

from io_utils import file_sha256, read_json, read_jsonl, write_json, write_text
from simulation_surface_registry import (
    FORMAL_CONDITIONED_EVAL_EVIDENCE_DIR,
    SIMULATION_SCENARIO_REGISTRY_PATH,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BENCHMARK_DIR = FORMAL_CONDITIONED_EVAL_EVIDENCE_DIR
DEFAULT_SCENARIO_REGISTRY = SIMULATION_SCENARIO_REGISTRY_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", default=str(DEFAULT_BENCHMARK_DIR))
    parser.add_argument("--scenario-registry", default=str(DEFAULT_SCENARIO_REGISTRY))
    parser.add_argument("--scenario-id", action="append")
    parser.add_argument("--output-dir")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()

def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def stable_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def slugify_scenario_id(value: str) -> str:
    return value.replace("::", "__").replace("/", "_")


def format_transcript_excerpt(transcript: list[dict[str, Any]], max_turns: int = 6) -> list[dict[str, str]]:
    excerpt: list[dict[str, str]] = []
    for row in transcript[:max_turns]:
        excerpt.append(
            {
                "role": clean_text(row.get("role")),
                "text": clean_text(row.get("text")),
            }
        )
    return excerpt


def scenario_digest(registry_row: dict[str, Any]) -> dict[str, Any]:
    return {
        "scenario_id": registry_row.get("scenario_id"),
        "scenario_family": registry_row.get("scenario_family"),
        "case_id": registry_row.get("case_id"),
        "case_family": registry_row.get("case_family"),
        "risk_band": registry_row.get("risk_band"),
        "difficulty_band": registry_row.get("difficulty_band"),
        "recommended_turn_budget": registry_row.get("recommended_turn_budget"),
        "split_labels": registry_row.get("split_labels") or {},
        "scenario_brief": registry_row.get("scenario_brief"),
        "scenario_tags": registry_row.get("scenario_tags") or [],
        "primary_target_construct": registry_row.get("primary_target_construct"),
        "review_focus": registry_row.get("review_focus") or [],
    }


def runtime_reviewer_payload(report: dict[str, Any]) -> dict[str, Any]:
    reviewer = report.get("reviewer_result") or {}
    if isinstance(reviewer.get("result"), dict):
        return reviewer["result"]
    return reviewer


def build_arm_snapshot(arm_row: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    reviewer = runtime_reviewer_payload(report)
    findings = reviewer.get("findings") or []
    return {
        "therapist_arm": arm_row.get("therapist_arm"),
        "therapist_arm_label": arm_row.get("therapist_arm_label"),
        "review_gate": arm_row.get("review_gate") or reviewer.get("review_gate") or {},
        "derived_metrics": arm_row.get("derived_metrics") or {},
        "final_state": arm_row.get("final_state") or report.get("final_session_state") or {},
        "patient_text_sample": arm_row.get("patient_text_sample") or [],
        "transcript_excerpt": format_transcript_excerpt(report.get("transcript") or []),
        "finding_digest": [
            {
                "severity": row.get("severity"),
                "category": row.get("category"),
                "message": row.get("message"),
            }
            for row in findings[:8]
        ],
        "runtime_report_path": arm_row.get("runtime_report_path"),
        "runtime_report_sha256": file_sha256(arm_row.get("runtime_report_path") or ""),
    }


def build_packet(
    scenario_id: str,
    summary: dict[str, Any],
    comparison_row: dict[str, Any],
    alignment_row: dict[str, Any],
    arm_rows: dict[str, dict[str, Any]],
    scenario_row: dict[str, Any],
) -> dict[str, Any]:
    failed_checks = [row for row in (comparison_row.get("checks") or []) if row.get("status") != "pass"]
    arm_snapshots: dict[str, Any] = {}
    for arm_name, arm_row in arm_rows.items():
        report = read_json(arm_row["runtime_report_path"])
        arm_snapshots[arm_name] = build_arm_snapshot(arm_row, report)

    packet_id = f"conditioned_tie::{stable_hash(scenario_id)}"
    return {
        "schema_version": "theraskill_conditioned_adjudication_packet_v1",
        "packet_id": packet_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_dir": str(Path(summary["artifacts"]["comparison_results"]["path"]).parent),
        "residual_type": "heuristic_tie_needing_external_adjudication",
        "scenario": scenario_digest(scenario_row),
        "comparison_context": {
            "comparison_gate": comparison_row.get("gate_status"),
            "failed_checks": failed_checks,
            "pass_count": comparison_row.get("pass_count"),
            "check_count": comparison_row.get("check_count"),
        },
        "expectation_alignment": alignment_row,
        "baseline_context": {
            "supportive_pass_count": (summary.get("arm_expectation_alignment") or {}).get("supportive_pass_count"),
            "unexpected_warning_arm_runs": (summary.get("arm_expectation_alignment") or {}).get("unexpected_warning_arm_runs"),
            "unexpected_fail_arm_runs": (summary.get("arm_expectation_alignment") or {}).get("unexpected_fail_arm_runs"),
            "comparison_gate_distribution": (summary.get("distributions") or {}).get("comparison_gate") or {},
            "arm_expectation_gate_distribution": (summary.get("distributions") or {}).get("arm_expectation_gate") or {},
        },
        "arm_snapshots": arm_snapshots,
        "adjudication_focus": {
            "primary_contrast": ["supportive", "rupture_prone"],
            "questions": [
                "在 supportive 与 rupture_prone 两个 arm 的 transcript 中，是否存在临床上可感知的 alliance / interpretive pressure 差异，即使当前 heuristic rank 未完全拉开？",
                "该 residual 更像是 patient simulator substrate failure，还是比较启发式在此 case 上的分辨率不足？",
                "如果只能基于 reviewer gate、state trace、前 3 轮 transcript 进行判断，这条 row 是否应继续阻断当前 conditioned baseline 的 stronger claim？",
            ],
            "non_blocking_if": [
                "supportive arm review_gate 仍为 clean pass",
                "scenario-level expectation_alignment.gate_status=pass",
                "不存在 unexpected_warning_arms 或 unexpected_fail_arms",
                "残余仅集中在单条 rupture-prone heuristic check，而不是 reviewer / memory / boundary 维度失败",
            ],
        },
        "scope_note": "This packet does not auto-promote the residual row to pass. It consolidates the evidence needed for external adjudication so the remaining tie is no longer hidden inside raw JSONL outputs.",
        "source_artifacts": {
            "comparison_summary": {
                "path": summary["artifacts"]["comparison_results"]["path"].replace("comparison_results.jsonl", "comparison_summary.json"),
                "sha256": file_sha256(
                    Path(summary["artifacts"]["comparison_results"]["path"]).parent / "comparison_summary.json"
                ),
            },
            "comparison_results": summary["artifacts"]["comparison_results"],
            "arm_results": summary["artifacts"]["arm_results"],
            "arm_expectation_alignment": summary["artifacts"]["arm_expectation_alignment"],
        },
    }


def render_packet_markdown(packet: dict[str, Any]) -> str:
    scenario = packet["scenario"]
    lines: list[str] = []
    lines.append(f"# Conditioned Tie Adjudication Packet: `{scenario['scenario_id']}`")
    lines.append("")
    lines.append(f"- Packet id: `{packet['packet_id']}`")
    lines.append(f"- Residual type: `{packet['residual_type']}`")
    lines.append(f"- Scenario family: `{scenario.get('scenario_family')}`")
    lines.append(f"- Case id: `{scenario.get('case_id')}`")
    lines.append(f"- Difficulty band: `{scenario.get('difficulty_band')}`")
    lines.append(f"- Risk band: `{scenario.get('risk_band')}`")
    lines.append("")
    lines.append("## Context")
    lines.append("")
    lines.append(f"- comparison gate: `{packet['comparison_context']['comparison_gate']}`")
    lines.append(
        f"- expectation alignment gate: `{(packet['expectation_alignment'] or {}).get('gate_status')}`"
    )
    lines.append(
        f"- supportive clean pass count in promoted baseline: `{packet['baseline_context']['supportive_pass_count']}`"
    )
    lines.append(
        f"- unexpected warnings / fails in promoted baseline: "
        f"`{packet['baseline_context']['unexpected_warning_arm_runs']}` / "
        f"`{packet['baseline_context']['unexpected_fail_arm_runs']}`"
    )
    lines.append("")
    lines.append("## Failed Heuristic Check")
    lines.append("")
    for row in packet["comparison_context"]["failed_checks"]:
        lines.append(f"- check: `{row.get('check')}`")
        for evidence in row.get("evidence") or []:
            lines.append(f"  - `{evidence}`")
    lines.append("")
    lines.append("## Adjudication Questions")
    lines.append("")
    for question in packet["adjudication_focus"]["questions"]:
        lines.append(f"- {question}")
    lines.append("")
    for arm_name in ["supportive", "rupture_prone", "poor", "overly_directive"]:
        snapshot = packet["arm_snapshots"].get(arm_name)
        if not snapshot:
            continue
        lines.append(f"## Arm: `{arm_name}`")
        lines.append("")
        lines.append(f"- review gate: `{snapshot['review_gate'].get('gate_status')}`")
        lines.append(f"- alliance: `{(snapshot.get('final_state') or {}).get('alliance_state')}`")
        lines.append(f"- disclosure: `{(snapshot.get('final_state') or {}).get('disclosure_state')}`")
        lines.append(f"- felt_understanding: `{(snapshot.get('final_state') or {}).get('felt_understanding')}`")
        lines.append(f"- readiness: `{(snapshot.get('final_state') or {}).get('readiness_state')}`")
        lines.append("")
        lines.append("Transcript excerpt:")
        for row in snapshot.get("transcript_excerpt") or []:
            lines.append(f"- {row['role']}: {row['text']}")
        lines.append("")
    lines.append("## Scope Note")
    lines.append("")
    lines.append(packet["scope_note"])
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    benchmark_dir = Path(args.benchmark_dir)
    output_dir = Path(args.output_dir) if args.output_dir else benchmark_dir / "adjudication"
    comparison_summary = read_json(benchmark_dir / "comparison_summary.json")
    comparison_rows = read_jsonl(benchmark_dir / "comparison_results.jsonl")
    arm_rows = read_jsonl(benchmark_dir / "arm_results.jsonl")
    alignment_rows = read_jsonl(benchmark_dir / "arm_expectation_alignment.jsonl")
    scenario_rows = read_jsonl(args.scenario_registry)

    comparison_by_scenario = {row["scenario_id"]: row for row in comparison_rows}
    alignment_by_scenario = {row["scenario_id"]: row for row in alignment_rows}
    scenario_by_id = {row["scenario_id"]: row for row in scenario_rows}
    arm_by_scenario: dict[str, dict[str, dict[str, Any]]] = {}
    for row in arm_rows:
        arm_by_scenario.setdefault(row["scenario_id"], {})[row["therapist_arm"]] = row

    target_ids = args.scenario_id or [
        row["scenario_id"]
        for row in comparison_rows
        if row.get("gate_status") != "pass"
    ]

    packets: list[dict[str, Any]] = []
    packet_paths: list[dict[str, str]] = []
    for scenario_id in target_ids:
        if scenario_id not in comparison_by_scenario:
            raise KeyError(f"scenario not found in comparison results: {scenario_id}")
        if scenario_id not in alignment_by_scenario:
            raise KeyError(f"scenario not found in arm expectation alignment: {scenario_id}")
        if scenario_id not in arm_by_scenario:
            raise KeyError(f"scenario not found in arm results: {scenario_id}")
        if scenario_id not in scenario_by_id:
            raise KeyError(f"scenario not found in scenario registry: {scenario_id}")

        packet = build_packet(
            scenario_id=scenario_id,
            summary=comparison_summary,
            comparison_row=comparison_by_scenario[scenario_id],
            alignment_row=alignment_by_scenario[scenario_id],
            arm_rows=arm_by_scenario[scenario_id],
            scenario_row=scenario_by_id[scenario_id],
        )
        packets.append(packet)

        slug = slugify_scenario_id(scenario_id)
        json_path = output_dir / f"heuristic_tie_packet__{slug}.json"
        md_path = output_dir / f"heuristic_tie_packet__{slug}.md"
        if not args.overwrite and (json_path.exists() or md_path.exists()):
            raise FileExistsError(f"packet already exists for {scenario_id}; pass --overwrite to replace it")
        write_json(json_path, packet)
        write_text(md_path, render_packet_markdown(packet).rstrip() + "\n")
        packet_paths.append(
            {
                "scenario_id": scenario_id,
                "json_path": str(json_path),
                "json_sha256": file_sha256(json_path) or "",
                "markdown_path": str(md_path),
                "markdown_sha256": file_sha256(md_path) or "",
            }
        )

    summary_payload = {
        "schema_version": "theraskill_conditioned_adjudication_summary_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_dir": str(benchmark_dir),
        "mixed_or_fail_rows_in_source": len([row for row in comparison_rows if row.get("gate_status") != "pass"]),
        "packet_count": len(packet_paths),
        "scenario_ids": [row["scenario_id"] for row in packet_paths],
        "packet_paths": packet_paths,
        "source_summary_path": str(benchmark_dir / "comparison_summary.json"),
        "source_summary_sha256": file_sha256(benchmark_dir / "comparison_summary.json"),
    }
    write_json(output_dir / "adjudication_summary.json", summary_payload)


if __name__ == "__main__":
    main()
