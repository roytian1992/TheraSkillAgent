import { useEffect, useMemo, useState } from 'react'
import { FileText, LoaderCircle, Package, Play, Search, ShieldCheck } from 'lucide-react'

type Locale = 'zh' | 'en'
type Operation = 'inspect' | 'analyze' | 'review' | 'package'
const endpoint = '/api/transcript/v1/transcript'
async function requestApi(path: string, body?: unknown) {
  const response = await fetch(endpoint + path, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
  const text = await response.text()
  let payload: any
  try { payload = JSON.parse(text) } catch { throw new Error(text || response.statusText) }
  if (!response.ok) throw new Error(Array.isArray(payload.detail) ? payload.detail.map((item: any) => item.loc.join('.') + ': ' + item.msg).join('; ') : typeof payload.detail === 'string' ? payload.detail : response.statusText)
  return payload
}
const runningStatuses = ['queued', 'running']
const retryStatuses = ['failed', 'partial', 'interrupted']
function scopeOf(value: any) { return JSON.stringify([value.text, value.filename, value.language, value.output_language, value.speaker_role_overrides || {}, value.skills || []]) }


function readDraft(): any { try { return JSON.parse(sessionStorage.getItem('theraskill_transcript_draft') || '{}') } catch { return {} } }

const skills = ['transcript-quality-reviewer', 'transcript-evaluator', 'risk-triage', 'therapy-response-reviewer']

export default function TranscriptStudio({ locale, onBack }: { locale: Locale; onBack: () => void }) {
  const [text, setText] = useState(() => readDraft().text ?? '患者：最近总是睡不好，嗯嗯。\n医师：听起来你很疲惫。\nPatient: I feel unsafe.')
  const [filename, setFilename] = useState(() => readDraft().filename || 'dialogue.txt')
  const [inputLanguage, setInputLanguage] = useState<'auto' | 'zh' | 'en' | 'mixed'>(() => readDraft().inputLanguage || 'auto')
  const [operation, setOperation] = useState<Operation>(() => readDraft().operation || 'inspect')
  const [mode, setMode] = useState<'plan' | 'model'>(() => readDraft().mode || 'plan')
  const [speakerRoles, setSpeakerRoles] = useState<Record<string, string>>(() => readDraft().speakerRoles || {})
  const [selectedSkills, setSelectedSkills] = useState<string[]>(() => readDraft().selectedSkills || ['transcript-quality-reviewer', 'therapy-response-reviewer'])
  const [includeRaw, setIncludeRaw] = useState(false)
  const [result, setResult] = useState<Record<string, unknown> | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [job, setJob] = useState<any>(null)
  const [jobs, setJobs] = useState<any[]>([])
  const running = !!job && runningStatuses.includes(job.status)
  const jobLabel = (value: string) => locale === 'zh' ? (({queued: '等待执行', running: '进行中', completed: '已完成', partial: '部分完成', failed: '失败', interrupted: '执行已中断', window_reviews: '逐窗评估与证据核对', synthesis: '综合发现', package: '打包结果', complete: '结束'} as Record<string, string>)[value] || value) : value.replaceAll('_', ' ')
  const adoptJob = (value: any, restore = false) => {
    setJob(value)
    if (value.result) setResult(value.result)
    if (restore && value.request) {
      const request = value.request
      setText(request.text); setFilename(request.filename); setInputLanguage(request.language)
      setSpeakerRoles(request.speaker_role_overrides || {}); setSelectedSkills(request.skills || [])
      setMode('model'); setOperation(value.operation); setIncludeRaw(!!request.include_raw)
      if (!value.result) setResult(null)
    }
    try { localStorage.setItem('theraskill-transcript-job', value.job_id) } catch {}
    const url = new URL(location.href); url.searchParams.set('transcript_job', value.job_id); history.replaceState({}, '', url)
  }
  const selectJob = async (id: string) => { setError(''); try { adoptJob(await requestApi('/jobs/' + id), true) } catch (reason) { setError(String(reason)) } }
  useEffect(() => {
    let active = true
    void requestApi('/jobs').then(async response => {
      if (!active) return
      setJobs(response.jobs)
      let stored = new URLSearchParams(location.search).get('transcript_job')
      if (!stored) { try { stored = localStorage.getItem('theraskill-transcript-job') } catch {} }
      if (!stored) stored = response.jobs.find((value: any) => runningStatuses.includes(value.status))?.job_id
      if (stored) {
        const value = await requestApi('/jobs/' + stored)
        if (active) adoptJob(value, true)
      }
    }).catch(reason => { if (active) setError(String(reason)) })
    return () => { active = false }
  }, [])
  useEffect(() => {
    if (!job) return
    setJobs(current => [job, ...current.filter(value => value.job_id !== job.job_id)].slice(0, 100))
  }, [job])
  useEffect(() => {
    if (!running) return
    let active = true, pending = false
    const timer = window.setInterval(() => {
      if (pending) return
      pending = true
      void requestApi('/jobs/' + job.job_id).then(value => { if (active) adoptJob(value) }).catch(reason => { if (active) setError(String(reason)) }).finally(() => { pending = false })
    }, 1200)
    return () => { active = false; window.clearInterval(timer) }
  }, [job?.job_id, running])
  const retry = async () => {
    if (!job) return
    setBusy(true); setError('')
    try { adoptJob(await requestApi('/jobs/' + job.job_id + '/retry', {revision: job.revision})) }
    catch (reason) { setError(String(reason)) }
    finally { setBusy(false) }
  }

  const copy = useMemo(() => locale === 'zh' ? {
    back: '返回工作台', title: '对话文件工作区', subtitle: '整理患者与医师对话，确认说话人，按维度评估并查看原文证据与综合报告。', file: '文件名', content: '对话内容', choose: '读取本地文件', action: '操作', inspect: '检查格式', analyze: '确定性分析', review: '多 skill 评估', package: '生成 bundle', mode: '评估模式', plan: '生成 review plan', model: '调用模型', skills: '选择 skills', raw: 'bundle 中包含原文', run: '执行', running: '处理中', output: '结果', noResult: '执行操作后，这里会显示可追溯结果。', evidence: '结果保留 turn_id 证据；风险信号仅为筛查。', modelWarning: '使用当前模型完成检索、逐窗评估、原文证据核对和综合。已完成的阶段自动保存；重试和打包会复用相同配置的结果。', packageNote: '默认不附带原始文件全文；规范化轮次仍包含对话内容。', error: '执行失败', language: '输入语言',
  } : {
    back: 'Back to workbench', title: 'Transcript workspace', subtitle: 'Normalize, analyze, review and package patient/clinician dialogue without overwriting source evidence.', file: 'Filename', content: 'Transcript', choose: 'Read local file', action: 'Operation', inspect: 'Inspect', analyze: 'Analyze', review: 'Multi-skill review', package: 'Package bundle', mode: 'Review mode', plan: 'Review plan', model: 'Call model', skills: 'Skills', raw: 'Include raw text in bundle', run: 'Run', running: 'Running', output: 'Result', noResult: 'The traceable result will appear here.', evidence: 'Results retain turn_id evidence; risk signals are screening only.', modelWarning: 'The configured model retrieves guidance, reviews windows, checks source quotes and synthesizes findings. Completed stages are saved and reused on retry and export when inputs and configuration match.', packageNote: 'The original file is excluded by default; normalized turns still contain dialogue text.', error: 'Failed', language: 'Input language'
  }, [locale])

  useEffect(() => { try { sessionStorage.setItem('theraskill_transcript_draft', JSON.stringify({ text, filename, speakerRoles, inputLanguage, selectedSkills, operation, mode })) } catch {} }, [text, filename, speakerRoles, inputLanguage, selectedSkills, operation, mode])
  const turns = ((result?.document as any)?.turns || []) as any[]
  const reviewRun = result?.reviews as any
  const reviews = (reviewRun?.reviews || []) as any[]
  const speakers = [...new Map(turns.map(turn => [turn.speaker_label, turn])).values()]

  const run = async () => {
    if (!text.trim()) return
    setBusy(true); setError('')
    try {
      const payload: any = { text, filename, language: inputLanguage, output_language: locale, mode, speaker_role_overrides: speakerRoles, skills: selectedSkills, include_raw: includeRaw }
      if (mode === 'model' && (operation === 'review' || operation === 'package')) {
        if (operation === 'package' && job?.run_id && scopeOf(payload) === scopeOf(job.request)) payload.review_run_id = job.run_id
        adoptJob(await requestApi('/jobs', {...payload, operation}))
      } else setResult(await requestApi('/' + operation, payload))
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  const readFile = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    setFilename(file.name)
    setText(await file.text())
    setSpeakerRoles({}); setResult(null)
  }

  return <div className="workspace-page">
    <div className="workspace-toolbar"><button className="back-button" onClick={onBack}>← {copy.back}</button><span className="workspace-status"><FileText size={16} /> TheraSkill transcript pipeline</span></div>
    <div className="workspace-title"><div><p className="eyebrow">TheraSkillAgent</p><h1>{copy.title}</h1><p>{copy.subtitle}</p></div></div>
    <div className="studio-grid">
      <section className="studio-editor panel">
        <p className="hint-note">{locale === 'zh' ? '模型评估使用当前界面语言；原文和证据引文保留原始语言。' : 'Model reviews use the interface language; original dialogue and evidence quotes keep their source language.'}</p><div className="studio-row"><label>{copy.file}<input value={filename} onChange={event => setFilename(event.target.value)} /></label><label>{copy.language}<select value={inputLanguage} onChange={event => setInputLanguage(event.target.value as 'auto' | 'zh' | 'en' | 'mixed')}><option value="auto">auto</option><option value="zh">中文</option><option value="en">English</option><option value="mixed">mixed</option></select></label></div>
        <label className="field-label">{copy.content}<textarea aria-label={copy.content} value={text} onChange={event => setText(event.target.value)} /></label>
        <label className="file-picker">{copy.choose}<input type="file" accept=".txt,.json,.jsonl,.srt,.vtt" onChange={readFile} /></label>
        <div className="operation-tabs">{(['inspect', 'analyze', 'review', 'package'] as Operation[]).map(item => <button key={item} className={operation === item ? 'active' : ''} onClick={() => setOperation(item)}>{item === 'inspect' ? <Search size={15} /> : item === 'analyze' ? <ShieldCheck size={15} /> : item === 'review' ? <Play size={15} /> : <Package size={15} />}{copy[item]}</button>)}</div>
        {(operation === 'review' || operation === 'package') && <div className="review-options"><label>{copy.mode}<select aria-label={copy.mode} value={mode} onChange={event => setMode(event.target.value as 'plan' | 'model')}><option value="plan">{copy.plan}</option><option value="model">{copy.model}</option></select></label><div><span className="field-label">{copy.skills}</span><div className="skill-checks">{skills.map(skill => <label key={skill}><input type="checkbox" checked={selectedSkills.includes(skill)} onChange={event => setSelectedSkills(current => event.target.checked ? [...current, skill] : current.filter(item => item !== skill))} />{skill}</label>)}</div></div></div>}
        {operation === 'package' && <label className="checkbox-line"><input type="checkbox" checked={includeRaw} onChange={event => setIncludeRaw(event.target.checked)} />{copy.raw}</label>}
        {mode === 'model' && (operation === 'review' || operation === 'package') && <p className="warning-note">{copy.modelWarning}</p>}
        {operation === 'package' && <p className="hint-note">{copy.packageNote}</p>}
        <button className="primary large-action" onClick={() => void run()} disabled={busy || running || !text.trim()}>{busy ? <LoaderCircle className="spin" /> : <Play size={16} />}{busy ? copy.running : copy.run}</button>
        {job && <section className="interaction-card" aria-live="polite"><h3>{locale === 'zh' ? '评估任务' : 'Review job'} · {jobLabel(job.status)}</h3><p>{jobLabel(job.stage)} · {locale === 'zh' ? '完成窗口' : 'Completed windows'} {job.progress?.completed_windows || 0}/{job.progress?.planned_windows || '—'}</p><p>{locale === 'zh' ? '关闭页面不会取消任务；刷新后会恢复进度。' : 'Closing this page does not cancel the job. Progress is restored on refresh.'}</p>{job.error && <p role="alert">{job.error}</p>}{retryStatuses.includes(job.status) && <button className="secondary" disabled={busy || running} onClick={() => void retry()}>{locale === 'zh' ? '从已保存进度继续' : 'Resume saved stages'}</button>}<small>{job.job_id}</small></section>}
        {jobs.length > 0 && <label>{locale === 'zh' ? '最近的对话评估任务' : 'Recent transcript review jobs'}<select aria-label={locale === 'zh' ? '最近的对话评估任务' : 'Recent transcript review jobs'} value={job?.job_id || ''} onChange={event => void selectJob(event.target.value)}><option value="" disabled>{locale === 'zh' ? '选择任务' : 'Select job'}</option>{jobs.map(value => <option key={value.job_id} value={value.job_id}>{value.filename} · {jobLabel(value.status)} · {value.created_at}</option>)}</select></label>}
        {error && <div className="error-box">{copy.error}: {error}</div>}
      </section>
      <section className="studio-result panel"><div className="result-heading"><h2>{copy.output}</h2>{result && <span>{String(result.operation || '')}</span>}</div>{result ? <><div className="result-summary">{copy.evidence}</div>{typeof result.download_url === 'string' && <a className="primary" href={'/api/transcript' + result.download_url}>{locale === 'zh' ? '下载完整 bundle（ZIP）' : 'Download bundle (ZIP)'}</a>}
        {speakers.length > 0 && <section><h3>{locale === 'zh' ? '确认说话人角色' : 'Confirm speaker roles'}</h3><p>{locale === 'zh' ? '修改后重新执行，原文保持不变。' : 'Run again after editing. The source text is preserved.'}</p>{speakers.map(turn => <label className="field-label" key={turn.speaker_label}>{turn.speaker_label}<select value={speakerRoles[turn.speaker_label] || turn.speaker_role} onChange={event => setSpeakerRoles(current => ({ ...current, [turn.speaker_label]: event.target.value }))}>{['unknown','patient','therapist','system'].map(role => <option key={role} value={role}>{locale === 'zh' ? (({unknown:'待确认',patient:'患者 / 来访者',therapist:'医师 / 咨询师',system:'系统'} as Record<string,string>)[role]) : role}</option>)}</select></label>)}</section>}
        {reviewRun?.mode === 'model' && <section className="result-summary"><strong>{locale === 'zh' ? '评估进度与覆盖' : 'Review coverage'} · {reviewRun.status}</strong><p>{locale === 'zh' ? '完成窗口' : 'Completed windows'}: {reviewRun.coverage?.completed_windows}/{reviewRun.coverage?.windows} · {locale === 'zh' ? '复用' : 'Reused'}: {reviewRun.reused_windows || 0} · {locale === 'zh' ? '已验证发现' : 'Verified findings'}: {reviewRun.coverage?.verified_findings || 0}</p>{reviewRun.coverage?.sample_only && <p>{locale === 'zh' ? '本次仅评估选定样本，不能代表整段会谈。' : 'Only the selected sample was reviewed; this does not cover the full session.'}</p>}{reviewRun.coverage?.failed_windows > 0 && <p>{locale === 'zh' ? '部分窗口未完成。再次执行可重试失败窗口，已完成结果会保留。' : 'Some windows failed. Run again to retry them while retaining completed results.'}</p>}</section>}
        {reviewRun?.synthesis?.themes?.length > 0 && <section><h3>{locale === 'zh' ? '跨窗口综合' : 'Synthesis across windows'}</h3>{reviewRun.synthesis.themes.map((theme: any, index: number) => <article className="resource-item" key={index}><p>{theme.summary}</p>{theme.recommendation && <p>{theme.recommendation}</p>}{theme.uncertainty && <p>{theme.uncertainty}</p>}<small>{(theme.finding_ids || []).join(', ')}</small></article>)}{reviewRun.synthesis.status !== 'completed' && <p>{locale === 'zh' ? '综合结果仍有未覆盖或待复核项，请结合下方发现查看。' : 'Some findings remain uncovered or require review; consult the findings below.'}</p>}</section>}
        {reviews.length > 0 && <section><h3>{locale === 'zh' ? '评估结果与证据' : 'Review findings and evidence'}</h3>{reviews.map((review, index) => <article className="resource-item" key={index}><strong>{review.skill} · {review.status}</strong><p>{review.overall}</p>{review.error && <p role="alert">{review.error}</p>}{(review.findings || []).map((finding: any, i: number) => <div key={i}><small>{finding.finding_id} · {(finding.turn_ids || []).join(', ')}</small>{(finding.evidence || []).map((quote: any, q: number) => <blockquote key={q}>{quote.quote}</blockquote>)}{finding.knowledge_ids?.length > 0 && <small>{locale === 'zh' ? '知识依据' : 'Knowledge evidence'}: {finding.knowledge_ids.join(', ')}</small>}{[['observed','原文观察','Observation'],['inference','模型判断','Model inference'],['recommendation','建议','Recommendation'],['uncertainty','不确定性','Uncertainty']].map(([key,zh,en]) => finding[key] && <p key={key}><b>{locale === 'zh' ? zh : en}：</b>{String(finding[key])}</p>)}</div>)}{review.quarantined_findings?.length > 0 && <details><summary>{locale === 'zh' ? '未通过证据核对的内容（不计入结果）' : 'Quarantined findings (excluded from results)'} · {review.quarantined_findings.length}</summary>{review.quarantined_findings.map((item: any, i: number) => <div key={i}><p>{item.finding?.observed || ''}</p><small>{(item.reasons || []).join(', ')}</small></div>)}</details>}{review.limitations?.length > 0 && <p>{locale === 'zh' ? '局限：' : 'Limitations: '}{review.limitations.join('; ')}</p>}</article>)}</section>}
        {turns.length > 0 && <details open><summary>{locale === 'zh' ? '对话轮次与来源' : 'Turns and source evidence'}</summary>{turns.map(turn => <article className="resource-item" key={turn.turn_id}><strong>{turn.turn_id} · {turn.speaker_label} · {turn.timestamp || ''}</strong><blockquote style={{whiteSpace:'pre-wrap'}}>{turn.raw_text}</blockquote>{turn.raw_text !== turn.normalized_text && <p>{turn.normalized_text}</p>}</article>)}</details>}
        <details><summary>{locale === 'zh' ? '完整结构化结果' : 'Full structured result'}</summary><pre className="json-output">{JSON.stringify(result, null, 2)}</pre></details></> : <div className="empty-result"><FileText size={28} />{copy.noResult}</div>}</section>
    </div>
  </div>
}
