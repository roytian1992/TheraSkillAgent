#!/usr/bin/env python3
"""Run the deterministic four-domain TheraSkill release gate."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    from .check_model_contract import check_model_contract
except ImportError:  # Direct ``python scripts/TheraSkill/run_release_gate.py`` usage.
    from check_model_contract import check_model_contract


DOMAINS = ("support", "simulation", "response_review", "contribution")
DEFAULT_PORTS = {"support": 8878, "simulation": 8876, "response_review": 8879, "contribution": 8881}
REQUIRED_RELEASE_FILES = (
    "manifest.json",
    "knowledge/phase6_reviewed_knowledge_units.jsonl",
    "retrieval/metadata_v2.sqlite",
    "retrieval/phase6_retrieval_fts.sqlite",
    "simulation/scenarios/scenario_registry.jsonl",
)


def _jsonl_count(path: Path) -> tuple[int, str | None]:
    count = 0
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                return count, f"{path}:{line_no}: invalid JSON ({exc})"
            if not isinstance(row, dict):
                return count, f"{path}:{line_no}: case must be an object"
            count += 1
    return count, None


def _check_eval_manifest(path: Path, failures: list[str]) -> dict[str, Any]:
    if not path.exists():
        failures.append(f"missing evaluation manifest: {path}")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        failures.append(f"invalid evaluation manifest {path}: {exc}")
        return {}
    if payload.get("schema_version") != "theraskill_four_domain_eval_v1":
        failures.append(f"unsupported evaluation manifest schema: {path}")
    domains = payload.get("domains")
    if not isinstance(domains, dict):
        failures.append("evaluation manifest has no domains map")
        return payload
    for domain in DOMAINS:
        entry = domains.get(domain)
        if not isinstance(entry, dict):
            failures.append(f"missing evaluation domain: {domain}")
            continue
        case_path = Path(str(entry.get("case_path", "")))
        count, error = _jsonl_count(case_path) if case_path.exists() else (0, f"missing case file: {case_path}")
        if error:
            failures.append(error)
        if count < 1:
            failures.append(f"evaluation domain has no cases: {domain}")
        if int(entry.get("case_count", -1)) != count:
            failures.append(f"case count mismatch for {domain}: manifest={entry.get('case_count')} actual={count}")
    return payload


def _check_health(domain: str, base_url: str, timeout: float, failures: list[str]) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/health"
    request = urllib.request.Request(url, headers={"Authorization": "Bearer local-release-gate"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
            if response.status != 200:
                failures.append(f"{domain} health returned HTTP {response.status}")
            return {"http_status": response.status, "body": body}
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        failures.append(f"{domain} health failed: {exc}")
        return {"error": str(exc)}


def run(
    runtime_root: Path,
    *,
    check_health: bool = True,
    timeout: float = 5.0,
    model_config: Path | None = None,
    expected_model: str | None = None,
) -> dict[str, Any]:
    failures: list[str] = []
    platform_root = runtime_root / "platform-data" / "theraskill"
    release_root = platform_root / "releases" / "current"
    if not release_root.exists():
        active = platform_root / "releases"
        candidates = sorted(path for path in active.iterdir() if path.is_dir()) if active.exists() else []
        if candidates:
            release_root = candidates[-1]
        else:
            failures.append(f"no TheraSkill release found below {active}")
    release_files = {}
    for relative in REQUIRED_RELEASE_FILES:
        target = release_root / relative
        release_files[relative] = target.exists()
        if not target.exists():
            failures.append(f"missing release artifact: {target}")
    eval_manifest = _check_eval_manifest(platform_root / "evaluations" / "manifest.json", failures)
    health: dict[str, Any] = {}
    if check_health:
        for domain, port in DEFAULT_PORTS.items():
            health[domain] = _check_health(domain, f"http://127.0.0.1:{port}", timeout, failures)
    model_contract: dict[str, Any] | None = None
    if model_config is not None:
        model_contract = check_model_contract(
            model_config,
            timeout=timeout,
            expected_model=expected_model,
        )
        failures.extend(f"model contract: {failure}" for failure in model_contract["failures"])
    result = {
        "schema_version": "theraskill_release_gate_result_v1",
        "status": "passed" if not failures else "failed",
        "runtime_root": str(runtime_root),
        "release_root": str(release_root),
        "release_files": release_files,
        "evaluation_manifest": eval_manifest,
        "health": health,
        "model_contract": model_contract,
        "failures": failures,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--skip-health", action="store_true")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--model-config", type=Path, help="Also verify Hermes config.yaml against /v1/models")
    parser.add_argument("--expected-model", help="Optional model id required by --model-config")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = run(
        args.runtime_root,
        check_health=not args.skip_health,
        timeout=args.timeout,
        model_config=args.model_config,
        expected_model=args.expected_model,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
