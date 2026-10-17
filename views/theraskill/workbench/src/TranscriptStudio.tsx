import { useMemo, useState } from 'react'
import { FileText, LoaderCircle, Package, Play, Search, ShieldCheck } from 'lucide-react'

type Locale = 'zh' | 'en'
type Operation = 'inspect' | 'analyze' | 'review' | 'package'

const skills = ['transcript-quality-reviewer', 'transcript-evaluator', 'risk-triage', 'therapy-response-reviewer']

export default function TranscriptStudio({ locale, onBack }: { locale: Locale; onBack: () => void }) {
  const [text, setText] = useState('患者：最近总是睡不好，嗯嗯。\n医师：听起来你很疲惫。\nPatient: I feel unsafe.')
  const [filename, setFilename] = useState('dialogue.txt')
  const [inputLanguage, setInputLanguage] = useState<'auto' | 'zh' | 'en' | 'mixed'>('auto')
  const [operation, setOperation] = useState<Operation>('inspect')
  const [mode, setMode] = useState<'plan' | 'model'>('plan')
  const [providerProfile, setProviderProfile] = useState('')
  const [selectedSkills, setSelectedSkills] = useState<string[]>(skills.slice(0, 2))
  const [includeRaw, setIncludeRaw] = useState(false)
  const [result, setResult] = useState<Record<string, unknown> | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const copy = useMemo(() => locale === 'zh' ? {
    back: '返回工作台', title: '对话文件工作区', subtitle: '上传或粘贴患者/医师对话，保留原文后完成规范化、分析、multi-skill review 和打包。', file: '文件名', content: '对话内容', choose: '读取本地文件', action: '操作', inspect: '检查格式', analyze: '确定性分析', review: '多 skill 评估', package: '生成 bundle', mode: '评估模式', plan: '生成 review plan', model: '调用模型', provider: 'Provider profile（模型模式必填）', skills: '选择 skills', raw: 'bundle 中包含原文', run: '执行', running: '处理中', output: '结果', noResult: '执行操作后，这里会显示可追溯结果。', evidence: '结果保留 turn_id 证据；风险信号仅为筛查。', modelWarning: '模型模式会把对话发送给选定 provider，请先确认脱敏和授权。', packageNote: '默认不把原文复制进 bundle，只保留哈希和规范化轮次。', error: '执行失败', language: '语言',
  } : {
    back: 'Back to workbench', title: 'Transcript workspace', subtitle: 'Normalize, analyze, review and package patient/clinician dialogue without overwriting source evidence.', file: 'Filename', content: 'Transcript', choose: 'Read local file', action: 'Operation', inspect: 'Inspect', analyze: 'Analyze', review: 'Multi-skill review', package: 'Package bundle', mode: 'Review mode', plan: 'Review plan', model: 'Call model', provider: 'Provider profile (required for model)', skills: 'Skills', raw: 'Include raw text in bundle', run: 'Run', running: 'Running', output: 'Result', noResult: 'The traceable result will appear here.', evidence: 'Results retain turn_id evidence; risk signals are screening only.', modelWarning: 'Model mode sends the transcript to the selected provider. Confirm redaction and authorization first.', packageNote: 'Raw text is excluded by default; hashes and normalized turns remain.', error: 'Failed', language: 'Language'
  }, [locale])

  const run = async () => {
    if (!text.trim()) return
    setBusy(true); setError('')
    try {
      const response = await fetch('/api/transcript/v1/transcript/' + operation, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, filename, language: inputLanguage, mode, provider_profile: providerProfile || null, skills: selectedSkills, include_raw: includeRaw }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(payload.detail || response.statusText)
      setResult(payload)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  const readFile = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    setFilename(file.name)
    setText(await file.text())
  }

  return <div className="workspace-page">
    <div className="workspace-toolbar"><button className="back-button" onClick={onBack}>← {copy.back}</button><span className="workspace-status"><FileText size={16} /> TheraSkill transcript pipeline</span></div>
    <div className="workspace-title"><div><p className="eyebrow">TheraSkillAgent</p><h1>{copy.title}</h1><p>{copy.subtitle}</p></div></div>
    <div className="studio-grid">
      <section className="studio-editor panel">
        <div className="studio-row"><label>{copy.file}<input value={filename} onChange={event => setFilename(event.target.value)} /></label><label>{copy.language}<select value={inputLanguage} onChange={event => setInputLanguage(event.target.value as 'auto' | 'zh' | 'en' | 'mixed')}><option value="auto">auto</option><option value="zh">中文</option><option value="en">English</option><option value="mixed">mixed</option></select></label></div>
        <label className="field-label">{copy.content}<textarea value={text} onChange={event => setText(event.target.value)} /></label>
        <label className="file-picker">{copy.choose}<input type="file" accept=".txt,.json,.jsonl,.srt,.vtt" onChange={readFile} /></label>
        <div className="operation-tabs">{(['inspect', 'analyze', 'review', 'package'] as Operation[]).map(item => <button key={item} className={operation === item ? 'active' : ''} onClick={() => setOperation(item)}>{item === 'inspect' ? <Search size={15} /> : item === 'analyze' ? <ShieldCheck size={15} /> : item === 'review' ? <Play size={15} /> : <Package size={15} />}{copy[item]}</button>)}</div>
        {(operation === 'review' || operation === 'package') && <div className="review-options"><label>{copy.mode}<select value={mode} onChange={event => setMode(event.target.value as 'plan' | 'model')}><option value="plan">{copy.plan}</option><option value="model">{copy.model}</option></select></label><label>{copy.provider}<input value={providerProfile} onChange={event => setProviderProfile(event.target.value)} placeholder="local_qwen" /></label><div><span className="field-label">{copy.skills}</span><div className="skill-checks">{skills.map(skill => <label key={skill}><input type="checkbox" checked={selectedSkills.includes(skill)} onChange={event => setSelectedSkills(current => event.target.checked ? [...current, skill] : current.filter(item => item !== skill))} />{skill}</label>)}</div></div></div>}
        {operation === 'package' && <label className="checkbox-line"><input type="checkbox" checked={includeRaw} onChange={event => setIncludeRaw(event.target.checked)} />{copy.raw}</label>}
        {mode === 'model' && (operation === 'review' || operation === 'package') && <p className="warning-note">{copy.modelWarning}</p>}
        {operation === 'package' && <p className="hint-note">{copy.packageNote}</p>}
        <button className="primary large-action" onClick={() => void run()} disabled={busy || !text.trim()}>{busy ? <LoaderCircle className="spin" /> : <Play size={16} />}{busy ? copy.running : copy.run}</button>
        {error && <div className="error-box">{copy.error}: {error}</div>}
      </section>
      <section className="studio-result panel"><div className="result-heading"><h2>{copy.output}</h2>{result && <span>{String(result.operation || '')}</span>}</div>{result ? <><div className="result-summary">{copy.evidence}</div><pre className="json-output">{JSON.stringify(result, null, 2)}</pre></> : <div className="empty-result"><FileText size={28} />{copy.noResult}</div>}</section>
    </div>
  </div>
}
