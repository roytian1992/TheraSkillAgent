#!/usr/bin/env python3
"""Build ESConv knowledge units."""

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


DATASET = "ESConv"
SOURCE_ROOT = "datasets/ESConv"
EXTRACTION_CONFIG = "configs/extraction_config.yaml"
MODEL_CONFIG = "configs/model_config.yaml"
PROMPT = "prompts/ESConv/support_strategy_sequence_zh.md"

OUTPUTS = {
    "support_strategy": "outputs/knowledge/units/ESConv.support_strategy.jsonl",
    "support_strategy_sequence": "outputs/knowledge/units/ESConv.support_strategy_sequence.jsonl",
}
MANIFESTS = {
    "support_strategy": "outputs/knowledge/manifests/ESConv.support_strategy.manifest.json",
    "support_strategy_sequence": "outputs/knowledge/manifests/ESConv.support_strategy_sequence.manifest.json",
}

STRATEGY_DEFINITIONS = {
    "Question": "Ask the seeker for more information, invite elaboration, or clarify their situation and feelings.",
    "Restatement or Paraphrasing": "Restate or paraphrase the seeker's words to show understanding and make the concern explicit.",
    "Reflection of feelings": "Name, reflect, or validate the seeker's emotional experience.",
    "Self-disclosure": "Share a related personal experience or perspective from the supporter.",
    "Affirmation and Reassurance": "Affirm the seeker, reassure them, or emphasize strengths, worth, hope, or care.",
    "Providing Suggestions": "Offer practical suggestions, coping steps, or possible actions.",
    "Information": "Provide informational framing, facts, explanations, or perspective relevant to the seeker's problem.",
    "Others": "Supporter action that does not fit the other ESConv strategy labels.",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["support_strategy", "support_strategy_sequence"], required=True)
    parser.add_argument("--source-root", default=SOURCE_ROOT)
    parser.add_argument("--output")
    parser.add_argument("--manifest")
    parser.add_argument("--failure-log")
    parser.add_argument("--extraction-config", default=EXTRACTION_CONFIG)
    parser.add_argument("--model-config", default=MODEL_CONFIG)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--conversation-id")
    parser.add_argument("--conversation-id-file")
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
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: str | Path, rows: list[dict[str, Any]], *, overwrite: bool) -> str:
    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(f"output exists: {path}; pass --overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            line = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            digest.update(line.encode("utf-8"))
            handle.write(line)
    return digest.hexdigest()


def append_jsonl(path: str | Path, row: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


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


def unit_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def unit_id(knowledge_type: str, source_id: str) -> str:
    return f"ku_esconv_{knowledge_type}_{unit_hash(source_id)}"


def load_conversations(source_root: str | Path) -> list[dict[str, Any]]:
    path = Path(source_root) / "ESConv.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    conversations = []
    for idx, conv in enumerate(data):
        conversation_id = f"esconv_{idx:04d}"
        dialog = []
        for turn_idx, turn in enumerate(conv.get("dialog") or [], start=1):
            annotation = turn.get("annotation") or {}
            dialog.append(
                {
                    "turn_id": f"t{turn_idx:03d}",
                    "speaker": turn.get("speaker"),
                    "content": str(turn.get("content") or "").strip(),
                    "strategy": annotation.get("strategy"),
                    "feedback": annotation.get("feedback"),
                }
            )
        conversations.append(
            {
                "conversation_id": conversation_id,
                "source_index": idx,
                "experience_type": conv.get("experience_type"),
                "emotion_type": conv.get("emotion_type"),
                "problem_type": conv.get("problem_type"),
                "situation": conv.get("situation"),
                "survey_score": conv.get("survey_score") or {},
                "dialog": dialog,
                "turn_count": len(dialog),
            }
        )
    return conversations


def base_source(source_id: str, **extra: Any) -> dict[str, Any]:
    source = {
        "dataset": DATASET,
        "source_id": source_id,
        "language": "en",
        "source_construction_type": "crowdsourced_emotional_support_conversation",
        "evidence_tier": "annotated_emotional_support_dialogue",
    }
    source.update(extra)
    return source


def support_strategy_embedding_text(content: dict[str, Any]) -> str:
    return "\n".join(
        [
            str(content.get("strategy_label") or ""),
            str(content.get("strategy_definition") or ""),
            json.dumps(content.get("typical_supporter_actions") or [], ensure_ascii=False),
            json.dumps(content.get("applicable_seeker_states") or [], ensure_ascii=False),
            json.dumps(content.get("expected_effect") or [], ensure_ascii=False),
        ]
    ).strip()


def sequence_embedding_text(content: dict[str, Any]) -> str:
    return "\n".join(
        [
            str(content.get("situation_summary") or ""),
            str(content.get("support_progression_summary") or ""),
            json.dumps(content.get("strategy_sequence") or [], ensure_ascii=False),
            json.dumps(content.get("helpful_strategy_patterns") or [], ensure_ascii=False),
        ]
    ).strip()


def wrap_unit(knowledge_type: str, source: dict[str, Any], content: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "theraskill_knowledge_unit_v1",
        "unit_id": unit_id(knowledge_type, source["source_id"]),
        "knowledge_type": knowledge_type,
        "source": source,
        "content": {knowledge_type: content},
        "quality": {
            "quality_status": content.get("quality_status"),
            "review_status": "deterministic_projection" if knowledge_type == "support_strategy" else "llm_extracted_unreviewed",
        },
        "normalization": {
            "controlled_label_audit": {
                "status": "source_label_schema_used",
                "issue_count": 0,
                "issues": [],
            }
        },
        "retrieval": {
            "embedding_status": "not_embedded",
            "embedding_text": support_strategy_embedding_text(content) if knowledge_type == "support_strategy" else sequence_embedding_text(content),
        },
        "provenance": provenance,
    }


def build_support_strategy_units(conversations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_strategy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    strategy_problem = defaultdict(Counter)
    strategy_emotion = defaultdict(Counter)
    strategy_feedback = defaultdict(Counter)
    for conv in conversations:
        for turn in conv["dialog"]:
            strategy = turn.get("strategy")
            if not strategy:
                continue
            item = {
                "conversation_id": conv["conversation_id"],
                "turn_id": turn["turn_id"],
                "text": turn["content"],
                "problem_type": conv.get("problem_type"),
                "emotion_type": conv.get("emotion_type"),
            }
            by_strategy[strategy].append(item)
            strategy_problem[strategy][conv.get("problem_type")] += 1
            strategy_emotion[strategy][conv.get("emotion_type")] += 1
        for idx, turn in enumerate(conv["dialog"]):
            feedback = turn.get("feedback")
            if feedback and idx > 0:
                prev_strategy = conv["dialog"][idx - 1].get("strategy")
                if prev_strategy:
                    strategy_feedback[prev_strategy][str(feedback)] += 1

    rows = []
    for strategy in sorted(STRATEGY_DEFINITIONS):
        examples = by_strategy.get(strategy, [])[:20]
        content = {
            "strategy_label": strategy,
            "strategy_definition": STRATEGY_DEFINITIONS[strategy],
            "typical_supporter_actions": [example["text"] for example in examples[:8]],
            "applicable_seeker_states": [
                {"problem_type": key, "count": value}
                for key, value in strategy_problem[strategy].most_common(8)
                if key
            ],
            "expected_effect": {
                "source_feedback_distribution_after_strategy": dict(strategy_feedback[strategy]),
                "interpretation_note": "Feedback is seeker turn-level source annotation following supporter actions; it is observational, not causal.",
            },
            "example_turns": examples,
            "source_conversation_count": len({example["conversation_id"] for example in by_strategy.get(strategy, [])}),
            "source_turn_count": len(by_strategy.get(strategy, [])),
            "quality_status": {
                "status": "usable" if by_strategy.get(strategy) else "insufficient_evidence",
                "limitations": ["Source labels are ESConv emotional-support strategy annotations, not clinical intervention labels."],
            },
        }
        source = base_source(
            f"strategy/{strategy}",
            strategy_label=strategy,
            source_strategy_turn_count=content["source_turn_count"],
            source_conversation_count=content["source_conversation_count"],
        )
        provenance = {
            "projection": "deterministic_from_esconv_strategy_labels",
            "source_file": str(Path(SOURCE_ROOT) / "ESConv.json"),
            "strategy_json": str(Path(SOURCE_ROOT) / "strategy.json"),
        }
        rows.append(wrap_unit("support_strategy", source, content, provenance))
    return rows


def emotion_change(conv: dict[str, Any]) -> str:
    seeker = (conv.get("survey_score") or {}).get("seeker") or {}
    try:
        initial = int(seeker.get("initial_emotion_intensity"))
        final = int(seeker.get("final_emotion_intensity"))
    except Exception:
        return "unknown"
    return "improved" if final < initial else "not_improved"


def build_sequence_payload(conv: dict[str, Any]) -> dict[str, Any]:
    seeker = (conv.get("survey_score") or {}).get("seeker") or {}
    return {
        "dataset": DATASET,
        "conversation_id": conv["conversation_id"],
        "source_index": conv["source_index"],
        "experience_type": conv.get("experience_type"),
        "emotion_type": conv.get("emotion_type"),
        "problem_type": conv.get("problem_type"),
        "situation": conv.get("situation"),
        "survey_score": conv.get("survey_score"),
        "initial_emotion_intensity": seeker.get("initial_emotion_intensity"),
        "final_emotion_intensity": seeker.get("final_emotion_intensity"),
        "emotion_change": emotion_change(conv),
        "dialogue_turns": conv["dialog"],
    }


def completed_conversation_ids(output_path: Path, failure_log: Path, resume: bool) -> set[str]:
    if not resume:
        return set()
    done = set()
    for row in read_jsonl(output_path):
        cid = (row.get("source") or {}).get("conversation_id")
        if cid:
            done.add(str(cid))
    for row in read_jsonl(failure_log):
        if row.get("conversation_id"):
            done.add(str(row["conversation_id"]))
    return done


def run_support_strategy(args: argparse.Namespace) -> dict[str, Any]:
    output = Path(args.output or OUTPUTS["support_strategy"])
    manifest_path = Path(args.manifest or MANIFESTS["support_strategy"])
    conversations = load_conversations(args.source_root)
    rows = build_support_strategy_units(conversations)
    if args.limit is not None:
        rows = rows[: args.limit]
    output_sha = write_jsonl(output, rows, overwrite=args.overwrite)
    manifest = {
        "schema_version": "theraskill_esconv_support_strategy_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET,
        "stage": "support_strategy",
        "source_root": args.source_root,
        "output": str(output),
        "output_sha256": output_sha,
        "rows": len(rows),
        "failure_rows": 0,
        "strategy_labels": sorted(STRATEGY_DEFINITIONS),
    }
    write_json(manifest_path, manifest)
    return manifest


def run_support_strategy_sequence(args: argparse.Namespace) -> dict[str, Any]:
    stage = "support_strategy_sequence"
    output = Path(args.output or OUTPUTS[stage])
    manifest_path = Path(args.manifest or MANIFESTS[stage])
    failure_log = Path(args.failure_log or output.with_suffix(".failures.jsonl"))
    if args.overwrite:
        output.unlink(missing_ok=True)
        failure_log.unlink(missing_ok=True)
    if output.exists() and not args.resume and not args.overwrite:
        raise FileExistsError(f"output exists: {output}; pass --resume or --overwrite")

    extraction_config = load_yaml(args.extraction_config)
    local_model_config = load_yaml(args.model_config)
    model_config = merged_model_config(extraction_config, local_model_config)
    if args.max_output_tokens is not None:
        model_config["max_output_tokens"] = args.max_output_tokens
    parse_retries = args.parse_retries if args.parse_retries is not None else parse_retry_attempts(extraction_config, local_model_config)
    max_prompt_tokens = args.max_prompt_tokens if args.max_prompt_tokens is not None else context_max_prompt_tokens(extraction_config, local_model_config)
    system_prompt = Path(PROMPT).read_text(encoding="utf-8")
    client = OpenAICompatibleClient(model_config)

    conversations = load_conversations(args.source_root)
    requested_ids = set()
    if args.conversation_id:
        requested_ids.add(args.conversation_id)
    if args.conversation_id_file:
        with Path(args.conversation_id_file).open("r", encoding="utf-8") as handle:
            requested_ids.update(line.strip() for line in handle if line.strip())
    if requested_ids:
        conversations = [conv for conv in conversations if conv["conversation_id"] in requested_ids]
    if args.limit is not None:
        conversations = conversations[: args.limit]
    done = completed_conversation_ids(output, failure_log, args.resume)
    counters = Counter()
    audits = []
    started_at = datetime.now(timezone.utc).isoformat()
    for conv in conversations:
        cid = conv["conversation_id"]
        if cid in done:
            counters["skipped_existing"] += 1
            continue
        payload = build_sequence_payload(conv)
        budget = prompt_budget_report(system_prompt, payload, max_prompt_tokens)
        audits.append({"conversation_id": cid, "problem_type": conv.get("problem_type"), **budget})
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
            source = base_source(
                f"conversation/{cid}",
                conversation_id=cid,
                source_index=conv["source_index"],
                problem_type=conv.get("problem_type"),
                emotion_type=conv.get("emotion_type"),
                source_turn_count=conv["turn_count"],
            )
            provenance = {
                "llm_stage": stage,
                "prompt_token_budget": budget,
                "source_file": str(Path(args.source_root) / "ESConv.json"),
            }
            append_jsonl(output, wrap_unit(stage, source, extracted, provenance))
            counters["success"] += 1
            print(f"{cid}: success", flush=True)
        except PromptBudgetExceededError as exc:
            append_jsonl(failure_log, {"conversation_id": cid, "stage": stage, "failure": {"reason": "prompt_token_budget_exceeded", "details": exc.report}})
            counters["prompt_token_budget_exceeded"] += 1
            print(f"{cid}: prompt_token_budget_exceeded", flush=True)
        except Exception as exc:  # noqa: BLE001
            append_jsonl(failure_log, {"conversation_id": cid, "stage": stage, "failure": {"reason": "runner_or_llm_failure", "error": f"{type(exc).__name__}: {exc}"}})
            counters["runner_or_llm_failure"] += 1
            print(f"{cid}: failure {type(exc).__name__}: {exc}", flush=True)

    rows = read_jsonl(output)
    failures = read_jsonl(failure_log)
    manifest = {
        "schema_version": "theraskill_esconv_strategy_sequence_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": started_at,
        "dataset": DATASET,
        "stage": stage,
        "source_root": args.source_root,
        "output": str(output),
        "output_sha256": file_sha256(output),
        "failure_log": str(failure_log),
        "failure_log_sha256": file_sha256(failure_log),
        "prompt": PROMPT,
        "prompt_sha256": text_sha256(system_prompt),
        "model_config": args.model_config,
        "extraction_config": args.extraction_config,
        "rows": len(rows),
        "failure_rows": len(failures),
        "counters": dict(counters),
        "prompt_token_audit": audits,
    }
    write_json(manifest_path, manifest)
    return manifest


def main() -> int:
    args = parse_args()
    if args.stage == "support_strategy":
        manifest = run_support_strategy(args)
    else:
        manifest = run_support_strategy_sequence(args)
    print(json.dumps({"stage": args.stage, "rows": manifest.get("rows"), "failure_rows": manifest.get("failure_rows"), "output": manifest.get("output")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
