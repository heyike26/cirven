import {createEffect,createSignal,For,Show,onCleanup} from 'solid-js';
import {agentSessionsApi,inspectOriginalOperation,originalOperationPage,type AgentSession,type OriginalOperationIndex,type OriginalOperationPage} from './agent-sessions';
import {authoredErrorCopy,authoredText,type UIErrorCopy} from './authored-ui-copy';
import {t as uiText} from './ui-language';
import './original-operation-recovery.css';

/** On-demand reader for the existing private index. It never sends or removes a request. */
export default function OriginalOperationRecovery(props:{session:AgentSession;active:boolean}){
 const [open,setOpen]=createSignal(false),[page,setPage]=createSignal<OriginalOperationPage>(),[results,setResults]=createSignal<Record<string,{status:string;message?:string;verified:boolean}>>({}),[pending,setPending]=createSignal(''),[error,setError]=createSignal<UIErrorCopy>('');
 const recordKey=(item:OriginalOperationIndex)=>JSON.stringify(item);
 let epoch=0,disposed=false;onCleanup(()=>{disposed=true;epoch++;});
 const scope=()=>JSON.stringify([props.session.project_id,props.session.session_ref]);let savedScope='';
 createEffect(()=>{const key=scope(),active=props.active;if(key!==savedScope||!active){savedScope=key;epoch++;setOpen(false);setPage(undefined);setResults({});setPending('');setError('');}});
 function load(offset=0){try{const value=originalOperationPage(localStorage,props.session,offset);setPage(value);setResults({});setError('');}catch(reason){setError(authoredErrorCopy(reason));}}
 async function inspect(item:OriginalOperationIndex){if(pending())return;const selected=props.session,key=scope(),token=epoch;setPending(recordKey(item));setError('');try{const value=await inspectOriginalOperation(agentSessionsApi,selected,item);if(!disposed&&props.active&&token===epoch&&key===scope())setResults(rows=>({...rows,[recordKey(item)]:{status:value.status,message:value.message,verified:!!value.receipt}}));}catch(reason){if(!disposed&&props.active&&token===epoch&&key===scope())setError(authoredErrorCopy(reason));}finally{if(!disposed&&token===epoch&&key===scope())setPending('');}}
 const status=(row:OriginalOperationIndex)=>{const value=results()[recordKey(row)];return !value?'尚未核查原结果':value.status==='NO_RECORD'?'未找到原回执，不能据此重发':!value.verified?'原结果暂不可核对':['UNKNOWN','UNKNOWN_OUTCOME','RECORDED_NOT_DISPATCHED','PENDING'].includes(value.status)?'原结果仍待核查':['FAILED','CANCELLED','STOPPED','REJECTED'].includes(value.status)?'原操作未完成，记录保留':'原回执已读取，请核对工作结果';};
 return <details class="as-original-operation-recovery" open={open()} onToggle={event=>{if(event.target!==event.currentTarget)return;const value=event.currentTarget.open;setOpen(value);if(value&&!page())load();}}><summary>{uiText('本机原输入与结果核查')}</summary>
  <Show when={open()}><p class="as-muted">{uiText('这里只读取原编号的回执，不重新发送、连接模型或删除记录。')}</p>
   <Show when={error()}><p class="as-inline-error" role="alert">{authoredText(error(),uiText)}</p></Show>
   <div class="as-original-operation-actions"><button type="button" disabled={!!pending()} onClick={()=>load(page()?.offset??0)}>{uiText('重新读取本机索引')}</button><Show when={page()}>{value=><span>{uiText('保留 {count} 条原输入',{count:value().total})}</span>}</Show></div>
   <Show when={page()?.entries.length} fallback={<Show when={page()&&!error()}><p>{uiText('此会话暂无本机原输入记录。')}</p></Show>}><For each={page()!.entries}>{item=><article>
    <p>{Array.from(item.input).slice(0,240).join('')}{Array.from(item.input).length>240?'…':''}</p><p class="as-muted" role="status">{uiText(status(item))}</p>
    <Show when={results()[recordKey(item)]?.message}><p class="as-inline-warning">{uiText(results()[recordKey(item)]!.message!)}</p></Show>
    <button type="button" disabled={!!pending()} onClick={()=>void inspect(item)}>{uiText(pending()===recordKey(item)?'核查中…':'核查此条原结果')}</button>
    <details><summary>{uiText('查看完整原输入与编号')}</summary><p class="as-original-operation-text">{item.input}</p><p>{uiText('原操作编号')} · <code>{item.operation_id}</code></p><Show when={item.generation}><p>{uiText('原连接代次')} · {item.generation}</p></Show><Show when={item.conversation_input?.originalWorkRef}><p>{uiText('此输入明确关联了原工作。')}</p></Show><Show when={item.conversation_input?.fileInput}><p>{uiText('此输入保留了原文件引用。')}</p></Show><Show when={item.conversation_input?.inputAttachments?.length}><p>{uiText('此输入保留了原附件引用。')}</p></Show></details>
   </article>}</For><div class="as-original-operation-actions"><button type="button" disabled={!!pending()||!page()?.offset} onClick={()=>load(Math.max(0,page()!.offset-8))}>{uiText('较新记录')}</button><button type="button" disabled={!!pending()||page()?.next_offset===null} onClick={()=>load(page()!.next_offset!)}>{uiText('较早记录')}</button></div></Show>
  </Show>
 </details>;
}
