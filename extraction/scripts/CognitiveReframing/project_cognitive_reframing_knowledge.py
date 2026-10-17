#!/usr/bin/env python3
"""Project Cognitive-Reframing data into knowledge-unit envelopes.

This is a deterministic projection. The source dataset already contains
thinking-trap labels and reframe examples, so this script does not call an LLM.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DATASET = "Cognitive-Reframing"
SOURCE_ROOT = "datasets/Cognitive-Reframing"
LICENSE = "CC-BY-NC-ND-4.0"
OUTPUTS = {
    "thinking_trap_unit": "outputs/knowledge/units/Cognitive-Reframing.thinking_trap_unit.jsonl",
    "reframing_step": "outputs/knowledge/units/Cognitive-Reframing.reframing_step.jsonl",
}
MANIFEST = "outputs/knowledge/manifests/Cognitive-Reframing.projection_manifest.json"
QA = "outputs/knowledge/manifests/Cognitive-Reframing.projection_qa.json"

TRAP_ALIASES = {
    "overgeneralization": "Overgeneralizing",
    "overgeneralizing": "Overgeneralizing",
    "mind reading": "Mind reading",
    "fortune telling": "Fortune telling",
    "labeling": "Labeling",
    "should statements": "Should statements",
    "catastrophizing": "Catastrophizing",
    "negative feeling or emotion": "Negative feeling or emotion",
    "all-or-nothing thinking": "All-or-nothing thinking",
    "personalizing": "Personalizing",
    "personalization": "Personalizing",
    "not distorted": "Not distorted",
    "emotional reasoning": "Emotional reasoning",
    "blaming": "Blaming",
    "disqualifying the positive": "Disqualifying the positive",
    "comparing and despairing": "Comparing and despairing",
    "comparing": "Comparing and despairing",
    "magnification": "Magnification",
    "none": "None",
}

TRAP_DEFINITIONS = {
    "All-or-nothing thinking": "Viewing a situation in extreme, binary terms rather than recognizing nuance.",
    "Blaming": "Attributing responsibility in a one-sided way that may intensify distress or reduce agency.",
    "Catastrophizing": "Expecting or emphasizing the worst possible outcome.",
    "Comparing and despairing": "Judging oneself negatively through unfavorable comparisons with others.",
    "Disqualifying the positive": "Discounting positive evidence, strengths, or progress.",
    "Emotional reasoning": "Treating a feeling as proof that a negative belief is true.",
    "Fortune telling": "Predicting a negative future outcome as if it were certain.",
    "Labeling": "Applying a broad negative label to oneself, others, or a situation.",
    "Magnification": "Exaggerating the importance or severity of a negative event or flaw.",
    "Mind reading": "Assuming one knows what others think, usually in a negative direction.",
    "Negative feeling or emotion": "The thought is dominated by negative affect that may need validation and reframing.",
    "Not distorted": "The source label indicates no clear cognitive distortion.",
    "Overgeneralizing": "Drawing a broad negative conclusion from a limited event or example.",
    "Personalizing": "Taking excessive personal responsibility for events or others' reactions.",
    "Should statements": "Using rigid should/must expectations that intensify guilt, shame, or pressure.",
    "None": "The reframing row did not specify an addressed thinking trap.",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", default=SOURCE_ROOT)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--manifest", default=MANIFEST)
    parser.add_argument("--qa-report", default=QA)
    return parser.parse_args()


def normalize_trap(label: str | None) -> str:
    key = str(label or "").strip().lower()
    return TRAP_ALIASES.get(key, str(label or "").strip() or "None")


def split_traps(value: str | None) -> list[str]:
    if not value:
        return ["None"]
    traps = [normalize_trap(part) for part in str(value).split(",") if part.strip()]
    return traps or ["None"]


def unit_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def unit_id(knowledge_type: str, source_id: str) -> str:
    return f"ku_cognitive_reframing_{knowledge_type}_{unit_hash(source_id)}"


def file_sha256(path: str | Path) -> str | None:
    path = Path(path)
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def read_reframing_rows(source_root: Path) -> list[dict[str, Any]]:
    with (source_root / "data/reframing_dataset.csv").open("r", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_thinking_trap_rows(source_root: Path) -> list[dict[str, Any]]:
    rows = []
    with (source_root / "data/thinking_traps.jsonl").open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if line.strip():
                row = json.loads(line)
                row["_line_no"] = line_no
                rows.append(row)
    return rows


def source_base(source_id: str, source_file: str, **extra: Any) -> dict[str, Any]:
    source = {
        "dataset": DATASET,
        "source_id": source_id,
        "language": "en",
        "source_construction_type": "annotated_cognitive_reframing_dataset",
        "evidence_tier": "annotated_method_dataset",
        "license": LICENSE,
        "source_file": source_file,
    }
    source.update(extra)
    return source


def wrap_unit(knowledge_type: str, source: dict[str, Any], content: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "theraskill_knowledge_unit_v1",
        "unit_id": unit_id(knowledge_type, source["source_id"]),
        "knowledge_type": knowledge_type,
        "source": source,
        "content": {knowledge_type: content},
        "quality": {
            "quality_status": content.get("quality_status"),
            "review_status": "deterministic_projection",
        },
        "normalization": {
            "controlled_label_audit": {
                "status": "source_label_normalized",
                "issue_count": 0,
                "issues": [],
            }
        },
        "retrieval": {
            "embedding_status": "not_embedded",
            "embedding_text": build_embedding_text(knowledge_type, content),
        },
        "provenance": provenance,
    }


def build_embedding_text(knowledge_type: str, content: dict[str, Any]) -> str:
    if knowledge_type == "thinking_trap_unit":
        return "\n".join(
            [
                str(content.get("trap_label") or ""),
                str(content.get("definition") or ""),
                json.dumps(content.get("recognition_cues") or [], ensure_ascii=False),
                json.dumps(content.get("example_thoughts") or [], ensure_ascii=False),
            ]
        ).strip()
    return "\n".join(
        [
            str(content.get("situation") or ""),
            str(content.get("negative_thought") or ""),
            json.dumps(content.get("thinking_traps") or [], ensure_ascii=False),
            str(content.get("reframe") or ""),
        ]
    ).strip()


def build_thinking_trap_units(source_root: Path, trap_rows: list[dict[str, Any]], reframing_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    examples = defaultdict(list)
    counts = Counter()
    for row in trap_rows:
        label = normalize_trap(row.get("completion"))
        prompt = str(row.get("prompt") or "").replace("->", "").strip()
        counts[label] += 1
        if prompt and len(examples[label]) < 12:
            examples[label].append({"thought": prompt, "source_id": f"thinking_traps.jsonl:{row['_line_no']}"})
    reframe_counts = Counter()
    for row in reframing_rows:
        for trap in split_traps(row.get("thinking_traps_addressed")):
            reframe_counts[trap] += 1
            if len(examples[trap]) < 12 and row.get("thought"):
                examples[trap].append({"thought": row["thought"], "source_id": f"reframing_dataset.csv:{reframing_rows.index(row)+1}"})

    labels = sorted(set(TRAP_DEFINITIONS) | set(counts) | set(reframe_counts))
    rows = []
    for label in labels:
        content = {
            "trap_label": label,
            "definition": TRAP_DEFINITIONS.get(label, "Source-provided thinking trap label without a curated definition."),
            "recognition_cues": examples.get(label, [])[:8],
            "example_thoughts": examples.get(label, [])[:8],
            "source_example_count": counts.get(label, 0),
            "reframing_example_count": reframe_counts.get(label, 0),
            "quality_status": {
                "status": "usable" if counts.get(label, 0) or reframe_counts.get(label, 0) else "limited",
                "limitations": ["Definitions are lightweight normalizations of source labels; this is not a diagnostic taxonomy."],
            },
        }
        source = source_base(
            f"thinking_trap/{label}",
            "data/thinking_traps.jsonl;data/reframing_dataset.csv",
            trap_label=label,
            source_example_count=content["source_example_count"],
            reframing_example_count=content["reframing_example_count"],
        )
        provenance = {
            "projection": "deterministic_label_aggregation",
            "source_root": str(source_root),
            "label_normalization": TRAP_ALIASES,
        }
        rows.append(wrap_unit("thinking_trap_unit", source, content, provenance))
    return rows


def build_reframing_step_units(source_root: Path, reframing_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for idx, row in enumerate(reframing_rows, start=1):
        traps = split_traps(row.get("thinking_traps_addressed"))
        source_id = f"reframing_dataset.csv:{idx}"
        content = {
            "situation": row.get("situation"),
            "negative_thought": row.get("thought"),
            "thinking_traps": traps,
            "reframe": row.get("reframe"),
            "reframing_operation": "source_provided_reframe",
            "linguistic_attributes": {
                "source_dataset_attributes_available": False,
                "note": "The local CSV contains situation, thought, reframe, and thinking_traps_addressed; it does not include the paper's seven linguistic attribute scores.",
            },
            "quality_status": {
                "status": "usable",
                "limitations": [
                    "No expert quality score is present in the local row.",
                    "License is CC-BY-NC-ND-4.0; use as an internal research knowledge reference, not as freely transformable content.",
                ],
            },
        }
        source = source_base(
            source_id,
            "data/reframing_dataset.csv",
            row_index=idx,
            thinking_traps=traps,
        )
        provenance = {
            "projection": "deterministic_row_projection",
            "source_root": str(source_root),
            "raw_thinking_traps_addressed": row.get("thinking_traps_addressed"),
        }
        rows.append(wrap_unit("reframing_step", source, content, provenance))
    return rows


def main() -> int:
    args = parse_args()
    source_root = Path(args.source_root)
    reframing_rows = read_reframing_rows(source_root)
    trap_rows = read_thinking_trap_rows(source_root)
    thinking_units = build_thinking_trap_units(source_root, trap_rows, reframing_rows)
    reframe_units = build_reframing_step_units(source_root, reframing_rows)
    output_shas = {
        "thinking_trap_unit": write_jsonl(OUTPUTS["thinking_trap_unit"], thinking_units, overwrite=args.overwrite),
        "reframing_step": write_jsonl(OUTPUTS["reframing_step"], reframe_units, overwrite=args.overwrite),
    }
    trap_labels = Counter()
    for unit in thinking_units:
        trap_labels[(unit.get("source") or {}).get("trap_label")] += 1
    qa = {
        "schema_version": "theraskill_cognitive_reframing_projection_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET,
        "status": "complete",
        "source_counts": {
            "reframing_dataset_rows": len(reframing_rows),
            "thinking_traps_rows": len(trap_rows),
        },
        "unit_counts": {
            "thinking_trap_unit": len(thinking_units),
            "reframing_step": len(reframe_units),
        },
        "trap_labels": sorted(label for label in trap_labels if label),
        "issues": [],
    }
    manifest = {
        "schema_version": "theraskill_cognitive_reframing_projection_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET,
        "source_root": str(source_root),
        "license": LICENSE,
        "projection_policy": "deterministic projection from source labels and rows; no LLM calls",
        "source_files": {
            "reframing_dataset": {
                "path": str(source_root / "data/reframing_dataset.csv"),
                "sha256": file_sha256(source_root / "data/reframing_dataset.csv"),
                "rows": len(reframing_rows),
            },
            "thinking_traps": {
                "path": str(source_root / "data/thinking_traps.jsonl"),
                "sha256": file_sha256(source_root / "data/thinking_traps.jsonl"),
                "rows": len(trap_rows),
            },
        },
        "unit_files": {
            key: {
                "path": OUTPUTS[key],
                "sha256": output_shas[key],
                "rows": len(thinking_units) if key == "thinking_trap_unit" else len(reframe_units),
            }
            for key in OUTPUTS
        },
        "qa_report": args.qa_report,
    }
    write_json(args.qa_report, qa)
    write_json(args.manifest, manifest)
    print(json.dumps({"status": "complete", "unit_counts": qa["unit_counts"], "qa_report": args.qa_report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
