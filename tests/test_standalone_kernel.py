from __future__ import annotations

import json
from pathlib import Path

from theraskill_agent.migration import migrate_data
from theraskill_agent.runtime import AgentRuntime
from theraskill_agent.source_adapters import inspect_source, sample_source


def test_runtime_records_structured_task_event(tmp_path: Path) -> None:
    result = AgentRuntime(tmp_path).run({"kind": "text.normalize", "text": "  a  b  "})
    assert result.status == "succeeded"
    assert result.output == {"text": "a b"}
    events = tmp_path / "data" / "theraskill" / "runs" / "events.jsonl"
    rows = [json.loads(line) for line in events.read_text().splitlines()]
    assert [row["event"] for row in rows] == ["task_started", "task_finished"]


def test_source_registry_inspects_json_and_samples(tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"
    path.write_text('{"id": 1}\n{"id": 2}\n', encoding="utf-8")
    info = inspect_source(path)
    assert info.adapter == "json"
    assert info.records == 2
    assert sample_source(path, 1)[0].payload["id"] == 1


def test_migration_plan_does_not_write(tmp_path: Path) -> None:
    source = tmp_path / "old"
    (source / "support").mkdir(parents=True)
    (source / "support" / "sessions.sqlite").write_bytes(b"x")
    destination = tmp_path / "new"
    report = migrate_data(source, destination, plan=True)
    assert report.planned is True
    assert not destination.exists()
