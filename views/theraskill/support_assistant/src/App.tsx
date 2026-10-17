import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertCircle,
  BookMarked,
  BookOpen,
  Check,
  Download,
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
import type { ContributionSummary, FeedbackConsent, SupportMessage, SupportSession } from './types'
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
    process_guidance: '过程参考',
    therapy_process_pattern: '互动模式',
    psychoeducation_or_support_guidance: '支持参考',
    method_rubric: '方法参考',
    risk_boundary: '边界参考',
  }
  return labels[role] || '参考依据'
}

function StructuredContent({ value }: { value: Record<string, unknown> }) {
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

  if (submitted) return <div className="feedback-recorded"><Check size={13} />已记录</div>
  return (
    <div className="response-feedback">
      <div className="feedback-trigger">
        <button className={helpful === true ? 'selected' : ''} onClick={() => choose(true)} title="有帮助"><ThumbsUp size={14} /></button>
        <button className={helpful === false ? 'selected' : ''} onClick={() => choose(false)} title="没帮助"><ThumbsDown size={14} /></button>
      </div>
      {helpful !== null && (
        <div className="feedback-form">
          {!helpful && (
            <div className="reason-grid">
              {feedbackReasons.map(([value, label]) => (
                <label key={value}>
                  <input type="checkbox" checked={reasons.includes(value)} onChange={() => toggleReason(value)} />
                  <span>{label}</span>
                </label>
              ))}
            </div>
          )}
          <textarea value={comment} onChange={(event) => setComment(event.target.value)} placeholder="补充说明（可选）" rows={2} maxLength={2000} />
          <div className="consent-options">
            <label><input type="checkbox" checked={consent.content_review} onChange={(event) => setConsentValue('content_review', event.target.checked)} />允许内容评审</label>
            <label><input type="checkbox" checked={consent.research_use} onChange={(event) => setConsentValue('research_use', event.target.checked)} />允许研究使用</label>
            <label><input type="checkbox" checked={consent.knowledge_derivation} onChange={(event) => setConsentValue('knowledge_derivation', event.target.checked)} />允许形成知识候选</label>
          </div>
          <button className="feedback-submit" onClick={submit} disabled={submitting}>
            {submitting ? <LoaderCircle className="spin" size={14} /> : <Check size={14} />}提交
          </button>
        </div>
      )}
    </div>
  )
}

function Message({ message, sessionId, onError, onFeedbackSubmitted }: {
  message: SupportMessage
  sessionId: string
  onError: (message: string) => void
  onFeedbackSubmitted: () => void
}) {
  const isUser = message.role === 'user'
  return (
    <article
      className={`message ${isUser ? 'user-message' : 'assistant-message'}`}
      data-presentation-mode={message.presentation_mode}
    >
      <div className="message-meta">{isUser ? '你' : 'TheraSkill'}</div>
      <div className="message-body">
        <p>{message.text}</p>
        {!isUser && <StructuredContent value={message.structured_content} />}
        {!isUser && message.evidence_summary.length > 0 && (
          <details className="evidence-details">
            <summary><BookOpen size={15} />参考依据</summary>
            <div className="evidence-list">
              {message.evidence_summary.map((item, index) => (
                <div className="evidence-row" key={`${item.label}-${index}`}>
                  <div>
                    <span>{item.label.replaceAll('_', ' ')}</span>
                    {item.used_for && <small className="evidence-row-detail">用途：{item.used_for}</small>}
                    {item.boundary && <small className="evidence-row-detail">边界：{item.boundary}</small>}
                    {item.inference && <small className="evidence-row-detail">{item.inference}</small>}
                  </div>
                  <small>{readableRole(item.role)}{item.source_type ? ` · ${item.source_type}` : ''}</small>
                </div>
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
  const [goal, setGoal] = useState('')
  const [actionItem, setActionItem] = useState('')
  const [saving, setSaving] = useState(false)
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
          <strong>{safety.attention_level === 'urgent' ? '现在需要优先确认安全' : '这段对话出现了需要关注的安全信号'}</strong>
          <p>请根据你的实际情况联系身边可信任的人、当地专业服务或当地急救资源。系统不会临时编造联系方式。</p>
          <div className="safety-action-row">
            <button onClick={() => void safetyAction('trusted_person')} disabled={saving}>联系可信任的人</button>
            <button onClick={() => void safetyAction('professional')} disabled={saving}>联系专业人员</button>
            <button onClick={() => void safetyAction('local_emergency')} disabled={saving}>当地紧急服务</button>
            <button onClick={() => void checkIn(true)} disabled={saving}>我现在安全</button>
            <button onClick={() => void checkIn(false)} disabled={saving}>我现在仍不安全</button>
          </div>
        </div>
      )}
      <details>
        <summary>我的目标与下一步</summary>
        {session.plan.goals.length > 0 && <ul>{session.plan.goals.map((item) => <li key={item.goal_id}>{item.text}</li>)}</ul>}
        {session.plan.action_items.length > 0 && <ul>{session.plan.action_items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul>}
        <div className="support-plan-inputs">
          <input value={goal} onChange={(event) => setGoal(event.target.value)} placeholder="添加一个我想关注的目标" maxLength={500} />
          <input value={actionItem} onChange={(event) => setActionItem(event.target.value)} placeholder="添加一个下一步行动" maxLength={500} />
          <button onClick={() => void savePlan()} disabled={saving || (!goal.trim() && !actionItem.trim())}>保存</button>
        </div>
      </details>
    </section>
  )
}

export default function App() {
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
    if (!window.confirm('删除这段对话？')) return
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
          <div><strong>TheraSkill</strong><span>Support</span></div>
          <button className="icon-button mobile-only" onClick={() => setSidebarOpen(false)} title="关闭会话列表"><X size={18} /></button>
        </div>
        <button className="new-session" onClick={startNew} disabled={busy}><Plus size={17} />新对话</button>
        <nav className="session-list" aria-label="会话历史">
          {orderedSessions.map((row) => (
            <div className={`session-row ${session?.session_id === row.session_id ? 'active' : ''}`} key={row.session_id}>
              <button className="session-select" onClick={() => { setSession(row); if (window.innerWidth < 760) setSidebarOpen(false) }}>
                <span>{row.title}</span>
                <small>{row.status === 'completed' ? '已结束' : row.generation.status === 'pending' ? '生成中' : '进行中'}</small>
              </button>
              <button className="row-action" onClick={() => remove(row)} title="删除对话" disabled={row.generation.status === 'pending'}><Trash2 size={15} /></button>
            </div>
          ))}
        </nav>
        <div className="connection-state"><span className={connected ? 'online' : 'offline'} />{connected ? '服务已连接' : '服务未连接'}</div>
      </aside>

      {sidebarOpen && <button className="sidebar-scrim mobile-only" onClick={() => setSidebarOpen(false)} aria-label="关闭侧栏" />}

      <section className="workspace">
        <header className="workspace-header">
          <button className="icon-button mobile-only" onClick={() => setSidebarOpen(true)} title="打开会话列表"><Menu size={19} /></button>
          <div className="session-heading">
            <h1>{session?.title || 'TheraSkill Support'}</h1>
            <span>{session?.status === 'completed' ? '已结束' : pending ? '正在回应' : '进行中'}</span>
          </div>
          <div className="header-actions">
            <button className="language-toggle" onClick={toggleLocale} title={locale === 'zh' ? '切换到英文' : 'Switch to Chinese'} aria-label={locale === 'zh' ? '切换到英文' : 'Switch to Chinese'}>{locale === 'zh' ? 'EN' : '中文'}</button>
            <button className="icon-button" onClick={() => setMemoryOpen(true)} title="我的记忆"><BookMarked size={18} /></button>
            <button className="icon-button" onClick={openContributions} title="我的反馈"><ListChecks size={18} /></button>
            <button className="icon-button" onClick={exportCurrent} title="导出对话" disabled={!session}><Download size={18} /></button>
            {active && <button className="icon-button stop" onClick={finish} title="结束对话" disabled={busy || pending}><Square size={17} /></button>}
          </div>
        </header>

        {error && <div className="error-banner"><AlertCircle size={17} /><span>{error}</span><button onClick={() => setError('')} title="关闭错误"><X size={15} /></button></div>}

        {session && <SafetyAndPlanCard session={session} onSession={setSession} onError={setError} />}

        <div className="conversation">
          {!session?.messages.length && (
            <div className="empty-state"><MessageCircle size={30} /><h2>今天想聊些什么？</h2></div>
          )}
          {session?.messages.map((message) => (
            <Message
              message={message}
              sessionId={session.session_id}
              onError={setError}
              onFeedbackSubmitted={() => { if (contributionOpen) void openContributions() }}
              key={message.message_id}
            />
          ))}
          {pending && (
            <article className="message assistant-message pending-message">
              <div className="message-meta">TheraSkill</div>
              <div className="message-body"><span className="thinking-dot" /><span className="thinking-dot" /><span className="thinking-dot" /></div>
            </article>
          )}
          {failed && (
            <div className="generation-failed"><AlertCircle size={18} /><span>回应生成失败</span><button onClick={retry}><RefreshCw size={15} />重试</button></div>
          )}
          <div ref={transcriptEnd} />
        </div>

        <footer className="composer-wrap">
          {session?.status === 'completed' ? (
            <div className="completed-state"><Check size={17} />这段对话已结束<button onClick={startNew}>开始新对话</button></div>
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
                placeholder="输入你想说的内容"
                rows={2}
                disabled={pending}
              />
              <button className="send-button" onClick={send} disabled={!draft.trim() || pending} title="发送"><Send size={19} /></button>
            </div>
          )}
        </footer>
      </section>
      {contributionOpen && (
        <>
          <button className="drawer-scrim" onClick={() => setContributionOpen(false)} aria-label="关闭我的反馈" />
          <aside className="contribution-drawer">
            <header><h2>我的反馈</h2><button className="icon-button" onClick={() => setContributionOpen(false)} title="关闭"><X size={17} /></button></header>
            {contributionLoading ? (
              <div className="drawer-loading"><LoaderCircle className="spin" size={18} /></div>
            ) : contributions.length ? (
              <div className="contribution-list">
                {contributions.map((item) => (
                  <article key={item.contribution_id}>
                    <div><strong>{item.contribution_type}</strong><span>{new Date(item.created_at).toLocaleString()}</span></div>
                    <small>{item.origin.product} · {item.target.target_type}</small>
                    {item.privacy_status === 'deletion_requested' ? (
                      <span className="withdrawn-label">已申请删除</span>
                    ) : (
                      <button onClick={() => withdraw(item.contribution_id)}><Trash2 size={14} />撤回</button>
                    )}
                  </article>
                ))}
              </div>
            ) : <div className="drawer-empty">暂无反馈</div>}
          </aside>
        </>
      )}
      {memoryOpen && <MemoryDrawer onClose={() => setMemoryOpen(false)} onError={setError} />}
    </main>
  )
}
