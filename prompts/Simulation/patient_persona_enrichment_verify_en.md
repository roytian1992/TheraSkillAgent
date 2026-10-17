# Patient Persona v2 Patch Verifier

Verify a targeted patient-persona patch against the supplied evidence summary, forbidden-generation boundaries, and candidate patch.

Check identity, relationships, history, symptoms, risks, timeline consistency, evidence origin, sensitivity, enabling/blocking conditions, observable behavior rules, natural-variation examples, and disclosure pacing. Do not request facts that are outside `target_slots`.

Allowed origins are `evidence_derived`, `bounded_inference`, and `synthetic_fill`. Synthetic fill is permitted only for wording variation without new facts. Conservative omission is correct when source evidence conflicts.

Return JSON only:
```json
{"case_id":"same as input","verdict":"pass|revise","issues":[{"code":"stable_english_code","field":"field.path","detail":"short reason"}],"retry_fields":[]}
```
