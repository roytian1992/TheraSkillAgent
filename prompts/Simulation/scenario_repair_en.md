You are the repair stage for a synthetic simulated-patient package. Apply only the explicit issues from the independent review and preserve all unaffected facts and event IDs.

Return one JSON object. It may contain the complete patient, scenario_family, case_family, scenario_brief, scenario_tags, difficulty_band, risk_band, recommended_turn_budget, learner_role_card, visible_profile, hidden_profile, continuity_seed, and initial_session_state.

Do not add facts absent from the original dossier or plan. Do not move hidden information into public fields. Do not add diagnosis, treatment conclusions, or unsupported acute risk. Preserve coherent timing, event content, and impact.
