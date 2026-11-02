"""Browser regression for the Assistant's first-use experience."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

import pytest


def test_support_assistant_welcome_prompts(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    assets = Path(__file__).parents[1] / "views/theraskill/support_assistant/dist"
    if not (assets / "index.html").is_file():
        pytest.skip("Build the support assistant before running its browser regression")

    session = {
        "schema_version": "theraskill_support_session_v1",
        "session_id": "fixture-session",
        "status": "active",
        "title": "新对话",
        "messages": [],
        "generation": {"status": "idle", "job_id": None, "error_code": None, "retryable": False},
        "safety": {"attention_level": "none", "source_risk_level": "none_observed", "signal_codes": [], "handoff_status": "none", "last_action": None, "check_in_status": "not_requested"},
        "plan": {"goals": [], "action_items": []},
        "created_at": "2026-10-26T00:00:00Z",
        "updated_at": "2026-10-26T00:00:00Z",
    }

    def handle(route):
        path = route.request.url.split("/api/plugins/theraskill-support")[-1].split("?")[0]
        if path == "/health":
            data = {"status": "ok"}
        elif path == "/sessions" and route.request.method == "GET":
            data = {"sessions": []}
        elif path == "/sessions" and route.request.method == "POST":
            data = session
        elif path.endswith("/diagnostics"):
            data = {"session_id": "fixture-session", "generation_status": "idle", "job_id": None,
                    "knowledge_mode": "auto", "failure_stage": None, "error_code": "none",
                    "index": {"status": "missing"}, "llm_model": "mock-llm", "latency_ms": {}}
        else:
            data = session
        route.fulfill(status=200, content_type="application/json", body=json.dumps(data, ensure_ascii=False))

    class QuietHandler(SimpleHTTPRequestHandler):
        def translate_path(self, path):
            prefix = "/theraskill/support"
            if path == prefix or path == prefix + "/":
                path = "/index.html"
            elif path.startswith(prefix + "/"):
                path = path[len(prefix):]
            return super().translate_path(path)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(assets)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except playwright.Error as exc:
                pytest.skip(f"Playwright Chromium is unavailable: {exc}")
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/plugins/theraskill-support/**", handle)
            page.goto(f"http://127.0.0.1:{server.server_port}/theraskill/support/")
            page.locator(".composer textarea").wait_for()
            page.get_by_role("button", name="仅模型", exact=True).click()
            assert page.get_by_role("button", name="仅模型", exact=True).get_attribute("aria-pressed") == "true"
            assert page.locator(".brand-row span").inner_text() == "助手"
            assert page.locator(".assistant-workspaces a").count() == 5
            assert page.locator(".assistant-workspaces a").nth(0).get_attribute("href") == "/?workspace=learning"
            assert page.locator(".assistant-workspaces a").nth(1).get_attribute("href") == "/?workspace=transcript"
            page.get_by_role("button", name="诊断信息").click()
            page.locator(".diagnostics-drawer").get_by_text("mock-llm").wait_for()
            page.get_by_role("button", name="关闭诊断").click()
            page.screenshot(path=str(tmp_path / "assistant-welcome.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.get_by_role("button", name="关闭侧栏").click(position={"x": 375, "y": 50})
            page.wait_for_timeout(250)
            page.locator(".composer textarea").wait_for()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(tmp_path / "assistant-mobile.png"), full_page=True)
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
