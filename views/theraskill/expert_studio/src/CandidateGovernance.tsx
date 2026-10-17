import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, FileCheck2, GitCompareArrows, LoaderCircle, RefreshCw, RotateCcw, Send, Sparkles, Upload } from 'lucide-react'
import { adjudicateCandidate, candidateAction, listCandidates, listKnowledgeReleases, previewKnowledgeRelease, promoteKnowledgeRelease, rollbackKnowledgeRelease } from './api'

const TERMINAL = new Set(['promoted', 'rejected', 'quarantined'])

export default function CandidateGovernance({ identity }: { identity: any }) {
  const role = identity?.contribution_role || ''
  const [candidates, setCandidates] = useState<any[]>([])
  const [releases, setReleases] = useState<any[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [notes, setNotes] = useState('')
  const [adjudication, setAdjudication] = useState<Record<string, { decision: string; rationale: string }>>({})

  const canCurate = role === 'knowledge_curator' || role === 'admin'
  const canAdjudicate = role === 'adjudicator'
  const canRelease = role === 'release_manager' || role === 'admin'

  async function refresh() {
    setBusy('refresh'); setError('')
    try {
      const [candidateRows, releaseRows] = await Promise.all([
        role === 'release_manager' ? Promise.resolve([]) : listCandidates(),
        listKnowledgeReleases(),
      ])
      setCandidates(candidateRows); setReleases(releaseRows)
      setSelected((current) => current.filter((id) => candidateRows.some((row) => row.knowledge_candidate_id === id && row.status === 'validated')))
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy('') }
  }

  useEffect(() => { if (role) refresh() }, [role])

  async function act(id: string, action: 'structure' | 'dedup-check' | 'validate') {
    setBusy(id + action); setError(''); setNotice('')
    try { await candidateAction(id, action); setNotice('候选状态已更新'); await refresh() }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy('') }
  }

  async function decide(id: string) {
    const value = adjudication[id] || { decision: 'approve', rationale: '' }
    if (!value.rationale.trim()) return
    setBusy(id + 'adjudicate'); setError(''); setNotice('')
    try {
      await adjudicateCandidate(id, { ...value, conflict_of_interest: false })
      setNotice('独立审定已记录'); await refresh()
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy('') }
  }

  async function preview() {
    if (!selected.length) return
    setBusy('preview'); setError(''); setNotice('')
    try { await previewKnowledgeRelease(selected, notes); setSelected([]); setNotes(''); setNotice('Preview release 已创建'); await refresh() }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy('') }
  }

  async function promote(release: any) {
    setBusy(release.release_id + 'promote'); setError(''); setNotice('')
    try { await promoteKnowledgeRelease(release.release_id, release.manifest_sha256); setNotice('Release 已激活'); await refresh() }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy('') }
  }

  async function rollback(release: any) {
    setBusy(release.release_id + 'rollback'); setError(''); setNotice('')
    try { await rollbackKnowledgeRelease(release.release_id, 'Expert Studio release rollback'); setNotice('Release 已回滚'); await refresh() }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy('') }
  }

  function toggle(id: string) {
    setSelected((values) => values.includes(id) ? values.filter((value) => value !== id) : [...values, id])
  }

  return <section className="governance-page">
    <div className="queue-header"><div><h1>候选治理</h1><span>{role}</span></div><button className="icon-command" onClick={refresh} title="刷新"><RefreshCw /></button></div>
    {error && <div className="error"><AlertTriangle />{error}</div>}
    {notice && <div className="notice"><CheckCircle2 />{notice}</div>}
    {role !== 'release_manager' && <section className="governance-band">
      <div className="governance-band-head"><h2>Knowledge candidates</h2><span>{candidates.filter((row) => !TERMINAL.has(row.status)).length} active</span></div>
      <div className="candidate-table">
        {candidates.length === 0 ? <div className="governance-empty">{busy === 'refresh' ? <LoaderCircle className="spin" /> : '暂无候选'}</div> : candidates.map((candidate) => {
          const state = adjudication[candidate.knowledge_candidate_id] || { decision: 'approve', rationale: '' }
          return <article key={candidate.knowledge_candidate_id}>
            <label className="candidate-select"><input type="checkbox" checked={selected.includes(candidate.knowledge_candidate_id)} disabled={candidate.status !== 'validated'} onChange={() => toggle(candidate.knowledge_candidate_id)} /></label>
            <div className="candidate-main"><div><strong>{candidate.structured_candidate?.canonical_label || candidate.candidate?.knowledge_type || candidate.knowledge_candidate_id}</strong><span className={`candidate-status status-${candidate.status}`}>{candidate.status}</span></div><p>{candidate.structured_candidate?.summary || candidate.candidate?.claim_or_pattern || '待结构化'}</p><small>{candidate.structured_candidate?.dedup_family || 'unclassified'} · {candidate.dedup_result?.llm_relation || 'dedup pending'}</small></div>
            <div className="candidate-actions">
              {canCurate && candidate.status === 'expert_reviewed_candidate' && <button onClick={() => act(candidate.knowledge_candidate_id, 'structure')} disabled={!!busy}><Sparkles />结构化</button>}
              {canCurate && candidate.status === 'structured' && <button onClick={() => act(candidate.knowledge_candidate_id, 'dedup-check')} disabled={!!busy}><GitCompareArrows />去重检查</button>}
              {canCurate && ['dedup_checked','adjudicated'].includes(candidate.status) && <button onClick={() => act(candidate.knowledge_candidate_id, 'validate')} disabled={!!busy}><FileCheck2 />回归验证</button>}
              {canAdjudicate && candidate.status === 'needs_adjudication' && <div className="adjudication-row"><select value={state.decision} onChange={(event) => setAdjudication({ ...adjudication, [candidate.knowledge_candidate_id]: { ...state, decision: event.target.value } })}><option value="approve">approve</option><option value="reject">reject</option><option value="quarantine">quarantine</option></select><input value={state.rationale} onChange={(event) => setAdjudication({ ...adjudication, [candidate.knowledge_candidate_id]: { ...state, rationale: event.target.value } })} placeholder="审定依据" /><button onClick={() => decide(candidate.knowledge_candidate_id)} disabled={!state.rationale.trim() || !!busy} title="提交审定"><Send /></button></div>}
            </div>
          </article>
        })}
      </div>
      {canCurate && <div className="preview-bar"><span>{selected.length} selected</span><input value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="Release notes" /><button className="primary" onClick={preview} disabled={!selected.length || !!busy}><Upload />创建 preview</button></div>}
    </section>}
    <section className="governance-band releases-band">
      <div className="governance-band-head"><h2>Incremental releases</h2><span>{releases.length}</span></div>
      <div className="release-list">{releases.length === 0 ? <div className="governance-empty">暂无 release</div> : releases.slice().reverse().map((release) => <div key={release.release_id}><strong>{release.release_id}</strong><span className={`candidate-status status-${release.status}`}>{release.status}</span><small>{release.candidate_ids.length} candidates · {release.manifest_sha256?.slice(0, 12)}</small>{canRelease && release.status === 'preview' && <button onClick={() => promote(release)} disabled={!!busy}><Upload />激活</button>}{canRelease && release.status === 'promoted' && <button className="danger-command" onClick={() => rollback(release)} disabled={!!busy}><RotateCcw />回滚</button>}</div>)}</div>
    </section>
  </section>
}
