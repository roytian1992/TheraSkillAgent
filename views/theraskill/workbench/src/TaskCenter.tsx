import { useEffect, useState } from 'react'

type Task = { id: string; kind: string; title: string; status: string; created: string; href: string }
export default function TaskCenter({ locale, compact = true }: { locale: 'zh' | 'en'; compact?: boolean }) {
  const statusLabel = (status: string) => locale === 'zh' ? (({queued:'排队中',pending:'处理中',running:'运行中',completed:'已完成',complete:'已完成',idle:'已完成',failed:'失败，可重试',partial:'部分完成',unavailable:'暂时无法读取状态',needs_input:'等待输入',needs_approval:'等待确认',interrupted:'已中断，可恢复',cancelled:'已取消'} as Record<string,string>)[status] || status) : (status === 'idle' ? 'completed' : status.replaceAll('_',' '))
  const [tasks, setTasks] = useState<Task[]>([])
  const [errors, setErrors] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  async function refresh() {
    setBusy(true)
    const sources: Array<{url: string; headers: Record<string, string>; kind: string; map: (job: any) => Task}> = [
      { url:'/api/tasks',headers:{},kind:locale==='zh'?'技能工作流':'Skill workflow',map:(j:any):Task=>({id:j.id,kind:locale==='zh'?'技能工作流':'Skill workflow',title:j.goal,status:j.status,created:j.created_at,href:'/?workspace=workflow&task_id='+j.id}) },
      { url: '/api/extraction/v1/extraction/jobs', headers: { 'X-Extraction-Role': 'researcher' }, kind: 'Extraction', map: (j: any): Task => ({ id: j.job_id, kind: 'Extraction', title: j.plan?.goal || j.job_id, status: j.status, created: j.created_at, href: '/?workspace=extraction&job_id=' + j.job_id }) },
      { url: '/api/expert/jobs', headers: { 'X-Expert-Role': 'expert', 'X-Expert-User': 'expert-local' }, kind: 'Expert', map: (j: any): Task => ({ id: j.job_id, kind: 'Expert', title: j.workflow === 'response_review' ? (locale === 'zh' ? '回应审核' : 'Response review') : (locale === 'zh' ? '内容构建' : 'Content generation'), status: j.status || 'unavailable', created: j.created_at, href: '/theraskill/expert/?job_id=' + j.job_id }) },
      { url: '/api/simulation/scenarios/generate-batch/jobs', headers: { 'X-Simulation-Role': 'researcher' }, kind: 'Scenario batch', map: (j: any): Task => ({ id: j.job_id, kind: 'Scenario batch', title: j.request?.course_title || j.job_id, status: j.status, created: j.created_at, href: '/theraskill/simulation/?batch_job=' + j.job_id }) },
    ]
    const results = await Promise.allSettled(sources.map(async source => {
      const response = await fetch(source.url, { headers: source.headers })
      if (!response.ok) throw new Error(source.kind + ': ' + response.status)
      const data = await response.json(); return (data.tasks || data.jobs || []).map(source.map)
    }))
    setTasks(results.flatMap(result => result.status === 'fulfilled' ? result.value : []).sort((a, b) => b.created.localeCompare(a.created)))
    setErrors(results.flatMap(result => result.status === 'rejected' ? [String(result.reason)] : []))
    setBusy(false)
  }
  useEffect(() => { void refresh() }, [locale])
  return <><button onClick={() => void refresh()} disabled={busy}>{locale === 'zh' ? '刷新任务' : 'Refresh tasks'}</button>{errors.map(error => <p key={error} role="alert">{error}</p>)}<div className="task-history">{tasks.slice(0, compact ? 4 : 1000).map(task => <a className="resource-item" key={task.id} href={task.href}><strong>{task.title}</strong><span>{task.kind} · {statusLabel(task.status)} · {new Date(task.created).toLocaleString()}</span></a>)}{tasks.length === 0 && !busy && <p>{locale === 'zh' ? '暂无保存的任务。开始一次抽取、审核或课程场景生成后，可在这里继续。' : 'No saved tasks. Start extraction, a review or a course batch, then return here to continue.'}</p>}</div></>
}
