#!/usr/bin/env python3
"""Extract case-level state/risk timelines for CPsyCounD v2 windows."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
for path in (SCRIPT_DIR, COMMON_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from cpsycound_surface_registry import CPSYCOUND_TIMELINE_INPUT_ROOT  # noqa: E402
from theraskill_llm import OpenAICompatibleClient, chat_json_with_repair, load_yaml, merged_model_config  # noqa: E402
from v2_extraction_common import format_registry_options, read_jsonl, registry_options  # noqa: E402


INPUT_ROOT = str(CPSYCOUND_TIMELINE_INPUT_ROOT)
PROMPT = "prompts/CPsyCounD/case_state_risk_timeline_zh.md"
REGISTRY = "configs/v2/normalization_registry.yaml"
OPTION_FIELDS = [
    "state_dimension",
    "certainty",
    "risk_domain",
    "risk_level",
    "required_action",
]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def prompt_text(path: str, options: dict[str, list[str]]) -> tuple[str, str]:
    template = Path(path).read_text(encoding="utf-8")
    selected = {field: options[field] for field in OPTION_FIELDS if field in options}
    digest = hashlib.sha256(template.encode("utf-8")).hexdigest()
    return template.replace("__NORMALIZATION_OPTIONS__", format_registry_options(selected)), digest


def load_cases_from_windows(input_root: Path) -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    turns_by_case: dict[str, dict[Any, dict[str, Any]]] = defaultdict(dict)
    metadata_by_case: dict[str, dict[str, Any]] = {}
    for path in sorted(input_root.glob("shard_*/evidence_windows.jsonl")):
        for window in read_jsonl(path):
            case_id = window["pilot_case_id"]
            metadata_by_case.setdefault(
                case_id,
                {
                    "pilot_case_id": case_id,
                    "dataset": window.get("dataset"),
                    "source_id": window.get("source_id"),
                    "lexical_risk_hint": window.get("lexical_risk_hint"),
                },
            )
            for turn in window.get("turns") or []:
                turn_id = turn.get("turn_id")
                if turn_id is not None:
                    turns_by_case[case_id][turn_id] = {
                        "turn_id": turn_id,
                        "role": turn.get("role"),
                        "text": turn.get("text"),
                    }
    for case_id, turns in turns_by_case.items():
        ordered = [turns[key] for key in sorted(turns)]
        cases[case_id] = {**metadata_by_case.get(case_id, {}), "turns": ordered}
    return cases


def compact_case(case: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    compact_turns = []
    turn_map = {}
    for idx, turn in enumerate(case.get("turns") or []):
        short = f"T{idx}"
        turn_map[short] = turn.get("turn_id")
        compact_turns.append({"turn": short, "role": turn.get("role"), "text": turn.get("text")})
    payload = {
        "case": "C1",
        "source_context": {
            "dataset": case.get("dataset"),
            "source_id": case.get("source_id"),
            "lexical_risk_hint": case.get("lexical_risk_hint"),
        },
        "turns": compact_turns,
    }
    id_mapping = {"case": "C1", "turn_ids": turn_map}
    return payload, id_mapping


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", default=INPUT_ROOT)
    parser.add_argument("--out", required=True)
    parser.add_argument("--model-config", required=True)
    parser.add_argument("--extraction-config", default="configs/extraction_config.yaml")
    parser.add_argument("--registry", default=REGISTRY)
    parser.add_argument("--prompt", default=PROMPT)
    parser.add_argument("--parse-retries", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--source-id", action="append", dest="source_ids")
    parser.add_argument("--num-shards", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def completed_case_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    done = set()
    for row in read_jsonl(path):
        if not row.get("failure"):
            done.add(str(row.get("pilot_case_id")))
    return done


def main() -> int:
    args = parse_args()
    if args.num_shards < 1:
        raise SystemExit("--num-shards must be >= 1")
    if args.shard_index < 0 or args.shard_index >= args.num_shards:
        raise SystemExit("--shard-index must be in [0, --num-shards)")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cases = load_cases_from_windows(Path(args.input_root))
    selected = sorted(cases.values(), key=lambda row: (str(row.get("source_id")), str(row.get("pilot_case_id"))))
    if args.source_ids:
        source_ids = set(args.source_ids)
        selected = [row for row in selected if str(row.get("source_id")) in source_ids]
    matching_count = len(selected)
    selected = [row for idx, row in enumerate(selected) if idx % args.num_shards == args.shard_index]
    if args.limit is not None:
        selected = selected[: args.limit]

    model_config = merged_model_config(load_yaml(args.extraction_config), load_yaml(args.model_config))
    client = OpenAICompatibleClient(model_config)
    options = registry_options(load_yaml(args.registry))
    prompt, prompt_hash = prompt_text(args.prompt, options)
    done = completed_case_ids(out) if args.resume else set()

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_root": args.input_root,
        "out": str(out),
        "matching_count": matching_count,
        "selected_count": len(selected),
        "num_shards": args.num_shards,
        "shard_index": args.shard_index,
        "model_config": args.model_config,
        "model": {
            "name": model_config.get("name"),
            "base_url": model_config.get("base_url"),
            "temperature": model_config.get("temperature"),
            "thinking": model_config.get("thinking"),
        },
        "prompt": {"path": args.prompt, "sha256": prompt_hash},
        "policy": "case-level timeline may use full case; later window projection enforces first_observed_turn < center_turn",
    }
    out.with_suffix(".manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with out.open("a" if args.resume else "w", encoding="utf-8") as handle:
        for idx, case in enumerate(selected, start=1):
            case_id = str(case.get("pilot_case_id"))
            if case_id in done:
                print(f"{case_id}: skipped_existing", flush=True)
                continue
            payload, id_mapping = compact_case(case)
            try:
                result = chat_json_with_repair(client, prompt, payload, parse_retries=args.parse_retries, force_json_first=True)
                row = {
                    "pilot_case_id": case_id,
                    "dataset": case.get("dataset"),
                    "source_id": case.get("source_id"),
                    "id_mapping": id_mapping,
                    "timeline": result,
                }
                print(f"{idx}/{len(selected)} {case_id}: timeline", flush=True)
            except Exception as exc:  # noqa: BLE001
                row = {
                    "pilot_case_id": case_id,
                    "dataset": case.get("dataset"),
                    "source_id": case.get("source_id"),
                    "failure": f"{type(exc).__name__}: {exc}",
                }
                print(f"{idx}/{len(selected)} {case_id}: failure {type(exc).__name__}", flush=True)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
