#!/usr/bin/env python3
"""Finalize Advanced Retrieval v2 after the full dense index is complete.

The script is an orchestrated runbook:
1. verify full dense index completion and QA,
2. optionally build full FAISS index,
3. run formal 120-case hybrid benchmark,
4. write a finalization manifest.

Use --dry-run while the dense build is still running.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import ensure_output_path, read_json, write_json
from retrieval_surface_registry import (
    ADVANCED_RETRIEVAL_V2_FINALIZE_LOG_PATH,
    ADVANCED_RETRIEVAL_V2_FINALIZE_MANIFEST_PATH,
    DENSE_BGE_M3_INDEX_DIR,
    DENSE_BGE_M3_QA_PATH,
    FAISS_BGE_M3_INDEX_DIR,
    FAISS_BGE_M3_QA_PATH,
    FAISS_BGE_M3_REPORT_PATH,
    FORMAL_HYBRID_QA_PATH,
    FORMAL_HYBRID_REPORT_PATH,
    FORMAL_HYBRID_SUMMARY_PATH,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
THERASKILL_PYTHON = Path(sys.executable)
DEFAULT_DENSE_DIR = DENSE_BGE_M3_INDEX_DIR
DEFAULT_DENSE_QA = DENSE_BGE_M3_QA_PATH
DEFAULT_FAISS_DIR = FAISS_BGE_M3_INDEX_DIR
DEFAULT_FINAL_MANIFEST = ADVANCED_RETRIEVAL_V2_FINALIZE_MANIFEST_PATH
DEFAULT_RUN_LOG = ADVANCED_RETRIEVAL_V2_FINALIZE_LOG_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dense-index-dir", default=str(DEFAULT_DENSE_DIR))
    parser.add_argument("--dense-qa", default=str(DEFAULT_DENSE_QA))
    parser.add_argument("--faiss-index-dir", default=str(DEFAULT_FAISS_DIR))
    parser.add_argument("--final-manifest", default=str(DEFAULT_FINAL_MANIFEST))
    parser.add_argument("--run-log", default=str(DEFAULT_RUN_LOG))
    parser.add_argument("--theraskill-python", default=str(THERASKILL_PYTHON))
    parser.add_argument("--faiss-python", default=sys.executable)
    parser.add_argument("--dense-backend", choices=["memmap", "faiss"], default="memmap")
    parser.add_argument("--skip-faiss-build", action="store_true")
    parser.add_argument("--wait", action="store_true", help="Poll until dense index is complete.")
    parser.add_argument("--poll-seconds", type=int, default=300)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_log(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")


def dense_status(dense_dir: Path, dense_qa: Path) -> dict[str, Any]:
    progress_path = dense_dir / "progress.json"
    metadata_path = dense_dir / "metadata.json"
    progress = read_json(progress_path) if progress_path.exists() else {}
    metadata = read_json(metadata_path) if metadata_path.exists() else {}
    qa = read_json(dense_qa) if dense_qa.exists() else {}
    metadata_counts = metadata.get("counts") or {}
    qa_counts = qa.get("counts") or {}
    qa_status = qa.get("status")
    qa_issues = qa.get("issues_count")
    metadata_status = metadata.get("status")
    metadata_complete = metadata_status == "complete"
    qa_complete = qa_status == "complete" and qa_issues == 0
    if metadata_complete and qa_complete:
        completed = int(metadata_counts.get("completed_count") or qa_counts.get("completed_count") or progress.get("completed_count") or 0)
        total = int(metadata_counts.get("target_eligible_rows") or qa_counts.get("target_eligible_rows") or progress.get("total_count") or completed)
    else:
        completed = int(progress.get("completed_count") or metadata_counts.get("completed_count") or qa_counts.get("completed_count") or 0)
        total = int(progress.get("total_count") or metadata_counts.get("target_eligible_rows") or qa_counts.get("target_eligible_rows") or 0)
    complete = bool(
        completed > 0
        and total > 0
        and completed == total
        and qa_complete
        and metadata_complete
    )
    return {
        "progress": progress,
        "metadata_exists": bool(metadata),
        "qa_exists": bool(qa),
        "completed_count": completed,
        "total_count": total,
        "metadata_status": metadata_status,
        "qa_status": qa_status,
        "qa_issues_count": qa_issues,
        "complete": complete,
    }


def command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def run_command(cmd: list[str], dry_run: bool, log_path: Path) -> dict[str, Any]:
    record = {"created_at": utc_now(), "cmd": cmd, "dry_run": dry_run}
    append_log(log_path, {"event": "command_start", **record})
    if dry_run:
        append_log(log_path, {"event": "command_skipped_dry_run", **record})
        return {"cmd": cmd, "skipped": True, "returncode": None}
    start = time.time()
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    result = {
        "cmd": cmd,
        "returncode": proc.returncode,
        "elapsed_seconds": round(time.time() - start, 3),
        "output_tail": "\n".join((proc.stdout or "").splitlines()[-40:]),
    }
    append_log(log_path, {"event": "command_complete", **result})
    if proc.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{result['output_tail']}")
    return result


def main() -> int:
    args = parse_args()
    dense_dir = Path(args.dense_index_dir)
    dense_qa = Path(args.dense_qa)
    faiss_dir = Path(args.faiss_index_dir)
    manifest_path = Path(args.final_manifest)
    log_path = Path(args.run_log)
    if not args.dry_run:
        ensure_output_path(manifest_path, overwrite=args.overwrite)

    dense = dense_status(dense_dir, dense_qa)
    while args.wait and not dense["complete"] and not args.dry_run:
        append_log(log_path, {"event": "dense_wait", "created_at": utc_now(), "dense_status": dense})
        time.sleep(max(args.poll_seconds, 10))
        dense = dense_status(dense_dir, dense_qa)

    faiss_available = False
    faiss_probe = [args.faiss_python, "-c", "import faiss; print('ok')"]
    try:
        probe = subprocess.run(faiss_probe, cwd=str(PROJECT_ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        faiss_available = probe.returncode == 0
    except Exception:
        faiss_available = False

    commands: list[list[str]] = []
    should_build_faiss = (args.dense_backend == "faiss") and not args.skip_faiss_build
    if should_build_faiss:
        commands.append([
            args.faiss_python,
            "scripts/AdvancedRetrieval/build_faiss_index.py",
            "--dense-index-dir", str(dense_dir),
            "--output-dir", str(faiss_dir),
            "--report", str(FAISS_BGE_M3_REPORT_PATH),
            "--qa-report", str(FAISS_BGE_M3_QA_PATH),
            "--overwrite",
        ])
    backend = args.dense_backend
    benchmark_cmd = [
        args.theraskill_python if backend == "memmap" else args.faiss_python,
        "scripts/AdvancedRetrieval/run_hybrid_retrieval_benchmark.py",
        "--dense-index-dir", str(dense_dir),
        "--dense-backend", backend,
        "--benchmark-scope", "formal",
        "--report", str(FORMAL_HYBRID_REPORT_PATH),
        "--qa-report", str(FORMAL_HYBRID_QA_PATH),
        "--summary", str(FORMAL_HYBRID_SUMMARY_PATH),
        "--overwrite",
    ]
    if backend == "faiss":
        benchmark_cmd.extend(["--faiss-index-dir", str(faiss_dir)])
    commands.append(benchmark_cmd)

    readiness_issues: list[dict[str, Any]] = []
    if not dense["complete"]:
        readiness_issues.append({"issue": "dense_index_not_complete", "dense_status": dense})
    if args.dense_backend == "faiss" and not faiss_available:
        readiness_issues.append({"issue": "faiss_python_unavailable", "faiss_python": args.faiss_python})
    if not Path(args.theraskill_python).exists():
        readiness_issues.append({"issue": "theraskill_python_missing", "theraskill_python": args.theraskill_python})
    if not command_exists(args.faiss_python) and not Path(args.faiss_python).exists():
        readiness_issues.append({"issue": "faiss_python_command_missing", "faiss_python": args.faiss_python})

    executed: list[dict[str, Any]] = []
    can_execute = not readiness_issues
    if args.dry_run or not can_execute:
        append_log(log_path, {
            "event": "finalize_not_executed",
            "created_at": utc_now(),
            "dry_run": args.dry_run,
            "readiness_issues": readiness_issues,
            "commands": commands,
        })
    else:
        for cmd in commands:
            executed.append(run_command(cmd, False, log_path))

    manifest = {
        "schema_version": "theraskill_advanced_retrieval_v2_finalize_manifest_v1",
        "created_at": utc_now(),
        "status": "ready" if can_execute else "not_ready",
        "dry_run": args.dry_run,
        "dense_status": dense,
        "faiss_available": faiss_available,
        "faiss_python": args.faiss_python,
        "theraskill_python": args.theraskill_python,
        "dense_backend": args.dense_backend,
        "skip_faiss_build": args.skip_faiss_build,
        "will_build_faiss": should_build_faiss,
        "readiness_issues_count": len(readiness_issues),
        "readiness_issues": readiness_issues,
        "planned_commands": commands,
        "executed_commands": executed,
        "outputs": {
            "faiss_index_dir": str(faiss_dir),
            "formal_report": str(FORMAL_HYBRID_REPORT_PATH),
            "formal_qa": str(FORMAL_HYBRID_QA_PATH),
            "formal_summary": str(FORMAL_HYBRID_SUMMARY_PATH),
        },
        "scope_note": "Run without --dry-run after dense_status.complete is true. Use --dense-backend faiss only when full FAISS index is built and faiss is available.",
    }
    write_json(manifest_path, manifest)
    print(json.dumps({
        "status": manifest["status"],
        "dry_run": args.dry_run,
        "readiness_issues_count": len(readiness_issues),
        "readiness_issues": readiness_issues,
        "manifest": str(manifest_path),
        "run_log": str(log_path),
        "planned_commands": commands,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if args.dry_run or can_execute else 1


if __name__ == "__main__":
    raise SystemExit(main())
