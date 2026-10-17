import { StrictMode, useEffect, useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Activity, BookOpen, CheckCircle2, ClipboardList, Database, ExternalLink, FileText, Languages, RefreshCw, Server, ShieldCheck, Sparkles, Wrench } from 'lucide-react'
import './styles.css'
import TranscriptStudio from './TranscriptStudio'
import ExtractionStudio from './ExtractionStudio'
import KnowledgeStudio from './KnowledgeStudio'

type Service = { id: string; label: string; port: number; path: string; description: string }
type ServiceState = Service & { status: 'checking' | 'online' | 'offline' }

const services: Service[] = [
  { id: 'support', label: 'Support Assistant', port: 8878, path: '/api/support/health', description: '低风险支持、安全流程和文本能力' },
  { id: 'simulation', label: 'Simulation Console', port: 8876, path: '/api/simulation/health', description: '情景训练、在线生成和课程任务' },
  { id: 'expert', label: 'Expert API', port: 8879, path: '/api/expert/health', description: '专家任务、回应审核和内容实验室' },
  { id: 'contribution', label: 'Contribution Hub', port: 8881, path: '/api/contribution/health', description: '候选治理、贡献和 release' },
  { id: 'extraction', label: 'Knowledge Extraction', port: 8891, path: '/api/extraction/health', description: '适配器抽取、候选 handoff 和进度' },
  { id: 'transcript', label: 'Transcript Pipeline', port: 8892, path: '/api/transcript/health', description: '对话规范化、分析和多 skill review' },
]

function App() {
  const [language, setLanguage] = useState<'zh' | 'en'>('zh')
  const [workspace, setWorkspace] = useState<'home' | 'transcript' | 'extraction' | 'knowledge'>('home')
  const [states, setStates] = useState<ServiceState[]>(services.map((service) => ({ ...service, status: 'checking' })))
  const [refreshing, setRefreshing] = useState(false)
  const copy = useMemo(() => language === 'zh' ? {
    eyebrow: 'TheraSkillAgent', title: '统一工作台', subtitle: '把技能、任务、治理和运行状态放到同一个入口。', refresh: '刷新服务', modules: '专业工作区', runtime: '运行状态', provider: 'Provider 配置', providerText: '模型 profile 由 configs/theraskill/providers.yaml 管理，凭据只从环境变量注入。', local: '本地 Qwen3.8-27B + BGE', remote: '可切换 OpenAI-compatible 远程 profile', tasks: '任务中心', taskText: '抽取 handoff 和课程批量生成都支持 durable job、进度和重试。', open: '进入工作区', online: '在线', offline: '离线', checking: '检查中', port: '端口', footer: '本地开发模式 · 不在工作台显示密钥',
  } : {
    eyebrow: 'TheraSkillAgent', title: 'Unified workbench', subtitle: 'One entry point for skills, jobs, governance, and runtime health.', refresh: 'Refresh services', modules: 'Workspaces', runtime: 'Runtime health', provider: 'Provider profiles', providerText: 'Profiles live in configs/theraskill/providers.yaml; credentials come from environment variables.', local: 'Local Qwen3.8-27B + BGE', remote: 'OpenAI-compatible remote profiles available', tasks: 'Task center', taskText: 'Extraction handoff and course batches support durable jobs, progress, and retry.', open: 'Open workspace', online: 'Online', offline: 'Offline', checking: 'Checking', port: 'Port', footer: 'Local development mode · secrets are never shown',
  }, [language])

  const refresh = async () => {
    setRefreshing(true)
    const next = await Promise.all(services.map(async (service) => {
      try { const response = await fetch(service.path); return { ...service, status: response.ok ? 'online' as const : 'offline' as const } } catch { return { ...service, status: 'offline' as const } }
    }))
    setStates(next); setRefreshing(false)
  }
  useEffect(() => { void refresh() }, [])

  if (workspace === 'transcript') return <div className="shell"><header className="topbar"><div className="brand"><div className="mark"><Activity size={18} /></div><div><strong>TheraSkillAgent</strong><span>{language === 'zh' ? '统一工作台' : 'Unified workbench'}</span></div></div><button className="language" onClick={() => setLanguage(language === 'zh' ? 'en' : 'zh')}><Languages size={16} /> {language === 'zh' ? 'EN' : '中文'}</button></header><main><TranscriptStudio locale={language} onBack={() => setWorkspace('home')} /></main></div>
  if (workspace === 'extraction') return <div className="shell"><header className="topbar"><div className="brand"><div className="mark"><Activity size={18} /></div><div><strong>TheraSkillAgent</strong><span>{language === 'zh' ? '统一工作台' : 'Unified workbench'}</span></div></div><button className="language" onClick={() => setLanguage(language === 'zh' ? 'en' : 'zh')}><Languages size={16} /> {language === 'zh' ? 'EN' : '中文'}</button></header><main><ExtractionStudio locale={language} onBack={() => setWorkspace('home')} /></main></div>
  if (workspace === 'knowledge') return <div className="shell"><header className="topbar"><div className="brand"><div className="mark"><Activity size={18} /></div><div><strong>TheraSkillAgent</strong><span>{language === 'zh' ? '统一工作台' : 'Unified workbench'}</span></div></div><button className="language" onClick={() => setLanguage(language === 'zh' ? 'en' : 'zh')}><Languages size={16} /> {language === 'zh' ? 'EN' : '中文'}</button></header><main><KnowledgeStudio locale={language} onBack={() => setWorkspace('home')} /></main></div>

  const focusedUrl = (path: string, port: number) => import.meta.env.DEV ? `${window.location.protocol}//${window.location.hostname}:${port}/` : path
  const openLinks = [
    { href: focusedUrl('/theraskill/support/', 5174), label: language === 'zh' ? '打开支持助手' : 'Open Support Assistant', icon: ShieldCheck },
    { href: focusedUrl('/theraskill/simulation/', 5173), label: language === 'zh' ? '打开情景控制台' : 'Open Simulation Console', icon: Sparkles },
    { href: focusedUrl('/theraskill/expert/', 5175), label: language === 'zh' ? '打开专家工作台' : 'Open Expert Studio', icon: BookOpen },
  ]
  return <div className="shell">
    <header className="topbar"><div className="brand"><div className="mark"><Activity size={18} /></div><div><strong>{copy.eyebrow}</strong><span>{copy.title}</span></div></div><button className="language" onClick={() => setLanguage(language === 'zh' ? 'en' : 'zh')}><Languages size={16} /> {language === 'zh' ? 'EN' : '中文'}</button></header>
    <main>
      <section className="hero"><div><p className="eyebrow">{copy.eyebrow}</p><h1>{copy.title}</h1><p>{copy.subtitle}</p></div><button className="refresh" onClick={() => void refresh()} disabled={refreshing}><RefreshCw size={16} className={refreshing ? 'spin' : ''} /> {copy.refresh}</button></section>
      <section className="section"><div className="section-heading"><h2><Server size={18} /> {copy.runtime}</h2><span>{states.filter((item) => item.status === 'online').length}/{states.length}</span></div><div className="service-grid">{states.map((service) => <article className="service-card" key={service.id}><div className="service-top"><span className={`dot ${service.status}`} /><strong>{service.label}</strong><span className="status">{service.status === 'online' ? copy.online : service.status === 'offline' ? copy.offline : copy.checking}</span></div><p>{service.description}</p><small>{copy.port} {service.port}</small></article>)}</div></section>
      <section className="columns"><article className="panel"><h2><Wrench size={18} /> {copy.provider}</h2><p>{copy.providerText}</p><div className="chips"><span>{copy.local}</span><span>{copy.remote}</span></div><code>theraskill-agent provider check --kind llm --live</code></article><article className="panel"><h2><ClipboardList size={18} /> {copy.tasks}</h2><p>{copy.taskText}</p><div className="task-row"><CheckCircle2 size={16} /> <span>Source Adapter → Candidate Governance</span></div><div className="task-row"><CheckCircle2 size={16} /> <span>Scenario Batch → Course Draft Review</span></div></article></section>
      <section className="section"><div className="section-heading"><h2><Sparkles size={18} /> {copy.modules}</h2></div><div className="link-grid"><button className="workspace-link" onClick={() => setWorkspace('knowledge')}><Database size={18} /><span>{language === 'zh' ? '知识与索引' : 'Knowledge & indexes'}</span><ExternalLink size={14} /></button>{openLinks.map(({ href, label, icon: Icon }) => <a key={href} href={href}><Icon size={18} /><span>{label}</span><ExternalLink size={14} /></a>)}<button className="workspace-link" onClick={() => setWorkspace('transcript')}><FileText size={18} /><span>{language === 'zh' ? '打开对话分析' : 'Open Transcript Studio'}</span><ExternalLink size={14} /></button><button className="workspace-link" onClick={() => setWorkspace('extraction')}><Database size={18} /><span>{language === 'zh' ? '打开知识抽取' : 'Open Extraction Studio'}</span><ExternalLink size={14} /></button></div></section>
    </main><footer>{copy.footer}</footer>
  </div>
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)
