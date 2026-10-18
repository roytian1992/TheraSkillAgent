export async function api(path: string, body?: any, headers: Record<string,string> = {}) {
  const response = await fetch(path, body === undefined ? {headers} : {method:'POST',headers:{'Content-Type':'application/json',...headers},body:JSON.stringify(body)})
  const value = await response.json()
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : JSON.stringify(value.detail || value))
  return value
}
export const statusLabel = (value: string, locale: 'zh'|'en') => locale === 'en' ? value.replaceAll('_',' ') : ({queued:'排队中',running:'执行中',pending:'等待子任务',needs_input:'等待输入',needs_approval:'等待确认',interrupted:'已中断，可恢复',failed:'失败，可重试',completed:'已完成',cancelled:'已取消',approved:'已批准',rejected:'已退回',blocked:'未通过门禁',passed:'通过门禁',complete:'已完成',active:'进行中',terminated:'已终止',not_started:'未开始',not_evaluable:'不可评估',soft_warning:'需关注',pass:'通过',fail:'未通过',needs_practice:'建议继续练习',draft:'草稿'}[value] || value)
