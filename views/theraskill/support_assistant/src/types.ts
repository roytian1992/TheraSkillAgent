export type GenerationStatus = 'idle' | 'pending' | 'failed'
export type SessionStatus = 'active' | 'completed'
export type MessageRole = 'user' | 'assistant'

export interface EvidenceSummary {
  source_id?: string
  /** Stable release unit id used to open the governed knowledge entry. */
  unit_id?: string
  dataset?: string
  excerpt?: string
  label: string
  role: string
  source_type?: string
  retrieval_mode?: string
  used_for?: string
  boundary?: string
  updated_at?: string
  inference?: string
  editor_url?: string
  index_id?: string
  source_index_id?: string
  knowledge_sha256?: string
}

/** Public retrieval status for one assistant turn; no report paths or raw text. */
export interface RetrievalSummary {
  retrieval_mode?: string
  retrieval_skipped?: boolean
  skip_reason?: string | null
  hit_count?: number
  source_index_id?: string | null
  knowledge_sha256?: string | null
}

export interface SupportMessage {
  schema_version: string
  message_id: string
  sequence: number
  role: MessageRole
  text: string
  presentation_mode: string
  structured_content: Record<string, unknown>
  evidence_summary: EvidenceSummary[]
  retrieval_summary: RetrievalSummary
  created_at: string
}

export interface SupportSafety {
  attention_level: 'none' | 'attention' | 'urgent'
  source_risk_level: string
  signal_codes: string[]
  handoff_status: string
  last_action?: string | null
  check_in_status: string
  check_in_at?: string | null
}

export interface SupportGoal {
  goal_id: string
  text: string
  status: string
  created_at: string
  updated_at: string
}

export interface SupportPlan {
  goals: SupportGoal[]
  action_items: string[]
  next_check_in_at?: string | null
  last_check_in_at?: string | null
  last_check_in_note?: string | null
}

export interface SupportSession {
  schema_version: string
  session_id: string
  status: SessionStatus
  title: string
  messages: SupportMessage[]
  generation: {
    status: GenerationStatus
    job_id: string | null
    error_code: string | null
    retryable: boolean
  }
  safety: SupportSafety
  plan: SupportPlan
  created_at: string
  updated_at: string
}

export interface SubmitMessageResult {
  schema_version: string
  session: SupportSession
  job_id: string
  response_pending: boolean
}

export interface FeedbackConsent {
  content_review: boolean
  research_use: boolean
  knowledge_derivation: boolean
}

export interface ContributionSummary {
  contribution_id: string
  contribution_type: string
  origin: {
    product: string
    session_id?: string | null
  }
  target: {
    target_type: string
    target_id: string
  }
  privacy_status: string
  workflow_status: string
  created_at: string
}

export interface DomainMemory {
  memory_id: string
  memory_type: string
  content: string
  confirmation_status: 'proposed' | 'active' | 'rejected' | 'superseded' | 'expired' | 'deleted'
  sensitivity_level: 'standard' | 'sensitive'
  expires_at: string
  updated_at: string
}
