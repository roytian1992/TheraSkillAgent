"""Browser regression for the assistant-style evaluation entry point."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import urlparse

import pytest


def test_evaluation_starts_with_a_plain_language_plan(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    assets = Path(__file__).parents[1] / "views/theraskill/workbench/dist"
    if not (assets / "index.html").is_file():
        pytest.skip("Build the workbench before running its browser regression")

    suite = {"cases": [{"id": "case-1", "skill": "emotional-support", "query": "Offer a grounded response"}]}
    retrieval_suite = {"schema_version": "theraskill_retrieval_suite_v1", "cases": []}
    options = {"resources": {"indexes": []}, "releases": [], "skill_drafts": []}

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(assets)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def api(route):
        path = urlparse(route.request.url).path
        if path.endswith("/api/evaluations/retrieval/suite"):
            payload = retrieval_suite
        elif path.endswith("/api/evaluations/suite"):
            payload = suite
        elif path.endswith("/api/evaluations"):
            payload = {"reports": []}
        elif path.endswith("/api/workflow-options"):
            payload = options
        else:
            payload = {}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except playwright.Error as exc:
                pytest.skip(f"Playwright Chromium is unavailable: {exc}")
            try:
                context = browser.new_context(viewport={"width": 1280, "height": 900})
                context.add_init_script("localStorage.setItem('theraskill-locale', 'en')")
                context.route("**/api/**", api)
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}/?workspace=evaluation", wait_until="networkidle")
                playwright.expect(page.get_by_role("heading", name="Check whether a change got worse")).to_be_visible()
                page.get_by_role("button", name="Establish a baseline").click()
                playwright.expect(page.get_by_role("status")).to_contain_text("Start by establishing a baseline")
                assert page.locator(".evaluation-advanced").get_attribute("open") is None
                page.get_by_placeholder("For example: I updated the risk communication Skill and want to check that output and evidence did not get worse.").fill("Check the latest knowledge index for regressions")
                page.get_by_role("button", name="Prepare check").click()
                playwright.expect(page.get_by_role("status")).to_contain_text("This is a knowledge or index check")
                playwright.expect(page.locator(".evaluation-advanced")).to_have_attribute("open", "")
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(tmp_path / "evaluation-assistant-mobile.png"), full_page=True)
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
