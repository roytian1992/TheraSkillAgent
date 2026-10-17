#!/usr/bin/env python3
"""Build full CPsyCounD v2 extraction inputs and case-preserving shards."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
for path in (SCRIPT_DIR, COMMON_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from build_skill_extraction_pilot import (  # noqa: E402
    TARGETS,
    build_windows,
    dialogue_turns,
    load_jsonl,
    summarize_case,
    write_jsonl,
)
from cpsycound_surface_registry import CPSYCOUND_FULL_ROOT, UNIFIED_DIALOGUES_PATH  # noqa: E402


DATASET = "CPsyCounD"


def shard_cases(
    cases: list[dict[str, Any]],
    windows_by_case: dict[str, list[dict[str, Any]]],
    shard_count: int,
) -> list[list[dict[str, Any]]]:
    shards: list[list[dict[str, Any]]] = [[] for _ in range(shard_count)]
    shard_loads = [0 for _ in range(shard_count)]
    ordered = sorted(
        cases,
        key=lambda case: (
            -len(windows_by_case[case["pilot_case_id"]]),
            str(case["source_id"]),
        ),
    )
    for case in ordered:
        shard_idx = min(range(shard_count), key=lambda idx: (shard_loads[idx], idx))
        shards[shard_idx].append(case)
        shard_loads[shard_idx] += len(windows_by_case[case["pilot_case_id"]])
    for shard in shards:
        shard.sort(key=lambda case: str(case["source_id"]))
    return shards


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(UNIFIED_DIALOGUES_PATH))
    parser.add_argument("--out-dir", default=str(CPSYCOUND_FULL_ROOT))
    parser.add_argument("--shards", type=int, default=8)
    args = parser.parse_args()

    if args.shards < 1:
        raise ValueError("--shards must be >= 1")

    input_path = Path(args.input)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    spec = TARGETS[DATASET]
    scanned = 0
    dataset_records = 0
    no_window_records: list[str] = []
    cases: list[dict[str, Any]] = []
    windows: list[dict[str, Any]] = []
    windows_by_case: dict[str, list[dict[str, Any]]] = {}

    for record in load_jsonl(input_path):
        scanned += 1
        if record.get("dataset") != DATASET:
            continue
        dataset_records += 1
        summary = summarize_case(record, spec)
        record_windows = build_windows(record, summary)
        if not record_windows:
            no_window_records.append(str(record.get("source_id")))
            continue
        cases.append(summary)
        windows.extend(record_windows)
        windows_by_case[summary["pilot_case_id"]] = record_windows

    cases.sort(key=lambda case: str(case["source_id"]))
    windows.sort(key=lambda row: (str(row["source_id"]), row["center_counselor_turn_id"]))

    cases_path = out_dir / "cpsycound_cases.jsonl"
    windows_path = out_dir / "cpsycound_evidence_windows.jsonl"
    shards_dir = out_dir / "shards"
    shards_dir.mkdir(parents=True, exist_ok=True)

    write_jsonl(cases_path, cases)
    write_jsonl(windows_path, windows)

    shard_infos = []
    shards = shard_cases(cases, windows_by_case, args.shards)
    for idx, shard_cases_rows in enumerate(shards):
        shard_id = f"shard_{idx:02d}"
        shard_dir = shards_dir / shard_id
        shard_dir.mkdir(parents=True, exist_ok=True)
        shard_case_ids = {case["pilot_case_id"] for case in shard_cases_rows}
        shard_windows = [row for row in windows if row["pilot_case_id"] in shard_case_ids]
        shard_cases_path = shard_dir / "cases.jsonl"
        shard_windows_path = shard_dir / "evidence_windows.jsonl"
        write_jsonl(shard_cases_path, shard_cases_rows)
        write_jsonl(shard_windows_path, shard_windows)
        shard_infos.append(
            {
                "shard_id": shard_id,
                "cases": len(shard_cases_rows),
                "windows": len(shard_windows),
                "cases_path": str(shard_cases_path),
                "evidence_windows_path": str(shard_windows_path),
            }
        )

    turn_counts = Counter(case["length_bucket"] for case in cases)
    window_counts_per_case = [len(windows_by_case[case["pilot_case_id"]]) for case in cases]
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path),
        "dataset": DATASET,
        "output_dir": str(out_dir),
        "construction_policy": {
            "case_policy": "include every CPsyCounD record that yields at least one counselor-centered evidence window",
            "window_policy": "one window per counselor turn with previous client turn and optional next client response",
            "sharding_policy": "case-preserving greedy balance by evidence-window count",
            "lexical_risk_hint_policy": "retained only as runner-managed metadata; not a clinical label and not sent as prompt evidence by the staged runner",
        },
        "counts": {
            "input_rows_scanned": scanned,
            "cpsycound_records_seen": dataset_records,
            "extractable_cases": len(cases),
            "records_without_evidence_windows": len(no_window_records),
            "evidence_windows": len(windows),
            "length_buckets": dict(turn_counts),
            "min_windows_per_case": min(window_counts_per_case) if window_counts_per_case else 0,
            "max_windows_per_case": max(window_counts_per_case) if window_counts_per_case else 0,
            "avg_windows_per_case": round(sum(window_counts_per_case) / len(window_counts_per_case), 3)
            if window_counts_per_case
            else 0,
        },
        "files": {
            "cases": str(cases_path),
            "evidence_windows": str(windows_path),
            "shards_dir": str(shards_dir),
        },
        "shards": shard_infos,
        "excluded_source_ids_sample": no_window_records[:50],
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    readme_lines = [
        "# CPsyCounD Full v2 Extraction Inputs",
        "",
        f"- Created UTC: `{manifest['created_at']}`",
        f"- Input: `{input_path}`",
        f"- Cases: `{cases_path}`",
        f"- Evidence windows: `{windows_path}`",
        f"- Manifest: `{manifest_path}`",
        f"- Shards: `{shards_dir}`",
        "",
        "## Counts",
        "",
        f"- CPsyCounD records seen: `{dataset_records}`",
        f"- Extractable cases: `{len(cases)}`",
        f"- Records without evidence windows: `{len(no_window_records)}`",
        f"- Evidence windows: `{len(windows)}`",
        "",
        "## Notes",
        "",
        "- Shards preserve case integrity so case-trajectory prompts see all windows available for each selected case.",
        "- Lexical risk hints are retained only as metadata and must not be treated as clinical labels.",
    ]
    (out_dir / "README.md").write_text("\n".join(readme_lines) + "\n", encoding="utf-8")

    print(json.dumps(manifest["counts"], ensure_ascii=False, indent=2))
    print(json.dumps(shard_infos, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
