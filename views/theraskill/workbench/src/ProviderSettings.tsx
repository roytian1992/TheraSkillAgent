import { useEffect, useState } from 'react'
import { CheckCircle2, Eye, EyeOff, FlaskConical, KeyRound, Save, Server, ShieldCheck } from 'lucide-react'
import { api } from './taskApi'

type ProviderBlock = {
  model: string
  base_url: string
  endpoint: string
  api_key: string
  api_key_configured?: boolean
  timeout_seconds?: number
  max_output_tokens?: number
  temperature?: number
  reasoning_effort?: string
  ssl_verify?: boolean
}

type ProviderSnapshot = {
  llm: ProviderBlock
  embedding: ProviderBlock
  active?: Record<string, string>
  files?: string[]
}

const emptyBlock = (): ProviderBlock => ({ model: '', base_url: '', endpoint: '', api_key: '' })

function editableBlock(block?: ProviderBlock): ProviderBlock {
  return {
    ...emptyBlock(),
    ...block,
    api_key: '',
    endpoint: block?.endpoint || '',
    base_url: block?.base_url || '',
    model: block?.model || '',
  }
}

export default function ProviderSettings({ locale, onBack }: { locale: 'zh' | 'en'; onBack: () => void }) {
  const zh = locale === 'zh'
  const [form, setForm] = useState<{ llm: ProviderBlock; embedding: ProviderBlock }>({ llm: emptyBlock(), embedding: emptyBlock() })
  const [snapshot, setSnapshot] = useState<ProviderSnapshot | null>(null)
  const [busy, setBusy] = useState<'load' | 'test' | 'save' | 'save-test' | null>('load')
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error' | 'info'; text: string } | null>(null)
  const [testResult, setTestResult] = useState<any>(null)
  const [showKey, setShowKey] = useState({ llm: false, embedding: false })

  async function load() {
    setBusy('load')
    try {
      const value = await api('/api/resources/providers/config')
      setSnapshot(value)
      setForm({ llm: editableBlock(value.llm), embedding: editableBlock(value.embedding) })
      setNotice(null)
    } catch (error) {
      setNotice({ kind: 'error', text: String(error) })
    } finally { setBusy(null) }
  }
  useEffect(() => { void load() }, [])

  function update(kind: 'llm' | 'embedding', key: keyof ProviderBlock, value: string) {
    setForm(current => ({ ...current, [kind]: { ...current[kind], [key]: value } }))
    setNotice(null)
  }

  function requestPayload() {
    const clean = (block: ProviderBlock) => Object.fromEntries(Object.entries(block).filter(([key, value]) => key === 'api_key' || value !== ''))
    return { llm: clean(form.llm), embedding: clean(form.embedding) }
  }

  async function test() {
    setBusy('test'); setNotice(null); setTestResult(null)
    try {
      const result = await api('/api/resources/providers/test', { kind: 'all', ...requestPayload() })
      setTestResult(result)
      setNotice(result.status === 'ok'
        ? { kind: 'ok', text: zh ? 'LLM 和 embedding 连接测试通过。' : 'LLM and embedding connection tests passed.' }
        : { kind: 'error', text: zh ? '至少一个连接测试失败，请查看下方结果。' : 'At least one connection test failed; see the results below.' })
      return result.status === 'ok'
    } catch (error) {
      setNotice({ kind: 'error', text: String(error) }); return false
    } finally { setBusy(null) }
  }

  async function save() {
    setBusy('save'); setNotice(null)
    try {
      const result = await api('/api/resources/providers/config', requestPayload())
      setSnapshot(result.config)
      setForm(current => ({ llm: { ...current.llm, api_key: '' }, embedding: { ...current.embedding, api_key: '' } }))
      setNotice({ kind: 'ok', text: zh ? `配置已保存到 ${result.path}。密钥不会回显。` : `Configuration saved to ${result.path}. Keys are never echoed.` })
    } catch (error) { setNotice({ kind: 'error', text: String(error) }) } finally { setBusy(null) }
  }

  async function saveAndTest() {
    setBusy('save-test'); setNotice(null); setTestResult(null)
    try {
      const result = await api('/api/resources/providers/test', { kind: 'all', ...requestPayload() })
      setTestResult(result)
      if (result.status !== 'ok') {
        setNotice({ kind: 'error', text: zh ? '测试未通过，因此没有保存配置。' : 'The test did not pass, so the configuration was not saved.' })
        return
      }
      const saved = await api('/api/resources/providers/config', requestPayload())
      setSnapshot(saved.config)
      setForm(current => ({ llm: { ...current.llm, api_key: '' }, embedding: { ...current.embedding, api_key: '' } }))
      setNotice({ kind: 'ok', text: zh ? `测试通过，配置已保存到 ${saved.path}。` : `Tests passed. Configuration saved to ${saved.path}.` })
    } catch (error) { setNotice({ kind: 'error', text: String(error) }) } finally { setBusy(null) }
  }

  const field = (kind: 'llm' | 'embedding', key: keyof ProviderBlock, label: string, placeholder?: string, type = 'text') => (
    <label className="provider-field" key={String(key)}><span>{label}</span><input type={type} value={String(form[kind][key] ?? '')} placeholder={placeholder} onChange={event => update(kind, key, event.target.value)} /></label>
  )
  const card = (kind: 'llm' | 'embedding') => {
    const isLlm = kind === 'llm'
    const configured = Boolean(snapshot?.[kind]?.api_key_configured)
    return <article className="provider-card" key={kind}>
      <div className="provider-card-title"><div><h2>{isLlm ? 'LLM' : 'Embedding'}</h2><p>{isLlm ? (zh ? '用于规划、生成、评估和交互。' : 'Used for planning, generation, evaluation, and interaction.') : (zh ? '用于知识索引和语义检索。' : 'Used for knowledge indexes and semantic retrieval.')}</p></div><Server size={18} /></div>
      {field(kind, 'model', zh ? '模型名' : 'Model name', isLlm ? 'Qwen3.8-27B' : 'bge-m3')}
      {field(kind, 'base_url', 'Base URL', 'https://provider.example/v1')}
      {!isLlm && field(kind, 'endpoint', zh ? 'Embedding endpoint（可选）' : 'Embedding endpoint (optional)', 'https://provider.example/v1/embeddings')}
      <label className="provider-field"><span><KeyRound size={13} /> API key {configured && <em>{zh ? '已配置，留空保持不变' : 'configured; leave blank to keep'}</em>}</span><div className="secret-input"><input type={showKey[kind] ? 'text' : 'password'} value={form[kind].api_key} placeholder={configured ? '••••••••' : 'sk-...'} onChange={event => update(kind, 'api_key', event.target.value)} autoComplete="new-password" /><button type="button" aria-label={showKey[kind] ? 'Hide API key' : 'Show API key'} onClick={() => setShowKey(value => ({ ...value, [kind]: !value[kind] }))}>{showKey[kind] ? <EyeOff size={15} /> : <Eye size={15} />}</button></div></label>
      <div className="provider-optional-grid">{field(kind, 'timeout_seconds', zh ? '超时（秒）' : 'Timeout (seconds)', '600', 'number')}{isLlm && field(kind, 'max_output_tokens', zh ? '最大输出 token' : 'Max output tokens', '4096', 'number')}{isLlm && field(kind, 'reasoning_effort', zh ? '推理强度（可选）' : 'Reasoning effort (optional)', '留空使用服务默认')}</div>
    </article>
  }

  return <div className="workspace-page provider-settings"><div className="workspace-toolbar"><button className="back-button" onClick={onBack}>← {zh ? '返回工作区' : 'Back to workspace'}</button><span className="workspace-status"><ShieldCheck size={15} /> {zh ? '密钥仅保存在本机配置文件' : 'Keys stay in the local config file'}</span></div><div className="workspace-title"><p className="eyebrow">{zh ? '连接设置' : 'CONNECTION SETTINGS'}</p><h1>{zh ? '模型与连接' : 'Models & connections'}</h1><p>{zh ? '在界面中分别配置 LLM 和 embedding，先测试再保存；保存的是当前机器的 providers.local.yaml。' : 'Configure LLM and embedding separately, test before saving, and keep the machine-local providers.local.yaml.'}</p></div><div className="provider-actions"><button onClick={() => void load()} disabled={busy !== null}><Server size={15} /> {zh ? '重新读取' : 'Reload'}</button><button onClick={() => void test()} disabled={busy !== null}><FlaskConical size={15} /> {busy === 'test' ? (zh ? '测试中…' : 'Testing…') : (zh ? '测试当前配置' : 'Test current values')}</button><button className="primary" onClick={() => void save()} disabled={busy !== null}><Save size={15} /> {busy === 'save' ? (zh ? '保存中…' : 'Saving…') : (zh ? '保存配置' : 'Save configuration')}</button><button className="primary" onClick={() => void saveAndTest()} disabled={busy !== null}><CheckCircle2 size={15} /> {busy === 'save-test' ? (zh ? '测试并保存中…' : 'Testing and saving…') : (zh ? '测试通过后保存' : 'Test then save')}</button></div>{notice && <div className={`provider-notice ${notice.kind}`}>{notice.text}</div>}<div className="provider-grid">{card('llm')}{card('embedding')}</div>{testResult && <section className="provider-test-result"><h2><FlaskConical size={17} /> {zh ? '最近一次测试' : 'Latest test'}</h2><div className="provider-test-grid">{Object.entries(testResult.results || {}).map(([kind, value]: [string, any]) => <div className={`provider-test-item ${value.status}`} key={kind}><strong>{kind === 'llm' ? 'LLM' : 'Embedding'} · {value.status}</strong><span>{value.model} · {value.base_url}</span>{value.error && <small>{value.error}</small>}{value.response_preview && <small>{value.response_preview}</small>}{value.vector_dimensions && <small>{value.vector_dimensions} dimensions</small>}</div>)}</div></section>}<p className="provider-footnote">{zh ? '当前生效：' : 'Effective files: '} {snapshot?.files?.join(' · ') || (zh ? '读取中…' : 'loading…')}。{zh ? ' API key 不会出现在界面返回值、日志或测试结果中。' : ' API keys are omitted from UI responses, logs, and test results.'}</p></div>
}
