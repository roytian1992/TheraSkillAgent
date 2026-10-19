# Tool inputs

`theraskill_patient` accepts `operation`, optional `scenario_id`, and `parameters`.
Build/generate parameters: `brief` (8–1200 characters, public), `language` (zh/en),
`scenario_family`, `difficulty_band` (low/medium/high), `risk_band`
(low/elevated/high), `scenario_tags`, `constraints`, `use_knowledge` (default true)
and `patient`. `patient` is required for build and optional for generate.

```json
{
  "display_name": "Alex",
  "age": "29",
  "gender": "",
  "occupation": "Designer",
  "relationship_status": "",
  "presenting_concern": "Work setbacks have made it difficult to switch off.",
  "background": [{"content": "Started this job six months ago.", "sensitivity": "surface"}],
  "recent_events": [{"event_id": "event_review", "time_label": "Last Friday", "content": "Received critical feedback on a project.", "impact": "Has been checking messages repeatedly.", "sensitivity": "moderate"}],
  "hidden_concerns": [{"content": "Worries that asking for help will disappoint others.", "sensitivity": "sensitive"}],
  "current_emotions": ["worried"],
  "goals": ["Understand what happens after receiving feedback"],
  "training_objectives": ["Practice validation and open exploration"],
  "communication_style": "Brief answers initially; avoid therapy jargon.",
  "disclosure_preferences": "Needs time and nonjudgmental questions before discussing personal fears."
}
```

Public identity and presenting concern are initially available. Each background
fact/event has `surface`, `moderate` or `sensitive` sensitivity. Moderate facts
require an appropriate state and supportive exploration; sensitive facts require
deeper disclosure readiness. Hidden concerns cannot be surface. Event timing,
content and impact travel together through the context compiler. Unknown facts
stay unspecified. Events need unique IDs (assigned automatically if omitted).

Update parameters: `expected_revision`, complete `patient`, optionally
`scenario_brief`, `scenario_family`, `case_family`, `scenario_tags`,
`difficulty_band`, `risk_band`, `recommended_turn_budget` (2–12).
Fork parameters: optional public `brief`. Inspect needs no parameters.

The generated draft is editable and can be practiced by its author. A teacher
must approve it before it appears as a reusable learner scenario. Approval is
reset when the draft is edited. Checks are structural, not clinical validation.
