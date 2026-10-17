---
name: transcript-evaluator
description: Coordinate evidence-grounded risk, therapeutic-process and dialogue-quality evaluation for normalized transcripts.
version: 0.1.0
---

# Transcript Evaluator

Use the normalized transcript bundle as the source of truth. Delegate focused
reviews to `risk-triage`, `therapy-response-reviewer` and other approved skills
when their triggers are present. Keep observed text, model inference,
recommendation and uncertainty in separate fields. Every score or flag must
cite one or more `turn_id` values.

Risk screening is a routing signal, not a diagnosis or final clinical risk
assessment. Model reviews remain candidates until a human reviewer accepts
them.
