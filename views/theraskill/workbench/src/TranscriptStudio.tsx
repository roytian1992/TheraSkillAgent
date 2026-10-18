import { useEffect, useMemo, useState } from 'react'
import { FileText, LoaderCircle, Package, Play, Search, ShieldCheck } from 'lucide-react'

type Locale = 'zh' | 'en'
type Operation = 'inspect' | 'analyze' | 'review' | 'package'

function readDraft(): any { try { return JSON.parse(sessionStorage.getItem('theraskill_transcript_draft') || '{}') } catch { return {} } }

const skills = ['transcript-quality-reviewer', 'transcript-evaluator', 'risk-triage', 'therapy-response-reviewer']

export default function TranscriptStudio({ locale, onBack }: { locale: Locale; onBack: () => void }) {
  const [text, setText] = useState(() => readDraft().text ?? '患者：最近总是睡不好，嗯嗯。\n医师：听起来你很疲惫。\nPatient: I feel unsafe.')
  const [filename, setFilename] = useState(() => readDraft().filename || 'dialogue.txt')
  const [inputLanguage, setInputLanguage] = useState<'auto' | 'zh' | 'en' | 'mixed'>('auto')
  const [operation, setOperation] = useState<Operation>('inspect')
  const [mode, setMode] = useState<'plan' | 'model'>('plan')
  const [speakerRoles, setSpeakerRoles] = useState<Record<string, string>>(() => readDraft().speakerRoles || {})
  const [selectedSkills, setSelectedSkills] = useState<string[]>(skills.slice(0, 2))
  const [includeRaw, setIncludeRaw] = useState(false)
  const [result, setResult] = useState<Record<string, unknown> | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const copy = useMemo(() => locale === 'zh' ? {
    back: '返回工作台', title: '对话文件工作区', subtitle: '上传或粘贴患者/医师对话，保留原文后完成规范化、分析、multi-skill review 和打包。', file: '文件名', content: '对话内容', choose: '读取本地文件', action: '操作', inspect: '检查格式', analyze: '确定性分析', review: '多 skill 评估', package: '生成 bundle', mode: '评估模式', plan: '生成 review plan', model: '调用模型', skills: '选择 skills', raw: 'bundle 中包含原文', run: '执行', running: '处理中', output: '结果', noResult: '执行操作后，这里会显示可追溯结果。', evidence: '结果保留 turn_id 证据；风险信号仅为筛查。', modelWarning: '模型模式会把对话发送给当前配置的 LLM，请先确认脱敏和授权。', packageNote: '默认不附带原始文件全文；规范化轮次仍包含对话内容。', error: '执行失败', language: '输入语言',
  } : {
    back: 'Back to workbench', title: 'Transcript workspace', subtitle: 'Normalize, analyze, review and package patient/clinician dialogue without overwriting source evidence.', file: 'Filename', content: 'Transcript', choose: 'Read local file', action: 'Operation', inspect: 'Inspect', analyze: 'Analyze', review: 'Multi-skill review', package: 'Package bundle', mode: 'Review mode', plan: 'Review plan', model: 'Call model', skills: 'Skills', raw: 'Include raw text in bundle', run: 'Run', running: 'Running', output: 'Result', noResult: 'The traceable result will appear here.', evidence: 'Results retain turn_id evidence; risk signals are screening only.', modelWarning: 'Model mode sends the transcript to the selected provider. Confirm redaction and authorization first.', packageNote: 'The original file is excluded by default; normalized turns still contain dialogue text.', error: 'Failed', language: 'Input language'
  }, [locale])

  useEffect(() => { sessionStorage.setItem('theraskill_transcript_draft', JSON.stringify({ text, filename, speakerRoles })) }, [text, filename, speakerRoles])
  const turns = ((result?.document as any)?.turns || []) as any[]
  const reviews = ((result?.reviews as any)?.reviews || []) as any[]
  const speakers = [...new Map(turns.map(turn => [turn.speaker_label, turn])).values()]

  const run = async () => {
    if (!text.trim()) return
    setBusy(true); setError('')
    try {
      const response = await fetch('/api/transcript/v1/transcript/' + operation, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, filename, language: inputLanguage, output_language: locale, mode, speaker_role_overrides: speakerRoles, skills: selectedSkills, include_raw: includeRaw }),
      })
      const body = await response.text()
      let payload: any
      try { payload = JSON.parse(body) } catch { throw new Error(body || response.statusText) }
      if (!response.ok) throw new Error(Array.isArray(payload.detail) ? payload.detail.map((item: any) => item.loc.join('.') + ': ' + item.msg).join('; ') : typeof payload.detail === 'string' ? payload.detail : response.statusText)
      setResult(payload)
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
        <button className="primary large-action" onClick={() => void run()} disabled={busy || !text.trim()}>{busy ? <LoaderCircle className="spin" /> : <Play size={16} />}{busy ? copy.running : copy.run}</button>
        {error && <div className="error-box">{copy.error}: {error}</div>}
      </section>
      <section className="studio-result panel"><div className="result-heading"><h2>{copy.output}</h2>{result && <span>{String(result.operation || '')}</span>}</div>{result ? <><div className="result-summary">{copy.evidence}</div>{typeof result.download_url === 'string' && <a className="primary" href={'/api/transcript' + result.download_url}>{locale === 'zh' ? '下载完整 bundle（ZIP）' : 'Download bundle (ZIP)'}</a>}
        {speakers.length > 0 && <section><h3>{locale === 'zh' ? '确认说话人角色' : 'Confirm speaker roles'}</h3><p>{locale === 'zh' ? '修改后重新执行，原文保持不变。' : 'Run again after editing. The source text is preserved.'}</p>{speakers.map(turn => <label className="field-label" key={turn.speaker_label}>{turn.speaker_label}<select value={speakerRoles[turn.speaker_label] || turn.speaker_role} onChange={event => setSpeakerRoles(current => ({ ...current, [turn.speaker_label]: event.target.value }))}>{['unknown','patient','therapist','system'].map(role => <option key={role} value={role}>{locale === 'zh' ? (({unknown:'待确认',patient:'患者 / 来访者',therapist:'医师 / 咨询师',system:'系统'} as Record<string,string>)[role]) : role}</option>)}</select></label>)}</section>}
        {reviews.length > 0 && <section><h3>{locale === 'zh' ? '评估结果与证据' : 'Review findings and evidence'}</h3>{reviews.map((review, index) => <article className="resource-item" key={index}><strong>{review.skill} · {review.status}</strong><p>{review.overall}</p>{(review.findings || []).map((finding: any, i: number) => <div key={i}><small>{(finding.turn_ids || []).join(', ')}</small>{[['observed','原文观察','Observation'],['inference','模型判断','Model inference'],['recommendation','建议','Recommendation'],['uncertainty','不确定性','Uncertainty']].map(([key,zh,en]) => finding[key] && <p key={key}><b>{locale === 'zh' ? zh : en}：</b>{String(finding[key])}</p>)}</div>)}{review.limitations?.length > 0 && <p>{locale === 'zh' ? '局限：' : 'Limitations: '}{review.limitations.join('; ')}</p>}</article>)}</section>}
        {turns.length > 0 && <details open><summary>{locale === 'zh' ? '对话轮次与来源' : 'Turns and source evidence'}</summary>{turns.map(turn => <article className="resource-item" key={turn.turn_id}><strong>{turn.turn_id} · {turn.speaker_label} · {turn.timestamp || ''}</strong><blockquote style={{whiteSpace:'pre-wrap'}}>{turn.raw_text}</blockquote>{turn.raw_text !== turn.normalized_text && <p>{turn.normalized_text}</p>}</article>)}</details>}
        <details><summary>{locale === 'zh' ? '完整结构化结果' : 'Full structured result'}</summary><pre className="json-output">{JSON.stringify(result, null, 2)}</pre></details></> : <div className="empty-result"><FileText size={28} />{copy.noResult}</div>}</section>
    </div>
  </div>
}
