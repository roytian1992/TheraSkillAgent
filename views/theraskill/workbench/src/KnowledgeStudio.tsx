import { useEffect, useRef, useState } from 'react'
import { CheckCircle2, Database, ExternalLink, Layers3, Plus, Search } from 'lucide-react'
import { KnowledgeJobView } from '../../shared/KnowledgeEntry'
import type { KnowledgeJob } from '../../shared/knowledge'

type Job = { id: string; operation: string; status: string; parameters: Record<string, unknown>; progress?: unknown; result?: unknown; error?: string }
type Resource = { id: string; manifest: { index_id?: string; counts?: Record<string, number>; input?: { mode: string; knowledge_sha256?: string } } }
type Catalog = { indexes: Resource[]; knowledge: Resource[]; active?: { index: string; index_id: string; mode: string }; previous?: unknown; jobs: Job[] }
type Hit = { canonical_label?: string; retrieval_text: string; release_unit_id: string; primary_language?: string; primary_evidence_tier?: string; dedup_family?: string }
type Result = { counts?: Record<string, number>; hits?: Hit[]; matches?: Hit[]; query_understanding?: { normalized_query: string; concepts: string[]; route: { preferred_families: string[] } }; count?: number; warnings?: string[]; embedding_requests_estimate?: number }
type Tab = 'browse' | 'add' | 'manage'

function newIndexId() {
  const date = new Date()
  return `knowledge-${date.toISOString().slice(0, 10).replaceAll('-', '')}-${date.getTime().toString(36)}`
}

export default function KnowledgeStudio({ locale, onBack }: { locale: 'zh' | 'en'; onBack: () => void }) {
  const zh = locale === 'zh'
  const t = (cn: string, en: string) => zh ? cn : en
  const initialJob = new URLSearchParams(location.search).get('job_id') || ''
  const [tab, setTab] = useState<Tab>(initialJob ? 'manage' : 'browse')
  const [catalog, setCatalog] = useState<Catalog>({ indexes: [], knowledge: [], jobs: [] })
  const [source, setSource] = useState('')
  const [indexId, setIndexId] = useState(newIndexId)
  const [mode, setMode] = useState('lexical')
  const [reuseLexical, setReuseLexical] = useState('')
  const [browseIndex, setBrowseIndex] = useState('')
  const [skill, setSkill] = useState('emotional-support')
  const [languageFilter, setLanguageFilter] = useState('')
  const [archive, setArchive] = useState('')
  const [catalogUrl, setCatalogUrl] = useState('')
  const [packageId, setPackageId] = useState('')
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [detail, setDetail] = useState<unknown>(null)
  const [selectedJob, setSelectedJob] = useState(initialJob)
  const [linkedJob, setLinkedJob] = useState<Job | null>(null)
  const [reports, setReports] = useState<Array<{ id: string; created_at: string; gate?: { status: string }; target?: { index_id?: string } }>>([])
  const [behaviorReport, setBehaviorReport] = useState('')
  const restoredSearchJob = useRef('')

  const activeName = catalog.active?.index.split('/').pop() || ''
  const selectedIndex = browseIndex || activeName || catalog.indexes[0]?.id || ''
  const activeResource = catalog.indexes.find(item => item.id === activeName)
  const result = detail as Result | null
  const resultMatches = result?.matches ?? result?.hits ?? []
  const linkedCandidate = linkedJob?.operation === 'reindex'
    ? (linkedJob.result as { index_directory_id?: string } | undefined)?.index_directory_id || String(linkedJob.parameters?.index_id || '')
    : ''
  const currentJob = linkedJob || catalog.jobs.find(job => job.id === selectedJob)
  const resultIndex = currentJob?.operation === 'search' ? String(currentJob.parameters?.index || selectedIndex) : selectedIndex
  const hasRunningJobs = catalog.jobs.some(job => ['queued', 'running'].includes(job.status))
  const indexExists = catalog.indexes.some(item => item.id === indexId)
  const operationName = (name: string) => zh ? ({ plan: '准备构建', build: '构建索引', verify: '检查索引', search: '查找知识', activate: '启用索引', rollback: '恢复上一版', install: '安装资源', reindex: '更新知识索引', catalog: '准备知识目录' }[name] ?? name) : name.replaceAll('_', ' ')
  const statusName = (name: string) => zh ? ({ queued: '等待中', running: '进行中', succeeded: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断' }[name] ?? name) : name
  const familyName = (name: string) => zh ? ({ style_response: '回应风格', therapy_process: '咨询过程', qa_psychoeducation: '心理教育', clinical_risk_assessment: '风险评估', method_rubric: '方法与量规', synthetic_construction: '合成对话方法' }[name] ?? name) : name.replaceAll('_', ' ')
  const hitText = (hit: Hit) => hit.canonical_label && hit.retrieval_text.startsWith(hit.canonical_label + ' ') ? hit.retrieval_text.slice(hit.canonical_label.length + 1) : hit.retrieval_text
  const hitTitle = (hit: Hit) => hit.canonical_label === 'style_example' ? t('回应示例', 'Response example') : hit.canonical_label || familyName(hit.dedup_family || '') || t('知识条目', 'Knowledge entry')
  const count = (value?: number) => typeof value === 'number' ? value.toLocaleString(zh ? 'zh-CN' : 'en-US') : '—'

  async function request(url: string, init?: RequestInit) {
    const response = await fetch('/api/resources' + url, init)
    const data = await response.json()
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data))
    return data
  }
  async function refresh() { setCatalog(await request('')) }
  const selectJob = (id: string) => {
    restoredSearchJob.current = ''
    setSelectedJob(id)
    const url = new URL(location.href)
    url.searchParams.set('job_id', id)
    history.replaceState({}, '', url)
  }
  async function submit(operation: string, parameters: Record<string, unknown>) {
    setBusy(true); setError('')
    try {
      const job = await request('/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ operation, parameters }) })
      selectJob(job.id); setDetail(null); await refresh()
    } catch (reason) { setError(String(reason)) } finally { setBusy(false) }
  }
  useEffect(() => { void refresh().catch(reason => setError(String(reason))) }, [])
  useEffect(() => {
    if (!hasRunningJobs) return
    const timer = window.setInterval(() => void refresh().catch(reason => setError(String(reason))), 3000)
    return () => window.clearInterval(timer)
  }, [hasRunningJobs])
  useEffect(() => {
    if (tab !== 'manage') return
    void fetch('/api/evaluations').then(response => response.ok ? response.json() : { reports: [] })
      .then(data => setReports(data.reports || [])).catch(() => setReports([]))
  }, [tab])
  useEffect(() => {
    const job = catalog.jobs.find(item => item.id === selectedJob)
    if (job) setDetail(job.result ?? null)
  }, [catalog, selectedJob])
  useEffect(() => {
    const job = linkedJob || catalog.jobs.find(item => item.id === selectedJob)
    if (!job || job.id !== selectedJob || job.operation !== 'search' || restoredSearchJob.current === job.id) return
    restoredSearchJob.current = job.id
    setQuery(String(job.parameters?.query || ''))
    setSkill(String(job.parameters?.skill || 'emotional-support'))
    setBrowseIndex(String(job.parameters?.index || ''))
    const filters = job.parameters?.filters as { language?: string } | undefined
    setLanguageFilter(String(filters?.language || ''))
  }, [catalog.jobs, linkedJob, selectedJob])
  useEffect(() => {
    setLinkedJob(null)
    if (!selectedJob) return
    let cancelled = false, timer: number | undefined
    const controller = new AbortController()
    const poll = async () => {
      try {
        const response = await fetch('/api/resources/jobs/' + encodeURIComponent(selectedJob), { signal: controller.signal })
        const value = await response.json()
        if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : 'Unable to load resource job')
        if (cancelled) return
        setLinkedJob(value); setDetail(value.result ?? null)
        if (['queued', 'running'].includes(value.status)) timer = window.setTimeout(poll, 1500)
        else void refresh().catch(reason => setError(String(reason)))
      } catch (reason) { if (!cancelled) setError(String(reason)) }
    }
    void poll()
    return () => { cancelled = true; controller.abort(); if (timer) window.clearTimeout(timer) }
  }, [selectedJob])

  const search = () => selectedIndex && query.trim() && void submit('search', {
    index: selectedIndex, query: query.trim(), skill, production: true,
    ...(languageFilter ? { filters: { language: languageFilter } } : {}),
  })
  const uploadKnowledge = async (file?: File) => {
    if (!file) return
    setBusy(true); setError('')
    try { const value = await request('/upload', { method: 'POST', body: file }); setSource(value.knowledge); setDetail(value.plan); await refresh() }
    catch (reason) { setError(String(reason)) } finally { setBusy(false) }
  }
  const uploadPackage = async (file?: File) => {
    if (!file) return
    setBusy(true); setError('')
    try { const value = await request('/upload-package', { method: 'POST', body: file }); selectJob(value.id); setTab('manage'); await refresh() }
    catch (reason) { setError(String(reason)) } finally { setBusy(false) }
  }
  const reportsFor = (item: Resource) => reports.filter(report => report.gate?.status === 'passed' && report.target?.index_id === item.manifest.index_id)
  const evidenceHref = (hit: Hit) => {
    const resource = catalog.indexes.find(item => item.id === resultIndex)
    const params = new URLSearchParams({ workspace: 'knowledge', knowledge_id: hit.release_unit_id })
    if (resource?.manifest.index_id) params.set('index_id', resource.manifest.index_id)
    if (resource?.manifest.input?.knowledge_sha256) params.set('knowledge_sha256', resource.manifest.input.knowledge_sha256)
    return '/?' + params.toString()
  }

  return <section className="knowledge-studio knowledge-simple">
    <button className="back-button" onClick={onBack}>← {t('返回工作台', 'Back to workbench')}</button>
    <div className="workspace-title"><h1>{t('知识与索引', 'Knowledge & indexes')}</h1><p>{t('先查找已有知识。需要添加内容或切换知识版本时，再进入对应页面。', 'Search existing knowledge first. Add content or switch versions when needed.')}</p></div>
    {error && <p role="alert" className="resource-error">{error}</p>}
    <div className="knowledge-current" role="status"><Database size={18} /><div><strong>{activeName ? t('当前可用的知识', 'Knowledge in use') : t('尚未启用知识索引', 'No active knowledge index')}</strong><span>{activeName ? `${activeName} · ${count(activeResource?.manifest.counts?.indexed)} ${t('条可检索记录', 'searchable records')}` : t('可以先安装知识包，或构建一个索引。', 'Install a knowledge package or build an index to get started.')}</span></div>{activeName && <CheckCircle2 size={17} />}</div>
    <nav className="knowledge-tabs" aria-label={t('知识操作', 'Knowledge actions')}>
      {([['browse', Search, t('查找知识', 'Find knowledge')], ['add', Plus, t('添加知识', 'Add knowledge')], ['manage', Layers3, t('管理版本', 'Manage versions')]] as const).map(([name, Icon, label]) =>
        <button key={name} className={tab === name ? 'active' : ''} aria-current={tab === name ? 'page' : undefined} onClick={() => setTab(name)}><Icon size={16} />{label}</button>)}
    </nav>

    {tab === 'browse' && <div className="knowledge-flow"><div className="knowledge-flow-heading"><h2>{t('想找什么知识？', 'What knowledge are you looking for?')}</h2><p>{t('输入一个真实问题，例如“首次会谈如何回应焦虑”。结果来自现有知识，不会生成新条目。', 'Enter a real question, such as “How should I respond to anxiety in a first session?” Results come from existing knowledge.')}</p></div>
      {catalog.indexes.length === 0 ? <div className="knowledge-empty">{t('还没有可检索的知识。', 'No searchable knowledge is installed.')} <button onClick={() => setTab('add')}>{t('添加知识', 'Add knowledge')}</button></div> : <>
        <div className="knowledge-search-row"><input aria-label={t('查找知识', 'Find knowledge')} value={query} onChange={event => setQuery(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') search() }} placeholder={t('描述你想解决的问题', 'Describe the question you want to answer')} /><button className="primary" disabled={busy || !query.trim()} onClick={search}><Search size={16} />{t('查找', 'Search')}</button></div>
        <div className="knowledge-search-options"><label>{t('用于哪个功能', 'Used for')}<select value={skill} onChange={event => setSkill(event.target.value)}><option value="emotional-support">{t('支持对话', 'Support conversation')}</option><option value="risk-triage">{t('风险沟通', 'Risk communication')}</option><option value="cognitive-reframing-support">{t('认知重构', 'Cognitive reframing')}</option><option value="rebt-belief-work-support">REBT</option><option value="synthetic-dialogue-builder">{t('情景生成', 'Scenario creation')}</option><option value="therapy-response-reviewer">{t('回应评阅', 'Response review')}</option></select></label><details><summary>{t('更换知识版本或筛选语言', 'Choose another version or filter language')}</summary><div className="knowledge-search-filters"><label>{t('知识版本', 'Knowledge version')}<select value={selectedIndex} onChange={event => setBrowseIndex(event.target.value)}>{catalog.indexes.map(item => <option key={item.id} value={item.id}>{item.id}{item.id === activeName ? t('（当前使用）', ' (active)') : ''}</option>)}</select></label><label>{t('语言', 'Language')}<select value={languageFilter} onChange={event => setLanguageFilter(event.target.value)}><option value="">{t('全部语言', 'All languages')}</option><option value="zh">中文</option><option value="en">English</option></select></label></div></details></div>
        {currentJob?.operation === 'search' && <p className="knowledge-job-status" role="status">{t('查找知识', 'Search')} · {statusName(currentJob.status)}{currentJob.error && ' · ' + currentJob.error}</p>}
        {currentJob?.operation === 'search' && result && <section className="knowledge-search-results"><h3>{t('找到的知识', 'Knowledge found')} · {count(result.count ?? resultMatches.length)}</h3>{result.warnings?.map(warning => <p key={warning} role="status">{warning}</p>)}{resultMatches.length === 0 && <p>{t('没有找到匹配内容。可以换一种问法，或检查所选知识版本。', 'No matches found. Try rephrasing or checking the selected knowledge version.')}</p>}{resultMatches.map(hit => <article className="knowledge-hit" key={hit.release_unit_id}><div><strong>{hitTitle(hit)}</strong><span>{[hit.dedup_family ? familyName(hit.dedup_family) : '', hit.primary_language?.toUpperCase()].filter(Boolean).join(' · ')}</span></div><p>{hitText(hit).slice(0, 280)}{hitText(hit).length > 280 ? '…' : ''}</p><details><summary>{t('全文与来源类型', 'Full text and source type')}</summary><p>{hit.retrieval_text}</p>{hit.primary_evidence_tier && <small>{t('来源类型', 'Source type')}: {hit.primary_evidence_tier}</small>}</details><a href={evidenceHref(hit)}>{t('查看与编辑知识', 'View and edit knowledge')} <ExternalLink size={14} /></a></article>)}{result.query_understanding && <details><summary>{t('查看系统如何理解问题', 'How the question was interpreted')}</summary><p>{result.query_understanding.normalized_query}</p><p>{result.query_understanding.route.preferred_families.map(familyName).join(' · ')}</p><p>{result.query_understanding.concepts.join(', ')}</p></details>}</section>}
      </>}</div>}

    {tab === 'add' && <div className="knowledge-flow"><div className="knowledge-flow-heading"><h2>{t('添加知识', 'Add knowledge')}</h2><p>{t('已有知识包可直接上传；自己整理的 JSONL 可以上传后构建检索版本。普通文本请先到知识抽取工作区整理。', 'Upload a knowledge package directly. Upload structured JSONL to build a searchable version. For raw text, start in Knowledge extraction.')}</p></div>
      <div className="knowledge-add-grid"><section><h3>{t('安装收到的资源包', 'Install a resource package')}</h3><p>{t('选择 ZIP 文件。系统会检查包内容；如包含可检索版本，完成后可在“管理版本”中启用。', 'Choose a ZIP file. The system checks its contents; if it includes a searchable version, you can activate it from Manage versions.')}</p><label className="knowledge-file-picker">{t('选择资源包 ZIP', 'Choose resource ZIP')}<input aria-label="Resource ZIP" type="file" accept=".zip" disabled={busy} onChange={event => void uploadPackage(event.target.files?.[0])} /></label></section><section><h3>{t('使用自己的结构化知识', 'Use structured knowledge')}</h3><p>{t('上传 JSONL 文件，或选择已安装的知识。构建完成后需检查并启用。', 'Upload a JSONL file or choose installed knowledge. Check and activate the built index afterwards.')}</p><label>{t('选择已安装知识', 'Select installed knowledge')}<select value={source && catalog.knowledge.some(item => item.id === source) ? source : ''} onChange={event => setSource(event.target.value)}><option value="">{t('请选择', 'Choose knowledge')}</option>{catalog.knowledge.map(item => <option key={item.id} value={item.id}>{item.id} · {count(item.manifest.counts?.total)} {t('条', 'records')}</option>)}</select></label><label className="knowledge-file-picker">{t('上传 JSONL', 'Upload JSONL')}<input type="file" accept=".jsonl" disabled={busy} onChange={event => void uploadKnowledge(event.target.files?.[0])} /></label>{source && <p className="knowledge-selected-source">{t('已选择：', 'Selected: ')}{source}</p>}
          <label>{t('查找方式', 'Search method')}<select value={mode} onChange={event => setMode(event.target.value)}><option value="lexical">{t('快速搜索（无需模型服务）', 'Fast search (no model service)')}</option><option value="hybrid">{t('智能匹配（理解相近说法）', 'Meaning-based search')}</option></select></label><p className="knowledge-cost-note">{mode === 'hybrid' ? t('智能匹配需要语义检索模型。大型知识库可能构建很久，并产生模型服务费用。', 'Meaning-based search needs a semantic model. Large collections may take a long time to build and incur provider costs.') : t('建议先用快速搜索，不产生模型费用。若不同说法难以匹配，再选智能匹配。', 'Start with fast search without model charges. Try meaning-based search if different wording does not match well.')}</p>
          <details><summary>{t('高级构建选项', 'Advanced build options')}</summary><label>{t('新版本 ID', 'New version ID')}<input value={indexId} onChange={event => setIndexId(event.target.value)} /></label><label>{t('知识文件路径或 ID', 'Knowledge file path or ID')}<input value={source} onChange={event => setSource(event.target.value)} placeholder="examples/knowledge/demo.jsonl" /></label>{mode === 'hybrid' && <label>{t('复用已有关键词数据', 'Reuse keyword data')}<select value={reuseLexical} onChange={event => setReuseLexical(event.target.value)}><option value="">{t('重新构建', 'Build from scratch')}</option>{catalog.indexes.map(item => <option key={item.id} value={item.id}>{item.id}</option>)}</select></label>}<button disabled={busy || !source} onClick={() => { setTab('manage'); void submit('plan', { knowledge: source, index_id: indexId, mode }) }}>{t('预览工作量', 'Preview workload')}</button></details>
          {indexExists && <p role="alert">{t('该版本 ID 已存在，请修改高级选项中的 ID。', 'That version ID already exists. Change it in Advanced options.')}</p>}
          <button className="primary" disabled={busy || !source || !indexId || indexExists} onClick={() => { setTab('manage'); void submit('build', { knowledge: source, index_id: indexId, mode, ...(mode === 'hybrid' && reuseLexical ? { reuse_lexical: reuseLexical } : {}) }) }}>{t('构建可检索版本', 'Build searchable version')}</button></section></div>
      <p><a href="/?workspace=extraction">{t('只有原始文本？前往知识抽取', 'Have raw text? Open Knowledge extraction')} <ExternalLink size={14} /></a></p>
      <details className="knowledge-remote-install"><summary>{t('高级：从服务器路径或下载目录安装', 'Advanced: install from a server path or catalog')}</summary><label>{t('服务器压缩包路径', 'Server archive path')}<input value={archive} onChange={event => setArchive(event.target.value)} /></label><button disabled={busy || !archive} onClick={() => { setTab('manage'); void submit('install', { archive }) }}>{t('安装本地包', 'Install local package')}</button><label>{t('下载目录 URL 或本地路径', 'Catalog URL or local path')}<input value={catalogUrl} onChange={event => setCatalogUrl(event.target.value)} /></label><label>{t('资源包 ID', 'Package ID')}<input value={packageId} onChange={event => setPackageId(event.target.value)} /></label><button disabled={busy || !catalogUrl || !packageId} onClick={() => { setTab('manage'); void submit('install', { catalog: catalogUrl, package: packageId }) }}>{t('下载并安装', 'Download and install')}</button></details>
    </div>}

    {tab === 'manage' && <div className="knowledge-flow"><div className="knowledge-flow-heading"><h2>{t('管理知识版本', 'Manage knowledge versions')}</h2><p>{t('构建或安装新版本后，先检查，再比较效果，最后启用。当前使用的版本不会被自动替换。', 'After building or installing a version, check it, compare its behavior, then activate it. The current version is never replaced automatically.')}</p>{activeName && <a href="/?workspace=evaluation">{t('为当前版本建立效果基线', 'Establish a behavior baseline for the current version')} <ExternalLink size={13} /></a>}</div>
      {linkedJob?.operation === 'reindex' && <div className="knowledge-entry"><KnowledgeJobView job={{ ...linkedJob, result: linkedJob.result as KnowledgeJob['result'], progress: linkedJob.progress as KnowledgeJob['progress'], resources_url: undefined }} locale={locale} />{linkedCandidate && <p className="ke-notice">{catalog.indexes.some(item => item.id === linkedCandidate) ? <>{t('这个候选版本已准备好，可以检查后启用。', 'This candidate is ready to check before activation.')} <a href={'#resource-index-' + encodeURIComponent(linkedCandidate)}>{linkedCandidate}</a></> : t('候选版本准备就绪后，会出现在下方列表。', 'The candidate will appear below when ready.')}</p>}</div>}
      {currentJob && <section className="knowledge-job-status" role="status"><strong>{operationName(currentJob.operation)} · {statusName(currentJob.status)}</strong>{currentJob.error && <p role="alert">{currentJob.error}</p>}{['queued', 'running'].includes(currentJob.status) && <p>{t('任务在后台进行，关闭页面后仍可回来查看。', 'The task continues in the background; you can return later.')}</p>}{currentJob.progress != null && currentJob.operation !== 'reindex' && <details><summary>{t('查看进度详情', 'View progress')}</summary><pre>{JSON.stringify(currentJob.progress, null, 2)}</pre></details>}{['failed', 'cancelled', 'interrupted'].includes(currentJob.status) && currentJob.operation === 'build' && <button disabled={busy} onClick={() => void submit('build', { ...currentJob.parameters, resume: true })}>{t('从已保存进度继续', 'Resume saved progress')}</button>}{['queued', 'running'].includes(currentJob.status) && <button onClick={() => void request(`/jobs/${currentJob.id}/cancel`, { method: 'POST' }).then(refresh).catch(reason => setError(String(reason)))}>{t('取消', 'Cancel')}</button>}</section>}
      {currentJob?.operation === 'plan' && result?.counts && <div className="knowledge-plan-summary" role="status"><strong>{t('预计处理', 'Estimated workload')}: {count(result.counts.total)} {t('条知识，其中', 'records, with')} {count(result.counts.eligible)} {t('条可用于查找', 'searchable')}</strong><p>{t('预计模型请求', 'Estimated model requests')}: {count(result.embedding_requests_estimate)}</p><button onClick={() => setTab('add')}>{t('返回并生成版本', 'Return to build a version')}</button></div>}
      {catalog.indexes.length === 0 && <div className="knowledge-empty">{t('还没有知识版本。', 'No knowledge versions yet.')} <button onClick={() => setTab('add')}>{t('添加知识', 'Add knowledge')}</button></div>}
      <div className="knowledge-version-list">{catalog.indexes.map(item => { const current = item.id === activeName; const availableReports = reportsFor(item); const canActivate = !catalog.active || current || !!behaviorReport && availableReports.some(report => report.id === behaviorReport); return <article className={'knowledge-version' + (linkedCandidate === item.id ? ' selected-candidate' : '')} id={'resource-index-' + encodeURIComponent(item.id)} key={item.id}><div className="knowledge-version-heading"><div><strong>{item.id}</strong><span>{count(item.manifest.counts?.indexed)} {t('条可检索记录', 'searchable records')} · {item.manifest.input?.mode === 'hybrid' ? t('语义增强', 'Semantic search') : t('快速搜索', 'Fast search')}</span></div>{current && <em>{t('当前使用', 'Active')}</em>}</div><div className="resource-actions"><button disabled={busy} onClick={() => void submit('verify', { index: item.id })}>{t('验证', 'Verify')}</button><button disabled={busy || current || !canActivate} onClick={() => void submit('activate', { index: item.id, report_id: behaviorReport || undefined })}>{t('启用', 'Activate')}</button><button onClick={() => { setBrowseIndex(item.id); setTab('browse') }}>{t('查找测试', 'Try searching')}</button></div>{!current && catalog.active && <div className="knowledge-version-review"><span>{t('更换当前版本需要通过效果比较和人工复核。', 'Changing the active version requires a passing comparison and human review.')}</span><a href={'/?workspace=evaluation&index=' + encodeURIComponent(item.id)}>{t('检查这个版本', 'Check this version')} <ExternalLink size={13} /></a>{availableReports.length > 0 && <label>{t('通过检查的报告', 'Passing report')}<select value={availableReports.some(report => report.id === behaviorReport) ? behaviorReport : ''} onChange={event => setBehaviorReport(event.target.value)}><option value="">{t('选择报告', 'Choose a report')}</option>{availableReports.map(report => <option key={report.id} value={report.id}>{report.created_at} · {report.id}</option>)}</select></label>}</div>}</article> })}</div>
      {Boolean(catalog.previous) && <details className="knowledge-rollback"><summary>{t('恢复上一个版本', 'Restore previous version')}</summary><p>{t('将之前使用的知识版本重新设为当前版本。', 'Make the previously active knowledge version current again.')}</p><button disabled={busy} onClick={() => void submit('rollback', {})}>{t('确认恢复', 'Confirm restore')}</button></details>}
      <details className="knowledge-history"><summary>{t('查看近期任务与技术结果', 'View recent jobs and technical results')}</summary>{catalog.jobs.map(job => <div className="resource-item" key={job.id}><strong>{operationName(job.operation)} · {statusName(job.status)}</strong><button onClick={() => { selectJob(job.id); setDetail(job.result ?? null) }}>{t('查看', 'View')}</button></div>)}{result && <div className="resource-result">{result.counts && <p>{t('记录总数', 'Total records')}: {count(result.counts.total)} · {t('可检索', 'Searchable')}: {count(result.counts.eligible)} · {t('向量', 'Vectors')}: {count(result.counts.vectors)}</p>}{result.embedding_requests_estimate !== undefined && <p>{t('预计 embedding 请求', 'Estimated embedding requests')}: {count(result.embedding_requests_estimate)}</p>}<pre className="resource-detail">{JSON.stringify(detail, null, 2)}</pre></div>}</details>
    </div>}
  </section>
}
