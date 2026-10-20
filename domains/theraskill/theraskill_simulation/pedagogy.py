"""Psychotherapy training design primitives.

The course UI used to make the instructor type a list of objectives and then
treated a 1--5 review score as the whole learning signal.  This module keeps a
small, deterministic teaching model separate from persistence and the LLM:

* courses start from a teaching template and observable competencies;
* knowledge references are attached to the design and to the feedback rubric;
* progress is a practice signal with repeated, cross-case evidence, rather
  than a claim of clinical competence.

The functions are intentionally pure so that the API can use them when a
teacher opens a course draft, and tests can exercise them without a model or a
knowledge index.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field


CompetencyLevel = Literal["orientation", "guided", "independent", "transfer"]
CourseStage = Literal["foundation", "core_skill", "integration", "supervision"]


class PedagogyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Competency(PedagogyModel):
    """A trainable, observable psychotherapy competency."""

    competency_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(min_length=8, max_length=500)
    observable_behaviors: list[str] = Field(min_length=1, max_length=8)
    knowledge_queries: list[str] = Field(default_factory=list, max_length=8)
    prerequisites: list[str] = Field(default_factory=list, max_length=8)
    safety_critical: bool = False


class CourseTemplate(PedagogyModel):
    """A starting point a teacher can adapt instead of designing from zero."""

    template_id: str = Field(min_length=2, max_length=80)
    title: str = Field(min_length=2, max_length=160)
    description: str = Field(min_length=8, max_length=700)
    stage: CourseStage
    recommended_competencies: list[str] = Field(min_length=1, max_length=12)
    recommended_case_tags: list[str] = Field(default_factory=list, max_length=16)
    scaffolding: list[str] = Field(min_length=1, max_length=8)
    completion_policy: dict[str, Any] = Field(default_factory=dict)


class KnowledgeReference(PedagogyModel):
    """A checked knowledge unit used while designing or reviewing a course."""

    unit_id: str = Field(min_length=1, max_length=160)
    quote: str = Field(min_length=1, max_length=1200)
    label: str = ""
    evidence_role: str = "teaching_reference"
    allowed_use: str | list[str] = ""
    forbidden_use: str | list[str] = ""
    source_kind: str = ""
    source_index_id: str = ""
    knowledge_sha256: str = ""
    source_text_sha256: str = ""
    dedup_family: str = ""


class KnowledgeCard(PedagogyModel):
    """A short teacher-editable explanation shown before a practice."""

    card_id: str
    competency_id: str
    title: str
    why_it_matters: str
    when_to_use: str
    common_misuse: str
    knowledge_unit_ids: list[str] = Field(default_factory=list)


class MicroPractice(PedagogyModel):
    """A bounded exercise that isolates one observable behaviour."""

    practice_id: str
    competency_id: str
    title: str
    prompt: str
    scenario_text: str = ""
    example_response: str = ""
    example_discussion: str = ""
    target_behavior: str
    hint: str
    reflection_prompt: str
    knowledge_unit_ids: list[str] = Field(default_factory=list)


class CourseDesignSeed(PedagogyModel):
    """Deterministic draft shown to a teacher before an LLM builds scenarios."""

    schema_version: str = "theraskill_course_design_seed_v1"
    template_id: str
    language: str = "zh"
    title: str
    description: str
    stage: CourseStage
    target_level: CompetencyLevel
    competency_ids: list[str]
    objectives: list[dict[str, Any]]
    scenario_constraints: dict[str, Any]
    scaffolding: list[str]
    knowledge_references: list[KnowledgeReference] = Field(default_factory=list)
    knowledge_cards: list[KnowledgeCard] = Field(default_factory=list)
    micro_practices: list[MicroPractice] = Field(default_factory=list)
    next_steps: list[str]


class ProgressObservation(PedagogyModel):
    """One human or machine-supported observation of an actual practice."""

    attempt_id: str = Field(min_length=1)
    competency_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    rating: float = Field(ge=1, le=5, strict=True, allow_inf_nan=False)
    observed_at: datetime | None = None
    level: Literal["orientation", "guided", "independent"] = "guided"
    evidence_unit_ids: list[str] = Field(default_factory=list, max_length=30)
    safety_issue: bool = False
    transfer_context: str = ""
    reviewer: str = ""


DEFAULT_COMPETENCIES: tuple[Competency, ...] = (
    Competency(
        competency_id="alliance_and_validation",
        name="治疗关系与验证",
        description="在尊重来访者自主性的前提下建立合作关系，并准确反映情绪和意义。",
        observable_behaviors=["使用具体情绪反映", "检查对方是否被理解", "尊重拒绝和节奏"],
        knowledge_queries=["therapeutic alliance validation empathy", "治疗关系 情绪验证"],
    ),
    Competency(
        competency_id="assessment_and_formulation",
        name="评估与个案概念化",
        description="从时间、情境和维持因素组织信息，形成可检验的工作假设，而不是给出仓促诊断。",
        observable_behaviors=["使用开放式问题", "区分事实、推测和待核实信息", "用来访者语言检查工作假设"],
        knowledge_queries=["psychotherapy assessment formulation", "心理咨询 个案概念化 评估"],
    ),
    Competency(
        competency_id="risk_and_safety",
        name="风险识别与安全计划",
        description="识别需要升级处理的风险信号，清楚说明限制并遵循机构的转介和安全流程。",
        observable_behaviors=["直接而不评判地询问风险", "确认即时安全与支持", "在超出训练范围时转介"],
        knowledge_queries=["suicide risk assessment safety planning", "心理咨询 危机 风险评估 安全计划"],
        safety_critical=True,
    ),
    Competency(
        competency_id="intervention_fidelity",
        name="干预选择与执行",
        description="把干预选择和当前目标、来访者偏好及证据边界连接起来，并检查回应后的效果。",
        observable_behaviors=["说明建议的目的和选择", "先征得同意再练习", "根据反馈调整而不是机械套用技术"],
        knowledge_queries=["evidence based psychotherapy intervention fidelity", "心理治疗 干预 共同决策"],
    ),
    Competency(
        competency_id="culture_boundaries_closure",
        name="文化谦逊、边界与结束",
        description="识别文化和权力差异，保持角色边界，并以清楚、合作的方式结束或转介。",
        observable_behaviors=["邀请来访者修正自己的假设", "明确角色和保密边界", "总结进展并共同计划下一步"],
        knowledge_queries=["cultural humility psychotherapy boundaries termination", "心理咨询 文化谦逊 边界 结束"],
    ),
)


DEFAULT_TEMPLATES: tuple[CourseTemplate, ...] = (
    CourseTemplate(
        template_id="foundation_first_session",
        title="首次会谈基础",
        description="练习建立合作关系、澄清来访目标和安排首次会谈的结构。",
        stage="foundation",
        recommended_competencies=["alliance_and_validation", "assessment_and_formulation"],
        recommended_case_tags=["first_session", "low_risk", "ambiguous_goal"],
        scaffolding=["示范对话", "带提示练习", "隐藏提示练习", "独立练习"],
        completion_policy={"minimum_attempts": 3, "minimum_independent": 2, "require_transfer": True},
    ),
    CourseTemplate(
        template_id="risk_and_safety_basics",
        title="风险识别与安全沟通",
        description="在模拟案例中练习询问风险、确认安全和遵守升级处理边界。",
        stage="core_skill",
        recommended_competencies=["risk_and_safety", "alliance_and_validation"],
        recommended_case_tags=["risk_assessment", "safety_planning", "elevated_risk"],
        scaffolding=["流程卡片", "关键节点提示", "限时模拟", "独立复盘"],
        completion_policy={"minimum_attempts": 4, "minimum_independent": 2, "block_on_safety_issue": True},
    ),
    CourseTemplate(
        template_id="integrated_case_formulation",
        title="从评估到工作假设",
        description="把多来源信息组织成可检验的工作假设，并在对话中持续修正。",
        stage="integration",
        recommended_competencies=["assessment_and_formulation", "intervention_fidelity", "culture_boundaries_closure"],
        recommended_case_tags=["complex_context", "formulation", "cultural_context"],
        scaffolding=["结构化提纲", "比较两种假设", "未知信息案例", "跨案例迁移"],
        completion_policy={"minimum_attempts": 4, "minimum_independent": 2, "require_transfer": True},
    ),
)


def _lookup(items: Sequence[BaseModel], key: str, value: str) -> BaseModel | None:
    return next((item for item in items if getattr(item, key, None) == value), None)


def design_course_seed(
    *,
    template_id: str,
    title: str | None = None,
    target_level: CompetencyLevel = "guided",
    competency_ids: Sequence[str] | None = None,
    scenario_count: int = 3,
    scenario_family: str = "",
    difficulty_band: str = "low",
    risk_band: str = "low",
    knowledge_units: Sequence[Mapping[str, Any]] = (),
    language: str = "zh",
    competencies: Sequence[Competency] = DEFAULT_COMPETENCIES,
    templates: Sequence[CourseTemplate] = DEFAULT_TEMPLATES,
) -> CourseDesignSeed:
    """Build a teacher-editable course seed from a template and knowledge hits.

    Retrieval is deliberately input-only: knowledge units are selected and
    quoted here, while scenario generation and learner feedback remain separate
    stages. This prevents a retrieved passage from becoming hidden patient
    instructions or an unsupported grading rule.
    """
    if scenario_count < 1 or scenario_count > 12:
        raise ValueError("scenario_count must be between 1 and 12")
    from .pedagogy_content import localize_catalog, exercise_content
    if language not in {"zh", "en"}:
        raise ValueError("language must be zh or en")
    competencies, templates = localize_catalog(competencies, templates, language)
    template = _lookup(templates, "template_id", template_id)
    if template is None:
        raise ValueError(f"Unknown course template: {template_id}")
    chosen_ids = list(dict.fromkeys(competency_ids or template.recommended_competencies))
    known = {item.competency_id: item for item in competencies}
    unknown = [item for item in chosen_ids if item not in known]
    if unknown:
        raise ValueError(f"Unknown competency IDs: {unknown}")
    selected_refs: list[KnowledgeReference] = []
    seen_refs: set[str] = set()
    for raw in knowledge_units:
        unit_id = str(raw.get("unit_id") or raw.get("release_unit_id") or raw.get("retrieval_id") or "").strip()
        quote = str(raw.get("quote") or raw.get("retrieval_text") or raw.get("text") or raw.get("text_preview") or "").strip()
        if not unit_id or not quote or unit_id in seen_refs or len(selected_refs) >= 12:
            continue
        seen_refs.add(unit_id)
        selected_refs.append(KnowledgeReference(
            unit_id=unit_id,
            quote=quote[:1200],
            label=str(raw.get("label") or raw.get("canonical_label") or "").strip(),
            evidence_role=str(raw.get("evidence_role") or "teaching_reference"),
            allowed_use=raw.get("allowed_use") or "",
            forbidden_use=raw.get("forbidden_use") or "",
            source_index_id=str(raw.get("source_index_id") or ""),
            knowledge_sha256=str(raw.get("knowledge_sha256") or ""),
            source_text_sha256=str(raw.get("text_sha256") or ""),
            dedup_family=str(raw.get("dedup_family") or ""),
            source_kind=str(raw.get("source_kind") or "").strip(),
        ))
    objectives = [{
        "id": item.competency_id,
        "description": item.description,
        "observable_behaviors": item.observable_behaviors,
        "knowledge_queries": item.knowledge_queries,
        "safety_critical": item.safety_critical,
        "observation_statuses": ["observed", "not_observed", "not_applicable"],
        "rubric_version": "theraskill_formative_v1",
        "rubric": [
            {"level": "not_observed", "label": "Not observed" if language == "en" else "未观察",
             "description": "Insufficient opportunity or evidence; leave the score empty." if language == "en" else "机会或证据不足，分数留空。"},
            *[{"level": str(score), "score": score, "label": label, "description": description}
              for score, label, description in (
                  [(1, "Needs revision", "The observed response misses the target or contradicts the client's stated meaning."),
                   (2, "Emerging", "A relevant attempt is present but a substantial mismatch needs correction."),
                   (3, "Developing", "The core behavior is present with a specific omission or mismatch."),
                   (4, "Effective here", "The response fits the expressed context and gives the client room to respond."),
                   (5, "Well adapted here", "The response precisely fits the available cues and adapts when feedback is available.")]
                  if language == "en" else
                  [(1, "需要修改", "已观察到的回应偏离目标，或与来访者表达的意义矛盾。"),
                   (2, "初步尝试", "出现相关尝试，但存在需要纠正的明显不匹配。"),
                   (3, "发展中", "已表现核心行为，仍有具体遗漏或不匹配。"),
                   (4, "本次表现合适", "回应契合已表达的情境，并给来访者回应空间。"),
                   (5, "本次适应良好", "回应准确贴合现有线索；有反馈时能据此调整。")]
              )],
        ],
    } for item in (known[key] for key in chosen_ids)]
    cards: list[KnowledgeCard] = []
    practices: list[MicroPractice] = []
    for item in (known[key] for key in chosen_ids):
        content = exercise_content(item.competency_id, language)
        # These are authored teaching starters. Retrieved candidates stay at course
        # level until a teacher explicitly connects a relevant unit to a card.
        cards.append(KnowledgeCard(
            card_id=f"card_{item.competency_id}", competency_id=item.competency_id,
            title=item.name, why_it_matters=item.description,
            when_to_use=content["when"], common_misuse=content["misuse"],
        ))
        for index, exercise in enumerate(content["exercises"]):
            practices.append(MicroPractice(
                practice_id=f"micro_{item.competency_id}" + (f"_{index+1}" if index else ""),
                competency_id=item.competency_id, target_behavior=item.observable_behaviors[index % len(item.observable_behaviors)],
                **exercise,
            ))
    scenario_constraints = {
        "count": scenario_count,
        "scenario_family": scenario_family,
        "difficulty_band": difficulty_band,
        "risk_band": risk_band,
        "scenario_tags": list(template.recommended_case_tags),
        "knowledge_use": "Use references for course rationale and review rubric; do not expose instructor notes as patient facts.",
    }
    return CourseDesignSeed(
        template_id=template.template_id,
        language=language,
        title=(title or template.title).strip(),
        description=template.description,
        stage=template.stage,
        target_level=target_level,
        competency_ids=chosen_ids,
        objectives=objectives,
        scenario_constraints=scenario_constraints,
        scaffolding=list(template.scaffolding),
        knowledge_references=selected_refs,
        knowledge_cards=cards,
        micro_practices=practices,
        next_steps=["Confirm the objectives and case constraints", "Select relevant knowledge references",
                    "Generate and review cases before practice", "Observe transfer in different cases"] if language == "en" else [
            "教师确认或修改能力目标与案例约束",
            "为每个目标选择已审核知识依据",
            "生成并审核案例草稿，再开始练习",
            "用至少两次不同案例观察迁移表现",
        ],
    )


def summarize_progress(
    observations: Sequence[ProgressObservation | Mapping[str, Any]],
    competency_ids: Sequence[str],
    *,
    completion_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarize repeated practice without calling it clinical competence.

    Independent/transfer status uses configurable teaching rules, not a
    validated competency scale. Callers supply observations in chronological
    order when timestamps are unavailable. A later review replaces an earlier
    review of the same attempt; it never counts as a second practice.
    """
    policy = dict(completion_policy or {})
    minimum_attempts = max(1, int(policy.get("minimum_attempts", 2)))
    minimum_independent = max(1, int(policy.get("minimum_independent", 2)))
    require_transfer = bool(policy.get("require_transfer", False))
    block_on_safety = bool(policy.get("block_on_safety_issue", True))
    minimum_rating = float(policy.get("minimum_independent_rating", 4))
    if not 1 <= minimum_rating <= 5:
        raise ValueError("minimum_independent_rating must be between 1 and 5")
    competency_ids = list(dict.fromkeys(competency_ids))
    normalized: list[dict[str, Any]] = []
    for raw in observations:
        item = ProgressObservation.model_validate(raw).model_dump()
        if str(item.get("competency_id") or "") not in set(competency_ids):
            continue
        normalized.append(item)
    if normalized and all(row["observed_at"] is not None for row in normalized):
        normalized.sort(key=lambda row: row["observed_at"].replace(tzinfo=timezone.utc)
                        if row["observed_at"].tzinfo is None else row["observed_at"])
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for item in normalized:
        key = (item["competency_id"], item["attempt_id"])
        previous = latest.pop(key, None)
        if previous and previous["scenario_id"] != item["scenario_id"]:
            raise ValueError("An attempt cannot refer to different scenarios")
        latest[key] = item
    normalized = list(latest.values())
    by_competency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in normalized:
        by_competency[str(item["competency_id"])].append(item)
    details: list[dict[str, Any]] = []
    blocked = False
    for competency_id in competency_ids:
        rows = by_competency.get(competency_id, [])
        ratings = [float(row["rating"]) for row in rows if isinstance(row.get("rating"), (int, float))]
        independent = [row for row in rows if row.get("level") == "independent" and row["rating"] >= minimum_rating]
        transfer_cases = {str(row.get("scenario_id")) for row in independent if str(row.get("transfer_context") or "").strip()}
        safety_issues = [row for row in rows if bool(row.get("safety_issue"))]
        is_blocked = block_on_safety and bool(safety_issues)
        blocked = blocked or is_blocked
        if is_blocked:
            status = "blocked_safety"
        elif len(rows) >= minimum_attempts and len(independent) >= minimum_independent and (not require_transfer or len(transfer_cases) >= 2):
            status = "transfer" if require_transfer and len(transfer_cases) >= 2 else "independent"
        elif len(rows) >= minimum_attempts:
            status = "guided"
        elif rows:
            status = "orientation"
        else:
            status = "not_started"
        details.append({
            "competency_id": competency_id,
            "status": status,
            "attempt_count": len(rows),
            "independent_count": len(independent),
            "transfer_case_count": len(transfer_cases),
            "first_rating": ratings[0] if ratings else None,
            "latest_rating": ratings[-1] if ratings else None,
            "recent_average": round(sum(ratings[-3:]) / min(3, len(ratings[-3:])), 2) if ratings else None,
            "safety_issue_count": len(safety_issues),
            "evidence_unit_count": len({unit for row in rows for unit in row.get("evidence_unit_ids", [])}),
        })
    completed = bool(details) and all(row["status"] in {"independent", "transfer"} for row in details) and not blocked
    return {
        "schema_version": "theraskill_learning_progress_v2",
        "policy": {"minimum_attempts": minimum_attempts, "minimum_independent": minimum_independent,
                   "minimum_independent_rating": minimum_rating, "require_transfer": require_transfer,
                   "block_on_safety_issue": block_on_safety},
        "status": "blocked_safety" if blocked else "ready_for_transfer" if completed else "in_progress",
        "completed_competencies": sum(row["status"] in {"independent", "transfer"} for row in details),
        "required_competencies": len(competency_ids),
        "competencies": details,
        "signals": {
            "attempts": len({str(row.get("attempt_id")) for row in normalized}),
            "scenarios": len({str(row.get("scenario_id")) for row in normalized}),
            "knowledge_units_cited": len({unit for row in normalized for unit in row.get("evidence_unit_ids", [])}),
        },
        "limitations": [
            "次数和评分阈值是可调整的教学观察规则，尚未经效度验证。",
            "这是重复练习和教师观察的进展信号，不是临床胜任力、诊断准确性或治疗效果证明。",
            "安全风险事件需要按机构流程由合格督导处理，不能由平均分抵消。",
        ],
    }
