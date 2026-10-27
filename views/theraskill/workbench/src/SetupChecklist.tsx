import { useEffect, useState } from 'react'
import { api } from './taskApi'

type Check = { status?: string; error?: string }
type Readiness = {
  llm: { model?: string; status?: string }
  embedding: { model?: string; status?: string }
  index: { ready: boolean; id?: string }
  simulation: { ready: boolean }
  skills: Array<{ ready: boolean }>
  text_policy: { max_chars: number; overlap: number }
}

export default function SetupChecklist({ locale }: { locale: 'zh' | 'en' }) {
  const t = (zh: string, en: string) => locale === 'zh' ? zh : en
  const [data, setData] = useState<Readiness | null>(null)
  const [error, setError] = useState('')
  const [live, setLive] = useState<Record<string, Check>>({})
  const [busy, setBusy] = useState('')

  const refresh = () => api('/api/resources/readiness').then(setData).catch(reason => setError(String(reason)))
  useEffect(() => { void refresh() }, [])

  async function test(kind: string) {
    setBusy(kind)
    setError('')
    setLive(previous => ({ ...previous, [kind]: { status: 'checking' } }))
    try {
      const result = await api('/api/resources/providers/check', { kind })
      setLive(previous => ({ ...previous, [kind]: result }))
    } catch (reason) { setError(String(reason)) } finally { setBusy('') }
  }

  const modelStatus = (kind: 'llm' | 'embedding') => {
    if (!data?.[kind]?.model) return t('尚未配置', 'Not configured')
    if (live[kind]?.status === 'checking') return t('连接检测中', 'Checking connection')
    if (live[kind]?.status === 'ok') return t('连接正常', 'Connection works')
    if (live[kind]?.status === 'error') return t('连接失败', 'Connection failed')
    return t('已保存配置，尚未检测连接', 'Saved, connection not tested')
  }

  return <section className="panel setup-checklist">
    <h2>{t('使用准备', 'Getting ready')}</h2>
    <p>{t('查看模型与素材是否可用。测试连接会向模型服务发送一次请求。', 'Check whether models and resources are available. A connection test sends one request to the model service.')}</p>
    {error && <p role="alert">{error}</p>}
    {data && <>
      <div className="resource-grid">
        {(['llm', 'embedding'] as const).map(kind => <article key={kind}>
          <h3>{kind === 'llm' ? t('对话模型', 'Conversation model') : t('语义检索模型', 'Semantic search model')}</h3>
          <p>{data[kind].model || t('尚未配置', 'Not configured')}</p>
          <p role="status">{modelStatus(kind)}{live[kind]?.error && ` · ${live[kind].error}`}</p>
          <button disabled={!!busy || !data[kind].model} onClick={() => void test(kind)}>{t('测试连接', 'Test connection')}</button>
        </article>)}
        <article>
          <h3>{t('知识', 'Knowledge')}</h3>
          <p>{data.index.ready ? t('已有可用知识', 'Knowledge is ready') : t('尚未启用知识', 'No active knowledge')}</p>
          <a href="/?workspace=knowledge">{t('查找或添加知识', 'Find or add knowledge')}</a>
        </article>
        <article>
          <h3>{t('模拟情景', 'Simulation scenarios')}</h3>
          <p>{data.simulation.ready ? t('素材已就绪', 'Resources are ready') : t('尚未安装情景素材', 'Scenario resources are missing')}</p>
          {!data.simulation.ready && <a href="/?workspace=knowledge">{t('安装资源包', 'Install a resource package')}</a>}
          <p>{data.skills.filter(skill => skill.ready).length}/{data.skills.length} {t('项技能可用', 'skills available')}</p>
        </article>
      </div>
      <div className="setup-checklist-footer"><a href="/?workspace=settings">{t('管理模型配置', 'Manage model settings')}</a><button onClick={() => void refresh()}>{t('重新检查', 'Refresh checks')}</button></div>
      <details className="setup-technical"><summary>{t('查看技术配置', 'Technical details')}</summary>
        <p>{t('知识版本', 'Knowledge version')}: {data.index.id || t('无', 'None')}</p>
        <p>{t('文本分段', 'Text segments')}: {data.text_policy.max_chars} {t('字符，重叠', 'characters, overlap')} {data.text_policy.overlap}</p>
      </details>
    </>}
  </section>
}
