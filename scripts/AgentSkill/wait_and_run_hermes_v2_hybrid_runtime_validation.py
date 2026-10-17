#!/usr/bin/env python3
"""Wait for formal retrieval gate pass, then run Hermes v2-hybrid runtime validation."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
ADVANCED_RETRIEVAL_DIR = SCRIPT_DIR.parent / "AdvancedRetrieval"
for path in (SCRIPT_DIR, ADVANCED_RETRIEVAL_DIR, COMMON_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from io_utils import read_json
from retrieval_surface_registry import FORMAL_HYBRID_AUDIT_PATH
from hermes_surface_registry import RUN_HERMES_QWEN_RUNTIME_VALIDATION_RELPATH
from runtime_validation_paths import (
    qwen_compatibility_log_path,
    qwen_compatibility_v2_cases_path,
    qwen_compatibility_v2_cases_qa_path,
    qwen_compatibility_v2_runtime_surface,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
THERASKILL_PYTHON = Path(sys.executable)
DEFAULT_GATE_AUDIT = FORMAL_HYBRID_AUDIT_PATH
DEFAULT_SURFACE = qwen_compatibility_v2_runtime_surface()
DEFAULT_CASES = qwen_compatibility_v2_cases_path()
DEFAULT_CASE_QA = qwen_compatibility_v2_cases_qa_path()
DEFAULT_RUN_ROOT = DEFAULT_SURFACE.run_root
DEFAULT_REPORT = DEFAULT_SURFACE.report
DEFAULT_QA = DEFAULT_SURFACE.qa
DEFAULT_SUMMARY = DEFAULT_SURFACE.summary
DEFAULT_LOG = qwen_compatibility_log_path("wait_and_run_hermes_v2_hybrid_runtime_validation.log")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate-audit", default=str(DEFAULT_GATE_AUDIT))
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--case-qa", default=str(DEFAULT_CASE_QA))
    parser.add_argument("--run-root", default=str(DEFAULT_RUN_ROOT))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--qa-report", default=str(DEFAULT_QA))
    parser.add_argument("--summary", default=str(DEFAULT_SUMMARY))
    parser.add_argument("--theraskill-python", default=str(THERASKILL_PYTHON))
    parser.add_argument("--log", default=str(DEFAULT_LOG))
    parser.add_argument("--endpoint", choices=["8001", "8002"], default="8002")
    parser.add_argument("--poll-seconds", type=int, default=300)
    parser.add_argument("--max-wait-seconds", type=int, default=0, help="0 means wait indefinitely.")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--skip-service-preflight", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_log(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")


def validate_inputs(case_qa_path: Path) -> dict[str, Any]:
    case_qa = read_json(case_qa_path)
    if not case_qa:
        return {"ok": False, "issue": "case_qa_missing", "path": str(case_qa_path)}
    if case_qa.get("status") != "complete" or case_qa.get("issues_count") != 0:
        return {"ok": False, "issue": "case_qa_not_complete", "case_qa": case_qa}
    if case_qa.get("case_count") != 30:
        return {"ok": False, "issue": "case_count_not_30", "case_qa": case_qa}
    return {"ok": True, "case_qa": case_qa}


def main() -> int:
    args = parse_args()
    gate_path = Path(args.gate_audit)
    case_qa_path = Path(args.case_qa)
    log_path = Path(args.log)
    input_check = validate_inputs(case_qa_path)
    if not input_check["ok"]:
        append_log(log_path, {"event": "input_check_failed", "created_at": utc_now(), **input_check})
        print(json.dumps({"status": "input_check_failed", **input_check}, ensure_ascii=False, indent=2, sort_keys=True))
        return 2

    start = time.time()
    gate = read_json(gate_path) if gate_path.exists() else None
    while not gate or gate.get("status") != "passed":
        elapsed = time.time() - start
        append_log(log_path, {
            "event": "formal_gate_wait",
            "created_at": utc_now(),
            "elapsed_seconds": round(elapsed, 3),
            "gate_exists": gate is not None,
            "gate_status": (gate or {}).get("status"),
            "gate_issues_count": (gate or {}).get("issues_count"),
        })
        if args.max_wait_seconds and elapsed >= args.max_wait_seconds:
            print(json.dumps({
                "status": "timeout",
                "gate_exists": gate is not None,
                "gate_status": (gate or {}).get("status"),
                "elapsed_seconds": round(elapsed, 3),
            }, ensure_ascii=False, indent=2, sort_keys=True))
            return 3
        time.sleep(max(args.poll_seconds, 10))
        gate = read_json(gate_path) if gate_path.exists() else None

    cmd = [
        args.theraskill_python,
        str(RUN_HERMES_QWEN_RUNTIME_VALIDATION_RELPATH),
        "--cases", args.cases,
        "--mode", "retrieval",
        "--endpoint", args.endpoint,
        "--run-root", args.run_root,
        "--report", args.report,
        "--qa-report", args.qa_report,
        "--summary", args.summary,
        "--timeout-seconds", str(args.timeout_seconds),
        "--max-turns", str(args.max_turns),
        "--overwrite",
    ]
    if args.skip_service_preflight:
        cmd.append("--skip-service-preflight")
    append_log(log_path, {"event": "runtime_validation_start", "created_at": utc_now(), "cmd": cmd})
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    append_log(log_path, {
        "event": "runtime_validation_complete",
        "created_at": utc_now(),
        "returncode": proc.returncode,
        "output_tail": "\n".join((proc.stdout or "").splitlines()[-80:]),
    })
    print(proc.stdout, end="")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
