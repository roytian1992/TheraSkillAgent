#!/usr/bin/env python3
"""Merge shard outputs for Simulation.patient_state_transition_pattern into canonical output."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from simulation_surface_registry import SIMULATION_EXTRACTION_FAMILY_SPECS

DEFAULT_SPEC = SIMULATION_EXTRACTION_FAMILY_SPECS["patient_state_transition_pattern"]
DEFAULT_SHARD_DIR = DEFAULT_SPEC.shard_dir
DEFAULT_OUTPUT = DEFAULT_SPEC.canonical_output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard-dir", default=DEFAULT_SHARD_DIR)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def main() -> int:
    args = parse_args()
    shard_dir = Path(args.shard_dir)
    output_path = Path(args.output)

    canonical = {}
    for row in read_jsonl(output_path):
        cid = str((row.get("source") or {}).get("candidate_id") or "")
        if cid:
            canonical[cid] = row

    for path in sorted(shard_dir.glob(f"{DEFAULT_SPEC.shard_prefix}*.jsonl")):
        for row in read_jsonl(path):
            cid = str((row.get("source") or {}).get("candidate_id") or "")
            if cid:
                canonical[cid] = row

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for cid in sorted(canonical):
            f.write(json.dumps(canonical[cid], ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    print(json.dumps({"merged_rows": len(canonical), "output": str(output_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
