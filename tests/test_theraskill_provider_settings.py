from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _app():
    from theraskill_agent.resource_api import router

    app = FastAPI()
    app.include_router(router)
    return app


def test_provider_settings_are_redacted_and_saved_locally(tmp_path, monkeypatch):
    from theraskill_agent.initialize import initialize_project

    monkeypatch.setenv("THERASKILL_AGENT_ROOT", str(tmp_path))
    initialize_project(tmp_path)
    with TestClient(_app()) as client:
        current = client.get("/api/resources/providers/config")
        assert current.status_code == 200
        assert "api_key" not in current.json()["llm"]
        payload = {
            "llm": {"model": "demo-llm", "base_url": "http://127.0.0.1:1/v1", "api_key": "sk-test-llm"},
            "embedding": {
                "model": "demo-embedding",
                "base_url": "http://127.0.0.1:1/v1",
                "endpoint": "http://127.0.0.1:1/v1/embeddings",
                "api_key": "sk-test-embedding",
            },
        }
        saved = client.post("/api/resources/providers/config", json=payload)
        assert saved.status_code == 200
        assert "sk-test" not in saved.text
        local = Path(tmp_path, "configs/theraskill/providers.local.yaml")
        assert local.is_file()
        assert local.stat().st_mode & 0o777 == 0o600
        assert "sk-test-llm" in local.read_text(encoding="utf-8")
        effective = client.get("/api/resources/providers/config").json()
        assert effective["llm"]["model"] == "demo-llm"
        assert effective["llm"]["api_key_configured"] is True
        assert "api_key" not in effective["llm"]


def test_provider_test_accepts_masked_key_and_never_returns_it(tmp_path, monkeypatch):
    from theraskill_agent.initialize import initialize_project
    from theraskill_agent import provider_smoke

    monkeypatch.setenv("THERASKILL_AGENT_ROOT", str(tmp_path))
    initialize_project(tmp_path)
    seen = []

    def fake_check(kind, *, live=False, config=None):
        seen.append((kind, config or {}))
        return {"kind": kind, "status": "ok", "model": config.get("model")}

    monkeypatch.setattr(provider_smoke, "check_provider", fake_check)
    with TestClient(_app()) as client:
        result = client.post(
            "/api/resources/providers/test",
            json={
                "kind": "all",
                "llm": {"model": "new-llm", "base_url": "http://llm/v1", "api_key": ""},
                "embedding": {"model": "new-embedding", "base_url": "http://embedding/v1", "api_key": ""},
            },
        )
        assert result.status_code == 200
        assert result.json()["status"] == "ok"
        assert [kind for kind, _ in seen] == ["llm", "embedding"]
        assert all(config.get("base_url") for _, config in seen)
        assert "api_key" not in result.text
