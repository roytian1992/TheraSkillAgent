"""Task/history UX against synthetic APIs, without touching project records.

Build views/theraskill/workbench before running. The browser only connects to a
temporary static server; every API request is intercepted by the fixture.
"""
from copy import deepcopy
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlparse

import pytest


def _task(identifier, title, *, kind="workflow", status="completed", origin="user", archived=False, children=None):
    return {
        "key": f"{kind}:{identifier}", "id": identifier, "kind": kind,
        "title": title, "status": status, "origin": origin,
        "created_at": "2026-10-20T01:00:00Z", "updated_at": "2026-10-20T02:00:00Z",
        "href": f"/?workspace=workflow&task_id={identifier}",
        "archived": archived, "children": children or [],
        "can_archive": status not in {"queued", "running"},
    }


class SyntheticTaskHistory:
    def __init__(self):
        child = _task("child", "同一次操作的抽取步骤", kind="extraction")
        child["parent_key"] = "workflow:waiting"
        self.records = [
            _task("waiting", "需要确认对话角色", status="needs_input", children=[child]),
            _task("running", "正在构建课程场景", kind="scenario_batch", status="running"),
            _task("failed", "需要重试的回应审核", kind="expert", status="failed"),
            _task("test-waiting", "QA 旧验收等待输入", status="needs_input", origin="test"),
            _task("test-archived", "QA 已归档测试记录", origin="test", archived=True,
                  children=[_task("test-archived-child", "QA 已归档抽取步骤", kind="extraction", origin="test", archived=True)]),
        ]
        self.records[1]["title_en"] = "Generating course scenarios"
        self.records.extend(_task(f"completed-{i:02}", f"已完成案例 {i:02}", kind="extraction" if i % 2 else "workflow") for i in range(25))
        self.http_status = 200
        self.source_errors = []
        self.requests = []
        self.mutations = []

    @staticmethod
    def _view(record):
        if record["archived"]:
            return "archived"
        return "history" if record["status"] in {"completed", "complete", "idle", "cancelled"} else "active"

    def handle(self, route):
        parsed = urlparse(route.request.url)
        path = parsed.path
        query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
        status, data = 200, {}
        if path == "/api/task-history/archive":
            payload = route.request.post_data_json
            self.mutations.append(payload)
            changed = []
            for record in self.records:
                if record["key"] in payload["keys"]:
                    record["archived"] = payload["archived"]
                    changed.append(record["key"])
            data = {"updated": changed, "archived": payload["archived"]}
        elif path == "/api/task-history":
            self.requests.append(query)
            status = self.http_status
            if status != 200:
                data = {"detail": "Fixture task history temporarily unavailable"}
            else:
                include_test = query.get("include_test", "false") == "true"
                visible = [record for record in self.records if include_test or record["origin"] != "test"]
                counts = {view: sum(self._view(record) == view for record in visible) for view in ("active", "history", "archived")}
                counts_including_test = {view: sum(self._view(record) == view for record in self.records) for view in ("active", "history", "archived")}
                records = [record for record in visible if self._view(record) == query.get("view", "active")]
                if query.get("kind"):
                    records = [record for record in records if record["kind"] == query["kind"]]
                if query.get("status"):
                    records = [record for record in records if record["status"] == query["status"]]
                if query.get("q"):
                    records = [record for record in records if query["q"].casefold() in record["title"].casefold()]
                page = int(query.get("page", "1"))
                page_size = int(query.get("page_size", "20"))
                data = {"items": deepcopy(records[(page-1)*page_size:page*page_size]), "total": len(records), "page": page, "page_size": page_size, "counts": counts, "counts_including_test": counts_including_test, "errors": self.source_errors}
        elif path == "/api/resources/readiness":
            data = {"llm": {}, "embedding": {}, "index": {"ready": False}, "simulation": {"ready": True}, "skills": [], "text_policy": {"max_chars": 2000, "overlap": 100}}
        route.fulfill(status=status, content_type="application/json", body=json.dumps(data, ensure_ascii=False))


@pytest.fixture
def task_history_browser(tmp_path):
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
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except playwright.Error as exc:
                pytest.skip(f"Playwright Chromium is unavailable: {exc}")
            page = browser.new_page(viewport={"width": 1360, "height": 1000})
            page.set_default_timeout(10000)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            api = SyntheticTaskHistory()
            page.route("**/api/**", api.handle)
            base = f"http://127.0.0.1:{server.server_port}"
            yield page, api, base, tmp_path, playwright.expect
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_task_history_active_filters_grouping_and_reversible_archive(task_history_browser):
    page, api, base, artifacts, expect = task_history_browser
    page.goto(base + "/?workspace=tasks")
    history = page.get_by_test_id("task-history")
    expect(history).to_have_attribute("data-view", "active")
    expect(history.get_by_text("需要确认对话角色", exact=True)).to_be_visible()
    expect(history.get_by_text("正在构建课程场景", exact=True)).to_be_visible()
    expect(history.get_by_text("需要重试的回应审核", exact=True)).to_be_visible()
    expect(history.get_by_text("QA 旧验收等待输入", exact=True)).to_have_count(0)
    expect(history.get_by_text("已完成案例 00", exact=True)).to_have_count(0)
    expect(history.locator('[data-task-key="scenario_batch:running"]').get_by_test_id("task-archive")).to_be_disabled()
    assert api.requests[-1].get("include_test", "false") == "false"
    # The archive destination includes test runs by default. Its preview count
    # must already match that destination while tests are hidden in Active.
    archived_tab = page.get_by_test_id("task-tab-archived")
    expect(archived_tab.locator("span")).to_have_text("1")
    archived_tab.click()
    expect(history.get_by_text("QA 已归档测试记录", exact=True)).to_be_visible()
    expect(archived_tab.locator("span")).to_have_text("1")
    archived = history.locator('[data-task-key="workflow:test-archived"]')
    archived.get_by_test_id("task-children-toggle").click()
    archived_child = archived.locator('[data-task-key="extraction:test-archived-child"]')
    expect(archived_child).to_be_visible()
    expect(archived_child.get_by_test_id("task-restore")).to_have_count(0)
    page.get_by_test_id("task-tab-active").click()
    expect(history.get_by_text("需要确认对话角色", exact=True)).to_be_visible()
    expect(page.get_by_test_id("task-include-test")).not_to_be_checked()

    waiting = history.locator('[data-task-key="workflow:waiting"]')
    expect(waiting.get_by_text("同一次操作的抽取步骤", exact=True)).to_have_count(0)
    waiting.get_by_test_id("task-children-toggle").click()
    expect(waiting.get_by_text("同一次操作的抽取步骤", exact=True)).to_be_visible()
    expect(waiting.locator('[data-task-key="extraction:child"]').get_by_test_id("task-archive")).to_have_count(0)
    waiting.get_by_test_id("task-children-toggle").click()
    expect(waiting.get_by_text("同一次操作的抽取步骤", exact=True)).to_have_count(0)
    page.get_by_test_id("task-status-filter").select_option("needs_input")
    expect(history.get_by_test_id("task-row")).to_have_count(1)
    expect(history.get_by_text("需要确认对话角色", exact=True)).to_be_visible()
    page.get_by_test_id("task-status-filter").select_option("")
    page.get_by_test_id("task-include-test").check()
    expect(history.get_by_text("QA 旧验收等待输入", exact=True)).to_be_visible()
    page.get_by_test_id("task-include-test").uncheck()
    expect(history.get_by_text("QA 旧验收等待输入", exact=True)).to_have_count(0)

    page.get_by_test_id("task-tab-history").click()
    expect(history).to_have_attribute("data-view", "history")
    expect(history.get_by_test_id("task-row")).to_have_count(20)
    page.get_by_test_id("task-next").click()
    expect(history.get_by_test_id("task-row")).to_have_count(5)
    expect(history.get_by_text("已完成案例 24", exact=True)).to_be_visible()
    page.get_by_test_id("task-prev").click()
    expect(history.get_by_text("已完成案例 00", exact=True)).to_be_visible()
    page.get_by_test_id("task-kind-filter").select_option("extraction")
    expect(history.get_by_test_id("task-row")).to_have_count(12)
    assert set(history.get_by_test_id("task-row").evaluate_all("rows => rows.map(row => row.dataset.taskKind)")) == {"extraction"}
    page.get_by_test_id("task-search").fill("已完成案例 03")
    expect(history.get_by_test_id("task-row")).to_have_count(1)
    row = history.locator('[data-task-key="extraction:completed-03"]')
    row.get_by_test_id("task-archive").click()
    expect(row).to_have_count(0)
    assert api.mutations[-1] == {"keys": ["extraction:completed-03"], "archived": True}
    assert next(record for record in api.records if record["id"] == "completed-03")["archived"] is True

    page.reload()
    expect(page.get_by_test_id("task-tab-archived").locator("span")).to_have_text("2")
    page.get_by_test_id("task-tab-archived").click()
    # View changes may preserve a deliberate search; clear it to inspect all archives.
    page.get_by_test_id("task-search").fill("")
    page.get_by_test_id("task-kind-filter").select_option("")
    expect(history.get_by_text("QA 已归档测试记录", exact=True)).to_be_visible()
    expect(page.get_by_test_id("task-tab-archived").locator("span")).to_have_text("2")
    row = history.locator('[data-task-key="extraction:completed-03"]')
    expect(row).to_be_visible()
    row.get_by_test_id("task-restore").click()
    expect(row).to_have_count(0)
    assert api.mutations[-1] == {"keys": ["extraction:completed-03"], "archived": False}
    page.get_by_test_id("task-tab-history").click()
    expect(history.get_by_text("已完成案例 03", exact=True)).to_be_visible()
    page.screenshot(path=str(artifacts / "task-history-restored.png"), full_page=True)

    page.get_by_role("button", name="EN", exact=True).click()
    expect(page.get_by_test_id("task-tab-active")).to_contain_text("Active & attention")
    expect(page.get_by_test_id("task-tab-history")).to_contain_text("History")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Task history overflows a 390px viewport"
    page.screenshot(path=str(artifacts / "task-history-mobile-english.png"), full_page=True)

    page.goto(base + "/")
    compact = page.get_by_test_id("task-history")
    expect(compact).to_have_attribute("data-compact", "true")
    expect(compact.get_by_text("需要确认对话角色", exact=True)).to_be_visible()
    expect(compact.get_by_text("Generating course scenarios", exact=True)).to_be_visible()
    expect(compact.get_by_text("已完成案例 03", exact=True)).to_have_count(0)
    expect(compact.get_by_text("QA 旧验收等待输入", exact=True)).to_have_count(0)
    expect(compact.get_by_test_id("task-tab-history")).to_have_count(0)
    expect(compact.get_by_test_id("task-archive")).to_have_count(0)
    assert api.requests[-1]["view"] == "active"
    assert api.requests[-1].get("include_test", "false") == "false"
    page.screenshot(path=str(artifacts / "task-home-active-only.png"), full_page=True)


def test_task_history_distinguishes_empty_partial_and_failed_loads(task_history_browser):
    page, api, base, artifacts, expect = task_history_browser
    api.source_errors = [{"kind": "expert", "message": "Fixture review store is unavailable"}]
    page.goto(base + "/?workspace=tasks")
    history = page.get_by_test_id("task-history")
    expect(history.get_by_text("需要确认对话角色", exact=True)).to_be_visible()
    expect(history.get_by_role("alert")).to_contain_text("当前列表可能不完整")
    expect(history.get_by_test_id("task-empty")).to_have_count(0)

    api.http_status = 503
    page.get_by_test_id("task-refresh").click()
    expect(history.get_by_test_id("task-error").filter(has_text="任务暂时无法加载")).to_be_visible()
    expect(history.get_by_text("需要确认对话角色", exact=True)).to_be_visible()
    expect(history.get_by_test_id("task-empty")).to_have_count(0)
    page.screenshot(path=str(artifacts / "task-load-failure-retains-rows.png"), full_page=True)

    # A fresh load has no cached records, but a service failure must not claim
    # there are simply no tasks. This also catches misleading empty-state flashes.
    page.reload()
    expect(history.get_by_role("alert")).to_contain_text("任务暂时无法加载")
    expect(history.get_by_test_id("task-empty")).to_have_count(0)
    api.http_status = 200
    api.records = []
    page.get_by_test_id("task-refresh").click()
    expect(history.get_by_role("alert")).to_contain_text("当前列表可能不完整")
    expect(history.get_by_test_id("task-empty")).to_have_count(0)
    api.source_errors = []
    page.get_by_test_id("task-refresh").click()
    expect(history.get_by_role("alert")).to_have_count(0)
    expect(history.get_by_test_id("task-empty")).to_be_visible()
    expect(history.get_by_test_id("task-row")).to_have_count(0)
    page.screenshot(path=str(artifacts / "task-empty-successful-load.png"), full_page=True)
