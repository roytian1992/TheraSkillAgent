#!/usr/bin/env python3
"""Apply H6 runtime status to Hermes skill manifests after human review."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import ensure_output_path, read_json, write_json
from hermes_surface_registry import (
    H5_E2E_QA_PATH,
    H5_LLM_QA_PATH,
    H3_HISTORICAL_RUNTIME_SMOKE_QA_PATH,
    H6_DECISIONS_PATH,
    H6_DECISIONS_QA_PATH,
    H6_ENGINEERING_RUNTIME_PILOT_QA_PATH,
    H6_STATUS_APPLY_REPORT_PATH,
    OUTPUT_ROOT as DEFAULT_SKILLS_ROOT,
    SKILLS,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DECISIONS = H6_DECISIONS_PATH
DEFAULT_DECISIONS_QA = H6_DECISIONS_QA_PATH
DEFAULT_PILOT_QA = H6_ENGINEERING_RUNTIME_PILOT_QA_PATH
DEFAULT_H3_QA = H3_HISTORICAL_RUNTIME_SMOKE_QA_PATH
DEFAULT_H5_E2E_QA = H5_E2E_QA_PATH
DEFAULT_H5_LLM_QA = H5_LLM_QA_PATH
DEFAULT_REPORT = H6_STATUS_APPLY_REPORT_PATH

APPROVAL_DECISIONS = {"approve", "approve_with_minor_edits"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills-root", default=str(DEFAULT_SKILLS_ROOT))
    parser.add_argument("--decisions", default=str(DEFAULT_DECISIONS))
    parser.add_argument("--decisions-qa", default=str(DEFAULT_DECISIONS_QA))
    parser.add_argument("--pilot-qa", default=str(DEFAULT_PILOT_QA))
    parser.add_argument("--h3-qa", default=str(DEFAULT_H3_QA))
    parser.add_argument("--h5-e2e-qa", default=str(DEFAULT_H5_E2E_QA))
    parser.add_argument("--h5-llm-qa", default=str(DEFAULT_H5_LLM_QA))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--apply", action="store_true", help="Actually update skill manifests when gate checks pass.")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def qa_clear(data: dict[str, Any]) -> bool:
    return data.get("status") == "complete" and data.get("issues_count") == 0


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"missing skill manifest: {path}")
    return read_json(path)


def main() -> int:
    args = parse_args()
    skills_root = Path(args.skills_root)
    report_path = Path(args.report)
    ensure_output_path(report_path, overwrite=args.overwrite)

    decisions_path = Path(args.decisions)
    decisions_qa_path = Path(args.decisions_qa)
    pilot_qa_path = Path(args.pilot_qa)
    h3_qa_path = Path(args.h3_qa)
    h5_e2e_qa_path = Path(args.h5_e2e_qa)
    h5_llm_qa_path = Path(args.h5_llm_qa)

    decisions = read_json(decisions_path)
    decisions_qa = read_json(decisions_qa_path)
    pilot_qa = read_json(pilot_qa_path)
    h3_qa = read_json(h3_qa_path)
    h5_e2e_qa = read_json(h5_e2e_qa_path)
    h5_llm_qa = read_json(h5_llm_qa_path)

    skill_decisions = decisions.get("skills") or {}
    expert_manual_review = decisions.get("expert_manual_review") or {}
    issues: list[dict[str, Any]] = []
    missing = sorted(set(SKILLS) - set(skill_decisions))
    extra = sorted(set(skill_decisions) - set(SKILLS))
    for skill in missing:
        issues.append({"skill": skill, "issue": "missing_decision"})
    for skill in extra:
        issues.append({"skill": skill, "issue": "unexpected_decision"})

    approved_skills: list[str] = []
    blocked_skills: list[str] = []
    pending_or_revision_skills: list[str] = []
    for skill in SKILLS:
        decision = (skill_decisions.get(skill) or {}).get("decision")
        if decision in APPROVAL_DECISIONS:
            approved_skills.append(skill)
        elif decision == "block":
            blocked_skills.append(skill)
        else:
            pending_or_revision_skills.append(skill)

    regression_clear = all(qa_clear(qa) for qa in [pilot_qa, h3_qa, h5_e2e_qa, h5_llm_qa])
    decisions_valid = decisions_qa.get("issues_count") == 0 and decisions_qa.get("status") == "valid_pending_or_complete"
    all_skills_decided = not pending_or_revision_skills and not missing and not extra
    apply_allowed = decisions_valid and regression_clear and all_skills_decided
    if not decisions_valid:
        issues.append({"scope": "decisions_qa", "issue": "decisions_qa_not_valid", "status": decisions_qa.get("status")})
    if not regression_clear:
        issues.append({"scope": "regression", "issue": "regression_qa_not_clear"})
    if pending_or_revision_skills:
        issues.append({"scope": "human_review", "issue": "skills_not_final_decision", "skills": pending_or_revision_skills})

    actions: dict[str, dict[str, Any]] = {}
    created_at = utc_now()
    for skill in SKILLS:
        manifest_path = skills_root / skill / "skill_manifest.json"
        manifest = load_manifest(manifest_path)
        decision_info = skill_decisions.get(skill) or {}
        decision = decision_info.get("decision")
        if decision in APPROVAL_DECISIONS:
            runtime_status = "controlled_runtime_pilot_ready"
        elif decision == "block":
            runtime_status = "blocked_by_human_review"
        else:
            runtime_status = "pending_human_review"
        h6_status = {
            "updated_at": created_at,
            "runtime_status": runtime_status,
            "human_review_decision": decision,
            "human_review_decisions_file": str(decisions_path),
            "decisions_qa": str(decisions_qa_path),
            "pilot_qa": str(pilot_qa_path),
            "h3_runtime_smoke_qa": str(h3_qa_path),
            "h5_e2e_eval_qa": str(h5_e2e_qa_path),
            "h5_llm_response_eval_qa": str(h5_llm_qa_path),
            "reviewer": decision_info.get("reviewer", ""),
            "reviewed_at": decision_info.get("reviewed_at", ""),
            "required_edits": decision_info.get("required_edits", []),
            "boundary_issues": decision_info.get("boundary_issues", []),
            "retrieval_policy_issues": decision_info.get("retrieval_policy_issues", []),
            "reference_doc_issues": decision_info.get("reference_doc_issues", []),
            "expert_manual_review": expert_manual_review,
        }
        should_write = bool(args.apply and apply_allowed)
        if should_write:
            manifest["h6_runtime_status"] = h6_status
            write_json(manifest_path, manifest)
        actions[skill] = {
            "manifest": str(manifest_path),
            "decision": decision,
            "target_runtime_status": runtime_status,
            "written": should_write,
        }

    report = {
        "schema_version": "theraskill_h6_runtime_status_apply_report_v1",
        "created_at": created_at,
        "apply_requested": bool(args.apply),
        "apply_allowed": apply_allowed,
        "written_count": sum(1 for action in actions.values() if action["written"]),
        "approved_skills": approved_skills,
        "blocked_skills": blocked_skills,
        "pending_or_revision_skills": pending_or_revision_skills,
        "decisions_valid": decisions_valid,
        "regression_clear": regression_clear,
        "all_skills_decided": all_skills_decided,
        "issues_count": len(issues),
        "issues": issues,
        "actions": actions,
        "scope_note": "Dry-run unless --apply is passed. The script never approves skills; it only applies explicit human-review decisions when gate checks pass.",
    }
    write_json(report_path, report)
    print(json.dumps({
        "report": str(report_path),
        "apply_requested": bool(args.apply),
        "apply_allowed": apply_allowed,
        "written_count": report["written_count"],
        "issues_count": len(issues),
        "pending_or_revision_skills": pending_or_revision_skills,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
