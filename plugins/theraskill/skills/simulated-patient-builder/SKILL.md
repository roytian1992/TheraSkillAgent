---
name: simulated-patient-builder
description: Build, inspect, revise and reuse synthetic patients for multi-turn counseling practice, including life background, recent experiences, goals and gradual disclosure.
runtime:
  required_tools:
  - theraskill_patient
---

# Simulated Patient Builder

Use `theraskill_patient` to persist the actual case used by the simulation UI.
Work in the user's language. Clarify material ambiguity in identity, learning
objectives or intended risk; otherwise create a draft with stated assumptions.
Distinguish the learner-visible training brief from private case facts.

Read [the patient schema](references/patient-schema.md) before constructing a
tool payload. Background and events are authored case facts, not things the
patient has already disclosed or memories from a conversation. Preserve supplied
facts and their sensitivity; do not invent acute risk or diagnoses to add drama.

- For an open-ended theme, use `generate` with a brief and any supplied `patient`
  seed. Generation runs through planning, dossier construction, scenario packaging,
  independent review, and a focused repair when needed. It retrieves construction
  knowledge when an active index is available; report missing grounding rather than
  inventing evidence. The result remains synthetic and draft regardless of retrieval
  or structural check scores.
- For a fully authored patient, use `build` to compile and save without an LLM.
- To revise, `inspect` first and use `update` with the returned revision as
  `expected_revision`. Send the complete patient object, preserving event IDs
  and unedited fields. On a conflict, inspect again and reconcile the changes.
- To create a different recent experience for the same patient, use `fork`,
  then inspect and update that copy. Editing creates an immutable bundle
  version; ongoing sessions keep the version with which they began.

Verify saved output with `inspect`. Return the scenario ID, saved revision,
assumptions, grounding limitations, and a relative workbench link:
`/theraskill/simulation?scenario_id=ID`. Do not claim approval or behavioral
validation from a structural check. Approval/rejection remains in the scenario
review interface. Keep hidden facts out of the learner-facing summary. In the
executor's `result.evidence_ids`, cite the saved `scenario_id` or `case_id`.
These are synthetic artifact references, not evidence of clinical validity.
