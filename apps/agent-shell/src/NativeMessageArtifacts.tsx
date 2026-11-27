import {createMemo,createSignal,For,Show} from 'solid-js';
import {t as uiText} from './ui-language';
import {nativeReportArtifact,nativeRevisionChanges} from './native-artifacts';
import type {NativeDocxRevisionArtifact,NativeDocxRevisionChange,NativeMessage,NativeMessageArtifact} from './native-collaboration';
import ContextUsage from './ContextUsage';

function ChangeRow(props:{change:NativeDocxRevisionChange}){
 return <li><p><small>{uiText('原文')}</small> <del>{props.change.quote||uiText('空文本')}</del></p><p><small>{uiText('修订')}</small> <ins>{props.change.replacement||uiText('删除此段文字')}</ins></p><Show when={props.change.reason}><p>{props.change.reason}</p></Show></li>;
}
function RevisionReview(props:{artifact:NativeDocxRevisionArtifact}){
 const [open,setOpen]=createSignal(false),[remaining,setRemaining]=createSignal(false),[evidence,setEvidence]=createSignal(false);
 const changes=createMemo(()=>nativeRevisionChanges(props.artifact));
 return <details class="as-native-revision-review" onToggle={event=>{setOpen(event.currentTarget.open);if(!event.currentTarget.open){setRemaining(false);setEvidence(false);}}}><summary>{uiText('修订明细（{count}）',{count:Array.isArray(props.artifact.review.changes)?props.artifact.review.changes.length:0})}</summary>
  <Show when={open()}><p>{uiText('依据提问时的保存稿生成；之后的人工修改未合并。')}</p>
   <Show when={changes()} fallback={<p class="as-inline-warning">{uiText('修订明细尚未核对，请在 Word 中审阅。')}</p>}>{items=><>
    <ol><For each={items().slice(0,20)}>{change=><ChangeRow change={change}/>}</For></ol>
    <Show when={items().length>20}><details class="as-native-revision-remaining" onToggle={event=>setRemaining(event.currentTarget.open)}><summary>{uiText('其余修订（{count}）',{count:items().length-20})}</summary><Show when={remaining()}><ol start={21}><For each={items().slice(20)}>{change=><ChangeRow change={change}/>}</For></ol></Show></details></Show>
   </>}</Show>
   <details class="as-native-revision-evidence" onToggle={event=>setEvidence(event.currentTarget.open)}><summary>{uiText('来源与修订回执')}</summary><Show when={evidence()}><pre class="as-json">{JSON.stringify({source_link_ref:props.artifact.source_link_ref,snapshot_ref:props.artifact.snapshot_ref,source_sha256:props.artifact.source_sha256,proposal_ref:props.artifact.proposal_ref,owner:props.artifact.owner,review:props.artifact.review},null,2)}</pre></Show></details>
  </Show>
 </details>;
}

/** Compact files in their original answer. Review disclosures are presentation
 * only: opening, re-inspection and all durable refs stay in the existing owner. */
export default function NativeMessageArtifacts(props:{message:NativeMessage;busy?:boolean;canOpen:(artifact:NativeMessageArtifact)=>boolean;
 onOpen:(artifact:NativeMessageArtifact)=>void;onInspect?:()=>void}){
 const revision=(artifact:NativeMessageArtifact)=>nativeReportArtifact(artifact,props.message)?.revision;
 return <>
  <ContextUsage summary={props.message.context_summary}/>
  <Show when={props.message.artifact_error?.message}>{error=><div class="as-native-artifact-error"><p class="as-inline-warning" role="alert">{uiText(error())}</p><Show when={props.onInspect}><button type="button" class="as-text-action" disabled={props.busy} onClick={()=>props.onInspect?.()}>{uiText('核查原操作')}</button></Show></div>}</Show>
  <Show when={!props.message.artifact_error&&props.message.artifact_status==='PREPARING'}><p class="as-muted" role="status">{uiText('正在生成修订稿…')}</p></Show>
  <Show when={!props.message.artifact_error&&props.message.artifact_status==='PROPOSED_NOT_PUBLISHED'}><p class="as-inline-warning" role="status">{uiText('修订提案已保留，尚未生成文件。')}</p></Show>
  <Show when={!props.message.artifact_error&&props.message.artifact_status==='NEEDS_CLARIFICATION'}><p class="as-inline-warning" role="status">{uiText('这段内容需要进一步确认，尚未生成修订文件。')}</p></Show>
  <Show when={!props.message.artifact_error&&props.message.artifact_status==='UNAVAILABLE'}><p class="as-inline-warning" role="status">{uiText('修订文件暂不可用，原回复与提案保留；请核查原操作，勿重复发送。')}</p></Show>
  <Show when={!props.message.artifact_error&&props.message.artifact_status==='CANCELLED_NOT_PUBLISHED'}><p class="as-muted" role="status">{uiText('已停止生成修订文件；原回复、原文件和提案保留。')}</p></Show>
  <Show when={!props.message.artifact_error&&props.message.artifact_status==='PUBLICATION_UNKNOWN'}><p class="as-inline-warning" role="status">{uiText('修订文件的生成结果尚未确认；请核查原操作，勿重新生成。')}</p><Show when={props.onInspect}><button type="button" class="as-text-action" disabled={props.busy} onClick={()=>props.onInspect?.()}>{uiText('核查原操作')}</button></Show></Show>
  <For each={props.message.artifacts??[]}>{artifact=><>
   <button type="button" class="as-native-artifact as-chat-artifact" disabled={props.busy||!props.canOpen(artifact)} onClick={()=>props.onOpen(artifact)}>
    <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><path d="M11.5 2.5H5.3A1.3 1.3 0 0 0 4 3.8v12.4a1.3 1.3 0 0 0 1.3 1.3h9.4a1.3 1.3 0 0 0 1.3-1.3V7m-4.5-4.5V7H16l-4.5-4.5ZM7 11h6m-6 3h4"/></svg>
    <span>{artifact.filename??artifact.id??uiText('实际产物')}<Show when={revision(artifact)}><small>{uiText('新修订稿 · 原稿未覆盖')}</small></Show></span>
    <small>{props.canOpen(artifact)?uiText('在原软件打开'):uiText('文件来源尚未返回')}</small>
   </button>
   <Show when={revision(artifact)}>{item=><RevisionReview artifact={item()}/>}</Show>
  </>}</For>
 </>;
}
