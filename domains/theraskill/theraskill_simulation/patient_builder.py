"""Reusable synthetic patient dossiers and deterministic Persona v2 compilation.

Life events are case facts, not conversation memories. Each event has its own
disclosure level; a recent event is not automatically public to the learner.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator


class PatientFact(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    content: str = Field(min_length=1, max_length=2000)
    sensitivity: Literal["surface", "moderate", "sensitive"] = "moderate"


class PatientLifeEvent(PatientFact):
    event_id: str = Field(default_factory=lambda: f"event_{uuid.uuid4().hex}", min_length=1, max_length=100)
    time_label: str = Field(default="", max_length=100)
    impact: str = Field(default="", max_length=1000)


class PatientDossier(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    display_name: str = Field(default="", max_length=100)
    age: str = Field(default="", max_length=80)
    gender: str = Field(default="", max_length=80)
    occupation: str = Field(default="", max_length=200)
    relationship_status: str = Field(default="", max_length=200)
    presenting_concern: str = Field(default="", max_length=2000)
    background: list[PatientFact] = Field(default_factory=list, max_length=20)
    recent_events: list[PatientLifeEvent] = Field(default_factory=list, max_length=20)
    hidden_concerns: list[PatientFact] = Field(default_factory=list, max_length=20)
    current_emotions: list[str] = Field(default_factory=list, max_length=12)
    goals: list[str] = Field(default_factory=list, max_length=12)
    training_objectives: list[str] = Field(default_factory=list, max_length=12)
    communication_style: str = Field(default="", max_length=1000)
    disclosure_preferences: str = Field(default="", max_length=1000)

    @field_validator("age", mode="before")
    @classmethod
    def numeric_age(cls, value):
        # Models often emit a numeric age even when the editor supports bands.
        return str(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else value

    @model_validator(mode="after")
    def unique_events(self):
        ids = [event.event_id for event in self.recent_events]
        if len(ids) != len(set(ids)):
            raise ValueError("Each life event must have a unique event_id")
        if any(fact.sensitivity == "surface" for fact in self.hidden_concerns):
            raise ValueError("Hidden concerns must have moderate or sensitive disclosure")
        if any(len(value) > 1000 for values in (self.current_emotions, self.goals, self.training_objectives) for value in values):
            raise ValueError("Patient list entries must be at most 1000 characters")
        return self


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(v.get("content") or "") if isinstance(v, dict) else str(v) for v in value if v]


def dossier_from_bundle(bundle: dict[str, Any], row: dict[str, Any]) -> PatientDossier:
    if bundle.get("patient_dossier"):
        return PatientDossier.model_validate(bundle["patient_dossier"])
    layer = bundle.get("authoritative_layer") or {}
    visible, hidden = layer.get("visible_profile") or {}, layer.get("hidden_profile") or {}
    continuity = layer.get("continuity_seed") or {}
    card = row.get("learner_role_card") or {}
    persona = bundle.get("_patient_persona_v2") or {}
    from .persona_compiler import effective_disclosure_nodes
    nodes = effective_disclosure_nodes(persona)
    identity = persona.get("immutable_identity") or {}
    demo = identity.get("demographic_profile") or visible.get("demographic_profile") or {}
    public = _strings(visible.get("presenting_problem_profile")) or _strings(hidden.get("surface_presenting_story"))
    background = [PatientFact(content=s[:2000], sensitivity="surface") for s in _strings(continuity.get("stable_identity_facts"))[:20] if s.strip()]
    concerns = [PatientFact(content=s[:2000], sensitivity="sensitive") for s in (
        _strings(hidden.get("latent_core_conflicts")) + _strings(hidden.get("withheld_or_unspoken_concerns"))
    )[:20] if s.strip()]
    if nodes:
        surface = {str(n.get("content")) for n in nodes if n.get("sensitivity") == "surface"}
        presenting = _strings((persona.get("psychological_model") or {}).get("presenting_problems"))
        public = [s for s in presenting if s in surface]
        background = [PatientFact(content=n["content"][:2000], sensitivity="surface") for n in nodes if n.get("content") and n.get("sensitivity") == "surface"][:20]
        concerns = [PatientFact(content=n["content"][:2000], sensitivity=n["sensitivity"]) for n in nodes if n.get("content") and n.get("sensitivity") in {"moderate", "sensitive"}][:20]
    communication = persona.get("communication_profile") or {}
    return PatientDossier(
        display_name=str(identity.get("display_name") or card.get("display_name") or ""),
        age=str(demo.get("age") or card.get("age_band") or ""),
        gender=str(demo.get("gender") or ""), occupation=str(demo.get("occupation") or card.get("occupation") or ""),
        relationship_status=str(demo.get("marital_status") or card.get("marital_status") or ""),
        presenting_concern="; ".join(public)[:2000] or str(row.get("scenario_brief") or ""),
        background=background, hidden_concerns=concerns,
        current_emotions=_strings(visible.get("emotional_profile"))[:12],
        training_objectives=(_strings(card.get("training_objectives")) or _strings((persona.get("training_contract") or {}).get("learning_objectives")))[:12],
        communication_style="; ".join(str(communication[k]) for k in ("response_length", "narrative_organization", "initiative_level") if communication.get(k))[:1000],
    )


def compile_patient(bundle: dict[str, Any], row: dict[str, Any], patient: PatientDossier) -> dict[str, Any]:
    """Embed the complete editor model and a bounded runtime persona in a bundle."""
    result = deepcopy(bundle)
    language = str(row.get("language") or "zh")
    english = language == "en"
    if not patient.presenting_concern.strip():
        raise ValueError("A presenting concern is required to build a patient")
    nodes: list[dict[str, Any]] = []

    def add(content: str, sensitivity: str, *, event: PatientLifeEvent | None = None):
        if not content.strip():
            return
        fact_id = "fact_" + hashlib.sha256(content.encode()).hexdigest()[:20]
        existing = next((n for n in nodes if n["fact_id"] == fact_id), None)
        order = {"surface": 0, "moderate": 1, "sensitive": 2}
        if existing:
            if order[existing["sensitivity"]] >= order[sensitivity]:
                return
            nodes.remove(existing)
        nodes.append({
            "fact_id": fact_id, "content": content, "sensitivity": sensitivity,
            "allowed_state_bands": {"surface": ["surface", "moderate", "deep"], "moderate": ["moderate", "deep"], "sensitive": ["deep"]}[sensitivity],
            "default_response_mode": {"surface": "answer", "moderate": "hint", "sensitive": "withhold"}[sensitivity],
            "facilitating_conditions": ["supportive validation", "non-judgmental exploration"],
            "blocking_conditions": ["pressure", "premature interpretation"],
            **({"event_id": event.event_id, "time_label": event.time_label} if event else {}),
        })

    add(patient.presenting_concern, "surface")
    for fact in patient.background:
        add(fact.content, fact.sensitivity)
    for event in patient.recent_events:
        # Keep timing and consequences attached to the fact, including after
        # context compilation; do not pass the full private timeline separately.
        content = " | ".join(v for v in (event.time_label, event.content, event.impact) if v)
        add(content, event.sensitivity, event=event)
    for fact in patient.hidden_concerns:
        add(fact.content, "sensitive" if fact.sensitivity == "surface" else fact.sensitivity)
    for goal in patient.goals:
        add(("Current goal: " if english else "当前希望：") + goal, "moderate")

    revision = int(row.get("revision") or 1)
    initial = dict((result.get("runtime_state_seed") or {}).get("initial_session_state") or {})
    initial.update(emotion_state=patient.current_emotions, disclosure_state="surface", risk_state=row.get("risk_band") or "low")
    initial.setdefault("alliance_state", "forming")
    initial.setdefault("readiness_state", "ready_for_guided_exploration")
    public = [n["content"] for n in nodes if n["sensitivity"] == "surface"]
    hidden = [n["content"] for n in nodes if n["sensitivity"] != "surface"]
    persona = {
        "schema_version": "theraskill_patient_persona_v2", "language": language,
        "case_id": row["case_id"], "persona_id": f"persona::{row['case_id']}::r{revision}",
        "immutable_identity": {
            "display_name": patient.display_name or ("Simulated patient" if english else "模拟来访者"),
            "demographic_profile": {"age": patient.age, "gender": patient.gender, "occupation": patient.occupation, "marital_status": patient.relationship_status},
            "facts": [{"fact_id": n["fact_id"], "content": n["content"]} for n in nodes],
        },
        "communication_profile": {"language": language, "response_length": "1-3 sentences",
            "vocabulary_and_abstraction": "everyday language", "narrative_organization": "follow the current topic",
            "initiative_level": patient.communication_style or "respond naturally without volunteering withheld facts"},
        "psychological_model": {"presenting_problems": [{"content": patient.presenting_concern}], "risk_watchpoints": []},
        "initial_state": initial,
        "disclosure_graph": {"nodes": nodes},
        "training_contract": {"learning_objectives": patient.training_objectives},
        "behavioral_constitution": {"principles": [
            {"principle_id": "identity", "category": "identity_consistency", "when": "always", "then": "Keep identity, event timing and consequences consistent; do not invent new life events as established facts."},
            {"principle_id": "disclosure", "category": "disclosure_behavior", "when": "exploring concerns", "then": "Disclose only facts provided in the allowed context; trust develops gradually."},
            {"principle_id": "boundaries", "category": "risk_expression_boundary", "when": "always", "then": "Do not introduce unsupported acute risk, diagnoses, medication or treatment history."},
            {"principle_id": "preferences", "category": "resistance_and_readiness", "when": "responding to exploration", "then": patient.disclosure_preferences or "Allow uncertainty and hesitation; avoid automatic agreement or rapid recovery."},
        ]},
    }
    result.update(patient_dossier=patient.model_dump(mode="json"), _patient_persona_v2=persona)
    result.setdefault("metadata", {}).update({key: row.get(key) for key in ("case_family", "difficulty_band", "risk_band", "scenario_tags", "language", "revision")})
    layer = result.setdefault("authoritative_layer", {})
    layer["visible_profile"] = {"presenting_problem_profile": [patient.presenting_concern], "emotional_profile": patient.current_emotions, "public_case_facts": public}
    layer["hidden_profile"] = {"withheld_or_unspoken_concerns": hidden}
    layer["continuity_seed"] = {"stable_identity_facts": [f.content for f in patient.background], "unfinished_threads": patient.goals}
    result.setdefault("runtime_state_seed", {})["initial_session_state"] = initial
    return result


def public_role_card(bundle: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    from .persona_compiler import PersonaCompiler
    context = PersonaCompiler().compile_for_move(bundle["_patient_persona_v2"]).runtime_context
    identity = context["immutable_identity"]
    demo = identity["demographic_profile"]
    return {"display_name": identity["display_name"], "age_band": demo.get("age"), "gender": demo.get("gender"),
            "occupation": demo.get("occupation"), "marital_status": demo.get("marital_status"),
            "surface_presenting_problems": context["public_presenting_problems"],
            "current_context": row.get("scenario_brief"),
            "training_objectives": bundle["patient_dossier"]["training_objectives"]}


def save_bundle_version(bundle: dict[str, Any], row: dict[str, Any], output_root: Path) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    version = f"{row['case_id']}.r{row.get('revision', 1)}.{uuid.uuid4().hex}.json"
    path = output_root / version
    data = (json.dumps(bundle, ensure_ascii=False, indent=2) + "\n").encode()
    # Unique, immutable file: a registry update publishes its reference.
    with path.open("xb") as handle:
        handle.write(data)
    return {**row, "case_bundle_path": str(path.resolve()), "bundle_sha256": hashlib.sha256(data).hexdigest()}


def build_patient_scenario(request: Any, output_root: Path, *, bundle: dict[str, Any] | None = None) -> dict[str, Any]:
    patient = request.patient
    now = datetime.now(timezone.utc).isoformat()
    row = {"scenario_id": f"generated_{uuid.uuid4().hex}", "case_id": f"generated_case_{uuid.uuid4().hex}",
           "revision": 1, "scenario_family": request.scenario_family, "case_family": "Synthetic patient" if request.language == "en" else "合成患者",
           "scenario_brief": request.brief, "difficulty_band": request.difficulty_band, "risk_band": request.risk_band,
           "language": request.language, "scenario_tags": request.scenario_tags or [request.scenario_family],
           "recommended_turn_budget": 6, "split_labels": {"formal_split": "generated"}, "generated": True,
           "synthetic": True, "review_status": "draft", "review_history": [], "generated_at": now}
    base = deepcopy(bundle or {})
    base.update(schema_version="theraskill_generated_case_bundle_v2", case_id=row["case_id"], bundle_id=f"bundle_{uuid.uuid4().hex}",
                metadata={"synthetic": True, "language": request.language})
    base.setdefault("provenance", {}).update(generated_by="patient-builder", generated_at=now, synthetic=True)
    row["grounding_status"] = (base["provenance"].get("knowledge_grounding") or {}).get("status", "authored")
    base = compile_patient(base, row, patient)
    row["learner_role_card"] = public_role_card(base, row)
    return {"scenario": save_bundle_version(base, row, output_root), "case_bundle": base}
