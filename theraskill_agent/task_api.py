"""Task, skill and behavioral evaluation routes in the existing workbench."""
from fastapi import APIRouter,HTTPException,Request
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
    def list_tasks():
        return {'tasks':[{key:task.get(key) for key in ['id','kind','goal','language','status','stage','created_at','updated_at','error']}
                         for task in engine.store.list()]}

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
        from .skill_readiness import check_skill
        return {'skills':[{**s.to_dict(),'readiness':check_skill(s.name,manager=manager)} for s in manager.list_skills()],'tools':TOOLS,'lock':manager.lock_snapshot()}

    @router.get('/api/skills/{name}/readiness')
    def readiness(name:str):
        from .skill_readiness import check_skill
        return guard(lambda:check_skill(name))

    @router.get('/api/workflow-options')
    def workflow_options():
        from .resources import list_resources
        resources=list_resources()
        try:
            value=engine.call('contribution','/v1/releases')
            releases=value if isinstance(value,list) else value.get('releases',[])
        except (RuntimeError,ValueError,KeyError):releases=[]
        from .paths import data_root
        drafts=[]
        for path in sorted((data_root()/'skill-drafts').glob('*/SKILL.md')):
            try:drafts.append(SkillManager().inspect(path.parent).to_dict())
            except (ValueError,OSError):continue
        return {'resources':resources,'releases':releases,'reports':behavior_eval.list_reports(summaries=True),'skill_drafts':drafts}

    @router.post('/api/skills/install')
    def install(body:InstallRequest):
        def execute():
            manager=SkillManager()
            return manager.install_local(body.source,name=body.name,overwrite=body.overwrite,report_id=body.report_id).to_dict()
        return guard(execute)

    @router.post('/api/skills/upload')
    async def upload_skill(request:Request):
        import io,stat,uuid,zipfile
        from .paths import data_root
        from .indexing.common import safe_path
        payload=bytearray()
        async for chunk in request.stream():
            payload.extend(chunk)
            if len(payload)>32*1024**2:raise HTTPException(413,'Skill ZIP upload limit is 32 MiB')
        folder=data_root()/'imports/skills'/uuid.uuid4().hex
        def unpack():
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                entries=archive.infolist()
                if sum(e.file_size for e in entries)>64*1024**2:raise ValueError('Unpacked skill exceeds 64 MiB')
                seen=set()
                for entry in entries:
                    target=safe_path(folder,entry.filename)
                    if entry.filename in seen or stat.S_ISLNK(entry.external_attr>>16):raise ValueError('Duplicate or linked skill member')
                    seen.add(entry.filename)
                    if entry.is_dir():continue
                    target.parent.mkdir(parents=True,exist_ok=True)
                    with archive.open(entry) as source,target.open('xb') as out:
                        import shutil
                        shutil.copyfileobj(source,out)
            skills=list(folder.rglob('SKILL.md'))
            if len(skills)!=1:raise ValueError('A skill ZIP must contain exactly one SKILL.md')
            from .skill_readiness import check_skill
            return {'source':str(skills[0].parent),'readiness':check_skill(skills[0].parent)}
        try:
            from starlette.concurrency import run_in_threadpool
            return await run_in_threadpool(unpack)
        except (ValueError,OSError,zipfile.BadZipFile) as exc:
            import shutil
            shutil.rmtree(folder,ignore_errors=True)
            raise HTTPException(422,str(exc)) from None

    @router.get('/api/skills/{name}/quality')
    def quality(name:str):return guard(lambda:SkillManager().optimize(name).to_dict())

    @router.post('/api/skills/{name}/rollback')
    def rollback(name:str):return guard(lambda:SkillManager().rollback(name).to_dict())

    @router.get('/api/evaluations/suite')
    def suite():return behavior_eval.load_suite()

    @router.get('/api/evaluations/retrieval/suite')
    def retrieval_suite():
        import json
        from .paths import resource_path
        return json.loads(resource_path('configs/theraskill/evaluations/retrieval.json').read_text())

    @router.get('/api/evaluations')
    def reports():return {'reports':behavior_eval.list_reports(summaries=True)}

    @router.get('/api/evaluations/{report_id}')
    def report(report_id:str):return guard(lambda:behavior_eval.get_report(report_id))

    @router.post('/api/evaluations/{report_id}/review')
    def review(report_id:str,body:ReviewRequest):
        return guard(lambda:behavior_eval.review_report(report_id,body.decision,body.comment,body.reviewer))

    return router
