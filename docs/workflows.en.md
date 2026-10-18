# TheraSkillAgent workflow guide

[中文](workflows.zh.md) · [Deployment](deployment.en.md)

Run `theraskill-agent ui` and open <http://127.0.0.1:5176/>. The home page provides **Skill workflows**, **Behavioral evaluations**, and **Courses and learning progress**, alongside transcript, knowledge, support, simulation and expert workspaces. All business modules run in the same process and browser port, without additional runtime dependencies.

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

Establish a baseline in Behavioral evaluations, then run identical cases and rubrics against a preview release, candidate index or skill directory. Each case retrieves real evidence, calls the configured LLM for an answer and calls a model to judge relevance, grounding, task completion, language and boundaries.

The interface shows engineering checks, actual answers, citations, score explanations, fingerprints, baseline changes and human decisions. The editable default suite is `configs/theraskill/evaluations/behavior.json`. Its four cases provide bounded functional checks; production evaluation needs cases representing the intended tasks, users and boundary conditions.

A passing report requires complete cases, successful checks, dimension scores meeting thresholds, a comparable baseline with no excessive regression, and human approval. Approval cannot override failed checks. Changes to knowledge, index manifests, skills/references, LLM/embedding settings or the evaluator invalidate approval or use of old reports. Rerun evaluation after changes. Reports and review history live in `data/theraskill/evaluations/behavior/`.

Model judgments are machine opinions. High scores or no regression do not establish clinical efficacy or prove that a candidate skill is better. Human reviewers should inspect actual outputs and evidence.

## Courses and learning progress

As an instructor, create a course with objective IDs/descriptions and approved scenarios, then assign it to a learner label. Learners load their local assignments by label and enter practice from each scenario. Existing sessions can also be linked by ID; a session is counted once.

After practice, retain the debrief and record objective ratings, feedback and an approval or a request for more practice. Scenario completion requires valid dialogue from both roles, a complete debrief meeting the course gate, and instructor approval when required. All required scenarios must be completed. Session changes invalidate previous instructor approval. Research dual-agent runs and review-only sessions are excluded from learner completion.

The report shows observations over attempts, latest ratings and changes. A single observation does not establish improvement, and objectives with the same ID in different courses are kept separate. Ratings are not causal learning effects or evidence of clinical competence. Learner labels are local teaching groups using existing local roles, not a new identity system.

## Languages and persistence

Switch Chinese/English in the interface; the preference is saved locally. Reviews, support responses, generation, task questions and results receive an output language. Transcript source language is independent of review language. Source text, evidence quotations, existing user content and JSON field names are preserved. Historical model outputs keep their original language.

Back up the writable project, including tasks, evaluation reports, course databases, sessions, `skills/local/`, configuration and knowledge/index resources. Restart the single application after code upgrades. Preserve data directories. Full knowledge releases continue to be distributed separately from source.
