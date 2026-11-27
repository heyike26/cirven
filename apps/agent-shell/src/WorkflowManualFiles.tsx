import {createUniqueId,For,Show} from 'solid-js';
import WorkflowDialog from './WorkflowDialog';
import {WorkflowIcon} from './WorkflowIcon';
import {manualFileSize,type ManualDraftFile,type ManualFileReceipt} from './workflow-manual-files';

type Tr=(zh:string,en:string)=>string;
export default function WorkflowManualFiles(p:{
 open:boolean;files:ManualDraftFile[];notice:string;busy:boolean;unavailable:boolean;t:Tr;
 onClose:()=>void;onChoose:(files:File[])=>void;onRemove:(id:string)=>void;onRetry:()=>void
}){
 const helpId=createUniqueId();
 const canChoose=()=>!p.busy&&(p.unavailable||p.files.length<3);
 let picker!:HTMLInputElement;
 let pickButton!:HTMLButtonElement;
 return <WorkflowDialog open={p.open} title={p.t('实测证据','Observation evidence')} size="compact" childScroll
  locale={p.t('zh-CN','en')} onClose={p.onClose}>
  <Show when={p.open}><div class="wf-observation-files">
   <div class="wf-observation-files-scroll">
    <p id={helpId}>{p.t('添加本次的串口日志、截图或采样数据。最多 3 个文件，合计 1 MiB。','Add serial logs, screenshots or capture data for this observation. Up to 3 files, 1 MiB total.')}</p>
    <input ref={picker} type="file" multiple hidden onChange={event=>{
     const files=Array.from(event.currentTarget.files??[]);
     event.currentTarget.value='';
     if(files.length)p.onChoose(files);
    }}/>
    <ul aria-label={p.t('待保存文件','Files to save')}>
     <For each={p.files}>{item=><li>
      <WorkflowIcon name="file" size={16}/>
      <span><strong title={item.file.name}>{item.file.name}</strong><small>{manualFileSize(item.file.size)}</small></span>
      <button type="button" aria-label={p.t('移除 ','Remove ')+item.file.name} aria-disabled={p.busy?'true':undefined}
       onClick={event=>{if(!p.busy){
        const next=event.currentTarget.closest('li')?.nextElementSibling?.querySelector('button');
        (next??event.currentTarget.closest('li')?.previousElementSibling?.querySelector('button')??pickButton).focus({preventScroll:true});
        p.onRemove(item.id);
       }}}><WorkflowIcon name="close" size={14}/></button>
     </li>}</For>
    </ul>
   </div>
   <footer>
    <p class="wf-observation-files-notice" role="status" aria-live="polite" title={p.notice}>{p.notice}</p>
    <div class="wf-actions">
     <button ref={pickButton} type="button" class="wf-primary" aria-describedby={helpId}
      aria-disabled={canChoose()?undefined:'true'}
      onClick={()=>{if(canChoose())p.unavailable?p.onRetry():picker.click();}}>
      <WorkflowIcon name={p.unavailable?'history':'plus'} size={16}/>
      {p.unavailable?p.t('重新读取','Read again'):p.t('选择文件','Choose files')}
     </button>
     <button type="button" onClick={p.onClose}>{p.t('返回实测','Back to observation')}</button>
    </div>
   </footer>
  </div></Show>
 </WorkflowDialog>;
}

export function ObservationFiles(p:{files?:ManualFileReceipt[];href:(id:string)=>string;t:Tr}){
 return <Show when={p.files?.length}><ul class="wf-observation-downloads" aria-label={p.t('实测证据','Observation evidence')}>
  <For each={p.files}>{item=><li>
   <a class="wf-file-download" href={p.href(item.id)} download={item.name} title={item.name}>
    <WorkflowIcon name="download" size={15}/><span>{item.name}</span>
   </a><small>{manualFileSize(item.bytes)}</small>
  </li>}</For>
 </ul></Show>;
}
