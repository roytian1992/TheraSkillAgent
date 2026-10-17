from pathlib import Path

from fastapi.testclient import TestClient

from domains.theraskill.services.simulation_api.app import SimulationAPISettings, create_app
from domains.theraskill.theraskill_simulation.scenario_generation import ScenarioGenerationRequest


def test_teaching_overview_exposes_loop_counts(tmp_path: Path) -> None:
    app = create_app(
        SimulationAPISettings(
            database_path=tmp_path / "sessions.sqlite",
            scenario_registry_path=tmp_path / "scenarios.jsonl",
            initialize_llm_engine=False,
        )
    )
    client = TestClient(app)
    response = client.get("/teaching/overview", headers={"X-Simulation-Role": "instructor"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "theraskill_teaching_overview_v1"
    assert "assignments" in payload and "annotations" in payload
    assert payload["next_actions"]


def test_curriculum_course_and_recommendation_surfaces(tmp_path: Path) -> None:
    app = create_app(
        SimulationAPISettings(
            database_path=tmp_path / "sessions.sqlite",
            scenario_registry_path=tmp_path / "scenarios.jsonl",
            initialize_llm_engine=False,
        )
    )
    client = TestClient(app)
    headers = {"X-Simulation-Role": "instructor"}
    course = client.post(
        "/courses",
        headers=headers,
        json={"title": "基础支持", "description": "课程说明", "skill_tags": ["validation"]},
    )
    assert course.status_code == 201
    assert course.json()["schema_version"] == "theraskill_training_course_v1"
    curriculum = client.get("/teaching/curriculum", headers=headers)
    assert curriculum.status_code == 200
    assert curriculum.json()["counts"]["courses"] == 1
    recommendations = client.get("/teaching/recommendations", headers=headers)
    assert recommendations.status_code == 200
    assert recommendations.json()["schema_version"] == "theraskill_learning_recommendations_v1"


def test_online_scenario_generation_is_restricted_and_persisted(tmp_path: Path) -> None:
    class FakeGenerator:
        def generate(self, request: ScenarioGenerationRequest):
            assert request.brief.startswith("失眠")
            return {
                "scenario": {
                    "scenario_id": "generated_test_case",
                    "scenario_family": request.scenario_family,
                    "case_id": "generated_case_test",
                    "case_family": "在线生成训练案例",
                    "difficulty_band": request.difficulty_band,
                    "risk_band": request.risk_band,
                    "scenario_brief": request.brief,
                    "scenario_tags": ["睡眠", "自责"],
                    "recommended_turn_budget": 4,
                    "split_labels": {"formal_split": "generated"},
                    "learner_role_card": {"display_name": "模拟来访者"},
                    "generated": True,
                    "synthetic": True,
                },
                "case_bundle": {},
            }

    app = create_app(
        SimulationAPISettings(
            database_path=tmp_path / "sessions.sqlite",
            scenario_registry_path=tmp_path / "scenarios.jsonl",
            generated_scenario_registry_path=tmp_path / "generated.jsonl",
            initialize_llm_engine=False,
        ),
        scenario_generator=FakeGenerator(),
    )
    client = TestClient(app)
    body = {"brief": "失眠和工作自责的初次访谈训练", "scenario_tags": ["sleep"]}
    denied = client.post("/scenarios/generate", headers={"X-Simulation-Role": "human_therapist"}, json=body)
    assert denied.status_code == 403
    created = client.post("/scenarios/generate", headers={"X-Simulation-Role": "researcher"}, json=body)
    assert created.status_code == 201
    assert created.json()["synthetic"] is True
    assert created.json()["safety"]["requires_researcher_or_instructor_review"] is True
    listed = client.get("/scenarios/generated", headers={"X-Simulation-Role": "researcher"})
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert listed.json()["scenarios"][0]["scenario_id"] == "generated_test_case"


def test_online_generation_preserves_requested_language(tmp_path: Path) -> None:
    seen: list[str] = []

    class FakeGenerator:
        def generate(self, request: ScenarioGenerationRequest):
            seen.append(request.language)
            return {
                "scenario": {
                    "scenario_id": "generated_language_case",
                    "scenario_family": request.scenario_family,
                    "case_id": "generated_language_case_bundle",
                    "case_family": "English generated case",
                    "difficulty_band": request.difficulty_band,
                    "risk_band": request.risk_band,
                    "scenario_brief": request.brief,
                    "scenario_tags": ["language"],
                    "recommended_turn_budget": 4,
                    "split_labels": {"formal_split": "generated"},
                    "learner_role_card": {"display_name": "Alex"},
                    "generated": True,
                    "synthetic": True,
                },
                "case_bundle": {},
            }

    app = create_app(
        SimulationAPISettings(
            database_path=tmp_path / "sessions.sqlite",
            scenario_registry_path=tmp_path / "scenarios.jsonl",
            generated_scenario_registry_path=tmp_path / "generated.jsonl",
            initialize_llm_engine=False,
        ),
        scenario_generator=FakeGenerator(),
    )
    client = TestClient(app)
    response = client.post(
        "/scenarios/generate",
        headers={"X-Simulation-Role": "researcher"},
        json={"brief": "Generate an English supportive exploration case", "language": "en"},
    )
    assert response.status_code == 201
    assert seen == ["en"]
    assert response.json()["scenario"]["language"] == "en"


def test_generated_draft_governance_edit_review_and_batch(tmp_path: Path) -> None:
    bundle_path = tmp_path / "generated_case.json"
    bundle_path.write_text(
        '{"authoritative_layer": {"visible_profile": {"name": "访客"}, "hidden_profile": {"fact": "x"}, "continuity_seed": {"state": "初始"}}}',
        encoding="utf-8",
    )

    class FakeGenerator:
        def generate(self, request: ScenarioGenerationRequest):
            index = getattr(self, "index", 0)
            self.index = index + 1
            return {
                "scenario": {
                    "scenario_id": f"generated_governance_{index}",
                    "scenario_family": request.scenario_family,
                    "case_id": f"generated_case_{index}",
                    "case_family": "在线生成训练案例",
                    "difficulty_band": request.difficulty_band,
                    "risk_band": request.risk_band,
                    "scenario_brief": f"{request.brief} 第 {index} 个不同练习主题",
                    "scenario_tags": ["治理测试", str(index)],
                    "recommended_turn_budget": 4,
                    "split_labels": {"formal_split": "generated"},
                    "learner_role_card": {"display_name": "模拟来访者"},
                    "generated": True,
                    "synthetic": True,
                    "case_bundle_path": str(bundle_path),
                },
                "case_bundle": {},
            }

    app = create_app(
        SimulationAPISettings(
            database_path=tmp_path / "sessions.sqlite",
            scenario_registry_path=tmp_path / "scenarios.jsonl",
            generated_scenario_registry_path=tmp_path / "generated.jsonl",
            initialize_llm_engine=False,
        ),
        scenario_generator=FakeGenerator(),
    )
    client = TestClient(app)
    headers = {"X-Simulation-Role": "researcher"}
    created = client.post("/scenarios/generate", headers=headers, json={"brief": "睡眠和工作压力的支持性访谈"})
    assert created.status_code == 201
    scenario_id = created.json()["scenario"]["scenario_id"]
    assert created.json()["review"]["status"] == "draft"
    draft_course = client.post(
        "/courses",
        headers={"X-Simulation-Role": "instructor"},
        json={"title": "不应发布", "scenario_ids": [scenario_id]},
    )
    assert draft_course.status_code == 422
    updated = client.patch(
        f"/scenarios/generated/{scenario_id}",
        headers=headers,
        json={"scenario_brief": "编辑后的睡眠和工作压力访谈主题", "scenario_tags": ["编辑后"]},
    )
    assert updated.status_code == 200
    assert updated.json()["scenario"]["review_status"] == "draft"
    rejected_without_reason = client.post(
        f"/scenarios/generated/{scenario_id}/review",
        headers=headers,
        json={"action": "reject"},
    )
    assert rejected_without_reason.status_code == 422
    approved = client.post(
        f"/scenarios/generated/{scenario_id}/review",
        headers=headers,
        json={"action": "approve"},
    )
    assert approved.status_code == 200
    assert approved.json()["review"]["status"] == "approved"
    reusable = client.get("/scenarios", headers={"X-Simulation-Role": "human_therapist"})
    assert reusable.status_code == 200
    assert any(row["scenario_id"] == scenario_id for row in reusable.json()["scenarios"])
    course = client.post(
        "/courses",
        headers={"X-Simulation-Role": "instructor"},
        json={"title": "已审核课程", "scenario_ids": [scenario_id]},
    )
    assert course.status_code == 201

    batch = client.post(
        "/scenarios/generate-batch",
        headers=headers,
        json={"course_title": "支持性访谈课程", "brief": "围绕压力和睡眠建立支持性访谈练习", "count": 2},
    )
    assert batch.status_code == 201
    assert batch.json()["schema_version"] == "theraskill_online_scenario_batch_v1"
    assert batch.json()["count"] == 2
    assert batch.json()["course_draft"]["review_required"] is True
