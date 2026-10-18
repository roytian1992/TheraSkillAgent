"""Unit fixtures for pause/resume, approvals and durable tool checkpoints."""
import os
import time
from pathlib import Path
import pytest
from theraskill_agent.task_store import TaskStore,TaskConflict
from theraskill_agent.task_runtime import TaskEngine
from theraskill_agent.skills import SkillManager


def wait(engine,task_id,statuses):
    until=time.monotonic()+5
    while time.monotonic()<until:
        value=engine.store.get(task_id)
        if value['status'] in statuses:return value
        time.sleep(.01)
    raise AssertionError(engine.store.get(task_id))


def test_checkpoint_rejects_stale_updates_and_preserves_history(tmp_path):
    store=TaskStore(tmp_path/'tasks.sqlite');a=store.create('skill','goal');stale=dict(a)
    a['stage']='inspection';saved=store.save(a)
    with pytest.raises(TaskConflict):store.save(stale)
    assert store.get(a['id'])['stage']=='inspection' and saved['revision']==1
    assert store.events(a['id'])[0]['kind']=='created'


def test_skill_question_resume_and_tool_failure_retry_does_not_repeat_completed_tools(tmp_path):
    record=SkillManager().inspect('transcript-normalizer').to_dict();calls=[]
    def decide(t):
        history=t['checkpoints'].get('skill_history',[])
        if not history:decision={'action':'ask','question':'Confirm roles','fields':[{'name':'role','type':'string'}]}
        elif not any('tool' in v for v in history):decision={'action':'tool','tool':'text.normalize','arguments':{'text':'a  b'}}
        else:decision={'action':'finish','result':{'answer':'a b'}}
        return {'decision':decision,'provenance':{'fixture':True},'skill_snapshot':record}
    def tool(name,args,**kwargs):
        calls.append((name,args))
        if len(calls)==1:raise RuntimeError('fixture transport failure')
        return {'text':'a b'}
    e=TaskEngine(TaskStore(tmp_path/'tasks.sqlite'),skill_step=decide,tool=tool)
    try:
        t=e.create('skill','normalize','en',{'skill':'transcript-normalizer'})
        t=wait(e,t['id'],{'needs_input'})
        assert t['stage']=='skill_question'
        e.action(t['id'],'respond',{'role':'therapist'},t['revision'])
        t=wait(e,t['id'],{'failed'})
        assert t['checkpoints']['pending_tool']['decision']['tool']=='text.normalize'
        e.action(t['id'],'retry',revision=t['revision'])
        t=wait(e,t['id'],{'completed'})
        assert len(calls)==2 and len([h for h in t['checkpoints']['skill_history'] if 'tool' in h])==1
        assert t['checkpoints']['result']['answer']=='a b'
        assert calls[0][1]['_call_id']==calls[1][1]['_call_id']
    finally:e.close()


def test_mutating_skill_tool_waits_for_user_and_decline_never_executes(tmp_path):
    record=SkillManager().inspect('knowledge-index-manager').to_dict();executed=[]
    def decide(t):
        decision={'action':'finish','result':{'answer':'Declined'}} if any('user_response' in h for h in t['checkpoints'].get('skill_history',[])) else {'action':'tool','tool':'theraskill_resources','arguments':{'operation':'build','parameters':{}}}
        return {'decision':decision,'provenance':{},'skill_snapshot':record}
    e=TaskEngine(TaskStore(tmp_path/'t.sqlite'),skill_step=decide,tool=lambda *a,**k:executed.append(a))
    try:
        t=e.create('skill','build','en',{'skill':'knowledge-index-manager'})
        t=wait(e,t['id'],{'needs_approval'})
        assert t['stage']=='tool_approval' and not executed
        e.action(t['id'],'respond',{'approved':False},t['revision'])
        wait(e,t['id'],{'completed'});assert not executed
    finally:e.close()


def test_new_engine_does_not_interrupt_another_live_process(tmp_path):
    store=TaskStore(tmp_path/'t.sqlite');t=store.create('skill','goal');t.update(status='running',worker_pid=os.getpid());store.save(t)
    e=TaskEngine(store)
    try:assert store.get(t['id'])['status']=='running'
    finally:e.close()


def test_interrupted_task_is_explicitly_resumable(tmp_path):
    store=TaskStore(tmp_path/'t.sqlite');t=store.create('skill','goal');t.update(status='running',worker_pid=999999999);store.save(t)
    e=TaskEngine(store)
    try:
        recovered=store.get(t['id']);assert recovered['status']=='interrupted'
        assert 'Resume' in recovered['error']
    finally:e.close()


def test_unpromoted_release_cannot_advance_extraction_to_index(tmp_path):
    store=TaskStore(tmp_path/'t.sqlite');t=store.create('extraction','goal',inputs={'source_path':'x'})
    t.update(stage='governance',status='needs_input',checkpoints={'handoff':{'candidate_ids':['c1']}});t=store.save(t)
    e=TaskEngine(store,call=lambda *a,**k:{'status':'preview','candidate_ids':['c1']})
    try:
        with pytest.raises(ValueError,match='promoted'):e.action(t['id'],'respond',{'release_id':'r'},t['revision'])
        assert store.get(t['id'])['stage']=='governance'
    finally:e.close()


def test_replan_keeps_prior_failure_and_uses_new_attempt_key(tmp_path):
    store=TaskStore(tmp_path/'t.sqlite');t=store.create('extraction','goal',inputs={'source_path':'x'})
    t.update(stage='trial',status='failed',checkpoints={'source':{'info':{}},'plan':{'old':True},'trial_job':{'job_id':'old'}})
    t=store.save(t);e=TaskEngine(store)
    e.launch=lambda *_:None
    try:
        next_task=e.action(t['id'],'replan',revision=t['revision'])
        assert next_task['stage']=='propose_plan' and next_task['checkpoints']['plan_revision']==1
        assert next_task['checkpoints']['previous_plans'][0]['trial_job']['job_id']=='old'
        assert 'trial_job' not in next_task['checkpoints']
    finally:e.close()


def test_governed_workflow_builds_base_plus_delta_and_requires_activation_review(tmp_path,monkeypatch):
    import json
    from theraskill_agent import resources,behavior_eval
    from theraskill_agent.paths import data_root
    from theraskill_agent.indexing.common import digest
    monkeypatch.setenv('THERASKILL_AGENT_ROOT',str(tmp_path))
    source=Path(__file__).resolve().parents[1]/'examples/knowledge/demo.jsonl'
    base=tmp_path/'base.jsonl';base.write_bytes(source.read_bytes())
    delta=tmp_path/'delta.jsonl';row=json.loads(source.read_text().splitlines()[0])
    row['id']='fixture_delta';delta.write_text(json.dumps(row)+'\n')
    monkeypatch.setattr(resources,'active_index',lambda:(tmp_path,{'input':{'knowledge_sha256':digest(base)}}))
    store=TaskStore(tmp_path/'tasks.sqlite');t=store.create('extraction','fixture lifecycle')
    t.update(status='interrupted',stage='build_index',checkpoints={'base_knowledge':str(base),'index_mode':'lexical','release':{'delta_units_path':str(delta)}});t=store.save(t)
    def gate(report_id,expected):
        if report_id!='fixture-reviewed':raise ValueError('Fixture gate blocked')
        assert expected['index_id']
    monkeypatch.setattr(behavior_eval,'verify_gate',gate)
    e=TaskEngine(store)
    try:
        e.action(t['id'],'retry',revision=t['revision']);t=wait(e,t['id'],{'needs_approval','failed'})
        assert t['stage']=='activation',t
        merged=Path(t['checkpoints']['index']['knowledge'])
        assert len([line for line in merged.read_text().splitlines() if line.strip()])==7
        assert not (data_root()/'active-index.json').exists()
        with pytest.raises(ValueError,match='gate blocked'):e.action(t['id'],'respond',{'confirmed':True,'report_id':'not-reviewed'},t['revision'])
        e.action(t['id'],'respond',{'confirmed':True,'report_id':'fixture-reviewed'},t['revision'])
        done=wait(e,t['id'],{'completed','failed'});assert done['status']=='completed',done
        selected=json.loads((data_root()/'active-index.json').read_text());assert selected['index_id']==done['checkpoints']['index']['index_id']
    finally:e.close()


def test_local_cancellation_is_saved_when_child_service_is_unavailable(tmp_path):
    store=TaskStore(tmp_path/'tasks.sqlite');t=store.create('extraction','fixture')
    t.update(status='needs_input',stage='trial',checkpoints={'trial_job':{'job_id':'child','status':'running'}});t=store.save(t)
    def offline(*args,**kwargs):raise RuntimeError('fixture service offline')
    e=TaskEngine(store,call=offline)
    try:
        cancelled=e.action(t['id'],'cancel',revision=t['revision'])
        assert cancelled['status']=='cancelled'
        assert cancelled['checkpoints']['cancellation_errors']==['fixture service offline']
        assert 'could not be confirmed' in cancelled['error']
    finally:e.close()


def test_extraction_child_can_retry_only_once_per_explicit_resume(tmp_path):
    calls=[]
    def child(service,path,payload=None):
        if path.endswith('/retry'):
            calls.append('retry');return {'job_id':'child','status':'running'}
        return {'job_id':'child','status':'failed','error':'fixture repeated failure'}
    store=TaskStore(tmp_path/'tasks.sqlite');t=store.create('extraction','fixture')
    t.update(status='needs_input',stage='trial',attempt=1,checkpoints={'plan':{},'trial_job':{'job_id':'child','status':'failed','_task_attempt':1}});t=store.save(t)
    e=TaskEngine(store,call=child)
    try:
        with pytest.raises(RuntimeError,match='repeated failure'):e._child(t,'trial',1)
        assert calls==[]
        t['attempt']=2;t=store.save(t);t,done=e._child(t,'trial',1)
        assert not done and calls==['retry']
        with pytest.raises(RuntimeError,match='repeated failure'):e._child(t,'trial',1)
        assert calls==['retry']
    finally:e.close()
