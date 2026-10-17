#!/usr/bin/env python3
"""Build the H6 gate report for Hermes runtime pilot readiness."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import ensure_output_path, read_json, write_text
from hermes_surface_registry import (
    H5_E2E_QA_PATH,
    H5_LLM_QA_PATH,
    H3_HISTORICAL_RUNTIME_SMOKE_QA_PATH,
    H6_COMPLETION_REPORT_PATH,
    H6_DECISIONS_PATH,
    H6_ENGINEERING_RUNTIME_PILOT_QA_PATH,
    H6_PACKET_INDEX_PATH,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DECISIONS = H6_DECISIONS_PATH
DEFAULT_PILOT_QA = H6_ENGINEERING_RUNTIME_PILOT_QA_PATH
DEFAULT_H3_QA = H3_HISTORICAL_RUNTIME_SMOKE_QA_PATH
DEFAULT_H5_E2E_QA = H5_E2E_QA_PATH
DEFAULT_H5_LLM_QA = H5_LLM_QA_PATH
DEFAULT_PACKET_INDEX = H6_PACKET_INDEX_PATH
DEFAULT_OUTPUT = H6_COMPLETION_REPORT_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decisions", default=str(DEFAULT_DECISIONS))
    parser.add_argument("--pilot-qa", default=str(DEFAULT_PILOT_QA))
    parser.add_argument("--h3-qa", default=str(DEFAULT_H3_QA))
    parser.add_argument("--h5-e2e-qa", default=str(DEFAULT_H5_E2E_QA))
    parser.add_argument("--h5-llm-qa", default=str(DEFAULT_H5_LLM_QA))
    parser.add_argument("--packet-index", default=str(DEFAULT_PACKET_INDEX))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def status_line(data: dict[str, Any]) -> str:
    parts = []
    for key in ["status", "issues_count", "case_count", "scenario_count", "evaluated_count", "passed_count", "failed_count"]:
        if key in data:
            parts.append(f"`{key}={data[key]}`")
    return ", ".join(parts)


def all_existing_qa_clear(*qas: dict[str, Any]) -> bool:
    return all(qa.get("status") == "complete" and qa.get("issues_count") == 0 for qa in qas)


def main() -> int:
    args = parse_args()
    output = Path(args.output)
    ensure_output_path(output, overwrite=args.overwrite)

    decisions_path = Path(args.decisions)
    pilot_qa_path = Path(args.pilot_qa)
    h3_qa_path = Path(args.h3_qa)
    h5_e2e_qa_path = Path(args.h5_e2e_qa)
    h5_llm_qa_path = Path(args.h5_llm_qa)
    packet_index_path = Path(args.packet_index)

    decisions = read_json(decisions_path)
    pilot_qa = read_json(pilot_qa_path)
    h3_qa = read_json(h3_qa_path)
    h5_e2e_qa = read_json(h5_e2e_qa_path)
    h5_llm_qa = read_json(h5_llm_qa_path)
    packet_index = read_json(packet_index_path) if packet_index_path.exists() else {}

    skill_decisions = decisions.get("skills") or {}
    approved = {"approve", "approve_with_minor_edits"}
    human_review_passed = bool(skill_decisions) and all(
        info.get("decision") in approved for info in skill_decisions.values()
    )
    pending_skills = sorted(
        skill for skill, info in skill_decisions.items()
        if info.get("decision") not in approved
    )
    regression_clear = all_existing_qa_clear(h3_qa, h5_e2e_qa, h5_llm_qa, pilot_qa)
    h6_gate_passed = human_review_passed and regression_clear
    status = "controlled_runtime_pilot_ready" if h6_gate_passed else "pending_human_review"
    reviewer = decisions.get("reviewer") or "not recorded"
    review_scope_note = decisions.get("review_scope_note") or (
        "User-authorized Codex agent review; not licensed clinical validation."
        if str(reviewer).lower().startswith("codex agent review")
        else ""
    )
    expert_manual_review = decisions.get("expert_manual_review") or {}

    lines = [
        "# TheraSkill Hermes H6 Gate Report",
        "",
        f"Created at: `{utc_now()}`",
        "",
        f"Status: `{status}`",
        "",
        "## Scope",
        "",
        "This report summarizes the H6 gate for the six Hermes runtime skills.",
        "It separates engineering pilot evidence from formal human-review approval.",
        "",
        "## Gate Inputs",
        "",
        f"- Human review decisions: `{decisions_path}`",
        f"- Runtime pilot QA: `{pilot_qa_path}`",
        f"- H3 historical runtime smoke QA: `{h3_qa_path}`",
        f"- H5 E2E retrieval QA: `{h5_e2e_qa_path}`",
        f"- H5 LLM response QA: `{h5_llm_qa_path}`",
        f"- Per-skill review packet index: `{packet_index_path}`",
        "",
        "## Current Evidence",
        "",
        f"- H3 historical runtime smoke: {status_line(h3_qa)}",
        f"- H5 E2E retrieval eval: {status_line(h5_e2e_qa)}",
        f"- H5 LLM response eval: {status_line(h5_llm_qa)}",
        f"- H6 runtime pilot: {status_line(pilot_qa)}",
        "",
        "## Human Review",
        "",
        f"- Decisions status: `{decisions.get('status')}`",
        f"- Reviewer: `{reviewer}`",
        f"- Review scope note: `{review_scope_note or 'none'}`",
        f"- Expert manual review: `{expert_manual_review.get('alignment', 'not recorded')}`",
        f"- Expert review summary: `{expert_manual_review.get('summary', 'not recorded')}`",
        f"- Human review passed: `{human_review_passed}`",
        f"- Skills not yet approved: `{', '.join(pending_skills) if pending_skills else 'none'}`",
        f"- Per-skill review packets: `{packet_index.get('packet_count', 0)}`",
        "",
    ]
    if human_review_passed:
        lines.extend([
            "The decisions file records completed user-authorized review for all six skills.",
            "This is a project gate review, not licensed clinical validation.",
            "",
        ])
    else:
        lines.extend([
            "Formal H6 approval requires an authorized reviewer to update the decisions file.",
            "The agent must not convert `pending_human_review` into approval without explicit user authorization.",
            "",
        ])
    lines.extend([
        "Per-skill packets for reviewer convenience:",
        "",
    ])
    for skill, info in sorted((packet_index.get("packets") or {}).items()):
        lines.append(f"- `{skill}`: `{info.get('path')}`")
    lines.extend([
        "",
        "## Runtime Pilot",
        "",
        f"- Pilot status: `{pilot_qa.get('status')}`",
        f"- Pilot case count: `{pilot_qa.get('case_count')}`",
        f"- Pilot passed / failed: `{pilot_qa.get('passed_count')} / {pilot_qa.get('failed_count')}`",
        f"- Pilot issues count: `{pilot_qa.get('issues_count')}`",
        f"- Skill distribution: `{pilot_qa.get('skill_distribution')}`",
        f"- Case type distribution: `{pilot_qa.get('case_type_distribution')}`",
        "",
        "## Gate Decision",
        "",
        f"- Regression QA clear: `{regression_clear}`",
        f"- Human review passed: `{human_review_passed}`",
        f"- H6 gate passed: `{h6_gate_passed}`",
        "",
    ])
    if h6_gate_passed:
        lines.extend([
            "All six Hermes skills may be marked `controlled_runtime_pilot_ready`.",
            "",
        ])
    else:
        lines.extend([
            "H6 is not complete yet. The next required action is formal human review.",
            "After the decisions file is updated, rerun this report and any required H3/H5 QA indicated by the reviewer.",
            "",
        ])
    write_text(output, "\n".join(lines))
    print(json.dumps({
        "output": str(output),
        "status": status,
        "h6_gate_passed": h6_gate_passed,
        "human_review_passed": human_review_passed,
        "regression_clear": regression_clear,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
