"""Atomic checkpoints for the stages of one patient or course design job."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
from typing import Any
import uuid


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def model_signature(client):
    """Bind cached generations to inference settings without persisting credentials."""
    seen = set()
    while id(client) not in seen:
        seen.add(id(client))
        concrete = getattr(client, "_hosted_client", None) or getattr(client, "_client", None)
        if concrete is None:
            break
        client = concrete
    settings = {name: getattr(client, name, None) for name in (
        "model", "base_url", "temperature", "max_output_tokens", "thinking", "reasoning_effort", "response_format_json")}
    settings["credential_sha256"] = fingerprint(getattr(client, "api_key", None))
    return settings


def implementation_signature():
    directory = Path(__file__).parent
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in ("generation_workflow.py", "scenario_generation.py", "patient_builder.py", "course_generation.py")}


@contextmanager
def generation_checkpoint(root: Path, run_id: str, binding: Any, progress=None):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,180}", run_id):
        raise ValueError("Invalid generation job ID")
    directory = root / "workflows" / run_id
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield GenerationCheckpoint(directory, binding, progress)


class GenerationCheckpoint:
    def __init__(self, directory: Path, binding: Any, progress=None):
        self.path = directory / "checkpoint.json"
        self.progress = progress
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {
            "binding": fingerprint(binding), "stages": {}, "ids": {k: uuid.uuid4().hex for k in ("scenario", "case", "bundle")},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if self.state["binding"] != fingerprint(binding):
            raise ValueError("Generation inputs or model changed. Start a new job to use the new configuration.")
        self.save()

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    @property
    def trace(self):
        return [{"stage": name, **{k: v for k, v in item.items() if k in {"status", "error"}}}
                for name, item in self.state["stages"].items()]

    def report(self, stage):
        if self.progress:
            self.progress(stage=stage, stages=self.trace)

    def run(self, stage, inputs, operation, validate=None):
        signature = fingerprint(inputs)
        previous = self.state["stages"].get(stage, {})
        if previous.get("status") == "completed" and previous.get("signature") == signature:
            value = deepcopy(previous["value"])
            if validate:
                validate(value)
            self.report(stage)
            return value
        self.state["stages"][stage] = {"status": "running", "signature": signature}
        self.save()
        self.report(stage)
        try:
            value = operation()
            if validate:
                validate(value)
            self.state["stages"][stage].update(status="completed", value=value)
        except Exception as exc:
            from theraskill_agent.text import redact_secrets
            self.state["stages"][stage].update(status="failed", error=redact_secrets(str(exc))[:500])
            self.save()
            self.report(stage)
            raise
        self.save()
        self.report(stage)
        return deepcopy(value)
