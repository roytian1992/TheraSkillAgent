# Patient State Memory Update

Update patient-state memory from the current turn and the prior state. Record only changes supported by the transcript and evidence.

Keep concerns, trust, disclosure pressure, motivation, uncertainty, and active goals separate. Preserve the acquisition source and time for each item. Do not infer that an absent belief means the patient was unaware. Do not add diagnoses, major history, or future events.

Return the exact requested JSON object. Use `unknown`, `unchanged`, or empty arrays when the evidence does not support a change.
