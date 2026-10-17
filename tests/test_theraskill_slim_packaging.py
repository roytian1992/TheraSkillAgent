"""Packaging contract for the standalone TheraSkillAgent distribution."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def test_runtime_dependencies_are_small_and_do_not_pull_model_stacks() -> None:
    dependencies = _project()["dependencies"]
    names = {item.split("[", 1)[0].split(">", 1)[0].split("=", 1)[0].strip().lower() for item in dependencies}
    assert names == {
        "pyyaml",
        "python-dotenv",
        "pydantic",
        "fastapi",
        "uvicorn",
        "httpx",
        "json-repair",
    }
    assert not names & {"torch", "transformers", "faiss-cpu", "chromadb", "sentence-transformers"}


def test_wheel_package_selection_excludes_legacy_host_packages() -> None:
    include = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]["packages"]["find"]["include"]
    assert "theraskill_agent" in include
    assert "domains.theraskill.*" in include
    assert "plugins.theraskill.*" in include
    for pattern in include:
        assert not pattern.startswith(("agent", "tools", "gateway", "providers", "hermes_cli"))


def test_root_node_manifest_has_no_eager_legacy_dependencies() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert package.get("dependencies", {}) == {}
    assert package.get("devDependencies", {}) == {}
    assert all(path.startswith("views/theraskill/") for path in package["workspaces"])
    assert len(package["workspaces"]) == 4


def test_manifest_does_not_copy_general_skill_catalog() -> None:
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "recursive-include plugins/theraskill" in manifest
    assert "graft skills" not in manifest
    assert "graft optional-skills" not in manifest
