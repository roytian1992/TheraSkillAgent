#!/usr/bin/env python3
"""Compile conditioned human-rating sheet rows into adjudication results summaries."""

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

from io_utils import file_sha256, read_json, write_json, write_text
from simulation_surface_registry import FORMAL_CONDITIONED_EVAL_ADJUDICATION_DIR
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ADJUDICATION_DIR = FORMAL_CONDITIONED_EVAL_ADJUDICATION_DIR


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adjudication-dir", default=str(DEFAULT_ADJUDICATION_DIR))
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()

def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def infer_adjudication_status(row: dict[str, Any]) -> str:
    explicit = clean_text(row.get("adjudication_status"))
    if explicit:
        return explicit
    blocking = clean_text(row.get("blocking_judgment"))
    residual_type = clean_text(row.get("residual_type"))
    if not any([blocking, residual_type, clean_text(row.get("rationale")), clean_text(row.get("reviewer_id"))]):
        return "needs_review"
    if blocking == "non_blocking" and residual_type in {"heuristic_resolution_limit", "mixed_but_nonblocking"}:
        return "adjudicated_non_blocking"
    if blocking == "blocking" or residual_type == "runtime_or_prompt_defect":
        return "adjudicated_blocking"
    if blocking == "uncertain" or residual_type == "insufficient_evidence":
        return "needs_more_review"
    return "invalid_or_incomplete"


def render_markdown(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Conditioned Human Rating Results Summary")
    lines.append("")
    lines.append(f"- Created at: `{summary['created_at']}`")
    lines.append(f"- Status: `{summary['status']}`")
    lines.append(f"- Total rows: `{summary['counts']['total_rows']}`")
    lines.append(f"- Reviewed rows: `{summary['counts']['reviewed_rows']}`")
    lines.append(f"- Fully adjudicated rows: `{summary['counts']['fully_adjudicated_rows']}`")
    lines.append("")
    lines.append("## Status Distribution")
    lines.append("")
    for key, value in summary["distributions"]["adjudication_status"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    lines.append("## Blocking Distribution")
    lines.append("")
    for key, value in summary["distributions"]["blocking_judgment"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    lines.append("## Residual Type Distribution")
    lines.append("")
    for key, value in summary["distributions"]["residual_type"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    lines.append("## Rows")
    lines.append("")
    for row in summary["rows"]:
        lines.append(f"- `{row['scenario_id']}`")
        lines.append(f"  - adjudication_status: `{row['adjudication_status']}`")
        lines.append(f"  - reviewer_id: `{row['reviewer_id']}`")
        lines.append(f"  - blocking_judgment: `{row['blocking_judgment']}`")
        lines.append(f"  - residual_type: `{row['residual_type']}`")
        lines.append(f"  - contrast_salience_1_to_5: `{row['contrast_salience_1_to_5']}`")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    adjudication_dir = Path(args.adjudication_dir)
    sheet_path = adjudication_dir / "human_rating_sheet_template.json"
    protocol_manifest_path = adjudication_dir / "human_rating_protocol_manifest.json"
    adjudication_summary_path = adjudication_dir / "adjudication_summary.json"

    result_summary_path = adjudication_dir / "human_rating_results_summary.json"
    result_md_path = adjudication_dir / "human_rating_results_summary.md"
    result_manifest_path = adjudication_dir / "human_rating_results_manifest.json"

    if not args.overwrite:
        for path in [result_summary_path, result_md_path, result_manifest_path]:
            if path.exists():
                raise FileExistsError(f"{path} already exists; pass --overwrite to replace it")

    sheet = read_json(sheet_path)
    adjudication_summary = read_json(adjudication_summary_path)
    protocol_manifest = read_json(protocol_manifest_path)
    rows = sheet.get("rows") or []

    normalized_rows: list[dict[str, Any]] = []
    status_counter: Counter[str] = Counter()
    blocking_counter: Counter[str] = Counter()
    residual_type_counter: Counter[str] = Counter()

    reviewed_rows = 0
    fully_adjudicated_rows = 0
    for row in rows:
        normalized = {
            "packet_id": clean_text(row.get("packet_id")),
            "scenario_id": clean_text(row.get("scenario_id")),
            "scenario_family": clean_text(row.get("scenario_family")),
            "comparison_gate": clean_text(row.get("comparison_gate")),
            "expectation_alignment_gate": clean_text(row.get("expectation_alignment_gate")),
            "reviewer_id": clean_text(row.get("reviewer_id")),
            "reviewed_at": clean_text(row.get("reviewed_at")),
            "contrast_salience_1_to_5": clean_text(row.get("contrast_salience_1_to_5")),
            "residual_type": clean_text(row.get("residual_type")),
            "blocking_judgment": clean_text(row.get("blocking_judgment")),
            "adjudication_status": infer_adjudication_status(row),
            "rationale": clean_text(row.get("rationale")),
        }
        if any(
            [
                normalized["reviewer_id"],
                normalized["reviewed_at"],
                normalized["contrast_salience_1_to_5"],
                normalized["residual_type"],
                normalized["blocking_judgment"],
                normalized["rationale"],
            ]
        ):
            reviewed_rows += 1
        if normalized["adjudication_status"] in {"adjudicated_non_blocking", "adjudicated_blocking"}:
            fully_adjudicated_rows += 1
        normalized_rows.append(normalized)
        status_counter[normalized["adjudication_status"]] += 1
        blocking_counter[normalized["blocking_judgment"] or "blank"] += 1
        residual_type_counter[normalized["residual_type"] or "blank"] += 1

    result_summary = {
        "schema_version": "theraskill_conditioned_human_rating_results_summary_v1",
        "created_at": utc_now(),
        "status": "complete",
        "counts": {
            "total_rows": len(normalized_rows),
            "reviewed_rows": reviewed_rows,
            "fully_adjudicated_rows": fully_adjudicated_rows,
        },
        "distributions": {
            "adjudication_status": dict(status_counter),
            "blocking_judgment": dict(blocking_counter),
            "residual_type": dict(residual_type_counter),
        },
        "rows": normalized_rows,
        "sources": {
            "sheet_path": str(sheet_path.resolve()),
            "sheet_sha256": file_sha256(sheet_path),
            "adjudication_summary_path": str(adjudication_summary_path.resolve()),
            "adjudication_summary_sha256": file_sha256(adjudication_summary_path),
            "protocol_manifest_path": str(protocol_manifest_path.resolve()),
            "protocol_manifest_sha256": file_sha256(protocol_manifest_path),
        },
    }
    write_json(result_summary_path, result_summary)
    write_text(result_md_path, render_markdown(result_summary).rstrip() + "\n")

    manifest = {
        "schema_version": "theraskill_conditioned_human_rating_results_manifest_v1",
        "created_at": utc_now(),
        "status": "complete",
        "packet_count": adjudication_summary.get("packet_count"),
        "artifacts": {
            "results_summary_json": {
                "path": str(result_summary_path.resolve()),
                "sha256": file_sha256(result_summary_path),
            },
            "results_summary_md": {
                "path": str(result_md_path.resolve()),
                "sha256": file_sha256(result_md_path),
            },
        },
        "inputs": {
            "sheet": {
                "path": str(sheet_path.resolve()),
                "sha256": file_sha256(sheet_path),
            },
            "protocol_manifest": {
                "path": str(protocol_manifest_path.resolve()),
                "sha256": file_sha256(protocol_manifest_path),
            },
        },
    }
    write_json(result_manifest_path, manifest)


if __name__ == "__main__":
    main()
