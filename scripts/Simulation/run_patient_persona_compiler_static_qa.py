#!/usr/bin/env python3
"""Run full-corpus static and counterfactual QA for the persona context firewall."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for path in (PROJECT_ROOT / "src", PROJECT_ROOT / "scripts/_common"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from io_utils import file_sha256, read_json, write_json  # noqa: E402
from simulation_surface_registry import SIMULATION_PERSONA_V2_DIR  # noqa: E402
from theraskill_simulation.persona_compiler import (  # noqa: E402
    PersonaCompiler,
    effective_disclosure_nodes,
    effective_initially_disclosed_fact_ids,
    public_demographic_profile,
)


DEFAULT_OUTPUT = PROJECT_ROOT / "outputs/simulation/personas/patient_persona_compiler.qa.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--persona-dir", default=str(SIMULATION_PERSONA_V2_DIR))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--expected-personas", type=int, default=3134)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def move(
    *, support: str, challenge: str, validation: str, repair: str, types: list[str]
) -> dict[str, Any]:
    return {
        "therapist_move_interpretation": {
            "support_level": support,
            "challenge_level": challenge,
            "validation_signal": validation,
            "repair_signal": repair,
            "risk_probe_signal": "present" if "risk_probe" in types else "absent",
            "move_type": types,
            "intent": ["increase_disclosure"] if "open_question" in types else [],
        }
    }


def leaked_forbidden_contents(persona: dict[str, Any], compiled: Any) -> list[str]:
    context_text = json.dumps(compiled.runtime_context, ensure_ascii=False)
    forbidden = set(compiled.forbidden_fact_ids)
    return [
        str(node.get("content"))
        for node in (persona.get("disclosure_graph") or {}).get("nodes") or []
        if node.get("fact_id") in forbidden
        and node.get("content")
        and str(node.get("content")) in context_text
    ]


def main() -> int:
    args = parse_args()
    paths = sorted(Path(args.persona_dir).glob("*.json"))
    compiler = PersonaCompiler()
    issues: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    supportive_move = move(
        support="high", challenge="low", validation="present", repair="absent",
        types=["validation", "reflection", "open_question"],
    )
    rupture_move = move(
        support="low", challenge="high", validation="absent", repair="absent",
        types=["cognitive_reframe"],
    )
    repair_move = move(
        support="high", challenge="low", validation="present", repair="present",
        types=["validation", "repair_attempt", "open_question"],
    )
    risk_move = move(
        support="high", challenge="low", validation="present", repair="absent",
        types=["validation", "risk_probe"],
    )

    if len(paths) != args.expected_personas:
        issues.append(
            {
                "issue": "persona_count_mismatch",
                "expected": args.expected_personas,
                "actual": len(paths),
            }
        )

    for path in paths:
        persona = read_json(path)
        case_id = str(persona.get("case_id"))
        effective_nodes = effective_disclosure_nodes(persona)
        forbidden_card_contents = {
            str(node.get("content"))
            for node in effective_nodes
            if node.get("sensitivity") != "surface" and node.get("content")
        }
        card_payload = {
            "demographic_profile": public_demographic_profile(
                persona.get("immutable_identity") or {}, forbidden_card_contents
            ),
            "surface_presenting_problems": [
                str(item.get("content"))
                for item in (persona.get("psychological_model") or {}).get("presenting_problems") or []
                if item.get("content") in {
                    str(node.get("content"))
                    for node in effective_nodes
                    if node.get("sensitivity") == "surface" and node.get("content")
                }
            ],
        }
        card_text = json.dumps(card_payload, ensure_ascii=False)
        card_leaks = [content for content in forbidden_card_contents if content in card_text]
        if card_leaks:
            issues.append(
                {"case_id": case_id, "issue": "learner_role_card_forbidden_content_leak", "contents": card_leaks[:10]}
            )
        initially_disclosed = effective_initially_disclosed_fact_ids(persona)
        variants = {
            "supportive": compiler.compile_for_turn(
                persona, {"disclosure_state": "moderate"}, initially_disclosed, supportive_move
            ),
            "rupture": compiler.compile_for_turn(
                persona, {"disclosure_state": "moderate"}, initially_disclosed, rupture_move
            ),
            "repair": compiler.compile_for_turn(
                persona, {"disclosure_state": "moderate"}, initially_disclosed, repair_move
            ),
            "risk_probe": compiler.compile_for_turn(
                persona, {"disclosure_state": "moderate"}, initially_disclosed, risk_move
            ),
            "surface": compiler.compile_for_turn(
                persona, {"disclosure_state": "surface"}, initially_disclosed, supportive_move
            ),
        }
        for name, compiled in variants.items():
            allowed = set(compiled.allowed_fact_ids)
            forbidden = set(compiled.forbidden_fact_ids)
            if allowed & forbidden:
                issues.append({"case_id": case_id, "variant": name, "issue": "allowed_forbidden_overlap"})
            leaks = leaked_forbidden_contents(persona, compiled)
            if leaks:
                issues.append(
                    {"case_id": case_id, "variant": name, "issue": "forbidden_content_leak", "contents": leaks[:10]}
                )
            counters[f"{name}_allowed_facts"] += len(allowed)

        if len(variants["rupture"].allowed_fact_ids) > len(variants["supportive"].allowed_fact_ids):
            issues.append({"case_id": case_id, "issue": "rupture_discloses_more_than_supportive"})
        if len(variants["repair"].allowed_fact_ids) < len(variants["rupture"].allowed_fact_ids):
            issues.append({"case_id": case_id, "issue": "repair_discloses_less_than_rupture"})
        sensitive_ids = {
            str(node.get("fact_id"))
            for node in (persona.get("disclosure_graph") or {}).get("nodes") or []
            if node.get("sensitivity") == "sensitive"
        }
        if sensitive_ids & set(variants["surface"].allowed_fact_ids) - set(initially_disclosed):
            issues.append({"case_id": case_id, "issue": "sensitive_fact_allowed_at_surface"})

        identity_hashes = {
            str(item.runtime_context.get("immutable_identity_checksum"))
            for item in variants.values()
        }
        if len(identity_hashes) != 1:
            issues.append({"case_id": case_id, "issue": "counterfactual_identity_drift"})

        undisclosed = [
            fact_id
            for fact_id in variants["supportive"].allowed_fact_ids
            if fact_id not in set(initially_disclosed)
        ]
        if undisclosed:
            carried = compiler.compile_for_turn(
                persona,
                {"disclosure_state": "surface"},
                [*initially_disclosed, undisclosed[0]],
                rupture_move,
            )
            if undisclosed[0] not in carried.allowed_fact_ids:
                issues.append({"case_id": case_id, "issue": "disclosed_fact_not_preserved_in_carryover"})

    status = "complete" if not issues else "failed"
    report = {
        "schema_version": "theraskill_patient_persona_compiler_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "persona_dir": str(Path(args.persona_dir).resolve()),
        "persona_count": len(paths),
        "expected_persona_count": args.expected_personas,
        "checks": {
            "persona_count_matches_expected": len(paths) == args.expected_personas,
            "allowed_forbidden_disjoint": not any(item["issue"] == "allowed_forbidden_overlap" for item in issues),
            "forbidden_content_absent_from_context": not any(item["issue"] == "forbidden_content_leak" for item in issues),
            "rupture_not_more_disclosive_than_supportive": not any(item["issue"] == "rupture_discloses_more_than_supportive" for item in issues),
            "repair_not_less_disclosive_than_rupture": not any(item["issue"] == "repair_discloses_less_than_rupture" for item in issues),
            "surface_sensitive_leak_zero": not any(item["issue"] == "sensitive_fact_allowed_at_surface" for item in issues),
            "counterfactual_identity_stable": not any(item["issue"] == "counterfactual_identity_drift" for item in issues),
            "carryover_preserves_disclosed_facts": not any(item["issue"] == "disclosed_fact_not_preserved_in_carryover" for item in issues),
            "learner_role_card_forbidden_content_absent": not any(item["issue"] == "learner_role_card_forbidden_content_leak" for item in issues),
        },
        "aggregate_counts": dict(counters),
        "persona_schema_sha256": file_sha256(
            PROJECT_ROOT / "schemas/simulation/patient_persona_v2.schema.json"
        ),
        "issues": issues[:1000],
        "issues_count": len(issues),
    }
    write_json(args.output, report, overwrite=args.overwrite)
    print(json.dumps({"status": status, "persona_count": len(paths), "issues_count": len(issues)}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
