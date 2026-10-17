#!/usr/bin/env python3
"""Wait for formal hybrid benchmark outputs, then run the gate audit."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from retrieval_surface_registry import (
    FORMAL_HYBRID_AUDIT_PATH,
    FORMAL_HYBRID_AUDIT_SUMMARY_PATH,
    FORMAL_HYBRID_QA_PATH,
    FORMAL_HYBRID_REPORT_PATH,
    FORMAL_HYBRID_WAIT_AND_AUDIT_LOG_PATH,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
THERASKILL_PYTHON = Path(sys.executable)
DEFAULT_FORMAL_QA = FORMAL_HYBRID_QA_PATH
DEFAULT_FORMAL_REPORT = FORMAL_HYBRID_REPORT_PATH
DEFAULT_AUDIT = FORMAL_HYBRID_AUDIT_PATH
DEFAULT_SUMMARY = FORMAL_HYBRID_AUDIT_SUMMARY_PATH
DEFAULT_LOG = FORMAL_HYBRID_WAIT_AND_AUDIT_LOG_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal-qa", default=str(DEFAULT_FORMAL_QA))
    parser.add_argument("--formal-report", default=str(DEFAULT_FORMAL_REPORT))
    parser.add_argument("--audit", default=str(DEFAULT_AUDIT))
    parser.add_argument("--summary", default=str(DEFAULT_SUMMARY))
    parser.add_argument("--theraskill-python", default=str(THERASKILL_PYTHON))
    parser.add_argument("--log", default=str(DEFAULT_LOG))
    parser.add_argument("--poll-seconds", type=int, default=300)
    parser.add_argument("--max-wait-seconds", type=int, default=0, help="0 means wait indefinitely.")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_log(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    args = parse_args()
    qa_path = Path(args.formal_qa)
    report_path = Path(args.formal_report)
    log_path = Path(args.log)
    start = time.time()
    while not (qa_path.exists() and report_path.exists()):
        elapsed = time.time() - start
        append_log(log_path, {
            "event": "formal_outputs_wait",
            "created_at": utc_now(),
            "elapsed_seconds": round(elapsed, 3),
            "formal_qa_exists": qa_path.exists(),
            "formal_report_exists": report_path.exists(),
        })
        if args.max_wait_seconds and elapsed >= args.max_wait_seconds:
            print(json.dumps({
                "status": "timeout",
                "formal_qa_exists": qa_path.exists(),
                "formal_report_exists": report_path.exists(),
                "elapsed_seconds": round(elapsed, 3),
            }, ensure_ascii=False, indent=2, sort_keys=True))
            return 2
        time.sleep(max(args.poll_seconds, 10))

    cmd = [
        args.theraskill_python,
        "scripts/AdvancedRetrieval/audit_formal_hybrid_benchmark.py",
        "--formal-qa", str(qa_path),
        "--formal-report", str(report_path),
        "--audit", args.audit,
        "--summary", args.summary,
        "--overwrite",
    ]
    append_log(log_path, {"event": "audit_start", "created_at": utc_now(), "cmd": cmd})
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    append_log(log_path, {
        "event": "audit_complete",
        "created_at": utc_now(),
        "returncode": proc.returncode,
        "output_tail": "\n".join((proc.stdout or "").splitlines()[-40:]),
    })
    print(proc.stdout, end="")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
