import { useEffect, useMemo, useState } from 'react'
import { CheckCircle2, ClipboardCheck, LoaderCircle, Play, Search, Square, Upload } from 'lucide-react'

type Locale = 'zh' | 'en'
const DEFAULT_SCHEMA = { type: 'object', properties: { claim: { type: 'string', description: 'Reusable claim' }, evidence: { type: 'string', description: 'Verbatim source evidence' } }, required: ['claim', 'evidence'] }
const CHINESE_SCHEMA = { ...DEFAULT_SCHEMA, properties: { claim: { type: 'string', description: '可复用的结论或策略' }, evidence: { type: 'string', description: '逐字原文依据' } } }
function stable(value: any): any { return Array.isArray(value) ? value.map(stable) : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(key => [key, stable(value[key])])) : value }
function planKey(plan: Record<string, any>) {
  const keys = ['goal', 'unit_definition', 'language', 'target_schema', 'evidence_required', 'evidence_fields', 'quality_rules', 'operations', 'semantic_review', 'model_profile', 'prompt_version']
  return JSON.stringify(stable({ source_path: plan.source?.path || plan.source?.input_uri || '', ...Object.fromEntries(keys.map(key => [key, plan[key]])) }))
}
function savedDraft() { try { return JSON.parse(sessionStorage.getItem('theraskill_extraction_draft') || '{}') } catch { return {} } }

export default function ExtractionStudio({ locale, onBack }: { locale: Locale; onBack: () => void }) {
  const [initial] = useState(savedDraft)
  const [sourcePath, setSourcePath] = useState(initial.sourcePath || '')
  const [goal, setGoal] = useState(initial.goal || '')
  const [unit, setUnit] = useState(initial.unit || (locale === 'zh' ? '每条源记录' : 'one source record'))
  const [schema, setSchema] = useState(initial.schema || JSON.stringify(locale === 'zh' ? CHINESE_SCHEMA : DEFAULT_SCHEMA, null, 2))
  const [job, setJob] = useState<Record<string, any> | null>(null)
  const [history, setHistory] = useState<Record<string, any>[]>([])
  const [preview, setPreview] = useState<Record<string, any> | null>(null)
  const [sourceInfo, setSourceInfo] = useState<Record<string, any> | null>(null)
  const [receipt, setReceipt] = useState<Record<string, any> | null>(null)
  const [planConfirmed, setPlanConfirmed] = useState(false)
  const [planExtras, setPlanExtras] = useState<Record<string, any>>(initial.planExtras || {})
  const [semanticReview, setSemanticReview] = useState<'auto' | 'always'>(initial.semanticReview === 'always' ? 'always' : 'auto')
  const [trialViewed, setTrialViewed] = useState<Record<string, any> | null>(null)
  const [trialAccepted, setTrialAccepted] = useState(false)
  const [skipTrial, setSkipTrial] = useState(false)
  const [skipReason, setSkipReason] = useState('')
  const [sampleLimit, setSampleLimit] = useState(3)
  const [fieldName, setFieldName] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const t = (zh: string, en: string) => locale === 'zh' ? zh : en
  const statusName = (value: string) => locale === 'zh' ? ({ queued: '等待中', running: '进行中', completed: '已完成', partial: '部分完成', failed: '失败', grounded: '已核对原文' } as Record<string, string>)[value] || value : value
  const fieldNameLabel = (value: string) => locale === 'zh' ? ({ claim: '结论或策略', evidence: '原文依据' } as Record<string, string>)[value] || value : value
  const parsed = useMemo(() => { try { return JSON.parse(schema) } catch { return null } }, [schema])
  const currentPlan = useMemo(() => ({ ...planExtras, goal, source: { path: sourcePath }, unit_definition: unit, target_schema: parsed, language: locale, semantic_review: semanticReview, status: 'draft' }), [planExtras, goal, sourcePath, unit, parsed, locale, semanticReview])
  const currentKey = planKey(currentPlan)
  const acceptedSample = Boolean(trialAccepted && trialViewed?.binding === currentKey)
  const fullRunReady = acceptedSample || (skipTrial && Boolean(skipReason.trim()))
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
    setJob(value); setPreview(null); setReceipt(null); setTrialViewed(null); setTrialAccepted(false); setSkipTrial(false); setSkipReason('')
    if (value.plan) {
      setSourcePath(value.plan.source?.path || '')
      setGoal(value.plan.goal || '')
      setUnit(value.plan.unit_definition || '')
      setSchema(JSON.stringify(value.plan.target_schema || DEFAULT_SCHEMA, null, 2))
      setPlanExtras(value.plan)
      setSemanticReview(value.plan.semantic_review === 'always' ? 'always' : 'auto')
      if (value.limit) setSampleLimit(Math.min(100, Number(value.limit)))
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
  useEffect(() => { sessionStorage.setItem('theraskill_extraction_draft', JSON.stringify({ sourcePath, goal, unit, schema, planExtras, semanticReview })) }, [sourcePath, goal, unit, schema, planExtras, semanticReview])
  useEffect(() => { setTrialViewed(null); setTrialAccepted(false); setSkipTrial(false); setSkipReason(''); setPlanConfirmed(false); setPreview(null) }, [currentKey])
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
    setPlanExtras(data.plan); setSemanticReview(data.plan.semantic_review === 'always' ? 'always' : 'auto'); setUnit(data.plan.unit_definition); updateSchema(data.plan.target_schema)
    setMessage(t(`模型参考 ${data.sample_count} 条样本提出了草稿。请检查字段、抽取单元和证据要求，然后确认。`, `The model proposed a draft from ${data.sample_count} samples. Review its fields, unit and evidence requirements, then confirm.`))
  })
  const start = (trial: boolean) => action(async () => {
    if (!parsed?.properties || !Object.keys(parsed.properties).length) throw new Error(t('请先修正 schema，至少保留一个字段。', 'Fix the schema and keep at least one field.'))
    if (!trial && !fullRunReady) throw new Error(t('请查看并接受当前设置的试抽结果，或明确说明跳过原因。', 'Review and accept a sample for these settings, or explain why you are skipping it.'))
    const trialReview = trial ? undefined : acceptedSample ? { decision: 'accepted', sample_job_id: trialViewed?.sample_job_id, review_token: trialViewed?.review_token, note: t('已查看试抽候选并确认继续全量抽取。', 'Viewed the sample candidates and accepted full extraction.') } : { decision: 'skipped', note: skipReason.trim() }
    const data = await request('/v1/extraction/jobs', { method: 'POST', body: JSON.stringify({ plan: currentPlan, limit: trial ? sampleLimit : null, trial_review: trialReview }) })
    selectJob(data); await refreshHistory()
  })
  const runAction = (name: 'preview' | 'handoff' | 'cancel' | 'retry') => action(async () => {
    if (!job?.job_id) return
    const data = await request('/v1/extraction/jobs/' + job.job_id + '/' + name + (name === 'preview' && job.limit ? '?limit=' + Math.min(100, Number(job.limit)) : ''), name === 'preview' ? {} : { method: 'POST' })
    if (name === 'preview') {
      setPreview(data); setTrialAccepted(false)
      const context = data.trial_review_context
      if (context && job.limit && planKey(job.plan) === currentKey) {
        setTrialViewed({ ...context, binding: currentKey, viewed_count: data.items.length })
        setMessage(t('试抽候选已展示。请检查证据和字段，再勾选接受试抽结果。', 'Sample candidates are displayed. Check their evidence and fields, then accept the sample.'))
      } else {
        setTrialViewed(null)
        if (job.limit) setMessage(t('当前试抽尚未完成质量检查，或表单设置已改变。请先处理问题或重新试抽。', 'This sample has not passed quality checks, or the settings have changed. Resolve its issues or run another sample.'))
      }
    }
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
      {sourceInfo && <section className="extraction-source-preview" aria-label={t('输入检查结果', 'Input inspection')}>
        <div className="extraction-source-summary"><strong>{t('已读取输入', 'Input inspected')}</strong><span>{sourceInfo.source?.records ?? sourceInfo.samples?.length ?? 0} {t('条记录', 'records')} · {sourceInfo.source?.format || sourceInfo.source?.adapter || t('文件', 'file')}</span></div>
        <details><summary>{t('查看原文样本（最多 5 条）', 'View source samples (up to 5)')}</summary>
          {(sourceInfo.samples || []).map((sample: any, index: number) => <article className="extraction-sample" key={`${sample.record_id || 'sample'}-${index}`}>
            <strong>{sample.record_id || `${t('样本', 'Sample')} ${index + 1}`}</strong>
            {sample.metadata?.line_start && <small>{t('原文行', 'Source lines')} {sample.metadata.line_start}{sample.metadata.line_end ? `–${sample.metadata.line_end}` : ''}</small>}
            <p>{typeof sample.payload === 'string' ? sample.payload : JSON.stringify(sample.payload, null, 2)}</p>
          </article>)}
        </details>
        <details><summary>{t('来源与格式信息', 'Source and format details')}</summary><pre>{JSON.stringify(sourceInfo.source, null, 2)}</pre></details>
      </section>}
      <label className="field-label">{t('抽取目标', 'Extraction goal')}<textarea className="short-textarea" placeholder={t('例如：提取有原文证据的共情回应策略', 'For example: extract empathic response strategies with source evidence')} value={goal} onChange={event => { setGoal(event.target.value); setPlanConfirmed(false) }} /></label>
      <button className="secondary-action" disabled={busy || !sourcePath || !goal.trim()} onClick={() => void suggest()}>{t('让模型建议字段与计划', 'Ask model to propose fields and plan')}</button>
      <p className="hint-note">{t('此操作向当前配置的 LLM 发送最多 5 条源记录；只生成计划，不启动抽取。', 'This sends up to 5 source records to your configured LLM to propose a plan. It does not start extraction.')}</p>
      <label className="field-label">{t('抽取单元', 'Unit definition')}<input value={unit} onChange={event => { setUnit(event.target.value); setPlanConfirmed(false) }} /></label>
      {parsed?.properties && <section><h3>{t('输出字段', 'Output fields')}</h3>{Object.entries(parsed.properties).map(([name, value]) => <div className="studio-row" key={name}><label>{name}<input aria-label={name + ' description'} value={(value as any).description || ''} placeholder={t('字段含义', 'Description')} onChange={event => updateSchema({ ...parsed, properties: { ...parsed.properties, [name]: { ...(value as any), description: event.target.value } } })} /></label><label>{t('类型', 'Type')}<select value={(value as any).type || 'string'} onChange={event => { const type = event.target.value; updateSchema({ ...parsed, properties: { ...parsed.properties, [name]: { ...(value as any), type, ...(type === 'array' ? { items: { type: 'string' } } : {}) } } }) }}>{['string','number','integer','boolean','array','object'].map(type => <option key={type}>{type}</option>)}</select></label><label><input type="checkbox" checked={(parsed.required || []).includes(name)} onChange={event => updateSchema({ ...parsed, required: event.target.checked ? [...(parsed.required || []), name] : (parsed.required || []).filter((item: string) => item !== name) })} />{t('必填', 'Required')}</label><button onClick={() => { const properties = { ...parsed.properties }; delete properties[name]; updateSchema({ ...parsed, properties, required: (parsed.required || []).filter((item: string) => item !== name) }) }}>{t('删除', 'Remove')}</button></div>)}<div className="studio-row"><input aria-label={t('新字段名称', 'New field name')} value={fieldName} onChange={event => setFieldName(event.target.value)} /><button disabled={!fieldName.trim() || Boolean(parsed.properties[fieldName.trim()])} onClick={() => { updateSchema({ ...parsed, properties: { ...parsed.properties, [fieldName.trim()]: { type: 'string' } } }); setFieldName('') }}>{t('添加字段', 'Add field')}</button></div></section>}
      <details open={!parsed}><summary>{t('高级：目标 schema（JSON）', 'Advanced: target schema (JSON)')}</summary><textarea aria-label={t('目标 schema（JSON）', 'Target schema (JSON)')} value={schema} onChange={event => { setSchema(event.target.value); setPlanConfirmed(false) }} />{!parsed && <p role="alert">{t('JSON 格式有误，请检查引号、逗号与括号。', 'Invalid JSON. Check quotes, commas and brackets.')}</p>}</details>
      <label className="field-label">{t('语义审阅', 'Semantic review')}<select aria-label={t('语义审阅', 'Semantic review')} value={semanticReview} onChange={event => setSemanticReview(event.target.value as 'auto' | 'always')}><option value="auto">{t('自动：审阅结论与复杂记录', 'Auto: review claims and complex records')}</option><option value="always">{t('全部：每条记录独立审阅', 'Always: independently review every record')}</option></select></label>
      <p className="hint-note">{t('每条记录均检查字段与逐条引文。独立语义审阅检查原文是否支持结论；发现问题最多定向修复一次并复审。自动模式只对简单的纯引文摘录省略独立审阅；含结论或其他非引文字段的记录都会审阅。', 'Every record receives schema and quote checks. Semantic review checks whether the source supports its claims, with at most one targeted repair and re-review. Auto skips the extra review only for simple verbatim excerpts; claims and other non-quote fields always receive semantic review.')}</p>
      <label className="checkbox-line"><input type="checkbox" checked={planConfirmed} onChange={event => setPlanConfirmed(event.target.checked)} />{t('我已确认字段、抽取单元与证据要求', 'I confirm the fields, unit and evidence requirements')}</label>
      <label>{t('试抽记录数', 'Trial record limit')}<input type="number" min={1} max={100} value={sampleLimit} onChange={event => setSampleLimit(Math.max(1, Math.min(100, Number(event.target.value) || 1)))} /></label>
      <div className="button-row"><button disabled={busy || !sourcePath || !goal.trim() || !parsed || !planConfirmed} onClick={() => void start(true)}>{t('小样本试抽', 'Run sample')}</button></div>
      <section className="notice" aria-label={t('全量运行前确认', 'Before full extraction')}>
        <h3>{t('全量运行前确认', 'Before full extraction')}</h3>
        <p>{t('试抽会覆盖输入首尾。完成后在右侧预览候选，检查字段与原文证据，再接受结果。修改输入或抽取设置后需重新确认。', 'The sample spans the input, including its beginning and end. Preview its candidates on the right, check fields and source evidence, then accept. Changing the source or extraction settings requires a new review.')}</p>
        {trialViewed?.binding === currentKey && <><p>{t('已查看试抽', 'Sample viewed')}: {trialViewed.sample_job_id} · {trialViewed.viewed_count}/{trialViewed.sample_units} {t('条候选', 'candidates')}</p><label className="checkbox-line"><input type="checkbox" checked={trialAccepted} onChange={event => { setTrialAccepted(event.target.checked); if (event.target.checked) setSkipTrial(false) }} />{t('我已查看并接受这些试抽结果，继续全量抽取', 'I have reviewed and accepted this sample for full extraction')}</label></>}
        <label className="checkbox-line"><input type="checkbox" checked={skipTrial} onChange={event => { setSkipTrial(event.target.checked); if (event.target.checked) setTrialAccepted(false) }} />{t('本次明确跳过试抽', 'Explicitly skip the sample for this run')}</label>
        {skipTrial && <label className="field-label">{t('跳过原因（随任务保存）', 'Reason for skipping (saved with the job)')}<textarea className="short-textarea" value={skipReason} maxLength={2000} onChange={event => setSkipReason(event.target.value)} placeholder={t('例如：该输入已在同一设置下人工检查，记录理由以便复核。', 'Explain why you are proceeding without a reviewed sample.')} /></label>}
        <button className="primary large-action" disabled={busy || !sourcePath || !goal.trim() || !parsed || !planConfirmed || !fullRunReady} onClick={() => void start(false)}>{busy ? <LoaderCircle className="spin" /> : <Play size={15} />}{t('启动全量抽取', 'Start full extraction')}</button>
      </section>
      {message && <p className="message-output" role="status">{message}</p>}
    </section><section className="studio-result panel"><h2>{t('抽取任务与结果', 'Extraction jobs and results')}</h2>
      <button onClick={() => void action(refreshHistory)}>{t('刷新任务列表', 'Refresh job list')}</button>
      <div className="job-history">{history.map(row => <button key={row.job_id} className="resource-item" onClick={() => selectJob(row)}><strong>{row.plan?.goal || row.job_id}</strong><span>{statusName(row.status)} · {row.created_at}</span></button>)}</div>
      {job ? <><div className="result-heading"><strong>{job.plan?.goal || job.job_id}</strong><span className={'job-status ' + job.status}>{statusName(job.status)}</span></div><p>{t('进度', 'Progress')}: {job.progress?.processed ?? 0} {t('条已处理', 'processed')} · {job.progress?.failures ?? 0} {t('条失败', 'failed')}</p>{job.error && <p role="alert">{job.error}</p>}{job.trial_review && <p className="hint-note">{job.trial_review.decision === 'accepted' ? t('此任务已记录试抽接受', 'This job records sample acceptance') : t('此任务已记录主动跳过试抽', 'This job records an explicit sample skip')}: {job.trial_review.sample_job_id || job.trial_review.note}</p>}
        <div className="button-row"><button onClick={() => void runAction('preview')} disabled={busy}><ClipboardCheck size={15} />{t('预览候选', 'Preview candidates')}</button><button onClick={() => void runAction('handoff')} disabled={busy || Boolean(receipt) || !['completed', 'partial'].includes(job.status)}><CheckCircle2 size={15} />{t('交给治理', 'Handoff to governance')}</button>{['failed','partial'].includes(job.status) && <button onClick={() => void runAction('retry')} disabled={busy}>{t('重试未完成记录', 'Retry unfinished records')}</button>}<button onClick={() => void runAction('cancel')} disabled={busy || !['queued', 'running'].includes(job.status)}><Square size={14} />{t('取消作业', 'Cancel job')}</button></div>
        {receipt && <div className="notice"><strong>{t('候选已交接，尚未发布为知识', 'Candidates handed off; knowledge is not published')}</strong><p>{receipt.candidate_ids?.length ?? 0} {t('条候选等待审核', 'candidates await review')}</p><a href={'/theraskill/expert/?view=governance&candidate_id=' + encodeURIComponent(receipt.candidate_ids?.[0] || '')}>{t('前往候选治理工作区', 'Open candidate governance')}</a></div>}
        {preview && <section className="extraction-candidate-list"><h3>{t('候选内容与原文依据', 'Candidates and source evidence')} · {preview.items?.length ?? 0}</h3>{(preview.items || []).map((item: any, index: number) => <article className="extraction-candidate" key={`${item.source_id || 'candidate'}-${index}`}><div><strong>{t('候选', 'Candidate')} {index + 1}</strong><span>{item.knowledge_type === 'dynamic_knowledge' ? t('过程知识', 'Process knowledge') : item.knowledge_type || ''}</span></div>{Object.entries(item.content && typeof item.content === 'object' ? item.content : { content: item.content }).map(([name, value]) => <p key={name}><b>{fieldNameLabel(name)}</b><br />{typeof value === 'string' ? value : JSON.stringify(value, null, 2)}</p>)}{item.evidence?.quotes?.map((quote: any, quoteIndex: number) => <blockquote key={quoteIndex}><small>{t('原文引句', 'Source quote')} · {statusName(quote.status || t('待核对', 'Needs review'))}</small><br />{quote.quote}</blockquote>)}{!!item.evidence?.ungrounded_fields?.length && <p role="alert">{t('未获原文支持的字段', 'Fields without source support')}: {item.evidence.ungrounded_fields.join(', ')}</p>}</article>)}<details><summary>{t('完整技术结果', 'Full technical result')}</summary><pre className="json-output">{JSON.stringify(preview, null, 2)}</pre></details></section>}<details><summary>{t('完整任务记录', 'Full job record')}</summary><pre className="json-output">{JSON.stringify(job, null, 2)}</pre></details>
      </> : <div className="empty-result"><ClipboardCheck size={28} />{t('选择历史任务或创建一个新任务。', 'Select a saved job or create a new one.')}</div>}
    </section></div>
  </div>
}
