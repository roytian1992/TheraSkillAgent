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
