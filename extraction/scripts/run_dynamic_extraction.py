#!/usr/bin/env python3
"""Plan and run goal-directed extraction without a dataset-specific adapter.

Examples:
  python scripts/run_dynamic_extraction.py \
    --source examples/input/cpsycoune_demo.json \
    --goal "抽取来访者状态变化" \
    --plan-output work/demo.plan.json --dry-run

  python scripts/run_dynamic_extraction.py \
    --source data.jsonl --plan work/demo.plan.json \
    --model-config configs/model_config.local.yaml \
    --output work/units.jsonl --manifest work/manifest.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from dynamic_extraction.contracts import ExtractionPlan, PlanError
from dynamic_extraction.executor import build_manifest, extract_records, load_source_records
from dynamic_extraction.planner import build_plan, plan_with_model
from _common.theraskill_llm import OpenAICompatibleClient, load_yaml


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="source file or directory handled by the adapter registry")
    parser.add_argument("--goal", help="Natural-language extraction goal")
    parser.add_argument("--plan", help="Existing ExtractionPlan JSON")
    parser.add_argument("--schema", help="Optional target JSON Schema file")
    parser.add_argument("--plan-output", help="Write the generated plan JSON here")
    parser.add_argument("--model-config", help="OpenAI-compatible model YAML; required for a real run")
    parser.add_argument("--planner", choices=("heuristic", "model"), default="heuristic", help="How to propose a schema when --plan is absent")
    parser.add_argument("--output", default="outputs/dynamic/knowledge_units.jsonl")
    parser.add_argument("--failures", default="outputs/dynamic/failures.jsonl")
    parser.add_argument("--manifest", default="outputs/dynamic/manifest.json")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-prompt-tokens", type=int, default=24000)
    parser.add_argument("--dry-run", action="store_true", help="Only generate/validate the plan")
    return parser


def _load_plan(args: argparse.Namespace, records: list[object]) -> ExtractionPlan:
    if args.plan:
        with Path(args.plan).open("r", encoding="utf-8") as handle:
            return ExtractionPlan.from_mapping(json.load(handle))
    if not args.goal:
        raise PlanError("either --plan or --goal is required")
    schema = None
    if args.schema:
        with Path(args.schema).open("r", encoding="utf-8") as handle:
            schema = json.load(handle)
    source = {"path": str(Path(args.source))}
    if args.planner == "model":
        if not args.model_config:
            raise PlanError("--planner model requires --model-config")
        model_config = load_yaml(args.model_config).get("model") or {}
        if not model_config.get("api_key") or not model_config.get("base_url") or not model_config.get("name"):
            raise PlanError("model planner requires model.api_key, model.base_url and model.name")
        client = OpenAICompatibleClient(model_config)
        samples = [
            (item.payload if hasattr(item, "payload") else item)
            for item in records[:5]
        ]
        samples = [item if isinstance(item, dict) else {"value": item} for item in samples]
        return plan_with_model(client, args.goal, samples, source=source)
    return build_plan(args.goal, target_schema=schema, source=source, status="planned" if args.dry_run else "approved")


def main() -> int:
    args = _parser().parse_args()
    try:
        records_for_plan = load_source_records(args.source)
        plan = _load_plan(args, records_for_plan)
    except (PlanError, OSError, json.JSONDecodeError) as exc:
        print(f"plan_error: {exc}", file=sys.stderr)
        return 2
    if args.plan_output:
        output_plan = Path(args.plan_output)
        output_plan.parent.mkdir(parents=True, exist_ok=True)
        output_plan.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.dry_run:
        records = records_for_plan
        preview = {
            "plan": plan.to_dict(),
            "source": str(Path(args.source)),
            "record_count": len(records),
        "preview_source_ids": [str(getattr(item, "record_id", index)) for index, item in enumerate(records[:3])],
            "next_step": "review the plan, then rerun without --dry-run and provide --model-config",
        }
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return 0
    if not args.model_config:
        print("model_config_required: provide --model-config for extraction", file=sys.stderr)
        return 2
    try:
        local_model_config = load_yaml(args.model_config)
        model_config = dict(local_model_config.get("model") or {})
        if not model_config.get("api_key") or not model_config.get("base_url") or not model_config.get("name"):
            raise ValueError("model config requires model.api_key, model.base_url and model.name")
        client = OpenAICompatibleClient(model_config)
        records = load_source_records(args.source)
        units, failures = extract_records(
            records,
            plan,
            client,
            limit=args.limit,
            max_prompt_tokens=args.max_prompt_tokens,
        )
        output_path = Path(args.output)
        failure_path = Path(args.failures)
        manifest_path = Path(args.manifest)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in units), encoding="utf-8")
        failure_path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in failures), encoding="utf-8")
        manifest = build_manifest(plan, args.source, units=units, failures=failures, model_name=client.model)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": manifest["status"], "units": len(units), "failures": len(failures), "manifest": str(manifest_path)}, ensure_ascii=False))
        return 0 if units or not failures else 1
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"extraction_error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
