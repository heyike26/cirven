import ContextUsage from './ContextUsage';
import {For,Show,createResource} from 'solid-js';
import AssistantReply from './AssistantReply';
import type {ThreadMessage} from './conversation-thread';
import type {NativeInputAttachment} from './native-collaboration';
import './conversation-transcript.css';

export default function ConversationTranscript(props:{messages:ThreadMessage[];locale:string;more:boolean;loading:boolean;error?:string;displayVersion:number;onMore:()=>void;onRetry:()=>void;onInput:(item:NativeInputAttachment)=>void;onResult:(row:ThreadMessage)=>void;onDocument?:(row:ThreadMessage)=>void;resolveUser:(row:ThreadMessage)=>Promise<string>;liveText:(row:ThreadMessage)=>string;onModelConnection?:()=>void}){
 const label=(zh:string,en:string)=>props.locale.startsWith('en')?en:zh;
 const taskCaption=(row:ThreadMessage)=>['PROCESSING','ACCEPTED','REQUESTING','DISPATCHING'].includes(row.status??'')?label('处理中','In progress'):row.status==='UNKNOWN_OUTCOME'?label('结果待核查','Result needs checking'):row.status==='NOT_CONFIGURED'?label('需要连接模型','Connect a model'):['FAILED','UNAVAILABLE','NOT_DISPATCHED','CANCELLED'].includes(row.status??'')?label('未完成，输入已保留','Not completed; your input is kept'):'';
 function User(propsRow:{row:ThreadMessage}){
  const [display]=createResource(()=>propsRow.row.native_feedback_ref?JSON.stringify([propsRow.row.id,propsRow.row.text_sha256,props.displayVersion]):false,()=>props.resolveUser(propsRow.row));
  return <div class="as-user-message"><div>{propsRow.row.native_feedback_ref?(display()??label('正在核对原反馈…','Checking the original feedback…')):propsRow.row.text}</div>
   <Show when={propsRow.row.input_file}>{file=><div class="as-thread-inputs"><span class="as-thread-file" title={file().verified?file().filename:undefined}>{file().verified?file().filename:label('关联文件待核对','Attached file needs checking')}</span></div>}</Show>
   <Show when={propsRow.row.input_attachments?.length}><div class="as-thread-inputs"><For each={propsRow.row.input_attachments}>{item=><span class="as-thread-input"><button type="button" title={item.label} onClick={()=>props.onInput(item)}>{item.label}</button><Show when={item.user_note}><span>{item.user_note}</span></Show></span>}</For></div></Show>
   <Show when={taskCaption(propsRow.row)}><small class="as-thread-task-caption">{taskCaption(propsRow.row)}</small></Show>
   <Show when={propsRow.row.source.kind==='native'&&propsRow.row.operation_id&&propsRow.row.request_sha256&&!props.messages.some(item=>item.role==='assistant'&&item.source.alias_id===propsRow.row.source.alias_id&&item.operation_id===propsRow.row.operation_id)}><button type="button" class="as-thread-result" onClick={()=>props.onResult(propsRow.row)}>{label('查看任务','View task')}</button></Show>
  </div>;
 }
 return <section class="as-thread-transcript" aria-label={label('当前会话','Current conversation')}>
  <Show when={props.more}><button type="button" class="as-thread-earlier" disabled={props.loading} onClick={props.onMore}>{label('查看更早的内容','Earlier messages')}</button></Show>
  <Show when={props.error}><div class="as-thread-read-error" role="status"><span>{props.error}</span><button type="button" onClick={props.onRetry} disabled={props.loading}>{label('重新读取','Read again')}</button></div></Show>
  <For each={props.messages}>{row=><article class="as-thread-message" data-reading-anchor={row.id} data-role={row.role} data-operation-id={row.operation_id??''} data-source-project-id={row.source.project_id??''} data-source-session-id={row.source.session_id??''}>
   <Show when={row.role==='user'} fallback={<Show when={row.discovery_review} fallback={<><Show when={row.failure_code==='MODEL_AUTH_REQUIRED'} fallback={<AssistantReply text={props.liveText(row)}/>}><p class="as-inline-warning" role="status">{label('模型连接已失效，本次请求未能完成。原问题已保留，请检查模型连接后继续。','The model connection is no longer valid. Your request is kept. Reconnect the model before continuing.')}</p><Show when={props.onModelConnection}><button type="button" class="as-thread-result" onClick={()=>props.onModelConnection?.()}>{label('检查模型连接','Check model connection')}</button></Show></Show><Show when={row.source.kind==='native'&&row.operation_id}><button type="button" class="as-thread-result" onClick={()=>props.onResult(row)}>{label('查看结果与产物','View result and files')}</button></Show></>}>
    <section class="as-thread-document"><header><strong><Show when={props.onDocument&&row.object_ref} fallback={label('需求记录','Requirements record')}><button type="button" class="as-thread-document-open" aria-label={label('打开需求记录版本 ','Open requirements revision ')+row.object_ref?.revision} onClick={()=>props.onDocument?.(row)}>{label('需求记录','Requirements record')}</button></Show></strong><span>{label('版本','Revision')} {row.object_ref?.revision}</span></header><AssistantReply text={row.text}/>
     <Show when={row.discovery_review?.claims.length}><ul><For each={row.discovery_review!.claims}>{claim=><li>{claim.text}</li>}</For></ul></Show>
     <Show when={row.discovery_review?.questions.length}><div class="as-thread-document-questions"><strong>{label('待确认','To clarify')}</strong><ul><For each={row.discovery_review!.questions}>{question=><li>{question.text}</li>}</For></ul></div></Show>
    </section>
   </Show>}><User row={row}/></Show>
   <Show when={row.role==='user'}><ContextUsage summary={row.context_summary}/></Show>
   <Show when={row.text_truncated}><small>{label('此处显示部分内容。','Part of the saved content is shown.')}</small></Show>
  </article>}</For>
  <Show when={props.loading&&!props.messages.length}><p role="status">{label('正在读取会话…','Reading conversation…')}</p></Show>
 </section>;
}
