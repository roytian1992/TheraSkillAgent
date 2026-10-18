import { useEffect, useMemo, useState } from 'react'
import { CheckCircle2, ClipboardCheck, LoaderCircle, Play, Search, Square, Upload } from 'lucide-react'

type Locale = 'zh' | 'en'
const DEFAULT_SCHEMA = { type: 'object', properties: { claim: { type: 'string', description: 'Reusable claim' }, evidence: { type: 'string', description: 'Verbatim source evidence' } }, required: ['claim', 'evidence'] }
function savedDraft() { try { return JSON.parse(sessionStorage.getItem('theraskill_extraction_draft') || '{}') } catch { return {} } }

export default function ExtractionStudio({ locale, onBack }: { locale: Locale; onBack: () => void }) {
  const [initial] = useState(savedDraft)
  const [sourcePath, setSourcePath] = useState(initial.sourcePath || '')
  const [goal, setGoal] = useState(initial.goal || '')
  const [unit, setUnit] = useState(initial.unit || 'one source record')
  const [schema, setSchema] = useState(initial.schema || JSON.stringify(DEFAULT_SCHEMA, null, 2))
  const [job, setJob] = useState<Record<string, any> | null>(null)
  const [history, setHistory] = useState<Record<string, any>[]>([])
  const [preview, setPreview] = useState<Record<string, any> | null>(null)
  const [sourceInfo, setSourceInfo] = useState<Record<string, any> | null>(null)
  const [receipt, setReceipt] = useState<Record<string, any> | null>(null)
  const [planConfirmed, setPlanConfirmed] = useState(false)
  const [planExtras, setPlanExtras] = useState<Record<string, any>>({})
  const [sampleLimit, setSampleLimit] = useState(3)
  const [fieldName, setFieldName] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const t = (zh: string, en: string) => locale === 'zh' ? zh : en
  const parsed = useMemo(() => { try { return JSON.parse(schema) } catch { return null } }, [schema])
  const request = async (path: string, options: RequestInit = {}) => {
    const response = await fetch('/api/extraction' + path, { ...options, headers: { 'Content-Type': 'application/json', 'X-Extraction-Role': 'researcher', 'X-Extraction-User': 'workbench', ...(options.headers || {}) } })
    const text = await response.text()
    let payload: any
    try { payload = JSON.parse(text) } catch { throw new Error(text || response.statusText) }
    if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : JSON.stringify(payload.detail || payload))
    return payload
  }
  const action = async (fn: () => Promise<void>) => { setBusy(true); setMessage(''); try { await fn() } catch (reason) { setMessage(reason instanceof Error ? reason.message : String(reason)) } finally { setBusy(false) } }
  const refreshHistory = async () => { const data = await request('/v1/extraction/jobs'); setHistory(data.jobs) }
  const selectJob = (value: Record<string, any>) => {
    setJob(value); setPreview(null); setReceipt(null)
    if (value.plan) {
      setSourcePath(value.plan.source?.path || '')
      setGoal(value.plan.goal || '')
      setUnit(value.plan.unit_definition || '')
      setSchema(JSON.stringify(value.plan.target_schema || DEFAULT_SCHEMA, null, 2))
      setPlanExtras(value.plan)
      setPlanConfirmed(false)
    }
    const url = new URL(location.href); url.searchParams.set('workspace', 'extraction'); url.searchParams.set('job_id', value.job_id); historyReplace(url)
  }
  function historyReplace(url: URL) { window.history.replaceState({}, '', url) }
  useEffect(() => {
    void action(async () => {
      await refreshHistory()
      const id = new URLSearchParams(location.search).get('job_id')
      if (id) selectJob(await request('/v1/extraction/jobs/' + encodeURIComponent(id)))
    })
  }, [])
  useEffect(() => { sessionStorage.setItem('theraskill_extraction_draft', JSON.stringify({ sourcePath, goal, unit, schema })) }, [sourcePath, goal, unit, schema])
  useEffect(() => {
    if (!job?.job_id || !['queued', 'running', 'cancel_requested'].includes(job.status)) return
    const timer = window.setInterval(() => request('/v1/extraction/jobs/' + job.job_id).then(setJob).catch(error => setMessage(String(error))), 1500)
    return () => window.clearInterval(timer)
  }, [job?.job_id, job?.status])
  const updateSchema = (next: any) => { setSchema(JSON.stringify(next, null, 2)); setPlanConfirmed(false) }
  const upload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]; if (!file) return
    await action(async () => {
      const data = await request('/v1/extraction/sources', { method: 'POST', body: JSON.stringify({ filename: file.name, text: await file.text() }) })
      setSourcePath(data.path); setSourceInfo(data); setPlanConfirmed(false)
    })
  }
  const inspect = () => action(async () => { setSourceInfo(await request('/v1/extraction/inspect', { method: 'POST', body: JSON.stringify({ path: sourcePath }) })) })
  const suggest = () => action(async () => {
    const data = await request('/v1/extraction/plans/suggest', { method: 'POST', body: JSON.stringify({ path: sourcePath, goal, language: locale }) })
    setPlanExtras(data.plan); setUnit(data.plan.unit_definition); updateSchema(data.plan.target_schema)
    setMessage(t(`模型参考 ${data.sample_count} 条样本提出了草稿。请检查字段、抽取单元和证据要求，然后确认。`, `The model proposed a draft from ${data.sample_count} samples. Review its fields, unit and evidence requirements, then confirm.`))
  })
  const start = (trial: boolean) => action(async () => {
    if (!parsed?.properties || !Object.keys(parsed.properties).length) throw new Error(t('请先修正 schema，至少保留一个字段。', 'Fix the schema and keep at least one field.'))
    const data = await request('/v1/extraction/jobs', { method: 'POST', body: JSON.stringify({ plan: { ...planExtras, goal, source: { path: sourcePath }, unit_definition: unit, target_schema: parsed, language: locale, status: 'draft' }, limit: trial ? sampleLimit : null }) })
    selectJob(data); await refreshHistory()
  })
  const runAction = (name: 'preview' | 'handoff' | 'cancel' | 'retry') => action(async () => {
    if (!job?.job_id) return
    const data = await request('/v1/extraction/jobs/' + job.job_id + '/' + name, name === 'preview' ? {} : { method: 'POST' })
    if (name === 'preview') setPreview(data)
    else if (name === 'handoff') { setReceipt(data); setPreview(null); setJob(await request('/v1/extraction/jobs/' + job.job_id)) }
    else setJob(data)
    await refreshHistory()
  })
  return <div className="workspace-page">
    <div className="workspace-toolbar"><button className="back-button" onClick={onBack}>← {t('返回工作台', 'Back to workbench')}</button></div>
    <div className="workspace-title"><div><p className="eyebrow">TheraSkillAgent</p><h1>{t('知识抽取工作区', 'Knowledge extraction workspace')}</h1><p>{t('检查输入 → 设计并确认字段 → 小样本试抽 → 完整运行 → 候选审核', 'Inspect → design and confirm fields → try a sample → run → review candidates')}</p></div></div>
    <div className="studio-grid extraction-grid"><section className="studio-editor panel">
      <label className="file-picker">{t('上传并使用文件', 'Upload and use file')}<input type="file" accept=".json,.jsonl,.csv,.tsv,.md,.txt,.srt,.vtt" onChange={upload} /></label>
      <label className="field-label">{t('输入文件', 'Input file')}<input value={sourcePath} onChange={event => { setSourcePath(event.target.value); setPlanConfirmed(false) }} /></label>
      <button className="secondary-action" onClick={() => void inspect()} disabled={busy || !sourcePath}><Search size={15} />{t('检查输入', 'Inspect input')}</button>
      {sourceInfo && <details open><summary>{t('输入与样本（最多 5 条）', 'Source and samples (up to 5)')}</summary><pre>{JSON.stringify(sourceInfo, null, 2)}</pre></details>}
      <label className="field-label">{t('抽取目标', 'Extraction goal')}<textarea className="short-textarea" placeholder={t('例如：提取有原文证据的共情回应策略', 'For example: extract empathic response strategies with source evidence')} value={goal} onChange={event => { setGoal(event.target.value); setPlanConfirmed(false) }} /></label>
      <button className="secondary-action" disabled={busy || !sourcePath || !goal.trim()} onClick={() => void suggest()}>{t('让模型建议字段与计划', 'Ask model to propose fields and plan')}</button>
      <p className="hint-note">{t('此操作向当前配置的 LLM 发送最多 5 条源记录；只生成计划，不启动抽取。', 'This sends up to 5 source records to your configured LLM to propose a plan. It does not start extraction.')}</p>
      <label className="field-label">{t('抽取单元', 'Unit definition')}<input value={unit} onChange={event => { setUnit(event.target.value); setPlanConfirmed(false) }} /></label>
      {parsed?.properties && <section><h3>{t('输出字段', 'Output fields')}</h3>{Object.entries(parsed.properties).map(([name, value]) => <div className="studio-row" key={name}><label>{name}<input aria-label={name + ' description'} value={(value as any).description || ''} placeholder={t('字段含义', 'Description')} onChange={event => updateSchema({ ...parsed, properties: { ...parsed.properties, [name]: { ...(value as any), description: event.target.value } } })} /></label><label>{t('类型', 'Type')}<select value={(value as any).type || 'string'} onChange={event => { const type = event.target.value; updateSchema({ ...parsed, properties: { ...parsed.properties, [name]: { ...(value as any), type, ...(type === 'array' ? { items: { type: 'string' } } : {}) } } }) }}>{['string','number','integer','boolean','array','object'].map(type => <option key={type}>{type}</option>)}</select></label><label><input type="checkbox" checked={(parsed.required || []).includes(name)} onChange={event => updateSchema({ ...parsed, required: event.target.checked ? [...(parsed.required || []), name] : (parsed.required || []).filter((item: string) => item !== name) })} />{t('必填', 'Required')}</label><button onClick={() => { const properties = { ...parsed.properties }; delete properties[name]; updateSchema({ ...parsed, properties, required: (parsed.required || []).filter((item: string) => item !== name) }) }}>{t('删除', 'Remove')}</button></div>)}<div className="studio-row"><input aria-label={t('新字段名称', 'New field name')} value={fieldName} onChange={event => setFieldName(event.target.value)} /><button disabled={!fieldName.trim() || Boolean(parsed.properties[fieldName.trim()])} onClick={() => { updateSchema({ ...parsed, properties: { ...parsed.properties, [fieldName.trim()]: { type: 'string' } } }); setFieldName('') }}>{t('添加字段', 'Add field')}</button></div></section>}
      <details open={!parsed}><summary>{t('高级：目标 schema（JSON）', 'Advanced: target schema (JSON)')}</summary><textarea aria-label={t('目标 schema（JSON）', 'Target schema (JSON)')} value={schema} onChange={event => { setSchema(event.target.value); setPlanConfirmed(false) }} />{!parsed && <p role="alert">{t('JSON 格式有误，请检查引号、逗号与括号。', 'Invalid JSON. Check quotes, commas and brackets.')}</p>}</details>
      <label className="checkbox-line"><input type="checkbox" checked={planConfirmed} onChange={event => setPlanConfirmed(event.target.checked)} />{t('我已确认字段、抽取单元与证据要求', 'I confirm the fields, unit and evidence requirements')}</label>
      <label>{t('试抽记录数', 'Trial record limit')}<input type="number" min={1} max={100} value={sampleLimit} onChange={event => setSampleLimit(Math.max(1, Math.min(100, Number(event.target.value) || 1)))} /></label>
      <div className="button-row"><button disabled={busy || !sourcePath || !goal.trim() || !parsed || !planConfirmed} onClick={() => void start(true)}>{t('小样本试抽', 'Run sample')}</button><button className="primary large-action" disabled={busy || !sourcePath || !goal.trim() || !parsed || !planConfirmed} onClick={() => void start(false)}>{busy ? <LoaderCircle className="spin" /> : <Play size={15} />}{t('验证并启动', 'Validate and start')}</button></div>
      {message && <p className="message-output" role="status">{message}</p>}
    </section><section className="studio-result panel"><h2>{t('抽取任务与结果', 'Extraction jobs and results')}</h2>
      <button onClick={() => void action(refreshHistory)}>{t('刷新任务列表', 'Refresh job list')}</button>
      <div className="job-history">{history.map(row => <button key={row.job_id} className="resource-item" onClick={() => selectJob(row)}><strong>{row.plan?.goal || row.job_id}</strong><span>{row.status} · {row.created_at}</span></button>)}</div>
      {job ? <><div className="result-heading"><strong>{job.job_id}</strong><span className={'job-status ' + job.status}>{job.status}</span></div><p>{t('进度', 'Progress')}: {JSON.stringify(job.progress || {})}</p>{job.error && <p role="alert">{job.error}</p>}
        <div className="button-row"><button onClick={() => void runAction('preview')} disabled={busy}><ClipboardCheck size={15} />{t('预览候选', 'Preview candidates')}</button><button onClick={() => void runAction('handoff')} disabled={busy || Boolean(receipt) || !['completed', 'partial'].includes(job.status)}><CheckCircle2 size={15} />{t('交给治理', 'Handoff to governance')}</button>{['failed','partial'].includes(job.status) && <button onClick={() => void runAction('retry')} disabled={busy}>{t('重试未完成记录', 'Retry unfinished records')}</button>}<button onClick={() => void runAction('cancel')} disabled={busy || !['queued', 'running'].includes(job.status)}><Square size={14} />{t('取消作业', 'Cancel job')}</button></div>
        {receipt && <div className="notice"><strong>{t('候选已交接，尚未发布为知识', 'Candidates handed off; knowledge is not published')}</strong><pre>{JSON.stringify(receipt, null, 2)}</pre><a href={'/theraskill/expert/?view=governance&candidate_id=' + encodeURIComponent(receipt.candidate_ids?.[0] || '')}>{t('前往候选治理工作区', 'Open candidate governance')}</a></div>}
        {preview && <pre className="json-output">{JSON.stringify(preview, null, 2)}</pre>}<details><summary>{t('完整任务记录', 'Full job record')}</summary><pre className="json-output">{JSON.stringify(job, null, 2)}</pre></details>
      </> : <div className="empty-result"><ClipboardCheck size={28} />{t('选择历史任务或创建一个新任务。', 'Select a saved job or create a new one.')}</div>}
    </section></div>
  </div>
}
