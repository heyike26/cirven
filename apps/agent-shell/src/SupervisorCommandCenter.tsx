import {createEffect,createMemo,createSignal,For,onCleanup,Show} from 'solid-js';
import type {SupervisorSnapshot,SupervisorTask,SupervisorTaskKey} from './global-supervisor';
import {uiLocale} from './ui-language';
import StudioPopover from './StudioPopover';
import {answerSupervisorStatusQuery,projectSupervisorCenter,summarizeSupervisorCenter,type CenterReadStatus,type CenterStatusAnswer} from './supervisor-command-center';
import {centerText} from './supervisor-command-center-copy';
import type {SupervisorConversationHistoryView,SupervisorHistorySearch,SupervisorConversationFollowupRef} from './supervisor-conversation-client';
import './supervisor-command-center.css';

export interface SupervisorCenterQuestion {text:string;observedAt:string|null;taskKeys:SupervisorTaskKey[];origin:'supervisor-command-center';followupRef?:{operation_id:string;request_sha256:string}}
export type SupervisorCenterReplyStatus='RETURNED'|'UNKNOWN'|'REJECTED'|'PROCESSING'|'REQUESTING'|'RUNNING'|'FAILED'|'CANCELLED';
export interface SupervisorCenterReply {status:SupervisorCenterReplyStatus;text?:string;operation_id?:string;error?:string|null;text_truncated?:boolean}
export interface SupervisorCenterConversationMessage {id:string;role:'user'|'assistant';text:string;operation_id?:string;status?:SupervisorCenterReplyStatus;error?:string|null;text_truncated?:boolean;provider_profile_ref?:{id:string;revision:number;sha256:string}}
export type SupervisorCenterAskAdmission={allowed:true}|{allowed:false;code:string;operation_id:string};
export interface SupervisorCenterSelectedOperation {operation_id:string;request_sha256:string;question:string;answer_text:string;status:SupervisorCenterReplyStatus}
export interface SupervisorCommandCenterProps {
  snapshot?:SupervisorSnapshot;readStatus?:CenterReadStatus;visible?:boolean;draft?:string;onDraftChange?:(value:string)=>void;
  open?:boolean;onOpenChange?:(value:boolean)=>void;
  onOpenTask:(task:SupervisorTask)=>unknown;onOpenAll:()=>unknown;
  operations?:{operation_id:string;request_sha256:string}[];messages?:SupervisorCenterConversationMessage[];onConfigure?:()=>unknown;canAsk?:boolean;
  providerProfileRef?:{id:string;revision:number;sha256:string}|null;
  error?:string;draftConflict?:boolean;onResolveDraftConflict?:(choice:'keep-local'|'use-remote')=>unknown;onRefresh?:()=>unknown;
  draftBlocked?:boolean;draftRecovery?:'CHECK_REQUIRED'|'RETRY_ALLOWED';onCheckDraftSave?:()=>unknown;onRetryDraftSave?:()=>unknown;
  onAsk?:(question:SupervisorCenterQuestion)=>Promise<SupervisorCenterReply>;onInspect?:(operationId:string)=>Promise<SupervisorCenterReply>;
  onLocalAnswer?:(question:string,answer:CenterStatusAnswer)=>unknown;
  askAdmission?:(text:string)=>SupervisorCenterAskAdmission;
  selectedOperation?:SupervisorCenterSelectedOperation;onDismissSelected?:()=>unknown;
  history?:SupervisorConversationHistoryView;onReadEarlier?:()=>unknown;onResetHistory?:()=>unknown;
  search?:SupervisorHistorySearch;searchBusy?:boolean;onSearchEarlier?:(query:string)=>unknown;onResetSearch?:()=>unknown;
  onSelectDiscussion?:(reference:SupervisorConversationFollowupRef)=>Promise<unknown>;
}
export default function SupervisorCommandCenter(props:SupervisorCommandCenterProps){
  const [ownOpen,setOwnOpen]=createSignal(false),[ownDraft,setOwnDraft]=createSignal(''),[answer,setAnswer]=createSignal<CenterStatusAnswer>(),[localQuestion,setLocalQuestion]=createSignal('');
  const [reply,setReply]=createSignal<SupervisorCenterReply>(),[sending,setSending]=createSignal(false),[replyQuestion,setReplyQuestion]=createSignal(''),[savingLocal,setSavingLocal]=createSignal(false);
  const [inspectionReply,setInspectionReply]=createSignal<SupervisorCenterReply>(),[inspecting,setInspecting]=createSignal(false);
  const [searchQuery,setSearchQuery]=createSignal(''),[selecting,setSelecting]=createSignal(false),[discussionError,setDiscussionError]=createSignal(false);let discussionRequest=0;
  let replyProfile:SupervisorCommandCenterProps['providerProfileRef'];
  let input:HTMLTextAreaElement|undefined,historyList:HTMLDivElement|undefined,disposed=false,request=0,inspectionRequest=0,historyRequest=0,composing=false;
  const text=(key:Parameters<typeof centerText>[1],values:Record<string,string|number>={})=>centerText(uiLocale(),key,values);
  const open=()=>props.open??ownOpen();
  const setOpen=(value:boolean|((value:boolean)=>boolean))=>{const next=typeof value==='function'?value(open()):value;setOwnOpen(next);props.onOpenChange?.(next);};
  const draft=()=>props.draft??ownDraft();
  const projection=createMemo(()=>projectSupervisorCenter(props.snapshot,uiLocale(),props.readStatus));
  const summary=createMemo(()=>summarizeSupervisorCenter(projection(),uiLocale()));
  const changeDraft=(value:string)=>{setOwnDraft(value);props.onDraftChange?.(value);};
  createEffect(()=>{
    const currentReply=reply();if(!currentReply?.operation_id||!['UNKNOWN','PROCESSING','REQUESTING','RUNNING'].includes(currentReply.status))return;
    const latest=props.messages?.findLast(message=>message.role==='assistant'&&message.operation_id===currentReply.operation_id&&message.status&&['RETURNED','REJECTED','FAILED','CANCELLED'].includes(message.status));
    if(latest?.status)setReply({operation_id:currentReply.operation_id,status:latest.status,text:latest.text,error:latest.error});
  });
  createEffect(()=>{if(!open()){historyRequest++;discussionRequest++;setSelecting(false);props.onResetSearch?.();}});
  onCleanup(()=>{discussionRequest++;disposed=true;request++;inspectionRequest++;historyRequest++;});
  const ask=async()=>{
    if(composing||props.draftBlocked||!draft().trim())return;
    const inputText=draft(),local=answerSupervisorStatusQuery(inputText,projection(),uiLocale());
    if(local){
      if(savingLocal())return;props.onResetHistory?.();setSavingLocal(true);setLocalQuestion(inputText);setAnswer(local);if(!unresolved())setReply(undefined);
      try{await props.onLocalAnswer?.(inputText,local);if(!disposed&&draft()===inputText)changeDraft('');}
      catch{if(!disposed)setReply({status:'REJECTED',text:text('draftUnavailable')});}
      finally{if(!disposed)setSavingLocal(false);}return;
    }
    if(sending()||blocksDraft())return;
    props.onResetHistory?.();
    setAnswer(undefined);setReply(undefined);
    if(!props.onAsk||props.canAsk===false){setReply({status:'REJECTED',text:text('retained')});return;}
    const owner=++request;setReplyQuestion(inputText);replyProfile=props.providerProfileRef;setSending(true);
    const selected=props.selectedOperation,followupRef=selected?{operation_id:selected.operation_id,request_sha256:selected.request_sha256}:undefined;
    try{
      const result=await props.onAsk({text:inputText,observedAt:projection().observedAt,taskKeys:projection().tasks.slice(0,8).map(item=>({...item.task!.key})),origin:'supervisor-command-center',...followupRef?{followupRef}:{}});
      if(!disposed&&request===owner){
        if(!validReply(result))setReply({status:'UNKNOWN',text:text('unknown')});
        else{const inspected=inspectionReply(),effective=inspected&&inspected.operation_id===result.operation_id&&!['UNKNOWN','PROCESSING','REQUESTING','RUNNING'].includes(inspected.status)&&['UNKNOWN','PROCESSING','REQUESTING','RUNNING'].includes(result.status)?inspected:result;
          setReply(effective);if(!['UNKNOWN','REJECTED'].includes(effective.status)&&draft()===inputText)changeDraft('');}
      }
    }catch{if(!disposed&&request===owner)setReply({status:'UNKNOWN',text:text('unknown')});}
    finally{if(!disposed&&request===owner)setSending(false);}
  };
  const validReply=(value:SupervisorCenterReply)=>value&&['RETURNED','UNKNOWN','REJECTED','PROCESSING','REQUESTING','RUNNING','FAILED','CANCELLED'].includes(value.status);
  const unresolved=()=>!!reply()&&['UNKNOWN','PROCESSING','REQUESTING','RUNNING'].includes(reply()!.status);
  const sameProfile=(previous:SupervisorCommandCenterProps['providerProfileRef'])=>!previous||!props.providerProfileRef||previous.id===props.providerProfileRef.id&&previous.revision===props.providerProfileRef.revision&&previous.sha256===props.providerProfileRef.sha256;
  const localDraft=()=>!!answerSupervisorStatusQuery(draft(),projection(),uiLocale());
  const blockedAdmission=()=>{
    if(localDraft()||props.canAsk===false)return;
    const admission=props.askAdmission?.(draft());return admission?.allowed===false?admission:undefined;
  };
  const blocksDraft=()=>{
    if(props.askAdmission)return !!blockedAdmission();
    if(reply()&&['PROCESSING','REQUESTING','RUNNING'].includes(reply()!.status))return true;
    if(reply()?.status==='UNKNOWN'&&draft()===replyQuestion()&&sameProfile(replyProfile))return true;
    return props.messages?.some(message=>{
      if(message.role!=='assistant'||!message.status)return false;
      if(['PROCESSING','REQUESTING','RUNNING'].includes(message.status))return true;
      if(message.status!=='UNKNOWN')return false;
      const question=props.messages?.find(row=>row.role==='user'&&row.operation_id===message.operation_id);
      return !question||question.text===draft()&&sameProfile(question.provider_profile_ref);
    })===true;
  };
  const inspect=async(operationId:string)=>{
    if(inspecting()||!props.onInspect)return;const owner=++inspectionRequest;setInspecting(true);
    const accept=(result:SupervisorCenterReply)=>{
      if(disposed||owner!==inspectionRequest)return;setInspectionReply(result);
      const current=reply();if(current?.operation_id&&current.operation_id!==operationId)return;
      if(current&&!['UNKNOWN','PROCESSING','REQUESTING','RUNNING'].includes(current.status)&&['UNKNOWN','PROCESSING','REQUESTING','RUNNING'].includes(result.status))return;
      setReply(result);
    };
    try{const result=await props.onInspect(operationId);accept(validReply(result)&&result.operation_id===operationId?result:{status:'UNKNOWN',operation_id:operationId,text:text('unknown')});}
    catch{accept({status:'UNKNOWN',operation_id:operationId,text:text('unknown')});}
    finally{if(!disposed&&owner===inspectionRequest)setInspecting(false);}
  };
  const searchView=()=>props.search?.query===searchQuery().trim()?props.search:undefined;
  const historyReference=(id:string|undefined)=>{
    if(!id)return;const operation=[...(props.history?.operations??[]),...(props.operations??[])].find(row=>row.operation_id===id);
    return operation?{operation_id:operation.operation_id,request_sha256:operation.request_sha256}:undefined;
  };
  const selectDiscussion=async(reference:SupervisorConversationFollowupRef)=>{
    if(selecting()||!props.onSelectDiscussion)return;const owner=++discussionRequest,trigger=document.activeElement;setSelecting(true);setDiscussionError(false);
    try{await props.onSelectDiscussion({...reference});if(!disposed&&owner===discussionRequest&&open()&&props.selectedOperation?.operation_id===reference.operation_id&&(document.activeElement===trigger||document.activeElement===document.body))queueMicrotask(()=>{if(!disposed&&owner===discussionRequest&&open()&&props.selectedOperation?.operation_id===reference.operation_id)input?.focus();});}
    catch{if(!disposed&&owner===discussionRequest)setDiscussionError(true);}
    finally{if(!disposed&&owner===discussionRequest)setSelecting(false);}
  };
  const messageMap=createMemo(()=>new Map([...(props.history?.messages??[]),...(props.history?.windowed?[]:props.messages??[])].map(message=>[message.id,message])));
  const historyHint=()=>props.history?.error?text(props.history.error==='CHANGED'?'historyChanged':'historyUnavailable'):props.history?.changed?text('historyChanged'):props.history?.windowed?text('historyWindow'):undefined;
  const loadEarlier=async()=>{
    if(!props.onReadEarlier||props.history?.loading)return;const owner=++historyRequest,element=historyList;
    const beforeTop=element?.scrollTop??0,bounds=element?.getBoundingClientRect?.(),anchor=element&&bounds?[...element.querySelectorAll<HTMLElement>('article[data-message-id]')].find(node=>node.getBoundingClientRect().bottom>bounds.top+32):undefined;
    const anchorId=anchor?.dataset.messageId,anchorTop=anchor?.getBoundingClientRect().top;
    try{await props.onReadEarlier();}catch{/* The client retains an independent readable-history error. */}
    if(disposed||owner!==historyRequest||!open()||!element||historyList!==element||element.scrollTop!==beforeTop||!bounds||element.getBoundingClientRect().width!==bounds.width)return;
    const current=anchorId?[...element.querySelectorAll<HTMLElement>('article[data-message-id]')].find(node=>node.dataset.messageId===anchorId):undefined;
    if(current&&anchorTop!==undefined)element.scrollTop+=current.getBoundingClientRect().top-anchorTop;
  };
  const localAnswerInHistory=()=>{
    const current=answer();if(!current||props.selectedOperation)return false;
    const visible=[...messageMap().values()];
    return visible.some((message,index)=>message.role==='assistant'&&!message.operation_id&&message.text===current.text&&
      visible[index-1]?.role==='user'&&!visible[index-1].operation_id&&visible[index-1].text===localQuestion());
  };
  const count=()=>projection().counts.attention||projection().counts.running;
  const selectedView=()=>{
    const selected=props.selectedOperation;if(!selected)return;
    let current={...selected,text_truncated:false};
    const latest=props.messages?.findLast(message=>message.role==='assistant'&&message.operation_id===selected.operation_id&&message.status);
    const inspected=inspectionReply()?.operation_id===selected.operation_id?inspectionReply():undefined;
    const currentReply=reply()?.operation_id===selected.operation_id?reply():undefined;
    for(const update of [latest,currentReply,inspected]){
      if(!update?.status)continue;
      if(!['UNKNOWN','PROCESSING','REQUESTING','RUNNING'].includes(current.status)&&['UNKNOWN','PROCESSING','REQUESTING','RUNNING'].includes(update.status))continue;
      current={...current,status:update.status,answer_text:update.text??current.answer_text,text_truncated:update.text_truncated===true};
    }
    return current;
  };
  const inputInspection=()=>props.askAdmission?blockedAdmission()?.operation_id:undefined;
  return <div class="as-supervisor-command-center" data-tone={summary().tone}>
    <StudioPopover open={open()} onOpenChange={setOpen} label={text('label')} class="as-supervisor-command-popover" placement="bottom-start" initialFocus={()=>input}
      trigger={<button class="as-supervisor-command-trigger" type="button" aria-label={text('label')+' · '+summary().label+(summary().status?' · '+summary().status:'')+' · '+text('ask')} title={text('label')+' · '+summary().label+(summary().status?' · '+summary().status:'')} aria-haspopup="dialog" aria-expanded={open()} onClick={()=>setOpen(value=>!value)}>
        <span class="as-supervisor-command-dot" aria-hidden="true"/>
        <span class="as-supervisor-command-scope">{text('label')}</span>
        <span class="as-supervisor-command-idle"><Show keyed when={JSON.stringify([summary().label,summary().status])}>{_key=><span class="as-supervisor-command-ticker"><span>{summary().label}</span><Show when={summary().status}><small>{summary().status}</small></Show></span>}</Show></span>
        <span class="as-supervisor-command-compact">{text('label')}</span><Show when={count()}><span class="as-supervisor-command-count">{count()}</span></Show>
        <svg class="as-supervisor-command-icon" viewBox="0 0 20 20" aria-hidden="true"><path d="M5 3h10a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H8l-4 3v-3a2 2 0 0 1-2-2V5a2 2 0 0 1 3-2Z"/><path d="M6 8h.1M10 8h.1M14 8h.1"/></svg>
        <svg class="as-supervisor-command-chevron" viewBox="0 0 20 20" aria-hidden="true"><path d="m6 8 4 4 4-4"/></svg>
      </button>}>
      <section class="as-supervisor-command-panel" data-selected={!!props.selectedOperation}>
        <header><h2>{text('label')}</h2><button type="button" aria-label={text('close')} onClick={()=>setOpen(false)}>×</button></header>
        <Show when={selectedView()}>{selected=><section class="as-supervisor-command-selected" aria-label={text('originalQuestion')} data-operation-id={selected().operation_id}>
          <header><h3>{text('originalQuestion')}</h3><Show when={props.onDismissSelected}><button type="button" class="as-supervisor-command-selected-dismiss" onClick={()=>void props.onDismissSelected?.()}>{text('recentDiscussion')}</button></Show></header>
          <p class="as-supervisor-command-selected-question">{selected().question}</p>
          <p class="as-supervisor-command-selected-answer" data-status={selected().status}>{selected().answer_text||text(selected().status==='UNKNOWN'?'unknown':['PROCESSING','REQUESTING','RUNNING'].includes(selected().status)?'pending':'noAnswer')}</p>
          <Show when={selected().text_truncated}><small>{text('abridged')}</small></Show>
          <Show when={['UNKNOWN','PROCESSING','REQUESTING','RUNNING'].includes(selected().status)&&props.onInspect&&inputInspection()!==selected().operation_id}><button type="button" disabled={inspecting()} onClick={()=>void inspect(selected().operation_id)}>{text('inspect')}</button></Show>
        </section>}</Show>
        <form onSubmit={event=>{event.preventDefault();void ask();}}>
          <textarea ref={input} aria-label={text('ask')} placeholder={text('ask')} value={draft()} rows={2} maxLength={8000}
            onInput={event=>changeDraft(event.currentTarget.value)} onCompositionStart={()=>{composing=true;}}
            onCompositionEnd={()=>{composing=false;}}
            onKeyDown={event=>{if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)&&!event.isComposing&&!composing){event.preventDefault();void ask();}else if(event.key==='Escape'&&!event.isComposing&&!composing){event.preventDefault();setOpen(false);}}}/>
          <div class="as-supervisor-command-form-actions"><Show when={(!props.onAsk||props.canAsk===false)&&props.onConfigure}><button type="button" onClick={()=>void props.onConfigure?.()}>{text('configure')}</button></Show>
            <Show when={inputInspection()&&props.onInspect}><button type="button" class="as-supervisor-command-input-inspect" disabled={inspecting()} onClick={()=>void inspect(inputInspection()!)}>{text('inspect')}</button></Show>
            <button type="submit" disabled={props.draftBlocked||!draft().trim()||(localDraft()?savingLocal():sending()||blocksDraft())}>{text(!localDraft()&&(sending()||blockedAdmission()?.code==='SUPERVISOR_OPERATION_PENDING')?'pending':'send')}</button></div>
        </form>
        <Show when={props.error}><div class="as-supervisor-command-error" role="alert"><p>{props.error}</p>
          <Show when={props.draftConflict&&props.onResolveDraftConflict} fallback={<Show when={props.draftRecovery} fallback={<Show when={props.onRefresh}><button type="button" onClick={()=>void props.onRefresh?.()}>{text('refresh')}</button></Show>}>{recovery=><Show when={recovery()==='RETRY_ALLOWED'} fallback={<button type="button" onClick={()=>void props.onCheckDraftSave?.()}>{text('checkDraftSave')}</button>}><button type="button" onClick={()=>void props.onRetryDraftSave?.()}>{text('retryDraftSave')}</button></Show>}</Show>}>
            <button type="button" onClick={()=>void props.onResolveDraftConflict?.('keep-local')}>{text('keepDraft')}</button>
            <button type="button" onClick={()=>void props.onResolveDraftConflict?.('use-remote')}>{text('readDraft')}</button>
          </Show>
        </div></Show>
        <Show when={!props.selectedOperation&&props.onSearchEarlier}><details class="as-supervisor-search" onToggle={event=>{if(!event.currentTarget.open){discussionRequest++;setSelecting(false);props.onResetSearch?.();}}}>
          <summary>{text('searchHistory')}</summary>
          <form onSubmit={event=>{event.preventDefault();if(!props.searchBusy&&searchQuery().trim())void props.onSearchEarlier?.(searchQuery());}}>
            <label>{text('searchWords')}<input type="search" value={searchQuery()} onInput={event=>{setSearchQuery(Array.from(event.currentTarget.value).slice(0,120).join(''));setDiscussionError(false);}}/></label>
            <button type="submit" disabled={!searchQuery().trim()||props.searchBusy}>{text('searchHistory')}</button>
          </form>
          <p class="as-supervisor-command-history-hint">{text('searchCoverage')}</p>
          <Show when={searchView()}>{found=><>
            <p role="status">{text('searchScanned',{scanned:found().scanned_records,total:found().total_records,matches:found().matched_records})}</p>
            <Show when={found().changed||found().error}><p role="status">{text(found().changed||found().error==='CHANGED'?'historyChanged':'historyUnavailable')}</p></Show>
            <Show when={found().windowed}><p>{text('searchWindow')}</p></Show>
            <div class="as-supervisor-search-results"><For each={found().results}>{hit=><article>
              <p>{hit.question}</p><p>{hit.answer_text}</p><Show when={hit.text_truncated}><small>{text('abridged')}</small></Show>
              <Show when={hit.reference&&props.onSelectDiscussion}><button type="button" disabled={selecting()||found().changed||!!found().error} onClick={()=>void selectDiscussion(hit.reference!)}>{text('continueDiscussion')}</button></Show>
            </article>}</For></div>
            <Show when={found().hasMore||found().changed||found().error}><button type="button" disabled={props.searchBusy} onClick={()=>void props.onSearchEarlier?.(searchQuery())}>{text(found().loading?'historyLoading':found().changed?'historyReadAgain':found().error?'historyRetry':'searchEarlier')}</button></Show>
          </>}</Show>
        </details></Show>
        <Show when={discussionError()}><p role="status">{text('discussionUnavailable')}</p></Show>
        <Show when={!props.selectedOperation&&(messageMap().size||props.history?.hasMore)}><div class="as-supervisor-command-history-region">
          <Show when={props.onReadEarlier&&(props.history?.hasMore||props.history?.loading||props.history?.changed||props.history?.error||props.history?.messages.length)}><div class="as-supervisor-command-history-tools">
            <Show when={props.history?.hasMore||props.history?.loading||props.history?.changed||props.history?.error}><button type="button" disabled={props.history?.loading} onClick={()=>void loadEarlier()}>{text(props.history?.loading?'historyLoading':props.history?.changed?'historyReadAgain':props.history?.error?'historyRetry':'historyEarlier')}</button></Show>
            <Show when={props.history?.messages.length&&props.onResetHistory}><button type="button" onClick={()=>{historyRequest++;props.onResetHistory?.();if(historyList)historyList.scrollTop=0;}}>{text('recentDiscussion')}</button></Show>
            <Show when={historyHint()}>{hint=><p class="as-supervisor-command-history-hint" role="status">{hint()}</p>}</Show>
          </div></Show>
          <div ref={historyList} class="as-supervisor-command-history as-supervisor-command-history-messages" aria-label={text('discussionHistory')} aria-live={props.history?.loading?'off':'polite'}>
          <For each={[...messageMap().keys()]}>{id=>{const message=()=>messageMap().get(id)!;return <article data-message-id={id} data-role={message().role}><p>{message().text}</p><Show when={message().text_truncated}><small>{text('abridged')}</small></Show>
          <Show when={message().role==='assistant'&&historyReference(message().operation_id)&&props.onSelectDiscussion}><button type="button" disabled={selecting()||props.history?.changed||!!props.history?.error} onClick={()=>void selectDiscussion(historyReference(message().operation_id)!)}>{text('continueDiscussion')}</button></Show>
          <Show when={message().operation_id&&message().operation_id!==inputInspection()&&message().status&&['UNKNOWN','PROCESSING','REQUESTING','RUNNING'].includes(message().status!)&&props.onInspect}><button type="button" disabled={inspecting()} onClick={()=>void inspect(message().operation_id!)}>{text('inspect')}</button></Show>
        </article>;}}</For></div></div></Show>
        <Show when={!localAnswerInHistory()?answer():undefined}>{value=><div class="as-supervisor-command-answer" role="status"><small>{text('local')}</small><p>{value().text}</p></div>}</Show>
        <Show when={reply()&&(!props.selectedOperation||reply()?.operation_id!==props.selectedOperation.operation_id)&&(props.selectedOperation||!(reply()?.operation_id&&props.messages?.some(message=>message.operation_id===reply()?.operation_id&&message.role==='assistant')))}>{_value=><div class="as-supervisor-command-answer" role="status" data-status={reply()!.status}><p>{reply()!.error==='SUPERVISOR_STORAGE_FULL'?text('askStorageFull'):reply()!.text||text(reply()!.status==='UNKNOWN'?'unknown':reply()!.status==='REJECTED'?'rejected':unresolved()?'pending':'noAnswer')}</p>
          <Show when={reply()?.text_truncated}><small>{text('abridged')}</small></Show>
          <Show when={reply()?.operation_id&&reply()?.operation_id!==inputInspection()&&unresolved()&&props.onInspect}><button type="button" disabled={inspecting()} onClick={()=>void inspect(reply()!.operation_id!)}>{text('inspect')}</button></Show>
        </div>}</Show>
        <footer><button type="button" onClick={()=>{void props.onOpenAll();setOpen(false);}}>{text('all')}</button></footer>
      </section>
    </StudioPopover>
  </div>;
}
