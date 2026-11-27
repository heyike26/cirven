import {createEffect,createSignal,createUniqueId,For,onCleanup} from 'solid-js';
import {WorkflowIcon} from './WorkflowIcon';

export default function WorkflowModeSwitch(p:{
 workflow:boolean;entry:'idle'|'pending'|'failed';locale:string;onChange:(workflow:boolean)=>void;onCancel:()=>void
}){
 const group=createUniqueId();
 const [showWaiting,setShowWaiting]=createSignal(false);
 const waiting=()=>p.entry==='pending'&&showWaiting();
 const failed=()=>p.entry==='failed';
 let element!:HTMLDivElement;
 createEffect(()=>{
  if(p.entry!=='pending'){setShowWaiting(false);return;}
  const timer=setTimeout(()=>setShowWaiting(true),200);
  onCleanup(()=>clearTimeout(timer));
 });
 function change(workflow:boolean){
  // The native radio checks itself before the target's directory is ready.
  for(const input of element.querySelectorAll<HTMLInputElement>('input'))input.checked=(input.value==='workflow')===p.workflow;
  p.onChange(workflow);
 }
 return <div ref={element} class="wf-mode-switch" role="group" aria-label={p.locale==='en'?'Work mode':'工作模式'} data-workflow={p.workflow} data-feedback={waiting()||failed()}
  onKeyDown={event=>{if(event.key==='Escape'&&p.entry!=='idle'){event.stopPropagation();p.onCancel();}}}>
  <i aria-hidden="true"/>
  <For each={['daily','workflow'] as const}>{mode=><label>
   <input type="radio" name={group} value={mode} checked={p.workflow===(mode==='workflow')}
    onClick={()=>{if(mode==='daily'&&!p.workflow&&p.entry!=='idle')p.onCancel();}}
    onChange={event=>{if(event.currentTarget.checked)change(mode==='workflow');}}/>
   <WorkflowIcon name={mode==='daily'?'message':'branch'} size={14}/>
   <span>{mode==='daily'?(p.locale==='en'?'Daily':'日常模式'):(p.locale==='en'?'Workflow':'工作流模式')}</span>
  </label>}</For>
  <div class="wf-mode-feedback" data-visible={waiting()} role="status">{waiting()?
   (p.locale==='en'?'Opening workflow…':'正在打开工作流…'):''}</div>
  <div class="wf-mode-feedback" data-visible={failed()} role="alert">{failed()?
   (p.locale==='en'?'Could not open workflow. Select Workflow to retry.':'暂未打开，点击工作流模式重试。'):''}</div>
 </div>;
}
