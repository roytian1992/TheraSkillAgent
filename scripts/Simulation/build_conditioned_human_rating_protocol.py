#!/usr/bin/env python3
"""Build external adjudication protocol and rating sheet templates for conditioned residual ties."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import file_sha256, read_json, write_json, write_text
from simulation_surface_registry import (
    BUILD_PATIENT_SIMULATOR_EVALUATION_PACKAGE_RELPATH,
    COMPILE_CONDITIONED_HUMAN_RATING_RESULTS_RELPATH,
    FORMAL_CONDITIONED_EVAL_ADJUDICATION_DIR,
    RUN_CONDITIONED_HUMAN_RATING_REFRESH_RELPATH,
    SYNC_CONDITIONED_HUMAN_RATING_SHEET_RELPATH,
    VALIDATE_CONDITIONED_HUMAN_RATING_SHEET_RELPATH,
)
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


def render_protocol(packet_paths: list[dict[str, Any]]) -> str:
    refresh_cmd = f"python {RUN_CONDITIONED_HUMAN_RATING_REFRESH_RELPATH}"
    sync_cmd = f"python {SYNC_CONDITIONED_HUMAN_RATING_SHEET_RELPATH}"
    compile_cmd = f"python {COMPILE_CONDITIONED_HUMAN_RATING_RESULTS_RELPATH}"
    validate_cmd = f"python {VALIDATE_CONDITIONED_HUMAN_RATING_SHEET_RELPATH}"
    package_cmd = f"python {BUILD_PATIENT_SIMULATOR_EVALUATION_PACKAGE_RELPATH}"
    lines: list[str] = []
    lines.append("# Conditioned Residual Human Rating Protocol")
    lines.append("")
    lines.append(f"- Created at: `{utc_now()}`")
    lines.append(f"- Packet count: `{len(packet_paths)}`")
    lines.append("- Purpose: determine whether the remaining conditioned residuals represent")
    lines.append("  - a true patient-simulator/runtime defect,")
    lines.append("  - a comparison-heuristic resolution limit,")
    lines.append("  - or an unresolved mixed case that should continue blocking stronger claims.")
    lines.append("")
    lines.append("## Materials")
    lines.append("")
    lines.append("Review each packet together with its linked JSON/MD artifact. Each packet already contains:")
    lines.append("- failed heuristic check evidence")
    lines.append("- scenario-level expectation alignment")
    lines.append("- supportive / rupture_prone / poor / overly_directive arm snapshots")
    lines.append("- transcript excerpts for all four arms")
    lines.append("")
    lines.append("## Execution Order")
    lines.append("")
    lines.append("Recommended workflow after a reviewer edits the CSV sheet:")
    lines.append("")
    lines.append("Preferred one-command entry:")
    lines.append("")
    lines.append(f"- `{refresh_cmd} --overwrite`")
    lines.append("")
    lines.append("Equivalent step-by-step sequence:")
    lines.append("")
    lines.append("1. Fill:")
    lines.append("   - `human_rating_sheet_template.csv`")
    lines.append("2. Sync CSV into canonical JSON:")
    lines.append(
        f"   - `{sync_cmd}`"
    )
    lines.append("3. Compile adjudication results:")
    lines.append(
        f"   - `{compile_cmd}`"
    )
    lines.append("4. Validate sheet readiness / field integrity:")
    lines.append(
        f"   - `{validate_cmd}`"
    )
    lines.append("5. Refresh the unified evaluation package:")
    lines.append(
        f"   - `{package_cmd}`"
    )
    lines.append("")
    lines.append("Note:")
    lines.append("- `human_rating_sheet_template.json` is a canonical machine-readable mirror of the CSV and should be treated as derived output.")
    lines.append("")
    lines.append("## Reviewer Task")
    lines.append("")
    lines.append("For each packet, answer all four fields below.")
    lines.append("")
    lines.append("### 1. Contrast Salience")
    lines.append("")
    lines.append("How clinically noticeable is the supportive-vs-rupture contrast in the packet?")
    lines.append("")
    lines.append("- `1`: no meaningful contrast visible")
    lines.append("- `2`: weak contrast")
    lines.append("- `3`: moderate but arguable contrast")
    lines.append("- `4`: clear contrast")
    lines.append("- `5`: very strong contrast")
    lines.append("")
    lines.append("### 2. Residual Type")
    lines.append("")
    lines.append("Choose one:")
    lines.append("")
    lines.append("- `heuristic_resolution_limit`: reviewer/state/transcript evidence looks acceptable; the tie is mainly a comparison-heuristic blind spot")
    lines.append("- `mixed_but_nonblocking`: some ambiguity remains, but the residual should not block the current baseline claim")
    lines.append("- `runtime_or_prompt_defect`: evidence suggests the patient runtime or conditioned prompting is still not behaving correctly")
    lines.append("- `insufficient_evidence`: packet is not enough to make a reliable judgment")
    lines.append("")
    lines.append("### 3. Blocking Judgment")
    lines.append("")
    lines.append("Choose one:")
    lines.append("")
    lines.append("- `non_blocking`: should not block the current authoritative conditioned baseline")
    lines.append("- `blocking`: should continue to block stronger conditioned claims until fixed")
    lines.append("- `uncertain`: needs more material or another reviewer")
    lines.append("")
    lines.append("### 4. Rationale")
    lines.append("")
    lines.append("Write a short justification grounded in the packet. Prefer evidence from:")
    lines.append("- transcript contrast")
    lines.append("- review gate")
    lines.append("- final state differences")
    lines.append("- expectation-alignment context")
    lines.append("")
    lines.append("## Decision Rule")
    lines.append("")
    lines.append("Recommended minimal decision rule for the current promoted baseline:")
    lines.append("")
    lines.append("- treat a packet as `adjudicated_non_blocking` if")
    lines.append("  - blocking_judgment = `non_blocking`, and")
    lines.append("  - residual_type is `heuristic_resolution_limit` or `mixed_but_nonblocking`")
    lines.append("- treat a packet as `adjudicated_blocking` if")
    lines.append("  - blocking_judgment = `blocking`, or")
    lines.append("  - residual_type = `runtime_or_prompt_defect`")
    lines.append("- otherwise leave it `needs_more_review`")
    lines.append("")
    lines.append("## Current Packet Registry")
    lines.append("")
    for row in packet_paths:
        lines.append(f"- `{row['scenario_id']}`")
        lines.append(f"  - packet json: `{row['json_path']}`")
        lines.append(f"  - packet md: `{row['markdown_path']}`")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    adjudication_dir = Path(args.adjudication_dir)
    summary_path = adjudication_dir / "adjudication_summary.json"
    summary = read_json(summary_path)
    packet_paths = summary.get("packet_paths") or []

    protocol_md_path = adjudication_dir / "human_rating_protocol.md"
    sheet_csv_path = adjudication_dir / "human_rating_sheet_template.csv"
    sheet_json_path = adjudication_dir / "human_rating_sheet_template.json"
    manifest_path = adjudication_dir / "human_rating_protocol_manifest.json"

    if not args.overwrite:
        for path in [protocol_md_path, sheet_csv_path, sheet_json_path, manifest_path]:
            if path.exists():
                raise FileExistsError(f"{path} already exists; pass --overwrite to replace it")

    write_text(protocol_md_path, render_protocol(packet_paths).rstrip() + "\n")

    csv_rows: list[dict[str, Any]] = []
    json_rows: list[dict[str, Any]] = []
    for row in packet_paths:
        packet = read_json(row["json_path"])
        base_row = {
            "packet_id": packet.get("packet_id"),
            "scenario_id": row["scenario_id"],
            "scenario_family": ((packet.get("scenario") or {}).get("scenario_family")),
            "comparison_gate": ((packet.get("comparison_context") or {}).get("comparison_gate")),
            "expectation_alignment_gate": ((packet.get("expectation_alignment") or {}).get("gate_status")),
            "reviewer_id": "",
            "reviewed_at": "",
            "contrast_salience_1_to_5": "",
            "residual_type": "",
            "blocking_judgment": "",
            "adjudication_status": "",
            "rationale": "",
        }
        csv_rows.append(base_row)
        json_rows.append(base_row)

    with sheet_csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()) if csv_rows else [])
        if csv_rows:
            writer.writeheader()
            writer.writerows(csv_rows)

    write_json(
        sheet_json_path,
        {
            "schema_version": "theraskill_conditioned_human_rating_sheet_template_v1",
            "created_at": utc_now(),
            "rows": json_rows,
        },
    )

    manifest = {
        "schema_version": "theraskill_conditioned_human_rating_protocol_manifest_v1",
        "created_at": utc_now(),
        "status": "complete",
        "adjudication_summary": {
            "path": str(summary_path.resolve()),
            "sha256": file_sha256(summary_path),
        },
        "packet_count": len(packet_paths),
        "artifacts": {
            "protocol_md": {
                "path": str(protocol_md_path.resolve()),
                "sha256": file_sha256(protocol_md_path),
            },
            "sheet_csv_template": {
                "path": str(sheet_csv_path.resolve()),
                "sha256": file_sha256(sheet_csv_path),
            },
            "sheet_json_template": {
                "path": str(sheet_json_path.resolve()),
                "sha256": file_sha256(sheet_json_path),
            },
        },
    }
    write_json(manifest_path, manifest)


if __name__ == "__main__":
    main()
