"""Isolated gate fixtures; these are not human or model quality evaluations."""
from types import SimpleNamespace
from pathlib import Path
import pytest
from theraskill_agent.skills import SkillManager
from theraskill_agent import behavior_eval,resource_jobs
from domains.theraskill.theraskill_contribution.release import KnowledgeReleaseService
from domains.theraskill.theraskill_contribution.candidate import CandidateWorkflowError


def test_release_promotion_stops_before_mutation_without_passing_behavior_gate(monkeypatch):
    release=SimpleNamespace(status='preview',release_id='release-a',manifest_sha256='sha',candidate_ids=['candidate-a'])
    service=object.__new__(KnowledgeReleaseService)
    service.repository=SimpleNamespace(get_knowledge_release=lambda rid:release,get_knowledge_candidate=lambda rid:pytest.fail('No candidate mutation is allowed'))
    service._verify_preview=lambda *args:{}
    with pytest.raises(CandidateWorkflowError,match='baseline comparison'):
        service.promote('release-a','sha',actor_subject_id='fixture',actor_role='knowledge_curator')
    checked=[]
    def gate(rid,target):
        checked.append((rid,target));raise ValueError('wrong target or unreviewed')
    monkeypatch.setattr(behavior_eval,'verify_gate',gate)
    with pytest.raises(CandidateWorkflowError,match='Behavior gate blocked'):
        service.promote('release-a','sha',actor_subject_id='fixture',actor_role='knowledge_curator',behavior_report_id='unreviewed')
    assert checked==[('unreviewed',{'release_id':'release-a','release_manifest_sha256':'sha'})]


def test_install_cannot_shadow_bundled_skill_without_review_and_can_rollback(tmp_path,monkeypatch):
    bundled=tmp_path/'bundled'/'demo';bundled.mkdir(parents=True)
    original='---\nname: demo\ndescription: Format text\n---\n# Format\nPreserve the input.\n'
    (bundled/'SKILL.md').write_text(original)
    incoming=tmp_path/'incoming';incoming.mkdir();(incoming/'SKILL.md').write_text(original+'Normalize spaces.\n')
    manager=SkillManager([bundled.parent],user_root=tmp_path/'installed')
    with pytest.raises(FileExistsError):manager.install_local(incoming)
    with pytest.raises(ValueError,match='reviewed behavior'):manager.install_local(incoming,overwrite=True)
    checksum=manager.inspect(incoming).bundle_checksum
    monkeypatch.setattr(behavior_eval,'verify_gate',lambda *args:{'target':{'skill_checksums':{str(incoming):checksum}}})
    installed=manager.install_local(incoming,overwrite=True,report_id='fixture-reviewed')
    assert Path(installed.path).is_relative_to(tmp_path/'installed')
    assert (bundled/'SKILL.md').read_text()==original
    assert manager.rollback('demo').checksum==manager.inspect(bundled).checksum


def test_repeated_resource_dispatch_uses_same_job_and_rejects_different_inputs(tmp_path,monkeypatch):
    import threading,time
    monkeypatch.setattr(resource_jobs,'state_root',lambda:tmp_path)
    entered=threading.Event();release=threading.Event();calls=[]
    def run(operation,parameters,**kw):
        calls.append(operation);entered.set();release.wait(3);return {'fixture':True}
    monkeypatch.setattr(resource_jobs,'run_operation',run)
    try:
        a=resource_jobs.submit_job('build',{'index_id':'fixture'},request_id='stable-dispatch')
        assert entered.wait(2)
        b=resource_jobs.submit_job('build',{'index_id':'fixture'},request_id='stable-dispatch')
        assert a['id']==b['id'] and calls==['build']
        with pytest.raises(ValueError,match='different inputs'):resource_jobs.submit_job('build',{'index_id':'changed'},request_id='stable-dispatch')
    finally:
        release.set()
        for _ in range(100):
            if resource_jobs.get_job(a['id'])['status']=='succeeded':break
            time.sleep(.01)
