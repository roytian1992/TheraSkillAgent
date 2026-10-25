# Course Agent design

Course setup no longer starts with a large form. An instructor can describe the teaching goal in plain language. The Agent asks only for missing decisions, shows the proposed plan, and produces an editable draft after explicit confirmation.

## One course-design flow

1. Open **Courses and learning progress** and use **Collaborative course design**. Describe what learners should practise, optionally including case count, difficulty, and risk. For example:

   > I want beginners to practise emotional validation in 3 low-risk cases.

2. The Agent extracts the goal and constraints. If the goal or case count is missing, it asks for that information without starting retrieval or generation.
3. When the essentials are present, the interface shows the topic, case count, difficulty, risk, and the next tool work: course blueprint, knowledge retrieval, and scenario batch generation.
4. After **Confirm and create course draft**, the existing blueprint service retrieves relevant knowledge and creates objectives, observable behaviours, knowledge cards, and micro-practices. This is an editable draft; it is not a saved course and it does not generate cases automatically.
5. The instructor can add constraints in the conversation or open **Advanced editing**. After objectives and learning units are reviewed, the instructor can generate scenario drafts, select approved cases, save the course, and assign it.

## Why this shape

Course goals, knowledge references, micro-practices, and scenarios depend on each other. Showing a plan before execution makes the Agent's next actions visible instead of letting one model call silently decide every setting. Keeping drafts separate from formal courses makes revision and rollback inexpensive.

## Agent, Skill, and Task boundaries

- The Course Agent collects intent, resolves constraints, asks questions, and orchestrates the next step.
- `course_blueprint` creates an editable course starting point from the goal and knowledge scope.
- `knowledge_retrieval` selects knowledge relevant to the course goals; risk review and response review use their own retrieval policies.
- `scenario_batch_generation` creates scenario batches after the course goals are confirmed.
- A Task records one generation, review, or batch run. The Course Agent does not create a Task for every chat message.

The current intent parser is deterministic and local, so it does not depend on a particular local model. Knowledge explanations and course content still use the configured model providers and retrieval workflow.

## Current boundary

The Course Agent collects the course goal, template, case count, difficulty, and risk, then passes those settings to the existing blueprint flow. Detailed edits to objectives, knowledge cards, and micro-practices remain in Advanced editing. A next extension can register common edits such as “add one practice” or “make this objective observable” as controlled tools that return a structured patch for instructor confirmation.

## Verification

Backend tests cover missing information, plan confirmation, English and Chinese input, and defaults. A browser regression covers the full path: send a description, show a plan, make sure no blueprint call happens before confirmation, then confirm and create the draft. The browser test uses synthetic API responses and does not call a real model.
