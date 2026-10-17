from __future__ import annotations

import json
from pathlib import Path

from scripts.TheraSkill.build_release_eval_sets import build
from scripts.TheraSkill.run_release_gate import run


def _release_tree(root: Path) -> Path:
    release = root / "platform-data/theraskill/releases/phase1"
    for relative in (
        "knowledge/phase6_reviewed_knowledge_units.jsonl",
        "retrieval/metadata_v2.sqlite",
        "retrieval/phase6_retrieval_fts.sqlite",
        "simulation/scenarios/scenario_registry.jsonl",
    ):
        target = release / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}\n" if target.suffix == ".jsonl" else "", encoding="utf-8")
    (release / "manifest.json").write_text("{}\n", encoding="utf-8")
    return release


def test_build_release_eval_sets_writes_all_four_domains(tmp_path: Path) -> None:
    output = tmp_path / "evaluations"
    manifest = build(output)
    assert set(manifest["domains"]) == {"support", "simulation", "response_review", "contribution"}
    assert all((output / f"{domain}.jsonl").exists() for domain in manifest["domains"])


def test_release_gate_passes_structural_runtime_without_health(tmp_path: Path) -> None:
    _release_tree(tmp_path)
    build(tmp_path / "platform-data/theraskill/evaluations")
    result = run(tmp_path, check_health=False)
    assert result["status"] == "passed", result["failures"]


def test_release_gate_fails_when_domain_case_is_missing(tmp_path: Path) -> None:
    _release_tree(tmp_path)
    output = tmp_path / "platform-data/theraskill/evaluations"
    build(output)
    (output / "support.jsonl").unlink()
    result = run(tmp_path, check_health=False)
    assert result["status"] == "failed"
    assert any("support" in failure for failure in result["failures"])
