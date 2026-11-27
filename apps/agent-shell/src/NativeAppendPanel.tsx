import {authoredErrorCopy,authoredText,type UIErrorCopy} from './authored-ui-copy';
import {t as uiText} from './ui-language';
import {createEffect,createMemo,createSignal,For,onCleanup,Show,untrack} from 'solid-js';
import {sameNativeReference,type NativeRef} from './native-collaboration';
import {appendActionRequest,appendActions,appendNeedsTargetReview,appendPending,appendTakeoverNotice,nativeAppendBridge,NativeAppendClient,NativeAppendRequests,type AppendPrepare,type AppendStorage,type AppendView,type NativeAppendBridge,type NativeAppendState} from './native-append';
import {appendCopy,appendStatusCopy} from './native-append-copy';
import './native-append.css';

export function NativeAppendResult(props:{view:AppendView;busy:boolean;contextRef?:NativeRef;onApprove:()=>void;onInspect:()=>void;onDismiss?:()=>void;onConfigureModel?:()=>void}){
  const copy=appendCopy,state=()=>props.view.snapshot,actions=()=>appendActions(props.view,props.contextRef);
  return <article class="as-append-card" data-append-operation={props.view.operation.operationId} data-append-state={state()?.state??props.view.review} aria-label={copy().preparingLabel}>
    <header><strong>{appendStatusCopy(state())}</strong></header>
    <Show when={state()?.target_label}><p class="as-append-target" title={state()!.target_label}>{state()!.target_label}</p></Show>
    <Show when={state()?.payload_text!==undefined}><pre class="as-append-payload" aria-label={copy().payload}>{state()!.payload_text}</pre></Show>
    <Show when={props.view.origin==='RECOVERED'}><p class="as-append-note">{copy().recovered}</p></Show>
    <Show when={props.view.origin==='LIVE'&&props.contextRef&&!sameNativeReference(props.contextRef,props.view.operation.contextRef)}><p class="as-append-note as-append-warning">{copy().changed}</p></Show>
    <Show when={props.view.origin==='LIVE'&&!props.contextRef&&state()?.state==='PREPARED'}><p class="as-append-note as-append-warning">{copy().noContext}</p></Show>
    <Show when={appendNeedsTargetReview(props.view)}><p class="as-append-note as-append-warning" role="status">{copy().unverified}</p></Show>
    <Show when={state()?.provider_state==='NOT_CONFIGURED'}><div class="as-append-actions"><span class="as-append-note">{copy().model}</span><Show when={props.onConfigureModel}><button type="button" onClick={props.onConfigureModel}>{copy().configure}</button></Show></div></Show>
    <Show when={actions().approve}><p class="as-append-note">{copy().scope}</p></Show>
    <div class="as-append-actions">
      <Show when={actions().approve}><button class="as-append-primary" type="button" disabled={props.busy} onClick={props.onApprove}>{copy().review}</button></Show>
      <Show when={props.onDismiss}><button type="button" onClick={props.onDismiss}>{actions().approve?copy().dismiss:copy().collapse}</button></Show>
      <Show when={!actions().approve}><button type="button" disabled={props.busy} onClick={props.onInspect}>{copy().inspect}</button></Show>
    </div>
    <details><summary>{copy().details}</summary><pre>{JSON.stringify({operation_id:props.view.operation.operationId,context_ref:props.view.operation.contextRef,preview_ref:state()?.preview_ref,approval_ref:state()?.approval_ref,qualification_ref:state()?.qualification_ref,payload_sha256:props.view.operation.payloadSha256,status:state()?.state,reason:state()?.reason,message:authoredText(props.view.message_copy??props.view.message??'',uiText),permission_state:state()?.permission_state,activation_state:state()?.activation_state,task_status:state()?.task_status,task_completed:state()?.task_completed,automatic_retry:false},null,2)}</pre></details>
  </article>;
}

export type NativeAppendPanelProps={active?:boolean;visible?:boolean;rawText?:string;contextRef?:NativeRef;providerProfileRef?:NativeRef;request?:AppendPrepare;diagnostics?:boolean;onDismiss?:()=>void;onConfigureModel?:()=>void;bridge?:NativeAppendBridge;storage?:AppendStorage};

export default function NativeAppendPanel(props:NativeAppendPanelProps){
  const copy=appendCopy,bridge=createMemo(()=>props.bridge??nativeAppendBridge(globalThis));
  const client=createMemo(()=>{const transport=bridge(),storage=props.storage??globalThis.localStorage;return transport&&storage?new NativeAppendClient(transport,storage):undefined;});
  const requests=createMemo(()=>{const value=client();return value?new NativeAppendRequests(value):undefined;});
  const intent=createMemo(()=>{try{return props.request?{identity:JSON.stringify(appendActionRequest(props.request)),error:''}:{identity:undefined,error:''};}catch(error){return{identity:undefined,error:authoredErrorCopy(error)};}});
  const identity=createMemo(()=>intent().identity),[view,setView]=createSignal<AppendView>(),[preparing,setPreparing]=createSignal(false),[busy,setBusy]=createSignal(''),[error,setError]=createSignal<UIErrorCopy>(''),[notice,setNotice]=createSignal('');
  const [historyOpen,setHistoryOpen]=createSignal(false),[history,setHistory]=createSignal<AppendView[]>([]),[capability,setCapability]=createSignal<NativeAppendState>();
  let alive=true,generation=0;onCleanup(()=>{alive=false;generation++;});
  const accept=(value:AppendView)=>setHistory(items=>[...items.filter(item=>item.operation.operationId!==value.operation.operationId),value].slice(-8));
  async function perform(label:string,work:()=>Promise<void>){if(props.active===false||props.visible===false||busy()||!client())return;const key=identity();setBusy(label);setError('');setNotice('');try{await work();}catch(reason){if(alive&&key===identity())setError(authoredErrorCopy(reason));}finally{if(alive)setBusy('');}}
  createEffect(()=>{const problem=intent().error;if(problem)setError(problem);});
  createEffect(()=>{
    const key=identity(),active=props.active!==false,controller=requests(),current=++generation;
    setView(undefined);setPreparing(false);
    if(!active||!key||!controller)return;
    setError('');setNotice('');setPreparing(true);
    void controller.prepare(JSON.parse(key)).then(value=>{if(!alive)return;accept(value);if(current===generation)setView(value);}).catch(reason=>{if(alive&&current===generation)setError(authoredErrorCopy(reason));}).finally(()=>{if(alive&&current===generation)setPreparing(false);});
  });
  async function inspect(value:AppendView){const key=identity(),result=await client()!.inspect(value.operation,value.origin);if(!alive)return;accept(result);if(key===identity()&&view()?.operation.operationId===result.operation.operationId)setView(result);}
  async function restore(){const current=client();if(!current)return;const epoch=generation,context=props.contextRef;const [state,values]=await Promise.all([current.current(context),current.restore(8)]);if(!alive||epoch!==generation||context!==props.contextRef&&!sameNativeReference(context,props.contextRef))return;setCapability(state);setHistory(values);}
  createEffect(()=>{props.contextRef;setCapability(undefined);if(props.active!==false&&props.diagnostics&&historyOpen()&&client())untrack(()=>void perform(copy().inspecting,restore));});
  createEffect(()=>{const value=view();if(props.active===false||props.visible===false||busy()||!value||value.review!=='OBSERVED'||!appendPending(value.snapshot))return;const timer=setTimeout(()=>void perform(copy().inspecting,()=>inspect(value)),1800);onCleanup(()=>clearTimeout(timer));});
  async function approve(value:AppendView){const key=identity(),context=props.contextRef;if(!context||!appendActions(value,context).approve)return;const result=await client()!.approve(value,context);if(!alive)return;accept(result);if(key===identity()&&view()?.operation.operationId===result.operation.operationId)setView(result);}
  async function takeover(){const result=await client()!.revoke();if(alive)setNotice(appendTakeoverNotice(result,uiText));await restore();}
  return <Show when={props.request||props.diagnostics}><section class="as-append-action" aria-label={copy().title}>
    <Show when={props.request}>
      <Show when={!client()}><p class="as-append-note" role="status">{copy().noBridge}</p></Show>
      <Show when={preparing()}><div class="as-append-card" role="status"><strong>{copy().preparing}</strong><Show when={props.onDismiss}><div class="as-append-actions"><button type="button" onClick={props.onDismiss}>{copy().dismiss}</button></div></Show></div></Show>
      <Show when={view()}>{value=><NativeAppendResult view={value()} busy={!!busy()} contextRef={props.contextRef} onDismiss={props.onDismiss} onConfigureModel={props.onConfigureModel} onApprove={()=>void perform(copy().approving,()=>approve(value()))} onInspect={()=>void perform(copy().inspecting,()=>inspect(value()))}/>}</Show>
    </Show>
    <Show when={error()}><p class="as-append-note as-append-error" role="alert">{authoredText(error(),uiText)}</p></Show>
    <Show when={props.request&&!view()&&!preparing()&&props.onDismiss}><div class="as-append-actions"><button type="button" onClick={props.onDismiss}>{copy().dismiss}</button></div></Show>
    <Show when={busy()}><p class="as-append-note" role="status">{busy()}</p></Show>
    <Show when={notice()}><p class="as-append-note" role="status">{notice()}</p></Show>
    <Show when={props.diagnostics}><details class="as-append-history" onToggle={event=>setHistoryOpen(event.currentTarget.open)}><summary>{copy().history}</summary><div class="as-append-history-body">
      <Show when={capability()}>{state=><p class="as-append-note">{appendStatusCopy(state())}</p>}</Show>
      <Show when={!history().length}><p class="as-append-note">{copy().empty}</p></Show>
      <For each={history().toReversed()}>{item=><NativeAppendResult view={{...item,origin:'RECOVERED'}} busy={!!busy()} contextRef={props.contextRef} onApprove={()=>{}} onInspect={()=>void perform(copy().inspecting,()=>inspect(item))}/>}</For>
      <div class="as-append-actions"><button type="button" disabled={!!busy()||!client()} onClick={()=>void perform(copy().inspecting,restore)}>{copy().refresh}</button><button type="button" disabled={!!busy()||!client()} onClick={()=>void perform(copy().takingOver,takeover)}>{copy().takeover}</button></div>
    </div></details></Show>
  </section></Show>;
}
