import { useEffect, useState } from 'react'
import { Check, LoaderCircle, Pencil, Plus, Trash2, X } from 'lucide-react'
import { approveMemory, deleteMemory, listMemories, proposeMemory, updateMemory } from './api'
import type { DomainMemory } from './types'

const types = [
  ['communication_preference', '沟通偏好'], ['user_confirmed_goal', '已确认目标'],
  ['user_confirmed_context', '已确认背景'], ['coping_preference', '应对偏好'],
  ['explicit_boundary', '明确边界'], ['do_not_remember', '不要记住'],
]

export default function MemoryDrawer({onClose,onError}:{onClose:()=>void;onError:(value:string)=>void}) {
  const [items,setItems]=useState<DomainMemory[]>([])
  const [loading,setLoading]=useState(true)
  const [type,setType]=useState('communication_preference')
  const [content,setContent]=useState('')
  const [sensitive,setSensitive]=useState(false)
  const [ttl,setTtl]=useState(90)
  const [editing,setEditing]=useState('')
  const [editText,setEditText]=useState('')
  const [busy,setBusy]=useState(false)
  async function refresh(){setLoading(true);try{setItems(await listMemories())}catch(error){onError(error instanceof Error?error.message:String(error))}finally{setLoading(false)}}
  useEffect(()=>{void refresh()},[])
  async function propose(){if(!content.trim())return;setBusy(true);try{const value=await proposeMemory({memory_type:type,content:content.trim(),sensitivity_level:sensitive?'sensitive':'standard',ttl_days:ttl});setItems(current=>[value,...current]);setContent('')}catch(error){onError(error instanceof Error?error.message:String(error))}finally{setBusy(false)}}
  async function approve(item:DomainMemory){try{const value=await approveMemory(item.memory_id,ttl);setItems(current=>current.map(row=>row.memory_id===value.memory_id?value:row))}catch(error){onError(error instanceof Error?error.message:String(error))}}
  async function save(item:DomainMemory){if(!editText.trim())return;try{const value=await updateMemory(item.memory_id,{content:editText.trim()});setItems(current=>current.map(row=>row.memory_id===value.memory_id?value:row));setEditing('')}catch(error){onError(error instanceof Error?error.message:String(error))}}
  async function remove(item:DomainMemory){try{const value=await deleteMemory(item.memory_id);setItems(current=>current.map(row=>row.memory_id===value.memory_id?value:row))}catch(error){onError(error instanceof Error?error.message:String(error))}}
  return <><button className="drawer-scrim" onClick={onClose} aria-label="关闭我的记忆"/><aside className="memory-drawer">
    <header><h2>我的记忆</h2><button className="icon-button" onClick={onClose} title="关闭"><X size={17}/></button></header>
    <div className="memory-create">
      <div className="memory-fields"><select value={type} onChange={event=>setType(event.target.value)}>{types.map(([value,label])=><option value={value} key={value}>{label}</option>)}</select><label>有效天数<input type="number" min="1" max="365" value={ttl} onChange={event=>setTtl(Number(event.target.value))}/></label></div>
      <textarea value={content} onChange={event=>setContent(event.target.value)} maxLength={2000} placeholder="填写需要跨对话保留的内容"/>
      <label className="sensitive-toggle"><input type="checkbox" checked={sensitive} onChange={event=>setSensitive(event.target.checked)}/>敏感内容</label>
      <button className="memory-primary" onClick={propose} disabled={busy||!content.trim()}>{busy?<LoaderCircle className="spin" size={14}/>:<Plus size={14}/>}创建待确认项</button>
    </div>
    {loading?<div className="drawer-loading"><LoaderCircle className="spin" size={18}/></div>:<div className="memory-list">{items.filter(item=>item.confirmation_status!=='deleted').map(item=><article key={item.memory_id}>
      <div className="memory-meta"><strong>{types.find(([value])=>value===item.memory_type)?.[1]||item.memory_type}</strong><span>{item.confirmation_status==='active'?'已启用':item.confirmation_status==='proposed'?'待确认':item.confirmation_status}</span></div>
      {editing===item.memory_id?<textarea value={editText} onChange={event=>setEditText(event.target.value)}/>:<p>{item.content}</p>}
      <small>{item.sensitivity_level==='sensitive'?'敏感 · ':''}有效至 {new Date(item.expires_at).toLocaleDateString()}</small>
      <div className="memory-actions">{item.confirmation_status==='proposed'&&<button onClick={()=>approve(item)}><Check size={14}/>确认启用</button>}{item.confirmation_status==='active'&&(editing===item.memory_id?<button onClick={()=>save(item)}><Check size={14}/>保存</button>:<button onClick={()=>{setEditing(item.memory_id);setEditText(item.content)}}><Pencil size={14}/>编辑</button>)}<button className="danger" onClick={()=>remove(item)}><Trash2 size={14}/>删除</button></div>
    </article>)}{!items.some(item=>item.confirmation_status!=='deleted')&&<div className="drawer-empty">暂无记忆</div>}</div>}
  </aside></>
}
