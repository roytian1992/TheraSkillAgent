#!/usr/bin/env python3
"""Profile real retrieval, optionally replaying a recorded LLM plan for comparisons.

Reference mode freezes only planning; all index queries and embedding calls are real.
Live mode runs the configured LLM planner. Outputs never include credentials.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import platform
import resource
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference-bundle', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--plan-mode', choices=('reference', 'live'), default='reference')
    parser.add_argument('--runs', type=int, default=1)
    parser.add_argument('--cases', type=Path, help='JSON list of query/skill/optional filters; requires live planning')
    args = parser.parse_args()
    if args.cases and args.plan_mode != 'live':
        parser.error('--cases requires --plan-mode live')
    args.output_dir.mkdir(parents=True, exist_ok=False)
    from domains.theraskill.hybrid_retrieval import TheraSkillHybridRetriever, selected_retrieval_paths
    from domains.theraskill.runtime import runtime_root
    reference = json.loads(args.reference_bundle.read_text())
    paths = selected_retrieval_paths(ROOT / 'configs/theraskill/retrieval.yaml',
                                     runtime_root() / 'support/runtime/cache')
    retriever = TheraSkillHybridRetriever(ROOT, paths)
    module = retriever._module
    if args.plan_mode == 'reference':
        module.load_or_build_plan = lambda _: {
            'plan': reference['llm_retrieval_plan'],
            'planner_trace': {**reference['planner_trace'], 'replayed_for_comparison': True},
        }
    timings = {}
    def instrument(owner, name, label=None):
        original = getattr(owner, name)
        @functools.wraps(original)
        def measured(*a, **kw):
            start = time.perf_counter()
            try:
                return original(*a, **kw)
            finally:
                row = timings.setdefault(label or name, {'calls': 0, 'elapsed_ms': 0.0})
                row['calls'] += 1
                row['elapsed_ms'] += (time.perf_counter() - start) * 1000
        setattr(owner, name, measured)
    for name in ('load_or_build_plan', 'resolve_plan_scope', 'count_scope_uncached',
                 'allowed_retrieval_ids', 'fetch_bm25_candidates', 'fetch_label_candidates',
                 'fetch_compact_label_candidates', 'fetch_required_family_candidates',
                 'fetch_dense_candidates', 'embedding_request_cached', 'faiss_shard_search',
                 'load_shard_ids', 'lexical_candidates',
                 'filter_candidates', 'rrf_merge_channels', 'build_items'):
        instrument(module, name)
    for name in ('load_index_value_inventory', 'chat_completion'):
        instrument(module.PLANNER, name, 'planner.' + name)
    manifest = {
        'reference_bundle': str(args.reference_bundle.resolve()),
        'reference_sha256': hashlib.sha256(args.reference_bundle.read_bytes()).hexdigest(),
        'plan_mode': args.plan_mode, 'python': sys.executable, 'python_version': platform.python_version(),
        'command': sys.argv, 'git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'index': str(paths.sqlite_fts.parent), 'model_config': str(paths.model_config),
        'skill': reference['skill'], 'runs': [], 'status': 'running',
        'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in [Path(__file__).resolve(), Path(module.__file__), Path(module.PLANNER.__file__),
                                    ROOT / 'theraskill_agent/indexing/search.py', ROOT / 'theraskill_agent/indexing/inventory.py',
                                    ROOT / 'domains/theraskill/hybrid_retrieval.py']},
    }
    cases = json.loads(args.cases.read_text()) if args.cases else [reference] * args.runs
    manifest['cases'] = cases if args.cases else [{'query_sha256': hashlib.sha256(reference['query'].encode()).hexdigest()}]
    (args.output_dir / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    for number, case in enumerate(cases, 1):
        timings.clear()
        started = time.perf_counter()
        bundle = retriever.search(case['query'], skill=case['skill'],
                                  top_k=case.get('top_k', 8),
                                  filters=case.get('filters', case.get('query_understanding', {}).get('filters')))
        elapsed_ms = (time.perf_counter() - started) * 1000
        (args.output_dir / f'bundle-{number}.json').write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + '\n')
        result = {'run': number, 'skill': case['skill'], 'elapsed_ms': elapsed_ms, 'stages': dict(timings),
                  'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  'unit_ids': [item.get('release_unit_id') for item in bundle['items']],
                  'families': [item.get('dedup_family') for item in bundle['items']],
                  'policy_checks': bundle['policy_checks'], 'quality_checks': bundle['quality_checks'],
                  'channel_counts': bundle['channel_counts']}
        manifest['runs'].append(result)
        (args.output_dir / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(result), flush=True)
    manifest['status'] = 'complete'
    (args.output_dir / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
