#!/usr/bin/env python3
"""Build candidate-id shard files for Simulation.patient_state_transition_pattern extraction."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import read_jsonl, write_json
from simulation_surface_registry import SIMULATION_EXTRACTION_FAMILY_SPECS

DEFAULT_SPEC = SIMULATION_EXTRACTION_FAMILY_SPECS["patient_state_transition_pattern"]
DEFAULT_CANDIDATES = DEFAULT_SPEC.candidates_jsonl
DEFAULT_OUTPUT_DIR = DEFAULT_SPEC.shard_dir
DEFAULT_OUTPUT_UNITS = DEFAULT_SPEC.canonical_output
DEFAULT_FAILURE_LOG = DEFAULT_SPEC.failure_log


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", default=DEFAULT_CANDIDATES)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--output-units", default=DEFAULT_OUTPUT_UNITS)
    parser.add_argument("--failure-log", default=DEFAULT_FAILURE_LOG)
    parser.add_argument("--shards", type=int, default=32)
    parser.add_argument("--exclude-completed", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    candidates = read_jsonl(Path(args.candidates))
    completed = set()

    if args.exclude_completed:
        for row in read_jsonl(Path(args.output_units)):
            cid = str((row.get("source") or {}).get("candidate_id") or "")
            if cid:
                completed.add(cid)
        for row in read_jsonl(Path(args.failure_log)):
            cid = str(row.get("candidate_id") or "")
            if cid:
                completed.add(cid)

    candidate_ids = [str(row.get("candidate_id")) for row in candidates if str(row.get("candidate_id"))]
    if completed:
        candidate_ids = [cid for cid in candidate_ids if cid not in completed]

    shards = [[] for _ in range(max(1, args.shards))]
    for idx, cid in enumerate(candidate_ids):
        shards[idx % len(shards)].append(cid)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for path in output_dir.glob("candidate_ids.shard_*.txt"):
        path.unlink()

    shard_counts = {}
    for idx, items in enumerate(shards):
        shard_name = f"shard_{idx:02d}"
        shard_counts[shard_name] = len(items)
        (output_dir / f"candidate_ids.{shard_name}.txt").write_text(
            "".join(f"{item}\n" for item in items),
            encoding="utf-8",
        )

    manifest = {
        "schema_version": "theraskill_patient_state_transition_shards_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "candidate_count_total": len(candidates),
        "excluded_completed_count": len(completed) if args.exclude_completed else 0,
        "remaining_candidate_count": len(candidate_ids),
        "shards": len(shards),
        "shard_counts": shard_counts,
        "output_dir": str(output_dir),
    }
    write_json(output_dir / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
