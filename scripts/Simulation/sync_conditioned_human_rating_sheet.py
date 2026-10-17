#!/usr/bin/env python3
"""Sync conditioned human-rating CSV edits into the canonical JSON sheet."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import read_json, write_json
from simulation_surface_registry import FORMAL_CONDITIONED_EVAL_ADJUDICATION_DIR
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ADJUDICATION_DIR = FORMAL_CONDITIONED_EVAL_ADJUDICATION_DIR


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adjudication-dir", default=str(DEFAULT_ADJUDICATION_DIR))
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()

def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> None:
    args = parse_args()
    adjudication_dir = Path(args.adjudication_dir)
    csv_path = adjudication_dir / "human_rating_sheet_template.csv"
    json_path = adjudication_dir / "human_rating_sheet_template.json"

    if not csv_path.exists():
        raise FileNotFoundError(csv_path)
    if not json_path.exists():
        raise FileNotFoundError(json_path)

    existing = read_json(json_path)
    existing_rows = existing.get("rows") or []
    existing_by_packet = {clean_text(row.get("packet_id")): row for row in existing_rows}

    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        csv_rows = list(reader)

    normalized_rows: list[dict[str, Any]] = []
    for row in csv_rows:
        packet_id = clean_text(row.get("packet_id"))
        if not packet_id:
            raise ValueError("CSV row missing packet_id")
        base = dict(existing_by_packet.get(packet_id) or {})
        for key, value in row.items():
            base[key] = clean_text(value)
        normalized_rows.append(base)

    payload = {
        "schema_version": existing.get("schema_version") or "theraskill_conditioned_human_rating_sheet_template_v1",
        "created_at": existing.get("created_at") or utc_now(),
        "synced_at": utc_now(),
        "rows": normalized_rows,
    }
    write_json(json_path, payload)


if __name__ == "__main__":
    main()
