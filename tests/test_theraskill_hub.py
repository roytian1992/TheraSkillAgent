from pathlib import Path

from fastapi.testclient import TestClient

from fastapi import FastAPI
import importlib.util


def _router():
    from theraskill_agent.overview import create_router
    return create_router()


def test_hub_overview_contract(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "hermes"
    (home / "platform-data/theraskill/releases/phase1").mkdir(parents=True)
    (home / "platform-data/theraskill/evaluations").mkdir(parents=True)
    (home / "platform-data/theraskill/evaluations/manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    app = FastAPI()
    app.include_router(_router())
    response = TestClient(app).get("/overview")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "theraskill_hub_overview_v1"
    assert set(payload["services"]) == {"support", "simulation", "expert", "contribution", "extraction"}
    assert payload["platform"]["platform_id"] == "theraskill"
    assert "llm" in payload["platform"]["providers"]
    assert {item["id"] for item in payload["apps"]} >= {"support", "simulation", "expert", "extraction"}
