from __future__ import annotations

from pathlib import Path

from scripts.TheraSkill import check_model_contract as contract


def test_model_contract_passes_when_config_and_endpoint_match(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "model:\n  default: Qwen3.8-27B\n  provider: custom\n  base_url: http://127.0.0.1:8002/v1\n  api_mode: chat_completions\n  api_key_env: TEST_MODEL_KEY\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_MODEL_KEY", "secret")
    monkeypatch.setattr(
        contract,
        "_fetch_models",
        lambda url, api_key, timeout: (200, [{"id": "Qwen3.8-27B", "root": "/models/Qwen3.8-27B"}]),
    )

    result = contract.check_model_contract(config)

    assert result["status"] == "passed"
    assert result["observed_model_ids"] == ["Qwen3.8-27B"]
    assert result["endpoint"] == "http://127.0.0.1:8002/v1/models"


def test_model_contract_reports_model_drift(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "model:\n  default: Qwen3.8-27B\n  provider: custom\n  base_url: http://127.0.0.1:8002/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(contract, "_fetch_models", lambda *args: (200, [{"id": "Qwen3-235B"}]))

    result = contract.check_model_contract(config)

    assert result["status"] == "failed"
    assert any("not served" in failure for failure in result["failures"])
