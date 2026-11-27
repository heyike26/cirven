import {createEffect,createUniqueId,onCleanup,onMount,type JSX} from 'solid-js';
import {WorkflowIcon} from './WorkflowIcon';

export function WorkflowDraftNotice(p:{message:string}){
  return <div class="wf-draft-notice-layer">
    <p class="wf-draft-notice" role="alert">{p.message}</p>
  </div>;
}

export default function WorkflowDialog(p:{
  open:boolean;title:string;onClose:()=>void;children:JSX.Element;side?:boolean;size?:'compact'|'regular'|'tools';locale?:string;childScroll?:boolean;draftNotice?:string
}){
  let dialog!:HTMLDialogElement;
  let trigger:HTMLElement|null=null;
  let backdropPress=false;
  const titleId=createUniqueId();
  function restore(){
    if(trigger?.isConnected)trigger.focus({preventScroll:true});
    trigger=null;
  }
  onMount(()=>createEffect(()=>{
    if(p.open&&!dialog.open){
      trigger=document.activeElement instanceof HTMLElement?document.activeElement:null;
      backdropPress=false;
      dialog.showModal();
    } else if(!p.open&&dialog.open){
      dialog.close();
      restore();
    }
  }));
  onCleanup(()=>{if(dialog?.open){dialog.close();restore();}});
  return <dialog ref={dialog} class="wf-dialog" data-side={p.side||undefined} data-size={p.size} aria-labelledby={titleId}
    onCancel={event=>{event.preventDefault();p.onClose();}}
    onPointerDown={event=>{backdropPress=event.target===dialog;}}
    onClick={event=>{if(backdropPress&&event.target===dialog)p.onClose();backdropPress=false;}}>
    <div class="wf-dialog-inner" data-draft-notice={p.draftNotice!==undefined||undefined}>
      <header class="wf-dialog-heading"><h2 id={titleId}>{p.title}</h2>
        <button type="button" class="wf-icon-button" aria-label={p.locale==='en'?'Close':'关闭'} onClick={p.onClose}><WorkflowIcon name="close"/></button>
      </header>
      {p.draftNotice!==undefined&&<WorkflowDraftNotice message={p.draftNotice}/>}
      <div class="wf-dialog-body" data-child-scroll={p.childScroll||undefined}>{p.children}</div>
    </div>
  </dialog>;
}
