"""Streaming Phase 6 and declaratively mapped knowledge inputs."""
from __future__ import annotations
import json
from pathlib import Path
from .projection import retrieval_row

PROJECTION_VERSION = 'theraskill_projection_v2'


def _field(row, path, default=None):
    value = row
    for part in path.split('.'):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value


def project(row, line, mapping=None):
    if not isinstance(row, dict):
        raise ValueError(f'line {line}: knowledge must be an object')
    if 'release_unit_id' not in row:
        mapping = mapping or {}
        get = lambda key, default=None: _field(row, mapping.get(key, key), default)
        approved = get('review_status') in {'approved', 'released'}
        policy = get('retrieval_policy', {})
        if not isinstance(policy, dict):
            raise ValueError(f'line {line}: retrieval_policy must be an object')
        for key in ('retrieval_eligible', 'embedding_eligible'):
            if key in policy and not isinstance(policy[key], bool):
                raise ValueError(f'line {line}: {key} must be boolean')
        row = {
            'release_unit_id': get('id'), 'release_unit_type': 'reviewed_cluster' if approved else 'candidate',
            'dedup_family': get('family', 'therapy_process'), 'canonical_knowledge_type': get('type', 'knowledge_claim'),
            'content': {'canonical_label': get('title', ''), 'summary': get('text', '')},
            'dataset_distribution': {str(get('source', 'custom')): 1},
            'knowledge_type_distribution': {str(get('type', 'knowledge_claim')): 1},
            'language_distribution': {str(get('language', 'auto')): 1},
            'license_constraints_distribution': {str(get('license', 'unspecified')): 1},
            'evidence_tier_distribution': {str(get('evidence_tier', 'unspecified')): 1},
            'quality': {'registry_status': get('review_status', 'candidate')},
            'retrieval_policy': {'retrieval_eligible': approved and policy.get('retrieval_eligible') is True,
                                 'embedding_eligible': approved and policy.get('embedding_eligible') is True,
                                 'default_route': policy.get('default_route', 'reviewed_cluster_retrieval' if approved else 'accounting_only_requires_review')},
        }
    if not isinstance(row.get('release_unit_id'), str) or not row['release_unit_id'].strip():
        raise ValueError(f'line {line}: nonempty stable knowledge ID required')
    for key in ('content', 'retrieval_policy'):
        if not isinstance(row.get(key), dict):
            raise ValueError(f'line {line}: {key} must be an object')
    policy = row['retrieval_policy']
    for key in ('retrieval_eligible', 'embedding_eligible'):
        if key in policy and not isinstance(policy[key], bool):
            raise ValueError(f'line {line}: {key} must be boolean')
    result = retrieval_row(row, line)
    if not result['retrieval_eligible']:
        result['embedding_eligible'] = False
    if result['retrieval_eligible'] and not result['retrieval_text']:
        raise ValueError(f'line {line}: eligible knowledge has no retrieval text')
    return result


def rows(path, mapping=None):
    with Path(path).open(encoding='utf-8-sig') as f:
        for line_no, line in enumerate(f, 1):
            if line.strip():
                try:
                    yield project(json.loads(line), line_no, mapping)
                except (TypeError, KeyError, json.JSONDecodeError) as exc:
                    raise ValueError(f'line {line_no}: invalid knowledge record: {exc}') from exc
