from __future__ import annotations

from pathlib import Path
import importlib


def test_domain_transports_build_inside_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))

    from domains.theraskill.services.contribution_api.app import (
        ContributionAPISettings,
        create_app as create_contribution_app,
    )
    from domains.theraskill.services.expert_api.app import (
        ExpertAPISettings,
        create_app as create_expert_app,
    )
    from domains.theraskill.services.extraction_api.app import (
        ExtractionAPISettings,
        create_app as create_extraction_app,
    )
    from domains.theraskill.services.simulation_api.app import (
        SimulationAPISettings,
        create_app as create_simulation_app,
    )
    from domains.theraskill.services.support_api.app import (
        SupportAPISettings,
        create_app as create_support_app,
    )

    config = Path(__file__).parents[1] / "configs" / "theraskill" / "retrieval.yaml"
    support = create_support_app(
        SupportAPISettings(
            database_path=tmp_path / "support.sqlite",
            initialize_llm_engine=False,
        )
    )
    simulation = create_simulation_app(
        SimulationAPISettings(
            database_path=tmp_path / "simulation.sqlite",
            scenario_registry_path=tmp_path / "scenarios.jsonl",
            initialize_llm_engine=False,
        )
    )
    expert = create_expert_app(ExpertAPISettings(database_path=tmp_path / "expert.sqlite"))
    contribution = create_contribution_app(
        ContributionAPISettings(
            database_path=tmp_path / "contribution.sqlite",
            candidate_model_config_path=config,
            knowledge_release_root=tmp_path / "releases",
            knowledge_release_slots_dir=tmp_path / "release-slots",
            operations_output_root=tmp_path / "operations",
            benchmark_cases_path=tmp_path / "benchmark.jsonl",
        )
    )
    extraction = create_extraction_app(
        ExtractionAPISettings(
            work_root=tmp_path / "extraction",
            extraction_project_root=tmp_path / "missing-extraction-project",
            model_config_path=tmp_path / "missing-model.yaml",
        )
    )

    route_counts = [len(app.routes) for app in (support, simulation, expert, contribution, extraction)]
    assert route_counts[0] >= 27  # safety handoff, check-in, plan, and resource routes
    assert route_counts[1] >= 36  # dual-agent research cycle route
    assert route_counts[2] >= 37
    assert route_counts[3] >= 50
    assert '/jobs/{job_id}/retry' in {route.path for route in expert.routes}
    assert route_counts[4] >= 5


def test_support_engine_excludes_sensitive_memory_by_default(tmp_path, monkeypatch):
    support_module = importlib.import_module("domains.theraskill.services.support_api.app")
    captured = {}

    class FakeEngine:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeContributionClient:
        def __init__(self):
            self.calls = []

        def memory_context(self, **kwargs):
            self.calls.append(kwargs)
            return {"items": []}

    client = FakeContributionClient()
    monkeypatch.setattr(support_module, "LLMSupportEngine", FakeEngine)
    support_module.create_app(
        support_module.SupportAPISettings(
            database_path=tmp_path / "support.sqlite",
            work_root=tmp_path / "runtime",
            initialize_llm_engine=True,
        ),
        contribution_client_override=client,
    )

    captured["memory_provider"]("subject-1")

    assert client.calls[0]["include_sensitive"] is False
