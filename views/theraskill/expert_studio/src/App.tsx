import { useEffect, useState } from 'react'
import { BookPlus, ClipboardCheck, Download, FilePenLine, FlaskConical, FolderInput, ListChecks, LoaderCircle, RefreshCw, Save, ShieldCheck } from 'lucide-react'
import { createJob, downloadExport, getJob, governanceIdentity, health, importCase, listCases, regenerate, saveVersion } from './api'
import ReviewQueue from './ReviewQueue'
import KnowledgeProposal from './KnowledgeProposal'
import CandidateGovernance from './CandidateGovernance'
import { useLocale } from './i18n'

type Tab = 'queue' | 'knowledge' | 'governance' | 'review' | 'content' | 'cases'

export default function App() {
  const { locale, toggleLocale } = useLocale()
  const [tab, setTab] = useState<Tab>(() => {
    const view = new URLSearchParams(window.location.search).get('view')
    return view === 'queue' ? 'queue' : view === 'case' ? 'cases' : view === 'governance' ? 'governance' : 'review'
  })
  const [online, setOnline] = useState(false)
  const [input, setInput] = useState('')
  const [job, setJob] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [framework, setFramework] = useState('CBT')
  const [turns, setTurns] = useState(6)
  const [instruction, setInstruction] = useState('')
  const [edited, setEdited] = useState('')
  const [sessionId, setSessionId] = useState('')
  const [caseTitle, setCaseTitle] = useState('')
  const [cases, setCases] = useState<any[]>([])
  const [governance, setGovernance] = useState<any>(null)

  useEffect(() => {
    health().then((value) => setOnline(value.status === 'ok' && value.support === 'ok'))
    listCases().then((value) => setCases(value.cases)).catch(() => {})
    governanceIdentity().then(setGovernance).catch(() => {})
    const jobId = new URLSearchParams(window.location.search).get('job_id')
    if (jobId) getJob(jobId).then(setJob).catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }, [])
  useEffect(() => {
    if (!job || job.status !== 'pending') return
    const timer = window.setInterval(() => getJob(job.job_id).then((value) => {
      setJob(value)
      if (value.status !== 'pending') window.clearInterval(timer)
    }).catch(() => {}), 1000)
    return () => window.clearInterval(timer)
  }, [job?.job_id, job?.status])
  useEffect(() => {
    if (job?.result) setEdited(JSON.stringify(job.result.structured_content || { text: job.result.text }, null, 2))
  }, [job?.result?.message_id])

  function changeTab(value: Tab) { setTab(value); setJob(null); setEdited('') }
  async function run() {
    if (!input.trim()) return
    setBusy(true); setError('')
    try {
      const payload = tab === 'review' ? { transcript_or_response: input } : { framework, turn_count: turns, goal: input, source_type: 'synthetic' }
      setJob(await createJob(tab === 'review' ? 'response_review' : 'content_lab', payload))
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) } finally { setBusy(false) }
  }
  async function save() {
    if (!job?.result) return
    try { await saveVersion(job.job_id, JSON.parse(edited), 'expert edit snapshot'); setJob(await getJob(job.job_id)) }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
  }
  async function runRegenerate() {
    if (!job || !instruction.trim()) return
    await regenerate(job.job_id, instruction); setJob({ ...job, status: 'pending' }); setInstruction('')
  }
  async function addCase() {
    const value = await importCase(sessionId, caseTitle)
    setCases([value, ...cases]); setSessionId(''); setCaseTitle('')
  }
  const result = job?.result
  return <div className="shell">
    <header><div><strong>TheraSkill</strong><span>Expert & Content Studio</span></div><div className="header-tools"><small className={online ? 'online' : ''}>{online ? '服务已连接' : '服务未连接'}</small><button className="language-toggle" onClick={toggleLocale} title={locale === 'zh' ? '切换到英文' : 'Switch to Chinese'} aria-label={locale === 'zh' ? '切换到英文' : 'Switch to Chinese'}>{locale === 'zh' ? 'EN' : '中文'}</button></div></header>
    <nav>
      <button className={tab === 'queue' ? 'active' : ''} onClick={() => changeTab('queue')}><ListChecks />反馈审阅</button>
      <button className={tab === 'knowledge' ? 'active' : ''} onClick={() => changeTab('knowledge')}><BookPlus />知识贡献</button>
      {['knowledge_curator','adjudicator','release_manager','admin'].includes(governance?.contribution_role) && <button className={tab === 'governance' ? 'active' : ''} onClick={() => changeTab('governance')}><ShieldCheck />候选治理</button>}
      <button className={tab === 'review' ? 'active' : ''} onClick={() => changeTab('review')}><ClipboardCheck />回应审核</button>
      <button className={tab === 'content' ? 'active' : ''} onClick={() => changeTab('content')}><FlaskConical />内容实验室</button>
      <button className={tab === 'cases' ? 'active' : ''} onClick={() => changeTab('cases')}><FolderInput />案例工作区</button>
    </nav>
    <main>{error && <div className="error">{error}</div>}{tab === 'queue' ? <ReviewQueue /> : tab === 'knowledge' ? <KnowledgeProposal /> : tab === 'governance' ? <CandidateGovernance identity={governance} /> : tab !== 'cases' ? <div className="workspace">
      <section className="editor"><h1>{tab === 'review' ? '治疗回应审核' : '合成对话构建'}</h1><p>{tab === 'review' ? '导入回应或 transcript，生成可追溯的专业审阅报告。' : '生成明确标记为 synthetic 的教学对话，并分离 dialogue、rationale 和 construction metadata。'}</p>
        {tab === 'content' && <div className="row"><label>框架<select value={framework} onChange={(event) => setFramework(event.target.value)}><option>CBT</option><option>REBT</option><option>支持性咨询</option><option>整合取向</option></select></label><label>轮数<input type="number" min="2" max="20" value={turns} onChange={(event) => setTurns(Number(event.target.value))} /></label></div>}
        <textarea value={input} onChange={(event) => setInput(event.target.value)} placeholder={tab === 'review' ? '粘贴治疗师回应或完整 transcript' : '填写角色、阶段、教学目标与约束'} />
        <button className="primary" onClick={run} disabled={busy || !input.trim()}>{busy ? <LoaderCircle className="spin" /> : <FilePenLine />}开始</button>
      </section>
      <section className="result"><div className="result-head"><h2>结果</h2>{result && <div><button onClick={() => downloadExport(job.job_id, 'json')}><Download />JSON</button><button onClick={() => downloadExport(job.job_id, 'markdown')}><Download />Markdown</button></div>}</div>
        {job?.status === 'pending' ? <div className="pending"><LoaderCircle className="spin" />正在检索并生成</div> : result ? <>
          <div className="source">{result.presentation_mode} · generated with evidence</div><p className="response">{result.text}</p>
          <textarea className="output-editor" value={edited} onChange={(event) => setEdited(event.target.value)} />
          <div className="actions"><button onClick={save}><Save />保存专家版本</button><input value={instruction} onChange={(event) => setInstruction(event.target.value)} placeholder="局部重生成要求" /><button onClick={runRegenerate} disabled={!instruction.trim()}><RefreshCw />重生成</button></div>
          {job.versions?.length > 0 && <details><summary>专家版本（{job.versions.length}）</summary><pre>{JSON.stringify(job.versions, null, 2)}</pre></details>}
          <details><summary>Provenance</summary><pre>{JSON.stringify(job.provenance, null, 2)}</pre></details>
        </> : <div className="empty">尚未生成结果</div>}
      </section>
    </div> : <section className="cases"><h1>案例与策略工作区</h1><div className="case-import"><input value={sessionId} onChange={(event) => setSessionId(event.target.value)} placeholder="终态 simulation session ID" /><input value={caseTitle} onChange={(event) => setCaseTitle(event.target.value)} placeholder="案例标题" /><button className="primary" onClick={addCase} disabled={!sessionId || !caseTitle}><FolderInput />导入案例草稿</button></div>{cases.map((value) => <article key={value.case_draft_id}><strong>{value.title}</strong><span>{value.source_type}</span><p>{value.public_view?.scenario?.scenario_brief}</p></article>)}</section>}</main>
  </div>
}
