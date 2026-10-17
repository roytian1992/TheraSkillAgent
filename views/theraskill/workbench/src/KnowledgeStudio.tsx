import { useEffect, useState } from 'react'

type Job = { id: string; operation: string; status: string; parameters: Record<string, unknown>; progress?: unknown; result?: unknown; error?: string }
type Resource = { id: string; manifest: { counts?: Record<string, number>; input?: { mode: string }; embedding?: { name?: string; dimension?: number } } }
type Catalog = { indexes: Resource[]; knowledge: Resource[]; active?: { index: string; index_id: string; mode: string }; previous?: unknown; jobs: Job[] }

export default function KnowledgeStudio({ locale, onBack }: { locale: 'zh' | 'en'; onBack: () => void }) {
  const zh = locale === 'zh'
  const [catalog, setCatalog] = useState<Catalog>({ indexes: [], knowledge: [], jobs: [] })
  const [source, setSource] = useState('')
  const [indexId, setIndexId] = useState('my-knowledge-v1')
  const [mode, setMode] = useState('lexical')
  const [profile, setProfile] = useState('local_bge')
  const [archive, setArchive] = useState('')
  const [catalogUrl, setCatalogUrl] = useState('')
  const [packageId, setPackageId] = useState('')
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [detail, setDetail] = useState<unknown>(null)
  const [selectedJob, setSelectedJob] = useState('')
  useEffect(() => { const job = catalog.jobs.find(item => item.id === selectedJob); if (job) setDetail(job.result ?? null) }, [catalog, selectedJob])
  const operationName = (name: string) => zh ? ({plan: '构建计划', build: '索引构建', verify: '完整性验证', search: '检索测试', activate: '启用索引', rollback: '恢复索引', install: '安装资源'}[name] ?? name) : name
  const statusName = (name: string) => zh ? ({queued: '等待中', running: '进行中', succeeded: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断'}[name] ?? name) : name
  const result = detail as { counts?: Record<string, number>; hits?: {canonical_label?: string; retrieval_text: string; release_unit_id: string}[]; embedding_requests_estimate?: number } | null
  async function request(url: string, init?: RequestInit) {
    const response = await fetch('/api/resources' + url, init)
    const data = await response.json()
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data))
    return data
  }
  async function refresh() { setCatalog(await request('')) }
  useEffect(() => {
    void refresh().catch(e => setError(String(e)))
    const timer = setInterval(() => void refresh().catch(e => setError(String(e))), 2000)
    return () => clearInterval(timer)
  }, [])
  async function submit(operation: string, parameters: Record<string, unknown>) {
    setBusy(true); setError('')
    try { const job = await request('/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ operation, parameters }) }); setSelectedJob(job.id); setDetail(null); await refresh() }
    catch (e) { setError(String(e)) } finally { setBusy(false) }
  }
  const buildParams = { knowledge: source, index_id: indexId, mode, ...(mode === 'hybrid' ? { embedding_profile: profile } : {}) }
  return <section className="knowledge-studio">
    <button onClick={onBack}>{zh ? '返回工作台' : 'Back to workbench'}</button>
    <h1>{zh ? '知识与索引' : 'Knowledge & indexes'}</h1>
    <p>{zh ? '导入知识，构建检索索引，验证后启用。词法检索无需模型；混合检索使用你配置的 embedding 服务。' : 'Import knowledge, build an index, verify it and activate it. Lexical retrieval needs no model; hybrid retrieval uses your configured embedding service.'}</p>
    {error && <p role="alert" className="resource-error">{error}</p>}
    <p>{zh ? '当前索引：' : 'Active index: '}{catalog.active ? `${catalog.active.index.split('/').pop()} (${catalog.active.mode})` : (zh ? '尚未选择新索引' : 'No managed index selected')}</p>
    <div className="columns">
      <article className="panel resource-form"><h2>{zh ? '从知识构建' : 'Build from knowledge'}</h2>
        <label>{zh ? '知识文件路径或已安装知识 ID' : 'Knowledge file path or installed ID'}<input value={source} onChange={e => setSource(e.target.value)} placeholder="examples/knowledge/demo.jsonl" /></label>
        {catalog.knowledge.length > 0 && <select aria-label={zh ? '选择已安装知识' : 'Select installed knowledge'} value="" onChange={e => setSource(e.target.value)}><option value="">{zh ? '选择已安装知识' : 'Select installed knowledge'}</option>{catalog.knowledge.map(r => <option key={r.id}>{r.id}</option>)}</select>}
        <label>{zh ? '上传 JSONL（最多 32 MiB）' : 'Upload JSONL (up to 32 MiB)'}<input type="file" accept=".jsonl" disabled={busy} onChange={async e => {
          const file = e.target.files?.[0]; if (!file) return
          setBusy(true); setError('')
          try { const result = await request('/upload', { method: 'POST', body: file }); setSource(result.knowledge); setDetail(result.plan) } catch (err) { setError(String(err)) } finally { setBusy(false) }
        }} /></label>
        <label>{zh ? '新索引名称' : 'New index name'}<input value={indexId} onChange={e => setIndexId(e.target.value)} /></label>
        <label>{zh ? '检索方式' : 'Retrieval mode'}<select value={mode} onChange={e => setMode(e.target.value)}><option value="lexical">{zh ? '词法 · 无需模型' : 'Lexical · no model'}</option><option value="hybrid">{zh ? '混合 · 使用 embedding 服务' : 'Hybrid · uses embedding service'}</option></select></label>
        {mode === 'hybrid' && <label>{zh ? 'Embedding 配置名' : 'Embedding profile'}<input value={profile} onChange={e => setProfile(e.target.value)} /></label>}
        <div className="resource-actions"><button disabled={busy || !source} onClick={() => void submit('plan', buildParams)}>{zh ? '预览构建计划' : 'Preview plan'}</button><button disabled={busy || !source || !indexId} onClick={() => void submit('build', buildParams)}>{zh ? '开始构建' : 'Build index'}</button><button disabled={busy || !source || !indexId} onClick={() => void submit('build', { ...buildParams, resume: true })}>{zh ? '继续未完成构建' : 'Resume build'}</button></div>
      </article>
      <article className="panel resource-form"><h2>{zh ? '安装独立资源包' : 'Install a resource package'}</h2>
        <label>{zh ? '本地压缩包路径（tar.gz）' : 'Local archive path (tar.gz)'}<input value={archive} onChange={e => setArchive(e.target.value)} /></label>
        <button disabled={busy || !archive} onClick={() => void submit('install', { archive })}>{zh ? '安装本地包' : 'Install local package'}</button>
        <label>{zh ? '下载目录 URL 或本地路径' : 'Catalog URL or local path'}<input value={catalogUrl} onChange={e => setCatalogUrl(e.target.value)} /></label>
        <label>{zh ? '资源包 ID' : 'Package ID'}<input value={packageId} onChange={e => setPackageId(e.target.value)} /></label>
        <button disabled={busy || !catalogUrl || !packageId} onClick={() => void submit('install', { catalog: catalogUrl, package: packageId })}>{zh ? '下载并安装' : 'Download and install'}</button>
        <p>{zh ? '安装不会自动切换当前索引。知识包和预构建索引可分别提供。' : 'Installation does not change the active index. Knowledge and prebuilt indexes are distributed separately.'}</p>
      </article>
    </div>
    <article className="panel resource-form"><h2>{zh ? '已安装索引' : 'Installed indexes'}</h2>
      <label>{zh ? '检索测试问题' : 'Test query'}<input value={query} onChange={e => setQuery(e.target.value)} /></label>
      {catalog.indexes.length === 0 && <p>{zh ? '还没有索引，可以先用自编小样例构建。' : 'No indexes yet. Start with the synthetic demo.'}</p>}
      {catalog.indexes.map(item => <div className="resource-item" key={item.id}><strong>{item.id}</strong><span>{item.manifest.input?.mode} · {item.manifest.counts?.indexed ?? 0} {zh ? '条可检索记录' : 'searchable records'}</span><div className="resource-actions"><button disabled={busy} onClick={() => void submit('verify', { index: item.id })}>{zh ? '验证' : 'Verify'}</button><button disabled={busy} onClick={() => void submit('activate', { index: item.id })}>{zh ? '启用' : 'Activate'}</button><button disabled={busy || !query} onClick={() => void submit('search', { index: item.id, query })}>{zh ? '词法测试' : 'Lexical test'}</button>{item.manifest.embedding && <button disabled={busy || !query || !profile} onClick={() => void submit('search', { index: item.id, query, embedding_profile: profile })}>{zh ? '混合测试' : 'Hybrid test'}</button>}</div></div>)}
      <button disabled={busy || !catalog.previous} onClick={() => void submit('rollback', {})}>{zh ? '恢复上一个索引' : 'Restore previous index'}</button>
    </article>
    <article className="panel"><h2>{zh ? '任务与结果' : 'Jobs & results'}</h2>{catalog.jobs.map(job => <div className="resource-item" key={job.id}><strong>{operationName(job.operation)} · {statusName(job.status)}</strong>{job.error && <p role="alert">{job.error}</p>}{job.progress !== undefined && <details><summary>{zh ? '构建进度详情' : 'Progress details'}</summary><pre>{JSON.stringify(job.progress, null, 2)}</pre></details>}<div className="resource-actions"><button onClick={() => { setSelectedJob(job.id); setDetail(job.result ?? null) }}>{zh ? '查看结果' : 'View result'}</button>{['running', 'queued'].includes(job.status) && <button onClick={() => void request(`/jobs/${job.id}/cancel`, { method: 'POST' }).then(refresh).catch(e => setError(String(e)))}>{zh ? '取消' : 'Cancel'}</button>}{['failed', 'cancelled', 'interrupted'].includes(job.status) && job.operation === 'build' && <button onClick={() => void submit('build', { ...job.parameters, resume: true })}>{zh ? '重试 / 继续' : 'Retry / resume'}</button>}</div></div>)}{result && <div className="resource-result">{result.counts && <p>{zh ? '记录总数' : 'Total records'}: {result.counts.total} · {zh ? '允许检索' : 'Eligible'}: {result.counts.eligible} · {zh ? '已构建向量' : 'Vectors built'}: {result.counts.vectors ?? 0}</p>}{result.embedding_requests_estimate !== undefined && <p>{zh ? '预计 embedding 请求次数' : 'Estimated embedding requests'}: {result.embedding_requests_estimate}</p>}{result.hits?.map(hit => <div className="resource-item" key={hit.release_unit_id}><strong>{hit.canonical_label || hit.release_unit_id}</strong><p>{hit.retrieval_text}</p></div>)}<details><summary>{zh ? '查看完整结果' : 'View full result'}</summary><pre className="resource-detail">{JSON.stringify(detail, null, 2)}</pre></details></div>}</article>
  </section>
}
