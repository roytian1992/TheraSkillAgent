import { tr, useTranslationSubscription } from '../../shared/i18n'
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertCircle,
  BookMarked,
  BookOpen,
  Check,
  Download,
  ExternalLink,
  ListChecks,
  LoaderCircle,
  Menu,
  MessageCircle,
  MoreHorizontal,
  Plus,
  RefreshCw,
  Send,
  Square,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  X,
} from 'lucide-react'
import MemoryDrawer from './MemoryDrawer'
import KnowledgeEntry from '../../shared/KnowledgeEntry'
import {
  completeSession,
  createSession,
  deleteSession,
  exportSession,
  getSession,
  health,
  listContributions,
  listSessions,
  retryResponse,
  recordSafetyAction,
  recordSafetyCheckIn,
  submitMessage,
  submitResponseFeedback,
  updatePlan,
  withdrawContribution,
} from './api'
import type { ContributionSummary, EvidenceSummary, FeedbackConsent, SupportMessage, SupportSession } from './types'
import { useLocale } from './i18n'

function downloadJson(filename: string, value: unknown) {
  const blob = new Blob([`${JSON.stringify(value, null, 2)}\n`], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

function readableRole(role: string) {
  const labels: Record<string, string> = {
    process_guidance: tr('过程参考'),
    therapy_process_pattern: tr('互动模式'),
    psychoeducation_or_support_guidance: tr('支持参考'),
    method_rubric: tr('方法参考'),
    risk_boundary: tr('边界参考'),
  }
  return labels[role] || tr('参考依据')
}

function StructuredContent({ value }: { value: Record<string, unknown> }) {
  useTranslationSubscription()

  const entries = Object.entries(value).filter(([, item]) => item !== null && item !== '')
  if (!entries.length) return null
  return (
    <div className="structured-content">
      {entries.map(([key, item]) => (
        <div className="structured-row" key={key}>
          <span>{key.replaceAll('_', ' ')}</span>
          {Array.isArray(item) ? (
            <ul>{item.map((entry, index) => <li key={index}>{typeof entry === 'string' ? entry : JSON.stringify(entry)}</li>)}</ul>
          ) : typeof item === 'object' ? (
            <pre>{JSON.stringify(item, null, 2)}</pre>
          ) : (
            <p>{String(item)}</p>
          )}
        </div>
      ))}
    </div>
  )
}

function KnowledgeDetailDrawer({ item, onClose }: { item: EvidenceSummary | null; onClose: () => void }) {
  const { locale } = useLocale()
  const t = (zh: string, en: string) => locale === 'zh' ? zh : en
  const dialog = useRef<HTMLElement>(null)
  const close = useRef(onClose)
  const dirty = useRef(false)
  const [dismiss, setDismiss] = useState(false)
  close.current = onClose
  const requestClose = () => { if (dirty.current) setDismiss(true); else close.current() }
  useEffect(() => {
    if (!item) return
    const previous = document.activeElement as HTMLElement | null
    const bodyOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    dialog.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); if (dirty.current) setDismiss(true); else close.current(); return }
      if (event.key !== 'Tab' || !dialog.current) return
      const elements = Array.from(dialog.current.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],textarea:not(:disabled),input:not(:disabled),select:not(:disabled),summary,[tabindex="0"]')).filter(node => node.getClientRects().length > 0)
      const first = elements[0], last = elements[elements.length - 1]
      if (!first) { event.preventDefault(); dialog.current.focus(); return }
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) { event.preventDefault(); last.focus() }
      if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialog.current)) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('keydown', onKey); document.body.style.overflow = bodyOverflow; previous?.focus() }
  }, [item])
  if (!item) return null
  const unitId = item.unit_id || item.source_id || ''
  const indexId = item.source_index_id || item.index_id
  return <>
    <button className="drawer-scrim" tabIndex={-1} onClick={requestClose} aria-label={t('关闭知识详情', 'Close knowledge details')} />
    <aside ref={dialog} className="knowledge-drawer" tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="knowledge-detail-title">
      <header><div><span className="drawer-kicker">{t('参考依据', 'Evidence')}</span><h2 id="knowledge-detail-title">{item.label}</h2></div><button className="icon-button" onClick={requestClose} aria-label={t('关闭知识详情', 'Close knowledge details')}><X size={17} /></button></header>
      <div className="knowledge-drawer-body">
        {dismiss && <div className="ke-dismiss" role="alert"><p>{t('草稿还有未保存的修改。', 'Your draft has unsaved changes.')}</p><button onClick={() => setDismiss(false)}>{t('继续编辑', 'Keep editing')}</button><button onClick={() => close.current()}>{t('放弃修改并关闭', 'Discard changes and close')}</button></div>}
        {unitId ? <KnowledgeEntry key={[unitId,indexId,item.knowledge_sha256].join(':')} id={unitId} indexId={indexId} checksum={item.knowledge_sha256} citation={item} locale={locale} onDirtyChange={value => { dirty.current = value }} /> : <div className="ke-citation"><h3>{t('对话中保存的引用', 'Citation saved with the response')}</h3><blockquote>{item.excerpt || item.label}</blockquote><p>{t('该历史条目没有可定位的知识 ID。', 'This historical citation has no knowledge entry ID.')}</p></div>}
      </div>
    </aside>
  </>
}

const feedbackReasons = [
  ['not_understood', '没有理解我的意思'],
  ['not_relevant', '回答不相关'],
  ['not_practical', '建议不实际'],
  ['uncomfortable_expression', '表达让我不舒服'],
  ['possibly_inaccurate', '事实可能有误'],
  ['risk_handling_inappropriate', '风险处理不合适'],
  ['other', '其他'],
] as const

function FeedbackControls({ sessionId, messageId, onError, onSubmitted }: {
  sessionId: string
  messageId: string
  onError: (message: string) => void
  onSubmitted: () => void
}) {
  useTranslationSubscription()

  const [helpful, setHelpful] = useState<boolean | null>(null)
  const [reasons, setReasons] = useState<string[]>([])
  const [comment, setComment] = useState('')
  const [consent, setConsent] = useState<FeedbackConsent>({
    content_review: false,
    research_use: false,
    knowledge_derivation: false,
  })
  const [submitting, setSubmitting] = useState(false)
  const [submitted, setSubmitted] = useState(false)

  const choose = (value: boolean) => {
    setHelpful(value)
    if (value) setReasons([])
  }
  const toggleReason = (reason: string) => {
    setReasons((current) => current.includes(reason) ? current.filter((item) => item !== reason) : [...current, reason])
  }
  const setConsentValue = (key: keyof FeedbackConsent, checked: boolean) => {
    setConsent((current) => ({
      ...current,
      [key]: checked,
      ...(key === 'knowledge_derivation' && checked ? { content_review: true } : {}),
      ...(key === 'content_review' && !checked ? { knowledge_derivation: false } : {}),
    }))
  }
  const submit = async () => {
    if (helpful === null) return
    setSubmitting(true)
    try {
      await submitResponseFeedback(sessionId, messageId, { helpful, reasons, comment, consent })
      setSubmitted(true)
      onSubmitted()
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSubmitting(false)
    }
  }

  if (submitted) return <div className="feedback-recorded"><Check size={13} />{tr("已记录")}</div>
  return (
    <div className="response-feedback">
      <div className="feedback-trigger">
        <button className={helpful === true ? 'selected' : ''} onClick={() => choose(true)} title={tr("有帮助")}><ThumbsUp size={14} /></button>
        <button className={helpful === false ? 'selected' : ''} onClick={() => choose(false)} title={tr("没帮助")}><ThumbsDown size={14} /></button>
      </div>
      {helpful !== null && (
        <div className="feedback-form">
          {!helpful && (
            <div className="reason-grid">
              {feedbackReasons.map(([value, label]) => (
                <label key={value}>
                  <input type="checkbox" checked={reasons.includes(value)} onChange={() => toggleReason(value)} />
                  <span>{tr(label)}</span>
                </label>
              ))}
            </div>
          )}
          <textarea value={comment} onChange={(event) => setComment(event.target.value)} placeholder={tr("补充说明（可选）")} rows={2} maxLength={2000} />
          <div className="consent-options">
            <label><input type="checkbox" checked={consent.content_review} onChange={(event) => setConsentValue('content_review', event.target.checked)} />{tr("允许内容评审")}</label>
            <label><input type="checkbox" checked={consent.research_use} onChange={(event) => setConsentValue('research_use', event.target.checked)} />{tr("允许研究使用")}</label>
            <label><input type="checkbox" checked={consent.knowledge_derivation} onChange={(event) => setConsentValue('knowledge_derivation', event.target.checked)} />{tr("允许形成知识候选")}</label>
          </div>
          <button className="feedback-submit" onClick={submit} disabled={submitting}>
            {submitting ? <LoaderCircle className="spin" size={14} /> : <Check size={14} />}{tr("提交")}</button>
        </div>
      )}
    </div>
  )
}

function Message({ message, sessionId, onError, onFeedbackSubmitted, onOpenEvidence }: {
  message: SupportMessage
  sessionId: string
  onError: (message: string) => void
  onFeedbackSubmitted: () => void
  onOpenEvidence: (item: EvidenceSummary) => void
}) {
  useTranslationSubscription()
  const { locale } = useLocale()
  const t = (zh: string, en: string) => locale === 'zh' ? zh : en

  const isUser = message.role === 'user'
  return (
    <article
      className={`message ${isUser ? 'user-message' : 'assistant-message'}`}
      data-presentation-mode={message.presentation_mode}
    >
      <div className="message-meta">{isUser ? tr('你') : 'TheraSkill'}</div>
      <div className="message-body">
        <p>{message.text}</p>
        {!isUser && <StructuredContent value={message.structured_content} />}
        {!isUser && message.retrieval_summary?.retrieval_skipped && (
          <div className="retrieval-status" role="status">
            <BookOpen size={13} />
            <span>{t('本轮为简单对话，未检索知识', 'Lightweight turn; no knowledge retrieval was needed')}</span>
          </div>
        )}
        {!isUser && !message.retrieval_summary?.retrieval_skipped && message.retrieval_summary?.retrieval_mode && message.evidence_summary.length === 0 && (
          <div className="retrieval-status" role="status">
            <BookOpen size={13} />
            <span>{t('本轮未找到可用参考依据', 'No usable reference evidence was returned for this turn')}</span>
          </div>
        )}
        {!isUser && message.evidence_summary.length > 0 && (
          <details className="evidence-details">
            <summary><BookOpen size={15} />{tr("参考依据")} <span className="evidence-count">{message.evidence_summary.length}</span></summary>
            <div className="evidence-list">
              {message.evidence_summary.map((item, index) => (
                <button className="evidence-row" key={`${item.label}-${index}`} onClick={() => onOpenEvidence(item)} title={tr('查看知识详情')}>
                  <div>
                    <span className="evidence-row-title">{item.label.replaceAll('_', ' ')}</span>
                    {item.excerpt && <blockquote style={{whiteSpace:'pre-wrap'}}>{item.excerpt}</blockquote>}
                    {(item.dataset || item.source_id) && <small className="evidence-row-detail">{item.dataset || tr('知识库')} · {item.source_id ? `${item.source_id.slice(0, 18)}…` : ''}</small>}
                    {item.used_for && <small className="evidence-row-detail">{tr("用途：")}{item.used_for}</small>}
                    {item.boundary && <small className="evidence-row-detail">{tr("边界：")}{item.boundary}</small>}
                    {item.inference && <small className="evidence-row-detail">{item.inference}</small>}
                  </div>
                  <small className="evidence-row-role">{readableRole(item.role)}{item.source_type ? ` · ${item.source_type}` : ''}<ExternalLink size={12} /></small>
                </button>
              ))}
            </div>
          </details>
        )}
        {!isUser && (
          <FeedbackControls
            sessionId={sessionId}
            messageId={message.message_id}
            onError={onError}
            onSubmitted={onFeedbackSubmitted}
          />
        )}
      </div>
    </article>
  )
}

function SafetyAndPlanCard({ session, onSession, onError }: {
  session: SupportSession
  onSession: (session: SupportSession) => void
  onError: (message: string) => void
}) {
  useTranslationSubscription()

  const [goal, setGoal] = useState('')
  const [actionItem, setActionItem] = useState('')
  const [saving, setSaving] = useState(false)
  const { locale } = useLocale()
  const t = (zh: string, en: string) => locale === 'zh' ? zh : en
  const [editingPlan, setEditingPlan] = useState(false)
  const [editedGoals, setEditedGoals] = useState('')
  const [editedActions, setEditedActions] = useState('')
  const safety = session.safety
  const hasSafety = safety.attention_level !== 'none'
  const savePlan = async () => {
    const goals = [...session.plan.goals.map((item) => item.text), ...(goal.trim() ? [goal.trim()] : [])]
    const actionItems = [...session.plan.action_items, ...(actionItem.trim() ? [actionItem.trim()] : [])]
    if (!goal.trim() && !actionItem.trim()) return
    setSaving(true)
    try {
      onSession(await updatePlan(session.session_id, { goals, action_items: actionItems }))
      setGoal('')
      setActionItem('')
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSaving(false)
    }
  }
  const safetyAction = async (action: 'trusted_person' | 'professional' | 'local_emergency' | 'continue_checkin' | 'decline') => {
    setSaving(true)
    try {
      onSession(await recordSafetyAction(session.session_id, action))
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSaving(false)
    }
  }
  const checkIn = async (safeNow: boolean) => {
    setSaving(true)
    try {
      onSession(await recordSafetyCheckIn(session.session_id, safeNow))
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSaving(false)
    }
  }
  return (
    <section className={`support-plan-card ${hasSafety ? `safety-${safety.attention_level}` : ''}`}>
      {hasSafety && (
        <div className="safety-action-panel">
          <strong>{safety.attention_level === 'urgent' ? tr('现在需要优先确认安全') : tr('这段对话出现了需要关注的安全信号')}</strong>
          <p>{tr("请根据你的实际情况联系身边可信任的人、当地专业服务或当地急救资源。这些按钮用于记录你的选择，不会代你拨号或联系他人。")}</p>
          {safety.last_action && <p role="status">{t('已记录你的联系选择：', 'Your contact choice is recorded: ')}{safety.last_action}</p>}{safety.check_in_status !== 'not_requested' && <p role="status">{t('安全确认状态：', 'Check-in status: ')}{safety.check_in_status}</p>}
          <div className="safety-action-row">
            <button onClick={() => void safetyAction('trusted_person')} disabled={saving}>{tr("联系可信任的人")}</button>
            <button onClick={() => void safetyAction('professional')} disabled={saving}>{tr("联系专业人员")}</button>
            <button onClick={() => void safetyAction('local_emergency')} disabled={saving}>{tr("当地紧急服务")}</button>
            <button onClick={() => void checkIn(true)} disabled={saving}>{tr("我现在安全")}</button>
            <button onClick={() => void checkIn(false)} disabled={saving}>{tr("我现在仍不安全")}</button>
          </div>
        </div>
      )}
      <details>
        <summary>{t('我的目标与下一步', 'My goals and next steps')}</summary>
        <button onClick={() => { setEditedGoals(session.plan.goals.map(item=>item.text).join('\n')); setEditedActions(session.plan.action_items.join('\n')); setEditingPlan(!editingPlan) }}>{t('编辑已有目标与行动', 'Edit saved goals and actions')}</button>
        {editingPlan && <div className="support-plan-inputs"><label>{t('目标，每行一项', 'Goals, one per line')}<textarea value={editedGoals} onChange={event=>setEditedGoals(event.target.value)} /></label><label>{t('行动，每行一项', 'Actions, one per line')}<textarea value={editedActions} onChange={event=>setEditedActions(event.target.value)} /></label><button disabled={saving} onClick={async()=>{setSaving(true);try{onSession(await updatePlan(session.session_id,{goals:editedGoals.split('\n').filter(x=>x.trim()),action_items:editedActions.split('\n').filter(x=>x.trim())}));setEditingPlan(false)}catch(error){onError(String(error))}finally{setSaving(false)}}}>{t('保存修改', 'Save changes')}</button></div>}
        {session.plan.goals.length > 0 && <ul>{session.plan.goals.map((item) => <li key={item.goal_id}>{item.text}</li>)}</ul>}
        {session.plan.action_items.length > 0 && <ul>{session.plan.action_items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul>}
        <div className="support-plan-inputs">
          <input value={goal} onChange={(event) => setGoal(event.target.value)} placeholder={tr("添加一个我想关注的目标")} maxLength={500} />
          <input value={actionItem} onChange={(event) => setActionItem(event.target.value)} placeholder={tr("添加一个下一步行动")} maxLength={500} />
          <button onClick={() => void savePlan()} disabled={saving || (!goal.trim() && !actionItem.trim())}>{tr("保存")}</button>
        </div>
      </details>
    </section>
  )
}

export default function App() {
  useTranslationSubscription()

  const { locale, toggleLocale } = useLocale()
  const [sessions, setSessions] = useState<SupportSession[]>([])
  const [session, setSession] = useState<SupportSession | null>(null)
  const [draft, setDraft] = useState('')
  const [connected, setConnected] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth >= 760)
  const [contributionOpen, setContributionOpen] = useState(false)
  const [memoryOpen, setMemoryOpen] = useState(false)
  const [contributions, setContributions] = useState<ContributionSummary[]>([])
  const [selectedEvidence, setSelectedEvidence] = useState<EvidenceSummary | null>(null)
  const [contributionLoading, setContributionLoading] = useState(false)
  const transcriptEnd = useRef<HTMLDivElement>(null)

  const pending = session?.generation.status === 'pending'
  const failed = session?.generation.status === 'failed'
  const active = session?.status === 'active'

  const refreshList = async () => {
    const rows = await listSessions()
    setSessions(rows)
    return rows
  }

  useEffect(() => {
    let cancelled = false
    const requestedSessionId = new URLSearchParams(window.location.search).get('session_id')
    const initialize = async () => {
      try {
        setConnected(await health())
        let rows = await refreshList()
        if (!rows.length) {
          const created = await createSession()
          rows = [created]
          setSessions(rows)
        }
        if (!cancelled) {
          const requested = requestedSessionId
            ? await getSession(requestedSessionId).catch(() => null)
            : null
          setSession(requested || rows[0])
        }
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason))
      }
    }
    void initialize()
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!session || session.generation.status !== 'pending') return
    const sessionId = session.session_id
    const timer = window.setInterval(async () => {
      try {
        const latest = await getSession(sessionId)
        setSession(latest)
        if (latest.generation.status !== 'pending') {
          window.clearInterval(timer)
          await refreshList()
        }
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason))
      }
    }, 1000)
    return () => window.clearInterval(timer)
  }, [session?.session_id, session?.generation.status])

  useEffect(() => {
    transcriptEnd.current?.scrollIntoView({ behavior: 'smooth' })
  }, [session?.messages.length, pending])

  const orderedSessions = useMemo(
    () => [...sessions].sort((a, b) => b.updated_at.localeCompare(a.updated_at)),
    [sessions],
  )

  const startNew = async () => {
    try {
      setBusy(true)
      setError('')
      const created = await createSession()
      setSession(created)
      setSessions((current) => [created, ...current])
      if (window.innerWidth < 760) setSidebarOpen(false)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  const send = async () => {
    if (!session || !draft.trim() || pending || !active) return
    const text = draft.trim()
    try {
      setDraft('')
      setError('')
      const result = await submitMessage(session.session_id, text)
      setSession(result.session)
      setSessions((current) => current.map((row) => row.session_id === result.session.session_id ? result.session : row))
    } catch (reason) {
      setDraft(text)
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  const retry = async () => {
    if (!session) return
    try {
      setError('')
      const result = await retryResponse(session.session_id)
      setSession(result.session)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  const finish = async () => {
    if (!session || pending) return
    try {
      setBusy(true)
      const updated = await completeSession(session.session_id)
      setSession(updated)
      await refreshList()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  const remove = async (target: SupportSession) => {
    if (!window.confirm(tr('删除这段对话？'))) return
    try {
      await deleteSession(target.session_id)
      const rows = await refreshList()
      if (session?.session_id === target.session_id) {
        setSession(rows[0] || await createSession())
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  const exportCurrent = async () => {
    if (!session) return
    try {
      downloadJson(`${session.session_id}.json`, await exportSession(session.session_id))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  const openContributions = async () => {
    setContributionOpen(true)
    setContributionLoading(true)
    try {
      setContributions(await listContributions())
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setContributionLoading(false)
    }
  }

  const withdraw = async (contributionId: string) => {
    try {
      await withdrawContribution(contributionId)
      setContributions((current) => current.map((item) => item.contribution_id === contributionId
        ? { ...item, privacy_status: 'deletion_requested', workflow_status: 'quarantined' }
        : item))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  return (
    <main className="app-shell">
      <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
        <div className="brand-row">
          <div className="brand-mark"><MessageCircle size={20} /></div>
          <div><a href="/">{tr("← TheraSkillAgent")}</a><span>{tr("Support")}</span></div>
          <button className="icon-button mobile-only" onClick={() => setSidebarOpen(false)} title={tr("关闭会话列表")}><X size={18} /></button>
        </div>
        <button className="new-session" onClick={startNew} disabled={busy}><Plus size={17} />{tr("新对话")}</button>
        <nav className="session-list" aria-label={tr("会话历史")}>
          {orderedSessions.map((row) => (
            <div className={`session-row ${session?.session_id === row.session_id ? 'active' : ''}`} key={row.session_id}>
              <button className="session-select" onClick={() => { setSession(row); if (window.innerWidth < 760) setSidebarOpen(false) }}>
                <span>{['新对话','New conversation'].includes(row.title)?tr('新对话'):row.title}</span>
                <small>{row.status === 'completed' ? tr('已结束') : row.generation.status === 'pending' ? tr('生成中') : tr('进行中')}</small>
              </button>
              <button className="row-action" onClick={() => remove(row)} title={tr("删除对话")} disabled={row.generation.status === 'pending'}><Trash2 size={15} /></button>
            </div>
          ))}
        </nav>
        <div className="connection-state"><span className={connected ? 'online' : 'offline'} />{connected ? tr('服务已连接') : tr('服务未连接')}</div>
      </aside>

      {sidebarOpen && <button className="sidebar-scrim mobile-only" onClick={() => setSidebarOpen(false)} aria-label={tr("关闭侧栏")} />}

      <section className="workspace">
        <header className="workspace-header">
          <button className="icon-button mobile-only" onClick={() => setSidebarOpen(true)} title={tr("打开会话列表")}><Menu size={19} /></button>
          <div className="session-heading">
            <h1>{session?.title ? (['新对话','New conversation'].includes(session.title)?tr('新对话'):session.title) : 'TheraSkill Support'}</h1>
            <span>{session?.status === 'completed' ? tr('已结束') : pending ? tr('正在回应') : tr('进行中')}</span>
          </div>
          <div className="header-actions">
            <button className="language-toggle" onClick={toggleLocale} title={locale === 'zh' ? tr('切换到英文') : 'Switch to Chinese'} aria-label={locale === 'zh' ? tr('切换到英文') : 'Switch to Chinese'}>{locale === 'zh' ? 'EN' : '中文'}</button>
            <button className="icon-button" onClick={() => setMemoryOpen(true)} title={tr("我的记忆")}><BookMarked size={18} /></button>
            <button className="icon-button" onClick={openContributions} title={tr("我的反馈")}><ListChecks size={18} /></button>
            <button className="icon-button" onClick={exportCurrent} title={tr("导出对话")} disabled={!session}><Download size={18} /></button>
            {active && <button className="icon-button stop" onClick={finish} title={tr("结束对话")} disabled={busy || pending}><Square size={17} /></button>}
          </div>
        </header>

        {error && <div className="error-banner"><AlertCircle size={17} /><span>{error}</span><button onClick={() => setError('')} title={tr("关闭错误")}><X size={15} /></button></div>}

        {session && <SafetyAndPlanCard session={session} onSession={setSession} onError={setError} />}

        <div className="conversation">
          {!session?.messages.length && (
            <div className="empty-state"><MessageCircle size={30} /><h2>{tr("今天想聊些什么？")}</h2></div>
          )}
          {session?.messages.map((message) => (
            <Message
              message={message}
              sessionId={session.session_id}
              onError={setError}
              onFeedbackSubmitted={() => { if (contributionOpen) void openContributions() }}
              onOpenEvidence={setSelectedEvidence}
              key={message.message_id}
            />
          ))}
          {pending && (
            <article className="message assistant-message pending-message">
              <div className="message-meta">TheraSkillAgent</div>
              <div className="message-body"><span className="thinking-dot" /><span className="thinking-dot" /><span className="thinking-dot" /><p>{locale === 'zh' ? tr('正在准备回应。任务在后台继续，稍后可从会话历史找回。') : 'Preparing a response. The job continues in the background; you can return through conversation history.'}</p></div>
            </article>
          )}
          {failed && (
            <div className="generation-failed"><AlertCircle size={18} /><span>{tr("回应生成失败")}</span><button onClick={retry}><RefreshCw size={15} />{tr("重试")}</button></div>
          )}
          <div ref={transcriptEnd} />
        </div>

        <footer className="composer-wrap">
          {session?.status === 'completed' ? (
            <div className="completed-state"><Check size={17} />{tr("这段对话已结束")}<button onClick={startNew}>{tr("开始新对话")}</button></div>
          ) : (
            <div className="composer">
              <textarea
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault()
                    void send()
                  }
                }}
                placeholder={tr("输入你想说的内容")}
                rows={2}
                disabled={pending}
              />
              <button className="send-button" onClick={send} disabled={!draft.trim() || pending} title={tr("发送")}><Send size={19} /></button>
            </div>
          )}
        </footer>
      </section>
      {contributionOpen && (
        <>
          <button className="drawer-scrim" onClick={() => setContributionOpen(false)} aria-label={tr("关闭我的反馈")} />
          <aside className="contribution-drawer">
            <header><h2>{tr("我的反馈")}</h2><button className="icon-button" onClick={() => setContributionOpen(false)} title={tr("关闭")}><X size={17} /></button></header>
            {contributionLoading ? (
              <div className="drawer-loading"><LoaderCircle className="spin" size={18} /></div>
            ) : contributions.length ? (
              <div className="contribution-list">
                {contributions.map((item) => (
                  <article key={item.contribution_id}>
                    <div><strong>{item.contribution_type}</strong><span>{new Date(item.created_at).toLocaleString()}</span></div>
                    <small>{item.origin.product} · {item.target.target_type}</small>
                    {item.privacy_status === 'deletion_requested' ? (
                      <span className="withdrawn-label">{tr("已申请删除")}</span>
                    ) : (
                      <button onClick={() => withdraw(item.contribution_id)}><Trash2 size={14} />{tr("撤回")}</button>
                    )}
                  </article>
                ))}
              </div>
            ) : <div className="drawer-empty">{tr("暂无反馈")}</div>}
          </aside>
        </>
      )}
      {memoryOpen && <MemoryDrawer onClose={() => setMemoryOpen(false)} onError={setError} />}
      <KnowledgeDetailDrawer key={selectedEvidence ? [selectedEvidence.unit_id || selectedEvidence.source_id,selectedEvidence.source_index_id || selectedEvidence.index_id].join(':') : 'closed'} item={selectedEvidence} onClose={() => setSelectedEvidence(null)} />
    </main>
  )
}
