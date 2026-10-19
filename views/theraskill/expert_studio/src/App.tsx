import { tr, useTranslationSubscription } from '../../shared/i18n'
import { useEffect, useState } from 'react'
import { BookPlus, ClipboardCheck, Download, FilePenLine, FlaskConical, FolderInput, ListChecks, LoaderCircle, RefreshCw, Save, ShieldCheck } from 'lucide-react'
import { createJob, downloadExport, getJob, governanceIdentity, health, importCase, listCases, listJobs, retryJob, regenerate, saveVersion, getExpertRole, setExpertRole, getReviewProfile, createExpertProfile, verifyExpertProfile } from './api'
import ReviewQueue from './ReviewQueue'
import KnowledgeProposal from './KnowledgeProposal'
import CandidateGovernance from './CandidateGovernance'
import { useLocale } from './i18n'

type Tab = 'queue' | 'knowledge' | 'governance' | 'review' | 'content' | 'cases'

export default function App() {
  useTranslationSubscription()

  const { locale, toggleLocale } = useLocale()
  const [tab, setTab] = useState<Tab>(() => {
    const view = new URLSearchParams(window.location.search).get('view')
    return ['queue','knowledge','governance','review','content','cases'].includes(view || '') ? view as Tab : view === 'case' ? 'cases' : 'review'
  })
  const [expertRole, changeExpertRole] = useState(getExpertRole)
  const [jobs, setJobs] = useState<any[]>([])
  const [profile, setProfile] = useState<any>(null)
  const [profileName, setProfileName] = useState('')
  const [profileDomains, setProfileDomains] = useState('')
  const [profileMessage, setProfileMessage] = useState('')
  const t = (zh: string, en: string) => locale === 'zh' ? zh : en
  const [online, setOnline] = useState(false)
  const [input, setInput] = useState(() => sessionStorage.getItem('theraskill_expert_draft') || '')
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
    health().then((value) => setOnline(value.status === 'ok' && value.support === 'ok')).catch(() => setOnline(false))
    listJobs().then(value => setJobs(value.jobs)).catch(err => setError(String(err)))
    getReviewProfile().then(setProfile).catch(() => setProfile(null))
    listCases().then((value) => setCases(value.cases)).catch(() => {})
    governanceIdentity().then(setGovernance).catch(() => {})
    const jobId = new URLSearchParams(window.location.search).get('job_id')
    if (jobId) getJob(jobId).then(value => { setJob(value); restoreInput(value); const view = new URLSearchParams(location.search).get('view'); if (!view || ['review','content'].includes(view)) setTab(value.workflow === 'content_lab' ? 'content' : 'review') }).catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }, [expertRole])
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

  useEffect(() => { sessionStorage.setItem('theraskill_expert_draft', input) }, [input])
  function restoreInput(value: any) { setInput(value.input?.transcript_or_response || value.input?.goal || ''); if (value.input?.framework) setFramework(value.input.framework); if (value.input?.turn_count) setTurns(value.input.turn_count) }
  function changeTab(value: Tab) { setTab(value); setError(''); const url = new URL(location.href); url.searchParams.set('view', value); if (!['review','content'].includes(value)) url.searchParams.delete('job_id'); window.history.replaceState({}, '', url) }
  useEffect(() => {
    if (!job?.job_id) return
    const url = new URL(location.href); url.searchParams.set('job_id', job.job_id); window.history.replaceState({}, '', url)
  }, [job?.job_id])
  async function openJob(id: string) {
    try { const value = await getJob(id); setJob(value); restoreInput(value); setTab(value.workflow === 'content_lab' ? 'content' : 'review'); const url = new URL(location.href); url.searchParams.set('job_id', id); url.searchParams.set('view', value.workflow === 'content_lab' ? 'content' : 'review'); window.history.replaceState({}, '', url); setError('') }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
  }
  async function retry() {
    if (!job) return
    try { await retryJob(job.job_id); setJob(await getJob(job.job_id)) } catch (err) { setError(String(err)) }
  }
  async function setupProfile() {
    try { const value = await createExpertProfile({subject_id:'expert-local',display_name:profileName,expertise_domains:profileDomains.split(/[,，]/).map(x=>x.trim()).filter(Boolean)}); setProfile(value); setProfileMessage(t('档案已创建，需管理员确认资质后验证。','Profile created. An administrator must verify credentials.')) } catch (err) { setError(String(err)) }
  }
  async function run() {
    if (!input.trim()) return
    setBusy(true); setError('')
    try {
      const payload = tab === 'review' ? { transcript_or_response: input, output_language: locale } : { framework, turn_count: turns, goal: input, source_type: 'synthetic', output_language: locale }
      setJob(await createJob(tab === 'review' ? 'response_review' : 'content_lab', payload)); setJobs((await listJobs()).jobs)
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) } finally { setBusy(false) }
  }
  async function save() {
    if (!job?.result) return
    try { await saveVersion(job.job_id, JSON.parse(edited), 'expert edit snapshot'); setJob(await getJob(job.job_id)) }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
  }
  async function runRegenerate() {
    if (!job || !instruction.trim()) return
    try { await regenerate(job.job_id, instruction); setJob({ ...job, status: 'pending' }); setInstruction('') } catch (err) { setError(String(err)) }
  }
  async function addCase() {
    try { const value = await importCase(sessionId, caseTitle); setCases([value, ...cases]); setSessionId(''); setCaseTitle('') } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
  }
  const displayedJob = job && ((tab === 'content' && job.workflow === 'content_lab') || (tab === 'review' && job.workflow === 'response_review')) ? job : null
  const result = displayedJob?.result
  return <div className="shell">
    <header><div><a href="/">{tr("← TheraSkillAgent")}</a><span>{tr("Expert & Content Studio")}</span></div><div className="header-tools"><label>{t('本地工作角色', 'Local workspace role')}<select value={expertRole} onChange={event => { setExpertRole(event.target.value); changeExpertRole(event.target.value) }}>{['expert','knowledge_curator','adjudicator','release_manager','admin'].map(value => <option key={value}>{value}</option>)}</select></label><small className={online ? 'online' : ''}>{online ? tr('服务已连接') : tr('服务未连接')}</small><button className="language-toggle" onClick={toggleLocale} title={locale === 'zh' ? tr('切换到英文') : 'Switch to Chinese'} aria-label={locale === 'zh' ? tr('切换到英文') : 'Switch to Chinese'}>{locale === 'zh' ? 'EN' : '中文'}</button></div></header>
    <nav>
      <button className={tab === 'queue' ? 'active' : ''} onClick={() => changeTab('queue')}><ListChecks />{t('反馈审阅', 'Feedback review')}</button>
      <button className={tab === 'knowledge' ? 'active' : ''} onClick={() => changeTab('knowledge')}><BookPlus />{t('知识贡献', 'Knowledge contribution')}</button>
      {['knowledge_curator','adjudicator','release_manager','admin'].includes(governance?.contribution_role) && <button className={tab === 'governance' ? 'active' : ''} onClick={() => changeTab('governance')}><ShieldCheck />{t('候选治理', 'Candidate governance')}</button>}
      <button className={tab === 'review' ? 'active' : ''} onClick={() => changeTab('review')}><ClipboardCheck />{t('回应审核', 'Response review')}</button>
      <button className={tab === 'content' ? 'active' : ''} onClick={() => changeTab('content')}><FlaskConical />{t('内容实验室', 'Content lab')}</button>
      <button className={tab === 'cases' ? 'active' : ''} onClick={() => changeTab('cases')}><FolderInput />{t('案例工作区', 'Case workspace')}</button>
    </nav>
    <main><details className="profile-setup"><summary>{t('专家档案与工作角色', 'Expert profile and workspace role')} · {profile?.status || t('未配置', 'Not configured')}</summary><p>{t('当前身份 expert-local。待验证档案不能提交正式专家贡献；角色操作仍由服务端校验。', 'Current subject: expert-local. Unverified profiles cannot submit formal expert contributions. Role operations are checked by the server.')}</p>{profile ? <><strong>{profile.display_name} · {profile.status}</strong>{expertRole === 'admin' && profile.status !== 'verified' && <button onClick={() => { if (window.confirm(t('已人工核验此专家资质，确认验证档案？', 'Have you verified this expert’s credentials and wish to approve the profile?'))) verifyExpertProfile(profile.expert_profile_id).then(setProfile).catch(err=>setError(String(err))) }}>{t('核验后批准档案', 'Approve verified credentials')}</button>}</> : expertRole === 'admin' ? <div><input placeholder={t('专家显示名', 'Expert display name')} value={profileName} onChange={event=>setProfileName(event.target.value)} /><input placeholder={t('专业领域，逗号分隔', 'Expertise domains, comma separated')} value={profileDomains} onChange={event=>setProfileDomains(event.target.value)} /><button disabled={!profileName.trim() || !profileDomains.trim()} onClick={() => void setupProfile()}>{t('创建待验证档案', 'Create pending profile')}</button></div> : <p>{t('请让本地管理员创建并验证专家档案，再进入贡献或反馈审阅。', 'Ask the local administrator to create and verify your expert profile before contributing or reviewing feedback.')}</p>}{profileMessage && <p>{profileMessage}</p>}</details>
    <details className="job-history" open><summary>{t('已保存任务', 'Saved jobs')} ({jobs.length})</summary>{jobs.map(value=><button key={value.job_id} onClick={()=>void openJob(value.job_id)}>{value.workflow} · {new Date(value.created_at).toLocaleString()} · {value.job_id.slice(-8)}</button>)}</details>{error && <div className="error">{error}</div>}{tab === 'queue' ? <ReviewQueue /> : tab === 'knowledge' ? <KnowledgeProposal /> : tab === 'governance' ? <CandidateGovernance identity={governance} /> : tab !== 'cases' ? <div className="workspace">
      <section className="editor"><h1>{tab === 'review' ? tr('治疗回应审核') : tr('合成对话构建')}</h1><p>{tab === 'review' ? tr('导入回应或 transcript，生成可追溯的专业审阅报告。') : tr('生成明确标记为 synthetic 的教学对话，并分离 dialogue、rationale 和 construction metadata。')}</p>
        {tab === 'content' && <div className="row"><label>{tr("框架")}<select value={framework} onChange={(event) => setFramework(event.target.value)}><option>CBT</option><option>REBT</option><option value="\u652f\u6301\u6027\u54a8\u8be2">{tr("\u652f\u6301\u6027\u54a8\u8be2")}</option><option value="\u6574\u5408\u53d6\u5411">{tr("\u6574\u5408\u53d6\u5411")}</option></select></label><label>{tr("发言条数")}<input type="number" min="2" max="20" value={turns} onChange={(event) => setTurns(Number(event.target.value))} /></label></div>}
        <textarea value={input} onChange={(event) => setInput(event.target.value)} placeholder={tab === 'review' ? tr('粘贴治疗师回应或完整 transcript') : tr('填写角色、阶段、教学目标与约束')} />
        <button className="primary" onClick={run} disabled={busy || displayedJob?.status === 'pending' || !input.trim()}>{busy ? <LoaderCircle className="spin" /> : <FilePenLine />}{tr("开始")}</button>
      </section>
      <section className="result"><div className="result-head"><h2>{tr("结果")}</h2>{result && <div><button onClick={() => downloadExport(job.job_id, 'json')}><Download />{tr("JSON")}</button><button onClick={() => downloadExport(job.job_id, 'markdown')}><Download />{tr("Markdown")}</button></div>}</div>
        {displayedJob?.status === 'failed' ? <div className="error" role="alert"><strong>{t('任务生成失败，输入和任务记录已保存。', 'Generation failed. Your input and job are saved.')}</strong><p>{displayedJob.provenance?.error || t('请检查模型配置后重试。','Check the model configuration and retry.')}</p><button onClick={() => void retry()}>{t('重试任务','Retry job')}</button></div> : displayedJob?.status === 'pending' ? <div className="pending"><LoaderCircle className="spin" />{tr("正在检索并生成")}</div> : result ? <>
          <div className="source">{result.presentation_mode}</div><p className="response">{result.text}</p>
          {result.structured_content?.generation_workflow && <details><summary>{tr('生成步骤与检查')}</summary><ol>{result.structured_content.generation_workflow.stages?.map((stage: any, index: number) => <li key={index}>{stage.stage} · {stage.status}{stage.checkpoint_reused ? ' · ' + tr('复用已完成结果') : ''}</li>)}</ol></details>}
          {result.structured_content?.review_segments?.map((segment: any, index: number) => <section key={index}><h3>{tr('原文依据与评审')}</h3>{segment.observations?.map((item: any, itemIndex: number) => <blockquote key={itemIndex}>{item.quote}<p>{item.observation}</p></blockquote>)}{segment.dimensions?.map((item: any, itemIndex: number) => <p key={itemIndex}><strong>{item.dimension}</strong> · {item.judgement} · {item.reason}</p>)}</section>)}
          <textarea className="output-editor" value={edited} onChange={(event) => setEdited(event.target.value)} />
          <div className="actions"><button onClick={save}><Save />{tr("保存专家版本")}</button><input value={instruction} onChange={(event) => setInstruction(event.target.value)} placeholder={tr("局部重生成要求")} /><button onClick={runRegenerate} disabled={!instruction.trim()}><RefreshCw />{tr("重生成")}</button></div>
          {job.versions?.length > 0 && <details><summary>{tr("专家版本（")}{job.versions.length}）</summary><pre>{JSON.stringify(job.versions, null, 2)}</pre></details>}
          <details><summary>{tr("Provenance")}</summary><pre>{JSON.stringify(job.provenance, null, 2)}</pre></details>
        </> : <div className="empty">{tr("尚未生成结果")}</div>}
      </section>
    </div> : <section className="cases"><h1>{tr("案例与策略工作区")}</h1><div className="case-import"><input value={sessionId} onChange={(event) => setSessionId(event.target.value)} placeholder={tr("终态 simulation session ID")} /><input value={caseTitle} onChange={(event) => setCaseTitle(event.target.value)} placeholder={tr("案例标题")} /><button className="primary" onClick={addCase} disabled={!sessionId || !caseTitle}><FolderInput />{tr("导入案例草稿")}</button></div>{cases.map((value) => <article key={value.case_draft_id}><strong>{value.title}</strong><span>{value.source_type}</span><p>{value.public_view?.scenario?.scenario_brief}</p></article>)}</section>}</main>
  </div>
}
