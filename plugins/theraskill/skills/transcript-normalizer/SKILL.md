---
name: transcript-normalizer
description: Preserve and normalize noisy patient/clinician transcripts without overwriting
  raw evidence.
version: 0.1.0
runtime:
  required_tools:
  - theraskill_transcript
---

# Transcript Normalizer

Use this skill when a dialogue file may contain mixed languages, inconsistent
speaker labels, missing punctuation, or speech-to-text artifacts.

Workflow:

1. Call `theraskill_transcript` with `operation=inspect`.
2. Show the detected encoding, language, speaker labels and sample turns.
3. Ask the user to confirm or edit role mappings before analysis.
4. Call `operation=analyze` and retain raw, normalized and correction-candidate
   fields separately.

Never silently correct ASR text. Corrections must remain suggestions until a
reviewer explicitly accepts them.
