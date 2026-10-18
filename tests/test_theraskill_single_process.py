"""Exercise the real six domain applications with network transports disabled."""
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from theraskill_agent.local_services import LocalServices, service_client, set_services
from theraskill_agent.webapp import UI_APPS, create_app


@pytest.fixture
def local_host(tmp_path, monkeypatch):
    from theraskill_agent.initialize import initialize_project
    initialize_project(tmp_path / 'workspace')
    monkeypatch.setenv('THERASKILL_AGENT_ROOT', str(tmp_path / 'workspace'))
    from domains.theraskill.services.support_api.app import SupportAPISettings, create_app as support
    from domains.theraskill.services.simulation_api.app import SimulationAPISettings, create_app as simulation
    from domains.theraskill.services.contribution_api.app import create_app as contribution
    from domains.theraskill.services.expert_api.app import create_app as expert
    from domains.theraskill.services.extraction_api.app import create_app as extraction
    from domains.theraskill.services.transcript_api.app import create_app as transcript
    host = LocalServices({
        'support': lambda: support(SupportAPISettings(initialize_llm_engine=False)),
        'simulation': lambda: simulation(SimulationAPISettings(initialize_llm_engine=False)),
        'expert': expert, 'contribution': contribution, 'extraction': extraction, 'transcript': transcript,
    })
    set_services(host)
    def no_network(*args, **kwargs):
        raise AssertionError('Business request attempted to open a network connection')
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', no_network)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', no_network)
    yield host
    host.close()


def test_all_modules_and_cross_module_calls_work_without_domain_ports(local_host, tmp_path):
    for name in UI_APPS.values():
        path = tmp_path / name / 'dist'
        path.mkdir(parents=True)
        (path / 'index.html').write_text(name)
    with TestClient(create_app(assets_root=tmp_path)) as browser:
        health = browser.get('/health').json()
        assert health['deployment'] == 'single_process'
        assert len(health['modules']) == 6
        for name in health['modules']:
            assert browser.get(f'/api/{name}/health').status_code == 200
        assert browser.get('/api/expert/health').json() == {'status':'ok','support':'ok','simulation':'ok'}
        overview = browser.get('/api/overview').json()
        assert all(s['status']=='online' and s['transport']=='inprocess' for s in overview['services'].values())
        assert all('port' not in s for s in overview['platform']['services'].values())
        # Expert jobs create and read support sessions through the same host.
        expert_headers={'X-Expert-Role':'expert'}
        job = browser.post('/api/expert/jobs', headers=expert_headers,
                           json={'workflow':'response_review','payload':{'text':'fixture'}})
        assert job.status_code == 202, job.text
        job_id=job.json()['job_id']
        result=browser.get(f'/api/expert/jobs/{job_id}',headers=expert_headers)
        assert result.status_code == 200, result.text
        assert result.json()['status'] in {'pending', 'failed'}  # LLM disabled in this fixture
        # Support/simulation contribution clients must not fall back to port 8881.
        from domains.theraskill.theraskill_contribution.client import HTTPContributionClient
        consent=HTTPContributionClient('inprocess://contribution').create_consent(
            subject_id='test-user',role='user',capture_tier='operational_only',
            purposes={'content_review':False,'research_use':False,'knowledge_derivation':False})
        assert consent['subject_id'] == 'test-user'
        assert browser.get('/api/support/contributions', headers={'X-Support-User':'test-user','X-Support-Role':'user'}).status_code == 200
        # Agent extraction calls use the very same application and uploaded path.
        from theraskill_agent.task_runtime import domain_call
        source=domain_call('extraction','/v1/extraction/sources',{'filename':'demo.txt','text':'患者：焦虑\n医师：很难受吧'})
        assert domain_call('extraction','/v1/extraction/inspect',{'path':source['path']})['source']['records'] > 0
        bundle=browser.post('/api/transcript/v1/transcript/package',json={'text':'患者：焦虑。\n医师：听起来很难受。'}).json()
        assert browser.get('/api/transcript'+bundle['download_url']).status_code == 200
        # Plugins and synchronous tools also share the local transport.
        with service_client('inprocess://simulation') as client:
            assert client.get('/scenarios').status_code == 200
    assert not local_host._thread.is_alive()


def test_child_lifespans_start_and_stop_once_and_failures_remain_json():
    events=[]
    @asynccontextmanager
    async def lifespan(app):
        events.append('start')
        yield
        events.append('stop')
    def factory():
        app=FastAPI(lifespan=lifespan)
        @app.get('/broken')
        def broken():
            from fastapi import HTTPException
            raise HTTPException(409,'domain conflict')
        return app
    host=LocalServices({'fixture':factory})
    try:
        host.start();host.start()
        response=host.request('fixture','GET','/broken')
        assert response.status_code == 409
        assert response.json()['detail'] == 'domain conflict'
        with pytest.raises(ValueError,match='Invalid domain path'):
            host.request('fixture','GET','//example.com/')
    finally:
        host.close();host.close()
    assert events == ['start','stop']


def test_accepted_response_returns_before_background_job_finishes():
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from fastapi import BackgroundTasks
    started=threading.Event(); release=threading.Event(); finished=threading.Event()
    def factory():
        app=FastAPI()
        def job():
            started.set();release.wait(5);finished.set()
        @app.post('/jobs', status_code=202)
        def queue(background_tasks: BackgroundTasks):
            background_tasks.add_task(job)
            return {'status':'pending'}
        return app
    host=LocalServices({'fixture':factory})
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            result=pool.submit(host.request,'fixture','POST','/jobs')
            try:
                assert result.result(timeout=2).status_code == 202
                assert started.wait(2)
                assert not finished.is_set()
            finally:
                release.set()
        assert finished.wait(2)
    finally:
        release.set();host.close()


def test_project_flag_and_initialization_support_a_fresh_machine(tmp_path, monkeypatch, capsys):
    import json
    from theraskill_agent.initialize import initialize_project
    from theraskill_agent.cli import main
    initialize_project(tmp_path)
    assert (tmp_path/'configs/theraskill/providers.local.example.yaml').is_file()
    monkeypatch.delenv('THERASKILL_AGENT_ROOT',raising=False)
    assert main(['--project',str(tmp_path),'status']) == 0
    assert json.loads(capsys.readouterr().out)['project_root'] == str(tmp_path)
    monkeypatch.delenv('THERASKILL_AGENT_ROOT',raising=False)


def test_startup_failure_closes_already_started_modules():
    events=[]
    @asynccontextmanager
    async def lifespan(app):
        events.append('start')
        try:yield
        finally:events.append('stop')
    def invalid():raise ValueError('invalid local configuration')
    host=LocalServices({'first':lambda:FastAPI(lifespan=lifespan),'invalid':invalid})
    try:
        with pytest.raises(RuntimeError,match='invalid local configuration'):
            host.start()
    finally:host.close()
    assert events == ['start','stop']
