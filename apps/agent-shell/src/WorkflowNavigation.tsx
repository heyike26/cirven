import {createUniqueId,For,Show} from 'solid-js';
import {WorkflowIcon,stageGlyph} from './WorkflowIcon';
import type {StageRun} from './stage-workflows';

export default function WorkflowNavigation(p:{
  run:StageRun;page:string;busy:boolean;onSelect:(id:string)=>void;t:(zh:string,en:string)=>string
}){
  const pickerId=createUniqueId();
  const status=(id:string)=>p.run.stages.find(s=>s.node_id===id)?.status;
  const label=(s:string|undefined)=>s==='approved'?p.t('已审核','Approved'):
    s==='pending'?p.t('待解锁','Locked'):s==='failed'?p.t('执行失败','Failed'):
    s==='running'?p.t('执行中','Running'):p.t('可查看','Available');
  return <nav class="wf-nav" aria-label={p.t('流程节点','Stages')} data-long={p.run.definition.nodes.length>8}>
    <ol class="wf-stage-track"><For each={p.run.definition.nodes}>{n=>
      <li data-status={status(n.id)} data-selected={n.id===p.page}>
        <button type="button" disabled={p.busy||status(n.id)==='pending'} aria-current={n.id===p.page?'step':undefined}
          aria-label={n.name+' · '+label(status(n.id))} title={n.name+' · '+label(status(n.id))} onClick={()=>p.onSelect(n.id)}>
          <span class="wf-track-mark"><WorkflowIcon name={status(n.id)==='approved'?'check':stageGlyph[n.kind]} size={15}/></span>
          <span class="wf-track-name">{n.name}</span>
        </button>
      </li>}</For></ol>
    <div class="wf-mobile-nav">
      <label for={pickerId}>{p.t('流程节点','Stages')}</label>
      <div class="wf-stage-picker"><WorkflowIcon name="layers" size={16}/>
        <select id={pickerId} value={p.page} disabled={p.busy} onChange={e=>p.onSelect(e.currentTarget.value)}>
          <For each={p.run.definition.nodes}>{(n,i)=><option value={n.id} selected={n.id===p.page} disabled={status(n.id)==='pending'}>{i()+1} · {n.name}</option>}</For>
        </select><span class="wf-picker-caption" aria-hidden="true">{p.t('流程','Stages')} · {p.run.definition.nodes.findIndex(n=>n.id===p.page)+1}/{p.run.definition.nodes.length}</span><WorkflowIcon name="chevron" size={13}/></div>
      <span>{p.run.stages.filter(s=>s.status==='approved').length}/{p.run.stages.length} {p.t('已审','approved')}</span>
    </div>
    <Show when={p.run.active_node_id&&p.run.active_node_id!==p.page}>
      <button class="wf-return" type="button" onClick={()=>p.onSelect(p.run.active_node_id!)}><WorkflowIcon name="arrow" size={14}/>{p.t('返回当前步骤','Back to active stage')}</button>
    </Show>
  </nav>;
}
