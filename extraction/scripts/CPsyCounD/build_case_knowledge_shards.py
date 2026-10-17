#!/usr/bin/env python3
"""Build case-id shards for the historical CPsyCounD case-knowledge pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cpsycound_surface_registry import CASE_KNOWLEDGE_INTERACTION_UNITS_PATH, CASE_KNOWLEDGE_SHARDS_DIR


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-units', default=str(CASE_KNOWLEDGE_INTERACTION_UNITS_PATH))
    parser.add_argument('--out-dir', default=str(CASE_KNOWLEDGE_SHARDS_DIR))
    parser.add_argument('--shards', type=int, default=8)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.shards < 1:
        raise ValueError('--shards must be >= 1')
    case_ids = set()
    with Path(args.input_units).open('r', encoding='utf-8') as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            case_id = (row.get('source') or {}).get('case_id')
            if case_id:
                case_ids.add(str(case_id))
    ordered = sorted(case_ids)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    shard_infos = []
    for idx in range(args.shards):
        shard_ids = ordered[idx :: args.shards]
        path = out_dir / f'case_ids.shard_{idx:02d}.txt'
        path.write_text('\n'.join(shard_ids) + ('\n' if shard_ids else ''), encoding='utf-8')
        shard_infos.append({'shard': idx, 'path': str(path), 'case_count': len(shard_ids)})
    manifest = {
        'input_units': args.input_units,
        'case_count': len(ordered),
        'shards': args.shards,
        'shard_infos': shard_infos,
    }
    (out_dir / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
