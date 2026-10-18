"""Real production retrieval, optional human qrels and bounded concurrency."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import platform
import sys
import time
import uuid
from .paths import data_root, resource_path
from .behavior_eval import fingerprint, target_snapshot
from .indexing.common import write_json, safe_id


def rank_metrics(ids, judgments):
    """Unjudged documents never become negative relevance labels."""
    if not judgments:
        return {'status':'unjudged','judged_hits':0,'recall_at_k':None,'ndcg_at_k':None}
    if any(not isinstance(v,int) or isinstance(v,bool) or not 0<=v<=3 for v in judgments.values()):
        raise ValueError('Relevance judgments must be integers 0..3')
    relevant={key for key,value in judgments.items() if value>0}
    dcg=sum((2**judgments.get(key,0)-1)/math.log2(rank+2) for rank,key in enumerate(ids))
    ideal=sum((2**score-1)/math.log2(rank+2) for rank,score in enumerate(sorted(judgments.values(),reverse=True)[:len(ids)]))
    return {'status':'partially_judged' if any(key not in judgments for key in ids) else 'judged',
            'judged_hits':sum(key in judgments for key in ids),'judged_total':len(judgments),
            'relevant_total':len(relevant),'recall_at_k':len(set(ids)&relevant)/len(relevant) if relevant else None,
            'ndcg_at_k':dcg/ideal if ideal else None}


def run_retrieval_suite(options, *, run_id=None, checkpoint=None, cancelled=None):
    suite=options.get('suite') or json.loads(resource_path('configs/theraskill/evaluations/retrieval.json').read_text())
    if suite.get('schema_version')!='theraskill_retrieval_suite_v1' or not suite.get('cases'):
        raise ValueError('Unsupported or empty retrieval suite')
    cases=suite['cases'];identifiers=[c['id'] for c in cases]
    for identifier in identifiers:safe_id(identifier)
    if len(identifiers)!=len(set(identifiers)):raise ValueError('Duplicate retrieval case IDs')
    workers=int(options.get('concurrency',1));repeats=int(options.get('repeats',1))
    if not 1<=workers<=4 or not 1<=repeats<=10:raise ValueError('Concurrency must be 1..4 and repeats 1..10')
    target,directory=target_snapshot(options)
    if directory is None:raise ValueError('Install and select a knowledge index before retrieval evaluation')
    folder=data_root()/'evaluations/retrieval'/safe_id(run_id or 'retrieval_'+uuid.uuid4().hex)
    folder.mkdir(parents=True,exist_ok=False)
    items=[];start=time.perf_counter()
    def execute(case,repeat):
        from .resources import using_index
        from plugins.theraskill.retrieval import search_managed_knowledge
        from .indexing.search import matches_scope
        from .query_understanding import understand_query
        if cancelled and cancelled():raise ValueError('Retrieval evaluation cancelled')
        begin=time.perf_counter()
        try:
            with using_index(directory):
                result=search_managed_knowledge(case['query'],skill=case['skill'],limit=case.get('limit',8),filters=case.get('filters'))
            hits=result['matches'];families={h.get('dedup_family') for h in hits}
            understanding=understand_query(case['query'],skill=case['skill'],filters=case.get('filters'))
            checks={'filters_respected':all(matches_scope(h,understanding) for h in hits),
                    'required_families':set(case.get('required_families',[])).issubset(families),
                    'expected_empty':not hits if case.get('expect_empty') else True,
                    'nonempty':bool(hits) if case.get('expect_nonempty',True) and not case.get('expect_empty') else True}
            unavailable=set(case.get('expected_unavailable_families',[]))
            if unavailable:
                reported=set(result.get('diagnostics',{}).get('quality_checks',{}).get('missing_required_families',[]))
                checks['known_data_gap_reported']=unavailable.issubset(reported) and not bool(unavailable&families)
            ids=[str(h.get('retrieval_id') or h.get('unit_id')) for h in hits]
            row={'id':case['id'],'repeat':repeat,'case':case,'status':'completed','result':result,'checks':checks,
                 'relevance':rank_metrics(ids,case.get('judgments'))}
        except Exception as exc:
            row={'id':case['id'],'repeat':repeat,'case':case,'status':'failed','error':str(exc)}
        row['seconds']=time.perf_counter()-begin
        return row
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures=[pool.submit(execute,c,r) for r in range(repeats) for c in cases]
            for future in as_completed(futures):
                row=future.result();items.append(row)
                write_json(folder/(row['id']+'_'+str(row['repeat'])+'.json'),row)
                if checkpoint:checkpoint(row['id']+'_'+str(row['repeat']),row)
    finally:
        # A failed/interrupted run still leaves every completed raw result.
        write_json(folder/'partial.json',{'items':items,'expected':len(cases)*repeats})
    times=sorted(row['seconds'] for row in items)
    percentile=lambda q:times[max(0,math.ceil(len(times)*q)-1)] if times else None
    complete=[row for row in items if row['status']=='completed']
    report={'id':folder.name,'schema_version':'theraskill_retrieval_report_v1','suite':suite,
            'suite_sha256':fingerprint(suite),'target':target,'python':sys.executable,'python_version':platform.python_version(),
            'concurrency':workers,'repeats':repeats,'elapsed_seconds':time.perf_counter()-start,
            'items':items,'expected':len(cases)*repeats,'completed':len(complete),'failed':len(items)-len(complete),
            'checks_passed':sum(all(row['checks'].values()) for row in complete),
            'degraded':sum(bool(row['result'].get('warnings')) for row in complete),
            'latency_seconds':{'p50':percentile(.5),'p95':percentile(.95),'max':max(times) if times else None},
            'human_judged_cases':sum(bool(c.get('judgments')) for c in cases),
            'known_data_gaps':[{'case_id':c['id'],'families':c['expected_unavailable_families']} for c in cases if c.get('expected_unavailable_families')],
            'limits':['Latency includes real model planning. Small-run percentiles are not an SLA.',
                      'Unjudged cases check engineering coverage only; they do not measure expert relevance.']}
    write_json(folder/'report.json',report)
    return report
