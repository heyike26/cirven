import {createSignal,For,Show} from 'solid-js';
import WorkflowDialog from './WorkflowDialog';
import {WorkflowIcon} from './WorkflowIcon';
import type {StageTemplate} from './stage-workflows';

export type TemplateReadState={kind:'reading';name:string}|{kind:'failed';name:string;message:string};

export default function WorkflowTemplateInput(p:{
 template?:StageTemplate|null;reader?:TemplateReadState;editable:boolean;
 t:(zh:string,en:string)=>string;onChoose:(file:File)=>void;onRemove:()=>void;
}){
 const [details,setDetails]=createSignal(false);
 const available=()=>p.template?.name??p.t('使用默认格式','Default format');
 const format=()=>p.template?.export_extension??'.txt';
 const status=()=>{
  switch(p.reader?.kind){
   case 'reading': return p.template?p.t('正在读取替换模板…','Reading replacement…'):p.t('正在解析模板…','Reading template…');
   case 'failed': return p.template?p.t('读取失败 · 保留现有模板','Read failed · template kept'):p.t('读取失败 · 使用默认格式','Read failed · using default');
   default: return p.template?p.t('审核后可导出','Available after approval')+' · '+format():p.t('可选用自己的模板','Choose a template if needed');
  }
 };
 const readError=()=>{const state=p.reader;return state?.kind==='failed'?state.message:undefined;};
 return <div class="wf-template-input">
  <label>{p.t('输出模板（可选）','Output template (optional)')}
   <input type="file" disabled={!p.editable} onChange={event=>{
    const file=event.currentTarget.files?.[0];
    event.currentTarget.value='';
    if(file)p.onChoose(file);
   }}/>
  </label>
  <div class="wf-template-summary" data-read-state={p.reader?.kind}>
   <div class="wf-template-summary-text">
    <strong title={available()}>{available()}</strong>
    <small role="status" title={status()}>{status()}</small>
   </div>
   <button type="button" aria-haspopup="dialog" onClick={()=>setDetails(true)}>
    {p.t('模板详情','Template details')}<WorkflowIcon name="next" size={13}/>
   </button>
  </div>
  <WorkflowDialog open={details()} title={p.t('模板详情','Template details')} size="compact" childScroll
   locale={p.t('zh-CN','en')} onClose={()=>setDetails(false)}>
   <Show when={details()}><div class="wf-template-details">
    <div class="wf-template-details-scroll">
     <div><h3>{p.t('当前模板','Current template')}</h3><p>{available()}</p></div>
     <Show when={p.reader}>{state=>
      <Show when={readError()} fallback={<p role="status">{p.t('正在读取','Reading')} · {state().name}</p>}>{error=>
       <div class="wf-error" role="alert">
        <strong>{p.t('未能读取','Could not read')} · {state().name}</strong>
        <p>{error()}</p>
       </div>
      }</Show>
     }</Show>
     <Show when={p.template}>{template=><>
      <p class="wf-composer-caption">{p.t('审核后可导出','Available after approval')} · {format()}</p>
      <Show when={template().warnings?.length}>
       <ul class="wf-template-warnings"><For each={template().warnings}>{warning=><li>{warning}</li>}</For></ul>
      </Show>
     </>}</Show>
    </div>
    <div class="wf-template-details-actions">
     <Show when={p.template}><button type="button" disabled={!p.editable||p.reader?.kind==='reading'} onClick={()=>{
      p.onRemove();setDetails(false);
     }}>{p.t('移除模板','Remove template')}</button></Show>
    </div>
   </div></Show>
  </WorkflowDialog>
 </div>;
}
