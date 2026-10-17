#!/usr/bin/env python3
"""Check that every locale-sensitive TheraSkill prompt has an English pair."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def check(prompt_root: Path) -> dict[str, object]:
    missing: list[str] = []
    checked: list[str] = []
    for source in sorted(prompt_root.glob("Simulation/*_zh.md")):
        target = source.with_name(source.name[:-6] + "_en.md")
        checked.append(str(source.relative_to(prompt_root)))
        if not target.exists():
            missing.append(str(target.relative_to(prompt_root)))
    return {
        "schema_version": "theraskill_prompt_coverage_v1",
        "checked": len(checked),
        "missing_english": missing,
        "status": "complete" if not missing else "partial",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt-root", type=Path, required=True)
    args = parser.parse_args()
    result = check(args.prompt_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
