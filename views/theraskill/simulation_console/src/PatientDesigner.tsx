import { useEffect, useRef, useState } from 'react'
import { tr } from '../../shared/i18n'
import type { CallerRole, Scenario, SessionLanguage } from './types'
import { buildPatient, forkPatient, inspectPatient, updateGeneratedScenario, startPatientJob, getPatientJob, listPatientJobs, retryPatientJob } from './api'
import type { PatientDesign, PatientJob } from './api'
import { cleanPatient, emptyPatient, PatientEditor } from './PatientEditor'
import { knowledgeHref } from '../../shared/knowledge'
import KnowledgeEntry from '../../shared/KnowledgeEntry'

function groundingItem(content: string): Record<string, any> {
  try { const parsed = JSON.parse(content); return parsed && typeof parsed === 'object' ? parsed : {} } catch { return {} }
}

function GroundingSource({ item, index, indexId }: { item: { retrieval_id: string; content: string }; index: number; indexId?: string }) {
  const [open, setOpen] = useState(false)
  const source = groundingItem(item.content)
  const id = source.release_unit_id || source.unit_id || item.retrieval_id
  const citedIndex = source.source_index_id || indexId
  const locale = localStorage.getItem('theraskill-locale') === 'en' ? 'en' : 'zh'
  return <details className="patient-grounding-source" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>{source.canonical_label && source.canonical_label !== 'style_example' ? source.canonical_label : `${tr('参考知识')} ${index + 1}`}</summary>
    {open && <KnowledgeEntry id={id} indexId={citedIndex} checksum={source.knowledge_sha256} locale={locale} />}
    <a href={knowledgeHref(id, citedIndex, source.knowledge_sha256)}>{tr('在知识工作区打开')}</a>
  </details>
}

export function PatientDesigner({ scenario, role, onSaved }: { scenario: Scenario; role: CallerRole; onSaved: (scenario: Scenario) => void }) {
  const [design, setDesign] = useState<PatientDesign | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  async function run(action: 'inspect' | 'save' | 'fork') {
    setBusy(true); setError(''); setNotice('')
    try {
      if (action === 'inspect') setDesign(await inspectPatient(role, scenario.scenario_id))
      if (action === 'fork') { const result = await forkPatient(role, scenario.scenario_id); onSaved(result.scenario) }
      if (action === 'save' && design) {
        const result = await updateGeneratedScenario(role, scenario.scenario_id, { patient: cleanPatient(design.patient), expected_revision: design.revision })
        onSaved(result.scenario); setDesign(await inspectPatient(role, scenario.scenario_id))
        setNotice(tr('已保存新版本；已有会话继续使用原版本。'))
      }
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) } finally { setBusy(false) }
  }
  return <section className="detail-band patient-design-band">
    <h2>{tr('患者档案与经历')}</h2>
    <p>{tr('查看背景、近期经历和隐藏设定，或复制成一个新场景继续修改。')}</p>
    <div className="governance-actions"><button className="secondary-button" disabled={busy} onClick={() => void run('inspect')}>{tr('查看和编辑患者档案')}</button><button className="secondary-button" disabled={busy} onClick={() => void run('fork')}>{tr('复制为新场景')}</button></div>
    {error && <p role="alert" className="error-banner">{error}</p>}{notice && <p role="status">{notice}</p>}
    {design && <div>
      <p>{tr('版本')} {design.revision} · {tr('知识支撑')}：{design.grounding.status === 'grounded' ? tr('已检索相关知识') : tr('未获得检索支撑')}{design.grounding.reason && <> · {design.grounding.reason === 'No active knowledge index' ? tr('尚未启用知识索引') : design.grounding.reason}</>}</p>
      {!!design.grounding.items?.length && <details className="patient-grounding"><summary>{tr('查看知识来源')} ({design.grounding.items.length})</summary>{design.grounding.items.map((item, index) => <GroundingSource key={item.retrieval_id} item={item} index={index} indexId={design.grounding.index_id} />)}</details>}
      {!scenario.generated && <p>{tr('已有资源病例请先复制，再编辑新场景。')}</p>}
      <fieldset disabled={!scenario.generated || busy} className="patient-dossier-fields"><PatientEditor value={design.patient} onChange={patient => setDesign({ ...design, patient })} prefix={`edit-${scenario.scenario_id}`} /></fieldset>
      {scenario.generated && <button className="primary-button" disabled={busy || !design.patient.presenting_concern.trim()} onClick={() => void run('save')}>{busy ? tr('正在保存…') : tr('保存患者新版本')}</button>}
    </div>}
  </section>
}

export function PatientBuilder({ role, language, onSaved }: { role: CallerRole; language: SessionLanguage; onSaved: (scenario: Scenario) => void }) {
  const [brief, setBrief] = useState('')
  const [patient, setPatient] = useState(emptyPatient)
  const [family, setFamily] = useState('supportive_exploration')
  const [difficulty, setDifficulty] = useState('medium')
  const [risk, setRisk] = useState('low')
  const [useKnowledge, setUseKnowledge] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [job, setJob] = useState<PatientJob | null>(null)
  const [jobs, setJobs] = useState<PatientJob[]>([])
  const notified = useRef('')
  const onSavedRef = useRef(onSaved)
  onSavedRef.current = onSaved
  const running = !!job && ['queued', 'running'].includes(job.status)
  const stageLabels: Record<string, string> = { knowledge: '检索构建知识', plan: '规划患者', patient: '构建档案', package: '构建场景', review: '审阅候选', repair: '定向修复', re_review: '修复后复验' }
  useEffect(() => {
    let active = true
    setJob(null); setJobs([]); setError('')
    void listPatientJobs(role).then(result => {
      if (!active) return
      const rows = result.jobs.filter(row => row.request.language === language)
      setJobs(rows)
      const saved = window.localStorage.getItem(`theraskill-patient-job-${language}`)
      const restored = rows.find(row => row.job_id === saved) || rows.find(row => ['queued', 'running', 'failed', 'partial'].includes(row.status))
      if (restored) { notified.current = restored.status === 'completed' ? restored.job_id : ''; setJob(restored) }
    }).catch(err => active && setError(String(err)))
    return () => { active = false }
  }, [role, language])
  useEffect(() => {
    if (!job || job.request.language !== language) return
    window.localStorage.setItem(`theraskill-patient-job-${language}`, job.job_id)
    setJobs(rows => [job, ...rows.filter(row => row.job_id !== job.job_id)].slice(0, 20))
    if (job.status === 'completed' && job.result?.scenario && notified.current !== job.job_id) {
      notified.current = job.job_id
      onSavedRef.current(job.result.scenario)
    }
  }, [job, language])
  useEffect(() => {
    if (!job || !running) return
    let active = true
    const timer = window.setInterval(() => { void getPatientJob(role, job.job_id).then(value => active && setJob(value)).catch(err => active && setError(String(err))) }, 1500)
    return () => { active = false; window.clearInterval(timer) }
  }, [job?.job_id, running, role])
  async function retry() {
    if (!job) return
    setBusy(true); setError('')
    try { setJob(await retryPatientJob(role, job.job_id)) }
    catch (err) { setError(String(err)) } finally { setBusy(false) }
  }
  async function save(generate: boolean) {
    setBusy(true); setError('')
    try {
      const payload = { brief, patient: cleanPatient(patient), scenario_family: family, difficulty_band: difficulty, risk_band: risk, language, use_knowledge: useKnowledge }
      if (generate) setJob(await startPatientJob(role, payload))
      else onSaved((await buildPatient(role, payload, false)).scenario)
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) } finally { setBusy(false) }
  }
  return <section className="detail-band online-generation-band">
    <h2>{tr('构建模拟患者')}</h2><p>{tr('输入主题生成患者，或自行填写档案。生成会分阶段完成规划、档案、场景和独立检查；保存后可反复练习、编辑及复制，结果仍需教师审核。')}</p>
    <label>{tr('训练主题（公开）')}<textarea aria-label={tr('训练主题（公开）')} rows={3} maxLength={1200} value={brief} onChange={e => setBrief(e.target.value)} placeholder={tr('例如：来访者因为工作绩效下降而持续自责，但暂时不愿讨论家庭关系')} /></label>
    <div className="patient-grid">
      <label>{tr('训练类型')}<select value={family} onChange={e => setFamily(e.target.value)}>{Object.entries({ supportive_exploration: '支持性探索', cognitive_reframing_probe: '认知重评', rupture_and_repair: '关系修复', risk_escalation_and_handoff: '风险与转介', rebt_belief_work: 'REBT 信念工作' }).map(([key, title]) => <option key={key} value={key}>{tr(title)}</option>)}</select></label>
      <label>{tr('难度')}<select value={difficulty} onChange={e => setDifficulty(e.target.value)}><option value="low">{tr('低')}</option><option value="medium">{tr('中')}</option><option value="high">{tr('高')}</option></select></label>
      <label>{tr('风险')}<select value={risk} onChange={e => setRisk(e.target.value)}><option value="low">{tr('低')}</option><option value="elevated">{tr('需关注')}</option><option value="high">{tr('高')}</option></select></label>
    </div>
    <details><summary>{tr('补充人设、背景和近期经历（可选）')}</summary><PatientEditor value={patient} onChange={setPatient} prefix="build" /></details>
    <label className="patient-grounding"><input type="checkbox" checked={useKnowledge} onChange={e => setUseKnowledge(e.target.checked)} />{tr('生成时检索相关构建知识')}</label>
    {error && <p role="alert" className="error-banner">{error}</p>}
    <div className="governance-actions"><button className="primary-button" disabled={busy || running || brief.trim().length < 8} onClick={() => void save(true)}>{busy || running ? tr('正在构建…') : tr('生成并保存患者草稿')}</button><button className="secondary-button" disabled={busy || running || brief.trim().length < 8 || !patient.presenting_concern.trim()} onClick={() => void save(false)}>{tr('直接保存填写的患者')}</button></div>
    {!!jobs.length && <label>{tr('患者生成任务')}<select aria-label={tr('患者生成任务')} value={job?.job_id || ''} onChange={e => setJob(jobs.find(row => row.job_id === e.target.value) || null)}>{jobs.map(row => <option key={row.job_id} value={row.job_id}>{String(row.request.brief || '').slice(0, 60)} · {row.status}</option>)}</select></label>}
    {job && <div className="patient-job" aria-live="polite">
      <p>{tr('任务状态')}：{tr(({ queued: '等待执行', running: '进行中', completed: '已完成', failed: '失败', partial: '部分完成' } as Record<string, string>)[job.status] || job.status)} · {tr('刷新页面后可继续查看进度。')}</p>
      <ol>{job.progress.stages?.map(stage => <li key={stage.stage}>{tr(stageLabels[stage.stage] || stage.stage)} · {tr(({ completed: '已完成', running: '进行中', failed: '失败' } as Record<string, string>)[stage.status] || stage.status)}{stage.error && <p role="alert">{stage.error}</p>}</li>)}</ol>
      {job.error && <p role="alert" className="error-banner">{job.error}</p>}
      {['failed', 'partial'].includes(job.status) && <button className="secondary-button" disabled={busy} onClick={() => void retry()}>{tr('从未完成步骤继续')}</button>}
      {job.result?.scenario && <button className="secondary-button" onClick={() => onSaved(job.result!.scenario)}>{tr('打开患者草稿')}</button>}
    </div>}
  </section>
}
