#!/usr/bin/env python3
"""Validate H6 human-review decisions and emit next actions."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import ensure_output_path, read_json, write_json, write_text
from hermes_surface_registry import (
    APPLY_H6_RUNTIME_STATUS_RELPATH,
    BUILD_H6_COMPLETION_REPORT_RELPATH,
    BUILD_H6_SKILL_REVIEW_PACKETS_RELPATH,
    CONDA_PYTHON,
    H6_DECISIONS_PATH,
    H6_DECISIONS_QA_PATH,
    H6_NEXT_ACTIONS_JSON_PATH,
    H6_NEXT_ACTIONS_MD_PATH,
    H6_PACKET_INDEX_PATH,
    RUN_H6_RUNTIME_PILOT_RELPATH,
    RUN_HERMES_E2E_EVAL_RELPATH,
    RUN_HERMES_LLM_RESPONSE_EVALUATOR_RELPATH,
    RUN_HERMES_RUNTIME_SMOKE_RELPATH,
    SKILLS,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DECISIONS = H6_DECISIONS_PATH
DEFAULT_PACKET_INDEX = H6_PACKET_INDEX_PATH
DEFAULT_QA = H6_DECISIONS_QA_PATH
DEFAULT_NEXT_ACTIONS_JSON = H6_NEXT_ACTIONS_JSON_PATH
DEFAULT_NEXT_ACTIONS_MD = H6_NEXT_ACTIONS_MD_PATH

ALLOWED_DECISIONS = {
    "pending_human_review",
    "approve",
    "approve_with_minor_edits",
    "revise_and_rerun_qa",
    "block",
}

APPROVAL_DECISIONS = {"approve", "approve_with_minor_edits"}

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decisions", default=str(DEFAULT_DECISIONS))
    parser.add_argument("--packet-index", default=str(DEFAULT_PACKET_INDEX))
    parser.add_argument("--qa-report", default=str(DEFAULT_QA))
    parser.add_argument("--next-actions-json", default=str(DEFAULT_NEXT_ACTIONS_JSON))
    parser.add_argument("--next-actions-md", default=str(DEFAULT_NEXT_ACTIONS_MD))
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def require_list(value: Any) -> bool:
    return isinstance(value, list)


def build_commands(requires_h3: bool, requires_h5: bool, requires_llm: bool, gate_candidate: bool) -> list[str]:
    commands: list[str] = []
    if requires_h3:
        commands.append(f"{CONDA_PYTHON} {RUN_HERMES_RUNTIME_SMOKE_RELPATH} --overwrite")
    if requires_h5:
        commands.append(f"{CONDA_PYTHON} {RUN_HERMES_E2E_EVAL_RELPATH} --overwrite")
    if requires_llm:
        commands.append(
            f"{CONDA_PYTHON} {RUN_HERMES_LLM_RESPONSE_EVALUATOR_RELPATH} "
            "--model-config configs/theraskill/retrieval.yaml --limit 12 --overwrite"
        )
    commands.append(f"{CONDA_PYTHON} {RUN_H6_RUNTIME_PILOT_RELPATH} --overwrite")
    commands.append(f"{CONDA_PYTHON} {BUILD_H6_SKILL_REVIEW_PACKETS_RELPATH} --overwrite")
    commands.append(f"{CONDA_PYTHON} {BUILD_H6_COMPLETION_REPORT_RELPATH} --overwrite")
    apply_flag = " --apply" if gate_candidate else ""
    commands.append(f"{CONDA_PYTHON} {APPLY_H6_RUNTIME_STATUS_RELPATH} --overwrite{apply_flag}")
    return commands


def main() -> int:
    args = parse_args()
    decisions_path = Path(args.decisions)
    packet_index_path = Path(args.packet_index)
    qa_path = Path(args.qa_report)
    next_actions_json = Path(args.next_actions_json)
    next_actions_md = Path(args.next_actions_md)
    for path in (qa_path, next_actions_json, next_actions_md):
        ensure_output_path(path, overwrite=args.overwrite)

    decisions = read_json(decisions_path)
    packet_index = read_json(packet_index_path) if packet_index_path.exists() else {}
    skills_data = decisions.get("skills") or {}
    issues: list[dict[str, Any]] = []
    skill_status: dict[str, dict[str, Any]] = {}

    missing = sorted(set(SKILLS) - set(skills_data))
    extra = sorted(set(skills_data) - set(SKILLS))
    for skill in missing:
        issues.append({"skill": skill, "issue": "missing_skill_decision"})
    for skill in extra:
        issues.append({"skill": skill, "issue": "unexpected_skill_decision"})

    for skill in SKILLS:
        info = skills_data.get(skill) or {}
        decision = info.get("decision")
        skill_issues: list[str] = []
        if decision not in ALLOWED_DECISIONS:
            skill_issues.append("invalid_decision")
        for field in ["required_edits", "boundary_issues", "retrieval_policy_issues", "reference_doc_issues"]:
            if not require_list(info.get(field)):
                skill_issues.append(f"{field}_must_be_list")
        for field in ["requires_h3_rerun", "requires_h5_rerun"]:
            if not isinstance(info.get(field), bool):
                skill_issues.append(f"{field}_must_be_boolean")
        if decision in APPROVAL_DECISIONS:
            if not str(info.get("reviewer") or "").strip():
                skill_issues.append("approved_skill_missing_reviewer")
            if not str(info.get("reviewed_at") or "").strip():
                skill_issues.append("approved_skill_missing_reviewed_at")
        if decision in {"revise_and_rerun_qa", "block"} and not (
            info.get("required_edits")
            or info.get("boundary_issues")
            or info.get("retrieval_policy_issues")
            or info.get("reference_doc_issues")
        ):
            skill_issues.append("revision_or_block_requires_issue_detail")
        for issue in skill_issues:
            issues.append({"skill": skill, "issue": issue})
        skill_status[skill] = {
            "decision": decision,
            "issues": skill_issues,
            "requires_h3_rerun": bool(info.get("requires_h3_rerun")),
            "requires_h5_rerun": bool(info.get("requires_h5_rerun")),
            "review_packet": (packet_index.get("packets") or {}).get(skill, {}).get("path", ""),
        }

    decisions_complete = all(skill_status.get(skill, {}).get("decision") in APPROVAL_DECISIONS for skill in SKILLS)
    pending_skills = sorted(
        skill for skill, status in skill_status.items()
        if status.get("decision") == "pending_human_review"
    )
    revise_skills = sorted(
        skill for skill, status in skill_status.items()
        if status.get("decision") == "revise_and_rerun_qa"
    )
    blocked_skills = sorted(
        skill for skill, status in skill_status.items()
        if status.get("decision") == "block"
    )
    requires_h3 = any(status.get("requires_h3_rerun") for status in skill_status.values())
    requires_h5 = any(status.get("requires_h5_rerun") for status in skill_status.values())
    requires_llm = any(
        skills_data.get(skill, {}).get("boundary_issues") or skills_data.get(skill, {}).get("required_edits")
        for skill in revise_skills
    )
    status = "valid_pending_or_complete" if not issues else "failed"
    gate_candidate = decisions_complete and not issues
    commands = build_commands(requires_h3, requires_h5, requires_llm, gate_candidate)
    next_required_action = (
        "rerun_h6_completion_report"
        if gate_candidate
        else "human_review_or_revision_required"
    )
    created_at = utc_now()
    qa = {
        "schema_version": "theraskill_h6_human_review_decisions_qa_v1",
        "created_at": created_at,
        "status": status,
        "issues_count": len(issues),
        "issues": issues,
        "decisions_complete": decisions_complete,
        "gate_candidate": gate_candidate,
        "pending_skills": pending_skills,
        "revise_skills": revise_skills,
        "blocked_skills": blocked_skills,
        "skill_status": skill_status,
        "next_required_action": next_required_action,
    }
    next_actions = {
        "schema_version": "theraskill_h6_human_review_next_actions_v1",
        "created_at": created_at,
        "decisions": str(decisions_path),
        "qa_report": str(qa_path),
        "gate_candidate": gate_candidate,
        "next_required_action": next_required_action,
        "pending_skills": pending_skills,
        "revise_skills": revise_skills,
        "blocked_skills": blocked_skills,
        "requires_h3_rerun": requires_h3,
        "requires_h5_rerun": requires_h5,
        "requires_llm_response_eval_rerun": requires_llm,
        "commands": commands,
        "scope_note": "This validates human-review decision structure and lists next commands. It does not approve skills.",
    }
    write_json(qa_path, qa)
    write_json(next_actions_json, next_actions)

    md_lines = [
        "# H6 Human Review Next Actions",
        "",
        f"Created at: `{created_at}`",
        "",
        f"Decision QA status: `{status}`",
        f"Gate candidate: `{gate_candidate}`",
        f"Next required action: `{next_required_action}`",
        "",
        "## Skill Status",
        "",
    ]
    for skill in SKILLS:
        item = skill_status.get(skill, {})
        md_lines.append(f"- `{skill}`: `{item.get('decision')}`; packet: `{item.get('review_packet')}`")
    md_lines.extend([
        "",
        "## Groups",
        "",
        f"- Pending human review: `{', '.join(pending_skills) if pending_skills else 'none'}`",
        f"- Revise and rerun QA: `{', '.join(revise_skills) if revise_skills else 'none'}`",
        f"- Blocked: `{', '.join(blocked_skills) if blocked_skills else 'none'}`",
        "",
        "## Commands",
        "",
    ])
    for command in commands:
        md_lines.extend(["```bash", command, "```", ""])
    md_lines.extend([
        "## Note",
        "",
        "This file is generated from `human_review_round1_decisions.json`.",
        "It does not convert pending decisions into approval.",
        "",
    ])
    write_text(next_actions_md, "\n".join(md_lines))

    print(json.dumps({
        "qa_report": str(qa_path),
        "next_actions_json": str(next_actions_json),
        "next_actions_md": str(next_actions_md),
        "status": status,
        "issues_count": len(issues),
        "gate_candidate": gate_candidate,
        "next_required_action": next_required_action,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
