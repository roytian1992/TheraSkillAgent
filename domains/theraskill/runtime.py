"""Project-local data paths and service definitions for TheraSkillAgent."""

from __future__ import annotations

import os
from pathlib import Path

from theraskill_agent.paths import data_root, project_root, state_root


CODE_ROOT = Path(__file__).resolve().parents[2]


def runtime_root() -> Path:
    """Compatibility function for the project's persistent domain data."""
    return data_root()


def service_runtime_root(service: str) -> Path:
    root = state_root() / "services" / service
    root.mkdir(parents=True, exist_ok=True)
    return root


def service_specs() -> dict[str, dict[str, object]]:
    """Return the project's internal domain transports."""
    def port(name: str, default: int) -> int:
        value = os.environ.get(f"THERASKILL_{name.upper()}_PORT", "").strip()
        return int(value) if value else default

    return {
        "simulation": {
            "module": "domains.theraskill.services.simulation_api.app:create_app",
            "port": port("simulation", 8876),
        },
        "support": {
            "module": "domains.theraskill.services.support_api.app:create_app",
            "port": port("support", 8878),
        },
        "expert": {
            "module": "domains.theraskill.services.expert_api.app:create_app",
            "port": port("expert", 8879),
        },
        "contribution": {
            "module": "domains.theraskill.services.contribution_api.app:create_app",
            "port": port("contribution", 8881),
        },
        "extraction": {
            "module": "domains.theraskill.services.extraction_api.app:create_app",
            "port": port("extraction", 8891),
        },
        "transcript": {
            "module": "domains.theraskill.services.transcript_api.app:create_app",
            "port": port("transcript", 8892),
        },
    }


def runtime_environment() -> dict[str, str]:
    env = os.environ.copy()
    env["THERASKILL_AGENT_ROOT"] = str(project_root())
    # The legacy engine reads this compatibility variable; its value is still
    # the single TheraSkillAgent project root.
    env["HERMES_HOME"] = str(project_root())
    # Load provider credentials from this project's .env before spawning
    # domain services so every subprocess uses the same configured providers.
    try:
        from theraskill_agent.config import load_env

        env.update(load_env())
    except Exception:
        pass
    # Only the explicit separate-process launcher uses this environment.
    for name, spec in service_specs().items():
        env.setdefault(f"THERASKILL_{name.upper()}_API_URL", f"http://127.0.0.1:{spec['port']}")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(CODE_ROOT) if not existing else f"{CODE_ROOT}{os.pathsep}{existing}"
    return env
