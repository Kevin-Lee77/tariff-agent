import React, { useEffect, useState } from 'react';
import { api, post } from '../api';

export function Memory({actor, notify}) {
  const [data,setData] = useState(null), [reasons,setReasons] = useState({});
  const reviewer = ['admin','reviewer'].includes(actor.role);
  const load = () => api('/v1/memory/experts').then(setData).catch(e=>notify(e.message,true));
  useEffect(()=>{load();},[]);
  async function revoke(row){
    try {await post(`/v1/memory/experts/${row.id}/revoke`,{reason:reasons[row.id] || ''}); await load(); notify('专家记忆已撤销');}
    catch(e){notify(e.message,true);}
  }
  return <>
    <section className="panel form-panel"><h2>任务记忆与专家经验</h2>
      <p>同一申报的事实和补充信息保存在任务中；经专家确认的案例可单独发布为跨任务参考。原始历史案例与模型报告不会自动成为专家记忆。</p>
      <p>记忆按知识和词典版本、有效期及撤销状态筛选。它提供经验参考，法律依据仍须从当前税则原文核验。</p>
      <button className="text-btn" onClick={load}>刷新专家记忆</button>
    </section>
    <section className="panel form-panel"><h2>专家案例记忆</h2>
      {!data ? <p>正在读取…</p> : !data.items.length ? <p>尚无已发布的专家记忆。在已确认任务详情中可由专家发布。</p> : data.items.map(row=><article className="report" key={row.id}>
        <h3>{row.code} · {row.status === 'revoked' ? '已撤销' : row.applicable ? '当前可用' : '已过期或版本不适用'}</h3>
        <p>{row.data.description}</p><p>专家说明：{row.data.expert_reason}</p>
        <p className="small-note">来源任务 {row.source_job.slice(0,12)} · 版本 {row.domain_version.slice(0,12)} · 有效期至 {new Date(row.expires*1000).toLocaleDateString()}</p>
        {row.status === 'revoked' && <p>撤销原因：{row.revocation_reason}</p>}
        {reviewer && row.status === 'published' && <div className="form-row">
          <input aria-label={`撤销原因-${row.id}`} placeholder="撤销原因，至少3个字" value={reasons[row.id] || ''} onChange={e=>setReasons({...reasons,[row.id]:e.target.value})}/>
          <button className="secondary-btn" disabled={(reasons[row.id]||'').trim().length<3} onClick={()=>revoke(row)}>撤销记忆</button>
        </div>}
      </article>)}
    </section>
  </>;
}

export function CaseMemory({task, reviewer, notify}) {
  const [memory,setMemory] = useState(null), [trace,setTrace] = useState(null);
  const load = () => Promise.all([api(`/v1/cases/${task.id}/memory`),api(`/v1/tasks/${task.id}/trace`)])
    .then(([m,t])=>{setMemory(m);setTrace(t);}).catch(e=>notify(e.message,true));
  useEffect(()=>{load();},[task.id,task.updated]);
  async function publish(){
    try {const result=await post(`/v1/cases/${task.id}/memory/publish`,{expected_revision:task.revision,ttl_days:180});
      notify(result.status==='published'?'专家案例已发布为记忆':'该记忆已撤销，不能自动重新发布');await load();}
    catch(e){notify(e.message,true);}
  }
  return <section className="report"><h3>任务记忆与恢复</h3>
    <p>输入版本：{task.revision?.slice(0,12)}</p>
    <p>已提交要素：{memory?.confirmed_elements?.length || 0} 项；模型抽取保留为建议，不覆盖这些事实。</p>
    <h3>上下文与工具探索</h3>
    <p>上下文准备 {memory?.runtime?.contexts?.length || 0} 次；卸载冗余记录 {memory?.runtime?.contexts?.reduce((n,row)=>n+row.omitted_records,0) || 0} 条。原始申报及确认要素保留。</p>
    {!!memory?.runtime?.contexts?.length && <p className="small-note">Token预算采用本地分词器估算，实际计费以模型返回用量为准。</p>}
    {memory?.runtime?.contexts?.filter(row=>row.omitted_records>0).map((row,i)=><p className="small-note" key={i}>{row.stage} · 估算输入 {row.input_before} → {row.input_after} · 原始记录 {row.artifact_id.slice(0,12)}</p>)}
    {memory?.runtime?.actions?.map((row,i)=><p key={i}>第 {row.round} 轮 · {row.skill_id} · {row.action}</p>)}
    {memory?.runtime?.human_review && <p>专家协同：{memory.runtime.human_review.status==='completed'?'已完成原生恢复':'已持久暂停，等待专家确认'}</p>}
    {reviewer && task.status==='approved' && <button className="primary-btn" onClick={publish}>发布为专家案例记忆</button>}
    {memory?.runs.map(run=><article className="description-card" key={run.id}>
      <b>执行 {run.id.slice(0,12)} · {run.status}</b>
      <p>最近阶段：{run.stage} · 知识版本：{run.domain_version.slice(0,12)}</p>
      {!!run.state.pending_questions?.length && <p>待补充：{run.state.pending_questions.join('；')}</p>}
      <p>专家参考：{run.state.expert_memory_ids?.length || 0} 条 · 已绑定引用：{Object.keys(run.state.evidence_manifest || {}).length} 条</p>
      <p className="small-note">Trace {run.trace_id || '未记录'} · 重试会先重新校验原文风险；输入或策略改变后建立新执行版本。</p>
    </article>)}
    <h3>调用链路</h3><p>本地记录 {trace?.items.length || 0} 个阶段、工具及模型Span；不记录隐藏思维过程。</p>
    {trace?.items.slice(-30).map(row=><p className="small-note" key={row.span_id}>{row.name} · {Math.round((row.ended-row.started)*1000)}ms · {row.status}</p>)}
  </section>;
}
