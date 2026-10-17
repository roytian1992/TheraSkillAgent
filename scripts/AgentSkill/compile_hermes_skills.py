#!/usr/bin/env python3
"""Compile TheraSkill Phase 6 retrieval into lightweight Hermes skill packages."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import ensure_output_dir, file_sha256 as sha256_file
from io_utils import write_json, write_text
from hermes_surface_registry import (
    KNOWLEDGE_ENGINE_RETRIEVAL_QA_PATH,
    KNOWLEDGE_ENGINE_SQLITE_QA_PATH,
    OUTPUT_ROOT,
    PHASE6_RETRIEVAL_TEXT_VIEW_PATH,
    PHASE6_REVIEWED_RELEASE_PATH,
    PHASE6_REVIEWED_RELEASE_QA_PATH,
    PHASE6_SQLITE_DB_PATH,
    PHASE6_STRUCTURED_INDEX_PATH,
)
from retrieval_wrapper_common import (
    CURRENT_RUNTIME_BRIDGE_MODE,
    RETRIEVAL_SCRIPT_FILENAMES,
    SKILL_PACKAGE_RUNTIME_SCRIPT,
    runtime_wrapper_path,
    WRAPPER_COMMON_PATH,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
DEFAULT_OUTPUT_ROOT = OUTPUT_ROOT
DEFAULT_DB = PHASE6_SQLITE_DB_PATH
DEFAULT_RELEASE = PHASE6_REVIEWED_RELEASE_PATH
DEFAULT_RELEASE_QA = PHASE6_REVIEWED_RELEASE_QA_PATH
DEFAULT_RETRIEVAL_QA = KNOWLEDGE_ENGINE_RETRIEVAL_QA_PATH
DEFAULT_SQLITE_QA = KNOWLEDGE_ENGINE_SQLITE_QA_PATH
RETRIEVAL_BRIDGE = runtime_wrapper_path(CURRENT_RUNTIME_BRIDGE_MODE)
HERMES_HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
HERMES_CONFIG = HERMES_HOME / "config.yaml"
SKILL_RETRIEVAL_PROBE_REPORT_FILENAME = "retrieval_probe.json"


@dataclass(frozen=True)
class SkillSpec:
    name: str
    description: str
    tags: list[str]
    related_skills: list[str]
    purpose: str
    use_when: list[str]
    do_not_use_for: list[str]
    families: list[str]
    query_template: str
    probe_query: str
    boundaries: list[str]
    examples: list[str]
    review_rubric: list[str]


SKILLS: list[SkillSpec] = [
    SkillSpec(
        name="emotional-support",
        description="Use when the user needs low-risk emotional support, empathic reflection, gentle clarification, or non-diagnostic coping support grounded in TheraSkill retrieval.",
        tags=["theraskill", "mental-health", "emotional-support", "retrieval"],
        related_skills=["risk-triage", "cognitive-reframing-support"],
        purpose="Support low-risk emotional distress with empathic, bounded, non-diagnostic responses grounded in dynamically retrieved TheraSkill evidence.",
        use_when=[
            "The user describes stress, anxiety, relationship distress, sadness, pressure, uncertainty, or need for supportive conversation.",
            "There is no explicit self-harm, suicide, violence, acute danger, or request for diagnosis.",
            "The response needs warmth, normalization, clarification, and small practical next steps.",
        ],
        do_not_use_for=[
            "Explicit self-harm, suicidal ideation, imminent danger, abuse emergency, or severe crisis. Route through risk-triage and platform safety policy.",
            "Formal diagnosis, treatment plan, medication advice, or replacing a clinician.",
            "Generating synthetic counseling datasets or reviewing therapist fidelity.",
        ],
        families=["style_response", "therapy_process", "qa_psychoeducation"],
        query_template="用户低风险情绪困扰、压力、焦虑、共情支持、温和建议",
        probe_query="最近压力很大，总是焦虑睡不好，想找人说说。",
        boundaries=[
            "Avoid diagnosis and avoid claiming clinical certainty.",
            "Use retrieved evidence as support patterns, not as quoted medical advice.",
            "If risk language appears, pause ordinary support and route to risk-triage.",
            "Do not treat synthetic construction knowledge as observed therapy evidence.",
        ],
        examples=[
            "A low-risk stress message should retrieve style_response, therapy_process, or qa_psychoeducation items and produce empathic reflection plus one or two gentle options.",
            "A vague sadness message should ask a clarifying question before giving structured advice.",
        ],
        review_rubric=[
            "Response reflects the user's emotion before suggesting actions.",
            "Response stays non-diagnostic and avoids certainty claims.",
            "Evidence bundle excludes source_inventory, deferred_accounting, and synthetic construction as observed evidence.",
            "Escalates to risk-triage if self-harm or suicide language appears.",
        ],
    ),
    SkillSpec(
        name="risk-triage",
        description="Use when user text includes self-harm, suicide, crisis, danger, severe symptom, or risk-boundary language and TheraSkill evidence should inform careful non-diagnostic triage.",
        tags=["theraskill", "mental-health", "risk", "triage", "retrieval"],
        related_skills=["emotional-support"],
        purpose="Identify and handle risk-boundary language using reviewed clinical risk assessment knowledge without diagnosing or assigning final risk scores.",
        use_when=[
            "The user mentions suicide, self-harm, not wanting to live, plans, means, imminent danger, or severe crisis.",
            "The user asks whether a mental-health situation is dangerous or needs urgent attention.",
            "A supportive response needs explicit safety-boundary handling.",
        ],
        do_not_use_for=[
            "Ordinary low-risk distress without crisis language; use emotional-support.",
            "Formal C-SSRS administration, diagnosis, risk scoring, or local emergency-resource generation.",
            "Data quality review. Clinical risk content is not a data-quality problem.",
        ],
        families=["clinical_risk_assessment", "qa_psychoeducation", "style_response"],
        query_template="自杀 自伤 危机 风险 轻生 不想活 安全边界",
        probe_query="我不想活了，最近一直在想自杀。",
        boundaries=[
            "Do not diagnose, score risk, or promise confidentiality/safety.",
            "Use crisis language as a routing and boundary signal, not as a final assessment.",
            "Defer local emergency instructions to downstream product safety policy.",
            "Keep tone supportive and direct; do not debate or minimize the risk statement.",
        ],
        examples=[
            "A direct suicidal statement should retrieve clinical_risk_assessment items and produce an immediate supportive boundary-aware response.",
            "Ambiguous hopelessness should be handled with gentle clarification and safety-oriented attention.",
        ],
        review_rubric=[
            "Risk language is recognized without overdiagnosis.",
            "Response does not provide a final risk category or clinical score.",
            "Evidence bundle prioritizes clinical_risk_assessment and excludes accounting-only rows.",
            "Response remains supportive rather than punitive or dismissive.",
        ],
    ),
    SkillSpec(
        name="cognitive-reframing-support",
        description="Use when the user needs help identifying cognitive distortions, reframing harsh self-talk, or generating alternative thoughts with TheraSkill retrieval support.",
        tags=["theraskill", "mental-health", "cognitive-reframing", "cbt", "retrieval"],
        related_skills=["emotional-support", "risk-triage"],
        purpose="Support bounded cognitive reframing: identify possible thinking patterns, soften self-talk, and propose alternative perspectives without forcing positivity.",
        use_when=[
            "The user describes self-blame, catastrophizing, all-or-nothing thinking, worthlessness, mind-reading, or rigid negative beliefs.",
            "The user asks for help reframing a thought or finding alternative interpretations.",
            "The interaction needs CBT-like structure but not formal therapy.",
        ],
        do_not_use_for=[
            "Active crisis or self-harm language; route to risk-triage first.",
            "Formal CBT treatment plans or diagnosis.",
            "Arguing the user out of feelings or invalidating real constraints.",
        ],
        families=["method_rubric", "qa_psychoeducation", "clinical_risk_assessment", "style_response"],
        query_template="认知重构 认知歪曲 灾难化 替代想法 信念 自我否定",
        probe_query="我觉得自己一无是处，什么都做不好，肯定永远不会变好。",
        boundaries=[
            "Treat distortion labels as hypotheses, not diagnoses or judgments.",
            "Reflect emotion before reframing; do not jump straight to correction.",
            "Keep alternative thoughts plausible and evidence-sensitive.",
            "If risk language appears, use risk-triage before reframing.",
        ],
        examples=[
            "A catastrophizing thought should retrieve method_rubric or qa_psychoeducation items and produce a balanced alternative thought.",
            "A harsh self-label should be reframed into behavior-specific and change-oriented language.",
        ],
        review_rubric=[
            "Response names possible thought patterns tentatively.",
            "Response validates affect before offering reframe.",
            "Alternative thought is realistic, not forced optimism.",
            "Evidence bundle excludes synthetic construction as observed evidence.",
        ],
    ),
    SkillSpec(
        name="synthetic-dialogue-builder",
        description="Use when constructing synthetic counseling dialogues from therapy framework, role, stage, rationale/dialogue separation, and source-governed TheraSkill construction knowledge.",
        tags=["theraskill", "synthetic-dialogue", "construction", "retrieval"],
        related_skills=["therapy-response-reviewer", "emotional-support"],
        purpose="Build synthetic counseling dialogue plans and drafts using construction-method knowledge, while keeping generated rationale and observed dialogue evidence separate.",
        use_when=[
            "The user asks to generate synthetic counseling dialogues, role-play samples, staged counseling turns, or therapy training data.",
            "The task needs therapy framework, role setup, stage structure, current turn analysis, rationale/dialogue separation, or source-governed construction rules.",
            "The output should be clearly synthetic and not presented as observed clinical dialogue.",
        ],
        do_not_use_for=[
            "Responding directly to a distressed user seeking support; use emotional-support or risk-triage.",
            "Treating SMILECHAT, PsyDTCorpus, or SoulChat-R1 construction fields as observed therapist evidence.",
            "Creating clinical diagnosis, risk score, or real treatment plan.",
        ],
        families=["synthetic_construction", "therapy_process", "method_rubric", "style_response"],
        query_template="rationale dialogue Generated Final response construction SMILECHAT PsyDTCorpus SoulChat",
        probe_query="请按角色设定、阶段结构和 rationale/dialogue 分离原则生成一段合成咨询对话。",
        boundaries=[
            "Label generated dialogue as synthetic.",
            "Keep construction rationale separate from final dialogue.",
            "Do not claim generated examples are observed therapy evidence.",
            "Use clinical process examples only as structural inspiration, not as copied dialogue.",
        ],
        examples=[
            "A synthetic case request should retrieve synthetic_construction and therapy_process items before drafting stages.",
            "A request for dialogue with rationale should output rationale separately from the client/therapist turns.",
        ],
        review_rubric=[
            "Output marks itself as synthetic/training data.",
            "Rationale and dialogue are separated.",
            "Synthetic construction evidence is used as method knowledge, not observed therapy evidence.",
            "No raw private data or large copied examples are inserted.",
        ],
    ),
    SkillSpec(
        name="therapy-response-reviewer",
        description="Use when reviewing a therapy-style response for stage fit, strategy, rationale/dialogue separation, source faithfulness, safety boundary, and therapeutic fidelity.",
        tags=["theraskill", "therapy-review", "response-review", "retrieval"],
        related_skills=["synthetic-dialogue-builder", "risk-triage", "emotional-support"],
        purpose="Review therapy-style responses with dynamic evidence for process fit, strategy quality, boundaries, and source-governed rationale/dialogue separation.",
        use_when=[
            "The user asks whether a counseling response is appropriate, safe, stage-consistent, or faithful to a therapeutic strategy.",
            "The task needs critique of empathy, reflection, question asking, intervention choice, rationale/dialogue separation, or boundary handling.",
            "The response is synthetic or draft-like and needs reviewer feedback before use.",
        ],
        do_not_use_for=[
            "Delivering therapy directly to a user in crisis; use risk-triage.",
            "Treating generated rationale as observed evidence.",
            "Formal clinical supervision, diagnosis, or legal/medical certification.",
        ],
        families=["therapy_process", "method_rubric", "synthetic_construction", "style_response", "qa_psychoeducation"],
        query_template="rationale dialogue stage strategy reflection support_strategy_sequence interaction_pattern",
        probe_query="请审核这个治疗师回应是否符合阶段、策略、rationale/dialogue 分离和安全边界。",
        boundaries=[
            "Review the response; do not impersonate a clinician or certify clinical safety.",
            "Separate observed dialogue evidence from generated rationale or construction notes.",
            "Flag risk-boundary issues and route them to risk-triage policy.",
            "Avoid turning style examples into clinical action evidence.",
        ],
        examples=[
            "A draft response review should retrieve therapy_process and method_rubric items, then produce strengths, issues, and revision suggestions.",
            "A generated rationale should be checked as non-evidence and kept separate from final dialogue.",
        ],
        review_rubric=[
            "Review identifies stage fit and strategy fit.",
            "Review distinguishes rationale, observed dialogue, and synthetic construction knowledge.",
            "Review flags safety or crisis boundary problems.",
            "Review gives concrete revisions without overclaiming clinical authority.",
        ],
    ),
    SkillSpec(
        name="rebt-belief-work-support",
        description="Use when supporting REBT-style ABC analysis, belief identification, disputation, and alternative belief generation with bounded TheraSkill retrieval.",
        tags=["theraskill", "rebt", "abc", "belief-work", "retrieval"],
        related_skills=["cognitive-reframing-support", "emotional-support", "risk-triage"],
        purpose="Support REBT-style belief work by structuring activating events, beliefs, consequences, disputation prompts, and alternative beliefs without over-therapizing.",
        use_when=[
            "The user asks for ABC analysis, belief disputation, alternative beliefs, or REBT-like structured reflection.",
            "The user describes an event, interpretation, emotional consequence, and wants a more workable belief.",
            "The task overlaps with cognitive reframing but needs explicit ABC / belief structure.",
        ],
        do_not_use_for=[
            "Active crisis or self-harm language; route to risk-triage first.",
            "Formal REBT therapy plan, diagnosis, or treatment replacement.",
            "Forcing rationality language in a way that invalidates emotion or context.",
        ],
        families=["method_rubric", "qa_psychoeducation", "therapy_process", "style_response"],
        query_template="ABC 信念 情绪 行为 认知 艾利斯 rational belief",
        probe_query="请用 ABC 框架帮我区分事件、信念、情绪后果，并尝试提出替代信念。",
        boundaries=[
            "Treat ABC as a reflection scaffold, not a diagnosis.",
            "Validate emotion before disputing or replacing beliefs.",
            "Use disputation gently; do not argue the user out of their experience.",
            "If risk language appears, route to risk-triage before belief work.",
        ],
        examples=[
            "An ABC request should separate activating event, belief, consequence, disputation question, and alternative belief.",
            "A rigid self-demand should be softened into a flexible preference when evidence supports it.",
        ],
        review_rubric=[
            "ABC components are separated clearly.",
            "Belief disputation is tentative and respectful.",
            "Alternative belief is plausible and not forced optimism.",
            "Response avoids diagnosis and routes crisis language appropriately.",
        ],
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def yaml_list(values: list[str], indent: int = 4) -> str:
    pad = " " * indent
    return "\n".join(f"{pad}- {value}" for value in values)


def skill_md(spec: SkillSpec) -> str:
    return f"""---
name: {spec.name}
description: "{spec.description}"
version: 0.1.0
author: TheraSkill Project
license: research-internal
platforms: [linux]
metadata:
  hermes:
    tags: [{", ".join(spec.tags)}]
    related_skills: [{", ".join(spec.related_skills)}]
---

# {spec.name}

## Overview

{spec.purpose}

This skill does not contain a copied knowledge base. It calls `scripts/{SKILL_PACKAGE_RUNTIME_SCRIPT}` to retrieve a runtime evidence bundle from the TheraSkill Phase 6 reviewed release through LLM query planning, normalized metadata prefiltering, and hybrid BM25/label/dense retrieval.

## When to Use

{yaml_list(spec.use_when, 0)}

## Do Not Use For

{yaml_list(spec.do_not_use_for, 0)}

## Runtime Retrieval

Use this command shape from the skill directory:

```bash
python scripts/{SKILL_PACKAGE_RUNTIME_SCRIPT} --skill {spec.name} --query "<current user context>" --top-k 8 --json
```

The script returns `theraskill_runtime_evidence_bundle_v3_llm_structured_hybrid` with the LLM retrieval plan, structured filter trace, channel counts, `release_unit_id`, source family, evidence tier, labels, score details, and text previews. Treat retrieved items as grounding and boundary evidence, not as text to copy verbatim.

## Workflow

1. Classify whether the current user context matches this skill and whether `risk-triage` should take priority.
2. Retrieve an evidence bundle using the current user context, not a generic query.
3. Read `references/source-policy.md` and `references/boundaries.md` before relying on evidence.
4. Draft the response using the retrieved patterns as constraints and examples.
5. Check the response against `references/review-rubric.md` before finalizing.

## Required Boundaries

{yaml_list(spec.boundaries, 0)}

## Supporting Files

- `references/source-policy.md`
- `references/boundaries.md`
- `references/examples.md`
- `references/review-rubric.md`
- `skill_manifest.json`
"""


def reference_docs(spec: SkillSpec) -> dict[str, str]:
    families = ", ".join(spec.families)
    allows_synthetic = spec.name in {"synthetic-dialogue-builder", "therapy-response-reviewer"}
    synthetic_policy = (
        "- Synthetic construction knowledge may be retrieved for this skill, but only as construction/review methodology, never as observed therapy evidence."
        if allows_synthetic
        else "- Do not retrieve or rely on synthetic construction knowledge as evidence for this skill."
    )
    return {
        "boundaries.md": "# Boundaries\n\n" + "\n".join(f"- {item}" for item in spec.boundaries) + "\n",
        "examples.md": "# Examples\n\n" + "\n".join(f"- {item}" for item in spec.examples) + "\n",
        "review-rubric.md": "# Review Rubric\n\n" + "\n".join(f"- {item}" for item in spec.review_rubric) + "\n",
        "source-policy.md": f"""# Source Policy

- Runtime source: TheraSkill Phase 6 reviewed knowledge through LLM-structured hybrid retrieval v3.
- Query understanding must come from the LLM retrieval planner. Do not replace it with keyword or rule-based parsing.
- Retrieval uses normalized enum metadata prefilters before BM25 / compact label / dense bge-m3 / required-family channels.
- Allowed retrieval families for this skill: `{families}`.
- Exclude `source_inventory` from ordinary retrieval.
- Exclude `deferred_accounting` / accounting-only rows from default retrieval.
{synthetic_policy}
- Keep `release_unit_id` in evidence bundles for traceability.
- Do not copy raw datasets or large JSONL files into the Hermes skill package.
""",
    }


def frontmatter_valid(content: str) -> tuple[bool, str | None]:
    if not content.startswith("---"):
        return False, "missing_yaml_frontmatter"
    match = re.search(r"\n---\s*\n", content[3:])
    if not match:
        return False, "unclosed_yaml_frontmatter"
    yaml_text = content[3 : match.start() + 3]
    for required in ("name:", "description:"):
        if required not in yaml_text:
            return False, f"frontmatter_missing_{required[:-1]}"
    if not content[match.end() + 3 :].strip():
        return False, "empty_body"
    return True, None


def run_retrieval_probe(skill_dir: Path, spec: SkillSpec) -> dict[str, Any]:
    output = skill_dir / "reports" / SKILL_RETRIEVAL_PROBE_REPORT_FILENAME
    cmd = [
        PYTHON,
        str(skill_dir / "scripts" / SKILL_PACKAGE_RUNTIME_SCRIPT),
        "--skill",
        spec.name,
        "--query",
        spec.probe_query,
        "--top-k",
        "6",
        "--output",
        str(output),
        "--overwrite",
    ]
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), text=True, capture_output=True)
    result: dict[str, Any] = {
        "command": cmd,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "output": str(output),
    }
    if proc.returncode == 0 and output.exists():
        bundle = json.loads(output.read_text(encoding="utf-8"))
        result["hits"] = len(bundle.get("items") or [])
        result["policy_checks"] = bundle.get("policy_checks")
        result["release_unit_ids"] = [item.get("release_unit_id") for item in bundle.get("items", [])]
        result["families"] = sorted({item.get("dedup_family") for item in bundle.get("items", [])})
    return result


def build_skill(output_root: Path, spec: SkillSpec, db: Path, overwrite: bool) -> dict[str, Any]:
    skill_dir = output_root / spec.name
    ensure_output_dir(skill_dir, overwrite=overwrite)
    prior_h6_runtime_status = None
    prior_manifest = skill_dir / "skill_manifest.json"
    if prior_manifest.exists():
        prior_data = json.loads(prior_manifest.read_text(encoding="utf-8"))
        prior_h6_runtime_status = prior_data.get("h6_runtime_status")
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
    (skill_dir / "references").mkdir(parents=True, exist_ok=True)
    (skill_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (skill_dir / "reports").mkdir(parents=True, exist_ok=True)

    shutil.copy2(RETRIEVAL_BRIDGE, skill_dir / "scripts" / SKILL_PACKAGE_RUNTIME_SCRIPT)
    shutil.copy2(WRAPPER_COMMON_PATH, skill_dir / "scripts" / WRAPPER_COMMON_PATH.name)
    write_text(skill_dir / "SKILL.md", skill_md(spec))
    for filename, content in reference_docs(spec).items():
        write_text(skill_dir / "references" / filename, content)

    manifest = {
        "schema_version": "theraskill_hermes_skill_manifest_v1",
        "created_at": utc_now(),
        "skill": spec.name,
        "source_plan": str(PROJECT_ROOT / "docs/hermes_skill_development_plan.zh.md"),
        "phase6_release": str(DEFAULT_RELEASE),
        "retrieval_indexes": {
            "sqlite_fts": str(db),
            "structured_index": str(PHASE6_STRUCTURED_INDEX_PATH),
            "retrieval_text_view": str(PHASE6_RETRIEVAL_TEXT_VIEW_PATH),
        },
        "qa_inputs": {
            "phase6_release_qa": str(DEFAULT_RELEASE_QA),
            "retrieval_index_qa": str(DEFAULT_RETRIEVAL_QA),
            "sqlite_retrieval_index_qa": str(DEFAULT_SQLITE_QA),
        },
        "retrieval_policy": {
            "families": spec.families,
            "query_template": spec.query_template,
            "probe_query": spec.probe_query,
            "dynamic_bundle_only": True,
            "no_archived_prototype_inputs": True,
        },
        "files": {
            "skill_md": str(skill_dir / "SKILL.md"),
            "retrieval_script": str(skill_dir / "scripts" / SKILL_PACKAGE_RUNTIME_SCRIPT),
            "retrieval_helper": str(skill_dir / "scripts" / WRAPPER_COMMON_PATH.name),
            "references": sorted(str(p) for p in (skill_dir / "references").glob("*.md")),
        },
    }
    if prior_h6_runtime_status:
        manifest["h6_runtime_status"] = prior_h6_runtime_status
    write_json(skill_dir / "skill_manifest.json", manifest)
    probe = run_retrieval_probe(skill_dir, spec)
    return {"skill": spec.name, "path": str(skill_dir), "manifest": manifest, "probe": probe}


def build_install_notes(output_root: Path) -> dict[str, Any]:
    config_exists = HERMES_CONFIG.exists()
    config_text = HERMES_CONFIG.read_text(encoding="utf-8") if config_exists else ""
    output_abs = str(output_root.resolve())
    already_configured = output_abs in config_text
    check = {
        "schema_version": "theraskill_hermes_external_dirs_check_v1",
        "created_at": utc_now(),
        "hermes_home": str(HERMES_HOME),
        "config_path": str(HERMES_CONFIG),
        "config_exists": config_exists,
        "external_dir": output_abs,
        "external_dir_exists": output_root.exists(),
        "already_configured": already_configured,
        "recommended_yaml": {
            "skills": {
                "external_dirs": [output_abs],
            }
        },
    }
    notes = f"""# Hermes External Dirs Install Notes

Generated at: {check['created_at']}

TheraSkill Hermes skills were generated at:

```text
{output_abs}
```

Current Hermes config checked:

```text
{HERMES_CONFIG}
```

`already_configured`: `{str(already_configured).lower()}`

Recommended config addition:

```yaml
skills:
  external_dirs:
    - {output_abs}
```

This script did not modify the Hermes config. Add the external dir manually, then reload or restart Hermes and inspect `/skills` or `skill_view`.
"""
    write_json(output_root / "hermes_external_dirs_check.json", check)
    write_text(output_root / "install_notes.md", notes)
    return check


def main() -> int:
    args = parse_args()
    output_root = Path(args.output_root)
    db = Path(args.db)
    if not db.exists():
        raise FileNotFoundError(f"SQLite retrieval DB not found: {db}")
    output_root.mkdir(parents=True, exist_ok=True)

    built = [build_skill(output_root, spec, db, args.overwrite) for spec in SKILLS]
    external_check = build_install_notes(output_root)

    issues: list[dict[str, Any]] = []
    for item in built:
        skill_dir = Path(item["path"])
        content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        ok, reason = frontmatter_valid(content)
        if not ok:
            issues.append({"skill": item["skill"], "issue": reason})
        required = [
            skill_dir / "SKILL.md",
            skill_dir / "skill_manifest.json",
            skill_dir / "scripts" / SKILL_PACKAGE_RUNTIME_SCRIPT,
            skill_dir / "scripts" / WRAPPER_COMMON_PATH.name,
            skill_dir / "references/boundaries.md",
            skill_dir / "references/examples.md",
            skill_dir / "references/review-rubric.md",
            skill_dir / "references/source-policy.md",
        ]
        for path in required:
            if not path.exists():
                issues.append({"skill": item["skill"], "issue": "missing_file", "path": str(path)})
        probe = item["probe"]
        if probe.get("returncode") != 0:
            issues.append({"skill": item["skill"], "issue": "retrieval_probe_failed", "stderr": probe.get("stderr")})
        if not probe.get("hits"):
            issues.append({"skill": item["skill"], "issue": "retrieval_probe_no_hits"})
        checks = probe.get("policy_checks") or {}
        for key in ("has_release_unit_ids", "source_inventory_excluded", "deferred_accounting_excluded", "synthetic_construction_policy_ok"):
            if checks.get(key) is not True:
                issues.append({"skill": item["skill"], "issue": f"policy_check_failed_{key}", "value": checks.get(key)})

    compile_report = {
        "schema_version": "theraskill_hermes_skill_compile_report_v1",
        "created_at": utc_now(),
        "output_root": str(output_root),
        "skills": [
            {
                "skill": item["skill"],
                "path": item["path"],
                "probe_hits": item["probe"].get("hits"),
                "probe_families": item["probe"].get("families"),
                "release_unit_ids": item["probe"].get("release_unit_ids"),
            }
            for item in built
        ],
        "external_dirs_check": external_check,
        "script_sha256": {
            "compile_hermes_skills.py": sha256_file(Path(__file__)),
            SKILL_PACKAGE_RUNTIME_SCRIPT: sha256_file(RETRIEVAL_BRIDGE),
            RETRIEVAL_SCRIPT_FILENAMES[CURRENT_RUNTIME_BRIDGE_MODE]: sha256_file(RETRIEVAL_BRIDGE),
            WRAPPER_COMMON_PATH.name: sha256_file(WRAPPER_COMMON_PATH),
        },
        "scope_note": "Skill compilation now records retrieval probes for package QA; older smoke-era naming is retired from the current compile surface.",
    }
    qa = {
        "schema_version": "theraskill_hermes_skill_qa_v1",
        "created_at": compile_report["created_at"],
        "status": "complete" if not issues else "failed",
        "issues_count": len(issues),
        "issues": issues,
        "skills_checked": [spec.name for spec in SKILLS],
        "external_dirs_already_configured": external_check["already_configured"],
    }
    write_json(output_root / "hermes_skill_compile_report.json", compile_report)
    write_json(output_root / "hermes_skill_qa.json", qa)
    print(json.dumps({"output_root": str(output_root), "status": qa["status"], "issues_count": len(issues), "skills": qa["skills_checked"]}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
