export type Scenario = {
  revision?: number
  grounding_status?: string
  generation_workflow?: {
    mode?: string
    stages?: Array<{ stage?: string; status?: string; error?: string }>
    review_status?: string
    repair_requested?: boolean
    issues?: Array<{ code?: string; severity?: string; detail?: string }>
  }
  parent_scenario_id?: string
  scenario_id: string
  scenario_family: string
  case_id?: string
  case_family?: string
  difficulty_band?: string
  risk_band?: string
  language?: SessionLanguage
  scenario_brief?: string
  scenario_tags: string[]
  recommended_turn_budget?: number
  split_labels: Record<string, string>
  generated?: boolean
  synthetic?: boolean
  review_status?: 'draft' | 'approved' | 'rejected' | string
  review_history?: Array<{ action: string; actor_role?: string; reason?: string; at?: string }>
  generated_at?: string
  batch_id?: string
  batch_index?: number
  batch_size?: number
  quality_report?: {
    status: 'pass' | 'warn' | 'fail' | string
    score?: number
    duplicate_of?: string | null
    duplicate_similarity?: number | null
    checks?: Array<{ name: string; status: string; detail?: string }>
  }
  case_bundle_path?: string
  learner_role_card?: {
    display_name?: string
    age_band?: string
    gender?: string
    occupation?: string
    marital_status?: string
    surface_presenting_problems?: string[]
    current_context?: string
    training_objectives?: string[]
  }
}

export type PatientFact = { content: string; sensitivity: 'surface' | 'moderate' | 'sensitive' }
export type PatientLifeEvent = PatientFact & { event_id: string; time_label: string; impact: string }
export type PatientDossier = {
  display_name: string; age: string; gender: string; occupation: string; relationship_status: string
  presenting_concern: string; background: PatientFact[]; recent_events: PatientLifeEvent[]
  hidden_concerns: PatientFact[]; current_emotions: string[]; goals: string[]; training_objectives: string[]
  communication_style: string; disclosure_preferences: string
}

export type SessionLanguage = 'zh' | 'en' | 'mixed'

export type TranscriptTurn = {
  turn_id: string
  turn_index: number
  role: string
  text: string
  created_at?: string
}

export type Review = {
  status: string
  gate_status?: string
  summary: Record<string, unknown> & { evidence_coverage?: { complete?: boolean; source_turn_count?: number; covered_character_count?: number; source_character_count?: number } }
  findings: Array<Record<string, unknown>>
}

export type SessionView = {
  schema_version: string
  session_id: string
  mode: string
  language: SessionLanguage
  status: string
  scenario: Scenario
  current_turn_index: number
  transcript: TranscriptTurn[]
  progress: Record<string, number>
  review?: Review | null
  created_at: string
  updated_at: string
}

export type SessionExport = {
  schema_version: string
  scope: string
  public_view: SessionView
  private_patient_state?: Record<string, unknown>
  private_therapist_state?: Record<string, unknown>
  instructor_persona_reveal?: {
    persona_id?: string
    reference_identity?: {
      display_name?: string
      demographic_profile?: Record<string, string>
    }
    hidden_fact_nodes?: Array<{
      fact_id: string
      content: string
      sensitivity: string
      facilitating_conditions: string[]
      blocking_conditions: string[]
    }>
    behavioral_principles?: Array<Record<string, unknown>>
  }
  event_count: number
}

export type TurnAnnotation = {
  annotation_id: string
  session_id: string
  turn_id: string
  comment: string
  alternative_response?: string | null
  created_at: string
}

export type TrainingAssignment = {
  assignment_id: string
  title: string
  description: string
  scenario_ids: string[]
  assignee: string
  status: string
  created_at: string
}

export type ResponseCandidate = {
  candidate_id: string
  framework: string
  strategy: string
  response: string
  rationale: string
  applicable_context: string
  limitations: string
  evidence?: Array<{ unit_id: string; quote: string }>
}

export type ResponseComparison = {
  status?: string
  comparison_id: string
  session_id: string
  target_turn_id: string
  candidates: ResponseCandidate[]
  model_config_id: string
  prompt_release_id: string
  knowledge_release_id: string
  evidence_unit_ids: string[]
  created_at: string
}

export type CallerRole = 'human_therapist' | 'human_client_role' | 'instructor' | 'researcher'

export type InteractiveMode =
  | 'human_therapist_vs_patient'
  | 'human_client_role_vs_therapist'
  | 'dual_agent_research'
  | 'review_only'

export type FeedbackConsent = {
  content_review: boolean
  research_use: boolean
  knowledge_derivation: boolean
}

export type ContributionSummary = {
  contribution_id: string
  contribution_type: string
  origin: { product: string; session_id?: string | null }
  target: { target_type: string; target_id: string }
  privacy_status: string
  workflow_status: string
  created_at: string
}
