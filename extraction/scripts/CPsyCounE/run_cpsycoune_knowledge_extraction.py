#!/usr/bin/env python3
"""Extract CPsyCounE knowledge units.

CPsyCounE is a small 45-case evaluation dialogue dataset. This runner keeps the
same knowledge-unit envelope used by CPsyCounD while avoiding CPsyCounD-specific
transition assumptions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[1]))

from _common.theraskill_llm import (  # noqa: E402
    OpenAICompatibleClient,
    PromptBudgetExceededError,
    chat_json_with_repair,
    context_max_prompt_tokens,
    load_yaml,
    merged_model_config,
    parse_retry_attempts,
    prompt_budget_report,
)


DATASET = "CPsyCounE"
SOURCE_ROOT = "datasets/CPsyCoun/CPsyCounE"
EXTRACTION_CONFIG = "configs/extraction_config.yaml"
MODEL_CONFIG = "configs/model_config.yaml"

STAGES = {
    "evaluation_dialogue_case": {
        "prompt": "prompts/CPsyCounE/evaluation_dialogue_case_zh.md",
        "output": "outputs/knowledge/units/CPsyCounE.evaluation_dialogue_case.jsonl",
        "manifest": "outputs/knowledge/manifests/CPsyCounE.evaluation_dialogue_case.manifest.json",
    },
    "topic_support_dialogue_pattern": {
        "prompt": "prompts/CPsyCounE/topic_support_dialogue_pattern_zh.md",
        "output": "outputs/knowledge/units/CPsyCounE.topic_support_dialogue_pattern.jsonl",
        "manifest": "outputs/knowledge/manifests/CPsyCounE.topic_support_dialogue_pattern.manifest.json",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=sorted(STAGES), required=True)
    parser.add_argument("--source-root", default=SOURCE_ROOT)
    parser.add_argument("--input-units", default=STAGES["evaluation_dialogue_case"]["output"])
    parser.add_argument("--output")
    parser.add_argument("--manifest")
    parser.add_argument("--failure-log")
    parser.add_argument("--extraction-config", default=EXTRACTION_CONFIG)
    parser.add_argument("--model-config", default=MODEL_CONFIG)
    parser.add_argument("--case-id")
    parser.add_argument("--topic")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--max-prompt-tokens", type=int)
    parser.add_argument("--parse-retries", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()



def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def append_jsonl(path: str | Path, row: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def file_sha256(path: str | Path) -> str | None:
    path = Path(path)
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def safe_slug(value: str) -> str:
    return (
        value.lower()
        .replace("&", "and")
        .replace(" ", "_")
        .replace("/", "_")
        .replace("-", "_")
    )


def unit_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def case_id_for(topic: str, file_stem: str) -> str:
    return f"cpsycoune_{safe_slug(topic)}_{file_stem}"


def unit_id(knowledge_type: str, source_id: str) -> str:
    return f"ku_cpsycoune_{knowledge_type}_{unit_hash(source_id)}"


def load_source_cases(source_root: str | Path) -> list[dict[str, Any]]:
    root = Path(source_root)
    cases: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/*.json")):
        topic = path.parent.name
        raw_turns = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw_turns, list):
            raise ValueError(f"expected list dialogue: {path}")
        turns = []
        for idx, text in enumerate(raw_turns, start=1):
            text = str(text)
            role, content = ("unknown", text)
            if "：" in text:
                role, content = text.split("：", 1)
            turns.append(
                {
                    "turn_id": f"t{idx:02d}",
                    "speaker": role,
                    "text": content,
                    "raw_text": text,
                }
            )
        source_id = f"{topic}/{path.name}"
        cases.append(
            {
                "case_id": case_id_for(topic, path.stem),
                "source_id": source_id,
                "topic": topic,
                "path": str(path),
                "turns": turns,
                "turn_count": len(turns),
            }
        )
    return cases


def source_envelope(case: dict[str, Any], *, knowledge_type: str) -> dict[str, Any]:
    return {
        "dataset": DATASET,
        "source_id": case["source_id"],
        "case_id": case["case_id"],
        "topic": case["topic"],
        "language": "zh",
        "source_construction_type": "evaluation_dialogue_sample",
        "evidence_tier": "reconstructed_dialogue_evaluation_sample",
        "source_turn_count": case["turn_count"],
        "knowledge_type_hint": knowledge_type,
    }


def build_embedding_text(stage: str, extracted: dict[str, Any]) -> str:
    if stage == "evaluation_dialogue_case":
        return "\n".join(
            [
                str(extracted.get("presenting_problem_summary") or ""),
                str(extracted.get("support_process_summary") or ""),
                json.dumps(extracted.get("supporter_strategy_sequence") or [], ensure_ascii=False),
                json.dumps(extracted.get("risk_or_boundary_signals") or [], ensure_ascii=False),
            ]
        ).strip()
    return "\n".join(
        [
            json.dumps(extracted.get("common_presenting_problems") or [], ensure_ascii=False),
            json.dumps(extracted.get("typical_opening_moves") or [], ensure_ascii=False),
            json.dumps(extracted.get("typical_support_sequence") or [], ensure_ascii=False),
            json.dumps(extracted.get("safe_response_boundaries") or [], ensure_ascii=False),
        ]
    ).strip()


def wrap_unit(stage: str, source: dict[str, Any], extracted: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    knowledge_type = stage
    source_id = source["source_id"] if stage == "evaluation_dialogue_case" else f"topic/{source['topic']}"
    return {
        "schema_version": "theraskill_knowledge_unit_v1",
        "unit_id": unit_id(knowledge_type, source_id),
        "knowledge_type": knowledge_type,
        "source": source,
        "content": {knowledge_type: extracted},
        "quality": {
            "quality_status": extracted.get("quality_status"),
            "review_status": "llm_extracted_unreviewed",
        },
        "normalization": {
            "controlled_label_audit": {
                "status": "not_applicable_dataset_specific_llm_output",
                "issue_count": 0,
                "issues": [],
            }
        },
        "retrieval": {
            "embedding_status": "not_embedded",
            "embedding_text": build_embedding_text(stage, extracted),
        },
        "provenance": provenance,
    }


def completed_ids(output_path: Path, failure_log: Path, *, stage: str, resume: bool) -> set[str]:
    if not resume:
        return set()
    done = set()
    for row in read_jsonl(output_path):
        source = row.get("source") or {}
        done.add(str(source.get("case_id") if stage == "evaluation_dialogue_case" else source.get("topic")))
    for row in read_jsonl(failure_log):
        key = row.get("case_id") if stage == "evaluation_dialogue_case" else row.get("topic")
        if key:
            done.add(str(key))
    return done


def build_case_payload(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataset": DATASET,
        "case_id": case["case_id"],
        "source_id": case["source_id"],
        "topic": case["topic"],
        "turn_count": case["turn_count"],
        "dialogue_turns": case["turns"],
    }


def load_case_units(path: str | Path) -> list[dict[str, Any]]:
    rows = read_jsonl(path)
    rows.sort(key=lambda row: ((row.get("source") or {}).get("topic") or "", (row.get("source") or {}).get("source_id") or ""))
    return rows


def build_topic_payload(topic: str, units: list[dict[str, Any]]) -> dict[str, Any]:
    cases = []
    for unit in units:
        source = unit.get("source") or {}
        content = (unit.get("content") or {}).get("evaluation_dialogue_case") or {}
        cases.append(
            {
                "case_id": source.get("case_id"),
                "source_id": source.get("source_id"),
                "topic": source.get("topic"),
                "presenting_problem_summary": content.get("presenting_problem_summary"),
                "support_process_summary": content.get("support_process_summary"),
                "supporter_strategy_sequence": content.get("supporter_strategy_sequence") or [],
                "seeker_response_sequence": content.get("seeker_response_sequence") or [],
                "risk_or_boundary_signals": content.get("risk_or_boundary_signals") or [],
                "quality_limitations": content.get("quality_limitations") or [],
            }
        )
    return {
        "dataset": DATASET,
        "topic": topic,
        "case_count": len(cases),
        "evaluation_dialogue_cases": cases,
    }


def run_case_stage(args: argparse.Namespace, client: OpenAICompatibleClient, system_prompt: str, max_prompt_tokens: int, parse_retries: int) -> dict[str, Any]:
    stage = "evaluation_dialogue_case"
    output_path = Path(args.output or STAGES[stage]["output"])
    manifest_path = Path(args.manifest or STAGES[stage]["manifest"])
    failure_log = Path(args.failure_log or output_path.with_suffix(".failures.jsonl"))
    if args.overwrite:
        output_path.unlink(missing_ok=True)
        failure_log.unlink(missing_ok=True)
    if output_path.exists() and not args.resume and not args.overwrite:
        raise FileExistsError(f"output exists: {output_path}; pass --resume or --overwrite")

    cases = load_source_cases(args.source_root)
    if args.topic:
        cases = [case for case in cases if case["topic"] == args.topic]
    if args.case_id:
        cases = [case for case in cases if case["case_id"] == args.case_id]
    if args.limit is not None:
        cases = cases[: args.limit]

    done = completed_ids(output_path, failure_log, stage=stage, resume=args.resume)
    counters = Counter()
    audits: list[dict[str, Any]] = []
    started_at = datetime.now(timezone.utc).isoformat()
    for case in cases:
        if case["case_id"] in done:
            counters["skipped_existing"] += 1
            continue
        payload = build_case_payload(case)
        budget = prompt_budget_report(system_prompt, payload, max_prompt_tokens)
        audits.append({"case_id": case["case_id"], "topic": case["topic"], **budget})
        if args.dry_run:
            counters["dry_run"] += 1
            continue
        try:
            extracted = chat_json_with_repair(
                client,
                system_prompt,
                payload,
                parse_retries=parse_retries,
                max_prompt_tokens=max_prompt_tokens,
                force_json_first=True,
            )
            source = source_envelope(case, knowledge_type=stage)
            provenance = {
                "llm_stage": stage,
                "prompt_token_budget": budget,
                "source_path": case["path"],
            }
            append_jsonl(output_path, wrap_unit(stage, source, extracted, provenance))
            counters["success"] += 1
            print(f"{case['case_id']}: success", flush=True)
        except PromptBudgetExceededError as exc:
            append_jsonl(failure_log, {"case_id": case["case_id"], "stage": stage, "failure": {"reason": "prompt_token_budget_exceeded", "details": exc.report}})
            counters["prompt_token_budget_exceeded"] += 1
        except Exception as exc:  # noqa: BLE001
            append_jsonl(failure_log, {"case_id": case["case_id"], "stage": stage, "failure": {"reason": "runner_or_llm_failure", "error": f"{type(exc).__name__}: {exc}"}})
            counters["runner_or_llm_failure"] += 1
            print(f"{case['case_id']}: failure {type(exc).__name__}: {exc}", flush=True)

    manifest = build_manifest(args, stage, output_path, failure_log, system_prompt, started_at, counters, audits)
    write_json(manifest_path, manifest)
    return manifest


def run_topic_stage(args: argparse.Namespace, client: OpenAICompatibleClient, system_prompt: str, max_prompt_tokens: int, parse_retries: int) -> dict[str, Any]:
    stage = "topic_support_dialogue_pattern"
    output_path = Path(args.output or STAGES[stage]["output"])
    manifest_path = Path(args.manifest or STAGES[stage]["manifest"])
    failure_log = Path(args.failure_log or output_path.with_suffix(".failures.jsonl"))
    if args.overwrite:
        output_path.unlink(missing_ok=True)
        failure_log.unlink(missing_ok=True)
    if output_path.exists() and not args.resume and not args.overwrite:
        raise FileExistsError(f"output exists: {output_path}; pass --resume or --overwrite")

    units = load_case_units(args.input_units)
    by_topic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for unit in units:
        topic = (unit.get("source") or {}).get("topic")
        if topic:
            by_topic[str(topic)].append(unit)
    topics = sorted(by_topic)
    if args.topic:
        topics = [topic for topic in topics if topic == args.topic]
    if args.limit is not None:
        topics = topics[: args.limit]

    done = completed_ids(output_path, failure_log, stage=stage, resume=args.resume)
    counters = Counter()
    audits: list[dict[str, Any]] = []
    started_at = datetime.now(timezone.utc).isoformat()
    for topic in topics:
        if topic in done:
            counters["skipped_existing"] += 1
            continue
        payload = build_topic_payload(topic, by_topic[topic])
        budget = prompt_budget_report(system_prompt, payload, max_prompt_tokens)
        audits.append({"topic": topic, **budget})
        if args.dry_run:
            counters["dry_run"] += 1
            continue
        try:
            extracted = chat_json_with_repair(
                client,
                system_prompt,
                payload,
                parse_retries=parse_retries,
                max_prompt_tokens=max_prompt_tokens,
                force_json_first=True,
            )
            source = {
                "dataset": DATASET,
                "source_id": f"topic/{topic}",
                "topic": topic,
                "language": "zh",
                "source_construction_type": "evaluation_dialogue_topic_aggregate",
                "evidence_tier": "reconstructed_dialogue_evaluation_sample",
                "source_case_count": len(by_topic[topic]),
                "source_case_ids": [(unit.get("source") or {}).get("case_id") for unit in by_topic[topic]],
            }
            provenance = {
                "llm_stage": stage,
                "prompt_token_budget": budget,
                "input_evaluation_dialogue_unit_ids": [unit.get("unit_id") for unit in by_topic[topic]],
            }
            append_jsonl(output_path, wrap_unit(stage, source, extracted, provenance))
            counters["success"] += 1
            print(f"{topic}: success", flush=True)
        except PromptBudgetExceededError as exc:
            append_jsonl(failure_log, {"topic": topic, "stage": stage, "failure": {"reason": "prompt_token_budget_exceeded", "details": exc.report}})
            counters["prompt_token_budget_exceeded"] += 1
        except Exception as exc:  # noqa: BLE001
            append_jsonl(failure_log, {"topic": topic, "stage": stage, "failure": {"reason": "runner_or_llm_failure", "error": f"{type(exc).__name__}: {exc}"}})
            counters["runner_or_llm_failure"] += 1
            print(f"{topic}: failure {type(exc).__name__}: {exc}", flush=True)

    manifest = build_manifest(args, stage, output_path, failure_log, system_prompt, started_at, counters, audits)
    write_json(manifest_path, manifest)
    return manifest


def build_manifest(
    args: argparse.Namespace,
    stage: str,
    output_path: Path,
    failure_log: Path,
    system_prompt: str,
    started_at: str,
    counters: Counter,
    audits: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = read_jsonl(output_path)
    failures = read_jsonl(failure_log)
    return {
        "schema_version": "theraskill_cpsycoune_knowledge_extraction_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": started_at,
        "dataset": DATASET,
        "stage": stage,
        "source_root": args.source_root,
        "output": str(output_path),
        "output_sha256": file_sha256(output_path),
        "failure_log": str(failure_log),
        "failure_log_sha256": file_sha256(failure_log),
        "prompt": STAGES[stage]["prompt"],
        "prompt_sha256": text_sha256(system_prompt),
        "model_config": args.model_config,
        "extraction_config": args.extraction_config,
        "counters": dict(counters),
        "rows": len(rows),
        "failure_rows": len(failures),
        "prompt_token_audit": audits,
    }


def main() -> int:
    args = parse_args()
    stage = args.stage
    extraction_config = load_yaml(args.extraction_config)
    local_model_config = load_yaml(args.model_config)
    model_config = merged_model_config(extraction_config, local_model_config)
    if args.max_output_tokens is not None:
        model_config["max_output_tokens"] = args.max_output_tokens
    parse_retries = args.parse_retries if args.parse_retries is not None else parse_retry_attempts(extraction_config, local_model_config)
    max_prompt_tokens = args.max_prompt_tokens if args.max_prompt_tokens is not None else context_max_prompt_tokens(extraction_config, local_model_config)
    system_prompt = Path(STAGES[stage]["prompt"]).read_text(encoding="utf-8")
    client = OpenAICompatibleClient(model_config)
    if stage == "evaluation_dialogue_case":
        manifest = run_case_stage(args, client, system_prompt, max_prompt_tokens, parse_retries)
    else:
        manifest = run_topic_stage(args, client, system_prompt, max_prompt_tokens, parse_retries)
    print(json.dumps({"stage": stage, "rows": manifest["rows"], "failure_rows": manifest["failure_rows"], "output": manifest["output"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
