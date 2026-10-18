"""Behavior cases execute the production task engine, never a prompt surrogate."""
from __future__ import annotations
import json
import time
import uuid
from pathlib import Path
from .paths import data_root


def execute_case(case, directory, *, selected, call_model, manager, release_id=None):
    from .task_store import TaskStore
    from .task_runtime import TaskEngine
    from .skill_execution import next_skill_step, execute_tool, tool_requires_approval
    from .resources import using_index
    folder = data_root() / 'evaluations/executions' / ('execution_' + uuid.uuid4().hex)
    folder.mkdir(parents=True)
    inputs = {'skill': selected, 'text': case.get('text', ''), 'max_steps': case.get('max_steps', 16),
              'max_context_chars': case.get('max_context_chars', 160000)}
    if case.get('source_text') is not None:
        suffix = case.get('source_suffix', '.txt')
        if suffix not in {'.txt', '.json', '.jsonl', '.csv', '.md', '.srt', '.vtt'}:
            raise ValueError('Unsupported evaluation source suffix')
        source = folder / ('source' + suffix)
        source.write_text(case['source_text'], encoding='utf-8')
        inputs['source_path'] = str(source)
    store = TaskStore(folder / 'tasks.sqlite')
    tool_records = []

    def tool(name, args, *, skill_path=None):
        start = time.perf_counter()
        # Evaluations never grant mutation/script approvals implicitly.
        if tool_requires_approval(name, args):
            raise ValueError('Evaluation requested a tool that requires explicit user approval: ' + name)
        result = execute_tool(name, args, skill_path=skill_path)
        if release_id and name == 'theraskill_knowledge_search':
            from .behavior_eval import preview_hits
            data = result.get('data', result)
            overlay = preview_hits(case, release_id)
            data['matches'] = overlay['matches'] + data.get('matches', [])
            data['preview_overlay'] = overlay.get('overlay_trace')
            data['count'] = len(data['matches'])
        tool_records.append({'tool': name, 'arguments': args, 'seconds': time.perf_counter()-start, 'result': result})
        return result

    engine = TaskEngine(store, skill_step=lambda t: next_skill_step(t, manager=manager, call_model=call_model), tool=tool)
    # Drive the same saved states synchronously. No second worker or pool waits.
    engine.launch = lambda _: None
    task = store.create('skill', case['query'], case['language'], inputs)
    start = time.perf_counter()
    answered=0
    try:
        with using_index(directory):
            engine._work(task['id'])
            responses = iter(case.get('responses', []))
            while True:
                task = store.get(task['id'])
                if task['status'] != 'needs_input':
                    break
                response = next(responses, None)
                if response is None:
                    break
                engine.action(task['id'], 'respond', response, task['revision'])
                answered+=1
                engine._work(task['id'])
    finally:
        engine.close()
    task = store.get(task['id'])
    events = store.events(task['id'])
    result = {'task_id': task['id'], 'status': task['status'], 'error': task.get('error'),
              'interaction': task.get('interaction'), 'seconds': time.perf_counter()-start,
              'events': events, 'tools': tool_records, 'trace_directory': str(folder),
              'responses_consumed':answered,
              'engine': 'TaskEngine', 'task': task}
    (folder / 'trace.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def collected_evidence(execution):
    matches, seen, warnings, diagnostics = [], set(), [], []
    for tool in execution['tools']:
        if tool['tool'] != 'theraskill_knowledge_search':
            continue
        result = tool['result']
        result = result.get('data', result)
        warnings.extend(result.get('warnings', []))
        diagnostics.append(result.get('diagnostics', {}))
        for hit in result.get('matches', []):
            key = str(hit.get('retrieval_id') or hit.get('unit_id') or hit.get('id'))
            if key not in seen:
                seen.add(key); matches.append(hit)
    return {'matches': matches, 'warnings': warnings, 'diagnostics': diagnostics,
            'execution_mode': 'production_task_engine'}
