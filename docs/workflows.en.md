# TheraSkillAgent workflow guide

[中文](workflows.zh.md) · [Deployment](deployment.en.md)

[Evidence checks, staged generation and recovery](workflow_reliability.en.md)

Run `theraskill-agent ui` and open <http://127.0.0.1:5176/>. The home page provides **Skill workflows**, **Behavioral evaluations**, and **Courses and learning progress**, alongside transcript, knowledge, support, simulation and expert workspaces. All business modules run in the same process and browser port, without additional runtime dependencies.

## Tasks & history

Start work in its workspace, then use **Tasks & history** to follow execution and reopen results:

- **Active & attention**: running, queued, waiting for input or approval, failed, partial, and interrupted runs.
- **History**: completed or cancelled runs.
- **Archived**: records put aside with an option to restore them. Archiving a workflow includes its related extraction steps; groups with running steps cannot be archived.

Task type, status, and keyword filters work together with pagination. Trial extraction, full extraction, and extraction from earlier plans are grouped under their workflow; expand a group to inspect its steps. Explicitly labelled test records are hidden from Active & attention and History by default and remain visible in Archived. Archiving preserves source records, results, and checkpoints. Restarting an archived job brings it back into the current list.

This view combines skill workflows, extraction, expert review/content generation, and course scenario batches. Simulation sessions, micro-practice, standalone patient generation, transcript analysis, and index jobs remain in their respective workspaces. If one source is unavailable, the page identifies it while keeping the other sources accessible.

Automated tests use temporary directories and synthetic API responses. For manual acceptance runs, start an isolated project as described in the [deployment guide](deployment.en.md). Test labels come from explicit record metadata, never from guessing based on a task's title.

## Interactive skill tasks

Open Skill workflows, describe a goal and supply text, a local path or an uploaded file. Select an installed skill or let the model select from the catalog. The executor reads the complete `SKILL.md` and asks the model to choose a registered tool, ask a question or finish. Reference files can be read through a tool.

Questions pause execution in a form. Submit an answer to continue the same task. Tools that change data show their arguments and require confirmation; rejecting the request prevents execution. Task history, decisions, results and answers are saved. Refreshing or reopening the task URL restores the view. Interrupted workers require an explicit resume, failed stages retain successful checkpoints, and cancellation reaches child jobs.

Markdown skills do not implicitly grant shell access. Registered capabilities cover text normalization/chunking, source inspection/sampling, retrieval, transcript analysis/review, extraction and index management. Skills requiring additional tools need adapters; extensions use `theraskill_agent.skill_execution.register_tool`. The interface lists the available catalog.

Skill evolution creates a model-generated draft under `data/theraskill/skill-drafts/<task-id>/`, preserving the original and references. A generated draft is not proof of improvement. Follow its evaluation link, establish a baseline for the selected skill, and compare the candidate using identical cases and rubrics. New installations are scanned. Replacing an existing skill, including a bundled name, requires a passing and human-reviewed comparison report. Fingerprints cover the skill and reference files. Previous versions remain available for rollback.

The CLI shares the same task store:

```bash
theraskill-agent task create skill --goal 'Organize this text and preserve its source' \
  --language en --inputs '{"text":"Example text","skill":"transcript-normalizer"}'
theraskill-agent task list
theraskill-agent task show TASK_ID
theraskill-agent task respond TASK_ID --values '{"answer":"Additional information"}'
theraskill-agent task retry TASK_ID
theraskill-agent task cancel TASK_ID
```

Creation and resume commands run until completion, failure or a pause for interaction. Service tasks can be inspected in the browser. Stale concurrent edits return a conflict and require a refresh.

## Source to active knowledge

Choose knowledge extraction in Skill workflows:

1. Inspect and sample the source. The model proposes task-specific fields, evidence fields and rules from the actual samples.
2. Edit the schema/plan and confirm a sample run.
3. Inspect actual outputs, evidence and provenance before confirming full extraction. Return to the plan or request replanning when necessary.
4. Review the full result and confirm handoff to expert governance.
5. Complete candidate structuring, deduplication, adjudication, validation and a preview release. Evaluate the preview against a baseline and obtain human review before promotion.
6. Return to the original task with a promoted release containing every candidate from this task. Supply the base knowledge JSONL matching the active index. The workflow verifies its fingerprint, merges the base corpus with the published delta, and builds a new index.
7. Evaluate the candidate index against a baseline, review the report, then enter its ID and explicitly confirm activation in the original task.

Sample and full runs have separate durable child jobs. Repeated dispatch uses stable request IDs. Source changes prevent continued extraction under the old plan. Governance, promotion and activation remain explicit actions.

Knowledge and indexes also supports independent import, construction, verification and search. A device with no active index can bootstrap its first selection. Changing an existing selection requires a passing reviewed report through the UI, tasks and CLI. File integrity verification and behavioral evaluation are separate checks. Restoring the previous active selection does not create a new candidate.

```bash
theraskill-agent knowledge activate --index CANDIDATE_DIRECTORY_ID \
  --behavior-report REVIEWED_REPORT_ID
theraskill-agent skill install /path/to/candidate --overwrite \
  --behavior-report REVIEWED_REPORT_ID
```

## Behavioral evaluations and gates

Establish a baseline in Behavioral evaluations, then run identical cases and rubrics against a preview release, candidate index or skill directory. Each case runs its installed skill through the production TaskEngine, including tools and scripted clarification answers, then calls a model to judge relevance, grounding, task completion, language and boundaries. Mutation and script approvals are never granted automatically by the evaluator.

The interface shows engineering checks, actual answers, citations, score explanations, fingerprints, baseline changes and human decisions. The editable default suite is `configs/theraskill/evaluations/behavior.json`. Its 12 cases execute the same production TaskEngine, tool registry, checkpoints and interactive questions as user tasks. They provide bounded functional checks; production evaluation needs cases representing the intended tasks, users and boundary conditions.

A passing report requires complete cases, successful checks, dimension scores meeting thresholds, a comparable baseline with no excessive regression, and human approval. Approval cannot override failed checks. Changes to knowledge, index manifests, skills/references, LLM/embedding settings or the evaluator invalidate approval or use of old reports. Rerun evaluation after changes. Reports and review history live in `data/theraskill/evaluations/behavior/`.

Model judgments are machine opinions. High scores or no regression do not establish clinical efficacy or prove that a candidate skill is better. Human reviewers should inspect actual outputs and evidence.

## Courses and learning progress

Start with a teaching template, adapt the behavioural rubric and exercises, then assign approved cases. Learners submit short responses, save reflection and request retrieval-grounded AI feedback; instructors record their own observations and decisions. Full case completion still uses actual session evidence and the configured review gate. Progress compares ratings only under comparable conditions.

See the [complete teaching and learning guide](learning.en.md) for course generation, micro-practices, LLM judging, feedback histories and progress.

## Languages and persistence

Switch Chinese/English in the interface; the preference is saved locally. Reviews, support responses, generation, task questions and results receive an output language. Transcript source language is independent of review language. Source text, evidence quotations, existing user content and JSON field names are preserved. Historical model outputs keep their original language.

Back up the writable project, including tasks, evaluation reports, course databases, sessions, `skills/local/`, configuration and knowledge/index resources. Restart the single application after code upgrades. Preserve data directories. Full knowledge releases continue to be distributed separately from source.

## Visual editing, long sources and retrieval evaluation

Workflow forms offer file uploads, installed resource/release/report selectors and a visual schema editor, including nested objects and arrays. Full plan JSON remains available under advanced options. Long TXT/Markdown/SRT/VTT inputs are segmented with original character offsets and configurable overlap; samples span the whole source. Review processes full turn windows by default and reports coverage and failed windows.

Behavior cases can be edited as forms. Engineering checks include actual completion, required tools, all required evidence families and consumption of scripted clarification answers. Saved reports retain actual execution traces. Runtime source fingerprints also bind reports to the code under evaluation.

The retrieval panel uses the production skill search path, 13 editable default cases, 1–4 concurrent requests and 1–10 repetitions. Inspect channel warnings, hard-filter checks and P50/P95. Optional human grades range from 0 to 3; identify the annotator and export the case/judgment JSON to preserve them. Unjudged Recall/nDCG are unavailable. Small samples are not latency guarantees. English-only synthetic-construction coverage is a known data gap, shown explicitly.

Upload a skill ZIP to inspect readiness before installation, or run `theraskill-agent skill check SKILL_NAME`. A missing adapter is actionable and never triggers dependency installation. `skill.run_script` requires explicit approval, accepts contained Python scripts only and records complete output files. It executes locally with permissions of the application user; it is not an operating-system sandbox. Context-budget pauses preserve history; answer the requested budget question to continue.
