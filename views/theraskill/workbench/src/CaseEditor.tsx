export default function CaseEditor({suite,onChange,locale,retrieval=false}:{suite:any;onChange:(suite:any)=>void;locale:'zh'|'en';retrieval?:boolean}) {
  const t=(zh:string,en:string)=>locale==='zh'?zh:en
  const update=(index:number,patch:any)=>onChange({...suite,cases:suite.cases.map((c:any,i:number)=>i===index?{...c,...patch}:c)})
  return <div className="case-editor"><p>{suite.cases.length} {t('个案例；修改后需建立对应的新基线。','cases; edits require a matching new baseline.')}</p>{suite.cases.map((c:any,i:number)=><details key={i}><summary>{c.id} · {c.skill}</summary>
    <label>{t('案例名称','Case ID')}<input value={c.id} onChange={e=>update(i,{id:e.target.value})}/></label>
    <label>{t('使用技能','Skill')}<input value={c.skill} onChange={e=>update(i,{skill:e.target.value})}/></label>
    <label>{t('输入问题','Task input')}<textarea value={c.query} onChange={e=>update(i,{query:e.target.value})}/></label>
    {!retrieval&&<><label>{t('输出语言','Output language')}<select value={c.language} onChange={e=>update(i,{language:e.target.value})}><option value="zh">中文</option><option value="en">English</option></select></label><label>{t('评价标准','Evaluation rubric')}<textarea value={c.rubric} onChange={e=>update(i,{rubric:e.target.value})}/></label></>}
    <label>{t('必需知识类型（逗号分隔，全部满足）','Required knowledge families (comma separated, all required)')}<input key={(c.required_families||[]).join(',')} defaultValue={(c.required_families||[]).join(', ')} onBlur={e=>update(i,{required_families:e.target.value.split(',').map(s=>s.trim()).filter(Boolean)})}/></label>
    <button onClick={()=>onChange({...suite,cases:suite.cases.filter((_:any,j:number)=>i!==j)})}>{t('移除此案例','Remove case')}</button>
  </details>)}<button onClick={()=>onChange({...suite,cases:[...suite.cases,{id:'case_'+Date.now(),skill:'emotional-support',language:locale,query:'',rubric:'',required_families:[]}]})}>{t('添加案例','Add case')}</button></div>
}
