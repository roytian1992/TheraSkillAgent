from pathlib import Path

from fastapi.testclient import TestClient

from domains.theraskill.services.simulation_api.app import SimulationAPISettings, create_app
from domains.theraskill.theraskill_simulation.scenario_generation import ScenarioGenerationBatchRequest


def client(tmp_path: Path):
    return TestClient(create_app(SimulationAPISettings(
        database_path=tmp_path / "sessions.sqlite",
        scenario_registry_path=tmp_path / "scenarios.jsonl",
        generated_scenario_registry_path=tmp_path / "generated.jsonl",
        generated_scenario_root=tmp_path / "cases",
        generated_batch_work_root=tmp_path / "jobs",
        initialize_llm_engine=False, initialize_scenario_generator=False,
    )))


def test_blueprint_works_without_an_installed_index(tmp_path, monkeypatch):
    monkeypatch.setattr("plugins.theraskill.retrieval.search_managed_knowledge", lambda *args, **kwargs: None)
    with client(tmp_path) as api:
        result = api.post("/courses/blueprint", headers={"X-Simulation-Role": "instructor"},
                          json={"template_id": "foundation_first_session", "scenario_count": 3})
        assert result.status_code == 200
        seed = result.json()
        assert seed["template_id"] == "foundation_first_session"
        assert seed["objectives"][0]["observable_behaviors"]
        assert seed["scenario_constraints"]["count"] == 3
        assert seed["retrieval"]["status"] == "unavailable"
        assert seed["knowledge_references"] == []
        assert seed["generation_payload"]["count"] == 3
        assert seed["generation_payload"]["skill_tags"] == seed["competency_ids"]
        assert seed["generation_payload"]["learning_units"]["micro_practices"]
        ScenarioGenerationBatchRequest.model_validate(seed["generation_payload"])


def test_blueprint_retrieves_once_and_keeps_knowledge_on_course(tmp_path, monkeypatch):
    calls = []
    def search(query, **kwargs):
        calls.append((query, kwargs))
        return {"release_id": "fixture-index", "retrieval_mode": "lexical", "matches": [
            {"release_unit_id": "knowledge-1", "canonical_label": "Reflect and check", "text_preview": "Reflect the person's emotion and check whether the reflection fits."}
        ]}
    monkeypatch.setattr("plugins.theraskill.retrieval.search_managed_knowledge", search)
    with client(tmp_path) as api:
        headers = {"X-Simulation-Role": "instructor"}
        seed = api.post("/courses/blueprint", headers=headers,
                        json={"template_id": "foundation_first_session", "title": "Listening"}).json()
        assert len(calls) == 1
        assert "alliance" in calls[0][0] and "assessment" in calls[0][0]
        assert "risk" not in calls[0][0]
        assert seed["retrieval"]["status"] == "grounded"
        assert seed["knowledge_references"][0]["unit_id"] == "knowledge-1"
        assert seed["objectives"][0]["observable_behaviors"]
        assert {"observed", "not_observed", "not_applicable"}.issubset(seed["objectives"][0]["observation_statuses"])
        assert any(item["level"] == "not_observed" for item in seed["objectives"][0]["rubric"])
        request = {"title": seed["title"], "description": seed["description"],
                   "learning_objectives": seed["objectives"],
                   "template_id": seed["template_id"], "competency_ids": seed["competency_ids"],
                   "scaffolding": seed["scaffolding"], "knowledge_references": seed["knowledge_references"],
                   "learning_units": {"knowledge_cards": seed["knowledge_cards"], "micro_practices": seed["micro_practices"]},
                   "design_retrieval": seed["retrieval"]}
        created = api.post("/courses", headers=headers, json=request)
        assert created.status_code == 201
        saved = api.get("/courses", headers=headers).json()["courses"][0]
        assert saved["knowledge_references"] == seed["knowledge_references"]
        assert saved["scaffolding"] == seed["scaffolding"]
        assert saved["learning_units"]["micro_practices"] == seed["micro_practices"]
        assert saved["design_retrieval"]["release_id"] == "fixture-index"
        assert saved["learning_objectives"][0]["observable_behaviors"] == seed["objectives"][0]["observable_behaviors"]
        assert saved["learning_objectives"][0]["rubric"][0]["level"] == "not_observed"


def test_course_rejects_unknown_observation_status(tmp_path):
    with client(tmp_path) as api:
        result = api.post("/courses", headers={"X-Simulation-Role": "instructor"}, json={
            "title": "Invalid rubric", "learning_objectives": [{"id": "a", "description": "A goal",
                "observation_statuses": ["maybe"]}],
        })
        assert result.status_code == 422


def test_blueprint_rejects_unknown_templates_and_learners(tmp_path, monkeypatch):
    monkeypatch.setattr("plugins.theraskill.retrieval.search_managed_knowledge", lambda *args, **kwargs: None)
    with client(tmp_path) as api:
        response = api.post("/courses/blueprint", headers={"X-Simulation-Role": "instructor"}, json={"template_id": "missing"})
        assert response.status_code == 422
        assert api.post("/courses/blueprint", headers={"X-Simulation-Role": "human_therapist"}, json={}).status_code == 403


def test_english_blueprint_preserves_language_and_rejects_dangling_exercises(tmp_path, monkeypatch):
    monkeypatch.setattr("plugins.theraskill.retrieval.search_managed_knowledge", lambda *a, **kw: None)
    with client(tmp_path) as api:
        headers = {"X-Simulation-Role": "instructor"}
        seed = api.post("/courses/blueprint", headers=headers, json={"language": "en"}).json()
        assert seed["title"] == "First-session foundations"
        assert seed["templates"][1]["title"] == "Risk recognition and safety communication"
        payload = {"title": seed["title"], "learning_objectives": seed["objectives"],
                   "learning_units": seed["generation_payload"]["learning_units"]}
        payload["learning_units"]["micro_practices"][0]["competency_id"] = "missing-objective"
        assert api.post("/courses", headers=headers, json=payload).status_code == 422
        payload["learning_units"]["micro_practices"][0]["competency_id"] = seed["objectives"][0]["id"]
        payload["learning_units"]["micro_practices"][0]["scenario_text"] = "  "
        assert api.post("/courses", headers=headers, json=payload).status_code == 422
        assert api.post("/courses", headers=headers, json={"title": "  "}).status_code == 422
