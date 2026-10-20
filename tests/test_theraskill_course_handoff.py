"""Generated-course handoff follows current reviewed artifacts, not job snapshots."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from domains.theraskill.services.simulation_api.app import SimulationAPISettings, create_app, scenario_public_digest
from domains.theraskill.theraskill_simulation.course_generation import _actual_cases
from domains.theraskill.theraskill_simulation.generation_workflow import fingerprint


TEACHER = {"X-Simulation-Role": "instructor"}
RESEARCHER = {"X-Simulation-Role": "researcher"}
JOB_ID = "scenario_batch_handoff"


def seed_handoff(app, *, approved=False):
    registry = app.state.session_service.scenario_registry
    rows = []
    for index in (1, 2):
        concern = f"第{index}次会谈中我感到孤单。"
        row = {"scenario_id": f"handoff_case_{index}", "batch_id": JOB_ID, "batch_index": index,
               "batch_size": 2, "generated": True, "synthetic": True, "scenario_family": "supportive_exploration",
               "difficulty_band": "medium", "risk_band": "low", "scenario_brief": concern,
               "scenario_tags": ["alliance"], "language": "zh", "revision": 1,
               "review_status": "approved" if approved else "draft", "review_history": [],
               "generation_workflow": {"review_status": "pass"},
               "patient_dossier": {"presenting_concern": concern,
                   "recent_events": [{"event_id": f"event_{index}", "time_label": "最近", "content": f"上周第{index}天与朋友产生误解。"}],
                   "training_objectives": ["反映情绪"]}}
        registry.register_generated(row)
        rows.append(row)
    plan = {"objectives": ["反映情绪"], "cases": [
        {"index": index, "learning_objective": "反映情绪", "brief": row["scenario_brief"]}
        for index, row in enumerate(rows, 1)]}
    actual, errors = _actual_cases(rows)
    assert not errors
    coverage = {"status": "pass", "expected_cases": 2, "actual_cases": 2,
                "deterministic_checks": {"status": "pass", "issues": []},
                "actual_artifact_sha256": fingerprint(actual), "plan_sha256": fingerprint(plan),
                "semantic_review": {"status": "pass", "summary": "Both patients provide a distinct opportunity.",
                    "issues": [], "duplicate_pairs": [],
                    "case_reviews": [{"index": index, "covered": True, "evidence": [row["scenario_brief"]], "issues": []}
                                     for index, row in enumerate(rows, 1)],
                    "objective_coverage": [{"objective": "反映情绪", "covered": True, "case_indices": [1, 2], "rationale": "Actual concerns can be reflected."}]}}
    course = {"title": "情绪反映课程", "description": "从示例到练习", "batch_id": JOB_ID,
              "scenario_ids": [row["scenario_id"] for row in rows], "skill_tags": ["alliance"],
              "template_id": "foundation_first_session", "competency_ids": ["alliance"],
              "scaffolding": ["示范", "微练习", "完整会谈"],
              "learning_objectives": [{"id": "alliance", "description": "反映情绪并核实理解",
                  "observable_behaviors": ["反映情绪"], "knowledge_queries": ["情绪反映"], "safety_critical": False,
                  "rubric_version": "teacher_2026", "rubric": [{"level": "effective", "description": "具体反映并邀请修正", "score": 4}]}],
              "knowledge_references": [{"unit_id": "ref-1", "quote": "反映情绪后，检查是否理解准确。"}],
              "design_retrieval": {"release_id": "pinned-release"},
              "learning_units": {"micro_practices": [{"practice_id": "micro-1", "competency_id": "alliance",
                  "title": "写一句反映", "scenario_text": "来访者说：我感到孤单。", "prompt": "写出下一句回应。",
                  "target_behavior": "反映情绪", "knowledge_unit_ids": ["ref-1"]}]}}
    job = {"job_id": JOB_ID, "status": "completed", "created_at": "2026-10-20T00:00:00+00:00",
           "updated_at": "2026-10-20T00:00:00+00:00", "request": {"count": 2},
           "progress": {"processed": 2, "total": 2}, "result": {"batch_id": JOB_ID, "count": 2,
               "scenarios": [scenario_public_digest(row, include_review_details=True) for row in rows],
               "course_plan": plan, "course_review": coverage, "course_draft": course}}
    app.state.scenario_batch_jobs._jobs[JOB_ID] = job
    app.state.scenario_batch_jobs._save(job)
    return job, rows


@pytest.fixture
def api(tmp_path: Path):
    settings = SimulationAPISettings(database_path=tmp_path / "sessions.sqlite",
        scenario_registry_path=tmp_path / "scenarios.jsonl", generated_scenario_registry_path=tmp_path / "generated.jsonl",
        generated_scenario_root=tmp_path / "cases", generated_batch_work_root=tmp_path / "jobs",
        initialize_llm_engine=False, initialize_scenario_generator=False)
    app = create_app(settings)
    with TestClient(app) as client:
        yield client, app


def test_approval_refresh_handoff_is_idempotent_and_preserves_teaching_material(api):
    client, app = api
    job, rows = seed_handoff(app)
    path = f"/scenarios/generate-batch/jobs/{JOB_ID}"
    before = client.get(path, headers=RESEARCHER).json()
    assert before["course_handoff"]["ready"] is False
    assert before["course_handoff"]["coverage_current"] is True
    assert client.post("/courses/from-batch", headers=TEACHER, json={"batch_id": JOB_ID}).status_code == 422
    for row in rows:
        app.state.session_service.scenario_registry.update_generated(row["scenario_id"], {"review_status": "approved"})
    current = client.get(path, headers=RESEARCHER).json()
    assert current["course_handoff"]["ready"] is True
    assert all(row["review_status"] == "approved" for row in current["result"]["scenarios"])
    assert all(row["review_status"] == "draft" for row in job["result"]["scenarios"])
    assert client.post("/courses/from-batch", headers=RESEARCHER, json={"batch_id": JOB_ID}).status_code == 403
    saved = client.post("/courses/from-batch", headers=TEACHER, json={"batch_id": JOB_ID})
    assert saved.status_code == 201, saved.text
    again = client.post("/courses/from-batch", headers=TEACHER, json={"batch_id": JOB_ID})
    assert again.status_code == 201 and again.json() == saved.json()
    result = saved.json()
    assert len(app.state.training_repository.list_courses()) == 1
    assert result["knowledge_references"] == job["result"]["course_draft"]["knowledge_references"]
    assert result["learning_units"] == job["result"]["course_draft"]["learning_units"]
    assert result["learning_objectives"][0]["knowledge_queries"] == ["情绪反映"]
    assert result["learning_objectives"][0]["rubric_version"] == "teacher_2026"
    assert result["design_retrieval"]["release_id"] == "pinned-release"
    assert client.get(path, headers=TEACHER).json()["course_handoff"]["course_id"] == result["course_id"]
    assert client.post("/courses/from-batch", headers=TEACHER,
                       json={"batch_id": JOB_ID, "title": "A different course"}).status_code == 409


@pytest.mark.parametrize("corruption", ["missing_coverage", "missing_member", "duplicate_member", "foreign_member",
    "wrong_count", "duplicate_index", "stale_artifact", "stale_plan", "invalid_evidence", "wrong_batch", "missing_objectives"])
def test_handoff_rejects_incomplete_or_changed_artifacts(api, corruption):
    client, app = api
    job, rows = seed_handoff(app, approved=True)
    registry = app.state.session_service.scenario_registry
    draft = job["result"]["course_draft"]
    if corruption == "missing_coverage": job["result"]["course_review"] = None
    elif corruption == "missing_member": draft["scenario_ids"].pop()
    elif corruption == "duplicate_member": draft["scenario_ids"][1] = draft["scenario_ids"][0]
    elif corruption == "foreign_member": draft["scenario_ids"][1] = "external_case"
    elif corruption == "wrong_count": job["result"]["count"] = 1
    elif corruption == "duplicate_index": registry.update_generated(rows[1]["scenario_id"], {"batch_index": 1})
    elif corruption == "stale_artifact": registry.update_generated(rows[0]["scenario_id"], {"scenario_brief": "A different concern"})
    elif corruption == "stale_plan": job["result"]["course_plan"]["objectives"] = ["Changed objective"]
    elif corruption == "invalid_evidence": job["result"]["course_review"]["semantic_review"]["case_reviews"][0]["evidence"] = ["Invented patient quote"]
    elif corruption == "wrong_batch": registry.update_generated(rows[0]["scenario_id"], {"batch_id": "another_batch"})
    elif corruption == "missing_objectives": draft.update(learning_objectives=[], skill_tags=[])
    result = client.post("/courses/from-batch", headers=TEACHER, json={"batch_id": JOB_ID})
    assert result.status_code == 422, result.text
    assert app.state.training_repository.list_courses() == []


def test_completed_job_with_edited_case_can_be_explicitly_rechecked(api, monkeypatch):
    client, app = api
    _, rows = seed_handoff(app, approved=True)
    app.state.session_service.scenario_registry.update_generated(rows[0]["scenario_id"], {"scenario_brief": "Edited teaching concern"})
    current = client.get(f"/scenarios/generate-batch/jobs/{JOB_ID}", headers=TEACHER).json()
    assert current["result"]["course_review"]["status"] == "needs_review"
    assert current["result"]["course_review"]["stale"] is True
    queued = []
    monkeypatch.setattr(app.state.scenario_batch_jobs._executor, "submit", lambda *args: queued.append(args))
    result = client.post(f"/scenarios/generate-batch/jobs/{JOB_ID}/retry", headers=TEACHER)
    assert result.status_code == 200 and result.json()["status"] == "queued"
    assert len(queued) == 1


def test_concurrent_batch_handoff_requests_return_the_same_course(api, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    client, app = api
    seed_handoff(app, approved=True)
    repository = app.state.training_repository
    original = repository.add_course
    barrier = Barrier(8)
    def aligned_save(course):
        barrier.wait(timeout=5)
        return original(course)
    monkeypatch.setattr(repository, "add_course", aligned_save)
    def save(_):
        result = client.post("/courses/from-batch", headers=TEACHER, json={"batch_id": JOB_ID})
        assert result.status_code == 201, result.text
        return result.json()
    with ThreadPoolExecutor(max_workers=8) as pool:
        courses = list(pool.map(save, range(8)))
    assert all(course == courses[0] for course in courses)
    assert len(repository.list_courses()) == 1


def test_browser_batch_review_save_and_return_to_learning(api, tmp_path):
    """Drive the compiled UI through real API handlers with synthetic local cases."""
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from urllib.parse import urlparse
    playwright = pytest.importorskip("playwright.sync_api")
    assets = Path(__file__).parents[1] / "views/theraskill/simulation_console/dist"
    if not (assets / "index.html").is_file():
        pytest.skip("Build the simulation console before running browser acceptance")
    client, app = api

    class Handler(SimpleHTTPRequestHandler):
        def translate_path(self, path):
            path = path.replace("/theraskill/simulation/", "/", 1)
            return super().translate_path(path)
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(assets)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    errors, api_errors = [], []
    def forward(route):
        request = route.request
        parsed = urlparse(request.url)
        path = parsed.path.removeprefix("/api/plugins/theraskill-simulation")
        if parsed.query:
            path += "?" + parsed.query
        response = client.request(request.method, path, headers=request.headers, content=request.post_data)
        if response.status_code >= 400:
            api_errors.append((path, response.status_code, response.text))
        route.fulfill(status=response.status_code, body=response.content,
                      content_type=response.headers.get("content-type", "application/json"))
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except playwright.Error as exc:
                pytest.skip(f"Playwright Chromium is unavailable: {exc}")
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 1050})
                context.add_init_script("localStorage.setItem('theraskill-locale', 'zh')")
                context.route("**/api/plugins/theraskill-simulation/**", forward)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                base = f"http://127.0.0.1:{server.server_port}/theraskill/simulation/"
                page.goto(base + "?batch_job=", wait_until="networkidle")
                playwright.expect(page.get_by_role("heading", name="批量生成课程场景", exact=True)).to_be_visible()
                assert page.get_by_role("button", name="开始训练", exact=True).count() == 0
                job, rows = seed_handoff(app)
                registry = app.state.session_service.scenario_registry
                for index, row in enumerate(rows):
                    bundle = tmp_path / f"browser_patient_{index}.json"
                    bundle.write_text(json.dumps({"patient_dossier": row["patient_dossier"],
                        "_patient_persona_v2": {"synthetic": True},
                        "authoritative_layer": {"visible_profile": {"name": "模拟来访者"},
                            "hidden_profile": {"concern": "孤单"}, "continuity_seed": {"goal": "理解"}}}), encoding="utf-8")
                    registry.update_generated(row["scenario_id"], {"case_bundle_path": str(bundle),
                        "case_id": f"browser_case_{index}", "case_family": "模拟训练", "recommended_turn_budget": 3})
                page.goto(base + f"?batch_job={JOB_ID}", wait_until="networkidle")
                for row in rows:
                    page.locator(".batch-generator").get_by_role("button", name=row["scenario_brief"], exact=True).click()
                    page.get_by_role("button", name="批准复用", exact=True).click()
                    playwright.expect(page.get_by_role("button", name="批准复用", exact=True)).to_be_disabled()
                switch = page.get_by_role("button", name="切换到教师角色以保存课程", exact=True)
                playwright.expect(switch).to_be_visible()
                switch.click()
                save = page.get_by_role("button", name="将已审核病例保存为课程", exact=True)
                playwright.expect(save).to_be_enabled()
                save.click()
                link = page.get_by_role("link", name="课程已保存 · 返回学习工作台", exact=True)
                playwright.expect(link).to_have_attribute("href", "/?workspace=learning")
                courses = app.state.training_repository.list_courses()
                assert len(courses) == 1
                repeated = client.post("/courses/from-batch", headers=TEACHER, json={"batch_id": JOB_ID})
                assert repeated.status_code == 201 and repeated.json()["course_id"] == courses[0].course_id
                page.screenshot(path=str(tmp_path / "simulation_course_handoff_desktop.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                sidebar = page.get_by_title("收起侧栏", exact=True)
                if sidebar.is_visible():
                    sidebar.click()
                playwright.expect(page.locator(".sidebar")).not_to_have_class("sidebar open")
                page.locator(".sidebar").evaluate("node => Promise.all(node.getAnimations().map(animation => animation.finished))")
                page.get_by_role("heading", name="批量生成课程场景", exact=True).scroll_into_view_if_needed()
                page.screenshot(path=str(tmp_path / "simulation_course_handoff_mobile.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.get_by_role("button", name="切换到英文", exact=True).click()
                playwright.expect(page.get_by_role("link", name="Course saved · Open learning workspace", exact=True)).to_be_visible()
                assert errors == []
                assert api_errors == []
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
