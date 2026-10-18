"""Knowledge/index routes on the existing workbench server."""
import uuid
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from . import resources, resource_jobs
from .paths import data_root

router = APIRouter(prefix='/api/resources')


class Operation(BaseModel):
    operation: str
    parameters: dict = Field(default_factory=dict)


class ProviderCheck(BaseModel):
    kind: str


@router.get('/readiness')
def readiness():
    from .provider_smoke import check_provider
    from .resources import active_index
    from .skill_readiness import check_skill
    from .skills import SkillManager
    from domains.theraskill.runtime import simulation_root
    from .source_adapters import text_policy
    checks={}
    for kind in ['llm','embedding']:
        try:checks[kind]=check_provider(kind)
        except (ValueError,OSError) as exc:checks[kind]={'status':'error','error':str(exc)}
    try:
        selected=active_index()
        checks['index']={'ready':bool(selected),'id':selected[1]['index_id'] if selected else None}
    except (ValueError,OSError) as exc:checks['index']={'ready':False,'error':str(exc)}
    checks['simulation']={'ready':(simulation_root()/'scenarios/scenario_registry.jsonl').is_file()}
    checks['skills']=[check_skill(s.name) for s in SkillManager().list_skills()]
    checks['text_policy']=text_policy()
    return checks


@router.post('/providers/check')
def provider_check(body:ProviderCheck):
    from .provider_smoke import check_provider
    try:return check_provider(body.kind,live=True)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None


@router.post('/upload-package',status_code=202)
async def upload_package(request:Request):
    path=data_root()/'imports'/(uuid.uuid4().hex+'.zip')
    path.parent.mkdir(parents=True,exist_ok=True)
    size=0
    try:
        with path.open('xb') as output:
            async for chunk in request.stream():
                size+=len(chunk)
                if size>16*1024**3:raise HTTPException(413,'Resource ZIP upload limit is 16 GiB; use the CLI for larger files')
                output.write(chunk)
        return resource_jobs.submit_job('install',{'archive':str(path)})
    except Exception:
        path.unlink(missing_ok=True)
        raise


@router.get('')
def list_resources():
    return {**resources.list_resources(), 'jobs': resource_jobs.list_jobs()}


@router.get('/providers')
def providers():
    from domains.theraskill.provider_config import load_provider_registry, public_provider_info
    registry = load_provider_registry()
    result = {kind: {key: value for key, value in public_provider_info(kind).items()
                     if key in {'model', 'base_url', 'endpoint', 'max_output_tokens', 'thinking', 'reasoning_effort'}}
              for kind in ('llm', 'embedding')}
    result['files'] = [registry[key] for key in ('_path', '_local_path') if registry.get(key)]
    return result


@router.post('/jobs')
def submit(body: Operation):
    try:
        return resource_jobs.submit_job(body.operation, body.parameters)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from None


@router.get('/jobs/{job_id}')
def status(job_id: str):
    try:
        return resource_jobs.get_job(job_id)
    except (ValueError, OSError):
        raise HTTPException(404, 'Job not found') from None


@router.post('/jobs/{job_id}/cancel')
def cancel(job_id: str):
    try:
        return resource_jobs.cancel_job(job_id)
    except (ValueError, OSError):
        raise HTTPException(404, 'Job not found') from None


@router.post('/upload')
async def upload(request: Request):
    # Direct binary upload avoids an additional multipart dependency. Large files
    # can be supplied by local path; the browser upload is bounded at 32 MiB.
    path = data_root() / 'imports' / (uuid.uuid4().hex + '.jsonl')
    path.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        with path.open('xb') as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > 32 * 1024 * 1024:
                    raise HTTPException(413, 'Upload limit is 32 MiB; use a local path for larger knowledge files')
                out.write(chunk)
        from .indexing import plan_index
        # Do not block the event loop during JSONL validation.
        from starlette.concurrency import run_in_threadpool
        plan = await run_in_threadpool(plan_index, path)
        from .indexing.common import write_json
        write_json(path.with_suffix('.manifest.json'),{'path':str(path),'id':path.stem,
            'manifest':{'knowledge_sha256':plan['knowledge_sha256'],'counts':plan['counts']}})
        return {'knowledge': str(path), 'plan': plan}
    except ValueError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(400, str(exc)) from None
    except Exception:
        path.unlink(missing_ok=True)
        raise
