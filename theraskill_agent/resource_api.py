"""Knowledge/index routes on the existing workbench server."""
import copy
import os
import uuid
from pathlib import Path
from typing import Any, Literal

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from . import resources, resource_jobs
from .paths import data_root, resource_path

router = APIRouter(prefix='/api/resources')


class Operation(BaseModel):
    operation: str
    parameters: dict = Field(default_factory=dict)


class ProviderCheck(BaseModel):
    kind: str


class ProviderBlockInput(BaseModel):
    """Editable, direct provider settings accepted by the local workbench."""

    model: str = Field(min_length=1, max_length=512)
    base_url: str = Field(min_length=1, max_length=2048)
    endpoint: str | None = Field(default=None, max_length=2048)
    api_key: str | None = Field(default=None, max_length=4096)
    timeout_seconds: int | None = Field(default=None, ge=1, le=3600)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    reasoning_effort: str | None = Field(default=None, max_length=64)
    thinking: dict[str, Any] | str | None = None
    ssl_verify: bool | None = None


class ProviderConfigWrite(BaseModel):
    llm: ProviderBlockInput
    embedding: ProviderBlockInput


class ProviderConfigTest(BaseModel):
    kind: Literal["llm", "embedding", "all"] = "all"
    llm: ProviderBlockInput | None = None
    embedding: ProviderBlockInput | None = None


def _provider_snapshot() -> dict[str, Any]:
    """Return effective provider settings without ever returning credentials."""
    from domains.theraskill.provider_config import load_provider_registry

    registry = load_provider_registry()
    result: dict[str, Any] = {
        "active": dict(registry.get("active") or {}),
        "files": [registry[key] for key in ("_path", "_local_path") if registry.get(key)],
    }
    for kind in ("llm", "embedding"):
        block = copy.deepcopy(dict(registry[kind]))
        configured = bool(block.get("api_key"))
        for key in list(block):
            if key.lower() in {"api_key", "token", "authorization", "password", "secret"} or key.lower().endswith("_key"):
                block.pop(key, None)
        block["api_key_configured"] = configured
        result[kind] = block
    return result


def _provider_local_path() -> Path:
    from domains.theraskill.provider_config import load_provider_registry

    registry = load_provider_registry()
    configured = Path(str(registry.get("_path", resource_path("configs/theraskill/providers.yaml"))))
    return configured.with_name("providers.local.yaml") if configured.name == "providers.yaml" else resource_path("configs/theraskill/providers.local.yaml")


def _candidate_block(kind: str, incoming: ProviderBlockInput | None) -> dict[str, Any]:
    from domains.theraskill.provider_config import load_provider_registry

    registry = load_provider_registry()
    current = copy.deepcopy(dict(registry[kind]))
    if incoming is not None:
        values = incoming.model_dump(exclude_none=True)
        # An empty key means "keep the currently configured key". This lets the UI
        # keep credentials masked while still allowing model/URL edits.
        if values.get("api_key") == "":
            values.pop("api_key", None)
        current.update(values)
    return current


def _write_provider_config(body: ProviderConfigWrite) -> Path:
    from domains.theraskill.provider_config import load_provider_registry

    llm = _candidate_block("llm", body.llm)
    embedding = _candidate_block("embedding", body.embedding)
    registry = load_provider_registry()
    payload = {
        key: copy.deepcopy(value)
        for key, value in registry.items()
        if not key.startswith("_") and key not in {"active", "llm", "embedding"}
    }
    payload.update({
        "active": {"llm": "local_llm", "embedding": "local_embedding"},
        "llm": llm,
        "embedding": embedding,
    })
    path = _provider_local_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    # Parse the merged configuration before reporting success. A malformed local
    # file should never be left looking like a successful save to the UI.
    load_provider_registry()
    return path


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
    try:
        return _provider_snapshot()
    except (ValueError, OSError) as exc:
        raise HTTPException(500, str(exc)) from None


@router.get('/providers/config')
def provider_config():
    """Alias with an explicit name for the editable model settings screen."""
    return providers()


@router.post('/providers/config')
def save_provider_config(body: ProviderConfigWrite):
    try:
        path = _write_provider_config(body)
        return {"status": "saved", "path": str(path), "config": _provider_snapshot()}
    except (ValueError, OSError, yaml.YAMLError) as exc:
        raise HTTPException(400, str(exc)) from None


@router.post('/providers/test')
def test_provider_config(body: ProviderConfigTest):
    from .provider_smoke import check_provider

    kinds = ("llm", "embedding") if body.kind == "all" else (body.kind,)
    results: dict[str, Any] = {}
    try:
        for kind in kinds:
            incoming = body.llm if kind == "llm" else body.embedding
            candidate = _candidate_block(kind, incoming)
            results[kind] = check_provider(kind, live=True, config=candidate)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from None
    return {"status": "ok" if all(item.get("status") == "ok" for item in results.values()) else "error", "results": results}


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
