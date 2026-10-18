from concurrent.futures import ThreadPoolExecutor
import pytest
from plugins.theraskill.extraction_plan import validate_plan,ExtractionPlanError
from theraskill_agent.extraction.contracts import ExtractionPlan,PlanError
from domains.theraskill.services.extraction_api.app import ExtractionJobStore,ExtractionAPISettings


def plan():return {'goal':'Extract evidence','source':{'path':'fixture.txt'},'target_schema':{'type':'object','properties':{'evidence':{'type':'string'}},'required':['evidence']}}


@pytest.mark.parametrize('field,value',[('operations',[{'op':'extract'}]),('quality_rules',{'field':'rule'}),('evidence_fields','evidence')])
def test_planning_and_execution_reject_same_invalid_contract(field,value):
    value_plan={**plan(),field:value}
    with pytest.raises(ExtractionPlanError,match='list of strings'):validate_plan(value_plan)
    with pytest.raises(PlanError,match='list of strings'):ExtractionPlan.from_mapping(value_plan)


def test_extraction_submission_is_idempotent_even_with_concurrent_retries(tmp_path):
    store=ExtractionJobStore(ExtractionAPISettings(work_root=tmp_path/'jobs',contribution_database_path=tmp_path/'c.sqlite'))
    store._run=lambda *_:None
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs=list(pool.map(lambda _:store.create(plan(),1,'qa','stable-call'),range(8)))
        assert len({v['job_id'] for v in jobs})==1 and len(store.list())==1
        with pytest.raises(ValueError,match='different extraction inputs'):store.create(plan(),2,'qa','stable-call')
        store._executor.shutdown(wait=True)
        recovered=ExtractionJobStore(store.settings);recovered._executor.shutdown(wait=True)
        assert recovered.get(jobs[0]['job_id'])['request_id']=='stable-call'
    finally:store._executor.shutdown(wait=True)
