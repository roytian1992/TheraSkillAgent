"""Home workbench regression: the Assistant is the primary entry point."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

import pytest


def test_home_makes_assistant_the_primary_entry_point(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    assets = Path(__file__).parents[1] / "views/theraskill/workbench/dist"
    if not (assets / "index.html").is_file():
        pytest.skip("Build the workbench before running its browser regression")

    def handle(route):
        path = route.request.url.split("/api")[-1].split("?")[0]
        if path == "/resources/providers":
            data = {"llm": {"model": "local-llm", "base_url": "http://127.0.0.1/v1"}, "embedding": {"model": "local-embedding", "base_url": "http://127.0.0.1/v1"}, "files": []}
        elif path == "/resources/readiness":
            data = {"llm": {"model": "local-llm", "status": "configured"}, "embedding": {"model": "local-embedding", "status": "configured"}, "index": {"ready": False}, "simulation": {"ready": False}, "skills": [], "text_policy": {"max_chars": 1000, "overlap": 100}}
        else:
            data = {"status": "ok"}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(data))

    class QuietHandler(SimpleHTTPRequestHandler):
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
            page = browser.new_page(viewport={"width": 1360, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/**", handle)
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            assistant_nav = page.locator("a.assistant-nav-link")
            assistant_card = page.locator("section.assistant-launch")
            assistant_nav.wait_for()
            assert assistant_card.count() == 1
            assert assistant_nav.get_attribute("href") == "/theraskill/support/"
            assert assistant_card.locator("a.assistant-launch-action").get_attribute("href") == "/theraskill/support/"
            assert page.get_by_role("button", name="开始一次任务", exact=True).is_visible()
            assert page.get_by_text("课程与学习进展", exact=True).is_visible()
            page.screenshot(path=str(tmp_path / "home-assistant-first.png"), full_page=True)
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
