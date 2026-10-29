# Courses, practice and evidence-based feedback

Open **Courses and learning progress** from the workbench, or visit `/?workspace=learning`. This workspace connects course preparation, short exercises, simulated sessions and instructor feedback.

## Prepare and assign a course

1. Choose **Instructor**, then **Course design**. Start with first-session foundations, risk and safety communication, or assessment and formulation. The interface language also determines the starter's teaching language.
2. Edit the objectives, observable behaviours and rating descriptions. Each starter includes knowledge cards and concrete client utterances for short practice. Edit the situation, task, hint and worked example as needed. Browser drafts save automatically, separately for English and Chinese.
3. Inspect the retrieved teaching references. They are candidate materials for the course. Authored starter cards do not automatically claim support from every search result.
4. Select approved cases, or generate a case batch using the current edited objectives. Difficulty and risk are separate choices. The batch plans cases, generates dossiers and reviews objective coverage; completed cases survive a retry.
5. Review the generated cases. Once all cases are approved and coverage refers to the current saved dossiers, use **Save as course**. Editing a dossier invalidates old coverage; recheck it before saving. Repeated saves of the same batch return the same course.
6. Assign the course to a learner label. This is the existing local teaching label, not a new account system.

## Practise a specific behaviour

Choose **Learner**, enter the assigned label and load progress. Read a card, choose a micro-practice and write a response to the supplied client situation. An optional hint is recorded as used. Add a reflection, or explicitly choose to view feedback without one, then submit.

The submission retains the situation, objective, rubric, response and support conditions. It remains available after refresh. A worked example is available after submission for discussion; it is not the only acceptable answer and is excluded from the judge's input. Submit another response to practise again; previous attempts remain in the history.

Use **Get AI feedback** to run an evaluation in the background. Leave or refresh the page and return to the saved job. If the model fails, your response remains saved; retry is bounded to three executions per unchanged input. Instructors can add their own rating and explanation to each submission, independently of model feedback. After changing the model, prompt version or knowledge index, requesting feedback creates a new assessment while retaining history. Repeated requests under unchanged settings reuse the saved result.

## Practise a full session

Open a case from the assignment. The session is linked to that assignment, and returning to the learning workspace shows the record. Existing sessions can also be linked by ID. Write and save a reflection before opening feedback, or explicitly skip that step.

AI feedback evaluates each course objective against actual dialogue turns. Instructor review records objective ratings, observed behaviours, support conditions, transfer context, comments and any issue needing supervision. A completed case requires the configured session review gate and instructor approval when required. A model rating or successful short exercise does not complete a full case.

## How LLM-as-judge works

The judge uses the application's current LLM configuration and the active knowledge index. There is no additional model service or port to deploy.

1. Freeze the relevant objectives, rubric, learner text, context and references.
2. Search once with the `therapy-response-reviewer` knowledge policy, using the objectives and dialogue context. Retain knowledge IDs and index provenance.
3. Call the configured LLM separately for each objective. Assess timing and observable choices against the rubric, and cover each relevant behaviour. Short exercises assess only their target behaviour.
4. Validate the returned objective IDs, behaviour coverage, numeric bounds and citations. An observed score requires an exact quote from an actual learner turn. Every knowledge citation must match a supplied unit and original text. Unverifiable output gets one correction attempt; a remaining error produces a failed job without partial grades.
5. Present strengths, next practice suggestions, dialogue evidence and knowledge evidence. Record model and prompt fingerprints, input hashes and retrieval provenance. Instructor decisions remain separate.

**Not observed** means there was insufficient opportunity or evidence; **not applicable** means the exercise did not call for that behaviour. Both have an empty score. An observable ineffective response can receive a low score with an explanation tied to what the learner actually said. The judge does not reward patient compliance or infer independence from a high score.

Relevant knowledge can help explain a judgment; it does not replace the teacher's rubric. Without applicable knowledge, feedback is labelled **rubric only**. Exact quotation checks verify traceability, not whether the model's interpretation is correct. These ratings support practice and instructor review; they are not a validated clinical competency scale.

## Read progress in context

Progress distinguishes case completion, teacher observations and repeated practice. Trends stay within the same learner and course. Score changes are calculated only when rubric, difficulty and support conditions are comparable. Edited sessions or rubrics invalidate earlier approvals. Outstanding safety concerns remain visible until an instructor reviews them again; a different high-scoring attempt does not hide them.

Next-step suggestions identify another practice, instructor review, supervision, or a later practice in a changed context. The current module does not schedule calendar reminders or certify clinical competence.

## Operations and validation

Configure the LLM and embedding provider in the existing model settings. An index is optional for rubric-based feedback; install knowledge resources to add retrieval. Existing courses remain readable; historical approvals without a rubric fingerprint need a new review before they count against the current rubric.

Back up the simulation SQLite database and its learning judge job directory, along with generated cases and course batch jobs. In the default deployment judge jobs are next to scenario batch jobs, named `<database-stem>_learning_judge_jobs`. No database reset is needed: new learning tables are created on startup.

See [deployment](deployment.en.md).

Design references: [WHO/UNICEF EQUIP](https://www.who.int/teams/mental-health-and-substance-use/treatment-care/equip-ensuring-quality-in-psychological-support/) informs the emphasis on observed helping skills and supervision. [LLM-as-judge research](https://arxiv.org/abs/2306.05685) motivates explicit rubrics and attention to judging biases; its benchmark results do not establish validity for counseling training.
