from __future__ import annotations

import json
from pathlib import Path

from domains.theraskill.services.extraction_api.app import ExtractionAPISettings, ExtractionJobStore
from domains.theraskill.theraskill_contribution.repository import SQLiteContributionRepository
from theraskill_agent.extraction.contracts import ExtractionPlan
from theraskill_agent.extraction.executor import build_manifest, extract_records, load_source_records
from theraskill_agent.runtime import AgentRuntime
from theraskill_agent.source_adapters import inspect_source


class FakeClient:
    model = "fake-model"

    def chat(self, system_prompt, user_payload, *, force_json_response=None, extra_user_instruction=None):
        return json.dumps({"claim": "supported", "evidence": "source text"})


def test_directory_adapter_and_dynamic_executor_preserve_provenance(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.jsonl").write_text('{"id":"a","text":"source text"}\n', encoding="utf-8")
    (source / "b.txt").write_text("source text", encoding="utf-8")
    info = inspect_source(source)
    assert info.adapter == "directory"
    assert info.records == 2
    records = load_source_records(source)
    assert records[0].metadata["relative_path"] == "a.jsonl"
    plan = ExtractionPlan(
        goal="extract a grounded claim",
        source={"path": str(source)},
        target_schema={
            "type": "object",
            "properties": {"claim": {"type": "string"}, "evidence": {"type": "string"}},
            "required": ["claim", "evidence"],
        },
        evidence_fields=("evidence",),
        status="approved",
    )
    units, failures = extract_records(records, plan, FakeClient())
    assert len(units) == 2 and not failures
    assert units[0]["provenance"]["source_ref"].endswith("a.jsonl")
    manifest = build_manifest(plan, source, units=units, failures=failures, model_name="fake-model")
    assert manifest["source"]["adapter"] == "directory"
    assert manifest["source"]["sha256"] == info.fingerprint
    resumed_units, resumed_failures = extract_records(records, plan, FakeClient(), skip_source_ids={records[0].record_id})
    assert len(resumed_units) == 1 and not resumed_failures


def test_runtime_result_has_reproducible_execution_context(tmp_path: Path) -> None:
    result = AgentRuntime(tmp_path).run(
        {
            "kind": "text.normalize",
            "text": "  a  b  ",
            "allowed_skills": ["text-normalizer"],
            "parameters": {"prompt_version": "p1"},
        }
    )
    assert result.status == "succeeded"
    assert result.run_id and result.provenance["skill_name"] == "text-normalizer"
    assert result.provenance["provider_kind"] == "llm"
    assert result.provenance["input_fingerprint"]


def test_runtime_can_run_transcript_analysis_without_overwriting_source(tmp_path: Path) -> None:
    source = tmp_path / "dialogue.txt"
    source.write_text("患者: 我想自杀。\n医师: 我听到了。", encoding="utf-8")
    result = AgentRuntime(tmp_path).run({"kind": "transcript.analyze", "input_path": str(source)})

    assert result.status == "succeeded"
    assert result.output["analysis"]["risk_screening"]["status"] == "review_required"
    assert source.read_text(encoding="utf-8").startswith("患者:")


def test_runtime_transcript_review_plan_supports_non_utf8_input(tmp_path: Path) -> None:
    source = tmp_path / "dialogue-gb.txt"
    source.write_bytes("患者：最近很害怕。\n医师：我听到了。".encode("gb18030"))
    result = AgentRuntime(tmp_path).run(
        {
            "kind": "transcript.review",
            "input_path": str(source),
            "parameters": {"mode": "plan", "skills": ["transcript-quality-reviewer"]},
        }
    )

    assert result.status == "succeeded"
    assert result.output["review_plan"][0]["skill"] == "transcript-quality-reviewer"
    assert result.output["document"]["source_encoding"] == "gb18030"


def test_extraction_handoff_imports_governed_candidates_idempotently(tmp_path: Path) -> None:
    work_root = tmp_path / "jobs"
    settings = ExtractionAPISettings(
        extraction_project_root=tmp_path,
        work_root=work_root,
        contribution_database_path=tmp_path / "contribution.sqlite",
        max_workers=1,
    )
    store = ExtractionJobStore(settings)
    try:
        job_id = "extract_upgrade_test"
        job_dir = work_root / job_id
        job_dir.mkdir(parents=True)
        units_path = job_dir / "knowledge_units.jsonl"
        units_path.write_text(
            json.dumps(
                {
                    "knowledge_type": "test",
                    "source_id": "r1",
                    "content": {"claim": "supported"},
                    "evidence": {"status": "pass"},
                    "qa": {"status": "pass"},
                    "provenance": {"source_ref": "demo.jsonl"},
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        job = {
            "job_id": job_id,
            "status": "completed",
            "artifacts": {"units": str(units_path)},
        }
        store._jobs[job_id] = job
        store._save(job)
        first = store.handoff(job_id, "curator")
        second = store.handoff(job_id, "curator")
        assert first["governance_status"] == "imported"
        assert first["candidate_ids"] == second["candidate_ids"]
        candidates = SQLiteContributionRepository(settings.contribution_database_path).list_knowledge_candidates()
        assert len(candidates) == 1
        assert candidates[0].status.value == "expert_reviewed_candidate"
    finally:
        store._executor.shutdown(wait=True)
