# Therapist Case-Memory Extraction

Extract therapist-facing case memory from the supplied session evidence.

Record only supported goals, alliance signals, interventions, unresolved topics, risk-monitoring points, and next-session focus. Preserve who observed each item, when it occurred, and whether it is direct evidence or bounded inference. Do not diagnose or convert a plan into an outcome.

Return the exact JSON schema. Use empty arrays and `unknown` for unsupported fields.
