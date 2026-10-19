"""Small JSON transport for the evidence-preserving transcript pipeline.

The browser sends text that the user selected locally. The service performs
the same normalization, analysis and review-plan operations as the CLI and
tool boundary, so the UI does not reimplement domain rules in JavaScript.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from theraskill_agent.paths import data_root
from theraskill_agent.reviews import build_review_plan, run_skill_reviews, read_review_run, prepare_review
from theraskill_agent.transcript_jobs import TranscriptJobs
from theraskill_agent.task_store import TaskConflict
from theraskill_agent.transcript import analyze_transcript, build_bundle, parse_transcript_source


class TranscriptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2_000_000)
    filename: str = Field(default="dialogue.txt", min_length=1, max_length=256)
    language: str = Field(default="auto", pattern="^(auto|zh|en|mixed)$")
    output_language: str = Field(default="auto", pattern="^(auto|zh|en)$")
    speaker_role_overrides: dict[str, str] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list, max_length=20)
    mode: str = Field(default="plan", pattern="^(plan|model)$")
    max_turns: int | None = Field(default=None, ge=1, le=10000)
    include_raw: bool = False
    review_run_id: str | None = Field(default=None, pattern=r"^review_[a-f0-9]{64}$")


class TranscriptJobRequest(TranscriptRequest):
    operation: Literal["review", "package"] = "review"
    mode: Literal["model"] = "model"


class RetryRequest(BaseModel):
    revision: int | None = None


def _document(request: TranscriptRequest):
    try:
        document = parse_transcript_source(
            request.text, filename=request.filename, encoding="browser-text",
            language=request.language, speaker_role_overrides=request.speaker_role_overrides,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid transcript format: {exc}") from exc
    document.metadata["ingest_channel"] = "theraskill_workbench"
    return document


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app):
        yield
        jobs.close()

    app = FastAPI(title="TheraSkill Transcript API", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": "theraskill-transcript"}

    def model_review(request: TranscriptRequest, document, analysis, progress=None):
        try:
            return run_skill_reviews(
                document, analysis, skills=request.skills or analysis["recommended_skills"],
                max_turns=request.max_turns,
                output_language=None if request.output_language == "auto" else request.output_language,
                review_run_id=request.review_run_id,
                progress=progress,
            )
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=409 if "already using" in str(exc) or "changed" in str(exc) else 422, detail=str(exc)) from exc

    @app.get("/v1/transcript/reviews/{run_id}")
    def review_progress(run_id: str) -> dict[str, Any]:
        try:
            return read_review_run(run_id)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="Review not found") from exc

    @app.post("/v1/transcript/inspect")
    def inspect(request: TranscriptRequest) -> dict[str, Any]:
        document = _document(request)
        return {
            "operation": "inspect",
            "document": document.to_dict(),
            "sample_turns": [turn.to_dict() for turn in document.turns[:5]],
            "next_action": "analyze",
        }

    @app.post("/v1/transcript/analyze")
    def analyze(request: TranscriptRequest) -> dict[str, Any]:
        document = _document(request)
        analysis = analyze_transcript(document)
        return {
            "operation": "analyze",
            "document": document.to_dict(),
            "analysis": analysis,
            "next_action": "review",
        }

    def review_result(request: TranscriptRequest, progress=None) -> dict[str, Any]:
        document = _document(request)
        analysis = analyze_transcript(document)
        skills = request.skills or analysis["recommended_skills"]
        if request.mode == "model":
            reviews = model_review(request, document, analysis, progress)
        else:
            reviews = {
                "mode": "plan",
                "document_id": document.document_id,
                "plans": build_review_plan(document, analysis, skills=skills, max_turns=request.max_turns, output_language=None if request.output_language == "auto" else request.output_language),
            }
        return {
            "operation": "review",
            "document": document.to_dict(),
            "analysis": analysis,
            "reviews": reviews,
            "next_action": "review_results" if request.mode == "model" else "approve_model_review",
        }

    def package_result(request: TranscriptRequest, progress=None) -> dict[str, Any]:
        document = _document(request)
        analysis = analyze_transcript(document)
        reviews: dict[str, Any] | None = None
        if request.mode == "model":
            reviews = model_review(request, document, analysis, progress)
        if progress:
            progress({"stage": "package", "run_id": reviews.get("run_id") if reviews else None,
                      "planned_windows": (reviews or {}).get("coverage", {}).get("windows", 0),
                      "completed_windows": (reviews or {}).get("coverage", {}).get("completed_windows", 0),
                      "reused_windows": (reviews or {}).get("reused_windows", 0)})
        bundle_id = f"bundle_{uuid.uuid4().hex[:16]}"
        output_dir = data_root() / "transcript" / "bundles" / bundle_id
        artifacts = build_bundle(
            document,
            analysis,
            output_dir,
            include_raw=request.include_raw,
            raw_text=request.text,
            reviews=reviews,
        )
        shutil.make_archive(str(output_dir), "zip", root_dir=output_dir)
        return {
            "operation": "package",
            "bundle_id": bundle_id,
            "download_url": f"/v1/transcript/bundles/{bundle_id}/download",
            "document": document.to_dict(include_raw=False),
            "analysis": analysis,
            "reviews": reviews,
            "artifacts": artifacts,
            "manifest": json.loads(Path(artifacts["manifest"]).read_text(encoding="utf-8")),
            "next_action": "download_bundle",
        }

    # Keep existing synchronous transports for CLI/tool integrations.
    @app.post("/v1/transcript/review")
    def review(request: TranscriptRequest):
        return review_result(request)

    @app.post("/v1/transcript/package")
    def package(request: TranscriptRequest):
        return package_result(request)

    def prepare_job(payload):
        request = TranscriptRequest.model_validate(payload)
        document = _document(request)
        analysis = analyze_transcript(document)
        return prepare_review(document, analysis, skills=request.skills or analysis["recommended_skills"],
                              max_turns=request.max_turns,
                              output_language=None if request.output_language == "auto" else request.output_language)["run_id"]

    def execute_job(payload, operation, progress):
        request = TranscriptRequest.model_validate(payload)
        return (package_result if operation == "package" else review_result)(request, progress)

    jobs = TranscriptJobs(prepare_job, execute_job)
    app.state.transcript_jobs = jobs

    def job_guard(operation):
        try:
            return operation()
        except (KeyError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="Transcript job not found") from exc
        except TaskConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/transcript/jobs", status_code=202)
    def start_job(request: TranscriptJobRequest):
        return job_guard(lambda: jobs.submit(request.model_dump(mode="json", exclude={"operation"}), request.operation))

    @app.get("/v1/transcript/jobs")
    def list_jobs():
        return {"jobs": jobs.list()}

    @app.get("/v1/transcript/jobs/{job_id}")
    def get_job(job_id: str):
        return job_guard(lambda: jobs.get(job_id))

    @app.post("/v1/transcript/jobs/{job_id}/retry", status_code=202)
    def retry_job(job_id: str, request: RetryRequest):
        return job_guard(lambda: jobs.retry(job_id, revision=request.revision))

    @app.get("/v1/transcript/bundles/{bundle_id}/download")
    def download_bundle(bundle_id: str) -> FileResponse:
        if not re.fullmatch(r"bundle_[a-f0-9]{16}", bundle_id):
            raise HTTPException(status_code=404, detail="Bundle not found")
        archive = data_root() / "transcript" / "bundles" / f"{bundle_id}.zip"
        if not archive.is_file():
            raise HTTPException(status_code=404, detail="Bundle not found")
        return FileResponse(archive, filename=f"{bundle_id}.zip", media_type="application/zip")

    return app
