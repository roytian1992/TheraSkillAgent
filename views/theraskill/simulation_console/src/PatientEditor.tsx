import { tr } from '../../shared/i18n'
import type { PatientDossier, PatientFact } from './types'

export const emptyPatient = (): PatientDossier => ({ display_name: '', age: '', gender: '', occupation: '', relationship_status: '', presenting_concern: '', background: [], recent_events: [], hidden_concerns: [], current_emotions: [], goals: [], training_objectives: [], communication_style: '', disclosure_preferences: '' })

export function PatientEditor({ value, onChange, prefix }: { value: PatientDossier; onChange: (value: PatientDossier) => void; prefix: string }) {
  const text = (field: keyof PatientDossier, label: string, multiline = false) => {
    const id = `${prefix}-${field}`
    return <label htmlFor={id} key={field}>{tr(label)}{multiline
      ? <textarea id={id} rows={2} maxLength={field === 'presenting_concern' ? 2000 : 1000} value={String(value[field])} onChange={e => onChange({ ...value, [field]: e.target.value })} />
      : <input id={id} maxLength={field === 'display_name' ? 100 : field === 'age' || field === 'gender' ? 80 : 200} value={String(value[field])} onChange={e => onChange({ ...value, [field]: e.target.value })} />}</label>
  }
  const lines = (field: 'current_emotions' | 'goals' | 'training_objectives', label: string) => <label>{tr(label)}<textarea aria-label={tr(label)} rows={2} value={value[field].join('\n')} onChange={e => onChange({ ...value, [field]: e.target.value.split('\n') })} /><small>{tr('每行一项')}</small></label>
  const sensitivity = (current: PatientFact['sensitivity'], change: (s: PatientFact['sensitivity']) => void, hidden = false) => <select aria-label={tr('披露层级')} value={current} onChange={e => change(e.target.value as PatientFact['sensitivity'])}>
    {!hidden && <option value="surface">{tr('开始即可透露')}</option>}<option value="moderate">{tr('建立信任后透露')}</option><option value="sensitive">{tr('深入探索后透露')}</option>
  </select>
  const facts = (field: 'background' | 'hidden_concerns', label: string) => <fieldset><legend>{tr(label)}</legend>
    {value[field].map((fact, index) => <div className="patient-fact" key={index}>
      <textarea aria-label={`${tr(label)} ${index + 1}`} rows={2} maxLength={2000} value={fact.content} onChange={e => onChange({ ...value, [field]: value[field].map((f, i) => i === index ? { ...f, content: e.target.value } : f) })} />
      {sensitivity(fact.sensitivity, s => onChange({ ...value, [field]: value[field].map((f, i) => i === index ? { ...f, sensitivity: s } : f) }), field === 'hidden_concerns')}
      <button type="button" className="secondary-button" onClick={() => onChange({ ...value, [field]: value[field].filter((_, i) => i !== index) })}>{tr('删除')}</button>
    </div>)}
    <button type="button" className="secondary-button" disabled={value[field].length >= 20} onClick={() => onChange({ ...value, [field]: [...value[field], { content: '', sensitivity: field === 'hidden_concerns' ? 'sensitive' : 'surface' }] })}>{tr('添加一项')}</button>
  </fieldset>
  return <div className="patient-editor">
    <p>{tr('身份和当前困扰供学员了解；背景与经历按披露层级逐步进入对话。')}</p>
    <div className="patient-grid">{text('display_name', '称呼')}{text('age', '年龄或年龄段')}{text('gender', '性别')}{text('occupation', '职业')}{text('relationship_status', '关系状态')}</div>
    {text('presenting_concern', '当前困扰（公开）', true)}
    {facts('background', '生活背景')}
    <fieldset><legend>{tr('近期经历')}</legend>
      <p>{tr('填写会谈之前发生的事、时间及影响。保存后将成为病例事实。')}</p>
      {value.recent_events.map((event, index) => <div className="patient-event" key={event.event_id}>
        <label>{tr('发生时间')}<input maxLength={100} value={event.time_label} placeholder={tr('例如：上周五、会谈前一天')} onChange={e => onChange({ ...value, recent_events: value.recent_events.map((v, i) => i === index ? { ...v, time_label: e.target.value } : v) })} /></label>
        <label>{tr('发生了什么')}<textarea rows={2} maxLength={2000} value={event.content} onChange={e => onChange({ ...value, recent_events: value.recent_events.map((v, i) => i === index ? { ...v, content: e.target.value } : v) })} /></label>
        <label>{tr('对患者的影响')}<textarea rows={2} maxLength={1000} value={event.impact} onChange={e => onChange({ ...value, recent_events: value.recent_events.map((v, i) => i === index ? { ...v, impact: e.target.value } : v) })} /></label>
        {sensitivity(event.sensitivity, s => onChange({ ...value, recent_events: value.recent_events.map((v, i) => i === index ? { ...v, sensitivity: s } : v) }))}
        <button type="button" className="secondary-button" onClick={() => onChange({ ...value, recent_events: value.recent_events.filter((_, i) => i !== index) })}>{tr('删除经历')}</button>
      </div>)}
      <button type="button" className="secondary-button" disabled={value.recent_events.length >= 20} onClick={() => onChange({ ...value, recent_events: [...value.recent_events, { event_id: `event_${crypto.randomUUID()}`, time_label: '', content: '', impact: '', sensitivity: 'moderate' }] })}>{tr('添加近期经历')}</button>
    </fieldset>
    {facts('hidden_concerns', '隐藏顾虑')}
    <div className="patient-grid">{lines('current_emotions', '当前情绪')}{lines('goals', '患者希望解决什么')}{lines('training_objectives', '训练目标')}</div>
    {text('communication_style', '表达习惯', true)}{text('disclosure_preferences', '披露偏好与阻抗', true)}
  </div>
}

export function cleanPatient(value: PatientDossier): PatientDossier {
  return { ...value, background: value.background.filter(v => v.content.trim()), recent_events: value.recent_events.filter(v => v.content.trim()), hidden_concerns: value.hidden_concerns.filter(v => v.content.trim()), current_emotions: value.current_emotions.filter(v => v.trim()), goals: value.goals.filter(v => v.trim()), training_objectives: value.training_objectives.filter(v => v.trim()) }
}
