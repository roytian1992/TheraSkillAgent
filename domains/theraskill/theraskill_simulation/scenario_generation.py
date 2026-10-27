"""Bounded online scenario generation for research and instructor drafts."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator
from .patient_builder import PatientDossier, compile_patient, dossier_from_bundle, public_role_card, save_bundle_version
from .generation_workflow import generation_checkpoint, fingerprint, model_signature, implementation_signature


class ScenarioGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brief: str = Field(min_length=8, max_length=1200)
    scenario_family: str = "supportive_exploration"
    difficulty_band: str = "medium"
    risk_band: str = "low"
    language: str = "zh"
    scenario_tags: list[str] = Field(default_factory=list)
    constraints: str = Field(default="", max_length=2000)
    patient: PatientDossier | None = None
    use_knowledge: bool = True
    request_id: str | None = Field(default=None, min_length=1, max_length=200)


class PatientBuildRequest(ScenarioGenerationRequest):
    patient: PatientDossier


class ScenarioForkRequest(BaseModel):
    brief: str | None = Field(default=None, min_length=8, max_length=1200)


class ScenarioGenerationBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

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
    # Optional course metadata travels with the resumable batch job. It is
    # copied back only after every generated case has been human-approved.
    template_id: str | None = None
    competency_ids: list[str] = Field(default_factory=list, max_length=12)
    scaffolding: list[str] = Field(default_factory=list, max_length=12)
    learning_units: dict[str, Any] = Field(default_factory=dict)
    learning_objectives: list[dict[str, Any]] = Field(default_factory=list, max_length=12)
    knowledge_references: list[dict[str, Any]] = Field(default_factory=list, max_length=12)
    design_retrieval: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_teaching_content(self):
        from .course_content import normalize_course_content
        content = normalize_course_content(self.learning_objectives, self.learning_units, self.knowledge_references)
        self.learning_objectives = content["learning_objectives"]
        self.learning_units = content["learning_units"]
        self.knowledge_references = content["knowledge_references"]
        return self


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
    patient: PatientDossier | None = None
    expected_revision: int | None = Field(default=None, ge=1)


class ScenarioDraftReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "reject"]
    reason: str = Field(default="", max_length=2000)
    override_model_review: bool = False


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


def _patient_signature(row: dict[str, Any]) -> str | None:
    """Compare authored facts, ignoring identifiers assigned during copying."""
    try:
        patient = json.loads(Path(row["case_bundle_path"]).read_text(encoding="utf-8")).get("patient_dossier")
        if not patient:
            return None
        patient = dict(patient)
        patient["recent_events"] = [{k: v for k, v in event.items() if k != "event_id"} for event in patient.get("recent_events", [])]
        return json.dumps(patient, ensure_ascii=False, sort_keys=True)
    except (KeyError, OSError, ValueError, TypeError):
        return None


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
    workflow = row.get("generation_workflow")
    if workflow:
        check("generation_review", workflow.get("review_status") in {"pass", "pass_after_repair"},
              severity="warning", detail="模型审阅尚未通过；请复核问题后重新检查或记录人工审核理由")
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
            if bundle.get("patient_dossier") is not None:
                patient = PatientDossier.model_validate(bundle["patient_dossier"])
                check("patient_concern", bool(patient.presenting_concern), detail="患者当前困扰为空")
                check("recent_experiences", bool(patient.recent_events), severity="warning", detail="尚未补充近期经历")
                check("patient_persona", bool(bundle.get("_patient_persona_v2")), detail="尚未编译患者人设与披露图")
                check("training_objectives", bool(patient.training_objectives), severity="warning", detail="建议补充训练目标")
                # This is a setup check, never a claim of clinical realism.
        except (OSError, ValueError, TypeError):
            check("case_bundle_json", False, detail="合成病例状态包不是有效 JSON")

    duplicates: list[dict[str, Any]] = []
    current_text = _normalise_text(_scenario_text(row))
    current_patient = _patient_signature(row)
    for other in existing_rows or []:
        if str(other.get("scenario_id")) == str(row.get("scenario_id")):
            continue
        other_text = _normalise_text(_scenario_text(other))
        if not current_text or not other_text:
            continue
        similarity = SequenceMatcher(None, current_text, other_text).ratio()
        if similarity >= 0.72:
            other_patient = _patient_signature(other)
            duplicates.append({"scenario_id": other.get("scenario_id"), "similarity": round(similarity, 3),
                               "different_patient_facts": bool(current_patient and other_patient and current_patient != other_patient)})
    duplicates.sort(key=lambda item: (float(item["similarity"]) >= 0.9 and not item["different_patient_facts"], float(item["similarity"])), reverse=True)
    duplicate = duplicates[0] if duplicates else None
    if duplicate:
        severity = "error" if float(duplicate["similarity"]) >= 0.9 and not duplicate["different_patient_facts"] else "warning"
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


def validate_scenario_review(value: Any) -> None:
    if not isinstance(value, dict) or value.get("status") not in {"pass", "repair"}:
        raise ValueError("Review must explicitly return status pass or repair")
    issues = value.get("issues")
    if not isinstance(issues, list):
        raise ValueError("Review must include an issues array")
    for issue in issues:
        if (not isinstance(issue, dict) or issue.get("severity") not in {"error", "warning"}
                or not str(issue.get("code") or "").strip() or not str(issue.get("detail") or "").strip()):
            raise ValueError("Each review issue needs code, severity and detail")
    if value["status"] == "pass" and any(issue["severity"] == "error" for issue in issues):
        raise ValueError("A review containing unresolved errors cannot pass")
    if value["status"] == "repair" and not issues:
        raise ValueError("A repair request must identify at least one issue")


def _validate_package(value: Any) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("scenario_brief"), str) or not 8 <= len(value["scenario_brief"].strip()) <= 1200:
        raise ValueError("Scenario package needs a public brief of 8–1200 characters")
    for name in ("learner_role_card", "visible_profile", "hidden_profile", "continuity_seed", "initial_session_state"):
        if name in value and not isinstance(value[name], dict):
            raise ValueError(f"{name} must be an object")
    if "scenario_tags" in value and (not isinstance(value["scenario_tags"], list) or any(not isinstance(v, str) for v in value["scenario_tags"])):
        raise ValueError("scenario_tags must contain strings")
    for name, allowed in (("risk_band", {"low", "elevated", "high"}), ("difficulty_band", {"low", "medium", "high"})):
        if name in value and value[name] not in allowed:
            raise ValueError(f"Invalid {name}")
    budget = value.get("recommended_turn_budget", 4)
    if type(budget) is not int or not 2 <= budget <= 12:
        raise ValueError("recommended_turn_budget must be an integer from 2 to 12")


def effective_scenario_package(patient: PatientDossier, package: dict, request: ScenarioGenerationRequest) -> dict:
    """Use the same compilation as saving, so the reviewer sees the final facts."""
    package = {**package, "scenario_family": request.scenario_family,
               "difficulty_band": request.difficulty_band, "risk_band": request.risk_band,
               "case_family": package.get("case_family") or ("Synthetic patient" if request.language == "en" else "合成患者"),
               "scenario_tags": _clean_list(package.get("scenario_tags"), limit=12) or _clean_list(request.scenario_tags, limit=12) or [request.scenario_family]}
    row = {**request.model_dump(mode="json"), **package, "case_id": "review-candidate", "revision": 1,
           "scenario_brief": package.get("scenario_brief") or request.brief}
    bundle = compile_patient({"runtime_state_seed": {"initial_session_state": package.get("initial_session_state") or {}}}, row, patient)
    return {**package, **bundle["authoritative_layer"],
            "learner_role_card": public_role_card(bundle, row),
            "initial_session_state": bundle["runtime_state_seed"]["initial_session_state"]}


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
        self.client = OpenAICompatibleClient(config)
        self.prompts = {
            "zh": {
                "plan": load_prompt(root / "prompts/Simulation/scenario_plan_zh.md"),
                "patient": load_prompt(root / "prompts/Simulation/patient_dossier_zh.md"),
                "package": load_prompt(root / "prompts/Simulation/scenario_package_zh.md"),
                "review": load_prompt(root / "prompts/Simulation/scenario_review_zh.md"),
                "repair": load_prompt(root / "prompts/Simulation/scenario_repair_zh.md"),
            },
            "en": {
                "plan": load_prompt(root / "prompts/Simulation/scenario_plan_en.md"),
                "patient": load_prompt(root / "prompts/Simulation/patient_dossier_en.md"),
                "package": load_prompt(root / "prompts/Simulation/scenario_package_en.md"),
                "review": load_prompt(root / "prompts/Simulation/scenario_review_en.md"),
                "repair": load_prompt(root / "prompts/Simulation/scenario_repair_en.md"),
            },
        }
        self.output_root = Path(output_root).resolve()

    def _prompts_for(self, language: str) -> dict[str, str]:
        """Return stage prompts, while accepting the old test/embedding shape."""
        selected = self.prompts.get(str(language).lower(), self.prompts["zh"])
        if isinstance(selected, str):
            return {stage: selected for stage in ("plan", "patient", "package", "review", "repair")}
        return selected

    def _prompt_for(self, language: str) -> str:
        """Compatibility accessor for callers that only need a final prompt."""
        return self._prompts_for(language)["package"]

    def _knowledge_context(self, request: ScenarioGenerationRequest) -> dict[str, Any]:
        if not request.use_knowledge:
            return {"status": "disabled", "items": []}
        from plugins.theraskill.retrieval import search_managed_knowledge
        from theraskill_agent.text import redact_secrets
        try:
            found = search_managed_knowledge(request.brief, skill="synthetic-dialogue-builder", limit=4)
            if found is None:
                return {"status": "unavailable", "reason": "No active knowledge index", "items": []}
            matches = list(found.get("matches") or [])
            warnings = list(found.get("warnings") or [])
            queries = [request.brief]
            if not matches:
                try:
                    from domains.theraskill.theraskill_llm import chat_json_with_repair
                    planned = chat_json_with_repair(self.client,
                        "Extract up to three short topical search phrases from the supplied scenario brief. "
                        "Each phrase must be an exact contiguous substring of the brief, 2 to 32 characters, "
                        "and useful for finding patient-simulation or therapy-process knowledge. "
                        "Return JSON {queries:[...]}; do not add facts or instructions.",
                        {"brief": request.brief}, parse_retries=1, max_prompt_tokens=2000, force_json_first=True)
                    candidates = planned.get("queries") if isinstance(planned, dict) else []
                    if not isinstance(candidates, list):
                        candidates = []
                    for candidate in candidates:
                        if not isinstance(candidate, str):
                            continue
                        phrase = candidate.strip()
                        if 2 <= len(phrase) <= 32 and phrase in request.brief and phrase not in queries:
                            queries.append(phrase)
                        if len(queries) >= 4:
                            break
                    seen = set()
                    for phrase in queries[1:]:
                        supplement = search_managed_knowledge(phrase, skill="synthetic-dialogue-builder", limit=2)
                        if supplement is None:
                            break
                        warnings.extend(supplement.get("warnings") or [])
                        for hit in supplement.get("matches") or []:
                            key = hit.get("retrieval_id") or hit.get("unit_id")
                            if key and key not in seen:
                                matches.append(hit)
                                seen.add(key)
                        if len(matches) >= 4:
                            break
                except Exception as exc:
                    warnings.append("Topic search unavailable: " + redact_secrets(str(exc))[:200])
            items = []
            for hit in matches[:4]:
                retrieval_id = hit.get("retrieval_id") or hit.get("unit_id")
                if not retrieval_id:
                    continue
                source = {key: hit[key] for key in (
                    "release_unit_id", "canonical_label", "source_index_id", "knowledge_sha256",
                    "dedup_family", "primary_dataset", "primary_evidence_tier", "primary_language",
                    "allowed_use", "forbidden_use") if hit.get(key) is not None}
                source["text_preview"] = str(hit.get("retrieval_text") or hit.get("text_preview") or "")[:1800]
                items.append({"retrieval_id": retrieval_id, "content": json.dumps(source, ensure_ascii=False)})
            return {"status": "grounded" if items else "no_matches", "index_id": found.get("index_id"),
                    "items": items, "queries": queries, "warnings": list(dict.fromkeys(warnings))}
        except Exception as exc:
            return {"status": "unavailable", "reason": redact_secrets(str(exc))[:500], "items": []}

    def generate(self, request: ScenarioGenerationRequest, *, workflow_id: str | None = None, progress=None) -> dict[str, Any]:
        """Resume one durable design job; a fresh invocation creates a new case."""
        prompts = self._prompts_for(request.language)
        binding = {"version": 3, "request": request.model_dump(mode="json"), "prompts": prompts,
                   "model": model_signature(self.client), "implementation": implementation_signature()}
        with generation_checkpoint(self.output_root, workflow_id or f"patient_{uuid.uuid4().hex}", binding, progress) as checkpoint:
            if checkpoint.state.get("result"):
                result = checkpoint.state["result"]
                row = result["scenario"]
                if not Path(row["case_bundle_path"]).is_file():
                    raise ValueError("Saved generation bundle is missing; restore it before retrying")
                import hashlib
                if hashlib.sha256(Path(row["case_bundle_path"]).read_bytes()).hexdigest() != row["bundle_sha256"]:
                    raise ValueError("Saved generation bundle changed; restore it before retrying")
                return result
            result = self._generate(request, checkpoint, prompts)
            checkpoint.state["result"] = result
            checkpoint.save()
            return result

    def _generate(self, request, checkpoint, prompts):
        from domains.theraskill.theraskill_llm import chat_json_with_repair
        from theraskill_agent.text import redact_secrets

        knowledge = checkpoint.run("knowledge", request.model_dump(mode="json"), lambda: self._knowledge_context(request))
        base_payload = {**request.model_dump(mode="json"), "knowledge_context": knowledge}
        supplied = ({key: value for key, value in request.patient.model_dump(mode="json").items() if value}
                    if request.patient is not None else {})

        def pinned_patient(value):
            patient = PatientDossier.model_validate({**value, **supplied})
            if not patient.presenting_concern:
                raise ValueError("Patient dossier requires a presenting concern")
            return patient

        def call_stage(stage, payload, *, validate=None, prompt_stage=None):
            prompt = prompts[prompt_stage or stage]
            def operation():
                return chat_json_with_repair(self.client, prompt, payload, parse_retries=1,
                    max_prompt_tokens=12000, force_json_first=True, validate=validate)
            return checkpoint.run(stage, {"prompt": prompt, "payload": payload}, operation, validate)

        def validate_plan(value):
            if not isinstance(value, dict) or not isinstance(value.get("training_goal"), str) or not value["training_goal"].strip():
                raise ValueError("Design plan requires a training_goal")

        def validate_patient(value):
            if not isinstance(value, dict) or not isinstance(value.get("patient"), dict):
                raise ValueError("Patient stage must return a patient object")
            pinned_patient(value["patient"])

        plan = call_stage("plan", {**base_payload, "workflow_stage": "plan"}, validate=validate_plan)
        patient_result = call_stage("patient", {**base_payload, "workflow_stage": "patient", "design_plan": plan}, validate=validate_patient)
        patient = pinned_patient(patient_result["patient"])
        package = call_stage("package", {**base_payload, "workflow_stage": "package", "design_plan": plan,
                                         "patient_dossier": patient.model_dump(mode="json")}, validate=_validate_package)
        package = effective_scenario_package(patient, package, request)
        history = []

        def review_candidate(stage):
            try:
                reviewed = call_stage(stage, {**base_payload, "workflow_stage": stage, "design_plan": plan,
                    "patient_dossier": patient.model_dump(mode="json"), "scenario_package": package},
                    validate=validate_scenario_review, prompt_stage="review")
            except Exception as exc:
                reviewed = {"status": "unavailable", "issues": [{"code": "review_unavailable", "severity": "error",
                             "detail": redact_secrets(str(exc))[:500]}]}
            history.append(dict(reviewed))
            return reviewed

        review = review_candidate("review")
        repair_requested = review["status"] == "repair"
        if repair_requested:
            before = fingerprint({"patient": patient.model_dump(mode="json"), "package": package})
            def validate_repair(value):
                if not isinstance(value, dict) or not value or set(value) - (set(package) | {"patient"}):
                    raise ValueError("Repair must change known patient or scenario fields")
                revised_patient = pinned_patient(value.get("patient", patient.model_dump(mode="json")))
                revised_package = {**package, **{key: v for key, v in value.items() if key != "patient"}}
                _validate_package(revised_package)
                revised_package = effective_scenario_package(revised_patient, revised_package, request)
                if before == fingerprint({"patient": revised_patient.model_dump(mode="json"), "package": revised_package}):
                    raise ValueError("Repair made no effective changes to the reviewed candidate")
            try:
                repaired = call_stage("repair", {**base_payload, "workflow_stage": "repair", "design_plan": plan,
                    "patient_dossier": patient.model_dump(mode="json"), "scenario_package": package, "review": review},
                    validate=validate_repair)
                patient = pinned_patient(repaired.get("patient", patient.model_dump(mode="json")))
                package = effective_scenario_package(patient, {**package, **{k: v for k, v in repaired.items() if k != "patient"}}, request)
                review = {**review_candidate("re_review"), "repaired": True}
                if review["status"] == "pass":
                    review["status"] = "pass_after_repair"
            except Exception as exc:
                review = {**review, "repair_status": "failed", "repair_error": redact_secrets(str(exc))[:500]}
        review["history"] = history
        patient_result = {"patient": patient.model_dump(mode="json")}
        trace = checkpoint.trace
        result = {**package, "patient": patient_result.get("patient")}
        scenario_id = f"generated_{checkpoint.state['ids']['scenario']}"
        case_id = f"generated_case_{checkpoint.state['ids']['case']}"
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
            "generated_at": checkpoint.state["created_at"],
            "revision": 1,
            "grounding_status": knowledge["status"],
            "generation_workflow": {
                "mode": "staged_agentic_generation",
                "stages": trace,
                "review_status": review["status"],
                "repair_requested": repair_requested,
                "issues": review.get("issues", []),
                "candidate_sha256": fingerprint({"patient": patient.model_dump(mode="json"), "package": package}),
            },
        }
        bundle = {
            "schema_version": "theraskill_generated_case_bundle_v1",
            "bundle_id": f"generated_bundle_{checkpoint.state['ids']['bundle']}",
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
                "source_unit_ids": {str(i): item["retrieval_id"] for i, item in enumerate(knowledge["items"])},
                "visible_profile": visible,
                "hidden_profile": hidden,
                "continuity_seed": continuity,
                "report_dialogue_bridge": {},
                "patient_formulation": {},
            },
            "runtime_priors": {},
            "runtime_state_seed": {"initial_session_state": initial_state},
            "provenance": {
                "generated_by": "theraskill_online_scenario_agentic_v3",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "request": request.model_dump(mode="json"),
                "knowledge_grounding": knowledge,
                "generation_workflow": {
                    "mode": "staged_agentic_generation",
                    "stages": trace,
                    "plan": plan,
                    "review": review,
                },
            },
        }
        bundle = compile_patient(bundle, scenario, patient)
        scenario["learner_role_card"] = public_role_card(bundle, scenario)
        scenario = save_bundle_version(bundle, scenario, self.output_root)
        return {"scenario": scenario, "case_bundle": bundle}

    def generate_batch(self, request: ScenarioGenerationBatchRequest, *, workflow_id: str | None = None) -> list[dict[str, Any]]:
        from .course_generation import plan_course, course_requests, review_course
        run_id = workflow_id or f"course_{uuid.uuid4().hex}"
        with generation_checkpoint(self.output_root, run_id,
                {"version": 1, "request": request.model_dump(mode="json"), "model": model_signature(self.client), "implementation": implementation_signature()}) as checkpoint:
            plan = plan_course(self.client, request, checkpoint=checkpoint)
            generated = [self.generate(case, workflow_id=f"{run_id}_case_{index}")
                         for index, case in enumerate(course_requests(request, plan), start=1)]
            report = review_course(self.client, request, plan,
                [{"index": index, "scenario": result["scenario"]} for index, result in enumerate(generated, start=1)],
                checkpoint=checkpoint)
            checkpoint.state["course_review"] = report
            checkpoint.save()
            return [{**result, "course_plan": plan, "course_review": report} for result in generated]
