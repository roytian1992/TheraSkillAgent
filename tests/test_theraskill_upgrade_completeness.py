"""User workflow regressions; all model responses here are explicit fixtures."""
import io
import json
from pathlib import Path
import zipfile
import pytest
from theraskill_agent import source_adapters as sources


def test_streamed_text_preserves_every_character_and_offsets(tmp_path,monkeypatch):
    monkeypatch.setattr(sources,'text_policy',lambda:{'max_chars':512,'overlap':50,'version':'fixture'})
    text='患者：有错别字不修改。\r\n医师：原文证据。\r\n'*350
    path=tmp_path/'asr.srt';path.write_bytes(text.encode())
    records=list(sources.get_adapter(path).iter_records(path))
    assert len(records)>10
    covered=set()
    for record in records:
        start,end=record.metadata['char_start'],record.metadata['char_end']
        assert record.payload==text[start:end] and len(record.payload)<=512
        covered.update(range(start,end))
    assert covered==set(range(len(text)))
    sample=sources.sample_source(path,5)
    assert sample[0].record_id==records[0].record_id
    assert sample[-1].record_id==records[-1].record_id
    assert len({r.record_id for r in sample})==5


def test_review_windows_cover_all_turns_and_huge_turn(tmp_path):
    from theraskill_agent.transcript import parse_transcript,analyze_transcript
    from theraskill_agent.reviews import build_review_plan
    document=parse_transcript('patient: '+('raw spelling errror '*1400)+'\ntherapist: Please clarify.\npatient: Last turn.')
    plans=build_review_plan(document,analyze_transcript(document),skills=['transcript-quality-reviewer'])
    assert len(plans)>1 and all(not p['coverage']['sample_only'] for p in plans)
    turns=[t for p in plans for t in p['user_payload']['turns']]
    assert {t['turn_id'] for t in turns}=={t.turn_id for t in document.turns}
    first=[t for t in turns if t['turn_id']==document.turns[0].turn_id]
    assert ''.join(t['raw_text'] for t in first)==document.turns[0].raw_text


def test_skill_preflight_reports_missing_requirements_before_call(tmp_path):
    from theraskill_agent.skill_readiness import check_skill
    from theraskill_agent.skill_execution import next_skill_step
    folder=tmp_path/'skill';folder.mkdir()
    (folder/'SKILL.md').write_text('---\nname: unavailable\nruntime:\n  required_tools: [missing.tool]\ndependencies: [python:fixture_nonexistent_theraskill]\n---\n# Test')
    ready=check_skill(folder)
    assert not ready['ready'] and ready['missing_tools']==['missing.tool']
    assert ready['missing_dependencies']==['python:fixture_nonexistent_theraskill']
    with pytest.raises(ValueError,match='not ready'):
        next_skill_step({'inputs':{'skill':str(folder)},'checkpoints':{}},call_model=lambda *_:pytest.fail('No model call'))


def test_context_budget_pauses_without_losing_history():
    from theraskill_agent.skill_execution import next_skill_step
    task={'goal':'Test budget','language':'en','inputs':{'skill':'emotional-support','text':'x'*10000,'max_context_chars':4000},'checkpoints':{'skill_history':[{'user_response':'retained'}]}}
    result=next_skill_step(task,call_model=lambda *_:pytest.fail('Budget should stop model call'))
    assert result['decision']['action']=='ask' and result['decision']['budget_pause']
    assert task['checkpoints']['skill_history']==[{'user_response':'retained'}]


def test_runtime_evaluation_executes_tools_and_requires_every_family(tmp_path,monkeypatch):
    from theraskill_agent import behavior_eval as b,skill_execution as execution
    monkeypatch.setenv('THERASKILL_AGENT_ROOT',str(tmp_path))
    calls=[]
    def tool(name,args,**kwargs):
        calls.append(name)
        return {'status':'success','data':{'matches':[{'retrieval_id':'real','dedup_family':'therapy_process'}]}}
    monkeypatch.setattr(execution,'execute_tool',tool)
    def model(system,payload):
        if 'scores' in system:
            dims=['relevance','grounding','task_completion','language','boundary']
            return {'scores':dict.fromkeys(dims,5),'reasons':dict.fromkeys(dims,'fixture')},{'model':'fixture'}
        if not payload['history']:
            return {'action':'tool','tool':'theraskill_knowledge_search','arguments':{'query':'fixture'}},{'model':'fixture'}
        return {'action':'finish','result':{'answer':'Fixture answer','evidence_ids':['real']}},{'model':'fixture'}
    case=b.load_suite()['cases'][0]
    result=b.evaluate_case(case,None,call_model=model)
    assert calls==['theraskill_knowledge_search']
    assert result['checks']['runtime_completed'] and result['checks']['valid_citations']
    assert not result['checks']['expected_family_recalled']
    assert result['execution']['events'] and Path(result['execution']['trace_directory'],'trace.json').is_file()


def test_zip_knowledge_and_simulation_install_in_fresh_workspace(tmp_path,monkeypatch):
    from theraskill_agent import resources
    from domains.theraskill.runtime import simulation_root
    monkeypatch.setenv('THERASKILL_AGENT_ROOT',str(tmp_path/'workspace'))
    root=Path(__file__).resolve().parents[1]
    archive=tmp_path/'knowledge.zip'
    record=resources.pack_resource(root/'examples/knowledge/demo.jsonl',archive,package_id='demo',kind='knowledge',license_id='fixture')
    installed=resources.install_package(archive,expected_sha256=record['sha256'])
    assert Path(installed['path'],'knowledge.jsonl').is_file()
    source=tmp_path/'simulation';(source/'scenarios').mkdir(parents=True)
    (source/'scenarios/scenario_registry.jsonl').write_text('{"scenario_id":"fixture"}\n')
    assets=tmp_path/'simulation.zip'
    resources.pack_resource(source,assets,package_id='sim-fixture',kind='simulation',license_id='fixture')
    resources.install_package(assets)
    assert (simulation_root()/'scenarios/scenario_registry.jsonl').is_file()
    with pytest.raises(ValueError,match='immutable'):resources.install_package(assets)


def test_zip_path_escape_is_rejected(tmp_path,monkeypatch):
    from theraskill_agent.resources import install_package
    monkeypatch.setenv('THERASKILL_AGENT_ROOT',str(tmp_path/'workspace'))
    archive=tmp_path/'bad.zip'
    manifest={'schema_version':'theraskill_resource_package_v1','package_id':'bad','kind':'simulation','files':{'../outside':{'size':1,'sha256':'0'*64}}}
    with zipfile.ZipFile(archive,'w') as bundle:
        bundle.writestr('package.json',json.dumps(manifest));bundle.writestr('../outside','x')
    with pytest.raises(ValueError,match='contained'):install_package(archive)
    assert not (tmp_path/'outside').exists()


def test_unjudged_relevance_is_not_a_zero_score():
    from theraskill_agent.retrieval_eval import rank_metrics
    assert rank_metrics(['x'],{})['ndcg_at_k'] is None
    metrics=rank_metrics(['known','unknown'],{'known':3,'other':2})
    assert metrics['status']=='partially_judged' and metrics['recall_at_k']==.5
    with pytest.raises(ValueError):rank_metrics(['known'],{'known':True})


def test_final_output_repair_uses_returned_document_id_and_retains_rejection():
    from theraskill_agent.skill_execution import next_skill_step, SkillOutputError
    history=[{'tool':'theraskill_transcript','result':{'status':'success','data':{'document':{'document_id':'actual-doc','turns':[{'turn_id':'turn-1'}]}}}}]
    task={'goal':'Describe only the supplied document','language':'en','inputs':{'skill':'transcript-normalizer'},'checkpoints':{'skill_history':history}}
    calls=[]
    def repair(system,payload):
        calls.append(payload)
        ids=['fabricated'] if len(calls)==1 else ['actual-doc','turn-1']
        return {'action':'finish','result':{'answer':'Fixture grounded answer','evidence_ids':ids}},{'fixture':True}
    result=next_skill_step(task,call_model=repair)
    assert len(calls)==2 and calls[1]['validation_errors']
    assert calls[0]['valid_evidence_ids']==['actual-doc','turn-1']
    assert result['provenance']['repair']['original_decision']['result']['evidence_ids']==['fabricated']
    calls.clear()
    def invalid(*_):
        calls.append(True)
        return {'action':'finish','result':{'answer':'Fixture','evidence_ids':['fabricated']}},{'fixture':True}
    with pytest.raises(SkillOutputError) as rejected:next_skill_step(task,call_model=invalid)
    assert len(calls)==2 and rejected.value.output['result']['evidence_ids']==['fabricated']


def test_evaluation_does_not_auto_approve_local_script(tmp_path,monkeypatch):
    from theraskill_agent.evaluation_runtime import execute_case
    from theraskill_agent.skills import SkillManager
    monkeypatch.setenv('THERASKILL_AGENT_ROOT',str(tmp_path))
    def model(*_):return {'action':'tool','tool':'skill.run_script','arguments':{'path':'scripts/run.py'}},{'fixture':True}
    case={'id':'script','query':'Must not auto approve','language':'en'}
    result=execute_case(case,None,selected='transcript-normalizer',call_model=model,manager=SkillManager())
    assert result['status']=='needs_approval' and result['tools']==[]
    assert result['task']['stage']=='tool_approval'


def test_skill_script_containment_and_complete_output(tmp_path,monkeypatch):
    from theraskill_agent.skill_readiness import run_script
    monkeypatch.setenv('THERASKILL_AGENT_ROOT',str(tmp_path/'workspace'))
    skill=tmp_path/'skill';(skill/'scripts').mkdir(parents=True)
    script=skill/'scripts/run.py';script.write_text('print("x"*20000)')
    result=run_script({'path':'scripts/run.py','arguments':[]},skill_path=skill)
    assert result['returncode']==0 and Path(result['stdout']).stat().st_size==20001
    outside=tmp_path/'outside.py';outside.write_text('raise Exception("must not execute")')
    (skill/'scripts/escape.py').symlink_to(outside)
    with pytest.raises(ValueError,match='inside'):run_script({'path':'scripts/escape.py'},skill_path=skill)
