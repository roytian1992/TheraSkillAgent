# Patient State Transition Extraction

Extract supported patient-state transitions from the supplied dialogue and evidence bundle.

For every transition include the holder, prior state, new state, trigger turn or event, story time, confidence, and source reference. Separate observed statements from bounded interpretation. Keep contradictions explicit; do not resolve them by invention.

Return JSON only. Empty transitions are valid when no supported change is present.
