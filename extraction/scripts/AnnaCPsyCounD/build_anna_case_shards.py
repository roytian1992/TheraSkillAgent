#!/usr/bin/env python3
"""Build case-id shards for Anna-CPsyCounD patient-side extraction."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from anna_cpsycound_surface_registry import DATASET_ROOT, PATIENT_PERSONA_OUTPUT_PATH, SHARD_DIR, SOURCE_FILE

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent


SOURCE_ROOT = str(DATASET_ROOT)
DEFAULT_OUT_DIR = str(SHARD_DIR.relative_to(PROJECT_ROOT))
DEFAULT_COMPLETED_OUTPUT = str(PATIENT_PERSONA_OUTPUT_PATH.relative_to(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", default=SOURCE_ROOT)
    parser.add_argument("--source-file", default=SOURCE_FILE)
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--shards", type=int, default=8)
    parser.add_argument("--completed-output", default=DEFAULT_COMPLETED_OUTPUT)
    parser.add_argument("--exclude-completed", action="store_true")
    return parser.parse_args()


def read_json(path: str | Path):
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_jsonl(path: str | Path):
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def resolve_path(path: str | Path) -> Path:
    path = Path(path)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def main() -> int:
    args = parse_args()
    if args.shards < 1:
        raise ValueError("--shards must be >= 1")

    source_root = resolve_path(args.source_root)
    rows = read_json(source_root / args.source_file)
    ordered_case_ids = [str(row["id"]) for row in rows]

    completed_ids = set()
    if args.exclude_completed:
        for row in read_jsonl(resolve_path(args.completed_output)):
            case_id = (row.get("content") or {}).get("case_id")
            if case_id:
                completed_ids.add(str(case_id))

    remaining = [case_id for case_id in ordered_case_ids if case_id not in completed_ids]

    out_dir = resolve_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    shard_infos = []
    for idx in range(args.shards):
        shard_ids = remaining[idx :: args.shards]
        path = out_dir / f"case_ids.shard_{idx:02d}.txt"
        path.write_text("\n".join(shard_ids) + ("\n" if shard_ids else ""), encoding="utf-8")
        shard_infos.append({"shard": idx, "path": str(path), "case_count": len(shard_ids)})

    manifest = {
        "dataset": "Anna-CPsyCounD",
        "source_file": str(source_root / args.source_file),
        "total_case_count": len(ordered_case_ids),
        "excluded_completed_case_count": len(completed_ids),
        "remaining_case_count": len(remaining),
        "shards": args.shards,
        "shard_infos": shard_infos,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
