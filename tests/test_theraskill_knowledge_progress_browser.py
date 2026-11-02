"""Browser check for human-readable knowledge build progress."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

import pytest


def test_knowledge_build_progress_bar(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    assets = Path(__file__).parents[1] / "views/theraskill/workbench/dist"
    if not (assets / "index.html").is_file():
        pytest.skip("Build the workbench before running its browser regression")

    job = {"id": "build-job", "operation": "build", "status": "running",
           "parameters": {"knowledge": "demo", "index_id": "demo-index"},
           "progress": {"stage": "lexical", "counts": {"total": 500},
                        "bytes": 5000, "total_bytes": 10000}}
    catalog = {"indexes": [], "knowledge": [], "simulation": [], "jobs": [job]}

    def handle(route):
        path = route.request.url.split("/api/resources")[-1].split("?")[0]
        data = job if path == "/jobs/build-job" else catalog
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
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/resources**", handle)
            page.route("**/api/evaluations", lambda route: route.fulfill(
                status=200, content_type="application/json", body='{"reports":[]}'))
            page.goto(f"http://127.0.0.1:{server.server_port}/?workspace=knowledge&job_id=build-job")
            progress = page.get_by_role("progressbar", name="构建关键词索引")
            progress.wait_for()
            assert progress.get_attribute("value") == "50"
            assert page.get_by_text("500 条知识已处理").is_visible()
            assert page.get_by_text("View progress").count() == 0
            page.screenshot(path=str(tmp_path / "knowledge-progress.png"), full_page=True)
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
