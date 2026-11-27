import ContextUsage from './ContextUsage';
import {For,Show} from 'solid-js';
import AssistantReply from './AssistantReply';
import {originalReadableParts,originalCompletedToolOnly,type OriginalMessage} from './agent-sessions';
import {stableItems} from './stable-items';
import {originalToolHasDisplayedResult} from './original-command-display';
import {t as uiText} from './ui-language';

/** Readable live content while the exact saved transcript catches up. */
export default function OriginalLiveMessage(props:{message:OriginalMessage;locale:string;displayedCommands?:ReadonlySet<string>}){
 const en=()=>props.locale==='en'||props.locale.startsWith('en-');
 // This describes the observed reply only. Whole-task termination still comes
 // from the exact operation receipt in OriginalTaskControls.
 const interrupted=()=>props.message.info.role==='assistant'&&(props.message.info.error as {name?:unknown}|undefined)?.name==='MessageAbortedError';
 const parts=stableItems(()=>originalReadableParts(props.message).map((part,index)=>({part,index})).filter(entry=>entry.part.kind!=='tool'||!originalToolHasDisplayedResult(props.message,entry.part.id,props.displayedCommands)),entry=>JSON.stringify([entry.part.id??null,entry.part.kind,entry.index]));
 const toolCaption=(status:'pending'|'running'|'completed'|'error'|'unknown')=>en()
  ?{pending:'Waiting',running:'In progress',completed:'Completed',error:'Incomplete',unknown:'Result needs checking'}[status]
  :{pending:'等待处理',running:'处理中',completed:'已完成',error:'未完成',unknown:'结果待核查'}[status];
 return <Show when={!originalCompletedToolOnly(props.message)&&(parts().length>0||props.message.info.error||props.message.info.role!=='assistant')}><article class="as-history-message" data-original-live-message={props.message.info.id}>
  <small>{props.message.info.role==='user'?uiText('你'):en()?'Assistant':'助手'}</small>
  <Show when={props.message.info.role==='user'&&props.message.input_file?.filename}>{filename=><p class="as-capability-result">{filename()}</p>}</Show>
  <Show when={props.message.info.role==='user'}><ContextUsage summary={props.message.context_summary}/></Show>
  <For each={parts()}>{entry=>{
   const text=()=>{const part=entry.part;return part.kind==='text'?part.text:undefined;};
   const filename=()=>{const part=entry.part;return part.kind==='file'?part.filename??(en()?'Attachment':'附件'):undefined;};
   const tool=()=>{const part=entry.part;return part.kind==='tool'?toolCaption(part.status):undefined;};
   return <>
    <Show when={text()}>{value=><Show when={props.message.info.role==='assistant'} fallback={<p>{value()}</p>}><AssistantReply text={value()}/></Show>}</Show>
    <Show when={filename()}>{value=><p class="as-capability-result">{value()}</p>}</Show>
    <Show when={tool()}>{value=><p class="as-capability-result">{uiText('工具执行记录')} · {value()}</p>}</Show>
   </>;
  }}</For>
  <Show when={props.message.info.error}><p role="status">{interrupted()?(en()?'This reply was interrupted. Your input is kept.':'本次回复已中断，原输入已保留。'):(en()?'Result needs checking. Your message is kept.':'处理结果待核查，原问题已保留。')}</p></Show>
 </article></Show>;
}
