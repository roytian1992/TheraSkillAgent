"""Task, skill and behavioral evaluation routes in the existing workbench."""
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field,ConfigDict
from .task_store import TaskConflict
from .skills import SkillManager
from . import behavior_eval


class TaskRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind:str='skill'
    goal:str=Field(min_length=1,max_length=20000)
    language:str='zh'
    inputs:dict=Field(default_factory=dict)


class ActionRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:str
    values:dict=Field(default_factory=dict)
    revision:int|None=None


class InstallRequest(BaseModel):
    source:str
    name:str|None=None
    overwrite:bool=False
    report_id:str|None=None


class ReviewRequest(BaseModel):
    decision:str
    comment:str=Field(min_length=1)
    reviewer:str=Field(min_length=1)


def create_router(engine):
    router=APIRouter()
    def guard(fn):
        try:return fn()
        except (KeyError,FileNotFoundError):raise HTTPException(404,'Record not found') from None
        except TaskConflict as exc:raise HTTPException(409,str(exc)) from None
        except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from None

    @router.get('/api/tasks')
    def list_tasks():return {'tasks':engine.store.list()}

    @router.post('/api/tasks',status_code=202)
    def create_task(body:TaskRequest):
        return guard(lambda:engine.create(body.kind,body.goal,body.language,body.inputs))

    @router.get('/api/tasks/{task_id}')
    def get_task(task_id:str):
        return guard(lambda:{**engine.store.get(task_id),'events':engine.store.events(task_id)})

    @router.post('/api/tasks/{task_id}/actions')
    def action(task_id:str,body:ActionRequest):
        return guard(lambda:engine.action(task_id,body.action,body.values,body.revision))

    @router.get('/api/skills')
    def skills():
        manager=SkillManager()
        from .skill_execution import TOOLS
        return {'skills':[s.to_dict() for s in manager.list_skills()],'tools':TOOLS,'lock':manager.lock_snapshot()}

    @router.post('/api/skills/install')
    def install(body:InstallRequest):
        def execute():
            manager=SkillManager()
            return manager.install_local(body.source,name=body.name,overwrite=body.overwrite,report_id=body.report_id).to_dict()
        return guard(execute)

    @router.get('/api/skills/{name}/quality')
    def quality(name:str):return guard(lambda:SkillManager().optimize(name).to_dict())

    @router.post('/api/skills/{name}/rollback')
    def rollback(name:str):return guard(lambda:SkillManager().rollback(name).to_dict())

    @router.get('/api/evaluations/suite')
    def suite():return behavior_eval.load_suite()

    @router.get('/api/evaluations')
    def reports():return {'reports':behavior_eval.list_reports()}

    @router.get('/api/evaluations/{report_id}')
    def report(report_id:str):return guard(lambda:behavior_eval.get_report(report_id))

    @router.post('/api/evaluations/{report_id}/review')
    def review(report_id:str,body:ReviewRequest):
        return guard(lambda:behavior_eval.review_report(report_id,body.decision,body.comment,body.reviewer))

    return router
