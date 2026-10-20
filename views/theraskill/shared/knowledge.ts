export type KnowledgeLocale = 'zh' | 'en'
export type KnowledgeValue = string | number | boolean | null | KnowledgeValue[] | { [key: string]: KnowledgeValue }
export type KnowledgeSection = { key: string; label: string; value: KnowledgeValue }
export const knowledgeFields = ['canonical_label', 'summary', 'applicable_context', 'evidence_source', 'allowed_use', 'forbidden_use', 'contraindications'] as const
export type KnowledgeField = typeof knowledgeFields[number]
export type KnowledgeFieldValue = string | string[]
export type KnowledgeJob = {
  id: string; status: string; revision?: number; progress?: Record<string, unknown>; error?: string
  result?: { index_directory_id?: string; index_id?: string; counts?: Record<string, number>; activation_required?: boolean }
  resources_url?: string
}
export type KnowledgeDetail = Partial<Record<KnowledgeField, KnowledgeValue>> & {
  release_unit_id?: string; retrieval_id?: string; revision: number; index_id: string; active_index_id?: string
  knowledge_type?: string; primary_dataset?: string; primary_language?: string; risk_level?: string
  source?: { kind?: string; status?: string; available?: boolean; knowledge_sha256?: string; error?: string }
  indexed_passage?: string; indexed_values?: Partial<Record<KnowledgeField, KnowledgeValue>>
  sections?: KnowledgeSection[]; indexed_sections?: KnowledgeSection[]; edited_fields?: string[]; editable_fields?: string[]
  index_status?: string; index_mode?: string; mode?: string; can_edit?: boolean; can_reindex?: boolean
  activation_required?: boolean; reindex_job?: KnowledgeJob | null; editor_url?: string
}
export type KnowledgeCitation = { excerpt?: string; label?: string; dataset?: string; source_index_id?: string; index_id?: string; knowledge_sha256?: string }
const API = (import.meta.env.VITE_SUPPORT_API_URL || '/api/plugins/theraskill-support').replace(/\/$/, '')
export class KnowledgeRequestError extends Error { status: number; constructor(status: number, detail: string) { super(detail); this.status = status } }
function headers() { return { 'Content-Type': 'application/json', 'X-Support-Role': 'user', 'X-Support-User': localStorage.getItem('theraskill_support_user') || 'local-user' } }
async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(API + '/knowledge/' + path, { ...options, headers: { ...headers(), ...(options.headers || {}) } })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const message = typeof data.detail === 'string' ? data.detail : Array.isArray(data.detail) ? data.detail.map((item: { msg?: string }) => item.msg || '').filter(Boolean).join('; ') : response.statusText
    throw new KnowledgeRequestError(response.status, message || 'HTTP ' + response.status)
  }
  return data as T
}
export function getKnowledge(id: string, indexId?: string, signal?: AbortSignal) { return request<KnowledgeDetail>(encodeURIComponent(id) + (indexId ? '?index_id=' + encodeURIComponent(indexId) : ''), { signal }) }
export function saveKnowledge(id: string, baseline: KnowledgeDetail, changes: Partial<Record<KnowledgeField, KnowledgeFieldValue>>) {
  return request<KnowledgeDetail>(encodeURIComponent(id), { method: 'PATCH', body: JSON.stringify({ ...changes, expected_revision: baseline.revision, expected_index_id: baseline.index_id }) })
}
export function rebuildKnowledge(id: string, baseline: KnowledgeDetail) {
  return request<{ job_id: string; status: string; index_directory_id?: string; activation_required: boolean }>(encodeURIComponent(id) + '/reindex', { method: 'POST', body: JSON.stringify({ expected_revision: baseline.revision, expected_index_id: baseline.index_id }) })
}
export function knowledgeHref(id: string, indexId?: string, checksum?: string) {
  const params = new URLSearchParams({ workspace: 'knowledge', knowledge_id: id })
  if (indexId) params.set('index_id', indexId)
  if (checksum) params.set('knowledge_sha256', checksum)
  return '/?' + params
}
