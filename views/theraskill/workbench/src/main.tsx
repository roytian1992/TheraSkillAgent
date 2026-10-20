import { StrictMode, useEffect, useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Activity, BookOpen, ClipboardList, Database, ExternalLink, FileText, Languages, RefreshCw, Server, ShieldCheck, Sparkles, Wrench } from 'lucide-react'
import './styles.css'
import TranscriptStudio from './TranscriptStudio'
import ExtractionStudio from './ExtractionStudio'
import KnowledgeStudio from './KnowledgeStudio'
import KnowledgeUnitEditor from './KnowledgeUnitEditor'
import TaskCenter from './TaskCenter'
import WorkflowStudio from './WorkflowStudio'
import EvaluationStudio from './EvaluationStudio'
import LearningStudio from './LearningStudio'
import SetupChecklist from './SetupChecklist'
import ProviderSettings from './ProviderSettings'

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
  const [language, setLanguage] = useState<'zh' | 'en'>(() => localStorage.getItem('theraskill-locale') === 'en' ? 'en' : 'zh')
  const [workspace, updateWorkspace] = useState<'home' | 'tasks' | 'settings' | 'transcript' | 'extraction' | 'knowledge' | 'workflow' | 'evaluation' | 'learning'>(() => {
    const value = new URLSearchParams(location.search).get('workspace')
    return ['tasks', 'settings', 'transcript', 'extraction', 'knowledge', 'workflow', 'evaluation', 'learning'].includes(value || '') ? value as 'tasks' | 'settings' | 'transcript' | 'extraction' | 'knowledge' | 'workflow' | 'evaluation' | 'learning' : 'home'
  })
  const [providerInfo, setProviderInfo] = useState<any>(null)
  const [, refreshRoute] = useState(0)
  const [knowledgeDirty, setKnowledgeDirty] = useState(false)
  function setWorkspace(value: 'home' | 'tasks' | 'settings' | 'transcript' | 'extraction' | 'knowledge' | 'workflow' | 'evaluation' | 'learning') {
    if (knowledgeDirty && !window.confirm(language === 'zh' ? '知识草稿还有未保存的修改。放弃修改并离开？' : 'This knowledge draft has unsaved changes. Discard them and leave?')) return
    const url = new URL(location.href)
    if (value === 'home') url.searchParams.delete('workspace'); else url.searchParams.set('workspace', value)
    url.searchParams.delete('job_id'); url.searchParams.delete('task_id'); url.searchParams.delete('report_id'); url.searchParams.delete('knowledge_id'); url.searchParams.delete('index_id'); url.searchParams.delete('knowledge_sha256'); url.searchParams.delete('index')
    window.history.pushState({}, '', url); updateWorkspace(value); setKnowledgeDirty(false); refreshRoute(version => version + 1)
  }
  useEffect(() => { localStorage.setItem('theraskill-locale', language) }, [language])
  useEffect(() => {
    const restore = () => { refreshRoute(version => version + 1); const value = new URLSearchParams(location.search).get('workspace'); updateWorkspace(['tasks','settings','transcript','extraction','knowledge','workflow','evaluation','learning'].includes(value || '') ? value as any : 'home') }
    window.addEventListener('popstate', restore)
    fetch('/api/resources/providers').then(response => response.ok ? response.json() : null).then(setProviderInfo).catch(() => {})
    return () => window.removeEventListener('popstate', restore)
  }, [])
  const [states, setStates] = useState<ServiceState[]>(services.map((service) => ({ ...service, status: 'checking' })))
  const [refreshing, setRefreshing] = useState(false)
  const copy = useMemo(() => language === 'zh' ? {
    eyebrow: 'TheraSkillAgent', title: '统一工作台', subtitle: '把技能、任务、治理和运行状态放到同一个入口。', refresh: '刷新服务', modules: '专业工作区', runtime: '运行状态', provider: '模型配置', providerText: '在 configs/theraskill/providers.yaml 分别配置模型与连接；providers.local.yaml 可覆盖本机设置。', local: '独立配置 LLM 与 embedding', remote: '兼容本地服务和 OpenAI-compatible API', tasks: '任务与历史', taskText: '跟进工作区发起的执行，补充信息，或重新打开结果。', open: '进入工作区', online: '在线', offline: '离线', checking: '检查中', port: '端口', footer: '本地开发模式 · 不在工作台显示密钥',
  } : {
    eyebrow: 'TheraSkillAgent', title: 'Unified workbench', subtitle: 'One entry point for skills, jobs, governance, and runtime health.', refresh: 'Refresh services', modules: 'Workspaces', runtime: 'Runtime health', provider: 'Model configuration', providerText: 'Configure LLM and embedding separately in providers.yaml; providers.local.yaml overrides local settings.', local: 'Independent LLM and embedding settings', remote: 'Local and OpenAI-compatible API services', tasks: 'Tasks & history', taskText: 'Track runs started in a workspace, provide input, and reopen results.', open: 'Open workspace', online: 'Online', offline: 'Offline', checking: 'Checking', port: 'Port', footer: 'Local development mode · secrets are never shown',
  }, [language])

  const refresh = async () => {
    setRefreshing(true)
    const next = await Promise.all(services.map(async (service) => {
      try { const response = await fetch(service.path); return { ...service, status: response.ok ? 'online' as const : 'offline' as const } } catch { return { ...service, status: 'offline' as const } }
    }))
    setStates(next); setRefreshing(false)
  }
  useEffect(() => { void refresh() }, [])

  const nav = <nav className="main-nav" aria-label={language === 'zh' ? '主导航' : 'Main navigation'}><button className={workspace === 'home' ? 'active' : ''} onClick={() => setWorkspace('home')}><Sparkles size={15} /> {language === 'zh' ? '工作台' : 'Workspace'}</button><button className={workspace === 'tasks' ? 'active' : ''} onClick={() => setWorkspace('tasks')}><ClipboardList size={15} /> {language === 'zh' ? '任务与历史' : 'Tasks & history'}</button><button className={workspace === 'settings' ? 'active' : ''} onClick={() => setWorkspace('settings')}><Wrench size={15} /> {language === 'zh' ? '模型与连接' : 'Models & connections'}</button></nav>
  const topbar = <header className="topbar"><a className="brand" href="/"><div className="mark"><Activity size={18} /></div><div><strong>TheraSkillAgent</strong><span>{language === 'zh' ? '统一工作台' : 'Unified workbench'}</span></div></a>{nav}<button className="language" onClick={() => setLanguage(language === 'zh' ? 'en' : 'zh')}><Languages size={16} /> {language === 'zh' ? 'EN' : '中文'}</button></header>
  if (workspace === 'workflow' || workspace === 'evaluation' || workspace === 'learning') return <div className="shell">{topbar}<main>{workspace === 'workflow' ? <WorkflowStudio locale={language} onBack={() => setWorkspace('home')} /> : workspace === 'evaluation' ? <EvaluationStudio locale={language} onBack={() => setWorkspace('home')} /> : <LearningStudio locale={language} onBack={() => setWorkspace('home')} />}</main></div>
  if (workspace === 'tasks') return <div className="shell">{topbar}<main><section className="workspace-page"><div className="workspace-title"><p className="eyebrow">{language === 'zh' ? '执行进度与记录' : 'PROGRESS & RECORDS'}</p><h1>{language === 'zh' ? '任务与历史' : 'Tasks & history'}</h1><p>{language === 'zh' ? '在工作区开始工作，在这里跟进进度、补充信息和重新打开结果。已结束的执行保存在历史中。' : 'Start work in a workspace, then track progress, provide input, and reopen results here. Finished runs are saved in History.'}</p></div><article className="panel task-center-page"><TaskCenter locale={language} compact={false} /></article></section></main></div>
  if (workspace === 'settings') return <div className="shell">{topbar}<main><ProviderSettings locale={language} onBack={() => setWorkspace('home')} /></main></div>
  if (workspace === 'transcript') return <div className="shell">{topbar}<main><TranscriptStudio locale={language} onBack={() => setWorkspace('home')} /></main></div>
  if (workspace === 'extraction') return <div className="shell">{topbar}<main><ExtractionStudio locale={language} onBack={() => setWorkspace('home')} /></main></div>
  if (workspace === 'knowledge') { const params = new URLSearchParams(location.search); const knowledgeId = params.get('knowledge_id'); const knowledgeIndexId = params.get('index_id'); return <div className="shell">{topbar}<main>{knowledgeId ? <KnowledgeUnitEditor id={knowledgeId} indexId={knowledgeIndexId || undefined} checksum={params.get('knowledge_sha256') || undefined} locale={language} onDirtyChange={setKnowledgeDirty} onBack={() => setWorkspace('knowledge')} /> : <KnowledgeStudio locale={language} onBack={() => setWorkspace('home')} />}</main></div> }

  const focusedUrl = (path: string, port: number) => import.meta.env.DEV ? `${window.location.protocol}//${window.location.hostname}:${port}/` : path
  const openLinks = [
    { href: focusedUrl('/theraskill/support/', 5174), label: language === 'zh' ? '打开支持助手' : 'Open Support Assistant', icon: ShieldCheck },
    { href: focusedUrl('/theraskill/simulation/', 5173), label: language === 'zh' ? '打开情景控制台' : 'Open Simulation Console', icon: Sparkles },
    { href: focusedUrl('/theraskill/expert/', 5175), label: language === 'zh' ? '打开专家工作台' : 'Open Expert Studio', icon: BookOpen },
  ]
  return <div className="shell">
    {topbar}
    <main className="home-main">
      <section className="hero"><div><p className="eyebrow">{copy.eyebrow}</p><h1>{copy.title}</h1><p>{copy.subtitle}</p></div><div className="hero-actions"><button className="primary hero-primary" onClick={() => setWorkspace('workflow')}><Wrench size={16} /> {language === 'zh' ? '开始一次任务' : 'Start a task'}</button><button className="refresh" onClick={() => void refresh()} disabled={refreshing}><RefreshCw size={16} className={refreshing ? 'spin' : ''} /> {copy.refresh}</button></div></section>
      <section className="section workspace-section"><div className="section-heading"><div><h2><Sparkles size={18} /> {copy.modules}</h2><p>{language === 'zh' ? '从这里进入抽取、分析、训练、评估和知识管理。' : 'Start extraction, analysis, training, evaluation, or knowledge management here.'}</p></div></div><div className="link-grid"><button className="workspace-link" onClick={() => setWorkspace('learning')}><BookOpen size={18} /><span>{language === 'zh' ? '课程与学习进展' : 'Courses and learning progress'}</span></button><button className="workspace-link" onClick={() => setWorkspace('workflow')}><Wrench size={18} /><span>{language === 'zh' ? 'Agent 任务流' : 'Agent task flows'}</span></button><button className="workspace-link" onClick={() => setWorkspace('evaluation')}><ClipboardList size={18} /><span>{language === 'zh' ? '效果回归与门禁' : 'Behavioral evaluations'}</span></button><button className="workspace-link" onClick={() => setWorkspace('knowledge')}><Database size={18} /><span>{language === 'zh' ? '知识与索引' : 'Knowledge & indexes'}</span><ExternalLink size={14} /></button>{openLinks.map(({ href, label, icon: Icon }) => <a key={href} href={href}><Icon size={18} /><span>{label}</span><ExternalLink size={14} /></a>)}<button className="workspace-link" onClick={() => setWorkspace('transcript')}><FileText size={18} /><span>{language === 'zh' ? '打开对话分析' : 'Open Transcript Studio'}</span><ExternalLink size={14} /></button><button className="workspace-link" onClick={() => setWorkspace('extraction')}><Database size={18} /><span>{language === 'zh' ? '打开知识抽取' : 'Open Extraction Studio'}</span><ExternalLink size={14} /></button></div></section>
      <SetupChecklist locale={language}/><section className="section runtime-section"><div className="section-heading"><div><h2><Server size={18} /> {copy.runtime}</h2><p>{language === 'zh' ? '当前应用模块运行在同一个 TheraSkillAgent 进程中。' : 'Application modules run in the same TheraSkillAgent process.'}</p></div><span>{states.filter((item) => item.status === 'online').length}/{states.length}</span></div><div className="service-grid">{states.map((service) => <article className="service-card" key={service.id}><div className="service-top"><span className={`dot ${service.status}`} /><strong>{service.label}</strong><span className="status">{service.status === 'online' ? copy.online : service.status === 'offline' ? copy.offline : copy.checking}</span></div><p>{language === 'zh' ? service.description : ({support:'Emotional support and safety follow-up',simulation:'Practice scenarios, generation and courses',expert:'Response review and teaching content',contribution:'Candidate governance and knowledge releases',extraction:'Extraction plans, jobs and candidate handoff',transcript:'Dialogue normalization, analysis and skill reviews'}[service.id])}</p><small>{language==='zh'?'应用内模块 · 共用当前端口':'In-process module · shared app port'}</small></article>)}</div></section>
      <section className="columns"><article className="panel"><h2><Wrench size={18} /> {copy.provider}</h2><p>{copy.providerText}</p><div className="chips"><span>{copy.local}</span><span>{copy.remote}</span></div>{providerInfo && <div><p>LLM: <strong>{providerInfo.llm?.model}</strong><br />{providerInfo.llm?.base_url}</p><p>Embedding: <strong>{providerInfo.embedding?.model}</strong><br />{providerInfo.embedding?.base_url || providerInfo.embedding?.endpoint}</p><details><summary>{language === 'zh' ? '生效配置文件' : 'Effective configuration files'}</summary>{providerInfo.files?.map((path: string) => <p key={path}>{path}</p>)}</details></div>}<code>./theraskill-agent provider check --kind llm --live</code><div className="panel-actions"><button className="secondary-action" onClick={() => setWorkspace('settings')}>{language === 'zh' ? '打开模型与连接设置' : 'Open model settings'} →</button></div></article><article className="panel"><h2><ClipboardList size={18} /> {copy.tasks}</h2><p>{copy.taskText}</p><TaskCenter locale={language} /></article></section>

    </main><footer>{copy.footer}</footer>
  </div>
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)
