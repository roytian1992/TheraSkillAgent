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
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from theraskill_agent.paths import data_root
from theraskill_agent.reviews import build_review_plan, run_skill_reviews
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
    app = FastAPI(title="TheraSkill Transcript API", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": "theraskill-transcript"}

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

    @app.post("/v1/transcript/review")
    def review(request: TranscriptRequest) -> dict[str, Any]:
        document = _document(request)
        analysis = analyze_transcript(document)
        skills = request.skills or analysis["recommended_skills"]
        if request.mode == "model":
            reviews = run_skill_reviews(
                document,
                analysis,
                skills=skills,
                max_turns=request.max_turns,
                output_language=None if request.output_language == "auto" else request.output_language,
            )
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

    @app.post("/v1/transcript/package")
    def package(request: TranscriptRequest) -> dict[str, Any]:
        document = _document(request)
        analysis = analyze_transcript(document)
        reviews: dict[str, Any] | None = None
        if request.mode == "model":
            reviews = run_skill_reviews(
                document,
                analysis,
                skills=request.skills or analysis["recommended_skills"],
                max_turns=request.max_turns,
                output_language=None if request.output_language == "auto" else request.output_language,
            )
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

    @app.get("/v1/transcript/bundles/{bundle_id}/download")
    def download_bundle(bundle_id: str) -> FileResponse:
        if not re.fullmatch(r"bundle_[a-f0-9]{16}", bundle_id):
            raise HTTPException(status_code=404, detail="Bundle not found")
        archive = data_root() / "transcript" / "bundles" / f"{bundle_id}.zip"
        if not archive.is_file():
            raise HTTPException(status_code=404, detail="Bundle not found")
        return FileResponse(archive, filename=f"{bundle_id}.zip", media_type="application/zip")

    return app
