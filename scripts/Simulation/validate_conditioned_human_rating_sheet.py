#!/usr/bin/env python3
"""Validate conditioned human-rating sheet rows and emit a readiness report."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import read_json, write_json, write_text
from simulation_surface_registry import FORMAL_CONDITIONED_EVAL_ADJUDICATION_DIR
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ADJUDICATION_DIR = FORMAL_CONDITIONED_EVAL_ADJUDICATION_DIR

ALLOWED_RESIDUAL_TYPES = {
    "",
    "heuristic_resolution_limit",
    "mixed_but_nonblocking",
    "runtime_or_prompt_defect",
    "insufficient_evidence",
}
ALLOWED_BLOCKING_JUDGMENTS = {"", "non_blocking", "blocking", "uncertain"}
ALLOWED_ADJUDICATION_STATUS = {
    "",
    "needs_review",
    "needs_more_review",
    "adjudicated_non_blocking",
    "adjudicated_blocking",
    "invalid_or_incomplete",
}
ALLOWED_SALIENCE = {"", "1", "2", "3", "4", "5"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adjudication-dir", default=str(DEFAULT_ADJUDICATION_DIR))
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()

def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Conditioned Human Rating Validation Report")
    lines.append("")
    lines.append(f"- Created at: `{report['created_at']}`")
    lines.append(f"- Status: `{report['status']}`")
    lines.append(f"- Total rows: `{report['counts']['total_rows']}`")
    lines.append(f"- Ready rows: `{report['counts']['ready_rows']}`")
    lines.append(f"- Blank rows: `{report['counts']['blank_rows']}`")
    lines.append(f"- Invalid rows: `{report['counts']['invalid_rows']}`")
    lines.append("")
    lines.append("## Validation Issues")
    lines.append("")
    for row in report["rows"]:
        if not row["issues"]:
            continue
        lines.append(f"- `{row['scenario_id']}`")
        for issue in row["issues"]:
            lines.append(f"  - {issue}")
    if not any(row["issues"] for row in report["rows"]):
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    adjudication_dir = Path(args.adjudication_dir)
    sheet_path = adjudication_dir / "human_rating_sheet_template.json"
    results_summary_path = adjudication_dir / "human_rating_results_summary.json"
    report_json_path = adjudication_dir / "human_rating_validation_report.json"
    report_md_path = adjudication_dir / "human_rating_validation_report.md"

    if not args.overwrite:
        for path in [report_json_path, report_md_path]:
            if path.exists():
                raise FileExistsError(f"{path} already exists; pass --overwrite to replace it")

    sheet = read_json(sheet_path)
    results_summary = read_json(results_summary_path)
    rows = sheet.get("rows") or []

    ready_rows = 0
    blank_rows = 0
    invalid_rows = 0
    issue_counter: Counter[str] = Counter()
    normalized_rows: list[dict[str, Any]] = []

    for row in rows:
        scenario_id = clean_text(row.get("scenario_id"))
        residual_type = clean_text(row.get("residual_type"))
        blocking_judgment = clean_text(row.get("blocking_judgment"))
        adjudication_status = clean_text(row.get("adjudication_status"))
        salience = clean_text(row.get("contrast_salience_1_to_5"))
        reviewer_id = clean_text(row.get("reviewer_id"))
        rationale = clean_text(row.get("rationale"))
        issues: list[str] = []

        if residual_type not in ALLOWED_RESIDUAL_TYPES:
            issues.append(f"invalid residual_type: {residual_type}")
            issue_counter["invalid_residual_type"] += 1
        if blocking_judgment not in ALLOWED_BLOCKING_JUDGMENTS:
            issues.append(f"invalid blocking_judgment: {blocking_judgment}")
            issue_counter["invalid_blocking_judgment"] += 1
        if adjudication_status not in ALLOWED_ADJUDICATION_STATUS:
            issues.append(f"invalid adjudication_status: {adjudication_status}")
            issue_counter["invalid_adjudication_status"] += 1
        if salience not in ALLOWED_SALIENCE:
            issues.append(f"invalid contrast_salience_1_to_5: {salience}")
            issue_counter["invalid_salience"] += 1

        filled_any = any([residual_type, blocking_judgment, adjudication_status, salience, reviewer_id, rationale])
        fully_ready = all([residual_type, blocking_judgment, reviewer_id, rationale])

        if not filled_any:
            blank_rows += 1
        elif issues:
            invalid_rows += 1
        elif fully_ready:
            ready_rows += 1
        else:
            issues.append("partially filled row: rating started but required fields are still missing")
            issue_counter["partial_row"] += 1

        normalized_rows.append(
            {
                "scenario_id": scenario_id,
                "reviewer_id": reviewer_id,
                "contrast_salience_1_to_5": salience,
                "residual_type": residual_type,
                "blocking_judgment": blocking_judgment,
                "adjudication_status": adjudication_status,
                "issues": issues,
            }
        )

    report = {
        "schema_version": "theraskill_conditioned_human_rating_validation_report_v1",
        "created_at": utc_now(),
        "status": "complete",
        "counts": {
            "total_rows": len(rows),
            "ready_rows": ready_rows,
            "blank_rows": blank_rows,
            "invalid_rows": invalid_rows,
        },
        "distributions": {
            "issue_counter": dict(issue_counter),
            "compiled_status_distribution": (results_summary.get("distributions") or {}).get("adjudication_status") or {},
        },
        "rows": normalized_rows,
        "sources": {
            "sheet_path": str(sheet_path.resolve()),
            "results_summary_path": str(results_summary_path.resolve()),
        },
    }
    write_json(report_json_path, report)
    write_text(report_md_path, render_markdown(report).rstrip() + "\n")


if __name__ == "__main__":
    main()
