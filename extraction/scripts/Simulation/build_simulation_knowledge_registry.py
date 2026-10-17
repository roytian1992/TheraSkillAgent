#!/usr/bin/env python3
"""Build a registry for patient/doctor simulation knowledge inputs."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import file_sha256, read_json, write_json, write_jsonl
from simulation_surface_registry import (
    SIMULATION_ARCHETYPE_BUNDLE_PATH,
    SIMULATION_KNOWLEDGE_REGISTRY_MANIFEST_PATH,
    SIMULATION_KNOWLEDGE_REGISTRY_PATH,
    SIMULATION_KNOWLEDGE_REGISTRY_QA_PATH,
    SIMULATION_KNOWLEDGE_REGISTRY_SUMMARY_PATH,
    SIMULATION_MEMORY_BUNDLE_PATH,
    SIMULATION_PERSONA_BUNDLE_PATH,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_JSONL = SIMULATION_KNOWLEDGE_REGISTRY_PATH
SUMMARY_JSON = SIMULATION_KNOWLEDGE_REGISTRY_SUMMARY_PATH
MANIFEST_JSON = SIMULATION_KNOWLEDGE_REGISTRY_MANIFEST_PATH
QA_JSON = SIMULATION_KNOWLEDGE_REGISTRY_QA_PATH


REGISTRY_SPECS = [
    {
        "registry_id": "psydt_persona_bundle",
        "dataset": "PsyDTCorpus",
        "knowledge_path": SIMULATION_PERSONA_BUNDLE_PATH,
        "manifest_path": PROJECT_ROOT / "outputs/knowledge/manifests/PsyDTCorpus.persona_projection_manifest.json",
        "qa_path": PROJECT_ROOT / "outputs/knowledge/manifests/PsyDTCorpus.persona_projection_qa.json",
        "knowledge_type": "persona_pattern_bundle",
        "simulation_role": "patient_persona_and_therapist_style_seed",
        "status_requirement": "complete",
        "notes": [
            "Synthetic role-design evidence only.",
            "Grouped by normalizedTag.",
            "Contains patient persona, hidden profile, therapist persona and persona-conditioned construction pattern.",
        ],
    },
    {
        "registry_id": "soulchatr1_memory_bundle",
        "dataset": "SoulChat-R1",
        "knowledge_path": SIMULATION_MEMORY_BUNDLE_PATH,
        "manifest_path": PROJECT_ROOT / "outputs/knowledge/manifests/SoulChat-R1.memory_projection_manifest.json",
        "qa_path": PROJECT_ROOT / "outputs/knowledge/manifests/SoulChat-R1.memory_projection_qa.json",
        "knowledge_type": "memory_pattern_bundle",
        "simulation_role": "patient_state_memory_and_therapist_planning_memory",
        "status_requirement": "complete",
        "notes": [
            "Synthetic memory/planning evidence only.",
            "Grouped by primary_stage x history_bucket.",
            "Contains patient state summary, therapist planning memory, stage transition pattern and memory update rule.",
        ],
    },
    {
        "registry_id": "mindecho_archetype_bundle",
        "dataset": "MindEcho-Chat-26",
        "knowledge_path": SIMULATION_ARCHETYPE_BUNDLE_PATH,
        "manifest_path": PROJECT_ROOT / "outputs/knowledge/manifests/MindEcho-Chat-26.archetype_projection_manifest.json",
        "qa_path": PROJECT_ROOT / "outputs/knowledge/manifests/MindEcho-Chat-26.archetype_projection_qa.json",
        "knowledge_type": "archetype_bundle",
        "simulation_role": "preset_patient_archetype_seed",
        "status_requirement": "complete",
        "notes": [
            "Synthetic roleplay preset evidence only.",
            "Current local source lacks explicit category field; current output is a single uncategorized group.",
            "If finer preset routing is needed, add profile-based secondary splitting later.",
        ],
    },
]

def read_jsonl_count(path: Path) -> int:
    count = 0
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                count += 1
    return count


def main() -> int:
    rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    for spec in REGISTRY_SPECS:
        knowledge_path = Path(spec["knowledge_path"])
        manifest_path = Path(spec["manifest_path"])
        qa_path = Path(spec["qa_path"])
        manifest = read_json(manifest_path) if manifest_path.exists() else {}
        qa = read_json(qa_path) if qa_path.exists() else {}
        row_count = read_jsonl_count(knowledge_path)
        manifest_status = manifest.get("status")
        qa_status = qa.get("status")
        expected_status = spec["status_requirement"]
        if manifest_status != expected_status:
            issues.append(
                {
                    "issue": "manifest_status_mismatch",
                    "registry_id": spec["registry_id"],
                    "expected": expected_status,
                    "actual": manifest_status,
                }
            )
        if qa_status != expected_status:
            issues.append(
                {
                    "issue": "qa_status_mismatch",
                    "registry_id": spec["registry_id"],
                    "expected": expected_status,
                    "actual": qa_status,
                }
            )
        if row_count <= 0:
            issues.append({"issue": "empty_or_missing_knowledge_file", "registry_id": spec["registry_id"], "path": str(knowledge_path)})
        rows.append(
            {
                "schema_version": "theraskill_simulation_knowledge_registry_entry_v1",
                "registry_id": spec["registry_id"],
                "dataset": spec["dataset"],
                "knowledge_type": spec["knowledge_type"],
                "simulation_role": spec["simulation_role"],
                "knowledge_path": str(knowledge_path),
                "knowledge_sha256": file_sha256(knowledge_path),
                "row_count": row_count,
                "manifest_path": str(manifest_path),
                "manifest_status": manifest_status,
                "qa_path": str(qa_path),
                "qa_status": qa_status,
                "status_requirement": expected_status,
                "notes": spec["notes"],
            }
        )

    registry_sha = write_jsonl(OUTPUT_JSONL, rows, sort_keys=True, separators=(",", ":"), return_sha256=True)
    summary = {
        "schema_version": "theraskill_simulation_knowledge_registry_summary_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "registry_rows": len(rows),
        "datasets": [row["dataset"] for row in rows],
        "simulation_roles": [row["simulation_role"] for row in rows],
        "row_count_distribution": {row["registry_id"]: row["row_count"] for row in rows},
        "issues_count": len(issues),
    }
    write_json(SUMMARY_JSON, summary)
    manifest = {
        "schema_version": "theraskill_simulation_knowledge_registry_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete" if not issues else "incomplete",
        "registry": {"path": str(OUTPUT_JSONL), "rows": len(rows), "sha256": registry_sha},
        "summary": {"path": str(SUMMARY_JSON), "sha256": file_sha256(SUMMARY_JSON)},
        "issues_count": len(issues),
    }
    write_json(MANIFEST_JSON, manifest)
    qa = {
        "schema_version": "theraskill_simulation_knowledge_registry_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": manifest["status"],
        "registry_rows": len(rows),
        "issues": issues,
        "issues_count": len(issues),
    }
    write_json(QA_JSON, qa)
    print(json.dumps({"status": qa["status"], "registry_rows": len(rows), "issues_count": len(issues)}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
