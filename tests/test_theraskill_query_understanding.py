from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from theraskill_agent.indexing import build_index, retrieve_index, search_index
from theraskill_agent.query_understanding import understand_query


DEMO = Path(__file__).parents[1] / "examples/knowledge/demo.jsonl"


def test_query_understanding_routes_skill_and_risk() -> None:
    result = understand_query("我现在很担心，可能会伤害自己", skill="risk-triage")

    assert result["language"] == "zh"
    assert result["risk_signal"]["detected"] is True
    assert result["automatic_filters"]["risk_scope"] == [
        "clinical_risk_or_assessment",
        "risk_related_text",
    ]
    assert result["route"]["preferred_families"][0] == "clinical_risk_assessment"


def test_query_understanding_detects_mixed_text_and_normalizes_filters() -> None:
    result = understand_query(
        "  认知 CBT：help me reframe  ",
        skill="cognitive-reframing-support",
        filters={"language": "en", "family": ["method_rubric"]},
    )

    assert result["normalized_query"] == "认知 CBT: help me reframe"
    assert result["language"] == "mixed"
    assert result["filters"] == {"primary_language": ["en"], "dedup_family": ["method_rubric"]}
    assert "method_rubric" in result["route"]["preferred_families"]


def test_managed_lexical_search_supports_cjk_and_language_filters(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("THERASKILL_AGENT_ROOT", str(tmp_path))
    output = tmp_path / "index"
    build_index(DEMO, output)

    cjk_hits = search_index(output, "核对原文证据", skill="therapy-response-reviewer")
    assert cjk_hits
    assert all(hit["primary_language"] == "zh" for hit in cjk_hits)

    english_hits = search_index(output, "uncertainty", filters={"language": "en"})
    assert english_hits and all(hit["primary_language"] == "en" for hit in english_hits)

    envelope = retrieve_index(
        output,
        "uncertainty",
        skill="emotional-support",
        filters={"language": "en"},
    )
    assert envelope["retrieval_mode"] == "lexical"
    assert envelope["query_understanding"]["skill"] == "emotional-support"
    assert envelope["count"] == len(envelope["matches"])


def test_skill_route_excludes_synthetic_construction_from_support(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("THERASKILL_AGENT_ROOT", str(tmp_path))
    source = tmp_path / "knowledge.jsonl"
    rows = [json.loads(line) for line in DEMO.read_text(encoding="utf-8").splitlines()]
    rows.append({
        "id": "synthetic_only",
        "type": "demonstration",
        "family": "synthetic_construction",
        "title": "uncertainty response construction",
        "text": "uncertainty response construction",
        "language": "en",
        "source": "synthetic",
        "license": "CC0-1.0",
        "evidence_tier": "synthetic",
        "review_status": "approved",
        "retrieval_policy": {"retrieval_eligible": True, "embedding_eligible": False},
    })
    source.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    output = tmp_path / "index"
    build_index(source, output)

    hits = search_index(output, "uncertainty response construction", skill="emotional-support")
    assert all(hit["dedup_family"] != "synthetic_construction" for hit in hits)


def test_concept_expansion_crosses_languages_without_changing_explicit_skill(tmp_path):
    source = tmp_path / 'knowledge.jsonl'
    row = json.loads(DEMO.read_text().splitlines()[0])
    row.update(id='reframe', family='method_rubric', title='Cognitive reframing', text='Explore an alternative thought collaboratively.')
    source.write_text(json.dumps(row) + '\n')
    build_index(source, tmp_path / 'index')

    hits = search_index(tmp_path / 'index', '经常灾难化', skill='cognitive-reframing-support')
    assert hits[0]['release_unit_id'] == 'reframe'
    understood = understand_query('审核这段关于自杀的回复', skill='therapy-response-reviewer')
    assert understood['skill'] == 'therapy-response-reviewer'
    assert understood['risk_signal']['detected'] and understood['warnings']
    assert understand_query('如何识别非理性信念')['suggested_skill'] == 'rebt-belief-work-support'

    row.update(id='reframe_zh', language='zh', title='认知重构', text='认知重构需要合作寻找替代想法。')
    source.write_text(json.dumps(row) + '\n')
    build_index(source, tmp_path / 'zh-index')
    assert search_index(tmp_path / 'zh-index', 'cognitive reframing', skill='cognitive-reframing-support')[0]['release_unit_id'] == 'reframe_zh'


@pytest.mark.parametrize('filters', [[], {'unknown': 'x'}, {'language': {'value': 'zh'}}, {'language': [1]}, {'language': True}])
def test_invalid_filters_are_rejected(filters):
    with pytest.raises(ValueError):
        understand_query('evidence', filters=filters)


def test_explicit_metadata_filters_combine_with_and(tmp_path):
    output = tmp_path / 'index'
    build_index(DEMO, output)
    filters = {'knowledge_type': 'demonstration', 'dataset': 'self_authored_synthetic', 'evidence_tier': 'synthetic', 'language': 'en'}
    hits = search_index(output, 'uncertainty', filters=filters)
    assert [hit['release_unit_id'] for hit in hits] == ['style_response_en']
    assert not search_index(output, 'uncertainty', filters={**filters, 'dataset': 'other_source'})


def test_index_rejects_unenforceable_filter_and_handles_low_signal_input(tmp_path):
    output = tmp_path / 'index'
    build_index(DEMO, output)
    assert retrieve_index(output, '?!')['query_understanding']['needs_clarification']
    assert search_index(output, '?!') == []
    with sqlite3.connect(output / 'phase6_retrieval_fts.sqlite') as db:
        db.execute('ALTER TABLE retrieval_meta DROP COLUMN primary_language')
    with pytest.raises(ValueError, match='rebuild'):
        search_index(output, 'evidence', filters={'language': 'en'})


def test_runtime_lexical_filters_before_quotas_and_backfill(tmp_path):
    from domains.theraskill.hybrid_retrieval import search_policy_lexical
    root = Path(__file__).parents[1]
    output = tmp_path / 'index'
    build_index(DEMO, output)
    result = search_policy_lexical(root, output / 'phase6_retrieval_fts.sqlite', '核对原文证据',
                                   skill='therapy-response-reviewer', top_k=3, filters={'language': 'zh'})
    assert len(result['items']) == 3
    assert all(item['primary_language'] == 'zh' for item in result['items'])
    assert result['query_understanding']['filters'] == {'primary_language': ['zh']}
    assert not search_policy_lexical(root, output / 'phase6_retrieval_fts.sqlite', '?!', skill='therapy-response-reviewer')['items']


def test_hybrid_scope_relaxation_and_backfill_preserve_explicit_filters(tmp_path, monkeypatch):
    pytest.importorskip('numpy')
    from domains.theraskill.hybrid_retrieval import TheraSkillHybridRetriever
    root = Path(__file__).parents[1]
    output = tmp_path / 'index'
    build_index(DEMO, output)
    # Load the real hybrid implementation; mock only external model work.
    adapter = object.__new__(TheraSkillHybridRetriever)
    adapter.project_root = root
    module = adapter._load_retriever()
    plan = module.PLANNER.fallback_plan('therapy-response-reviewer', '核对原文证据')
    monkeypatch.setattr(module, 'load_or_build_plan', lambda args: {'plan': plan, 'planner_trace': {'planner_status': 'test'}})
    monkeypatch.setattr(module, 'fetch_dense_candidates', lambda *args: ([{'retrieval_id': 'ret_bad', 'dedup_family': 'therapy_process', 'primary_language': 'en'}], {}, None, {}))
    from theraskill_agent.indexing.builder import DB
    args = module.parse_args(['--db', str(output / DB), '--metadata-v2-db', str(output / 'metadata_v2.sqlite'),
                              '--compact-label-db', str(output / 'compact_label_v1.sqlite'), '--skill', 'therapy-response-reviewer',
                              '--query', '核对原文证据', '--top-k', '3', '--knowledge-release-slot', str(tmp_path / 'no-overlay')])
    args.query_understanding = understand_query(args.query, skill=args.skill, filters={'language': 'zh'})
    result = module.build_bundle(args)
    assert len(result['items']) == 3
    assert all(item['primary_language'] == 'zh' for item in result['items'])
    assert result['structured_filter']['candidate_scope_count'] == 3
    # Required-family and policy backfill functions must enforce the same scope.
    scoped = result['llm_retrieval_plan']
    for candidates in [module.fetch_required_family_candidates(output / DB, scoped, args.query, 10, skip_fts=True),
                       module.fetch_policy_family_fallback(output / DB, args.skill, scoped, 10)]:
        assert candidates
        import theraskill_agent.indexing.search as search
        assert all(item['primary_language'] == 'zh' for item in search.filter_candidates(output / DB, candidates, args.query_understanding))
        assert len(search.filter_candidates(output / DB, candidates, args.query_understanding)) == len(candidates)


def test_cli_understanding_does_not_need_an_index():
    from theraskill_agent.cli import build_parser
    from theraskill_agent.resource_cli import run_command
    args = build_parser().parse_args(['index', 'understand', '--query', 'CBT 认知重构', '--skill', 'cognitive-reframing-support'])
    assert run_command(args)['task_type'] == 'framework_guided_intervention'


def test_advanced_dense_filters_before_truncating_global_neighbors(tmp_path):
    faiss = pytest.importorskip('faiss')
    import numpy as np
    from domains.theraskill.hybrid_retrieval import TheraSkillHybridRetriever
    adapter = object.__new__(TheraSkillHybridRetriever)
    adapter.project_root = Path(__file__).parents[1]
    module = adapter._load_retriever()
    index = faiss.IndexFlatIP(2)
    index.add(np.asarray([[1., 0.], [0., 1.]], dtype=np.float32))
    faiss.write_index(index, str(tmp_path / 'index.faiss'))
    (tmp_path / 'ids.jsonl').write_text('{"retrieval_id":"globally_closest"}\n{"retrieval_id":"matches_filter"}\n')
    (tmp_path / 'manifest.json').write_text(json.dumps({'shards': [{
        'shard_name': 'test', 'dedup_family': 'therapy_process', 'default_route': 'reviewed_cluster_retrieval',
        'artifacts': {'index': 'index.faiss', 'ids': 'ids.jsonl'},
    }]}))
    hits, trace = module.faiss_shard_search(tmp_path, {}, [1., 0.], 1, {'matches_filter'})
    assert [item['retrieval_id'] for item in hits] == ['matches_filter']
    assert trace['metadata_prefilter'] is True


@pytest.mark.parametrize('failure', ['json', 'enum'])
def test_planner_gets_real_index_inventory_and_retries_validation(tmp_path, monkeypatch, failure):
    from types import SimpleNamespace
    from domains.theraskill.hybrid_retrieval import TheraSkillHybridRetriever
    root = Path(__file__).parents[1]
    output = tmp_path / 'index'
    build_index(DEMO, output)
    adapter = object.__new__(TheraSkillHybridRetriever)
    adapter.project_root = root
    planner = adapter._load_retriever().PLANNER
    plan = planner.fallback_plan('emotional-support', 'empathy')
    plan['metadata_filters']['default_route'] = []
    plan['metadata_filters']['dedup_family'] = ['style_response', 'therapy_process']
    prompts = []
    def chat(config, prompt):
        prompts.append(prompt)
        value = json.loads(json.dumps(plan))
        if len(prompts) == 1:
            if failure == 'json':
                return {'choices': [{'message': {'content': '{"unfinished":'}}]}
            value['metadata_filters']['primary_knowledge_type'] = ['invented_type']
        return {'choices': [{'message': {'content': json.dumps(value)}}]}
    monkeypatch.setattr(planner, 'chat_completion', chat)
    args = SimpleNamespace(db=str(output / 'phase6_retrieval_fts.sqlite'), model_config='unused.yaml',
                           prompt=str(root / 'prompts/AdvancedRetrieval/retrieval_query_planner_zh_en.md'),
                           skill='emotional-support', query='empathy', allow_degraded_fallback=False, validation_attempts=2)
    result = planner.build_plan(args)
    assert 'demonstration' in prompts[0] and '{{index_inventory}}' not in prompts[0]
    assert 'failed validation' in prompts[1]
    assert result['planner_trace']['planner_status'] == 'complete'
    assert result['planner_trace']['fallback_used'] is False
    assert len(result['planner_trace']['attempts']) == 2
    monkeypatch.setattr(planner, 'chat_completion', lambda *args: {'choices': [{'message': {'content': '{'}}]})
    with pytest.raises(ValueError):
        planner.build_plan(args)
