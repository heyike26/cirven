import {batch,createSignal,For,Show} from 'solid-js';
import {blankNode,stageNames,validateNodes,type Capabilities,type Catalog,type Definition,type Kernel,type NodeConfig,type StageKind,type StageNode} from './stage-workflows';
import WorkflowDialog from './WorkflowDialog';
import {WorkflowIcon,stageGlyph} from './WorkflowIcon';

type Tr=(zh:string,en:string)=>string;
export function StagePolicy(p:{value:NodeConfig;catalog:Catalog;t:Tr;onChange:(v:NodeConfig)=>void}){
 const change=(patch:Partial<NodeConfig>)=>p.onChange({...p.value,...patch});
 const categories:(keyof Capabilities)[]=['skills','tools','mcp'];
 function toggle(category:keyof Capabilities,id:string,checked:boolean){
  const old=p.value.capabilities[category];
  change({capabilities:{...p.value.capabilities,[category]:checked?[...new Set([...old,id])]:old.filter(v=>v!==id)}});
 }
 return <div class="wf-policy">
  <div class="wf-controls">
   <label>{p.t('执行内核','Execution engine')}<select value={p.value.kernel} onChange={e=>change({kernel:e.currentTarget.value as Kernel})}>
    <option value="opencode">OpenCode</option><option value="claude-code">Claude Code</option></select></label>
   <label>{p.t('子 Agent 上限','Subagent limit')}<input type="number" min="0" max="32" step="1" value={p.value.max_subagents}
    onChange={e=>{const value=e.currentTarget.valueAsNumber;if(Number.isInteger(value)&&value>=0&&value<=32)change({max_subagents:value});else e.currentTarget.value=String(p.value.max_subagents);}}/></label>
  </div>
  <For each={categories}>{category=><details class="wf-permission-group">
   <summary><WorkflowIcon name="chevron" size={15}/><span>{category==='skills'?'Skills':category==='tools'?'Tools':'MCP'}</span><small>{p.value.capabilities[category].length} {p.t('已允许','allowed')}</small></summary>
   <div class="wf-permissions"><For each={[...new Set([...p.catalog[category].map(x=>x.id),...p.value.capabilities[category]])]}
    fallback={<small>{p.t('暂无已注册项','No registered entries')}</small>}>{id=><label><input type="checkbox"
    checked={p.value.capabilities[category].includes(id)} onChange={e=>toggle(category,id,e.currentTarget.checked)}/>
    {p.catalog[category].find(x=>x.id===id)?.name??id}</label>}</For></div>
  </details>}</For>
 </div>;
}

export default function StageDefinitionEditor(p:{draft:Definition;catalog:Catalog;t:Tr;busy:boolean;status:string;
 mode:'save'|'retry'|'copy';onDraft:(v:Definition)=>void;onCancel:()=>void;onSave:()=>void}){
 const draft=()=>p.draft;
 const [active,setActive]=createSignal(draft().nodes[0]?.id);
 const [policyOpen,setPolicyOpen]=createSignal(false);
 const node=()=>draft().nodes.find(v=>v.id===active());
 const index=()=>draft().nodes.findIndex(v=>v.id===active());
 const before=()=>draft().nodes.slice(0,index());
 const blocked=()=>p.busy||p.mode==='save'&&(!draft().name.trim()||!validateNodes(draft().nodes));
 const cancelBlocked=()=>p.busy||p.mode==='retry';
 const update=(change:Partial<StageNode>)=>p.onDraft({...draft(),nodes:draft().nodes.map(n=>n.id===active()?{...n,...change}:n)});
 function move(step:number){
  const list=[...draft().nodes],i=index();if(!list[i+step])return;
  [list[i],list[i+step]]=[list[i+step],list[i]];p.onDraft({...draft(),nodes:list});
 }
 function remove(){
  const id=active(),i=index(),next=draft().nodes.filter(n=>n.id!==id).map(n=>({...n,inputs:n.inputs.filter(x=>x!==id)}));
  batch(()=>{p.onDraft({...draft(),nodes:next});setActive(next[Math.min(i,next.length-1)]?.id);});
 }
 function add(){
  const n=blankNode(),previous=draft().nodes.at(-1);if(previous)n.inputs=[previous.id];
  batch(()=>{p.onDraft({...draft(),nodes:[...draft().nodes,n]});setActive(n.id);});
 }
 return <form class="wf-definition" novalidate onSubmit={e=>{e.preventDefault();if(!blocked())p.onSave();}}>
  <header class="wf-editor-head"><h1 tabindex="-1">{draft().revision?p.t('编辑工作流','Edit workflow'):p.t('新建工作流','New workflow')}</h1></header>
  <label class="wf-editor-name">{p.t('工作流名称','Workflow name')}<input required value={draft().name} onInput={e=>p.onDraft({...draft(),name:e.currentTarget.value})}/></label>
  <div class="wf-editor-layout">
   <nav class="wf-editor-index" aria-label={p.t('节点目录','Stage index')}>
    <h3>{p.t('流程节点','Stages')} <small>· {draft().nodes.length}</small></h3>
    <div class="wf-definition-nodes"><For each={draft().nodes}>{(n,i)=><button type="button" aria-pressed={active()===n.id} onClick={()=>setActive(n.id)}>
     <WorkflowIcon name={stageGlyph[n.kind]} size={15}/>{i()+1} · {n.name}</button>}</For></div>
    <button type="button" onClick={add}><WorkflowIcon name="plus" size={15}/>{p.t('添加节点','Add stage')}</button>
   </nav>
   <Show when={node()}>{n=><section class="wf-node-fields" aria-label={p.t('节点设置','Stage settings')}>
    <div class="wf-editor-label"><span class="wf-stage-symbol"><WorkflowIcon name={stageGlyph[n().kind]}/></span><h2>{n().name||p.t('未命名节点','Untitled stage')}</h2></div>
    <div class="wf-controls"><label>{p.t('节点名称','Stage name')}<input required value={n().name} onInput={e=>update({name:e.currentTarget.value})}/></label>
     <label>{p.t('节点类型','Stage type')}<select value={n().kind} onChange={e=>update({kind:e.currentTarget.value as StageKind})}>
      <For each={Object.keys(stageNames) as StageKind[]}>{kind=><option value={kind} selected={kind===n().kind}>{p.t(...stageNames[kind])}</option>}</For></select></label></div>
    <label>{p.t('执行指令','Stage instructions')}<textarea value={n().instructions} rows="3" onInput={e=>update({instructions:e.currentTarget.value})}/></label>
    <fieldset><legend>{p.t('允许读取的已审核节点','Approved inputs this stage can read')}</legend>
     <div class="wf-checks"><For each={before()} fallback={<small>{p.t('首个节点从项目输入开始。','The first stage starts with project input.')}</small>}>
      {other=><label class="wf-check"><input type="checkbox" checked={n().inputs.includes(other.id)} onChange={e=>update({inputs:e.currentTarget.checked?[...n().inputs,other.id]:n().inputs.filter(v=>v!==other.id)})}/>{other.name}</label>}</For></div>
     <For each={n().inputs.filter(x=>!before().some(s=>s.id===x))}>{id=><button type="button" onClick={()=>update({inputs:n().inputs.filter(x=>x!==id)})}>{p.t('移除已移到后面的输入','Remove input now positioned later')} · {draft().nodes.find(x=>x.id===id)?.name}</button>}</For>
    </fieldset>
    <div><button type="button" aria-haspopup="dialog" onClick={()=>setPolicyOpen(true)}><WorkflowIcon name="sliders" size={16}/>{p.t('内核与节点权限','Engine and stage permissions')}</button></div>
    <WorkflowDialog side open={policyOpen()} title={p.t('内核与节点权限','Engine and stage permissions')} locale={p.t('zh-CN','en')} onClose={()=>setPolicyOpen(false)}>
     <StagePolicy value={n()} catalog={p.catalog} t={p.t} onChange={update}/>
     <footer class="wf-footer"><button type="button" class="wf-primary" onClick={()=>setPolicyOpen(false)}>{p.t('返回编辑','Return to editor')}</button></footer>
    </WorkflowDialog>
    <div class="wf-actions"><button type="button" disabled={index()===0} onClick={()=>move(-1)}>{p.t('上移','Move up')}</button>
     <button type="button" disabled={index()===draft().nodes.length-1} onClick={()=>move(1)}>{p.t('下移','Move down')}</button>
     <button class="wf-danger-text" type="button" onClick={remove}>{p.t('移除节点','Remove stage')}</button></div>
   </section>}</Show>
  </div>
  <Show when={!validateNodes(draft().nodes)}><p class="wf-questions" role="status">{p.t('至少保留一个具名节点，并将输入节点放在使用它的节点之前。','Keep at least one named stage and place dependencies before their consumers.')}</p></Show>
  <footer class="wf-footer wf-definition-footer">
   <p class="wf-definition-status" role="status" title={p.status}>{p.status}</p>
   <button type="button" aria-disabled={cancelBlocked()} onClick={()=>{if(!cancelBlocked())p.onCancel();}}>{p.t('取消','Cancel')}</button>
   <button class="wf-primary wf-definition-save" type="submit" aria-disabled={blocked()}>
    {p.mode==='retry'?p.t('重试上次保存','Retry last save'):p.mode==='copy'?p.t('转为副本','Keep as a copy'):p.t('保存工作流','Save workflow')}
   </button>
  </footer>
 </form>;
}
