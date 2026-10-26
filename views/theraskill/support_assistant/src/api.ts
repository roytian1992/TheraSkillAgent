import type { ContributionSummary, DomainMemory, FeedbackConsent, SubmitMessageResult, SupportSession, WorkspaceRoute } from './types'

const API_URL = (import.meta.env.VITE_SUPPORT_API_URL || '/api/plugins/theraskill-support').replace(/\/$/, '')
function userId() {
  return window.localStorage.getItem('theraskill_support_user') || 'local-user'
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-Support-User': userId(),
      'X-Support-Role': 'user',
      ...(options.headers || {}),
    },
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(payload.detail || `Request failed: ${response.status}`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function health(): Promise<boolean> {
  try {
    return (await fetch(`${API_URL}/health`)).ok
  } catch {
    return false
  }
}

export async function listSessions(): Promise<SupportSession[]> {
  const result = await request<{ sessions: SupportSession[] }>('/sessions?limit=100')
  return result.sessions
}

export async function createSession(): Promise<SupportSession> {
  return request('/sessions', { method: 'POST', body: JSON.stringify({output_language: window.localStorage.getItem('theraskill-locale') === 'en' ? 'en' : 'zh'}) })
}

export async function getSession(sessionId: string): Promise<SupportSession> {
  return request(`/sessions/${encodeURIComponent(sessionId)}`)
}

export async function submitMessage(
  sessionId: string,
  text: string,
): Promise<SubmitMessageResult> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/messages`, {
    method: 'POST',
    headers: { 'Idempotency-Key': crypto.randomUUID() },
    body: JSON.stringify({ text, output_language: window.localStorage.getItem('theraskill-locale') === 'en' ? 'en' : 'zh' }),
  })
}

export async function routeWorkspace(text: string): Promise<WorkspaceRoute> {
  return request('/workspace/route', {
    method: 'POST',
    body: JSON.stringify({ text, language: window.localStorage.getItem('theraskill-locale') === 'en' ? 'en' : 'zh' }),
  })
}

export async function retryResponse(sessionId: string): Promise<SubmitMessageResult> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/retry`, { method: 'POST' })
}

export async function completeSession(sessionId: string): Promise<SupportSession> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/complete`, { method: 'POST' })
}

export async function updatePlan(
  sessionId: string,
  value: { goals?: string[]; action_items?: string[]; next_check_in_at?: string | null },
): Promise<SupportSession> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/plan`, {
    method: 'PATCH',
    body: JSON.stringify(value),
  })
}

export async function recordSafetyAction(
  sessionId: string,
  action: 'trusted_person' | 'professional' | 'local_emergency' | 'continue_checkin' | 'decline',
): Promise<SupportSession> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/safety/action`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
}

export async function recordSafetyCheckIn(
  sessionId: string,
  safeNow: boolean,
  note?: string,
): Promise<SupportSession> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/safety/check-in`, {
    method: 'POST',
    body: JSON.stringify({ safe_now: safeNow, note: note || null }),
  })
}

export async function deleteSession(sessionId: string): Promise<void> {
  await request(`/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' })
}

export async function exportSession(sessionId: string): Promise<Record<string, unknown>> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/export?scope=public`)
}

export async function submitResponseFeedback(
  sessionId: string,
  messageId: string,
  value: {
    helpful: boolean
    reasons: string[]
    comment: string
    consent: FeedbackConsent
  },
): Promise<{ contribution: ContributionSummary; replayed: boolean }> {
  return request(
    `/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/feedback`,
    {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({ ...value, comment: value.comment.trim() || null }),
    },
  )
}

export async function listContributions(): Promise<ContributionSummary[]> {
  const result = await request<{ contributions: ContributionSummary[] }>('/contributions?limit=100')
  return result.contributions
}

export async function withdrawContribution(contributionId: string): Promise<void> {
  await request(`/contributions/${encodeURIComponent(contributionId)}`, { method: 'DELETE' })
}
export async function listMemories(): Promise<DomainMemory[]> {
  return (await request<{memories: DomainMemory[]}>('/memory')).memories
}
export async function proposeMemory(value: {memory_type:string;content:string;sensitivity_level:string;ttl_days:number}): Promise<DomainMemory> {
  return request('/memory/proposals',{method:'POST',body:JSON.stringify(value)})
}
export async function approveMemory(id:string,ttlDays:number): Promise<DomainMemory> {
  return request(`/memory/${encodeURIComponent(id)}/approve?ttl_days=${ttlDays}`,{method:'POST'})
}
export async function updateMemory(id:string,value:{content?:string;ttl_days?:number;sensitivity_level?:string}): Promise<DomainMemory> {
  return request(`/memory/${encodeURIComponent(id)}`,{method:'PATCH',body:JSON.stringify(value)})
}
export async function deleteMemory(id:string): Promise<DomainMemory> {
  return request(`/memory/${encodeURIComponent(id)}`,{method:'DELETE'})
}
