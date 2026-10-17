import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Archive,
  ArrowLeft,
  BookOpenText,
  CheckCircle2,
  CirclePause,
  CirclePlay,
  ClipboardCheck,
  Clock3,
  Filter,
  Flag,
  History,
  GitCompareArrows,
  ListChecks,
  LoaderCircle,
  MessageSquareText,
  NotebookPen,
  PanelLeftClose,
  PanelLeftOpen,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  Sparkles,
  Star,
  Square,
  Trash2,
  UserRound,
  X,
} from 'lucide-react'
import {
  createSession,
  createReviewSession,
  createAssignment,
  createComparison,
  addAnnotation,
  generatePatientOpening,
  generateScenario,
  generateScenarioBatch,
  getSession,
  health,
  listScenarios,
  listAnnotations,
  listComparisons,
  listGeneratedScenarios,
  listContributions,
  listSessions,
  reviewGeneratedScenario,
  updateGeneratedScenario,
  runDualCycle,
  sessionAction,
  submitTurn,
  submitFeedback,
  submitTurnFlag,
  withdrawContribution,
} from './api'
import type {
  CallerRole,
  ContributionSummary,
  FeedbackConsent,
  InteractiveMode,
  ResponseComparison,
  Scenario,
  SessionExport,
  SessionView,
  TurnAnnotation,
} from './types'
import { useLocale } from './i18n'

const FAMILY_LABELS: Record<string, string> = {
  supportive_exploration: '支持性探索',
  cognitive_reframing_probe: '认知重评',
  rupture_and_repair: '关系修复',
  risk_escalation_and_handoff: '风险与转介',
  rebt_belief_work: 'REBT 信念工作',
}

const FEEDBACK_DIMENSIONS = [
  ['patient_realism', '患者真实感'],
  ['therapist_response_appropriateness', '治疗师回应恰当性'],
  ['scenario_difficulty_fit', '场景难度匹配'],
  ['reviewer_usefulness', '复盘反馈有用性'],
] as const

function familyLabel(value: string) {
  return FAMILY_LABELS[value] || value
}

function statusLabel(value: string) {
  return {
    active: '进行中',
    paused: '已暂停',
    completed: '已完成',
    terminated: '已终止',
    failed: '失败',
  }[value] || value
}

export default function App() {
  const { locale, toggleLocale } = useLocale()
  const [role, setRole] = useState<CallerRole>('human_therapist')
  const [connected, setConnected] = useState(false)
  const [view, setView] = useState<'scenarios' | 'history'>(() => (
    new URLSearchParams(window.location.search).get('view') === 'history' ? 'history' : 'scenarios'
  ))
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [historyRows, setHistoryRows] = useState<SessionView[]>([])
  const [selected, setSelected] = useState<Scenario | null>(null)
  const [session, setSession] = useState<SessionExport | null>(null)
  const [searchText, setSearchText] = useState('')
  const [family, setFamily] = useState('')
  const [risk, setRisk] = useState('')
  const [difficulty, setDifficulty] = useState('')
  const [onlineBrief, setOnlineBrief] = useState('')
  const [onlineGenerating, setOnlineGenerating] = useState(false)
  const [onlineGenerated, setOnlineGenerated] = useState(false)
  const [draftEditOpen, setDraftEditOpen] = useState(false)
  const [draftBrief, setDraftBrief] = useState('')
  const [draftTags, setDraftTags] = useState('')
  const [draftDifficulty, setDraftDifficulty] = useState('medium')
  const [draftRisk, setDraftRisk] = useState('low')
  const [reviewReason, setReviewReason] = useState('')
  const [governanceBusy, setGovernanceBusy] = useState(false)
  const [batchTitle, setBatchTitle] = useState('')
  const [batchBrief, setBatchBrief] = useState('')
  const [batchCount, setBatchCount] = useState(4)
  const [batchBusy, setBatchBusy] = useState(false)
  const [batchNotice, setBatchNotice] = useState('')
  const [draft, setDraft] = useState('')
  const [openingMode, setOpeningMode] = useState<'patient' | 'therapist'>('patient')
  const [trainingMode, setTrainingMode] = useState<InteractiveMode>('human_therapist_vs_patient')
  const [busy, setBusy] = useState(false)
  const [sessionReady, setSessionReady] = useState(true)
  const [patientPending, setPatientPending] = useState(false)
  const [waitSeconds, setWaitSeconds] = useState(0)
  const [error, setError] = useState('')
  const [feedbackRating, setFeedbackRating] = useState(0)
  const [feedbackComment, setFeedbackComment] = useState('')
  const [feedbackSent, setFeedbackSent] = useState(false)
  const [feedbackDimensions, setFeedbackDimensions] = useState<Record<string, number>>({})
  const [feedbackConsent, setFeedbackConsent] = useState<FeedbackConsent>({
    content_review: false,
    research_use: false,
    knowledge_derivation: false,
  })
  const [contributionOpen, setContributionOpen] = useState(false)
  const [contributions, setContributions] = useState<ContributionSummary[]>([])
  const [contributionLoading, setContributionLoading] = useState(false)
  const [flagTurnId, setFlagTurnId] = useState('')
  const [flagCategory, setFlagCategory] = useState('unrealistic')
  const [flagComment, setFlagComment] = useState('')
  const [flagConsent, setFlagConsent] = useState<FeedbackConsent>({
    content_review: false,
    research_use: false,
    knowledge_derivation: false,
  })
  const [annotations, setAnnotations] = useState<TurnAnnotation[]>([])
  const [annotationTurnId, setAnnotationTurnId] = useState('')
  const [annotationComment, setAnnotationComment] = useState('')
  const [alternativeResponse, setAlternativeResponse] = useState('')
  const [assignmentTitle, setAssignmentTitle] = useState('')
  const [assignmentAssignee, setAssignmentAssignee] = useState('')
  const [assignmentSent, setAssignmentSent] = useState(false)
  const [comparisons, setComparisons] = useState<ResponseComparison[]>([])
  const [selectedCandidateId, setSelectedCandidateId] = useState('')
  const transcriptEnd = useRef<HTMLDivElement>(null)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const annotationLoadRef = useRef(0)
  const canGenerateOnline = role === 'researcher' || role === 'instructor'
  const canGovernGenerated = canGenerateOnline

  const loadScenarios = useCallback(async () => {
    setError('')
    try {
      const rows = await listScenarios(role, {
        family: family || undefined,
        risk: risk || undefined,
        difficulty: difficulty || undefined,
        search: searchText.trim() || undefined,
        language: locale,
      })
      const generatedRows = canGenerateOnline && !family && !risk && !difficulty && !searchText.trim()
        ? await listGeneratedScenarios(role, locale)
        : []
      const visibleRows = Array.from(new Map([...generatedRows, ...rows].map((row) => [row.scenario_id, row])).values())
      setScenarios(visibleRows)
      if ((!selected || (!selected.generated && !visibleRows.some((row) => row.scenario_id === selected.scenario_id))) && visibleRows.length) {
        setSelected(visibleRows[0])
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [canGenerateOnline, difficulty, family, locale, risk, role, searchText, selected])

  const loadHistory = useCallback(async () => {
    try {
      setHistoryRows(await listSessions(role))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [role])

  useEffect(() => {
    health().then(setConnected)
    loadScenarios()
    loadHistory()
  }, [loadHistory, loadScenarios])

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const sessionId = params.get('session_id')
    if (!sessionId) return
    let cancelled = false
    setSessionReady(false)
    getSession(role, sessionId)
      .then((value) => {
        if (!cancelled) {
          setSession(value)
          setSidebarOpen(false)
        }
      })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)) })
      .finally(() => { if (!cancelled) setSessionReady(true) })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    const scenarioId = new URLSearchParams(window.location.search).get('scenario_id')
    if (scenarioId) {
      const match = scenarios.find((row) => row.scenario_id === scenarioId)
      if (match) setSelected(match)
    }
  }, [scenarios])

  useEffect(() => {
    if (!selected?.generated) {
      setDraftEditOpen(false)
      return
    }
    setDraftBrief(selected.scenario_brief || '')
    setDraftTags((selected.scenario_tags || []).join('、'))
    setDraftDifficulty(selected.difficulty_band || 'medium')
    setDraftRisk(selected.risk_band || 'low')
    setReviewReason('')
    setBatchNotice('')
  }, [selected?.scenario_id])

  useEffect(() => {
    transcriptEnd.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [session?.public_view.transcript.length])

  useEffect(() => {
    setFeedbackRating(0)
    setFeedbackComment('')
    setFeedbackDimensions({})
    setFeedbackConsent({ content_review: false, research_use: false, knowledge_derivation: false })
    setFeedbackSent(false)
  }, [session?.public_view.session_id])

  useEffect(() => {
    if (!patientPending) {
      setWaitSeconds(0)
      return
    }
    const started = Date.now()
    const timer = window.setInterval(() => {
      setWaitSeconds(Math.floor((Date.now() - started) / 1000))
    }, 1000)
    return () => window.clearInterval(timer)
  }, [patientPending])

  const activeMode = session?.public_view.mode || trainingMode
  const isClientRoleMode = activeMode === 'human_client_role_vs_therapist'
  const isDualMode = activeMode === 'dual_agent_research'
  const learnerActor: CallerRole = isDualMode
    ? 'researcher'
    : isClientRoleMode ? 'human_client_role' : 'human_therapist'
  const requestRole: CallerRole = role === 'instructor' ? 'instructor' : learnerActor
  const stateUpdatePending = Boolean(
    session?.public_view.progress.state_update_pending
      || session?.public_view.progress.therapist_memory_update_pending,
  )
  const reviewPending = session?.public_view.review?.status === 'pending'

  useEffect(() => {
    if (!session) {
      setAnnotations([])
      setComparisons([])
      return
    }
    const sessionId = session.public_view.session_id
    const loadId = ++annotationLoadRef.current
    Promise.all([
      listAnnotations(requestRole, sessionId),
      listComparisons(requestRole, sessionId),
    ]).then(([annotationRows, comparisonRows]) => {
      if (loadId !== annotationLoadRef.current) return
      setAnnotations(annotationRows)
      setComparisons(comparisonRows)
    }).catch(() => {
      // Collaboration data is optional while the core session remains usable.
    })
  }, [requestRole, session?.public_view.session_id])

  useEffect(() => {
    if (!stateUpdatePending || !session) return
    const sessionId = session.public_view.session_id
    const timer = window.setInterval(async () => {
      try {
        const latest = await getSession(requestRole, sessionId)
        setSession(latest)
        const latestPending = Boolean(
          latest.public_view.progress.state_update_pending
            || latest.public_view.progress.therapist_memory_update_pending,
        )
        if (!latestPending) {
          window.clearInterval(timer)
        }
      } catch {
        // The main error surface handles explicit user actions; polling retries quietly.
      }
    }, 1000)
    return () => window.clearInterval(timer)
  }, [requestRole, session?.public_view.session_id, stateUpdatePending])

  useEffect(() => {
    if (!reviewPending || !session) return
    const sessionId = session.public_view.session_id
    const timer = window.setInterval(async () => {
      try {
        const latest = await getSession(requestRole, sessionId)
        setSession(latest)
        if (latest.public_view.review?.status !== 'pending') {
          window.clearInterval(timer)
        }
      } catch {
        // Review polling is retried until the terminal result is available.
      }
    }, 1000)
    return () => window.clearInterval(timer)
  }, [reviewPending, requestRole, session?.public_view.session_id])

  const refreshSession = useCallback(async () => {
    if (!session) return
    setSession(await getSession(requestRole, session.public_view.session_id))
  }, [requestRole, session])

  async function startScenario() {
    if (!selected) return
    setSessionReady(false)
    setBusy(true)
    setError('')
    try {
      const actor: CallerRole = trainingMode === 'dual_agent_research'
        ? 'researcher'
        : trainingMode === 'human_client_role_vs_therapist' ? 'human_client_role' : 'human_therapist'
      const created = await createSession(actor, selected.scenario_id, trainingMode, locale)
      const createdSession = await getSession(actor, created.session_id)
      setSession(createdSession)
      setSidebarOpen(false)
      if (trainingMode === 'human_therapist_vs_patient' && openingMode === 'patient') {
        setPatientPending(true)
        await generatePatientOpening(created.session_id)
        setSession(await getSession(actor, created.session_id))
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setPatientPending(false)
      await new Promise<void>((resolve) => {
        window.requestAnimationFrame(() => window.requestAnimationFrame(() => resolve()))
      })
      setBusy(false)
      setSessionReady(true)
    }
  }

  async function runOnlineScenarioGeneration() {
    if (!canGenerateOnline || onlineBrief.trim().length < 8 || onlineGenerating) return
    setOnlineGenerating(true)
    setError('')
    try {
      const result = await generateScenario(role, onlineBrief.trim(), 'supportive_exploration', 'medium', 'low', locale)
      setScenarios((current) => [result.scenario, ...current.filter((row) => row.scenario_id !== result.scenario.scenario_id)])
      setSelected(result.scenario)
      setOnlineGenerated(true)
      setOnlineBrief('')
      setTrainingMode('human_therapist_vs_patient')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setOnlineGenerating(false)
    }
  }

  async function saveGeneratedDraft() {
    if (!selected?.generated || !draftBrief.trim() || governanceBusy) return
    setGovernanceBusy(true)
    setError('')
    try {
      const result = await updateGeneratedScenario(role, selected.scenario_id, {
        scenario_brief: draftBrief.trim(),
        scenario_tags: draftTags.split(/[、,，]/).map((item) => item.trim()).filter(Boolean),
        difficulty_band: draftDifficulty,
        risk_band: draftRisk,
      })
      setScenarios((rows) => rows.map((row) => row.scenario_id === result.scenario.scenario_id ? result.scenario : row))
      setSelected(result.scenario)
      setDraftEditOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setGovernanceBusy(false)
    }
  }

  async function reviewSelectedGenerated(action: 'approve' | 'reject') {
    if (!selected?.generated || governanceBusy) return
    if (action === 'reject' && !reviewReason.trim()) {
      setError('退回草稿前请填写原因。')
      return
    }
    setGovernanceBusy(true)
    setError('')
    try {
      const result = await reviewGeneratedScenario(role, selected.scenario_id, action, reviewReason.trim())
      setScenarios((rows) => rows.map((row) => row.scenario_id === result.scenario.scenario_id ? result.scenario : row))
      setSelected(result.scenario)
      setReviewReason('')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setGovernanceBusy(false)
    }
  }

  async function runBatchGeneration() {
    if (!canGovernGenerated || batchBrief.trim().length < 8 || !batchTitle.trim() || batchBusy) return
    setBatchBusy(true)
    setBatchNotice('')
    setError('')
    try {
      const result = await generateScenarioBatch(role, {
        course_title: batchTitle.trim(),
        course_description: batchBrief.trim(),
        skill_tags: [],
        brief: batchBrief.trim(),
        count: Math.max(1, Math.min(12, batchCount)),
        language: locale,
      })
      setScenarios((rows) => [...result.scenarios, ...rows.filter((row) => !result.scenarios.some((item) => item.scenario_id === row.scenario_id))])
      if (result.scenarios[0]) setSelected(result.scenarios[0])
      setBatchNotice(`已生成 ${result.scenarios.length} 个草稿；课程草案待逐个审核后使用。`)
      setBatchTitle('')
      setBatchBrief('')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBatchBusy(false)
    }
  }

  async function openHistorySession(row: SessionView) {
    setSessionReady(false)
    setBusy(true)
    try {
      const rowRole: CallerRole = role === 'instructor'
        ? 'instructor'
        : row.mode === 'dual_agent_research'
          ? 'researcher'
        : row.mode === 'human_client_role_vs_therapist'
          ? 'human_client_role'
          : 'human_therapist'
      setSession(await getSession(rowRole, row.session_id))
      setSidebarOpen(false)
    } finally {
      setBusy(false)
      setSessionReady(true)
    }
  }

  async function sendTurn(event: FormEvent) {
    event.preventDefault()
    if (!session || !draft.trim() || busy) return
    const text = draft.trim()
    if (isDualMode) {
      setDraft('')
      if (composerRef.current) composerRef.current.value = ''
      setBusy(true)
      setPatientPending(true)
      setError('')
      try {
        await runDualCycle(session.public_view.session_id, session.public_view.transcript.length ? undefined : text)
        setSession(await getSession('researcher', session.public_view.session_id))
      } catch (err) {
        setDraft(text)
        if (composerRef.current) composerRef.current.value = text
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        setPatientPending(false)
        setBusy(false)
      }
      return
    }
    const originalSession = session
    const optimisticTurn = {
      turn_id: `optimistic-${crypto.randomUUID()}`,
      turn_index: session.public_view.current_turn_index + 1,
      role: isClientRoleMode ? 'patient' : 'therapist',
      text,
    }
    setDraft('')
    if (composerRef.current) composerRef.current.value = ''
    setBusy(true)
    setPatientPending(true)
    setError('')
    setSession({
      ...session,
      public_view: {
        ...session.public_view,
        transcript: [...session.public_view.transcript, optimisticTurn],
      },
    })
    try {
      await submitTurn(learnerActor, session.public_view.session_id, text)
      setSession(await getSession(learnerActor, session.public_view.session_id))
    } catch (err) {
      setSession(originalSession)
      setDraft(text)
      if (composerRef.current) composerRef.current.value = text
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setPatientPending(false)
      setBusy(false)
    }
  }

  async function runAction(action: 'pause' | 'resume' | 'complete') {
    if (!session) return
    setBusy(true)
    setError('')
    try {
      await sessionAction(learnerActor, session.public_view.session_id, action)
      setSession(await getSession(requestRole, session.public_view.session_id))
      await loadHistory()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function openReviewOnly() {
    if (!activeSession || role !== 'researcher' || !terminal || activeSession.mode === 'review_only') return
    setBusy(true)
    setError('')
    try {
      const created = await createReviewSession(
        'researcher',
        activeSession.session_id,
        activeSession.scenario.scenario_id,
        activeSession.language,
      )
      setSession(await getSession('researcher', created.session_id))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function sendFeedback() {
    if (!session || feedbackRating < 1) return
    setBusy(true)
    try {
      await submitFeedback(
        role,
        session.public_view.session_id,
        feedbackRating,
        feedbackComment,
        feedbackDimensions,
        feedbackConsent,
      )
      setFeedbackSent(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function openContributions() {
    setContributionOpen(true)
    setContributionLoading(true)
    try {
      setContributions(await listContributions(role))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setContributionLoading(false)
    }
  }

  async function withdraw(contributionId: string) {
    try {
      await withdrawContribution(role, contributionId)
      setContributions((current) => current.map((item) => item.contribution_id === contributionId
        ? { ...item, privacy_status: 'deletion_requested', workflow_status: 'quarantined' }
        : item))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  async function sendTurnFlag() {
    if (!session || !flagTurnId) return
    setBusy(true)
    try {
      await submitTurnFlag(
        learnerActor,
        session.public_view.session_id,
        flagTurnId,
        flagCategory,
        flagComment,
        flagConsent,
      )
      setFlagTurnId('')
      setFlagComment('')
      setFlagConsent({ content_review: false, research_use: false, knowledge_derivation: false })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function sendAnnotation() {
    if (!session || !annotationTurnId || !annotationComment.trim()) return
    setBusy(true)
    try {
      const created = await addAnnotation(
        session.public_view.session_id,
        annotationTurnId,
        annotationComment.trim(),
        alternativeResponse,
      )
      annotationLoadRef.current += 1
      setAnnotations((rows) => [...rows, created])
      setAnnotationComment('')
      setAlternativeResponse('')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function sendAssignment() {
    if (!selected || !assignmentTitle.trim() || !assignmentAssignee.trim()) return
    setBusy(true)
    try {
      await createAssignment(
        assignmentTitle.trim(),
        assignmentAssignee.trim(),
        [selected.scenario_id],
      )
      setAssignmentSent(true)
      setAssignmentTitle('')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function generateComparison() {
    if (!session) return
    const patientTurns = session.public_view.transcript.filter((turn) => turn.role === 'patient')
    const target = patientTurns.at(-1)
    if (!target) return
    setBusy(true)
    try {
      const created = await createComparison(
        role === 'instructor' ? 'instructor' : 'human_therapist',
        session.public_view.session_id,
        target.turn_id,
      )
      setComparisons((rows) => [created, ...rows])
      setSelectedCandidateId('')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const activeSession = session?.public_view
  const responseCount = activeSession?.progress.agent_response_count || 0
  const turnBudget = activeSession?.progress.recommended_turn_budget || 0
  const progress = turnBudget ? Math.min(100, Math.round((responseCount / turnBudget) * 100)) : 0
  const hasReview = Boolean(activeSession?.review)
  const hiddenState = session?.private_patient_state
  const instructorReveal = session?.instructor_persona_reveal
  const terminal = Boolean(activeSession && ['completed', 'terminated', 'failed'].includes(activeSession.status))
  const latestComparison = comparisons[0]

  const summaryEntries = useMemo(() => {
    if (!activeSession?.review?.summary) return []
    return Object.entries(activeSession.review.summary).filter(([, value]) =>
      Array.isArray(value) ? value.length > 0 : Boolean(value),
    )
  }, [activeSession?.review])

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-block">
          <button
            className="icon-button mobile-only"
            onClick={() => setSidebarOpen((value) => !value)}
            title={sidebarOpen ? '收起侧栏' : '展开侧栏'}
          >
            {sidebarOpen ? <PanelLeftClose size={18} /> : <PanelLeftOpen size={18} />}
          </button>
          <div className="brand-mark">TS</div>
          <div>
            <strong>TheraSkill</strong>
            <span>Training Studio</span>
          </div>
        </div>
        <div className="topbar-actions">
          <button className="language-toggle" onClick={toggleLocale} title={locale === 'zh' ? '切换到英文' : 'Switch to Chinese'} aria-label={locale === 'zh' ? '切换到英文' : 'Switch to Chinese'}>{locale === 'zh' ? 'EN' : '中文'}</button>
          <div className={`connection ${connected ? 'online' : 'offline'}`}>
            <span />
            {connected ? '服务已连接' : '服务未连接'}
          </div>
          <button className="icon-button" onClick={openContributions} title="我的反馈"><ListChecks size={17} /></button>
          <div className="segmented" aria-label="查看角色">
            <button
              className={role === 'human_therapist' ? 'active' : ''}
              onClick={() => setRole('human_therapist')}
            >
              学员
            </button>
            <button
              className={role === 'instructor' ? 'active' : ''}
              onClick={() => setRole('instructor')}
            >
              教师
            </button>
            <button
              className={role === 'researcher' ? 'active' : ''}
              onClick={() => setRole('researcher')}
            >
              研究员
            </button>
          </div>
        </div>
      </header>

      <div className="workspace">
        <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
          <nav className="sidebar-tabs">
            <button className={view === 'scenarios' ? 'active' : ''} onClick={() => setView('scenarios')}>
              <BookOpenText size={17} /> 场景库
            </button>
            <button className={view === 'history' ? 'active' : ''} onClick={() => setView('history')}>
              <History size={17} /> 训练记录
            </button>
          </nav>

          {view === 'scenarios' ? (
            <>
              <div className="filter-panel">
                <label className="search-field">
                  <Search size={16} />
                  <input
                    value={searchText}
                    onChange={(event) => setSearchText(event.target.value)}
                    onKeyDown={(event) => event.key === 'Enter' && loadScenarios()}
                    placeholder="搜索主题或标签"
                  />
                </label>
                <div className="filter-row">
                  <Filter size={15} />
                  <select value={family} onChange={(event) => setFamily(event.target.value)}>
                    <option value="">全部类型</option>
                    {Object.entries(FAMILY_LABELS).map(([value, label]) => (
                      <option key={value} value={value}>{label}</option>
                    ))}
                  </select>
                  <select value={difficulty} onChange={(event) => setDifficulty(event.target.value)}>
                    <option value="">全部难度</option>
                    <option value="low">低</option>
                    <option value="medium">中</option>
                    <option value="high">高</option>
                  </select>
                  <select value={risk} onChange={(event) => setRisk(event.target.value)}>
                    <option value="">全部风险</option>
                    <option value="low">低</option>
                    <option value="elevated">需关注</option>
                    <option value="high">高</option>
                  </select>
                </div>
                <button className="secondary-button" onClick={loadScenarios}>
                  <RefreshCw size={15} /> 更新结果
                </button>
              </div>
              <div className="list-caption">{scenarios.length} 个可用训练场景</div>
              <div className="scenario-list">
                {scenarios.map((scenario) => (
                  <button
                    key={scenario.scenario_id}
                    className={`scenario-row ${selected?.scenario_id === scenario.scenario_id ? 'selected' : ''}`}
                    onClick={() => setSelected(scenario)}
                  >
                    <div className="scenario-row-top">
                      <span>{familyLabel(scenario.scenario_family)}</span>
                      <small>{scenario.difficulty_band || '未标注'}</small>
                    </div>
                    <strong>{scenario.scenario_brief?.replaceAll('`', '')}</strong>
                    <div className="scenario-meta">
                      <span>{scenario.case_family}</span>
                      <span>{scenario.risk_band === 'elevated' ? '需关注' : '低风险'}</span>
                    </div>
                  </button>
                ))}
              </div>
            </>
          ) : (
            <div className="history-list">
              <div className="list-caption">最近 {historyRows.length} 次训练</div>
              {historyRows.map((row) => (
                <button key={row.session_id} className="history-row" onClick={() => openHistorySession(row)}>
                  <div>
                    <strong>{familyLabel(row.scenario.scenario_family)}</strong>
                    <span>{row.scenario.scenario_brief?.replaceAll('`', '')}</span>
                  </div>
                  <small className={`status-text ${row.status}`}>{statusLabel(row.status)}</small>
                </button>
              ))}
            </div>
          )}
        </aside>

        <main className="main-stage">
          {error && <div className="error-banner">{error}</div>}
          {!activeSession ? (
            <section className="scenario-detail">
              {selected ? (
                <>
                  <div className="detail-heading">
                    <div>
                      <span className="eyebrow">{familyLabel(selected.scenario_family)}</span>
                      <h1>{selected.scenario_brief?.replaceAll('`', '')}</h1>
                    </div>
                    <button className="primary-button" onClick={startScenario} disabled={busy || !connected || role === 'instructor'}>
                      {busy ? <LoaderCircle className="spin" size={17} /> : <CirclePlay size={17} />}
                      开始训练
                    </button>
                  </div>
                  <div className="fact-strip">
                    <div><UserRound size={17} /><span>病例类别</span><strong>{selected.case_family || '未标注'}</strong></div>
                    <div><Clock3 size={17} /><span>建议轮次</span><strong>{selected.recommended_turn_budget || 3} 轮</strong></div>
                    <div><ShieldCheck size={17} /><span>风险带</span><strong>{selected.risk_band === 'elevated' ? '需关注' : '低风险'}</strong></div>
                  </div>
                  {selected.learner_role_card && Object.keys(selected.learner_role_card).length > 0 && (
                    <section className="detail-band role-card-band">
                      <div className="role-card-heading"><UserRound size={20} /><h2>来访者角色</h2></div>
                      <div className="role-card-grid">
                        <div><span>称呼</span><strong>{selected.learner_role_card.display_name || '模拟来访者'}</strong></div>
                        <div><span>年龄段</span><strong>{selected.learner_role_card.age_band || '未说明'}</strong></div>
                        <div><span>职业</span><strong>{selected.learner_role_card.occupation || '未说明'}</strong></div>
                        <div><span>关系状态</span><strong>{selected.learner_role_card.marital_status || '未说明'}</strong></div>
                      </div>
                      {(selected.learner_role_card.surface_presenting_problems || []).length > 0 && (
                        <div className="role-card-problems">
                          {(selected.learner_role_card.surface_presenting_problems || []).map((item) => <span key={item}>{item}</span>)}
                        </div>
                      )}
                    </section>
                  )}
                  {selected.generated && canGovernGenerated && (
                    <section className="detail-band governance-band">
                      <div className="role-card-heading"><ClipboardCheck size={20} /><h2>生成草稿治理</h2></div>
                      <div className="governance-summary">
                        <span className={`governance-status ${selected.review_status || 'draft'}`}>
                          {selected.review_status === 'approved' ? '已批准' : selected.review_status === 'rejected' ? '已退回' : '待审核'}
                        </span>
                        <span>质量 {selected.quality_report?.score ?? '—'} 分</span>
                        <span>{selected.quality_report?.status === 'fail' ? '存在阻断问题' : selected.quality_report?.status === 'warn' ? '有提醒项' : '检查通过'}</span>
                        {selected.batch_id && <span>批次 {selected.batch_index}/{selected.batch_size}</span>}
                      </div>
                      {selected.quality_report?.duplicate_of && (
                        <p className="governance-warning">与已有草稿「{selected.quality_report.duplicate_of}」相似度 {selected.quality_report.duplicate_similarity}，请先修改主题。</p>
                      )}
                      {!draftEditOpen ? (
                        <div className="governance-actions">
                          <button className="secondary-button" onClick={() => setDraftEditOpen(true)}>编辑草稿</button>
                          <button className="primary-button" onClick={() => void reviewSelectedGenerated('approve')} disabled={governanceBusy || selected.review_status === 'approved'}>
                            {governanceBusy ? <LoaderCircle className="spin" size={15} /> : <CheckCircle2 size={15} />} 批准复用
                          </button>
                        </div>
                      ) : (
                        <div className="governance-editor">
                          <label>场景描述<textarea value={draftBrief} onChange={(event) => setDraftBrief(event.target.value)} rows={3} maxLength={1200} /></label>
                          <label>训练标签<input value={draftTags} onChange={(event) => setDraftTags(event.target.value)} placeholder="用逗号分隔" /></label>
                          <div className="governance-selects">
                            <label>难度<select value={draftDifficulty} onChange={(event) => setDraftDifficulty(event.target.value)}><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select></label>
                            <label>风险<select value={draftRisk} onChange={(event) => setDraftRisk(event.target.value)}><option value="low">低</option><option value="elevated">需关注</option><option value="high">高</option></select></label>
                          </div>
                          <div className="governance-actions"><button className="secondary-button" onClick={() => setDraftEditOpen(false)}>取消</button><button className="primary-button" onClick={() => void saveGeneratedDraft()} disabled={governanceBusy || draftBrief.trim().length < 8}>保存并重新检查</button></div>
                        </div>
                      )}
                      <label className="governance-reason">退回原因<input value={reviewReason} onChange={(event) => setReviewReason(event.target.value)} placeholder="退回时必填，批准时可留空" /></label>
                      <button className="tertiary-danger" onClick={() => void reviewSelectedGenerated('reject')} disabled={governanceBusy}>退回草稿</button>
                    </section>
                  )}
                  {canGenerateOnline && !session && (
                    <section className="detail-band online-generation-band">
                      <div className="role-card-heading"><Sparkles size={20} /><h2>在线生成训练情景</h2></div>
                      <p>输入一个训练主题，生成仅用于研究和演示的 synthetic draft。生成结果会带有可追踪的病例状态，确认后即可开始训练，不会写入正式测试集。</p>
                      <textarea
                        value={onlineBrief}
                        onChange={(event) => setOnlineBrief(event.target.value)}
                        placeholder="例如：来访者因为工作绩效下降而持续自责，但暂时不愿讨论家庭关系"
                        rows={3}
                        maxLength={1200}
                      />
                      <button
                        className="secondary-button"
                        onClick={() => void runOnlineScenarioGeneration()}
                        disabled={onlineGenerating || onlineBrief.trim().length < 8}
                      >
                        {onlineGenerating ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}
                        {onlineGenerating ? '正在生成情景…' : '生成 synthetic draft'}
                      </button>
                      {onlineGenerated && <small className="online-generation-note">草稿已生成。开始训练前请确认场景描述、难度和风险带。</small>}
                      <div className="batch-generator">
                        <h3>批量生成课程场景</h3>
                        <input value={batchTitle} onChange={(event) => setBatchTitle(event.target.value)} placeholder="课程标题" maxLength={200} />
                        <textarea value={batchBrief} onChange={(event) => setBatchBrief(event.target.value)} placeholder="课程目标或共同训练主题（至少 8 个字）" rows={2} maxLength={1200} />
                        <div className="batch-generator-row"><input type="number" min={1} max={12} value={batchCount} onChange={(event) => setBatchCount(Number(event.target.value) || 1)} /><button className="secondary-button" onClick={() => void runBatchGeneration()} disabled={batchBusy || batchBrief.trim().length < 8 || !batchTitle.trim()}>{batchBusy ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />} 生成课程批次</button></div>
                        {batchNotice && <small className="online-generation-note">{batchNotice}</small>}
                      </div>
                    </section>
                  )}
                  <section className="detail-band">
                    <h2>训练标签</h2>
                    <div className="tag-cloud">
                      {selected.scenario_tags.slice(0, 12).map((tag) => <span key={tag}>{tag}</span>)}
                    </div>
                  </section>
                  <section className="detail-band readiness-band">
                    <ClipboardCheck size={22} />
                    <div>
                      <h2>会话准备</h2>
                      <p>{trainingMode === 'human_therapist_vs_patient'
                        ? '系统将固定病例设定并维护隐藏状态。训练开始后，学员只能看到患者实际披露的内容。'
                        : trainingMode === 'dual_agent_research'
                          ? '系统将连续运行模拟治疗师与模拟患者，用于研究、回归测试和过程分析。生成内容均为合成对话。'
                          : '你将依据公开病例信息扮演来访者，与模拟治疗师进行受控训练。模拟回应仅用于学习，不构成医疗服务。'}</p>
                    </div>
                  </section>
                  <section className="detail-band opening-band">
                    <div>
                      <h2>训练角色</h2>
                      <div className="opening-control" aria-label="训练角色">
                        <button
                          className={trainingMode === 'human_therapist_vs_patient' ? 'active' : ''}
                          onClick={() => setTrainingMode('human_therapist_vs_patient')}
                        >
                          扮演治疗师
                        </button>
                        <button
                          className={trainingMode === 'human_client_role_vs_therapist' ? 'active' : ''}
                          onClick={() => setTrainingMode('human_client_role_vs_therapist')}
                        >
                          扮演来访者
                        </button>
                        {role === 'researcher' && (
                          <button
                            className={trainingMode === 'dual_agent_research' ? 'active' : ''}
                            onClick={() => setTrainingMode('dual_agent_research')}
                          >
                            双代理研究
                          </button>
                        )}
                      </div>
                    </div>
                  </section>
                  {trainingMode === 'human_therapist_vs_patient' && (
                  <section className="detail-band opening-band">
                    <div>
                      <h2>开场角色</h2>
                      <div className="opening-control" aria-label="开场角色">
                        <button
                          className={openingMode === 'patient' ? 'active' : ''}
                          onClick={() => setOpeningMode('patient')}
                        >
                          患者先说
                        </button>
                        <button
                          className={openingMode === 'therapist' ? 'active' : ''}
                          onClick={() => setOpeningMode('therapist')}
                        >
                          治疗师先说
                        </button>
                      </div>
                    </div>
                  </section>
                  )}
                  {role === 'instructor' && (
                    <section className="detail-band instructor-assignment">
                      <div>
                        <h2>分配当前场景</h2>
                        <div className="assignment-fields">
                          <input
                            value={assignmentTitle}
                            onChange={(event) => setAssignmentTitle(event.target.value)}
                            placeholder="训练任务名称"
                          />
                          <input
                            value={assignmentAssignee}
                            onChange={(event) => setAssignmentAssignee(event.target.value)}
                            placeholder="学员标识"
                          />
                          <button className="secondary-button" onClick={sendAssignment} disabled={busy || !assignmentTitle.trim() || !assignmentAssignee.trim()}>
                            <ListChecks size={15} /> {assignmentSent ? '已分配' : '分配任务'}
                          </button>
                        </div>
                      </div>
                    </section>
                  )}
                </>
              ) : (
                <div className="empty-state"><BookOpenText size={30} /><p>选择一个训练场景</p></div>
              )}
            </section>
          ) : (
            <section className="session-layout">
              <div className="session-main">
                <div className="session-header">
                  <button className="icon-button" onClick={() => setSession(null)} title="返回场景库">
                    <ArrowLeft size={18} />
                  </button>
                  <div>
                    <span>{familyLabel(activeSession.scenario.scenario_family)}</span>
                    <h1>{activeSession.scenario.scenario_brief?.replaceAll('`', '')}</h1>
                  </div>
                  <div className="session-controls">
                    <span className={`session-status ${activeSession.status}`}>{statusLabel(activeSession.status)}</span>
                    {activeSession.status === 'active' && (
                      <button className="icon-button" onClick={() => runAction('pause')} title="暂停会话" disabled={busy || stateUpdatePending}>
                        <CirclePause size={18} />
                      </button>
                    )}
                    {activeSession.status === 'paused' && (
                      <button className="icon-button" onClick={() => runAction('resume')} title="恢复会话" disabled={busy}>
                        <CirclePlay size={18} />
                      </button>
                    )}
                    {['active', 'paused'].includes(activeSession.status) && (
                      <button className="icon-button danger" onClick={() => runAction('complete')} title="结束并复盘" disabled={busy || stateUpdatePending}>
                        <Square size={17} />
                      </button>
                    )}
                    <button className="icon-button" onClick={refreshSession} title="刷新会话" disabled={busy}>
                      <RefreshCw size={17} />
                    </button>
                  </div>
                </div>

                <div className="transcript" aria-live="polite">
                  {activeSession.transcript.length === 0 && (
                    <div className="conversation-start">
                      <MessageSquareText size={25} />
                      <strong>会话已就绪</strong>
                      <span>{isDualMode ? '输入首个治疗师回合，系统将运行一轮双代理对话。' : isClientRoleMode ? '请以来访者身份开始这次受控体验。' : '等待治疗师开始这次访谈。'}</span>
                    </div>
                  )}
                  {activeSession.transcript.map((turn) => {
                    const flaggable = role !== 'instructor' && (
                      (isClientRoleMode && turn.role === 'therapist')
                      || (!isClientRoleMode && turn.role === 'patient')
                    )
                    return (
                      <div key={turn.turn_id} className={`turn ${turn.role}`}>
                        <div className="turn-avatar">{turn.role === 'therapist' ? 'T' : 'P'}</div>
                        <div className="turn-body">
                          <span>{turn.role === 'therapist'
                            ? (isDualMode ? '模拟治疗师' : isClientRoleMode ? '模拟治疗师' : '治疗师 · 你')
                            : (isClientRoleMode ? '来访者 · 你' : '模拟患者')}</span>
                          <p>{turn.text}</p>
                          {flaggable && (
                            <button className="turn-flag" onClick={() => setFlagTurnId(turn.turn_id)} title="标记此回应">
                              <Flag size={13} />标记
                            </button>
                          )}
                        </div>
                      </div>
                    )
                  })}
                  {patientPending && activeSession.status === 'active' && (
                    <div className={`turn ${isClientRoleMode ? 'therapist' : 'patient'} pending`}>
                      <div className="turn-avatar">{isClientRoleMode ? 'T' : 'P'}</div>
                      <div className="turn-body"><span>{isDualMode ? '双代理运行中' : isClientRoleMode ? '模拟治疗师' : '模拟患者'}</span><p><LoaderCircle className="spin" size={17} /> 正在形成回应{waitSeconds > 0 ? ` · ${waitSeconds} 秒` : '…'}</p></div>
                    </div>
                  )}
                  <div ref={transcriptEnd} />
                </div>

                {activeSession.status === 'active' && sessionReady && role !== 'instructor' && (
                  <form className="composer" onSubmit={sendTurn}>
                    <textarea
                      ref={composerRef}
                      onInput={(event) => setDraft(event.currentTarget.value)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' && !event.shiftKey) {
                          event.preventDefault()
                          event.currentTarget.form?.requestSubmit()
                        }
                      }}
                      placeholder={isDualMode ? '输入首个或下一轮治疗师回合' : isClientRoleMode ? '以来访者身份回应' : '输入治疗师回应'}
                      rows={3}
                      disabled={busy || stateUpdatePending}
                    />
                    <button className="send-button" disabled={!draft.trim() || busy || stateUpdatePending} title="发送回应">
                      {busy ? <LoaderCircle className="spin" size={18} /> : <Send size={18} />}
                    </button>
                  </form>
                )}
                {stateUpdatePending && (
                  <div className="state-sync"><LoaderCircle className="spin" size={13} /> {isClientRoleMode ? '正在同步治疗师案例记忆' : '正在同步患者状态与记忆'}</div>
                )}
              </div>

              <aside className="session-inspector">
                <section>
                  <div className="inspector-title"><Clock3 size={16} /><h2>训练进度</h2></div>
                  <div className="progress-row"><strong>{responseCount} / {turnBudget || '—'}</strong><span>{isClientRoleMode ? '治疗师回应' : '患者回应'}</span></div>
                  <div className="progress-track"><span style={{ width: `${progress}%` }} /></div>
                </section>
                <section>
                  <div className="inspector-title"><ShieldCheck size={16} /><h2>场景信息</h2></div>
                  <dl>
                    <div><dt>难度</dt><dd>{activeSession.scenario.difficulty_band}</dd></div>
                    <div><dt>风险带</dt><dd>{activeSession.scenario.risk_band}</dd></div>
                    <div><dt>类别</dt><dd>{activeSession.scenario.case_family}</dd></div>
                  </dl>
                </section>
                {activeSession.scenario.learner_role_card && Object.keys(activeSession.scenario.learner_role_card).length > 0 && (
                  <section>
                    <div className="inspector-title"><UserRound size={16} /><h2>来访者角色</h2></div>
                    <dl>
                      <div><dt>称呼</dt><dd>{activeSession.scenario.learner_role_card.display_name}</dd></div>
                      <div><dt>年龄段</dt><dd>{activeSession.scenario.learner_role_card.age_band}</dd></div>
                      <div><dt>职业</dt><dd>{activeSession.scenario.learner_role_card.occupation}</dd></div>
                    </dl>
                  </section>
                )}
                {hasReview && (
                  <section className="review-panel">
                    <div className="inspector-title"><CheckCircle2 size={17} /><h2>会话复盘</h2></div>
                    {reviewPending ? (
                      <div className="review-pending"><LoaderCircle className="spin" size={15} /> 正在生成复盘</div>
                    ) : activeSession.review?.status === 'failed' ? (
                      <div className="review-failed">复盘生成失败，请稍后重试。</div>
                    ) : (
                      <>
                        <div className="review-gate">Reviewer gate: {activeSession.review?.gate_status || '未标注'}</div>
                        {summaryEntries.map(([key, value]) => (
                          <div className="review-section" key={key}>
                            <h3>{key.replaceAll('_', ' ')}</h3>
                            {Array.isArray(value) ? (
                              <ul>{value.map((item, index) => <li key={index}>{String(item)}</li>)}</ul>
                            ) : <p>{String(value)}</p>}
                          </div>
                        ))}
                      </>
                    )}
                  </section>
                )}
                {role === 'researcher' && terminal && activeSession.mode !== 'review_only' && (
                  <section className="review-panel review-only-panel">
                    <div className="inspector-title"><GitCompareArrows size={17} /><h2>独立复盘</h2></div>
                    <p>创建一个只读复盘 session，保留原训练记录并独立运行 reviewer。</p>
                    <button className="secondary-button" onClick={() => void openReviewOnly()} disabled={busy}>
                      {busy ? <LoaderCircle className="spin" size={15} /> : <GitCompareArrows size={15} />} 创建 review-only
                    </button>
                  </section>
                )}
                {role === 'instructor' && hiddenState && ['completed', 'terminated', 'failed'].includes(activeSession.status) && (
                  <section className="instructor-panel">
                    <div className="inspector-title"><Archive size={16} /><h2>教师视图</h2></div>
                    <div className="review-section">
                      <h3>潜在核心冲突</h3>
                      <ul>{((hiddenState.latent_concerns as string[]) || []).map((item) => <li key={item}>{item}</li>)}</ul>
                    </div>
                    <div className="review-section">
                      <h3>渐进披露条件</h3>
                      <ul>{((hiddenState.disclosure_conditions as string[]) || []).map((item) => <li key={item}>{item}</li>)}</ul>
                    </div>
                  </section>
                )}
                {role === 'instructor' && instructorReveal && terminal && (
                  <section className="instructor-panel">
                    <div className="inspector-title"><BookOpenText size={16} /><h2>参考 Persona</h2></div>
                    <div className="review-section">
                      <h3>固定身份</h3>
                      <p>{instructorReveal.reference_identity?.display_name || '模拟来访者'}</p>
                    </div>
                    <div className="review-section">
                      <h3>隐藏事实与披露条件</h3>
                      <ul>
                        {(instructorReveal.hidden_fact_nodes || []).map((node) => (
                          <li key={node.fact_id}>
                            <strong>{node.sensitivity}</strong> {node.content}
                            {node.facilitating_conditions.length > 0 && <small>{node.facilitating_conditions.join('；')}</small>}
                          </li>
                        ))}
                      </ul>
                    </div>
                  </section>
                )}
                {terminal && role === 'instructor' && (
                  <section className="annotation-panel">
                    <div className="inspector-title"><NotebookPen size={16} /><h2>逐轮批注</h2></div>
                    <select value={annotationTurnId} onChange={(event) => setAnnotationTurnId(event.target.value)}>
                      <option value="">选择一个 turn</option>
                      {activeSession.transcript.map((turn) => (
                        <option key={turn.turn_id} value={turn.turn_id}>
                          {turn.turn_index}. {turn.role === 'therapist' ? '治疗师' : '患者'}
                        </option>
                      ))}
                    </select>
                    <textarea value={annotationComment} onChange={(event) => setAnnotationComment(event.target.value)} placeholder="批注" rows={3} />
                    <textarea value={alternativeResponse} onChange={(event) => setAlternativeResponse(event.target.value)} placeholder="替代回应（可选）" rows={3} />
                    <button className="secondary-button" onClick={sendAnnotation} disabled={busy || !annotationTurnId || !annotationComment.trim()}>
                      保存批注
                    </button>
                    {annotations.map((item) => (
                      <div className="annotation-item" key={item.annotation_id}>
                        <strong>{item.turn_id}</strong>
                        <p>{item.comment}</p>
                        {item.alternative_response && <blockquote>{item.alternative_response}</blockquote>}
                      </div>
                    ))}
                  </section>
                )}
                {terminal && !isClientRoleMode && (
                  <section className="comparison-panel">
                    <div className="inspector-title"><GitCompareArrows size={16} /><h2>回应比较</h2></div>
                    {!latestComparison ? (
                      <button className="secondary-button" onClick={generateComparison} disabled={busy || !activeSession.transcript.some((turn) => turn.role === 'patient')}>
                        {busy ? <LoaderCircle className="spin" size={15} /> : <GitCompareArrows size={15} />} 生成三个策略候选
                      </button>
                    ) : (
                      <div className="candidate-list">
                        {latestComparison.candidates.map((candidate) => {
                          const selectedCandidate = selectedCandidateId === candidate.candidate_id
                          return (
                            <button
                              className={`candidate-item ${selectedCandidate ? 'selected' : ''}`}
                              key={candidate.candidate_id}
                              onClick={() => setSelectedCandidateId(candidate.candidate_id)}
                            >
                              <span>{candidate.framework} · {candidate.strategy}</span>
                              <p>{candidate.response}</p>
                              {selectedCandidate && (
                                <div className="candidate-analysis">
                                  <strong>分析</strong>
                                  <p>{candidate.rationale}</p>
                                  <small>适用：{candidate.applicable_context}</small>
                                  <small>局限：{candidate.limitations}</small>
                                </div>
                              )}
                            </button>
                          )
                        })}
                      </div>
                    )}
                  </section>
                )}
                {['completed', 'terminated', 'failed'].includes(activeSession.status) && (
                  <section className="feedback-panel">
                    <div className="inspector-title"><Star size={16} /><h2>会话反馈</h2></div>
                    {feedbackSent ? (
                      <div className="feedback-sent"><CheckCircle2 size={17} /> 已提交</div>
                    ) : (
                      <>
                        <div className="rating-row" aria-label="会话评分">
                          {[1, 2, 3, 4, 5].map((value) => (
                            <button
                              key={value}
                              className={feedbackRating === value ? 'active' : ''}
                              onClick={() => setFeedbackRating(value)}
                              title={`${value} 分`}
                            >
                              {value}
                            </button>
                          ))}
                        </div>
                        <div className="dimension-ratings">
                          {FEEDBACK_DIMENSIONS.map(([key, label]) => (
                            <div className="dimension-row" key={key}>
                              <span>{label}</span>
                              <div className="rating-row" aria-label={label}>
                                {[1, 2, 3, 4, 5].map((value) => (
                                  <button
                                    key={value}
                                    className={feedbackDimensions[key] === value ? 'active' : ''}
                                    onClick={() => setFeedbackDimensions((current) => ({ ...current, [key]: value }))}
                                    title={`${label} ${value} 分`}
                                  >
                                    {value}
                                  </button>
                                ))}
                              </div>
                            </div>
                          ))}
                        </div>
                        <textarea
                          value={feedbackComment}
                          onChange={(event) => setFeedbackComment(event.target.value)}
                          placeholder="记录体验或问题"
                          rows={3}
                        />
                        <div className="feedback-consent">
                          <label><input type="checkbox" checked={feedbackConsent.content_review} onChange={(event) => setFeedbackConsent((current) => ({ ...current, content_review: event.target.checked, ...(!event.target.checked ? { knowledge_derivation: false } : {}) }))} />允许内容评审</label>
                          <label><input type="checkbox" checked={feedbackConsent.research_use} onChange={(event) => setFeedbackConsent((current) => ({ ...current, research_use: event.target.checked }))} />允许研究使用</label>
                          <label><input type="checkbox" checked={feedbackConsent.knowledge_derivation} onChange={(event) => setFeedbackConsent((current) => ({ ...current, knowledge_derivation: event.target.checked, ...(event.target.checked ? { content_review: true } : {}) }))} />允许形成知识候选</label>
                        </div>
                        <button
                          className="secondary-button"
                          onClick={sendFeedback}
                          disabled={feedbackRating < 1 || busy}
                        >
                          提交反馈
                        </button>
                      </>
                    )}
                  </section>
                )}
              </aside>
            </section>
          )}
        </main>
      </div>
      {flagTurnId && (
        <div className="flag-modal-layer">
          <button className="drawer-scrim" onClick={() => setFlagTurnId('')} aria-label="关闭标记" />
          <section className="flag-modal" role="dialog" aria-modal="true" aria-label="标记此回应">
            <header><h2>标记此回应</h2><button className="icon-button" onClick={() => setFlagTurnId('')} title="关闭"><X size={17} /></button></header>
            <select value={flagCategory} onChange={(event) => setFlagCategory(event.target.value)} aria-label="问题类别">
              <option value="unrealistic">表现不真实</option>
              <option value="inappropriate_response">回应不恰当</option>
              <option value="state_inconsistent">前后状态不一致</option>
              <option value="risk_handling_concern">风险处理有疑问</option>
              <option value="other">其他</option>
            </select>
            <textarea value={flagComment} onChange={(event) => setFlagComment(event.target.value)} placeholder="补充说明（可选）" rows={3} />
            <div className="feedback-consent">
              <label><input type="checkbox" checked={flagConsent.content_review} onChange={(event) => setFlagConsent((current) => ({ ...current, content_review: event.target.checked, ...(!event.target.checked ? { knowledge_derivation: false } : {}) }))} />允许内容评审</label>
              <label><input type="checkbox" checked={flagConsent.research_use} onChange={(event) => setFlagConsent((current) => ({ ...current, research_use: event.target.checked }))} />允许研究使用</label>
              <label><input type="checkbox" checked={flagConsent.knowledge_derivation} onChange={(event) => setFlagConsent((current) => ({ ...current, knowledge_derivation: event.target.checked, ...(event.target.checked ? { content_review: true } : {}) }))} />允许形成知识候选</label>
            </div>
            <button className="primary-button" onClick={sendTurnFlag} disabled={busy}>{busy ? <LoaderCircle className="spin" size={15} /> : <Flag size={15} />}提交标记</button>
          </section>
        </div>
      )}
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
    </div>
  )
}
