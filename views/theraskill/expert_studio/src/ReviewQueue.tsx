import { codeLabel } from '../../shared/codeLabels'
import { tr, useTranslationSubscription } from '../../shared/i18n'
import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, ClipboardCheck, LoaderCircle, RefreshCw, Send } from 'lucide-react'
import { getReview, getReviewProfile, listReviewQueue, submitReviewDecision } from './api'

const ISSUE_TYPES = [
  'acceptable', 'routing_error', 'retrieval_error', 'knowledge_error', 'missing_knowledge',
  'response_generation_error', 'reviewer_error', 'scenario_error', 'safety_boundary_error',
  'not_enough_context',
]
const ACTIONS = [
  'record_evaluation', 'propose_response_revision', 'create_regression_case',
  'propose_policy_change', 'flag_knowledge_unit', 'create_knowledge_candidate',
  'escalate_to_adjudication',
]
const LANES = ['product', 'routing', 'retrieval', 'policy', 'knowledge', 'reviewer', 'scenario', 'regression']

function toggle(items: string[], value: string) {
  return items.includes(value) ? items.filter((item) => item !== value) : [...items, value]
}

function JsonBlock({ value }: { value: unknown }) {
  useTranslationSubscription()

  return <pre>{JSON.stringify(value ?? {}, null, 2)}</pre>
}

export default function ReviewQueue() {
  useTranslationSubscription()

  const [queue, setQueue] = useState<any[]>([])
  const [profile, setProfile] = useState<any>(null)
  const [selected, setSelected] = useState<string>('')
  const [review, setReview] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [issues, setIssues] = useState<string[]>(['acceptable'])
  const [actions, setActions] = useState<string[]>(['record_evaluation'])
  const [lanes, setLanes] = useState<string[]>(['product'])
  const [confidence, setConfidence] = useState(3)
  const [rationale, setRationale] = useState('')
  const [correction, setCorrection] = useState('')
  const [regression, setRegression] = useState('{\n  "input": "",\n  "expected_behavior": ""\n}')
  const [candidate, setCandidate] = useState('{\n  "knowledge_type": "",\n  "claim_or_pattern": "",\n  "evidence_source": "expert_review"\n}')

  async function refresh() {
    setLoading(true); setError('')
    try {
      const [items, currentProfile] = await Promise.all([listReviewQueue(), getReviewProfile()])
      setQueue(items); setProfile(currentProfile)
      if (selected && !items.some((item) => item.contribution_id === selected)) {
        setSelected(''); setReview(null)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally { setLoading(false) }
  }

  useEffect(() => { refresh() }, [])

  async function open(id: string) {
    setSelected(id); setReview(null); setError(''); setNotice('')
    try { setReview(await getReview(id)) }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
  }

  function changeIssue(value: string) {
    if (value === 'acceptable') setIssues(['acceptable'])
    else setIssues(toggle(issues.filter((item) => item !== 'acceptable'), value))
  }

  async function submit() {
    if (!review?.assignment || !issues.length || !actions.length || !rationale.trim()) return
    setSubmitting(true); setError(''); setNotice('')
    try {
      const payload: Record<string, unknown> = {
        assignment_id: review.assignment.assignment_id,
        issue_types: issues,
        actions,
        remediation_lanes: lanes,
        confidence,
        rationale,
        conflict_of_interest: false,
      }
      if (actions.includes('propose_response_revision')) payload.corrected_response = correction
      if (actions.includes('create_regression_case')) payload.regression_case = JSON.parse(regression)
      if (actions.includes('create_knowledge_candidate')) payload.knowledge_candidate = JSON.parse(candidate)
      await submitReviewDecision(selected, payload)
      setNotice(tr('专家决策已记录'))
      setRationale(''); setCorrection('')
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally { setSubmitting(false) }
  }

  return <section className="review-queue-page">
    <div className="queue-header">
      <div><h1>{tr("反馈审阅")}</h1><span>{profile ? `${profile.display_name} · ${profile.status}` : tr('未绑定专家档案')}</span></div>
      <button className="icon-command" onClick={refresh} title={tr("刷新队列")}><RefreshCw /></button>
    </div>
    {error && <div className="error"><AlertTriangle />{error}</div>}
    {notice && <div className="notice"><CheckCircle2 />{notice}</div>}
    <div className="review-layout">
      <aside className="queue-list" aria-label={tr("审阅队列")}>
        <div className="queue-list-head"><strong>{tr("已分配")}</strong><span>{queue.length}</span></div>
        {loading ? <div className="queue-empty"><LoaderCircle className="spin" /></div> : queue.length === 0 ? <div className="queue-empty">{tr("暂无待审阅项")}</div> : queue.map((item) =>
          <button key={item.contribution_id} className={selected === item.contribution_id ? 'selected' : ''} onClick={() => open(item.contribution_id)}>
            <span className={`priority p${Math.floor(item.priority / 20)}`}>{item.priority}</span>
            <strong>{item.product}</strong>
            <small>{item.contribution_type}</small>
            <em>{item.reasons.join(' · ')}</em>
          </button>)}
      </aside>
      <div className="review-detail">
        {!selected ? <div className="review-placeholder"><ClipboardCheck /><strong>{tr("选择一条已分配记录")}</strong></div> : !review ? <div className="review-placeholder"><LoaderCircle className="spin" /></div> : <>
          <div className="workbench-grid">
            <section><h2>{tr("对话上下文")}</h2><p>{review.context.user_message || tr('无用户正文')}</p><JsonBlock value={review.context.bounded_conversation} /></section>
            <section><h2>{tr("Agent 回应")}</h2><p>{review.response.assistant_response || tr('无回应正文')}</p><JsonBlock value={review.context.feedback} /></section>
            <section><h2>{tr("路由与证据")}</h2><JsonBlock value={review.evidence} /></section>
            <section><h2>{tr("Machine Reviewer")}</h2>{review.machine_reviewer ? <JsonBlock value={review.machine_reviewer} /> : <p className="muted">{tr("本条记录没有 machine reviewer 结果")}</p>}</section>
          </div>
          <form className="decision-form" onSubmit={(event) => { event.preventDefault(); submit() }}>
            <div className="decision-head"><h2>{tr("专家决策")}</h2><span>{tr("优先级")}{review.triage?.priority}</span></div>
            <fieldset><legend>{tr("问题归因")}</legend><div className="check-grid">{ISSUE_TYPES.map((value) => <label key={value}><input type="checkbox" checked={issues.includes(value)} onChange={() => changeIssue(value)} />{codeLabel(value)}</label>)}</div></fieldset>
            <fieldset><legend>{tr("处理动作")}</legend><div className="check-grid">{ACTIONS.map((value) => <label key={value}><input type="checkbox" checked={actions.includes(value)} onChange={() => setActions(toggle(actions, value))} />{codeLabel(value)}</label>)}</div></fieldset>
            <fieldset><legend>{tr("处理通道")}</legend><div className="check-grid compact">{LANES.map((value) => <label key={value}><input type="checkbox" checked={lanes.includes(value)} onChange={() => setLanes(toggle(lanes, value))} />{codeLabel(value)}</label>)}</div></fieldset>
            <div className="decision-fields">
              <label>{tr("置信度")}<select value={confidence} onChange={(event) => setConfidence(Number(event.target.value))}>{[1,2,3,4,5].map((value) => <option key={value}>{value}</option>)}</select></label>
              <label className="wide">{tr("判断依据")}<textarea value={rationale} onChange={(event) => setRationale(event.target.value)} required /></label>
              {actions.includes('propose_response_revision') && <label className="wide">{tr("修订回应")}<textarea value={correction} onChange={(event) => setCorrection(event.target.value)} required /></label>}
              {actions.includes('create_regression_case') && <label className="wide">{tr("回归案例 JSON")}<textarea className="mono" value={regression} onChange={(event) => setRegression(event.target.value)} required /></label>}
              {actions.includes('create_knowledge_candidate') && <label className="wide">{tr("Knowledge candidate JSON")}<textarea className="mono" value={candidate} onChange={(event) => setCandidate(event.target.value)} required /></label>}
            </div>
            <button className="primary decision-submit" disabled={submitting || !issues.length || !actions.length || !rationale.trim()}>{submitting ? <LoaderCircle className="spin" /> : <Send />}{tr("提交决策")}</button>
          </form>
        </>}
      </div>
    </div>
  </section>
}
