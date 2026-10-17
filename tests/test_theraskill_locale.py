from __future__ import annotations

from pathlib import Path
import json
from tempfile import TemporaryDirectory

import pytest

from domains.theraskill.theraskill_simulation.scenarios import (
    ScenarioNotFoundError,
    ScenarioRegistry,
)
from domains.theraskill.theraskill_simulation.repository import SQLiteSessionRepository
from domains.theraskill.theraskill_simulation.service import SimulationSessionService
from domains.theraskill.runtime import runtime_root


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = runtime_root()
REGISTRY = RUNTIME_ROOT / "current/simulation/scenarios/scenario_registry.jsonl"
PILOT_SCENARIO_ID = "scenario::supportive_exploration::998a78b598dfc09a"
CASE_OVERLAY = RUNTIME_ROOT / "current/simulation/locales/en/cases/00235848-787b-4f64-a50b-05477a54c42c.json"
CASE_MANIFEST = RUNTIME_ROOT / "current/simulation/locales/en/cases/manifest.json"
CASE_DIR = RUNTIME_ROOT / "current/simulation/locales/en/cases"

pytestmark = pytest.mark.skipif(
    not REGISTRY.exists(),
    reason="TheraSkill locale release is installed in HERMES_HOME at runtime",
)
EN_RUNTIME_PROMPTS = [
    "patient_opening_en.md",
    "patient_turn_realization_en.md",
    "patient_disclosure_controller_en.md",
    "patient_state_updater_from_bundle_en.md",
    "patient_state_memory_writeback_from_bundle_en.md",
    "patient_session_runtime_reviewer_en.md",
    "therapist_move_interpretation_en.md",
    "therapist_turn_generation_en.md",
    "therapist_case_memory_update_en.md",
    "session_longitudinal_summary_en.md",
    "session_reviewer_gate_en.md",
]


def test_english_pilot_overlay_replaces_scenario_text() -> None:
    registry = ScenarioRegistry(REGISTRY)
    zh = registry.get(PILOT_SCENARIO_ID, language="zh")
    en = registry.get(PILOT_SCENARIO_ID, language="en")

    assert zh["scenario_brief"] != en["scenario_brief"]
    assert en["scenario_brief"].startswith("A ")


def test_english_registry_exposes_only_translated_pilot_rows() -> None:
    registry = ScenarioRegistry(REGISTRY)
    rows = registry.list(
        language="en",
        split_key="pilot_split",
        split_value="pilot_train",
        limit=200,
    )

    assert len(rows) == 15
    assert all(not any("\u4e00" <= char <= "\u9fff" for char in row["scenario_brief"]) for row in rows)


def test_english_runtime_prompt_set_is_complete() -> None:
    prompt_dir = ROOT / "prompts/Simulation"
    assert all((prompt_dir / name).is_file() for name in EN_RUNTIME_PROMPTS)
    assert all(
        not any("\u4e00" <= char <= "\u9fff" for char in (prompt_dir / name).read_text(encoding="utf-8"))
        for name in EN_RUNTIME_PROMPTS
    )


def test_untranslated_scenario_is_not_selected_for_english() -> None:
    registry = ScenarioRegistry(REGISTRY)
    with pytest.raises(ScenarioNotFoundError, match="language=en"):
        registry.get("scenario::supportive_exploration::acd6ae353832e16d", language="en")


def test_manual_case_overlay_preserves_case_identity() -> None:
    manifest = json.loads(CASE_MANIFEST.read_text(encoding="utf-8"))
    overlay = json.loads(CASE_OVERLAY.read_text(encoding="utf-8"))
    case_overlays = [CASE_DIR / f"{case_id}.json" for case_id in manifest["case_ids"]]
    assert manifest["translation_method"] == "manual"
    assert manifest["translated_case_bundles"] == 12
    assert len(case_overlays) == manifest["translated_case_bundles"]
    assert all(path.exists() for path in case_overlays)
    assert overlay["case_id"] == "00235848-787b-4f64-a50b-05477a54c42c"
    assert overlay["authoritative_layer"]["visible_profile"]["demographic_profile"]["gender"] == "female"
    for path in case_overlays:
        localized = json.loads(path.read_text(encoding="utf-8"))
        assert not any("\u4e00" <= char <= "\u9fff" for char in json.dumps(localized, ensure_ascii=False))


def test_english_session_uses_translated_case_seed_with_runtime_persona_overlay() -> None:
    registry = ScenarioRegistry(REGISTRY)
    with TemporaryDirectory() as temporary_dir:
        repository = SQLiteSessionRepository(Path(temporary_dir) / "sessions.sqlite")
        service = SimulationSessionService(repository=repository, scenario_registry=registry)
        snapshot = service.create_session(
            scenario_id="scenario::cognitive_reframing_probe::eca3a44e6c29b1eb",
            language="en",
            session_id="english-locale-check",
        )

    assert snapshot.private_patient_state is not None
    assert snapshot.private_patient_state.runtime_state["emotion_state"] == [
        "painful",
        "helpless",
        "attached",
    ]
    assert snapshot.private_patient_state.latent_concerns[0].startswith("Fear that")
    assert snapshot.session.metadata.get("patient_persona_id", "").startswith(
        "persona::en_case_overlay::"
    )
    assert snapshot.session.metadata.get("patient_persona_v2_path") is None
