#!/usr/bin/env python3
"""Compare managed retrieval with the exact historical local lexical function.

This measures route/family coverage, filtering and latency on real knowledge.
It does not claim expert relevance labels or clinical effectiveness. Hybrid
evaluation makes real embedding requests through the configured provider.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from theraskill_agent.indexing import retrieve_index
from theraskill_agent.indexing.common import digest, write_json
from theraskill_agent.indexing.search import matches_scope
from theraskill_agent.query_understanding import understand_query
from theraskill_agent.text import redact_secrets


def baseline_function(ref):
    source = subprocess.check_output(['git', 'show', f'{ref}:plugins/theraskill/retrieval.py'], cwd=ROOT, text=True)
    node = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == '_query_fts')
    code = ast.get_source_segment(source, node)
    namespace = {'sqlite3': sqlite3, 'Path': Path, 'Any': Any, 'RetrievalError': RuntimeError}
    exec(compile(code, f'{ref}:_query_fts', 'exec'), namespace)
    return namespace['_query_fts'], hashlib.sha256(code.encode()).hexdigest()


def evaluate(args):
    if not args.baseline_only and not args.index:
        raise ValueError('--index is required unless --baseline-only is set')
    if args.baseline_only and not args.baseline_db:
        raise ValueError('--baseline-only needs --baseline-db')
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    rows_path = output / 'cases.jsonl'
    if rows_path.exists():
        raise ValueError('evaluation output already exists; choose a new directory')
    cases = [json.loads(line) for line in Path(args.cases).read_text().splitlines() if line.strip()]
    baseline, baseline_sha = baseline_function(args.baseline_ref) if args.baseline_db else (None, None)
    metadata = {'started_at': datetime.now(timezone.utc).isoformat(), 'cases_path': str(Path(args.cases).resolve()),
                'cases_sha256': digest(args.cases), 'case_count': len(cases), 'index': str(Path(args.index).resolve()) if args.index else None,
                'index_manifest_sha256': digest(Path(args.index) / 'manifest.json') if args.index else None,
                'code_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                'python': sys.executable, 'baseline_ref': args.baseline_ref if baseline else None,
                'baseline_function_sha256': baseline_sha, 'baseline_db': args.baseline_db,
                'measurement': 'engineering route/family/filter coverage; not expert relevance or clinical validation'}
    write_json(output / 'run.json', metadata)
    records = []
    with rows_path.open('x') as handle:
        for case in cases:
            understanding = understand_query(case['query'], skill=case['skill'], filters=case.get('filters'))
            for backend in (['historical_local_lexical'] if args.baseline_only else (['historical_local_lexical', 'managed'] if baseline else ['managed'])):
                start = time.monotonic()
                row = {'case_id': case['id'], 'backend': backend, 'case': case}
                try:
                    if backend == 'managed':
                        result = retrieve_index(args.index, case['query'], skill=case['skill'], filters=case.get('filters'))
                        hits = result['matches']
                        row['retrieval_mode'] = result['retrieval_mode']
                    else:
                        hits = baseline(Path(args.baseline_db), case['query'], 8)
                    families = [h.get('dedup_family') for h in hits]
                    expected = case.get('expected_families') or []
                    # Historical metadata did not contain language. Hydrate only
                    # that missing field to assess filters, without reordering.
                    if backend != 'managed' and args.baseline_metadata and hits:
                        with sqlite3.connect(args.baseline_metadata) as db:
                            for hit in hits:
                                found = db.execute('SELECT primary_language FROM metadata_v2 WHERE retrieval_id=?', (hit['retrieval_id'],)).fetchone()
                                hit['primary_language'] = found[0] if found else None
                    row.update(count=len(hits), families=families,
                               family_hit_at_5=bool(set(expected).intersection(families[:5])) if expected else None,
                               scope_violations=sum(not matches_scope(h, understanding) for h in hits),
                               empty_expectation_pass=(not hits) if case.get('expect_empty') else None,
                               hits=hits)
                except Exception as exc:
                    row['error'] = redact_secrets(str(exc))
                row['seconds'] = round(time.monotonic() - start, 4)
                records.append(row)
                handle.write(json.dumps(row, ensure_ascii=False) + '\n'); handle.flush()
                print(json.dumps({k: v for k, v in row.items() if k not in {'hits', 'case'}}, ensure_ascii=False), flush=True)
    summary = {}
    for backend in dict.fromkeys(row['backend'] for row in records):
        selected = [r for r in records if r['backend'] == backend]
        family_cases = [r for r in selected if r.get('case', {}).get('expected_families')]
        empty_cases = [r for r in selected if r.get('case', {}).get('expect_empty')]
        latency = sorted(r['seconds'] for r in selected)
        summary[backend] = {'cases': len(selected), 'errors': sum('error' in r for r in selected),
                            'family_cases': len(family_cases), 'family_hits_at_5': sum(r.get('family_hit_at_5') is True for r in family_cases),
                            'empty_cases': len(empty_cases), 'empty_passes': sum(r.get('empty_expectation_pass') is True for r in empty_cases),
                            'scope_violations': sum(r.get('scope_violations', 0) for r in selected),
                            'median_seconds': latency[len(latency)//2], 'max_seconds': max(latency)}
    write_json(output / 'summary.json', summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index')
    parser.add_argument('--cases', default=str(ROOT / 'examples/retrieval/rollout_cases.jsonl'))
    parser.add_argument('--output', required=True)
    parser.add_argument('--baseline-db')
    parser.add_argument('--baseline-metadata')
    parser.add_argument('--baseline-ref', default='e687748^')
    parser.add_argument('--baseline-only', action='store_true')
    print(json.dumps(evaluate(parser.parse_args()), indent=2))
