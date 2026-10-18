"""Correctness checks for cache validity and concurrent retrieval execution."""
from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from theraskill_agent.indexing import build_index
from theraskill_agent.indexing import inventory
from theraskill_agent.query_understanding import SUPPORTED_RETRIEVAL_SKILLS, understand_query

ROOT = Path(__file__).parents[1]
DEMO = ROOT / 'examples/knowledge/demo.jsonl'


def test_built_inventory_is_portable_and_avoids_database_scans(tmp_path, monkeypatch):
    build_index(DEMO, tmp_path / 'original')
    shutil.copytree(tmp_path / 'original', tmp_path / 'moved')
    inventory._CACHE.clear()
    def unexpected(*a, **kw):
        raise AssertionError('portable inventory must not scan metadata')
    monkeypatch.setattr(inventory.sqlite3, 'connect', unexpected)
    trace = {}
    values = inventory.load_inventory(tmp_path / 'moved/phase6_retrieval_fts.sqlite', trace=trace)
    assert trace['source'] == 'index_sidecar'
    assert {'therapy_process', 'method_rubric'} <= values['dedup_family']


def test_inventory_disk_cache_and_same_path_updates(tmp_path):
    db = tmp_path / 'index.sqlite'
    with sqlite3.connect(db) as connection:
        connection.execute('CREATE TABLE retrieval_meta (' + ','.join(column + ' TEXT' for column in inventory.COLUMNS) + ')')
        connection.execute('INSERT INTO retrieval_meta VALUES (?,?,?,?,?)', ('method_rubric', 'ordinary', 'reviewed', 'method', 'observed'))
    cache = tmp_path / 'inventory-cache.json'
    trace = {}
    inventory.load_inventory(db, cache_path=cache, trace=trace)
    assert trace['source'] == 'database'
    inventory._CACHE.clear()
    inventory.load_inventory(db, cache_path=cache, trace=trace)
    assert trace['source'] == 'disk_cache'
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE retrieval_meta SET primary_knowledge_type='new_method'")
    values = inventory.load_inventory(db, cache_path=cache, trace=trace)
    assert trace['source'] == 'database'
    assert values['primary_knowledge_type'] == {'new_method'}
    # Callers cannot mutate the shared cached inventory.
    values['primary_knowledge_type'].clear()
    assert inventory.load_inventory(db)['primary_knowledge_type'] == {'new_method'}


@pytest.fixture
def hybrid(tmp_path):
    pytest.importorskip('numpy')
    from domains.theraskill.hybrid_retrieval import TheraSkillHybridRetriever
    adapter = object.__new__(TheraSkillHybridRetriever)
    adapter.project_root = ROOT
    module = adapter._load_retriever()
    output = tmp_path / 'index'
    records = [json.loads(line) for line in DEMO.read_text().splitlines()]
    for language in ('en', 'zh'):
        records.append({**records[0], 'id': 'risk_' + language, 'family': 'clinical_risk_assessment',
                        'language': language, 'title': 'Risk assessment',
                        'text': 'Synthetic fixture: 核对原文证据 suicide risk assessment.'})
    source = tmp_path / 'knowledge.jsonl'
    source.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in records))
    build_index(source, output)
    def args_for(skill):
        args = module.parse_args(['--db', str(output / 'phase6_retrieval_fts.sqlite'),
            '--metadata-v2-db', str(output / 'metadata_v2.sqlite'),
            '--compact-label-db', str(output / 'compact_label_v1.sqlite'), '--skill', skill,
            '--query', '核对原文证据 empathy cognitive reframing', '--top-k', '3',
            '--knowledge-release-slot', str(tmp_path / 'no-overlay')])
        args.query_understanding = understand_query(args.query, skill=skill, filters={'language': 'zh'})
        return args
    return module, args_for


@pytest.mark.parametrize('skill', sorted(SUPPORTED_RETRIEVAL_SKILLS))
def test_parallel_channels_preserve_each_skill_and_explicit_filters(hybrid, monkeypatch, skill):
    module, args_for = hybrid
    calls = []
    def plan(args):
        calls.append(args.skill)
        return {'plan': module.PLANNER.fallback_plan(args.skill, args.query),
                'planner_trace': {'planner_status': 'test_fixture'}}
    monkeypatch.setattr(module, 'load_or_build_plan', plan)
    monkeypatch.setattr(module, 'fetch_dense_candidates', lambda *a: ([], {}, None, {}))
    original = module.allowed_retrieval_ids
    id_queries = []
    def allowed(*args):
        id_queries.append(1)
        return original(*args)
    monkeypatch.setattr(module, 'allowed_retrieval_ids', allowed)
    result = module.build_bundle(args_for(skill))
    assert calls == [skill] and len(id_queries) == 1
    assert result['items'] and all(item['primary_language'] == 'zh' for item in result['items'])
    assert result['skill'] == skill
    assert all(result['policy_checks'].values())
    assert result['timings']['total_ms'] > 0
    assert {'planning', 'scope_resolution', 'scope_ids', 'query_lexical', 'dense', 'selection'} <= result['timings']['stages_ms'].keys()


def test_required_and_bm25_channels_really_overlap(hybrid, monkeypatch):
    module, args_for = hybrid
    monkeypatch.setattr(module, 'load_or_build_plan', lambda a: {
        'plan': module.PLANNER.fallback_plan(a.skill, a.query), 'planner_trace': {}})
    monkeypatch.setattr(module, 'fetch_dense_candidates', lambda *a: ([], {}, None, {}))
    rendezvous = threading.Barrier(2, timeout=5)
    def bm25(*a):
        rendezvous.wait()
        return [], {}
    def required(*a, **kw):
        rendezvous.wait()
        return []
    monkeypatch.setattr(module, 'fetch_bm25_candidates', bm25)
    monkeypatch.setattr(module, 'fetch_required_family_candidates', required)
    assert module.build_bundle(args_for('therapy-response-reviewer'))['items']


def test_same_skill_requests_do_not_serialize_planning(tmp_path, monkeypatch):
    from domains.theraskill.hybrid_retrieval import TheraSkillHybridRetriever
    from types import SimpleNamespace
    rendezvous = threading.Barrier(2, timeout=5)
    def bundle(args):
        rendezvous.wait()
        return {'items': []}
    adapter = object.__new__(TheraSkillHybridRetriever)
    adapter.paths = SimpleNamespace(cache_root=tmp_path, sqlite_fts=tmp_path / 'fts', metadata_v2=tmp_path / 'meta',
        compact_label=tmp_path / 'labels', faiss_shards=tmp_path / 'shards', model_config=tmp_path / 'model')
    adapter._module = SimpleNamespace(parse_args=lambda _: SimpleNamespace(), build_bundle=bundle)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(adapter.search, query, skill='emotional-support') for query in ('work stress', 'feeling sad')]
        assert all(future.result()['items'] == [] for future in futures)


def test_mapped_flat_index_and_scoped_array_preserve_exact_neighbors(tmp_path):
    faiss = pytest.importorskip('faiss')
    import numpy as np
    from theraskill_agent.indexing.vectors import read_faiss_index
    rng = np.random.default_rng(7)
    vectors = rng.normal(size=(128, 16)).astype('float32')
    query = rng.normal(size=(1, 16)).astype('float32')
    index = faiss.IndexFlatIP(16)
    index.add(vectors)
    faiss.write_index(index, str(tmp_path / 'flat.faiss'))
    mapped = read_faiss_index(tmp_path / 'flat.faiss')
    positions = np.asarray([2, 9, 19, 65, 100, 127], dtype=np.int64)
    expected_scores, expected_ids = index.search(query, 4, params=faiss.SearchParameters(sel=faiss.IDSelectorBatch(positions)))
    scores, ids = mapped.search(query, 4, params=faiss.SearchParameters(sel=faiss.IDSelectorArray(positions)))
    np.testing.assert_array_equal(ids, expected_ids)
    np.testing.assert_allclose(scores, expected_scores, atol=1e-5)


def test_required_method_family_is_not_displaced_by_a_large_qa_pool(tmp_path):
    pytest.importorskip('numpy')
    from domains.theraskill.hybrid_retrieval import TheraSkillHybridRetriever
    adapter = object.__new__(TheraSkillHybridRetriever)
    adapter.project_root = ROOT
    module = adapter._load_retriever()
    template = json.loads(DEMO.read_text().splitlines()[0])
    records = [{**template, 'id': f'qa-{i}', 'family': 'qa_psychoeducation', 'language': 'zh',
                'title': '合成测试问答', 'text': '认知重构 ' * 50} for i in range(100)]
    records.extend([{**template, 'id': 'method-zh', 'family': 'method_rubric', 'language': 'zh', 'text': '认知重构方法'},
                    {**template, 'id': 'method-en', 'family': 'method_rubric', 'language': 'en', 'text': 'cognitive reframing'}])
    source = tmp_path / 'knowledge.jsonl'
    source.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in records))
    output = tmp_path / 'index'
    build_index(source, output)
    plan = module.PLANNER.fallback_plan('cognitive-reframing-support', '认知重构')
    plan['target_families']['required'] = ['qa_psychoeducation', 'method_rubric']
    plan['_query_understanding'] = understand_query('认知重构', skill='cognitive-reframing-support', filters={'language': 'zh'})
    rows = module.fetch_required_family_candidates(output / 'phase6_retrieval_fts.sqlite', plan, '认知重构', 8, skip_fts=True)
    assert {'qa_psychoeducation', 'method_rubric'} <= {row['dedup_family'] for row in rows}
    assert len(rows) == 8
    assert 'method-zh' in {row['release_unit_id'] for row in rows}
    assert 'method-en' not in {row['release_unit_id'] for row in rows}


def test_scope_guard_preserves_eligibility_without_scanning_the_boolean_index(tmp_path):
    pytest.importorskip('numpy')
    from domains.theraskill.hybrid_retrieval import TheraSkillHybridRetriever
    adapter = object.__new__(TheraSkillHybridRetriever)
    adapter.project_root = ROOT
    module = adapter._load_retriever()
    db = tmp_path / 'metadata_v2.sqlite'
    with sqlite3.connect(db) as con:
        con.executescript('''CREATE TABLE metadata_v2 (retrieval_id TEXT, primary_language TEXT, retrieval_eligible INTEGER);
            CREATE INDEX eligible ON metadata_v2(retrieval_eligible);
            CREATE INDEX language ON metadata_v2(primary_language);''')
        con.executemany('INSERT INTO metadata_v2 VALUES (?,?,?)', [
            ('allowed', 'en', 1), ('zero', 'en', 0), ('null', 'en', None),
            ('two', 'en', 2), ('other-language', 'zh', 1)])
    understanding = {'filters': {'primary_language': ['en']}}
    where, params = ['m.retrieval_eligible = 1'], []
    module.append_query_scope(db, {'_query_understanding': understanding}, where, params, 'metadata_v2')
    with sqlite3.connect(db) as con:
        sql = 'SELECT m.retrieval_id FROM metadata_v2 m WHERE ' + ' AND '.join(where)
        assert con.execute(sql, params).fetchall() == [('allowed',)]
        assert 'USING INDEX language' in str(con.execute('EXPLAIN QUERY PLAN ' + sql, params).fetchall())


def test_scope_id_cache_respects_filters_and_database_updates(hybrid):
    module, args_for = hybrid
    args = args_for('therapy-response-reviewer')
    plan = {'metadata_filters': {}, '_query_understanding': args.query_understanding}
    db = Path(args.metadata_v2_db)
    ids = module.allowed_retrieval_ids(db, plan, 50000, 10)
    assert ids
    removed = next(iter(ids))
    ids.clear()
    assert removed in module.allowed_retrieval_ids(db, plan, 50000, 10)
    with sqlite3.connect(db) as con:
        con.execute('UPDATE metadata_v2 SET retrieval_eligible=0 WHERE retrieval_id=?', (removed,))
    assert removed not in module.allowed_retrieval_ids(db, plan, 50000, 10)
    plan['_query_understanding'] = understand_query(args.query, skill='therapy-response-reviewer', filters={'language': 'en'})
    english = module.allowed_retrieval_ids(db, plan, 50000, 10)
    assert english and not english.intersection(module.allowed_retrieval_ids(db,
        {'metadata_filters': {}, '_query_understanding': args.query_understanding}, 50000, 10))
