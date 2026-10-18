from __future__ import annotations

from fastapi.testclient import TestClient

from domains.theraskill.services.extraction_api.app import ExtractionAPISettings, create_app as create_extraction_app
from domains.theraskill.services.transcript_api.app import create_app as create_transcript_app


def test_transcript_workbench_transport_exposes_all_operations() -> None:
    client = TestClient(create_transcript_app())
    payload = {
        "text": "患者：最近睡不好。\\n医师：听起来你很疲惫。",
        "filename": "demo.txt",
        "skills": ["transcript-quality-reviewer"],
    }
    for operation in ("inspect", "analyze", "review", "package"):
        response = client.post(f"/v1/transcript/{operation}", json=payload)
        assert response.status_code == 200
        body = response.json()
        assert body["operation"] == operation
        assert body["document"]["turns"][0]["turn_id"] == "turn_0001"


def test_extraction_workbench_can_upload_and_inspect_source(tmp_path) -> None:
    settings = ExtractionAPISettings(
        extraction_project_root=tmp_path,
        work_root=tmp_path / "jobs",
        contribution_database_path=tmp_path / "contribution.sqlite",
    )
    client = TestClient(create_extraction_app(settings))
    headers = {"X-Extraction-Role": "researcher"}
    uploaded = client.post(
        "/v1/extraction/sources",
        headers=headers,
        json={"filename": "records.jsonl", "text": '{"id":"1","text":"demo"}\n'},
    )
    assert uploaded.status_code == 200
    body = uploaded.json()
    assert body["source"]["records"] == 1
    inspected = client.post("/v1/extraction/inspect", headers=headers, json={"path": body["path"]})
    assert inspected.status_code == 200
    assert inspected.json()["source"]["format"] == "jsonl"


def test_browser_and_cli_share_json_srt_adapters_and_preserve_original(tmp_path) -> None:
    import hashlib
    from theraskill_agent.transcript import load_transcript
    client = TestClient(create_transcript_app())
    sources = {
        "dialogue.json": '{"messages":[{"role":"patient","content":"压力，很大？"},{"role":"therapist","content":"听起来很累。"}]}',
        "dialogue.srt": "1\n00:00:01,000 --> 00:00:02,000\n患者：压力，很大？\n\n2\n00:00:03,000 --> 00:00:04,000\n医师：听起来很累。\n",
    }
    for name, text in sources.items():
        path = tmp_path / name; path.write_text(text, encoding="utf-8")
        expected, _ = load_transcript(path)
        response = client.post('/v1/transcript/inspect', json={"filename":name,"text":text})
        assert response.status_code == 200
        actual = response.json()["document"]
        assert len(actual["turns"]) == 2
        assert actual["raw_text_sha256"] == hashlib.sha256(text.encode()).hexdigest()
        assert [turn["raw_text"] for turn in actual["turns"]] == [turn.raw_text for turn in expected.turns]
        assert actual["turns"][0]["raw_text"] == "压力，很大？"
        assert actual["turns"][0]["normalized_text"] == "压力,很大?"


def test_transcript_bundle_can_be_downloaded_without_exposing_arbitrary_paths(tmp_path, monkeypatch) -> None:
    import io, zipfile
    import domains.theraskill.services.transcript_api.app as module
    monkeypatch.setattr(module, "data_root", lambda: tmp_path)
    client = TestClient(module.create_app())
    response = client.post('/v1/transcript/package', json={"text":"患者：压力很大。\n医师：听起来很累。"})
    assert response.status_code == 200
    download = client.get(response.json()["download_url"])
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.content)) as bundle:
        assert {"manifest.json", "transcript.json", "analysis.json"} <= set(bundle.namelist())
    assert client.get('/v1/transcript/bundles/not-a-bundle/download').status_code == 404


def test_simulation_runtime_failure_is_a_retryable_json_response(tmp_path) -> None:
    from domains.theraskill.services.simulation_api.app import SimulationAPISettings, create_app
    from domains.theraskill.theraskill_simulation.engines import SimulationEngineError
    app = create_app(SimulationAPISettings(database_path=tmp_path/'session.sqlite',scenario_registry_path=tmp_path/'scenarios.jsonl',generated_batch_work_root=tmp_path/'batch',initialize_llm_engine=False))
    @app.get('/test-runtime-failure')
    def failure():
        raise SimulationEngineError('Synthetic upstream disconnect; dialogue is preserved')
    response = TestClient(app).get('/test-runtime-failure')
    assert response.status_code == 502
    assert response.json()['retryable'] is True
    assert 'Synthetic upstream disconnect' in response.json()['detail']


def test_extraction_retry_reprocesses_failures_and_keeps_successful_checkpoint(tmp_path, monkeypatch) -> None:
    from pathlib import Path
    import time
    from types import SimpleNamespace
    from domains.theraskill.services.extraction_api.app import ExtractionJobStore
    from theraskill_agent.extraction import executor
    import domains.theraskill.theraskill_llm as llm
    source=tmp_path/'records.jsonl';source.write_text('{"id":"a","text":"source A"}\n{"id":"b","text":"source B"}\n')
    config=tmp_path/'model.yaml';config.write_text('{}')
    seen=[]
    def chat(_client,_prompt,payload,**kwargs):
        seen.append(payload['source_id'])
        if len(seen)==2:raise RuntimeError('synthetic record failure')
        return {'claim':'synthetic strategy','evidence':payload['source_record']['text']}
    monkeypatch.setattr(llm,'OpenAICompatibleClient',lambda _:SimpleNamespace(model='test-only'))
    monkeypatch.setattr(executor,'chat_json_with_repair',chat)
    store=ExtractionJobStore(ExtractionAPISettings(work_root=tmp_path/'jobs',extraction_project_root=Path(__file__).parents[1]/'extraction',model_config_path=config))
    def wait(jid):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            row=store.get(jid)
            if row['status'] not in ['queued','running']:return row
            time.sleep(.01)
        raise AssertionError('Extraction job did not finish')
    try:
        created=store.create({'goal':'Extract source-grounded strategies','source':{'path':str(source)},'unit_definition':'one record','target_schema':{'type':'object','properties':{'claim':{'type':'string'},'evidence':{'type':'string'}},'required':['claim','evidence']}},None,'test')
        jid=created['job_id'];partial=wait(jid)
        assert partial['status']=='partial' and partial['counts']=={'units':1,'failures':1}
        store.retry(jid);completed=wait(jid)
        assert completed['status']=='completed' and completed['counts']=={'units':2,'failures':0}
        assert len(seen)==3 and seen[1]==seen[2] and seen[0]!=seen[1]
        assert (tmp_path/'jobs'/jid/'failures.attempt-1.jsonl').read_text().strip()
    finally:
        store._executor.shutdown(wait=True)
