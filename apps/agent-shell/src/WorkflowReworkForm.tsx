import {createEffect,createSignal,For,onCleanup,Show} from 'solid-js';
import {stageApi,type ReworkPreview,type RunStage,type StageRun} from './stage-workflows';
import WorkflowReworkEvidence from './WorkflowReworkEvidence';

export type ReworkDraft={reason:string;target?:string};

export default function WorkflowReworkForm(p:{
 run:StageRun;stage:RunStage;draft:ReworkDraft;editable:boolean;t:(zh:string,en:string)=>string;
 onDraft:(draft:ReworkDraft)=>void;
 onSubmit:(revision:number,target:string,caseIds:string[],reason:string)=>Promise<void>;
}){
 const [preview,setPreview]=createSignal<ReworkPreview>();
 const [error,setError]=createSignal('');
 const [retry,setRetry]=createSignal(0);
 const target=()=>{
  const targets=preview()?.targets??[];
  return p.draft.target?targets.find(node=>node.id===p.draft.target):targets.at(-1);
 };
 createEffect(()=>{
  retry();
  const run=p.run,stage=p.stage;
  const controller=new AbortController();
  setPreview(undefined);setError('');
  void stageApi.reworkPreview(run,stage,controller.signal).then(value=>{
   if(!controller.signal.aborted)setPreview(value);
  }).catch(reason=>{
   if(!controller.signal.aborted)setError(reason instanceof Error?reason.message:p.t('返工范围暂时无法读取。','Could not read the rework scope.'));
  });
  onCleanup(()=>controller.abort());
 });

 return <form class="wf-stage-panel wf-rework-form" onSubmit={e=>{
  e.preventDefault();
  const selected=target(),basis=preview(),reason=p.draft.reason.trim();
  if(p.editable&&selected&&basis&&reason)void p.onSubmit(basis.revision,selected.id,basis.case_ids,reason);
 }}>
  <Show when={preview()} fallback={<Show when={error()} fallback={
   <p role="status">{p.t('正在核对返工范围…','Checking the rework scope…')}</p>
  }>{message=><div class="wf-rework-feedback">
   <p class="wf-error" role="alert">{message()}</p>
   <button type="button" onClick={()=>setRetry(n=>n+1)}>{p.t('重试读取范围','Retry reading scope')}</button>
  </div>}</Show>}>{basis=>
   <Show when={basis().targets.length} fallback={<p role="status">{p.t('当前测试没有可返工的已审开发节点。','This test has no eligible approved development stage.')}</p>}>
    <Show when={basis().targets.length>1||!target()} fallback={
     <p class="wf-rework-target"><span>{p.t('开发节点','Development stage')}</span><strong>{target()?.name}</strong></p>
    }>
     <label>{p.t('开发节点','Development stage')}<select disabled={!p.editable}
      value={target()?.id??''} onChange={e=>p.onDraft({...p.draft,target:e.currentTarget.value})}>
      <option value="" disabled selected={!target()}>{p.t('请选择开发节点','Select a development stage')}</option>
      <For each={basis().targets}>{node=><option value={node.id} selected={node.id===target()?.id}>{node.name}</option>}</For>
     </select></label>
    </Show>
    <Show when={target()}>{selected=><details class="wf-rework-impact">
     <summary>{p.t('待重新审核的阶段','Stages to review again')} · {selected().affected.length}
      <Show when={basis().case_ids.length}> · {basis().case_ids.length} {p.t('个关联用例','linked test cases')}</Show>
     </summary>
     <ol><For each={selected().affected}>{node=><li>{node.name}</li>}</For></ol>
     <Show when={basis().case_ids.length}><p>{p.t('关联用例','Linked test cases')} · {basis().case_ids.join('、')}</p></Show>
    </details>}</Show>
    <WorkflowReworkEvidence run={p.run} stage={p.stage} revision={basis().revision} t={p.t}/>
   </Show>
  }</Show>
  <label>{p.t('返工原因','Reason for rework')}<textarea required maxLength={8000} rows="3" disabled={!p.editable}
   value={p.draft.reason} onInput={e=>p.onDraft({...p.draft,reason:e.currentTarget.value})}/></label>
  <div class="wf-actions"><button type="submit" class="wf-primary" disabled={!p.editable||!target()||!p.draft.reason.trim()}>
   {p.t('记录并返回开发','Record and reopen development')}
  </button></div>
 </form>;
}
