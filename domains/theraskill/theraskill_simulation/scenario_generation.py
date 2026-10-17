"""Bounded online scenario generation for research and instructor drafts."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ScenarioGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brief: str = Field(min_length=8, max_length=1200)
    scenario_family: str = "supportive_exploration"
    difficulty_band: str = "medium"
    risk_band: str = "low"
    language: str = "zh"
    scenario_tags: list[str] = Field(default_factory=list)
    constraints: str = Field(default="", max_length=2000)


class ScenarioGenerationBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    course_title: str = Field(min_length=1, max_length=200)
    course_description: str = Field(default="", max_length=1200)
    skill_tags: list[str] = Field(default_factory=list)
    brief: str = Field(min_length=8, max_length=1200)
    count: int = Field(default=4, ge=1, le=12)
    scenario_family: str = "supportive_exploration"
    difficulty_band: str = "medium"
    risk_band: str = "low"
    language: str = "zh"
    scenario_tags: list[str] = Field(default_factory=list)
    constraints: str = Field(default="", max_length=2000)


class ScenarioDraftUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_brief: str | None = Field(default=None, min_length=8, max_length=1200)
    scenario_family: str | None = Field(default=None, min_length=1, max_length=120)
    case_family: str | None = Field(default=None, min_length=1, max_length=200)
    difficulty_band: str | None = Field(default=None, min_length=1, max_length=40)
    risk_band: str | None = Field(default=None, min_length=1, max_length=40)
    scenario_tags: list[str] | None = None
    recommended_turn_budget: int | None = Field(default=None, ge=2, le=12)
    learner_role_card: dict[str, Any] | None = None


class ScenarioDraftReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "reject"]
    reason: str = Field(default="", max_length=2000)


class ScenarioGenerator(Protocol):
    def generate(self, request: ScenarioGenerationRequest) -> dict[str, Any]: ...


def _normalise_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _scenario_text(row: dict[str, Any]) -> str:
    tags = " ".join(str(item) for item in (row.get("scenario_tags") or []))
    return " ".join(
        part for part in (
            row.get("scenario_brief"),
            row.get("case_family"),
            tags,
        ) if part
    )


def build_quality_report(
    row: dict[str, Any],
    existing_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run deterministic pre-review checks for a generated synthetic scenario."""
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, *, severity: str = "error", detail: str = "") -> None:
        checks.append({
            "name": name,
            "status": "pass" if passed else severity,
            "detail": "检查通过" if passed else detail,
        })

    brief = _normalise_text(row.get("scenario_brief"))
    check("scenario_brief", 8 <= len(brief) <= 1200, detail="训练主题长度需在 8 至 1200 字符之间")
    tags = _clean_list(row.get("scenario_tags"), limit=12)
    check("scenario_tags", bool(tags), severity="warning", detail="建议至少保留一个训练标签")
    role_card = row.get("learner_role_card")
    check(
        "learner_role_card",
        isinstance(role_card, dict) and bool(role_card),
        severity="warning",
        detail="来访者角色卡为空，开始训练前应补充",
    )
    check("synthetic_marker", row.get("generated") is True and row.get("synthetic") is True, detail="必须标记为合成草稿")
    risk_band = _normalise_text(row.get("risk_band"))
    check("risk_band", risk_band in {"low", "elevated", "high"}, severity="warning", detail="风险带应为 low、elevated 或 high")
    bundle_path = str(row.get("case_bundle_path") or "")
    bundle_ok = bool(bundle_path) and Path(bundle_path).exists()
    check("case_bundle", bundle_ok, severity="error", detail="合成病例状态包不存在")
    if bundle_ok:
        try:
            bundle = json.loads(Path(bundle_path).read_text(encoding="utf-8"))
            authoritative = bundle.get("authoritative_layer") or {}
            check("visible_profile", bool(authoritative.get("visible_profile")), severity="warning", detail="公开病例信息为空")
            check("hidden_profile", bool(authoritative.get("hidden_profile")), severity="warning", detail="隐藏状态为空，可能降低连续性")
            check("continuity_seed", bool(authoritative.get("continuity_seed")), severity="warning", detail="连续性种子为空")
        except (OSError, ValueError, TypeError):
            check("case_bundle_json", False, detail="合成病例状态包不是有效 JSON")

    duplicates: list[dict[str, Any]] = []
    current_text = _normalise_text(_scenario_text(row))
    for other in existing_rows or []:
        if str(other.get("scenario_id")) == str(row.get("scenario_id")):
            continue
        other_text = _normalise_text(_scenario_text(other))
        if not current_text or not other_text:
            continue
        similarity = SequenceMatcher(None, current_text, other_text).ratio()
        if similarity >= 0.72:
            duplicates.append({"scenario_id": other.get("scenario_id"), "similarity": round(similarity, 3)})
    duplicates.sort(key=lambda item: float(item["similarity"]), reverse=True)
    duplicate = duplicates[0] if duplicates else None
    if duplicate:
        severity = "error" if float(duplicate["similarity"]) >= 0.9 else "warning"
        check(
            "duplicate_similarity",
            False,
            severity=severity,
            detail=f"与 {duplicate['scenario_id']} 的文本相似度为 {duplicate['similarity']:.3f}",
        )
    else:
        check("duplicate_similarity", True, detail="未发现高相似度生成情景")

    errors = sum(item["status"] == "error" for item in checks)
    warnings = sum(item["status"] == "warning" for item in checks)
    score = max(0, round(100 - errors * 35 - warnings * 8))
    status = "fail" if errors else "warn" if warnings else "pass"
    return {
        "status": status,
        "score": score,
        "checks": checks,
        "duplicate_of": duplicate["scenario_id"] if duplicate else None,
        "duplicate_similarity": duplicate["similarity"] if duplicate else None,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _clean_list(value: Any, *, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(" ".join(str(item).split()) for item in value if str(item).strip()))[:limit]


class LLMScenarioGenerator:
    def __init__(
        self,
        *,
        project_root: str | Path,
        extraction_config: str | Path,
        model_config: str | Path,
        output_root: str | Path,
    ) -> None:
        root = Path(project_root).resolve()
        common = root / "scripts/_common"
        simulation = root / "scripts/Simulation"
        import sys
        for path in (common, simulation):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))
        from domains.theraskill.theraskill_llm import OpenAICompatibleClient, load_prompt, load_yaml, merged_model_config

        config = merged_model_config(load_yaml(extraction_config), load_yaml(model_config))
        config["max_output_tokens"] = min(int(config.get("max_output_tokens") or 1800), 1800)
        self.client = OpenAICompatibleClient(config)
        self.prompts = {
            "zh": load_prompt(root / "prompts/Simulation/scenario_generation_zh.md"),
            "en": load_prompt(root / "prompts/Simulation/scenario_generation_en.md"),
        }
        self.output_root = Path(output_root).resolve()

    def _prompt_for(self, language: str) -> str:
        return self.prompts.get(str(language).lower(), self.prompts["zh"])

    def generate(self, request: ScenarioGenerationRequest) -> dict[str, Any]:
        from domains.theraskill.theraskill_llm import chat_json_with_repair

        result = chat_json_with_repair(
            self.client,
            self._prompt_for(request.language),
            request.model_dump(mode="json"),
            parse_retries=1,
            max_prompt_tokens=6000,
            force_json_first=True,
        )
        scenario_id = f"generated_{uuid.uuid4().hex}"
        case_id = f"generated_case_{uuid.uuid4().hex}"
        visible = result.get("visible_profile") or {}
        hidden = result.get("hidden_profile") or {}
        continuity = result.get("continuity_seed") or {}
        initial_state = result.get("initial_session_state") or {}
        brief = " ".join(str(result.get("scenario_brief") or request.brief).split())[:1200]
        tags = _clean_list(result.get("scenario_tags"), limit=12) or _clean_list(request.scenario_tags, limit=12)
        scenario = {
            "scenario_id": scenario_id,
            "scenario_family": str(result.get("scenario_family") or request.scenario_family),
            "case_id": case_id,
            "case_family": str(result.get("case_family") or "在线生成训练案例"),
            "difficulty_band": str(result.get("difficulty_band") or request.difficulty_band),
            "risk_band": str(result.get("risk_band") or request.risk_band),
            "language": request.language,
            "scenario_brief": brief,
            "scenario_tags": tags,
            "recommended_turn_budget": max(2, min(int(result.get("recommended_turn_budget") or 4), 12)),
            "split_labels": {"formal_split": "generated", "pilot_split": "generated"},
            "learner_role_card": result.get("learner_role_card") or {},
            "generated": True,
            "synthetic": True,
            "review_status": "draft",
            "review_history": [],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        bundle = {
            "schema_version": "theraskill_generated_case_bundle_v1",
            "bundle_id": f"generated_bundle_{uuid.uuid4().hex}",
            "case_id": case_id,
            "source_id": f"online::{scenario_id}",
            "metadata": {
                "case_family": scenario["case_family"],
                "risk_band": scenario["risk_band"],
                "difficulty_band": scenario["difficulty_band"],
                "scenario_tags": tags,
                "synthetic": True,
                "language": request.language,
            },
            "authoritative_layer": {
                "source_unit_ids": {},
                "visible_profile": visible,
                "hidden_profile": hidden,
                "continuity_seed": continuity,
                "report_dialogue_bridge": {},
                "patient_formulation": {},
            },
            "runtime_priors": {},
            "runtime_state_seed": {"initial_session_state": initial_state},
            "provenance": {
                "generated_by": "theraskill_online_scenario_generator_v1",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "request": request.model_dump(mode="json"),
            },
        }
        case_path = self.output_root / f"{case_id}.json"
        case_path.parent.mkdir(parents=True, exist_ok=True)
        case_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        scenario["case_bundle_path"] = str(case_path)
        return {"scenario": scenario, "case_bundle": bundle}

    def generate_batch(self, request: ScenarioGenerationBatchRequest) -> list[dict[str, Any]]:
        generated: list[dict[str, Any]] = []
        for index in range(request.count):
            variation = (
                f"{request.constraints.strip()}\n"
                + (
                    f"This is scenario {index + 1}/{request.count} in a course batch. "
                    "Vary the trigger or client goal so scenarios in the same batch are distinct."
                    if str(request.language).lower() == "en"
                    else (
                        f"这是课程批次中的第 {index + 1}/{request.count} 个场景。"
                        "请改变触发情境或来访者目标，避免与同批其他场景重复。"
                    )
                )
            ).strip()
            generated.append(self.generate(ScenarioGenerationRequest(
                brief=request.brief,
                scenario_family=request.scenario_family,
                difficulty_band=request.difficulty_band,
                risk_band=request.risk_band,
                language=request.language,
                scenario_tags=request.scenario_tags,
                constraints=variation,
            )))
        return generated
