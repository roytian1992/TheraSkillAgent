"""Shared validation for saved courses and generated-course handoff."""
from copy import deepcopy
import json
import re
import unicodedata

STATUSES = {'observed', 'not_observed', 'not_applicable'}


def _text(value, name, limit=2000, *, optional=False):
    if not isinstance(value, str) or (not value.strip() and not optional) or len(value) > limit:
        raise ValueError(f'{name} must be {"a" if optional else "a non-empty"} string of at most {limit} characters')
    return value.strip()


def _rows(value, name, limit):
    if not isinstance(value, list) or len(value) > limit or any(not isinstance(item, dict) for item in value):
        raise ValueError(f'{name} must contain at most {limit} objects')
    return value


def _strings(value, name, limit=32):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f'{name} must be a list of at most {limit} strings')
    values = [_text(item, name, 500) for item in value]
    if len(set(values)) != len(values):
        raise ValueError(f'{name} must not contain duplicates')
    return values


def normalize_course_content(objectives, learning_units, knowledge_references):
    """Preserve teacher-authored metadata while rejecting unusable saved tasks."""
    if len(json.dumps([objectives, learning_units, knowledge_references], ensure_ascii=False)) > 200000:
        raise ValueError('Course teaching content is too large')
    normalized, ids, descriptions = [], set(), set()
    for raw in _rows(objectives, 'learning_objectives', 12):
        row = deepcopy(raw)
        row['id'] = _text(row.get('id'), 'objective id', 120)
        if row['id'] in ids:
            raise ValueError('Learning objectives need unique IDs')
        ids.add(row['id'])
        row['description'] = _text(row.get('description'), 'objective description')
        # Match the planner's objective comparison before accepting a course or
        # queuing an expensive generation job. Exact teacher objectives cannot
        # later be rewritten by the planner to resolve duplicate descriptions.
        comparable = unicodedata.normalize('NFKC', row['description']).casefold()
        comparable = re.sub(r'^(?:case|scenario|patient|案例|情景|患者)\s*[#第]?\d+[:：.、\s-]*', '', comparable)
        comparable = ''.join(re.findall(r'\w+', comparable))
        if not comparable:
            raise ValueError('Learning objective descriptions must contain meaningful text')
        if comparable in descriptions:
            raise ValueError('Learning objective descriptions must be distinct')
        descriptions.add(comparable)
        row['observable_behaviors'] = _strings(row.get('observable_behaviors', []), 'observable_behaviors', 8)
        row['observation_statuses'] = _strings(row.get('observation_statuses', ['observed', 'not_observed', 'not_applicable']), 'observation_statuses', 3)
        if not row['observation_statuses'] or set(row['observation_statuses']) - STATUSES:
            raise ValueError('observation_statuses must use observed, not_observed or not_applicable')
        row['rubric'] = _rows(row.get('rubric', []), 'rubric', 12)
        levels = set()
        for anchor in row['rubric']:
            level = _text(anchor.get('level'), 'rubric level', 80)
            _text(anchor.get('description'), 'rubric description')
            if level in levels:
                raise ValueError('Rubric levels must be unique')
            levels.add(level)
            if 'score' in anchor:
                score = anchor['score']
                if isinstance(score, bool) or not isinstance(score, (int, float)) or not 1 <= score <= 5:
                    raise ValueError('Rubric score must be between 1 and 5')
        row['rubric_version'] = _text(row.get('rubric_version', 'teacher_defined_v1'), 'rubric_version', 160)
        if 'knowledge_queries' in row:
            row['knowledge_queries'] = _strings(row['knowledge_queries'], 'knowledge_queries', 12)
        if 'safety_critical' in row and not isinstance(row['safety_critical'], bool):
            raise ValueError('safety_critical must be a boolean')
        normalized.append(row)
    refs, ref_ids = [], set()
    for raw in _rows(knowledge_references, 'knowledge_references', 12):
        row = deepcopy(raw)
        row['unit_id'] = _text(row.get('unit_id'), 'knowledge unit_id', 160)
        row['quote'] = _text(row.get('quote') or row.get('text'), 'knowledge quote', 12000)
        if row['unit_id'] in ref_ids:
            raise ValueError('Knowledge references must have unique IDs')
        ref_ids.add(row['unit_id'])
        refs.append(row)
    if not isinstance(learning_units, dict) or set(learning_units) - {'knowledge_cards', 'micro_practices'}:
        raise ValueError('learning_units must contain knowledge_cards and/or micro_practices')
    units = deepcopy(learning_units)
    for kind, id_key in [('knowledge_cards', 'card_id'), ('micro_practices', 'practice_id')]:
        seen = set()
        for row in _rows(units.get(kind, []), kind, 64):
            row[id_key] = _text(row.get(id_key), id_key, 160)
            if row[id_key] in seen:
                raise ValueError(f'{id_key} must be unique')
            seen.add(row[id_key])
            if row.get('competency_id') not in ids:
                raise ValueError(f'{kind} competency_id must match a course objective')
            row['title'] = _text(row.get('title'), f'{kind} title', 300)
            refs_used = _strings(row.get('knowledge_unit_ids', []), 'knowledge_unit_ids', 60)
            if set(refs_used) - ref_ids:
                raise ValueError('Teaching units may reference only course knowledge units')
            row['knowledge_unit_ids'] = refs_used
            required = ('scenario_text', 'prompt', 'target_behavior') if kind == 'micro_practices' else ('why_it_matters', 'when_to_use', 'common_misuse')
            for key in required:
                row[key] = _text(row.get(key), key, 5000)
            for key in ('hint', 'reflection_prompt', 'example_response', 'example_discussion'):
                if key in row:
                    row[key] = _text(row[key], key, 5000, optional=True)
            if kind == 'micro_practices':
                objective = next(item for item in normalized if item['id'] == row['competency_id'])
                if row['target_behavior'] not in objective['observable_behaviors']:
                    raise ValueError('Micro-practice target_behavior must match an observable course behavior')
    return {'learning_objectives': normalized, 'learning_units': units, 'knowledge_references': refs}
