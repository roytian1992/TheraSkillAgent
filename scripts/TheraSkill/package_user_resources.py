#!/usr/bin/env python3
"""Build two directly installable knowledge and simulation ZIPs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def package_resources(source: Path, output: Path, version: str) -> list[dict]:
    from theraskill_agent.indexing.common import digest, safe_id
    from theraskill_agent.resources import pack_resource

    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    version = safe_id(version)
    original_manifest = source / "manifest.json"
    release = json.loads(original_manifest.read_text(encoding="utf-8"))
    provenance = {
        "original_release_id": release["release_id"],
        "original_manifest_sha256": digest(original_manifest),
        "original_counts": release.get("counts", {}),
        "license_note": "Original dataset and per-record use restrictions apply; the source-code license does not license the data.",
    }
    output.mkdir(parents=True, exist_ok=False)
    records = []
    for kind, payload in (
        ("knowledge", source / "knowledge/phase6_reviewed_knowledge_units.jsonl"),
        ("simulation", source / "simulation"),
    ):
        package_id = f"{kind}-{version}"
        archive = output / f"{package_id}.zip"
        print(f"Packaging {archive.name}", flush=True)
        records.append(pack_resource(
            payload, archive, package_id=package_id, kind=kind,
            license_id="LicenseRef-Original-Dataset-Restrictions",
            provenance=provenance,
        ))
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "data/theraskill/current")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", default="phase7-20261028")
    args = parser.parse_args()
    for record in package_resources(args.source, args.output, args.version):
        print(f"{record['id']}: {record['size']} bytes, SHA256 {record['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
