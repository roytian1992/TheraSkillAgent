"""Source-text hydration after metadata-only hybrid recall, with one index binding."""
import hashlib
import json
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

from domains.theraskill.hybrid_retrieval import HybridRetrievalError
from plugins.theraskill import retrieval
from theraskill_agent import resources


@pytest.fixture
def indexed(tmp_path, monkeypatch):
    directory = tmp_path / 'index'
    directory.mkdir()
    db = directory / 'retrieval.sqlite'
    passage = 'Reflect the emotion and invite correction. Exact source text—not a compact label.'
    with sqlite3.connect(db) as connection:
        connection.executescript('''
            CREATE TABLE retrieval_meta(retrieval_id TEXT PRIMARY KEY, release_unit_id TEXT,
                dedup_family TEXT, default_route TEXT, retrieval_eligible INTEGER, retrieval_text_chars INTEGER);
            CREATE VIRTUAL TABLE retrieval_fts USING fts5(retrieval_id UNINDEXED, retrieval_text);
        ''')
        connection.execute('INSERT INTO retrieval_meta VALUES (?,?,?,?,?,?)',
                           ('retrieval-1', 'unit-1', 'therapy_process', 'reviewed_cluster_retrieval', 1, len(passage)))
        connection.execute('INSERT INTO retrieval_fts VALUES (?,?)', ('retrieval-1', passage))
    manifest = {'index_id': 'fixture-index', 'input': {'mode': 'hybrid', 'knowledge_sha256': 'a' * 64},
                'artifacts': {'sqlite_fts': 'retrieval.sqlite', 'metadata_v2': 'metadata.sqlite',
                              'compact_label': 'labels.sqlite', 'faiss_shards': 'faiss/manifest.json'}}
    selected = (directory, manifest)
    monkeypatch.setattr(resources, 'active_index', lambda: selected)
    matches = [{'retrieval_id': 'retrieval-1', 'release_unit_id': 'unit-1', 'dedup_family': 'therapy_process',
                'default_route': 'reviewed_cluster_retrieval', 'canonical_label': 'Reflect emotion',
                'text_preview': '', 'evidence_role': 'therapy_process_pattern',
                'allowed_use': ['identify process choice'], 'forbidden_use': ['diagnose a person']}]

    class MetadataOnlyRetriever:
        def __init__(self, project, paths):
            self.paths = paths

        def search(self, query, **kwargs):
            return {'items': deepcopy(matches), 'source_index': str(self.paths.sqlite_fts)}

    monkeypatch.setattr(retrieval, 'TheraSkillHybridRetriever', MetadataOnlyRetriever)
    return directory, manifest, db, passage, matches


def test_managed_search_hydrates_final_hits_and_preserves_policy_and_identity(indexed):
    _, _, _, passage, original = indexed
    result = retrieval.search_managed_knowledge('reflect emotion', skill='therapy-response-reviewer', limit=6)
    hit = result['matches'][0]
    assert hit['retrieval_text'] == passage and hit['text_preview'] == passage
    for key in ('retrieval_id', 'release_unit_id', 'allowed_use', 'forbidden_use', 'evidence_role'):
        assert hit[key] == original[0][key]
    assert hit['source_index_id'] == result['index_id'] == 'fixture-index'
    assert hit['knowledge_sha256'] == result['knowledge_sha256'] == 'a' * 64
    assert hit['text_sha256'] == hashlib.sha256(passage.encode()).hexdigest()
    assert not hit['text_truncated']
    assert result['diagnostics']['timings']['source_hydration_seconds'] >= 0
    assert original[0]['text_preview'] == ''  # Never mutate a retriever's cached hit.


def test_selection_is_resolved_once_even_if_active_index_changes(indexed, monkeypatch):
    directory, manifest, _, _, _ = indexed
    reads = []

    def changing_index():
        reads.append(1)
        if len(reads) > 1:
            pytest.fail('Selection was re-read after binding search and source lookup')
        return directory, manifest

    monkeypatch.setattr(resources, 'active_index', changing_index)
    assert retrieval.search_managed_knowledge('emotion', skill='therapy-response-reviewer', limit=1)['count'] == 1
    assert len(reads) == 1


def test_lexical_fallback_uses_the_same_hydration(indexed, monkeypatch):
    _, _, db, passage, matches = indexed

    def unavailable(*args, **kwargs):
        raise HybridRetrievalError('Synthetic optional dependency unavailable')

    monkeypatch.setattr(retrieval, 'TheraSkillHybridRetriever', unavailable)
    monkeypatch.setattr('domains.theraskill.hybrid_retrieval.search_policy_lexical',
                        lambda *a, **kw: {'items': matches, 'source_index': str(db)})
    result = retrieval.search_managed_knowledge('emotion', skill='therapy-response-reviewer', limit=1)
    assert result['retrieval_mode'] == 'policy_lexical_fallback'
    assert result['matches'][0]['retrieval_text'] == passage


def test_mismatched_unit_identity_and_overlay_hits_never_borrow_selected_index_text(indexed):
    _, _, _, _, matches = indexed
    matches[0]['release_unit_id'] = 'unit-from-other-release'
    matches.append({'retrieval_id': 'overlay-only', 'release_unit_id': 'overlay-unit', 'text_preview': 'Unbound overlay text'})
    result = retrieval.search_managed_knowledge('emotion', skill='therapy-response-reviewer', limit=2)
    assert result['matches'] == []
    assert any('omitted' in warning for warning in result['warnings'])


def test_different_search_source_is_an_error_not_a_cross_release_fallback(indexed, monkeypatch, tmp_path):
    class WrongSource:
        def __init__(self, *args):
            pass

        def search(self, *args, **kwargs):
            return {'source_index': str(tmp_path / 'another-index.sqlite'), 'items': []}

    monkeypatch.setattr(retrieval, 'TheraSkillHybridRetriever', WrongSource)
    with pytest.raises(retrieval.RetrievalError, match='different knowledge index'):
        retrieval.search_managed_knowledge('emotion', skill='therapy-response-reviewer', limit=1)


def test_historical_unaligned_rowids_use_verified_same_database_text(indexed):
    _, _, db, passage, _ = indexed
    with sqlite3.connect(db) as connection:
        connection.execute('DELETE FROM retrieval_fts')
        connection.execute('INSERT INTO retrieval_fts(rowid,retrieval_id,retrieval_text) VALUES (?,?,?)',
                           (1, 'different-id', 'Wrong text at the guessed rowid'))
        connection.execute('INSERT INTO retrieval_fts(rowid,retrieval_id,retrieval_text) VALUES (?,?,?)',
                           (30, 'retrieval-1', passage))
    result = retrieval.search_managed_knowledge('emotion', skill='therapy-response-reviewer', limit=1)
    assert result['matches'][0]['retrieval_text'] == passage


def test_hydration_returns_bounded_exact_passages(indexed):
    _, _, db, _, _ = indexed
    source = 'An exact passage. ' * 500
    with sqlite3.connect(db) as connection:
        connection.execute('UPDATE retrieval_fts SET retrieval_text=?', (source,))
        connection.execute('UPDATE retrieval_meta SET retrieval_text_chars=?', (len(source),))
    hit = retrieval.search_managed_knowledge('emotion', skill='therapy-response-reviewer', limit=1)['matches'][0]
    assert hit['retrieval_text'] == source[:6000]
    assert hit['text_preview'] == source[:1200]
    assert hit['text_truncated']


def test_actual_judge_receives_and_validates_hydrated_source(indexed, tmp_path):
    from domains.theraskill.theraskill_simulation.learning_judge import LearningJudge

    class Client:
        model = 'offline-evidence-test'

        def chat(self, prompt, payload, **kwargs):
            reference = payload['references'][0]
            observation = {'observation': 'observed', 'rating': 4, 'rationale': 'Invites correction.',
                           'transcript_evidence': [{'turn_id': 't1', 'quote': 'Is that right?'}],
                           'knowledge_evidence': [{'unit_id': reference['unit_id'], 'quote': reference['text']}],
                           'knowledge_note': ''}
            return json.dumps({'objective_id': payload['objective']['objective_id'], **observation,
                               'behaviors': [], 'strengths': ['Checks understanding'], 'next_practice': ['Invite a correction']})

    judge = LearningJudge(tmp_path, tmp_path / 'unused.yaml', client=Client(), retrieve=retrieval.search_managed_knowledge)
    result = judge.evaluate(objectives=[{'id': 'empathy', 'description': 'Check the reflection'}],
                            transcript=[{'turn_id': 't1', 'role': 'trainee', 'text': 'Is that right?'}])
    assert result['evidence_mode'] == 'knowledge_grounded'
    assert result['retrieval']['reference_count'] == 1
    assert result['references'][0]['text'] == indexed[3]
    assert result['objectives'][0]['knowledge_evidence'][0]['unit_id'] == 'unit-1'


def test_blueprint_seed_receives_actual_source_passage(indexed):
    from domains.theraskill.theraskill_simulation.pedagogy import design_course_seed
    bundle = retrieval.search_managed_knowledge('emotion', skill='therapy-response-reviewer', limit=6)
    seed = design_course_seed(template_id='foundation_first_session', knowledge_units=bundle['matches'])
    assert seed.knowledge_references[0].unit_id == 'unit-1'
    assert seed.knowledge_references[0].quote == indexed[3]
