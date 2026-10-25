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
            page.get_by_placeholder("输入你想说的内容").wait_for()
            page.get_by_role("button", name="帮我理解这段对话", exact=True).wait_for()
            assert page.locator(".brand-row span").inner_text() == "助手"
            page.get_by_role("button", name="帮我理解这段对话", exact=True).click()
            assert page.get_by_placeholder("输入你想说的内容").input_value() == "帮我理解这段对话"
            page.screenshot(path=str(tmp_path / "assistant-welcome.png"), full_page=True)
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
