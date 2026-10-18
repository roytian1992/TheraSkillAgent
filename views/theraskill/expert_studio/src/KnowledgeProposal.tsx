import { tr, useTranslationSubscription } from '../../shared/i18n'
import { useState } from 'react'
import { CheckCircle2, LoaderCircle, Send } from 'lucide-react'
import { createKnowledgeProposal } from './api'

export default function KnowledgeProposal() {
  useTranslationSubscription()

  const [form, setForm] = useState({
    knowledge_type: 'interaction_pattern', claim_or_pattern: '', applicable_context: '',
    contraindications: '', risk_level: 'low', evidence_source: '', source_evidence_tier: 'expert_authored',
    expert_confidence: 3, license_use_restrictions: '',
  })
  const [evidence, setEvidence] = useState('[]')
  const [regression, setRegression] = useState('[]')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [created, setCreated] = useState<any>(null)
  function change(key: string, value: string | number) { setForm({...form, [key]: value}) }
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setCreated(null)
    try {
      setCreated(await createKnowledgeProposal({...form, evidence_spans: JSON.parse(evidence), suggested_regression_cases: JSON.parse(regression)}))
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy(false) }
  }
  return <section className="knowledge-page">
    <div className="knowledge-head"><h1>{tr("专家知识贡献")}</h1>{created && <span><CheckCircle2 />{tr("已进入 candidate review")}</span>}</div>
    {error && <div className="error">{error}</div>}
    <form onSubmit={submit}>
      <div className="knowledge-grid">
        <label>{tr("Knowledge type")}<input value={form.knowledge_type} onChange={(event) => change('knowledge_type', event.target.value)} required /></label>
        <label>{tr("Risk level")}<select value={form.risk_level} onChange={(event) => change('risk_level', event.target.value)}><option value="low">{tr("low")}</option><option value="medium">{tr("medium")}</option><option value="high">{tr("high")}</option></select></label>
        <label className="wide">{tr("Claim or pattern")}<textarea value={form.claim_or_pattern} onChange={(event) => change('claim_or_pattern', event.target.value)} required /></label>
        <label className="wide">{tr("Applicable context")}<textarea value={form.applicable_context} onChange={(event) => change('applicable_context', event.target.value)} required /></label>
        <label className="wide">{tr("Contraindications")}<textarea value={form.contraindications} onChange={(event) => change('contraindications', event.target.value)} /></label>
        <label>{tr("Evidence source")}<input value={form.evidence_source} onChange={(event) => change('evidence_source', event.target.value)} required /></label>
        <label>{tr("Source / evidence tier")}<input value={form.source_evidence_tier} onChange={(event) => change('source_evidence_tier', event.target.value)} required /></label>
        <label>{tr("Expert confidence")}<select value={form.expert_confidence} onChange={(event) => change('expert_confidence', Number(event.target.value))}>{[1,2,3,4,5].map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>{tr("License / use restrictions")}<input value={form.license_use_restrictions} onChange={(event) => change('license_use_restrictions', event.target.value)} /></label>
        <label className="wide">{tr("Evidence spans JSON")}<textarea className="mono" value={evidence} onChange={(event) => setEvidence(event.target.value)} required /></label>
        <label className="wide">{tr("Suggested regression cases JSON")}<textarea className="mono" value={regression} onChange={(event) => setRegression(event.target.value)} required /></label>
      </div>
      <button className="primary" disabled={busy || !form.claim_or_pattern || !form.applicable_context || !form.evidence_source}>{busy ? <LoaderCircle className="spin" /> : <Send />}{tr("提交贡献")}</button>
    </form>
    {created && <div className="candidate-receipt"><strong>{created.contribution?.contribution_id}</strong><span>{tr("不会直接写入生产 knowledge")}</span></div>}
  </section>
}
