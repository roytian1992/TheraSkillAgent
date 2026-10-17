from pathlib import Path
import sys
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

def test_clean_project_has_no_machine_specific_dataset_paths():
    for path in (ROOT / "scripts").rglob("*.py"):
        if path.name == "test_clean_project.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "/vepfs-mlp2/" not in text

def test_model_is_configurable():
    text = (ROOT / "configs/model_config.yaml").read_text(encoding="utf-8")
    assert "name:" in text and "base_url:" in text

def test_expected_surfaces_exist():
    for rel in [
        "scripts/CPsyCounD/run_case_knowledge_extraction.py",
        "scripts/CPsyCounE/run_cpsycoune_knowledge_extraction.py",
        "scripts/ESConv/run_esconv_knowledge_extraction.py",
        "scripts/Simulation/run_patient_state_transition_extraction.py",
        "scripts/DedupMerge/build_phase6_reviewed_knowledge_release.py",
    ]:
        assert (ROOT / rel).exists()


def test_dynamic_plan_is_dataset_agnostic_and_evidence_first(tmp_path):
    from dynamic_extraction.planner import build_plan

    plan = build_plan("抽取适合情景模拟的来访者状态变化", source={"path": "demo.json"})
    assert plan.target_schema["type"] == "object"
    assert "evidence" in plan.target_schema["required"]
    assert plan.source["path"] == "demo.json"


def test_dynamic_cli_dry_run_generates_reviewable_plan(tmp_path):
    plan_path = tmp_path / "plan.json"
    command = [
        sys.executable,
        str(ROOT / "scripts/run_dynamic_extraction.py"),
        "--source",
        str(ROOT / "examples/input/cpsycoune_demo.json"),
        "--goal",
        "抽取情景模拟中的回应策略",
        "--plan-output",
        str(plan_path),
        "--dry-run",
    ]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["record_count"] == 1
    saved_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert saved_plan["target_schema"]["properties"]["evidence"]["type"] == "string"


def test_model_planner_output_is_validated_before_use():
    from dynamic_extraction.planner import plan_with_model

    class FakeClient:
        model = "fake-model"

        def chat(self, *_args, **_kwargs):
            return json.dumps(
                {
                    "unit_definition": "one dialogue",
                    "language": "zh",
                    "target_schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "claim": {"type": "string"},
                            "evidence": {"type": "string"},
                        },
                        "required": ["claim", "evidence"],
                    },
                    "evidence_required": True,
                    "evidence_fields": ["evidence"],
                    "quality_rules": ["Use source evidence only."],
                    "operations": ["extract", "validate"],
                }
            )

    plan = plan_with_model(FakeClient(), "抽取回应策略", [{"text": "询问感受"}])
    assert plan.status == "planned"
    assert plan.target_schema["required"] == ["claim", "evidence"]
