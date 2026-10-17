#!/usr/bin/env python3
"""Consolidate shard outputs for the historical CPsyCounD case-knowledge pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cpsycound_surface_registry import CASE_KNOWLEDGE_SHARDS_DIR, CASE_KNOWLEDGE_STAGE_OUTPUTS

STAGE_OUTPUTS = {stage: str(path) for stage, path in CASE_KNOWLEDGE_STAGE_OUTPUTS.items()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=sorted(STAGE_OUTPUTS), required=True)
    parser.add_argument('--shard-dir', default=str(CASE_KNOWLEDGE_SHARDS_DIR))
    parser.add_argument('--output')
    parser.add_argument('--failure-output')
    parser.add_argument('--manifest')
    parser.add_argument('--include-existing', action='store_true', help='Include existing canonical stage output before shard rows.')
    return parser.parse_args()


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    with path.open('r', encoding='utf-8') as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with path.open('w', encoding='utf-8') as handle:
        for row in rows:
            line = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
            digest.update(line.encode('utf-8'))
            handle.write(line)
    return digest.hexdigest()


def case_id(row: dict[str, Any]) -> str | None:
    source = row.get('source') or {}
    value = source.get('case_id')
    return str(value) if value else None


def failure_case_id(row: dict[str, Any]) -> str | None:
    value = row.get('case_id')
    return str(value) if value else None


def main() -> int:
    args = parse_args()
    output = Path(args.output or STAGE_OUTPUTS[args.stage])
    failure_output = Path(args.failure_output or output.with_suffix('.failures.jsonl'))
    manifest_path = Path(args.manifest or f'outputs/knowledge/manifests/CPsyCounD.{args.stage}.shard_consolidation_manifest.json')
    shard_dir = Path(args.shard_dir)

    rows: list[dict[str, Any]] = []
    if args.include_existing:
        rows.extend(read_jsonl(output))
    for path in sorted(shard_dir.glob(f'{args.stage}.shard_*.jsonl')):
        rows.extend(read_jsonl(path))

    failures: list[dict[str, Any]] = []
    if args.include_existing:
        failures.extend(read_jsonl(failure_output))
    for path in sorted(shard_dir.glob(f'{args.stage}.shard_*.failures.jsonl')):
        failures.extend(read_jsonl(path))

    by_case: dict[str, dict[str, Any]] = {}
    duplicate_success = Counter()
    for row in rows:
        cid = case_id(row)
        if not cid:
            continue
        if cid in by_case:
            duplicate_success[cid] += 1
        by_case[cid] = row

    by_failure_case: dict[str, dict[str, Any]] = {}
    duplicate_failures = Counter()
    for row in failures:
        cid = failure_case_id(row)
        if not cid:
            continue
        if cid in by_failure_case:
            duplicate_failures[cid] += 1
        by_failure_case[cid] = row

    for cid in list(by_failure_case):
        if cid in by_case:
            del by_failure_case[cid]

    merged_rows = [by_case[cid] for cid in sorted(by_case)]
    merged_failures = [by_failure_case[cid] for cid in sorted(by_failure_case)]
    output_sha = write_jsonl(output, merged_rows)
    failure_sha = write_jsonl(failure_output, merged_failures) if merged_failures else None
    if not merged_failures and failure_output.exists():
        failure_output.unlink()

    manifest = {
        'schema_version': 'theraskill_case_knowledge_shard_consolidation_manifest_v1',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'stage': args.stage,
        'shard_dir': str(shard_dir),
        'output': str(output),
        'output_sha256': output_sha,
        'failure_output': str(failure_output),
        'failure_output_sha256': failure_sha,
        'rows': len(merged_rows),
        'failure_rows': len(merged_failures),
        'duplicate_success_cases': dict(duplicate_success),
        'duplicate_failure_cases': dict(duplicate_failures),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
