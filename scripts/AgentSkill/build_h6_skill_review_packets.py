#!/usr/bin/env python3
"""Build per-skill H6 review packets from pilot evidence."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import ensure_output_path, read_json, write_json, write_text
from hermes_surface_registry import (
    H6_DECISIONS_PATH,
    H6_ENGINEERING_RUNTIME_PILOT_REPORT_PATH,
    H6_PACKET_DIR,
    H6_PACKET_INDEX_PATH,
    OUTPUT_ROOT,
)
from retrieval_wrapper_common import SKILL_PACKAGE_RUNTIME_SCRIPT

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PILOT_REPORT = H6_ENGINEERING_RUNTIME_PILOT_REPORT_PATH
DEFAULT_DECISIONS = H6_DECISIONS_PATH
DEFAULT_OUTPUT_DIR = H6_PACKET_DIR
DEFAULT_INDEX = H6_PACKET_INDEX_PATH

SKILL_FOCUS = {
    "emotional-support": [
        "Empathy and reflection before suggestions.",
        "No diagnosis, medication advice, or treatment-plan claims.",
        "Self-harm / suicide / imminent danger language should route to risk-triage.",
    ],
    "risk-triage": [
        "Recognize suicide/self-harm/crisis language without final clinical risk scores.",
        "Avoid diagnosis and overconfident safety claims.",
        "Leave local emergency-resource wording to downstream product safety policy.",
    ],
    "cognitive-reframing-support": [
        "Use tentative cognitive-pattern language.",
        "Validate emotion before reframing.",
        "Avoid forced positivity; alternatives should be plausible.",
    ],
    "synthetic-dialogue-builder": [
        "Clearly label synthetic / training material.",
        "Separate rationale from final dialogue.",
        "Never treat construction knowledge as observed clinical evidence.",
    ],
    "therapy-response-reviewer": [
        "Separate observed dialogue, generated rationale, and construction notes.",
        "Review stage fit, strategy fit, empathy, and safety boundaries.",
        "Give concrete revision suggestions without certifying clinical safety.",
    ],
    "rebt-belief-work-support": [
        "Separate ABC components.",
        "Use disputation gently and respectfully.",
        "Route crisis language to risk-triage before belief work.",
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-report", default=str(DEFAULT_PILOT_REPORT))
    parser.add_argument("--decisions", default=str(DEFAULT_DECISIONS))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--index", default=str(DEFAULT_INDEX))
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def rel(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def build_packet(skill: str, results: list[dict[str, Any]], decision: dict[str, Any], created_at: str) -> str:
    skill_root = OUTPUT_ROOT / skill
    lines = [
        f"# H6 Review Packet: {skill}",
        "",
        f"Created at: `{created_at}`",
        "",
        "## Purpose",
        "",
        "This packet gathers the files, pilot cases, and retrieved evidence summaries a human reviewer needs for H6.",
        "It is not an approval decision.",
        "",
        "## Current Decision State",
        "",
        f"- Decision: `{decision.get('decision', 'missing')}`",
        f"- Reviewer: `{decision.get('reviewer', '')}`",
        f"- Reviewed at: `{decision.get('reviewed_at', '')}`",
        f"- Requires H3 rerun: `{decision.get('requires_h3_rerun', False)}`",
        f"- Requires H5 rerun: `{decision.get('requires_h5_rerun', False)}`",
        "",
        "## Files To Review",
        "",
        f"- `{rel(skill_root / 'SKILL.md')}`",
        f"- `{rel(skill_root / 'references/boundaries.md')}`",
        f"- `{rel(skill_root / 'references/examples.md')}`",
        f"- `{rel(skill_root / 'references/review-rubric.md')}`",
        f"- `{rel(skill_root / 'references/source-policy.md')}`",
        f"- `{rel(skill_root / 'scripts' / SKILL_PACKAGE_RUNTIME_SCRIPT)}`",
        "",
        "## Skill-Specific Focus",
        "",
    ]
    for item in SKILL_FOCUS.get(skill, []):
        lines.append(f"- [ ] {item}")
    lines.extend(["", "## Pilot Cases", ""])
    for result in results:
        families = ", ".join(result.get("families") or [])
        labels = ", ".join(result.get("labels") or [])
        release_ids = ", ".join(str(x) for x in (result.get("release_unit_ids") or [])[:8])
        lines.extend([
            f"### {result['case_id']}",
            "",
            f"- Case type: `{result.get('case_type')}`",
            f"- Query: {result.get('query')}",
            f"- Retrieval passed: `{result.get('passed')}`",
            f"- Hits: `{result.get('hits')}`",
            f"- Families: `{families}`",
            f"- Labels: `{labels}`",
            f"- Release unit IDs: `{release_ids}`",
            f"- Expected checks: `{', '.join(result.get('expected_checks') or [])}`",
        ])
        if result.get("expected_route_note"):
            lines.append(f"- Route note: {result['expected_route_note']}")
        lines.extend(["", "Evidence preview:", ""])
        for evidence in result.get("evidence_preview") or []:
            preview = (evidence.get("text_preview") or "").replace("\n", " ")
            if len(preview) > 500:
                preview = preview[:497] + "..."
            lines.extend([
                f"- `{evidence.get('release_unit_id')}` | `{evidence.get('dedup_family')}` | `{evidence.get('canonical_label')}` | `{evidence.get('primary_dataset')}`",
                f"  - {preview}",
            ])
        lines.append("")
    lines.extend([
        "## Reviewer Decision Fields",
        "",
        f"Update `{rel(H6_DECISIONS_PATH)}` after review.",
        "",
        "Allowed decisions:",
        "",
        "- `approve`",
        "- `approve_with_minor_edits`",
        "- `revise_and_rerun_qa`",
        "- `block`",
        "",
        "Reviewer notes:",
        "",
        "```text",
        "",
        "```",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    pilot_report_path = Path(args.pilot_report)
    decisions_path = Path(args.decisions)
    output_dir = Path(args.output_dir)
    index_path = Path(args.index)
    ensure_output_path(index_path, overwrite=args.overwrite)

    pilot_report = read_json(pilot_report_path)
    decisions = read_json(decisions_path)
    by_skill: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in pilot_report.get("results") or []:
        by_skill[result["skill"]].append(result)

    created_at = utc_now()
    packets: dict[str, dict[str, Any]] = {}
    for skill in sorted(by_skill):
        path = output_dir / f"{skill}.md"
        ensure_output_path(path, overwrite=args.overwrite)
        decision = (decisions.get("skills") or {}).get(skill, {})
        content = build_packet(skill, by_skill[skill], decision, created_at)
        write_text(path, content)
        packets[skill] = {
            "path": str(path),
            "case_count": len(by_skill[skill]),
            "decision": decision.get("decision", "missing"),
            "passed_count": sum(1 for result in by_skill[skill] if result.get("passed")),
            "failed_count": sum(1 for result in by_skill[skill] if not result.get("passed")),
        }

    write_json(index_path, {
        "schema_version": "theraskill_h6_skill_review_packets_index_v1",
        "created_at": created_at,
        "pilot_report": str(pilot_report_path),
        "human_review_decisions": str(decisions_path),
        "packet_count": len(packets),
        "packets": packets,
        "scope_note": "Per-skill review packets for human review. These are not approval decisions.",
    })
    print(json.dumps({
        "index": str(index_path),
        "packet_count": len(packets),
        "packets": packets,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
