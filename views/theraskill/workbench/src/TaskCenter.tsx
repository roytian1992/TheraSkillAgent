import { useCallback, useEffect, useRef, useState } from 'react'
import { Archive, ArchiveRestore, ChevronDown, ChevronRight, RefreshCw } from 'lucide-react'

type TaskView = 'active' | 'history' | 'archived'
type Task = {
  key: string; id: string; kind: string; title: string; title_en?: string; status: string; group_status?: string
  created_at: string; updated_at: string; href: string; parent_key?: string
  children: Task[]; archived: boolean; origin: 'user' | 'test'; archive_reason?: string
  can_archive?: boolean; archive_blocked_reason?: string | null
}
type TaskPage = {
  items: Task[]; total: number; page: number; page_size: number
  counts: Record<TaskView, number>; counts_including_test?: Record<TaskView, number>; errors: Array<{ kind: string; message: string }>
}
const kinds = ['workflow', 'extraction', 'expert', 'scenario_batch']
const statuses = ['running', 'queued', 'pending', 'needs_input', 'needs_approval', 'failed', 'interrupted', 'partial', 'unavailable', 'unknown', 'cancel_requested', 'completed', 'cancelled']
const running = new Set(['running', 'queued', 'pending', 'cancel_requested'])
function hasRunning(task: Task): boolean {
  return running.has(task.status) || (task.children || []).some(hasRunning)
}
function canArchive(task: Task): boolean {
  return task.can_archive ?? (!['running', 'queued', 'pending', 'cancel_requested', 'unavailable', 'unknown'].includes(task.status) && (task.children || []).every(canArchive))
}

export default function TaskCenter({ locale, compact = true }: { locale: 'zh' | 'en'; compact?: boolean }) {
  const zh = locale === 'zh'
  const t = (cn: string, en: string) => zh ? cn : en
  const [view, setView] = useState<TaskView>('active')
  const [kind, setKind] = useState('')
  const [status, setStatus] = useState('')
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [includeTest, setIncludeTest] = useState(false)
  const [page, setPage] = useState(1)
  const [result, setResult] = useState<{ query: string; data: TaskPage } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(false)
  const [actionError, setActionError] = useState('')
  const [notice, setNotice] = useState<'archived' | 'restored' | 'restored_test' | null>(null)
  const [mutating, setMutating] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const request = useRef<{ id: number; controller?: AbortController }>({ id: 0 })
  const mounted = useRef(true)
  const queryKey = new URLSearchParams({
    view: compact ? 'active' : view, kind: compact ? '' : kind, status: compact ? '' : status,
    q: compact ? '' : query, page: String(compact ? 1 : page), page_size: compact ? '4' : '20',
    include_test: String(!compact && includeTest),
  }).toString()
  const currentQuery = useRef(queryKey)
  currentQuery.current = queryKey
  const data = result?.query === queryKey ? result.data : null

  const refresh = useCallback(async () => {
    request.current.controller?.abort()
    const controller = new AbortController()
    const id = ++request.current.id
    request.current.controller = controller
    setBusy(true)
    setError(false)
    try {
      const response = await fetch('/api/task-history?' + queryKey, { signal: controller.signal })
      if (!response.ok) throw new Error('task_history_unavailable')
      const next: TaskPage = await response.json()
      if (!Array.isArray(next.items) || !next.counts) throw new Error('invalid_task_history')
      if (id !== request.current.id || currentQuery.current !== queryKey || !mounted.current) return
      const lastPage = Math.max(1, Math.ceil(next.total / next.page_size))
      if (!compact && next.page > lastPage) { setPage(lastPage); return }
      setResult({ query: queryKey, data: next })
    } catch (cause) {
      if (!controller.signal.aborted && id === request.current.id && currentQuery.current === queryKey && mounted.current) setError(true)
    } finally {
      if (id === request.current.id && mounted.current) setBusy(false)
    }
  }, [queryKey, compact])

  const latestRefresh = useRef(refresh)
  latestRefresh.current = refresh

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; request.current.controller?.abort(); ++request.current.id }
  }, [])
  useEffect(() => {
    const timeout = window.setTimeout(() => { setQuery(search.trim()); setPage(1) }, 250)
    return () => window.clearTimeout(timeout)
  }, [search])
  useEffect(() => { void refresh(); return () => request.current.controller?.abort() }, [refresh])
  const hasActiveRun = data?.items.some(hasRunning) ?? false
  useEffect(() => {
    if (!hasActiveRun || (view !== 'active' && !compact)) return
    const timer = window.setInterval(() => { if (!document.hidden && !mutating) void refresh() }, 10000)
    return () => window.clearInterval(timer)
  }, [hasActiveRun, view, compact, refresh, mutating])

  const statusLabel = (value: string) => {
    const labels: Record<string, [string, string]> = {
      queued: ['排队中', 'Queued'], pending: ['处理中', 'Processing'], running: ['运行中', 'Running'],
      completed: ['已完成', 'Completed'], complete: ['已完成', 'Completed'], idle: ['已完成', 'Completed'],
      failed: ['失败，需处理', 'Failed · needs attention'], partial: ['部分完成', 'Partially completed'],
      unavailable: ['状态待确认', 'Status unavailable'], unknown: ['状态待确认', 'Status unknown'], cancel_requested: ['正在取消', 'Cancelling'], needs_input: ['等待输入', 'Needs input'],
      needs_approval: ['等待确认', 'Needs approval'], interrupted: ['已中断', 'Interrupted'], cancelled: ['已取消', 'Cancelled'],
    }
    return labels[value]?.[zh ? 0 : 1] || value.replaceAll('_', ' ')
  }
  const kindLabel = (value: string) => {
    const labels: Record<string, [string, string]> = {
      workflow: ['Skill 工作流', 'Skill workflow'], extraction: ['知识抽取', 'Knowledge extraction'],
      expert: ['审核与内容构建', 'Review & content'], scenario_batch: ['课程场景批量生成', 'Course scenario batch'],
    }
    return labels[value]?.[zh ? 0 : 1] || value
  }
  function changeView(next: TaskView) {
    setView(next); setIncludeTest(next === 'archived'); setStatus(''); setPage(1)
    setActionError(''); setNotice(null)
  }
  function toggleChildren(key: string) {
    setExpanded(previous => { const next = new Set(previous); if (next.has(key)) next.delete(key); else next.add(key); return next })
  }
  async function archive(task: Task) {
    const archiveNow = !task.archived
    setMutating(task.key); setActionError(''); setNotice(null)
    try {
      const response = await fetch('/api/task-history/archive', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ keys: [task.key], archived: archiveNow }),
      })
      if (!response.ok) throw new Error('archive_failed')
      if (!mounted.current) return
      setNotice(archiveNow ? 'archived' : task.origin === 'test' ? 'restored_test' : 'restored')
      await latestRefresh.current()
    } catch {
      if (mounted.current) setActionError(t('操作未保存，请重试；任务记录仍保留。', 'The change could not be saved. Your task records are preserved; please retry.'))
    } finally {
      if (mounted.current) setMutating(null)
    }
  }
  function renderTask(task: Task, child = false) {
    const title = (!zh && task.title_en) || task.title || task.id
    const children = task.children || []
    const open = expanded.has(task.key)
    const blockArchive = !task.archived && !canArchive(task)
    const date = task.updated_at || task.created_at
    const dateValue = new Date(date)
    const dateLabel = Number.isNaN(dateValue.getTime()) ? '' : dateValue.toLocaleString(zh ? 'zh-CN' : 'en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
    const tone = running.has(task.status) ? 'running' : ['completed', 'complete', 'idle'].includes(task.status) ? 'done' : task.status === 'cancelled' ? 'neutral' : 'attention'
    return <article className={`task-entry${child ? ' task-child' : ''}`} key={task.key} data-testid="task-row" data-task-key={task.key} data-task-kind={task.kind}>
      <div className="task-entry-main">
        <div className="task-entry-content">
          <div className="task-entry-labels"><span className="task-kind">{kindLabel(task.kind)}</span>{task.origin === 'test' && <span className="task-test-badge">{t('测试记录', 'Test run')}</span>}<span className={`task-status ${tone}`}>{statusLabel(task.status)}</span></div>
          <a className="task-title" href={task.href}>{title}</a>
          <div className="task-entry-meta">{dateLabel && <time dateTime={date}>{t('更新于 ', 'Updated ')}{dateLabel}</time>}{children.length > 0 && <span>{t(`${children.length} 个子任务`, `${children.length} subtasks`)}</span>}</div>
          {task.group_status && task.group_status !== task.status && <div className="task-group-attention">{t('子任务状态：', 'Subtask status: ')}{statusLabel(task.group_status)}</div>}
        </div>
        {!compact && !child && <button className="task-archive-action" data-testid={task.archived ? 'task-restore' : 'task-archive'} onClick={() => void archive(task)} disabled={!!mutating || blockArchive} title={blockArchive ? t('运行中或状态尚未确认的任务需先处理，再归档。', 'Finish or resolve running and unavailable tasks before archiving.') : undefined} aria-label={`${task.archived ? t('恢复', 'Restore') : t('归档', 'Archive')}：${title}`}>
          {task.archived ? <ArchiveRestore size={15} /> : <Archive size={15} />}
          <span>{mutating === task.key ? t('保存中…', 'Saving…') : task.archived ? t('恢复', 'Restore') : t('归档', 'Archive')}</span>
        </button>}
      </div>
      {!compact && children.length > 0 && <><button className="task-children-toggle" data-testid="task-children-toggle" aria-expanded={open} aria-controls={`children-${task.key}`} onClick={() => toggleChildren(task.key)}>{open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}{open ? t('收起子任务', 'Hide subtasks') : t('查看子任务', 'Show subtasks')}</button>{open && <div className="task-children" id={`children-${task.key}`}>{children.map(item => renderTask(item, true))}</div>}</>}
    </article>
  }
  const totalPages = Math.max(1, Math.ceil((data?.total || 0) / (data?.page_size || 20)))
  const filtered = !!(kind || status || query)
  return <section className={`task-history-view${compact ? ' compact' : ''}`} data-testid="task-history" data-view={view} data-compact={compact} aria-label={t('任务与历史', 'Tasks & history')}>
    {!compact && <>
      <div className="task-tabs" role="tablist" aria-label={t('任务视图', 'Task views')}>{(['active', 'history', 'archived'] as TaskView[]).map(tab => <button key={tab} role="tab" tabIndex={view === tab ? 0 : -1} aria-selected={view === tab} aria-controls="task-history-results" id={`task-tab-${tab}`} data-testid={`task-tab-${tab}`} className={view === tab ? 'active' : ''} onClick={() => changeView(tab)} onKeyDown={event => { if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return; event.preventDefault(); const tabs: TaskView[] = ['active', 'history', 'archived']; const index = event.key === 'Home' ? 0 : event.key === 'End' ? 2 : (tabs.indexOf(tab) + (event.key === 'ArrowRight' ? 1 : 2)) % 3; changeView(tabs[index]); document.getElementById(`task-tab-${tabs[index]}`)?.focus() }}>{tab === 'active' ? t('进行中与待处理', 'Active & attention') : tab === 'history' ? t('历史记录', 'History') : t('已归档', 'Archived')}<span>{tab === 'archived' && view !== 'archived' && !includeTest ? (data?.counts_including_test?.archived ?? data?.counts.archived ?? '—') : (data?.counts[tab] ?? '—')}</span></button>)}</div>
      <div className="task-filters">
        <label className="task-search"><span>{t('搜索任务', 'Search tasks')}</span><input type="search" data-testid="task-search" value={search} onChange={event => setSearch(event.target.value)} placeholder={t('按名称或任务编号搜索', 'Search by title or task ID')} /></label>
        <label><span>{t('任务类型', 'Task type')}</span><select data-testid="task-kind-filter" value={kind} onChange={event => { setKind(event.target.value); setPage(1) }}><option value="">{t('全部类型', 'All types')}</option>{kinds.map(value => <option key={value} value={value}>{kindLabel(value)}</option>)}</select></label>
        <label><span>{t('状态', 'Status')}</span><select data-testid="task-status-filter" value={status} onChange={event => { setStatus(event.target.value); setPage(1) }}><option value="">{t('全部状态', 'All statuses')}</option>{statuses.map(value => <option key={value} value={value}>{statusLabel(value)}</option>)}</select></label>
      </div>
    </>}
    <div className="task-toolbar">
      {compact ? <span>{data ? t(`${data.total} 项进行中或需要处理`, `${data.total} active or needing attention`) : t('查看执行进度与待处理事项', 'Execution progress and items needing attention')}</span> : <label className="task-test-toggle"><input type="checkbox" data-testid="task-include-test" checked={includeTest} onChange={event => { setIncludeTest(event.target.checked); setPage(1) }} />{t('显示测试记录', 'Include test runs')}</label>}
      <button className="task-refresh" data-testid="task-refresh" onClick={() => void refresh()} disabled={busy}><RefreshCw size={14} className={busy ? 'spin' : ''} />{busy ? t('加载中…', 'Loading…') : t('刷新', 'Refresh')}</button>
    </div>
    {!compact && <p className="task-view-help">{view === 'active' ? t('运行中、等待输入或确认，以及失败或中断后需要处理的任务。', 'Runs in progress, requests for input or approval, and failed or interrupted tasks needing attention.') : view === 'history' ? t('已完成或取消的任务，可重新打开结果，也可归档。', 'Completed or cancelled runs. Reopen results or archive a record.') : t('归档会收起记录并保留结果；恢复后会按当前状态回到任务或历史中。', 'Archived records keep their results. Restore a record to return it to its current task or history view.')}</p>}
    {error && <p className="task-notice error" data-testid="task-error" role="alert">{t('任务暂时无法加载。已加载的记录仍保留，请点击刷新重试。', 'Tasks could not be loaded. Previously loaded records remain available; refresh to retry.')}</p>}
    {data?.errors?.map(source => <p className="task-notice error" data-testid="task-error" role="alert" key={source.kind}>{t(`${kindLabel(source.kind)}暂时无法读取，当前列表可能不完整。请刷新重试。`, `${kindLabel(source.kind)} is unavailable. This list may be incomplete; refresh to retry.`)}</p>)}
    {actionError && <p className="task-notice error" data-testid="task-error" role="alert">{actionError}</p>}
    {notice && <p className="task-notice success" role="status">{notice === 'archived' ? t('已归档，可在“已归档”中恢复。', 'Archived. You can restore it from Archived.') : notice === 'restored_test' ? t('测试记录已恢复。进入任务或历史后，勾选“显示测试记录”即可查看。', 'Test run restored. Enable Include test runs in the task or history view to find it.') : t('已恢复，可在当前状态对应的任务或历史中查看。', 'Restored to the task or history view for its current status.')}</p>}
    <div id={compact ? undefined : 'task-history-results'} className="task-history-results" role={compact ? undefined : 'tabpanel'} aria-labelledby={compact ? undefined : `task-tab-${view}`} aria-busy={busy}>
      {data?.items.map(task => renderTask(task))}
      {!data && busy && <p className="task-empty" data-testid="task-empty" role="status">{t('正在加载任务…', 'Loading tasks…')}</p>}
      {data?.items.length === 0 && !busy && !error && !!data.errors?.length && <p className="task-empty" data-testid="task-incomplete">{t('当前没有可显示的记录。部分来源尚未读取成功，请刷新后确认任务状态。', 'No records are available yet. Some sources could not be loaded; refresh to confirm task status.')}</p>}
      {data?.items.length === 0 && !busy && !error && !data.errors?.length && <div className="task-empty" data-testid="task-empty"><strong>{filtered ? t('没有符合条件的记录', 'No matching records') : compact || view === 'active' ? t('目前没有需要处理的任务', 'Nothing needs your attention') : view === 'history' ? t('暂无历史记录', 'No history yet') : t('暂无归档记录', 'No archived records')}</strong><p>{filtered ? t('试试调整搜索条件或筛选项。', 'Try another search or adjust your filters.') : compact || view === 'active' ? t('从工作区开始抽取、审核或批量生成后，可在这里跟进。', 'Start extraction, a review, or batch generation in a workspace, then track it here.') : view === 'history' ? t('完成或取消的任务会保存在这里。', 'Completed and cancelled tasks will appear here.') : t('归档后的任务仍可打开结果，也可以恢复。', 'Archived tasks retain their results and can be restored.')}</p></div>}
    </div>
    {!compact && data && data.total > 0 && <div className="task-pagination"><span>{t(`共 ${data.total} 项任务 · 第 ${data.page} / ${totalPages} 页`, `${data.total} tasks · Page ${data.page} of ${totalPages}`)}</span><div><button data-testid="task-prev" onClick={() => setPage(value => value - 1)} disabled={page <= 1 || busy}>{t('上一页', 'Previous')}</button><button data-testid="task-next" onClick={() => setPage(value => value + 1)} disabled={page >= totalPages || busy}>{t('下一页', 'Next')}</button></div></div>}
    {!compact && <p className="task-scope-note">{t('包含 Skill 工作流、知识抽取、审核与内容构建、课程场景批量任务。同一次执行的子任务合并显示，数量按主任务统计。', 'Includes Skill workflows, knowledge extraction, review and content creation, and course scenario batches. Related subtasks are grouped; counts refer to top-level tasks.')}</p>}
    {compact && <a className="task-view-all" data-testid="task-view-all" href="/?workspace=tasks">{t('查看任务与历史', 'View tasks & history')} →</a>}
  </section>
}
