# Building reusable simulated patients

Open the simulation workspace and select Researcher or Instructor. **Build a
simulated patient** accepts a theme for model generation or an authored dossier
that can be saved without a model call. No simulation resource pack, extra server
port or additional dependency is required for an authored case.

A dossier includes public identity and presenting concern, background, recent
experiences, emotions, goals, learning objectives, withheld concerns,
communication style and disclosure preferences. Events have stable IDs, timing,
content, impact and their own disclosure level. They are synthetic facts before
the session, separate from conversation memories.

Select surface, moderate or sensitive disclosure for each fact/event. Timing and
impact stay attached to the event through Persona v2 compilation. The compiler
selects permitted context using the current state and therapist move. This limits
context exposure; it does not guarantee that a model never invents or paraphrases
facts. Inspect actual trajectories before educational use.

## Edit and reuse

**Inspect and edit patient dossier** saves a new immutable bundle version and
resets approval to draft. Updates include the revision read by the editor; stale
edits return a conflict. Existing sessions and their review-only copies retain
their original bundle. New sessions use the latest version.

**Copy as new scenario** creates an independent draft, useful for changing recent
events while retaining the patient. Resource cases must be copied before editing.
Start another practice to reuse a case; use the existing follow-up-session
workflow when conversation state should carry over. Follow-ups retain the original
case version so old conversation memories are not mixed with revised case facts.

Persistent files under `data/theraskill/` are:

- `simulation/generated_scenarios.jsonl`: scenario registry, revisions and reviews.
- `simulation/generated_cases/`: immutable dossiers and embedded Persona v2.
- `simulation/sessions.sqlite`: conversations, states and memories.

## Skill and grounding

Select `simulated-patient-builder` in Skill workflows. For example:

> Build a synthetic patient who feels self-critical after workplace feedback.
> Include two recent experiences, keep family concerns private, and save the
> case for later practice and editing.

Its `theraskill_patient` tool supports generate, build, inspect, update and fork
through the same in-process API as the UI. See the
[schema reference](../plugins/theraskill/skills/simulated-patient-builder/references/patient-schema.md).

Generation retrieves construction guidance from the active knowledge index by
default. Saved provenance records actual source/index IDs and retrieval status.
Missing indexes, failed retrieval and no matches remain explicit; generation may
still create an ungrounded synthetic draft. Retrieved guidance informs training
design and disclosure, never patient biography. The form can disable retrieval.

## Staged online generation

**Generate and save patient draft** uses a staged workflow instead of asking one model call to fill every field at once:

1. **Planning**: derive the training goal, recent-event design, hidden concerns, and disclosure path from the theme, difficulty, risk, and construction guidance.
2. **Patient dossier**: generate only the `PatientDossier` fields: identity, presenting concern, background, recent experiences, hidden concerns, goals, and communication preferences.
3. **Scenario package**: use the validated dossier to produce the public role card, hidden settings, continuity seeds, and initial session state.
4. **Independent review**: check identity, timeline, public/hidden leakage, risk band, and training-objective alignment.
5. **Focused repair**: when the review finds an error, repair only the named issue before deterministic checks run again.

Each stage is a separate structured JSON call, so a Qwen-class local model solves smaller problems instead of placing the entire case schema in one prompt. The result still goes through dossier validation, Persona v2 compilation, duplicate checks, and draft governance. The case bundle stores stage status and the review summary so the workflow can later expose durable Task checkpoints.

The browser button currently waits for the complete workflow and returns once; generation is staged, but each stage is not yet shown as a pausable Tasks Center step. When `simulated-patient-builder` is called through the Agent workflow, the outer Agent can clarify, call tools, and inspect/update the saved case while using this same staged generator underneath.

## Runtime and limits

For Persona v2, fast mode interprets the actual therapist move and realizes a
response, then updates state and memory in the background: usually two foreground
and two background model calls, with more calls possible for repairs/retries.
The compiler controls disclosure without a separate model disclosure call.
The UI waits for synchronization and supports retrying failures. Dual-model
research cycles also complete both agents' deferred updates.

Quality checks cover structure, patient compilation, recent events, objectives
and text duplication. They do not establish behavioral or clinical validity.
Drafts still require instructor review for learner reuse. Event times are authored
labels; there is no general calendar simulation or automatic generation of new
life events between sessions. Edit/copy a case before starting the next session
when its recent experiences should change.
