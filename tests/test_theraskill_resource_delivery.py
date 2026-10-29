"""Local resource delivery stays independent from application installation."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


def test_resource_delivery_is_exactly_two_directly_installable_zips(tmp_path, monkeypatch):
    from theraskill_agent import resources

    root = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    knowledge = source / "knowledge/phase6_reviewed_knowledge_units.jsonl"
    knowledge.parent.mkdir(parents=True)
    shutil.copyfile(root / "examples/knowledge/demo.jsonl", knowledge)
    registry = source / "simulation/scenarios/scenario_registry.jsonl"
    registry.parent.mkdir(parents=True)
    registry.write_text('{"scenario_id":"fixture-1"}\n', encoding="utf-8")
    (source / "manifest.json").write_text(
        json.dumps({"release_id": "fixture", "counts": {"knowledge_release_rows": 6}}),
        encoding="utf-8",
    )

    output = tmp_path / "delivery"
    subprocess.run(
        [sys.executable, str(root / "scripts/TheraSkill/package_user_resources.py"),
         "--source", str(source), "--output", str(output), "--version", "fixture-v1"],
        check=True, capture_output=True, text=True,
    )
    assert {path.name for path in output.iterdir()} == {
        "knowledge-fixture-v1.zip", "simulation-fixture-v1.zip",
    }

    monkeypatch.setenv("THERASKILL_AGENT_ROOT", str(tmp_path / "project"))
    for kind in ("knowledge", "simulation"):
        archive = output / f"{kind}-fixture-v1.zip"
        with zipfile.ZipFile(archive) as bundle:
            manifest = json.loads(bundle.read("package.json"))
            assert manifest["kind"] == kind
            assert manifest["provenance"]["original_release_id"] == "fixture"
            assert manifest["files"]
        installed = resources.install_package(archive, expected_kind=kind)
        assert installed["kind"] == kind
