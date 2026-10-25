"""Browser regression for the conversational course-design entry point."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

import pytest


def test_course_agent_collects_intent_and_waits_for_confirmation(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    assets = Path(__file__).parents[1] / "views/theraskill/workbench/dist"
    if not (assets / "index.html").is_file():
        pytest.skip("Build the workbench before running its browser regression")

    calls = []
    blueprint = {
        "template_id": "foundation_first_session",
        "title": "情绪验证入门",
        "description": "练习情绪验证",
        "objectives": [{"id": "validation", "description": "验证情绪", "observable_behaviors": ["反映感受"]}],
        "knowledge_cards": [],
        "micro_practices": [],
        "knowledge_references": [],
        "scenario_constraints": {"count": 3, "difficulty_band": "low", "risk_band": "low"},
    }

    def handle(route):
        path = route.request.url.split("/api/simulation")[-1].split("?")[0]
        body = route.request.post_data_json if route.request.method == "POST" else None
        if route.request.method == "POST":
            calls.append({"path": path, "body": body})
        if path == "/learning/report":
            data = {"assignments": [], "trends": []}
        elif path == "/scenarios":
            data = {"scenarios": []}
        elif path == "/courses":
            data = {"courses": []}
        elif path == "/courses/agent-turn":
            assert body["message"] == "我想让初学者练习情绪验证，做 3 个低风险案例"
            data = {
                "schema_version": "theraskill_course_agent_turn_v1",
                "reply": "我整理成一个课程计划，确认后才会检索知识并生成草案。",
                "draft_patch": {"description": "让初学者练习情绪验证", "scenario_count": 3},
                "draft": {
                    "title": "情绪验证入门",
                    "description": "让初学者练习情绪验证",
                    "scenario_count": 3,
                    "template_id": "foundation_first_session",
                    "difficulty_band": "low",
                    "risk_band": "low",
                },
                "missing": [],
                "ready": True,
                "suggested_action": "confirm_blueprint",
                "tool_plan": ["course_blueprint", "knowledge_retrieval", "scenario_batch_generation"],
            }
        elif path == "/courses/blueprint":
            data = blueprint
        else:
            data = {}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(data, ensure_ascii=False))

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(assets)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    errors = []
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except playwright.Error as exc:
                pytest.skip(f"Playwright Chromium is unavailable: {exc}")
            page = browser.new_page(viewport={"width": 1360, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/**", handle)
            page.goto(f"http://127.0.0.1:{server.server_port}/?workspace=learning")
            composer = page.get_by_label("告诉课程 Agent 你的想法", exact=True)
            composer.fill("我想让初学者练习情绪验证，做 3 个低风险案例")
            page.get_by_role("button", name="发送给 Agent", exact=True).click()
            page.get_by_text("确认后才会检索知识并生成草案。", exact=False).wait_for()
            confirm = page.get_by_role("button", name="确认并生成课程草案", exact=True)
            assert confirm.is_visible()
            assert not any(call["path"] == "/courses/blueprint" for call in calls)
            confirm.click()
            page.get_by_text("Agent 已生成课程草案；尚未生成病例，也未保存为正式课程。", exact=True).wait_for()
            assert any(call["path"] == "/courses/agent-turn" for call in calls)
            assert any(call["path"] == "/courses/blueprint" for call in calls)
            assert page.get_by_label("课程标题", exact=True).input_value() == "情绪验证入门"
            page.screenshot(path=str(tmp_path / "course-agent-confirmed.png"), full_page=True)
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
