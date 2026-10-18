"""Behavior reports distinguish execution, model scores and human decisions."""
import copy
import pytest
from theraskill_agent import behavior_eval as b
from theraskill_agent.skills import SkillManager


def report(report_id='r',score=4):
    suite=b.load_suite();suite['cases']=suite['cases'][:1];case=suite['cases'][0]
    target={'index_id':'idx_1','skill_checksums':{},'provider_sha256':'p','evaluator_source_sha256':'s'}
    return {'id':report_id,'suite':suite,'suite_sha256':b.fingerprint(suite),'target':target,
            'items':[{'id':case['id'],'status':'completed','checks':{'grounded':True},'scores':{k:score for k in ['relevance','grounding','task_completion','language','boundary']}}],
            'machine_passed':True,'human_review':{'status':'pending'}}


def test_baseline_cannot_pass_without_comparison_or_human():
    r=report();assert b.gate_status(r)['status']=='blocked'
    assert set(b.gate_status(r)['reasons'])=={'baseline_required','human_review_required'}


def test_identical_suite_required_and_regression_is_blocked():
    old=report('baseline',4);new=report('candidate',3)
    new['comparison']=b.compare_reports(new,old);new['human_review']={'status':'approved'}
    assert 'behavior_regression' in b.gate_status(new)['reasons']
    changed=copy.deepcopy(old);changed['suite_sha256']='different'
    with pytest.raises(ValueError,match='different suite'):b.compare_reports(new,changed)


def test_missing_failed_case_is_not_a_comparable_success():
    old=report();new=report('new');new['items'][0]={'id':old['items'][0]['id'],'status':'failed','error':'timeout'}
    comparison=b.compare_reports(new,old)
    assert not comparison['all_comparable'] and comparison['mean_delta'] is None


def test_approval_is_bound_to_report_and_target(tmp_path,monkeypatch):
    monkeypatch.setattr(b,'_root',lambda:tmp_path)
    r=report();r['comparison']=b.compare_reports(r,report('base'));b.write_report(r)
    monkeypatch.setattr(b,'target_snapshot',lambda options:(r['target'],None))
    approved=b.review_report('r','approved','Fixture review only','unit reviewer')
    assert approved['gate']['status']=='passed'
    assert b.verify_gate('r',{'index_id':'idx_1'})['id']=='r'
    with pytest.raises(ValueError,match='different version'):b.verify_gate('r',{'index_id':'idx_2'})
    approved['items'][0]['scores']['relevance']=1;b.write_report(approved)
    with pytest.raises(ValueError,match='changed after human review'):b.verify_gate('r',{'index_id':'idx_1'})


def test_invalid_citations_are_failed_even_with_positive_judge(monkeypatch):
    monkeypatch.setattr(b,'_hits',lambda *a:{'matches':[{'retrieval_id':'real','dedup_family':'therapy_process'}]})
    def model(system,payload):
        if 'scores' in system:
            return {'scores':{k:5 for k in ['relevance','grounding','task_completion','language','boundary']},'reasons':{k:'fixture' for k in ['relevance','grounding','task_completion','language','boundary']}},{'fixture':True}
        return {'answer':'Example response','evidence_ids':['fabricated'],'limitations':[]},{'fixture':True}
    result=b.evaluate_case(b.load_suite()['cases'][0],None,call_model=model)
    assert not result['checks']['valid_citations']


def test_suite_rejects_nonfinite_gate_threshold():
    suite=b.load_suite();suite['minimum_score']=float('nan')
    with pytest.raises(ValueError):b.load_suite(suite)


def test_mismatched_baseline_is_rejected_before_any_model_call(tmp_path,monkeypatch):
    monkeypatch.setattr(b,'_root',lambda:tmp_path)
    baseline=report('base');baseline['suite_sha256']='other';b.write_report(baseline)
    monkeypatch.setattr(b,'target_snapshot',lambda *_:pytest.fail('No provider or retrieval access is needed'))
    with pytest.raises(ValueError,match='matching baseline'):b.run_suite({'baseline_id':'base'})


def test_reference_changes_invalidate_reviewed_skill_bundle(tmp_path,monkeypatch):
    skill=tmp_path/'skill';skill.mkdir();(skill/'SKILL.md').write_text('---\nname: fixture\n---\n# Test\n')
    ref=skill/'reference.md';ref.write_text('Original evidence requirements')
    original=SkillManager().inspect(skill)
    monkeypatch.setattr(b,'_root',lambda:tmp_path/'reports')
    r=report();r['target']['skill_checksums']={str(skill):original.bundle_checksum}
    r['comparison']=b.compare_reports(r,report('baseline'));b.write_report(r)
    monkeypatch.setattr(b,'target_snapshot',lambda options:(r['target'],None))
    b.review_report('r','approved','Fixture approval','unit reviewer')
    assert b.verify_gate('r',{})['id']=='r'
    ref.write_text('Changed evidence requirements')
    assert SkillManager().inspect(skill).checksum==original.checksum
    with pytest.raises(ValueError,match='references changed'):b.verify_gate('r',{})


def test_embedding_setting_changes_block_approval_before_recording_human_decision(tmp_path,monkeypatch):
    monkeypatch.setattr(b,'_root',lambda:tmp_path)
    r=report();r['target']['embedding_sha256']='old';r['comparison']=b.compare_reports(r,report('base'));b.write_report(r)
    current={**r['target'],'embedding_sha256':'new'}
    monkeypatch.setattr(b,'target_snapshot',lambda options:(current,None))
    with pytest.raises(ValueError,match='Model settings'):b.review_report('r','approved','Fixture','unit reviewer')
    assert b.get_report('r')['human_review']['status']=='pending'
