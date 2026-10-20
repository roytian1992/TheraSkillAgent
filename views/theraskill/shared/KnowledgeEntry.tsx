import { useCallback, useEffect, useRef, useState } from 'react'
import { getKnowledge, knowledgeFields, knowledgeHref, KnowledgeRequestError, rebuildKnowledge, saveKnowledge } from './knowledge'
import type { KnowledgeCitation, KnowledgeDetail, KnowledgeField, KnowledgeFieldValue, KnowledgeJob, KnowledgeLocale, KnowledgeSection, KnowledgeValue } from './knowledge'
import './knowledge.css'

const labels: Record<string, [string, string]> = {
  canonical_label: ['标题', 'Title'], summary: ['知识摘要', 'Summary'], applicable_context: ['适用情境', 'Applicable context'], evidence_source: ['证据来源', 'Evidence source'], allowed_use: ['可以用于', 'Allowed use'], forbidden_use: ['使用边界', 'Boundaries'], contraindications: ['禁忌或注意事项', 'Precautions'],
  release_unit_type: ['知识单元类型', 'Knowledge unit type'], dedup_family: ['知识分类', 'Knowledge family'], canonical_knowledge_type: ['知识类型', 'Knowledge type'], language_distribution: ['语言分布', 'Language distribution'], dataset_distribution: ['数据集分布', 'Dataset distribution'], evidence_tier_distribution: ['证据层级分布', 'Evidence tier distribution'], knowledge_type_distribution: ['知识类型分布', 'Knowledge type distribution'], retrieval_eligible: ['可用于检索', 'Retrieval eligible'], embedding_eligible: ['可生成向量', 'Embedding eligible'], default_route: ['检索路径', 'Retrieval route'],
  source_faithfulness_notes: ['来源保真说明', 'Source faithfulness'], representative_text_ref: ['代表性来源', 'Representative source'], registry_fields: ['知识细节', 'Knowledge details'], dedup_text_preview: ['来源摘录', 'Source excerpt'], provenance: ['来源记录', 'Provenance'], quality: ['评审信息', 'Review information'], retrieval_policy: ['检索规则', 'Retrieval policy'],
  release_unit_id: ['知识条目 ID', 'Knowledge entry ID'], primary_dataset: ['数据集', 'Dataset'], text: ['内容', 'Text'], quote: ['引文', 'Quote'], speaker: ['说话人', 'Speaker'], role: ['角色', 'Role'], turn_id: ['轮次', 'Turn'], source_id: ['来源 ID', 'Source ID'], shared_pattern: ['共同模式', 'Shared pattern'], clinical_boundary_notes: ['适用边界', 'Use boundaries'], action_sequence: ['行动步骤', 'Action sequence'], trigger: ['触发条件', 'Trigger'], response: ['回应', 'Response'], rationale: ['依据', 'Rationale'], source: ['来源', 'Source'], steps: ['步骤', 'Steps'], examples: ['示例', 'Examples'], name: ['名称', 'Name'], description: ['说明', 'Description'],
}
function label(key: string, locale: KnowledgeLocale, fallback?: string) { const name = key.split('.').pop() || key; return labels[name]?.[locale === 'zh' ? 0 : 1] || fallback || name.replaceAll('_', ' ') }
function hasValue(value: KnowledgeValue | undefined): boolean { return value !== undefined && value !== null && value !== '' && (!Array.isArray(value) || value.length > 0) }
export function KnowledgeValueView({ value, locale, depth = 0 }: { value: KnowledgeValue; locale: KnowledgeLocale; depth?: number }) {
  if (value === null) return null
  if (typeof value === 'string') {
    const trimmed = value.trim()
    if ((trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
      try { const parsed = JSON.parse(trimmed); if (parsed && typeof parsed === 'object') return <KnowledgeValueView value={parsed} locale={locale} depth={depth + 1} /> } catch { /* Preserve source prose. */ }
    }
    return <p className="ke-prose">{value}</p>
  }
  if (Array.isArray(value)) return <ul className="ke-value-list">{value.map((item, index) => <li key={index}><KnowledgeValueView value={item} locale={locale} depth={depth + 1} /></li>)}</ul>
  if (typeof value === 'object') return <dl className="ke-value-object">{Object.entries(value).filter(([, item]) => hasValue(item)).map(([key, item]) => <div key={key}><dt>{label(key, locale)}</dt><dd>{depth > 3 && typeof item === 'object' && item !== null ? <details><summary>{locale === 'zh' ? '展开内容' : 'Show content'}</summary><KnowledgeValueView value={item} locale={locale} depth={0} /></details> : <KnowledgeValueView value={item} locale={locale} depth={depth + 1} />}</dd></div>)}</dl>
  return <p className="ke-prose">{typeof value === 'boolean' ? value ? (locale === 'zh' ? '是' : 'Yes') : (locale === 'zh' ? '否' : 'No') : String(value)}</p>
}
function Sections({ sections, locale }: { sections: KnowledgeSection[]; locale: KnowledgeLocale }) { return <div className="ke-sections">{sections.filter(section => hasValue(section.value)).map(section => <section className="ke-field" key={section.key}><h4>{label(section.key, locale, section.label)}</h4><KnowledgeValueView value={section.value} locale={locale} /></section>)}</div> }
const stageLabels: Record<string, [string, string]> = { queued: ['等待开始', 'Queued'], running: ['进行中', 'Running'], succeeded: ['已完成', 'Completed'], failed: ['失败', 'Failed'], interrupted: ['已中断', 'Interrupted'], cancelled: ['已取消', 'Cancelled'], materialize_overrides: ['整理知识修订', 'Preparing revised knowledge'], source_catalog: ['建立原始内容目录', 'Preparing source catalog'], build: ['构建检索索引', 'Building retrieval index'], embedding: ['生成向量', 'Generating embeddings'], embeddings: ['生成向量', 'Generating embeddings'], lexical: ['构建词法索引', 'Building lexical index'], verify: ['验证完整性', 'Verifying integrity'], complete: ['构建完成', 'Build complete'], active: ['已启用', 'Active'], inactive: ['历史版本', 'Historical version'], stale: ['草稿尚未建立索引', 'Draft not indexed'], building: ['索引构建中', 'Index building'], built: ['候选索引已构建', 'Candidate index built'], pending: ['等待构建', 'Pending build'] }
export function KnowledgeJobView({ job, locale }: { job: KnowledgeJob; locale: KnowledgeLocale }) {
  const t = (zh: string, en: string) => locale === 'zh' ? zh : en
  const progress = job.progress || {}
  const stage = typeof progress.stage === 'string' ? progress.stage : ''
  const stageText = stageLabels[stage]?.[locale === 'zh' ? 0 : 1] || stage.replaceAll('_', ' ')
  const completed = typeof progress.bytes === 'number' ? progress.bytes : typeof progress.completed === 'number' ? progress.completed : undefined
  const total = typeof progress.total_bytes === 'number' ? progress.total_bytes : typeof progress.total === 'number' ? progress.total : undefined
  const percent = typeof progress.percent === 'number' ? Math.min(100, Math.max(0, progress.percent)) : completed !== undefined && total && total > 0 ? Math.min(100, completed / total * 100) : undefined
  const counts = progress.counts && typeof progress.counts === 'object' ? progress.counts as Record<string, number> : {}
  const candidate = job.result?.index_directory_id
  return <section className={'ke-job ' + (['failed', 'interrupted', 'cancelled'].includes(job.status) ? 'ke-failed' : '')} aria-label={t('索引任务', 'Index job')}>
    <div className="ke-heading"><h3>{t('索引重建', 'Index rebuild')}</h3><span className="ke-badge">{stageLabels[job.status]?.[locale === 'zh' ? 0 : 1] || job.status}</span></div>
    <p className="ke-muted">{job.id}</p>{stageText && <p>{stageText}{percent !== undefined ? ' · ' + Math.round(percent) + '%' : ''}</p>}{percent !== undefined && <progress max={100} value={percent} aria-label={t('索引构建进度', 'Index build progress')} />}
    {typeof counts.indexed === 'number' && <p className="ke-muted">{t('已索引记录', 'Indexed records')}: {counts.indexed.toLocaleString()}{typeof counts.vectors === 'number' ? ' · ' + t('向量', 'Vectors') + ': ' + counts.vectors.toLocaleString() : ''}</p>}
    {typeof progress.line === 'number' && <p className="ke-muted">{t('已处理记录', 'Records processed')}: {progress.line.toLocaleString()}</p>}
    {job.error && <p role="alert">{job.error}</p>}
    {job.status === 'succeeded' && <p>{t('候选索引已构建。完成验证和效果对比后，再启用该版本。', 'The candidate index is built. Verify it and compare behavior before activating this version.')}</p>}
    <div className="ke-actions">{job.resources_url && <a href={job.resources_url}>{t('查看任务、验证与启用', 'View job, verification and activation')}</a>}{candidate && job.status === 'succeeded' && <a href={'/?workspace=evaluation&index=' + encodeURIComponent(candidate)}>{t('评估这个候选索引', 'Evaluate this candidate index')}</a>}</div>
  </section>
}
function inputValues(value: KnowledgeDetail): Partial<Record<KnowledgeField, KnowledgeFieldValue>> { const result: Partial<Record<KnowledgeField, KnowledgeFieldValue>> = {}; for (const key of knowledgeFields) { const item = value[key]; if (typeof item === 'string') result[key] = item; else if (Array.isArray(item) && item.every(entry => typeof entry === 'string')) result[key] = item as string[]; else if (item === undefined || item === null) result[key] = '' } return result }
function same(a: unknown, b: unknown) { return JSON.stringify(a) === JSON.stringify(b) }

export default function KnowledgeEntry({ id, indexId, checksum, citation, locale, workspace = false, onDirtyChange }: { id: string; indexId?: string; checksum?: string; citation?: KnowledgeCitation; locale: KnowledgeLocale; workspace?: boolean; onDirtyChange?: (dirty: boolean) => void }) {
  const t = (zh: string, en: string) => locale === 'zh' ? zh : en
  const [detail, setDetail] = useState<KnowledgeDetail | null>(null)
  const [baseline, setBaseline] = useState<KnowledgeDetail | null>(null)
  const [draft, setDraft] = useState<Partial<Record<KnowledgeField, KnowledgeFieldValue>>>({})
  const [editing, setEditing] = useState(false)
  const [loading, setLoading] = useState(true)
  const [mutation, setMutation] = useState<'save' | 'reindex' | null>(null)
  const [error, setError] = useState('')
  const [pollError, setPollError] = useState('')
  const [conflict, setConflict] = useState(false)
  const [notice, setNotice] = useState('')
  const [retryLoad, setRetryLoad] = useState(0)
  const alive = useRef(true), serial = useRef(0), pinned = useRef(indexId), mutating = useRef(false), editingRef = useRef(false)
  const baselineValues = baseline ? inputValues(baseline) : {}
  const dirtyKeys = knowledgeFields.filter(key => Object.prototype.hasOwnProperty.call(draft, key) && !same(draft[key], baselineValues[key]))
  const dirty = editing && dirtyKeys.length > 0
  useEffect(() => { onDirtyChange?.(dirty); return () => onDirtyChange?.(false) }, [dirty, onDirtyChange])
  useEffect(() => { editingRef.current = editing }, [editing])
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])
  const readableError = useCallback((reason: unknown) => {
    if (reason instanceof KnowledgeRequestError && reason.status === 409) return locale === 'zh' ? '此知识的版本或当前索引已变化。重新读取后检查你的修改，再保存。' : 'This entry or the active index changed. Reload, review your edits, and save again.'
    if (reason instanceof KnowledgeRequestError && reason.status === 404) return locale === 'zh' ? '本次引用的知识版本未安装或已移除。仍可查看对话中保存的引用摘录。' : 'The cited knowledge version is not installed or has been removed. The citation excerpt remains available.'
    return reason instanceof Error ? reason.message : String(reason)
  }, [locale])
  const checked = useCallback((value: KnowledgeDetail) => {
    if (checksum && value.source?.knowledge_sha256 !== checksum) throw new Error(locale === 'zh' ? '知识来源校验不一致；请检查本次引用对应的资源版本。' : 'The source checksum does not match this citation. Check the cited resource version.')
    if (pinned.current && value.index_id !== pinned.current) throw new Error(locale === 'zh' ? '返回的知识索引与引用版本不一致。' : 'The returned index does not match the cited version.')
    pinned.current ||= value.index_id
    return value
  }, [checksum, locale])
  useEffect(() => {
    alive.current = true
    const controller = new AbortController(), ticket = ++serial.current
    setLoading(true); setError('')
    void getKnowledge(id, pinned.current, controller.signal).then(checked).then(value => {
      if (!alive.current || ticket !== serial.current) return
      setDetail(value); setBaseline(value); setDraft(inputValues(value)); setConflict(false)
    }).catch(reason => { if (!controller.signal.aborted && alive.current && ticket === serial.current) setError(readableError(reason)) }).finally(() => { if (alive.current && ticket === serial.current) setLoading(false) })
    return () => { alive.current = false; controller.abort(); serial.current++ }
  }, [id, indexId, retryLoad])
  const running = ['queued', 'running'].includes(detail?.reindex_job?.status || '')
  const preparing = detail?.source?.status === 'preparing'
  useEffect(() => {
    if (!running && !preparing) return
    let cancelled = false, timer: number | undefined
    const controller = new AbortController()
    const poll = async () => {
      if (cancelled) return
      if (mutating.current) { timer = window.setTimeout(poll, 1200); return }
      const ticket = ++serial.current
      try {
        const value = checked(await getKnowledge(id, pinned.current, controller.signal))
        if (!cancelled && alive.current && ticket === serial.current) {
          setDetail(value); setPollError('')
          if (!editingRef.current) { setBaseline(value); setDraft(inputValues(value)) }
        }
      } catch (reason) { if (!cancelled && alive.current && !controller.signal.aborted) setPollError((locale === 'zh' ? '进度暂时无法刷新：' : 'Progress could not be refreshed: ') + readableError(reason)) }
      if (!cancelled) timer = window.setTimeout(poll, 1500)
    }
    timer = window.setTimeout(poll, 1200)
    return () => { cancelled = true; controller.abort(); if (timer) window.clearTimeout(timer) }
  }, [id, running, preparing, checked, readableError, locale])
  const reloadKeepingEdits = async () => {
    if (mutating.current) return
    mutating.current = true
    const edits = Object.fromEntries(dirtyKeys.map(key => [key, draft[key]]))
    const ticket = ++serial.current
    setLoading(true); setError('')
    try {
      const latest = checked(await getKnowledge(id, pinned.current))
      if (!alive.current || ticket !== serial.current) return
      setDetail(latest); setBaseline(latest); setDraft({ ...inputValues(latest), ...edits }); setConflict(false)
      setNotice(t('已读取新版本并保留你的修改。请核对后再次保存。', 'The latest version is loaded and your edits are retained. Review them before saving again.'))
    } catch (reason) { if (alive.current) setError(readableError(reason)) }
    finally { mutating.current = false; if (alive.current) setLoading(false) }
  }
  const save = async () => {
    if (!baseline || !dirty || mutating.current || conflict) return
    const changes = Object.fromEntries(dirtyKeys.map(key => [key, draft[key]]))
    if (typeof draft.canonical_label === 'string' && !draft.canonical_label.trim()) { setError(t('标题不能为空。', 'The title cannot be empty.')); return }
    mutating.current = true; serial.current++; setMutation('save'); setError(''); setNotice('')
    try {
      const value = checked(await saveKnowledge(id, baseline, changes))
      if (!alive.current) return
      setDetail(value); setBaseline(value); setDraft(inputValues(value)); setEditing(false)
      setNotice(t('草稿已保存，当前引用保持不变。重建候选索引后可验证、评估并启用。', 'The draft is saved; the cited version is unchanged. Rebuild a candidate index, then verify, evaluate and activate it.'))
    } catch (reason) { if (alive.current) { setError(readableError(reason)); setConflict(reason instanceof KnowledgeRequestError && reason.status === 409) } }
    finally { mutating.current = false; if (alive.current) setMutation(null) }
  }
  const rebuild = async () => {
    if (!detail || dirty || running || mutating.current) return
    mutating.current = true; serial.current++; setMutation('reindex'); setError(''); setNotice('')
    try {
      const job = await rebuildKnowledge(id, detail)
      if (!alive.current) return
      setDetail(current => current ? { ...current, activation_required: true, reindex_job: { id: job.job_id, status: job.status, resources_url: '/?workspace=knowledge&job_id=' + encodeURIComponent(job.job_id) } } : current)
      setNotice(t('索引任务已提交，可离开页面后继续查看。完成构建不会自动启用。', 'The index job is saved and can be revisited later. Completing the build does not activate it.'))
      const value = checked(await getKnowledge(id, pinned.current))
      if (alive.current) { setDetail(value); if (!editingRef.current) { setBaseline(value); setDraft(inputValues(value)) } }
    } catch (reason) { if (alive.current) { setError(readableError(reason)); setConflict(reason instanceof KnowledgeRequestError && reason.status === 409) } }
    finally { mutating.current = false; if (alive.current) setMutation(null) }
  }
  const editable = detail?.editable_fields || knowledgeFields
  const sections = detail?.indexed_sections || (detail?.indexed_values ? knowledgeFields.map(key => ({ key, label: key, value: detail.indexed_values?.[key] ?? null })) : [])
  const currentHref = detail?.active_index_id && detail.active_index_id !== detail.index_id ? knowledgeHref(id, detail.active_index_id) : null
  return <div className="knowledge-entry" data-testid="knowledge-entry" aria-busy={loading || !!mutation}>
    {citation?.excerpt && <section className="ke-citation"><h3>{t('这条回应引用的摘录', 'Excerpt cited by this response')}</h3><blockquote>{citation.excerpt}</blockquote><p className="ke-muted">{citation.dataset}{citation.source_index_id || citation.index_id ? ' · ' + (citation.source_index_id || citation.index_id) : ''}</p></section>}
    {citation && !indexId && <p className="ke-history">{t('这条历史引用未保存索引版本。下方读取当前已安装的知识条目，无法确认它与当时引用的版本一致。对话中的原引用摘录保持不变。', 'This older citation has no saved index version. The entry below comes from the currently installed index; its match to the originally cited version cannot be confirmed. The saved citation excerpt is unchanged.')}</p>}
    {loading && <p role="status" className="ke-status">{t('正在载入知识内容…', 'Loading knowledge content…')}</p>}
    {error && <div className="ke-alert" role="alert"><p>{error}</p>{conflict ? <button onClick={() => void reloadKeepingEdits()} disabled={loading || !!mutation}>{t('重新读取版本（保留输入）', 'Reload version (keep edits)')}</button> : !detail ? <button onClick={() => setRetryLoad(value => value + 1)} disabled={loading}>{t('重新加载', 'Reload')}</button> : null}</div>}
    {pollError && <p className="ke-alert" role="alert">{pollError}</p>}
    {notice && <p className="ke-notice" role="status">{notice}</p>}
    {detail && <>
      <div className="ke-heading"><div><p className="ke-eyebrow">{t('知识条目', 'Knowledge entry')}</p><h2>{typeof detail.canonical_label === 'string' ? detail.canonical_label : id}</h2></div><span className={'ke-badge status-' + detail.index_status}>{stageLabels[detail.index_status || '']?.[locale === 'zh' ? 0 : 1] || detail.index_status}</span></div>
      <dl className="ke-meta"><div><dt>{t('来源', 'Source')}</dt><dd>{detail.source?.kind === 'original' ? t('原始知识字段', 'Original knowledge fields') : t('索引保存的段落', 'Passage stored in the index')}</dd></div><div><dt>{t('草稿修订', 'Draft revision')}</dt><dd>{detail.revision}</dd></div>{detail.primary_dataset && <div><dt>{t('数据集', 'Dataset')}</dt><dd>{detail.primary_dataset}</dd></div>}<div><dt>{t('知识条目 ID', 'Knowledge entry ID')}</dt><dd>{detail.release_unit_id || id}</dd></div><div><dt>{t('引用索引', 'Referenced index')}</dt><dd>{detail.index_id}</dd></div><div><dt>{t('索引方式', 'Index mode')}</dt><dd>{(detail.index_mode || detail.mode) === 'hybrid' ? t('混合检索 · 包含 embedding', 'Hybrid retrieval · includes embeddings') : t('词法检索 · 无需 embedding', 'Lexical retrieval · no embeddings')}</dd></div></dl>
      {preparing && <p className="ke-status" role="status">{t('正在后台整理原始内容目录。先展示已索引段落，原始字段就绪后会自动载入。', 'The source catalog is being prepared in the background. The indexed passage is available now; original fields will appear when ready.')}</p>}
      {detail.source?.status === 'failed' && <p className="ke-alert" role="alert">{t('原始内容目录暂不可用：', 'The original source catalog is unavailable: ')}{detail.source.error}</p>}
      {detail.source?.status === 'missing' && <p className="ke-muted">{t('该版本的原始知识文件尚未安装；可阅读索引保存的内容。', 'The original knowledge file for this version is not installed. You can read the content retained in the index.')}</p>}
      {currentHref && <p className="ke-history">{t('这是历史引用版本。', 'This is a historical citation version.')} <a href={currentHref}>{t('在当前索引中查找此条目', 'Find this entry in the active index')}</a></p>}
      <section className="ke-source"><h3>{detail.source?.kind === 'original' ? t('原始知识内容', 'Original knowledge content') : t('已索引的知识内容', 'Indexed knowledge content')}</h3><Sections sections={sections.filter(section => !section.key.startsWith('source.'))} locale={locale} />{sections.some(section => section.key.startsWith('source.')) && <details className="ke-source-meta"><summary>{t('来源与索引信息', 'Source and index information')}</summary><Sections sections={sections.filter(section => section.key.startsWith('source.'))} locale={locale} /></details>}{!sections.length && detail.indexed_passage && <KnowledgeValueView value={detail.indexed_passage} locale={locale} />}</section>
      {!!detail.edited_fields?.length && !editing && <section className="ke-draft"><h3>{t('已保存的草稿修改', 'Saved draft changes')}</h3><p className="ke-muted">{t('这些修改尚未替换引用版本。', 'These changes have not replaced the cited version.')}</p><Sections sections={detail.edited_fields.map(key => ({ key, label: key, value: detail[key as KnowledgeField] ?? null }))} locale={locale} /></section>}
      {editing && <section className="ke-edit" aria-label={t('编辑知识草稿', 'Edit knowledge draft')}><div className="ke-heading"><h3>{t('编辑知识草稿', 'Edit knowledge draft')}</h3><span className="ke-muted">{t('基于修订', 'Based on revision')} {baseline?.revision}</span></div>{knowledgeFields.filter(key => editable.includes(key)).map(key => {
        const value = draft[key]
        if (value === undefined) return <div className="ke-field" key={key}><h4>{label(key, locale)}</h4><p className="ke-muted">{t('该字段为复合结构，保留原始内容。', 'This field contains structured data and is preserved as-is.')}</p><KnowledgeValueView value={baseline?.[key] ?? null} locale={locale} /></div>
        return <label className="ke-input" key={key}><span>{label(key, locale)}{Array.isArray(value) ? ' · ' + t('每行一项', 'one item per line') : ''}</span><textarea aria-label={label(key, locale) + (Array.isArray(value) ? ' · ' + t('每行一项', 'one item per line') : '')} rows={key === 'canonical_label' ? 1 : 3} value={Array.isArray(value) ? value.join('\n') : value} disabled={!!mutation} onChange={event => { const next = Array.isArray(value) ? event.target.value.split(/\r?\n/) : event.target.value; setDraft(current => ({ ...current, [key]: next })) }} /></label>
      })}{dirty && <p className="ke-muted" role="status">{t('有未保存的修改', 'You have unsaved changes')}</p>}</section>}
      <div className="ke-actions">{editing ? <><button className="ke-primary" disabled={!dirty || !!mutation || loading || conflict || detail.can_edit === false} onClick={() => void save()}>{mutation === 'save' ? t('保存中…', 'Saving…') : t('保存草稿', 'Save draft')}</button><button disabled={!!mutation} onClick={() => { setEditing(false); setConflict(false); setError(''); setBaseline(detail); setDraft(inputValues(detail)) }}>{t('取消编辑', 'Cancel editing')}</button></> : <button className="ke-primary" disabled={loading || !!mutation || detail.can_edit === false} onClick={() => { setBaseline(detail); setDraft(inputValues(detail)); setEditing(true); setNotice('') }}>{t('编辑知识', 'Edit knowledge')}</button>}
        <button disabled={loading || !!mutation || dirty || running || detail.can_reindex === false || conflict} onClick={() => void rebuild()}>{mutation === 'reindex' ? t('提交中…', 'Submitting…') : ['failed', 'interrupted', 'cancelled'].includes(detail.reindex_job?.status || '') ? t('重试索引任务', 'Retry index job') : running ? t('索引任务进行中', 'Index job in progress') : t('重新生成索引', 'Rebuild index')}</button>
        {!workspace && <a href={knowledgeHref(id, detail.index_id, checksum || detail.source?.knowledge_sha256)}>{t('在知识工作区打开', 'Open knowledge workspace')} ↗</a>}
      </div>
      <p className="ke-muted">{(detail.mode || detail.index_mode) === 'hybrid' ? t('重建会生成完整的候选知识索引，可能对整个知识库调用配置的 embedding 服务；大型知识库需要较长时间。完成后需验证、评估并启用。', 'Rebuilding creates a full candidate knowledge index and may call the configured embedding service for the entire library. Large libraries take time. Verify, evaluate and activate it when complete.') : t('重建会生成完整的候选词法索引；大型知识库需要较长时间。完成后需验证、评估并启用。', 'Rebuilding creates a full candidate lexical index. Large libraries take time. Verify, evaluate and activate it when complete.')}</p>
      {detail.reindex_job && <KnowledgeJobView job={detail.reindex_job} locale={locale} />}
    </>}
  </div>
}
