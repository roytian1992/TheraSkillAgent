import type {
  CallerRole,
  ContributionSummary,
  FeedbackConsent,
  InteractiveMode,
  ResponseComparison,
  Scenario,
  SessionExport,
  SessionLanguage,
  SessionView,
  TrainingAssignment,
  TurnAnnotation,
  PatientDossier,
} from './types'

const API_URL = (import.meta.env.VITE_SIMULATION_API_URL || '/api/plugins/theraskill-simulation').replace(/\/$/, '')

function userId() {
  return window.localStorage.getItem('theraskill_simulation_user') || 'local-user'
}

async function request<T>(
  path: string,
  role: CallerRole,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-Simulation-Role': role,
      'X-Simulation-User': userId(),
      ...(options.headers || {}),
    },
  })
  if (!response.ok) {
    const text = await response.text()
    let detail = text || response.statusText
    try { const payload = JSON.parse(text); detail = typeof payload.detail === 'string' ? payload.detail : JSON.stringify(payload.detail || payload) } catch { /* A proxy may return plain text. */ }
    throw new Error(detail || `Request failed: ${response.status}`)
  }
  return response.json() as Promise<T>
}

export async function health(): Promise<boolean> {
  try {
    const response = await fetch(`${API_URL}/health`)
    return response.ok
  } catch {
    return false
  }
}

export async function listScenarios(
  role: CallerRole,
  filters: { family?: string; risk?: string; difficulty?: string; search?: string; language?: SessionLanguage },
): Promise<Scenario[]> {
  const params = new URLSearchParams({ split_value: filters.language === 'en' ? 'pilot_train' : 'test', limit: '200' })
  if (filters.language === 'en') params.set('split_key', 'pilot_split')
  if (filters.language) params.set('language', filters.language)
  if (filters.family) params.set('family', filters.family)
  if (filters.risk) params.set('risk_band', filters.risk)
  if (filters.difficulty) params.set('difficulty_band', filters.difficulty)
  if (filters.search) params.set('search', filters.search)
  const result = await request<{ scenarios: Scenario[] }>(`/scenarios?${params}`, role)
  return result.scenarios
}

export async function generateScenario(
  role: CallerRole,
  brief: string,
  scenarioFamily = 'supportive_exploration',
  difficultyBand = 'medium',
  riskBand = 'low',
  language: SessionLanguage = 'zh',
): Promise<{ scenario: Scenario; draft: Scenario; quality?: ScenarioQualityReport }> {
  return request('/scenarios/generate', role, {
    method: 'POST',
    body: JSON.stringify({
      brief,
      scenario_family: scenarioFamily,
      difficulty_band: difficultyBand,
      risk_band: riskBand,
      language,
    }),
  })
}

export type PatientDesign = { patient: PatientDossier; revision: number; grounding: { status: string; reason?: string; index_id?: string; items?: Array<{ retrieval_id: string; content: string }> }; scenario: Scenario }
export const inspectPatient = (role: CallerRole, id: string) => request<PatientDesign>(`/scenarios/${encodeURIComponent(id)}/patient`, role)
export const forkPatient = (role: CallerRole, id: string) => request<{ scenario: Scenario }>(`/scenarios/${encodeURIComponent(id)}/fork`, role, { method: 'POST', body: '{}' })
export const buildPatient = (role: CallerRole, payload: Record<string, unknown>, generate: boolean) => request<{ scenario: Scenario }>(generate ? '/scenarios/generate' : '/scenarios/build-patient', role, { method: 'POST', body: JSON.stringify(payload) })
export type PatientJob = { job_id: string; status: string; error?: string; request: Record<string, unknown>; progress: { processed: number; total: number; stage?: string; stages?: Array<{ stage: string; status: string; error?: string }> }; result?: { scenario: Scenario } }
export const startPatientJob = (role: CallerRole, payload: Record<string, unknown>) => request<PatientJob>('/scenarios/generate/jobs', role, { method: 'POST', body: JSON.stringify(payload) })
export const getPatientJob = (role: CallerRole, id: string) => request<PatientJob>(`/scenarios/generate/jobs/${encodeURIComponent(id)}`, role)
export const listPatientJobs = (role: CallerRole) => request<{ jobs: PatientJob[] }>('/scenarios/generate/jobs', role)
export const retryPatientJob = (role: CallerRole, id: string) => request<PatientJob>(`/scenarios/generate/jobs/${encodeURIComponent(id)}/retry`, role, { method: 'POST' })
export const checkGeneratedScenario = (role: CallerRole, id: string) => request<{ scenario: Scenario; quality: ScenarioQualityReport }>(`/scenarios/generated/${encodeURIComponent(id)}/check`, role, { method: 'POST' })

export type ScenarioQualityReport = {
  status: 'pass' | 'warn' | 'fail' | string
  score?: number
  duplicate_of?: string | null
  duplicate_similarity?: number | null
  checks?: Array<{ name: string; status: string; detail?: string }>
}

export async function updateGeneratedScenario(
  role: CallerRole,
  scenarioId: string,
  patch: Record<string, unknown>,
): Promise<{ scenario: Scenario; draft: Scenario; quality?: ScenarioQualityReport }> {
  return request(`/scenarios/generated/${encodeURIComponent(scenarioId)}`, role, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}

export async function reviewGeneratedScenario(
  role: CallerRole,
  scenarioId: string,
  action: 'approve' | 'reject',
  reason = '',
  overrideModelReview = false,
): Promise<{ scenario: Scenario; quality?: ScenarioQualityReport; review: { status: string } }> {
  return request(`/scenarios/generated/${encodeURIComponent(scenarioId)}/review`, role, {
    method: 'POST',
    body: JSON.stringify({ action, reason, override_model_review: overrideModelReview }),
  })
}

export type BatchJob = { job_id: string; status: string; course_handoff?: { ready: boolean; reasons: string[]; expected_count: number; current_count: number; approved_count: number; coverage_current: boolean; course_id: string | null }; progress: { processed: number; total: number; stage?: string; case_index?: number }; error?: string; result?: { scenarios: Scenario[]; count: number; course_draft: { title: string; description?: string; scenario_ids: string[]; skill_tags?: string[]; template_id?: string; competency_ids?: string[]; scaffolding?: string[]; learning_objectives?: Array<Record<string, unknown>>; knowledge_references?: Array<Record<string, unknown>>; design_retrieval?: Record<string, unknown> }; course_plan?: { objectives: string[]; cases: Array<{ index: number; learning_objective: string; brief: string }> }; course_review?: { status: string; coverage?: Array<{ objective: string; covered: boolean; case_indices: number[]; rationale: string }>; deterministic_checks?: { issues: Array<{ detail: string }> }; semantic_review?: { issues?: string[]; summary?: string; error?: string } } } }

export const getBatchJob = (role: CallerRole, id: string) => request<BatchJob>(`/scenarios/generate-batch/jobs/${id}`, role)
export const retryBatchJob = (role: CallerRole, id: string) => request<BatchJob>(`/scenarios/generate-batch/jobs/${id}/retry`, role, { method: 'POST' })
export const createCourseFromBatch = (role: CallerRole, id: string) => request<Record<string, unknown>>('/courses/from-batch', role, { method: 'POST', body: JSON.stringify({ batch_id: id }) })
export const listBatchJobs = (role: CallerRole) => request<{ jobs: BatchJob[] }>('/scenarios/generate-batch/jobs', role)
export const retrySessionUpdate = (role: CallerRole, id: string) => request<SessionView>(`/sessions/${id}/updates/retry`, role, { method: 'POST' })
export const retrySessionReview = (role: CallerRole, id: string) => request<SessionView>(`/sessions/${id}/review/retry`, role, { method: 'POST' })

export async function generateScenarioBatch(
  role: CallerRole,
  payload: {
    course_title: string
    course_description: string
    skill_tags: string[]
    brief: string
    count: number
    language?: SessionLanguage
    scenario_family?: string
    difficulty_band?: string
    risk_band?: string
  },
): Promise<BatchJob> {
  return request('/scenarios/generate-batch/jobs', role, {
    method: 'POST',
    body: JSON.stringify({ language: 'zh', ...payload }),
  })
}

export async function listGeneratedScenarios(role: CallerRole, language: SessionLanguage = 'zh'): Promise<Scenario[]> {
  const params = new URLSearchParams({ limit: '200', language })
  const result = await request<{ scenarios: Scenario[] }>(`/scenarios/generated?${params}`, role)
  return result.scenarios
}

export async function listSessions(role: CallerRole): Promise<SessionView[]> {
  const result = await request<{ sessions: SessionView[] }>('/sessions?limit=100', role)
  return result.sessions
}

export async function createSession(
  role: CallerRole,
  scenarioId: string,
  mode: InteractiveMode,
  language: SessionLanguage = 'zh',
): Promise<SessionView> {
  return request('/sessions', role, {
    method: 'POST',
    body: JSON.stringify({
      scenario_id: scenarioId,
      mode,
      language,
    }),
  })
}

export async function createReviewSession(
  role: CallerRole,
  sourceSessionId: string,
  scenarioId: string,
  language: SessionLanguage = 'zh',
): Promise<SessionView> {
  return request('/sessions', role, {
    method: 'POST',
    body: JSON.stringify({
      scenario_id: scenarioId,
      mode: 'review_only',
      language,
      source_session_id: sourceSessionId,
    }),
  })
}

export async function getSession(role: CallerRole, sessionId: string): Promise<SessionExport> {
  return request(`/sessions/${encodeURIComponent(sessionId)}`, role)
}

export async function generatePatientOpening(sessionId: string): Promise<void> {
  await request(`/sessions/${encodeURIComponent(sessionId)}/patient-opening`, 'human_therapist', {
    method: 'POST',
    headers: { 'Idempotency-Key': `opening-${sessionId}` },
  })
}

export async function submitTurn(
  role: CallerRole,
  sessionId: string,
  text: string,
): Promise<{ state_update_pending: boolean; deferred_update_id?: string }> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/turns`, role, {
    method: 'POST',
    headers: { 'Idempotency-Key': crypto.randomUUID() },
    body: JSON.stringify({ text, actor: role }),
  })
}

export async function runDualCycle(
  sessionId: string,
  initialTherapistUtterance?: string,
): Promise<Record<string, unknown>> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/dual-cycle`, 'researcher', {
    method: 'POST',
    headers: { 'Idempotency-Key': crypto.randomUUID() },
    body: JSON.stringify({ initial_therapist_utterance: initialTherapistUtterance || null }),
  })
}

export async function sessionAction(
  role: CallerRole,
  sessionId: string,
  action: 'pause' | 'resume' | 'complete',
): Promise<SessionView> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/${action}`, role, {
    method: 'POST',
  })
}

export async function submitFeedback(
  role: CallerRole,
  sessionId: string,
  rating: number,
  comment: string,
  dimensionRatings: Record<string, number>,
  consent: FeedbackConsent,
): Promise<void> {
  await request(`/sessions/${encodeURIComponent(sessionId)}/feedback`, role, {
    method: 'POST',
    headers: { 'Idempotency-Key': crypto.randomUUID() },
    body: JSON.stringify({
      rating,
      dimension_ratings: dimensionRatings,
      comment: comment.trim() || null,
      consent,
    }),
  })
}

export async function listAnnotations(
  role: CallerRole,
  sessionId: string,
): Promise<TurnAnnotation[]> {
  const result = await request<{ annotations: TurnAnnotation[] }>(
    `/sessions/${encodeURIComponent(sessionId)}/annotations`, role,
  )
  return result.annotations
}

export async function addAnnotation(
  sessionId: string,
  turnId: string,
  comment: string,
  alternativeResponse: string,
): Promise<TurnAnnotation> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/annotations`, 'instructor', {
    method: 'POST',
    headers: { 'Idempotency-Key': crypto.randomUUID() },
    body: JSON.stringify({
      turn_id: turnId,
      comment,
      alternative_response: alternativeResponse.trim() || null,
    }),
  })
}

export async function listContributions(role: CallerRole): Promise<ContributionSummary[]> {
  const result = await request<{ contributions: ContributionSummary[] }>('/contributions?limit=100', role)
  return result.contributions
}

export async function withdrawContribution(role: CallerRole, contributionId: string): Promise<void> {
  await request(`/contributions/${encodeURIComponent(contributionId)}`, role, { method: 'DELETE' })
}

export async function submitTurnFlag(
  role: CallerRole,
  sessionId: string,
  turnId: string,
  category: string,
  comment: string,
  consent: FeedbackConsent,
): Promise<void> {
  await request(
    `/sessions/${encodeURIComponent(sessionId)}/turns/${encodeURIComponent(turnId)}/flag`,
    role,
    {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({ category, comment: comment.trim() || null, consent }),
    },
  )
}

export async function createAssignment(
  title: string,
  assignee: string,
  scenarioIds: string[],
): Promise<TrainingAssignment> {
  return request('/assignments', 'instructor', {
    method: 'POST',
    body: JSON.stringify({ title, assignee, scenario_ids: scenarioIds }),
  })
}

export async function listComparisons(
  role: CallerRole,
  sessionId: string,
): Promise<ResponseComparison[]> {
  const result = await request<{ comparisons: ResponseComparison[] }>(
    `/sessions/${encodeURIComponent(sessionId)}/comparisons`, role,
  )
  return result.comparisons
}

export async function createComparison(
  role: CallerRole,
  sessionId: string,
  targetTurnId: string,
): Promise<ResponseComparison> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/comparisons`, role, {
    method: 'POST',
    body: JSON.stringify({ target_turn_id: targetTurnId }),
  })
}
