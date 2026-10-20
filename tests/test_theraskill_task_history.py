"""History is presentation metadata: executions survive filters and archiving."""
import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from theraskill_agent.task_history import HistoryMetadataStore, TaskHistory, create_router
from theraskill_agent.task_store import TaskStore


class Sources:
    def __init__(self):
        self.rows = {"extraction": [], "expert": [], "simulation": []}
        self.fail = set()
        self.calls = []

    def request(self, service, method, path, *, params, headers):
        self.calls.append((service, params.copy()))
        if service in self.fail:
            return httpx.Response(503)
        rows = self.rows[service]
        start, size = params["offset"], params["limit"]
        return httpx.Response(200, json={"jobs": rows[start:start+size], "total": len(rows)})


@pytest.fixture
def history(tmp_path):
    return TaskHistory(TaskStore(tmp_path / "tasks.sqlite"), Sources(),
                       HistoryMetadataStore(tmp_path / "history.sqlite"))


def workflow(history, goal="A learning task", status="completed", checkpoints=None):
    row = history.task_store.create("skill", goal)
    row.update(status=status, checkpoints=checkpoints or {})
    return history.task_store.save(row)


def extraction(history, task_id, *, status="completed", goal="Reflection strategy"):
    row = {"job_id": task_id, "status": status, "created_at": "2026-10-20T09:00:00+00:00",
           "updated_at": "2026-10-20T09:00:00+00:00", "plan": {"goal": goal, "secret": "not a summary"}}
    history.services.rows["extraction"].append(row)
    return row


def test_filters_counts_and_real_pagination_do_not_stop_at_source_cap(history):
    for i in range(207):
        extraction(history, f"extract_{i:04d}", goal=f"练习 {i}")
    for i in range(203):
        workflow(history, f"Workflow {i}")
    workflow(history, "Need schema", status="needs_input")
    workflow(history, "Cancelled run", status="cancelled")
    assert history.list()["counts"] == {"active": 1, "history": 411, "archived": 0}
    first = history.list(view="history", kind="extraction", page=1, page_size=100)
    last = history.list(view="history", kind="extraction", page=3, page_size=100)
    assert first["total"] == last["total"] == 207
    assert len(first["items"]) == 100 and len(last["items"]) == 7
    assert not {row["key"] for row in first["items"]} & {row["key"] for row in last["items"]}
    assert any(service == "extraction" and params["offset"] == 200 for service, params in history.services.calls)
    matched = history.list(view="history", q="练习 206")
    assert matched["total"] == 1
    assert history.list(status="needs_input")["counts"] == {"active": 1, "history": 0, "archived": 0}
    assert history.list(view="history", page=100)["items"] == []


def test_archive_persists_is_reversible_and_keeps_execution_and_events(history):
    original = workflow(history)
    key = "workflow:" + original["id"]
    before_events = history.task_store.events(original["id"])
    assert history.archive([key], archived=True) == {"updated": [key]}
    reloaded = TaskHistory(history.task_store, history.services, HistoryMetadataStore(history.metadata.path))
    assert reloaded.list(view="history")["total"] == 0
    archived = reloaded.list(view="archived")["items"][0]
    assert archived["archive_reason"] == "user_archived"
    assert reloaded.archive([key], archived=False) == {"updated": [key]}
    assert reloaded.list(view="history")["total"] == 1
    assert history.task_store.get(original["id"]) == original
    assert history.task_store.events(original["id"]) == before_events


def test_test_origin_is_explicit_never_inferred_from_user_title(history):
    a = workflow(history, "QA synthetic testing is my real course topic")
    b = workflow(history, "Historical development run")
    rows, _ = history.collect()
    verified = next(row for row in rows if row["id"] == b["id"])
    history.metadata.set_records([verified], origin="test", archived=True, reason="verified_qa")
    assert [row["id"] for row in history.list(view="history")["items"]] == [a["id"]]
    assert history.list(view="archived")["total"] == 0
    preview = history.list(view="archived")
    assert preview["counts"] == {"active": 0, "history": 1, "archived": 0}
    assert preview["counts_including_test"] == {"active": 0, "history": 1, "archived": 1}
    assert history.list(q="Historical")["counts_including_test"]["archived"] == 1
    assert history.list(q="unrelated")["counts_including_test"]["archived"] == 0
    restored = history.list(view="archived", include_test=True)["items"][0]
    assert restored["origin"] == "test" and restored["archive_reason"] == "verified_qa"
    history.archive([verified["key"]], archived=False)
    assert history.list(view="history")["total"] == 1
    assert history.list(view="history", include_test=True)["total"] == 2


def test_count_preview_groups_mixed_origins_once_and_uses_included_child_state(history):
    extraction(history, "test_child", status="failed", goal="QA child")
    workflow(history, "User curriculum", checkpoints={"trial_job": {"job_id": "test_child"}})
    rows, _ = history.collect()
    history.metadata.set_records([next(row for row in rows if row["id"] == "test_child")], origin="test")
    preview = history.list(view="history")
    assert preview["counts"] == {"active": 0, "history": 1, "archived": 0}
    assert preview["counts_including_test"] == {"active": 1, "history": 0, "archived": 0}
    assert sum(preview["counts_including_test"].values()) == 1
    assert history.list(include_test=True)["counts"] == preview["counts_including_test"]
    assert history.list(kind="extraction", status="failed", q="QA")["counts_including_test"]["active"] == 1
    assert history.list(kind="expert")["counts_including_test"] == {"active": 0, "history": 0, "archived": 0}


def test_group_before_pagination_and_find_child_under_completed_parent(history):
    extraction(history, "child_1", status="failed", goal="Missing therapist schema")
    extraction(history, "child_2")
    extraction(history, "independent", goal="Unrelated schema")
    parent = workflow(history, "Build curriculum knowledge", checkpoints={
        "trial_job": {"job_id": "child_1"},
        "previous_plans": [{"full_job": {"job_id": "child_2"}}],
    })
    result = history.list(kind="extraction", status="failed", q="therapist", page_size=1)
    assert result["total"] == 1 and result["counts"]["active"] == 1
    group = result["items"][0]
    assert group["id"] == parent["id"] and group["status"] == "completed" and group["group_status"] == "failed"
    assert {child["id"] for child in group["children"]} == {"child_1", "child_2"}
    history.archive([group["key"]], archived=True)
    assert history.list(view="archived")["total"] == 1
    assert all(row["archived"] for row in history.list(view="archived")["items"][0]["children"])
    history.archive([group["key"]], archived=False)
    assert history.list()["total"] == 1


def test_only_creation_tool_results_establish_child_ownership(history):
    extraction(history, "created")
    extraction(history, "only_read")
    parent = workflow(history, checkpoints={"skill_history": [
        {"tool": "theraskill_extraction", "arguments": {"operation": "start"}, "result": {"job_id": "created"}},
        {"tool": "theraskill_extraction", "arguments": {"operation": "status"}, "result": {"job_id": "only_read"}},
    ]})
    rows = history.list(view="history")["items"]
    assert len(rows) == 2
    assert next(row for row in rows if row["id"] == parent["id"])["children"][0]["id"] == "created"
    assert next(row for row in rows if row["id"] == "only_read")["parent_key"] is None


@pytest.mark.parametrize("status", ["queued", "running", "pending", "cancel_requested", "unavailable"])
def test_archive_rejects_running_parent_or_child_without_partial_changes(history, status):
    child = extraction(history, "child", status=status)
    parent = workflow(history, checkpoints={"trial_job": {"job_id": child["job_id"]}})
    row = history.list()["items"][0]
    assert row["can_archive"] is False
    with pytest.raises(HTTPException) as caught:
        history.archive(["workflow:" + parent["id"]], archived=True)
    assert caught.value.status_code in {409, 503}
    assert history.metadata.all() == {}


def test_resumed_archived_test_task_resurfaces_without_show_tests(history):
    saved = workflow(history, status="needs_input")
    row = history.collect()[0][0]
    history.metadata.set_records([row], origin="test", archived=True, reason="verified_qa")
    assert history.list()["total"] == 0
    saved.update(status="running")
    saved = history.task_store.save(saved)
    resumed = history.list()["items"][0]
    assert resumed["origin"] == "test" and resumed["archive_stale"] and not resumed["archived"]
    saved.update(status="completed")
    history.task_store.save(saved)
    assert history.list(view="history")["total"] == 1


def test_partial_source_failure_keeps_known_rows_and_reports_incomplete_result(history):
    saved = workflow(history, status="needs_approval")
    history.services.fail.add("extraction")
    result = history.list()
    assert result["items"][0]["id"] == saved["id"]
    assert result["errors"] == [{"kind": "extraction", "message": "extraction returned HTTP 503"}]
    with pytest.raises(HTTPException) as caught:
        history.archive(["workflow:" + saved["id"]], archived=True)
    assert caught.value.status_code == 503


def test_summaries_do_not_leak_source_payloads_and_expert_states_are_normalized(history):
    history.services.rows["expert"] = [{"job_id": "review", "workflow": "response_review", "status": "idle",
                                        "payload_json": "private conversation", "api_key": "private key"}]
    extraction(history, "sample")
    serialized = json.dumps(history.list(view="history"), ensure_ascii=False)
    assert "private conversation" not in serialized and "private key" not in serialized and "not a summary" not in serialized
    expert = next(row for row in history.list(view="history")["items"] if row["kind"] == "expert")
    assert expert["status"] == "completed" and expert["title_en"] == "Response review"


def test_api_validates_paging_view_kind_and_archive_input(history, monkeypatch):
    monkeypatch.setattr("theraskill_agent.task_history.HistoryMetadataStore", lambda: history.metadata)
    app = FastAPI()
    app.include_router(create_router(history.task_store, history.services))
    client = TestClient(app)
    for suffix in ("page=0", "page_size=101", "view=missing", "kind=missing"):
        assert client.get("/api/task-history?" + suffix).status_code == 422
    assert client.post("/api/task-history/archive", json={"keys": [], "archived": True}).status_code == 422
    assert client.post("/api/task-history/archive", json={"keys": ["workflow:missing"], "archived": True}).status_code == 404


def test_support_status_batch_is_exact_owned_and_not_limited_to_recent_sessions(tmp_path):
    from domains.theraskill.theraskill_support.repository import SQLiteSupportRepository
    from domains.theraskill.theraskill_support.contracts import SupportPublicSession, SupportSessionRecord
    repository = SQLiteSupportRepository(tmp_path / "support.sqlite")
    for index in range(505):
        record = SupportSessionRecord(public=SupportPublicSession(session_id=f"session_{index}"),
                                      owner_id="expert-studio", knowledge_release_id="fixture")
        repository.create(record, [])
    other = SupportSessionRecord(public=SupportPublicSession(session_id="other_owner"),
                                 owner_id="someone_else", knowledge_release_id="fixture")
    repository.create(other, [])
    result = repository.statuses("expert-studio", ["session_0", "session_504", "other_owner", "missing"])
    assert {row["session_id"] for row in result} == {"session_0", "session_504"}
    assert all(set(row) == {"session_id", "status", "updated_at"} for row in result)
    assert repository.statuses("expert-studio", []) == []
