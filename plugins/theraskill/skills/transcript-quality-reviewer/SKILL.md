---
name: transcript-quality-reviewer
description: Review transcript structure, speaker attribution, ASR noise and evidence
  quality before downstream analysis.
version: 0.1.0
runtime:
  required_tools:
  - theraskill_transcript
---

# Transcript Quality Reviewer

Use after deterministic transcript normalization and before model-backed
clinical-process review. Check turn boundaries, speaker-role confidence,
missing or duplicated text, language consistency, timestamps and correction
suggestions. Cite `turn_id` and source line spans for every issue.

Do not infer a diagnosis or repair text without recording the original text,
the proposed change, confidence and reviewer decision.
