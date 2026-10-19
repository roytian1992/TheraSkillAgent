import { useState } from 'react'
import { tr } from '../../shared/i18n'
import type { CallerRole, Scenario, SessionLanguage } from './types'
import { buildPatient, forkPatient, inspectPatient, updateGeneratedScenario } from './api'
import type { PatientDesign } from './api'
import { cleanPatient, emptyPatient, PatientEditor } from './PatientEditor'

export function PatientDesigner({ scenario, role, onSaved }: { scenario: Scenario; role: CallerRole; onSaved: (scenario: Scenario) => void }) {
  const [design, setDesign] = useState<PatientDesign | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  async function run(action: 'inspect' | 'save' | 'fork') {
    setBusy(true); setError(''); setNotice('')
    try {
      if (action === 'inspect') setDesign(await inspectPatient(role, scenario.scenario_id))
      if (action === 'fork') { const result = await forkPatient(role, scenario.scenario_id); onSaved(result.scenario) }
      if (action === 'save' && design) {
        const result = await updateGeneratedScenario(role, scenario.scenario_id, { patient: cleanPatient(design.patient), expected_revision: design.revision })
        onSaved(result.scenario); setDesign(await inspectPatient(role, scenario.scenario_id))
        setNotice(tr('已保存新版本；已有会话继续使用原版本。'))
      }
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) } finally { setBusy(false) }
  }
  return <section className="detail-band patient-design-band">
    <h2>{tr('患者档案与经历')}</h2>
    <p>{tr('查看背景、近期经历和隐藏设定，或复制成一个新场景继续修改。')}</p>
    <div className="governance-actions"><button className="secondary-button" disabled={busy} onClick={() => void run('inspect')}>{tr('查看和编辑患者档案')}</button><button className="secondary-button" disabled={busy} onClick={() => void run('fork')}>{tr('复制为新场景')}</button></div>
    {error && <p role="alert" className="error-banner">{error}</p>}{notice && <p role="status">{notice}</p>}
    {design && <div>
      <p>{tr('版本')} {design.revision} · {tr('知识支撑')}：{design.grounding.status === 'grounded' ? tr('已检索相关知识') : tr('未获得检索支撑')} {design.grounding.reason}</p>
      {!!design.grounding.items?.length && <details><summary>{tr('查看知识来源')}</summary>{design.grounding.items.map(item => <p key={item.retrieval_id}>{item.retrieval_id}</p>)}</details>}
      {!scenario.generated && <p>{tr('已有资源病例请先复制，再编辑新场景。')}</p>}
      <fieldset disabled={!scenario.generated || busy} className="patient-dossier-fields"><PatientEditor value={design.patient} onChange={patient => setDesign({ ...design, patient })} prefix={`edit-${scenario.scenario_id}`} /></fieldset>
      {scenario.generated && <button className="primary-button" disabled={busy || !design.patient.presenting_concern.trim()} onClick={() => void run('save')}>{busy ? tr('正在保存…') : tr('保存患者新版本')}</button>}
    </div>}
  </section>
}

export function PatientBuilder({ role, language, onSaved }: { role: CallerRole; language: SessionLanguage; onSaved: (scenario: Scenario) => void }) {
  const [brief, setBrief] = useState('')
  const [patient, setPatient] = useState(emptyPatient)
  const [family, setFamily] = useState('supportive_exploration')
  const [difficulty, setDifficulty] = useState('medium')
  const [risk, setRisk] = useState('low')
  const [useKnowledge, setUseKnowledge] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function save(generate: boolean) {
    setBusy(true); setError('')
    try {
      const result = await buildPatient(role, { brief, patient: cleanPatient(patient), scenario_family: family, difficulty_band: difficulty, risk_band: risk, language, use_knowledge: useKnowledge }, generate)
      onSaved(result.scenario)
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) } finally { setBusy(false) }
  }
  return <section className="detail-band online-generation-band">
    <h2>{tr('构建模拟患者')}</h2><p>{tr('输入主题生成患者，或自行填写档案。生成会分阶段完成规划、档案、场景和独立检查；保存后可反复练习、编辑及复制，结果仍需教师审核。')}</p>
    <label>{tr('训练主题（公开）')}<textarea aria-label={tr('训练主题（公开）')} rows={3} maxLength={1200} value={brief} onChange={e => setBrief(e.target.value)} placeholder={tr('例如：来访者因为工作绩效下降而持续自责，但暂时不愿讨论家庭关系')} /></label>
    <div className="patient-grid">
      <label>{tr('训练类型')}<select value={family} onChange={e => setFamily(e.target.value)}>{Object.entries({ supportive_exploration: '支持性探索', cognitive_reframing_probe: '认知重评', rupture_and_repair: '关系修复', risk_escalation_and_handoff: '风险与转介', rebt_belief_work: 'REBT 信念工作' }).map(([key, title]) => <option key={key} value={key}>{tr(title)}</option>)}</select></label>
      <label>{tr('难度')}<select value={difficulty} onChange={e => setDifficulty(e.target.value)}><option value="low">{tr('低')}</option><option value="medium">{tr('中')}</option><option value="high">{tr('高')}</option></select></label>
      <label>{tr('风险')}<select value={risk} onChange={e => setRisk(e.target.value)}><option value="low">{tr('低')}</option><option value="elevated">{tr('需关注')}</option><option value="high">{tr('高')}</option></select></label>
    </div>
    <details><summary>{tr('补充人设、背景和近期经历（可选）')}</summary><PatientEditor value={patient} onChange={setPatient} prefix="build" /></details>
    <label className="patient-grounding"><input type="checkbox" checked={useKnowledge} onChange={e => setUseKnowledge(e.target.checked)} />{tr('生成时检索相关构建知识')}</label>
    {error && <p role="alert" className="error-banner">{error}</p>}
    <div className="governance-actions"><button className="primary-button" disabled={busy || brief.trim().length < 8} onClick={() => void save(true)}>{busy ? tr('正在构建…') : tr('生成并保存患者草稿')}</button><button className="secondary-button" disabled={busy || brief.trim().length < 8 || !patient.presenting_concern.trim()} onClick={() => void save(false)}>{tr('直接保存填写的患者')}</button></div>
  </section>
}
