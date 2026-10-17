# Patient Persona v2 Enrichment

Produce only a bounded enrichment patch for the requested persona slots. Every factual item must be traceable to the supplied evidence. Mark derived items with `bounded_inference` and wording-only examples with `synthetic_fill`.

Do not invent diagnoses, comorbidity, self-harm, suicide, violence, abuse, trauma, medication, hospitalization, legal history, identity facts, relationships, or life events. Do not reconcile contradictory source facts. Preserve disclosure limits and uncertainty.

Return the requested JSON schema exactly, with empty arrays or `unknown` when evidence is absent. Do not output Markdown or explanations.
