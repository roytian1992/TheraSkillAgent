"""Browser regression with synthetic API responses; no model or live data calls."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import urlparse

import pytest


def test_sample_acceptance_skip_and_refresh_in_workbench(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    assets = Path(__file__).parents[1] / "views/theraskill/workbench/dist"
    if not (assets / "index.html").is_file():
        pytest.skip("Build the workbench before running its browser regression")

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(assets)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    jobs, submissions, errors = {}, [], []

    def api(route):
        request = route.request
        path = urlparse(request.url).path
        payload = {}
        if path.endswith("/v1/extraction/jobs"):
            if request.method == "POST":
                body = request.post_data_json
                submissions.append(body)
                jid = f"extract_browser_{len(submissions)}"
                payload = {"job_id": jid, "plan": body["plan"], "limit": body["limit"], "status": "completed",
                           "created_at": "2026-10-20", "progress": {"processed": 1}, "counts": {"units": 1, "failures": 0},
                           "trial_review": body.get("trial_review")}
                jobs[jid] = payload
            else:
                payload = {"jobs": list(jobs.values())}
        elif "/v1/extraction/jobs/" in path:
            jid = path.split("/v1/extraction/jobs/")[1].split("/")[0]
            payload = jobs[jid]
            if path.endswith("/preview"):
                payload = {"job_id": jid, "status": "completed", "items": [{"content": {"evidence": "Synthetic source quote"}, "qa": {"status": "pass"}}],
                           "trial_review_context": {"sample_job_id": jid, "review_token": "synthetic-preview-token", "source_fingerprint": "synthetic-source", "sample_units": 1}}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except playwright.Error as exc:
                pytest.skip(f"Playwright Chromium is unavailable: {exc}")
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 1100})
                context.add_init_script("localStorage.setItem('theraskill-locale', 'en')")
                context.route("**/api/**", api)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                base = f"http://127.0.0.1:{server.server_port}"
                page.goto(base + "/?workspace=extraction", wait_until="networkidle")
                page.get_by_label("Input file", exact=True).fill("/synthetic/source.jsonl")
                page.get_by_label("Extraction goal", exact=True).fill("Extract source grounded observations")
                confirm = page.get_by_role("checkbox", name="I confirm the fields, unit and evidence requirements")
                full = page.get_by_role("button", name="Start full extraction", exact=True)
                confirm.check()
                playwright.expect(full).to_be_disabled()
                page.get_by_role("button", name="Run sample", exact=True).click()
                page.get_by_role("button", name="Preview candidates", exact=True).click()
                accept = page.get_by_role("checkbox", name="I have reviewed and accepted this sample for full extraction")
                playwright.expect(accept).to_be_visible()
                playwright.expect(full).to_be_disabled()
                confirm.check()
                accept.check()
                playwright.expect(full).to_be_enabled()
                page.get_by_label("Semantic review", exact=True).select_option("always")
                playwright.expect(accept).to_have_count(0)
                confirm.check()
                playwright.expect(full).to_be_disabled()
                page.get_by_role("checkbox", name="Explicitly skip the sample for this run").check()
                playwright.expect(full).to_be_disabled()
                page.get_by_label("Reason for skipping (saved with the job)").fill("Manually checked synthetic fixture.")
                playwright.expect(full).to_be_enabled()
                full.click()
                playwright.expect(page.get_by_text("This job records an explicit sample skip", exact=False)).to_be_visible()
                assert submissions[-1]["trial_review"]["decision"] == "skipped"
                assert submissions[-1]["plan"]["semantic_review"] == "always"

                # Loading an earlier sample after refresh never restores consent.
                page.goto(base + "/?workspace=extraction&job_id=extract_browser_1", wait_until="networkidle")
                playwright.expect(accept).to_have_count(0)
                confirm.check()
                playwright.expect(full).to_be_disabled()
                page.get_by_role("button", name="Preview candidates", exact=True).click()
                accept.check()
                playwright.expect(full).to_be_enabled()
                full.click()
                playwright.expect(page.get_by_text("This job records sample acceptance", exact=False)).to_be_visible()
                assert submissions[-1]["trial_review"]["sample_job_id"] == "extract_browser_1"
                assert submissions[-1]["trial_review"]["review_token"] == "synthetic-preview-token"
                page.get_by_role("button", name="中文", exact=False).click()
                playwright.expect(page.get_by_role("heading", name="全量运行前确认")).to_be_visible()
                playwright.expect(page.get_by_label("语义审阅", exact=True)).to_have_value("auto")
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(tmp_path / "extraction-review-mobile.png"), full_page=True)
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
