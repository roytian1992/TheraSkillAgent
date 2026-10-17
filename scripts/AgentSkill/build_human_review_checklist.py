#!/usr/bin/env python3
"""Build a human review checklist for TheraSkill Hermes skills."""

from __future__ import annotations

from hermes_surface_registry import HUMAN_REVIEW_CHECKLIST_PATH, OUTPUT_ROOT
from retrieval_wrapper_common import SKILL_PACKAGE_RUNTIME_SCRIPT


OUTPUT = HUMAN_REVIEW_CHECKLIST_PATH
OUTPUT_ROOT_REL = str(OUTPUT_ROOT.relative_to(OUTPUT_ROOT.parents[1]))


CHECKLIST = f"""# TheraSkill Hermes Skills Human Review Checklist

Generated for the six runtime Hermes skills under `{OUTPUT_ROOT_REL}/`.

## Scope

Review the generated `SKILL.md`, references, retrieval evidence bundles, deterministic E2E report, and LLM response evaluator report if available.

## Global Checks

- [ ] The skill trigger is clear and does not overlap confusingly with another skill.
- [ ] The skill does not copy large raw data, raw JSONL, or private source text into the package.
- [ ] The distributed retrieval runtime script `{SKILL_PACKAGE_RUNTIME_SCRIPT}` is present and callable inside each skill package.
- [ ] The retrieval script returns `release_unit_id` for every evidence item.
- [ ] `source_inventory` is excluded from ordinary retrieval.
- [ ] `deferred_accounting` / accounting-only rows are excluded from default retrieval.
- [ ] Synthetic construction knowledge is never treated as observed therapy evidence.
- [ ] Clinical risk content is treated as a safety/content category, not as a data-quality problem.

## Skill-Specific Checks

### emotional-support

- [ ] Uses empathy and reflection before suggestions.
- [ ] Avoids diagnosis, medication advice, and treatment-plan claims.
- [ ] Routes self-harm / suicide / imminent danger language to `risk-triage`.

### risk-triage

- [ ] Recognizes suicide/self-harm/crisis language without assigning final clinical risk scores.
- [ ] Avoids diagnosis and avoids overconfident safety claims.
- [ ] Leaves local emergency-resource wording to downstream product safety policy.

### cognitive-reframing-support

- [ ] Labels cognitive distortions tentatively.
- [ ] Validates emotion before reframing.
- [ ] Alternative thoughts are plausible and not forced positivity.

### synthetic-dialogue-builder

- [ ] Clearly labels outputs as synthetic / training material.
- [ ] Separates rationale from final dialogue.
- [ ] Uses construction knowledge as method guidance, not observed clinical evidence.

### therapy-response-reviewer

- [ ] Distinguishes observed dialogue, generated rationale, and construction notes.
- [ ] Reviews stage fit, strategy fit, empathy, and safety boundaries.
- [ ] Gives concrete revision suggestions without certifying clinical safety.

### rebt-belief-work-support

- [ ] Separates ABC components: activating event, belief, consequence.
- [ ] Uses disputation gently and respectfully.
- [ ] Routes crisis language to `risk-triage` before belief work.

## Evidence Review

- [ ] Sample retrieved evidence items are relevant to the case.
- [ ] Evidence families match the skill's source policy.
- [ ] Synthetic construction appears only for `synthetic-dialogue-builder` or `therapy-response-reviewer`.
- [ ] Cognitive distortion evidence from C-IMHI is treated as cognitive-label knowledge, not as crisis evidence unless crisis cues are present.

## Final Decision

- [ ] approve
- [ ] approve with minor edits
- [ ] revise and rerun QA
- [ ] block from runtime use pending review

Reviewer notes:

```text

```
"""


def main() -> int:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(CHECKLIST, encoding="utf-8")
    print(str(OUTPUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
