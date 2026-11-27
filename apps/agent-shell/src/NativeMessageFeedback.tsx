import {createSignal,onCleanup,Show} from 'solid-js';
import type {NativeMessage} from './native-collaboration';
import {authoredText,type UIErrorCopy} from './authored-ui-copy';
import {t as uiText} from './ui-language';
import AssistantReply from './AssistantReply';
import TaskStatus from './TaskStatus';

export function nativeFeedbackBody(message:NativeMessage):string|undefined{
  if(typeof message.text==='string'&&message.text.trim())return message.text;
  if(typeof message.summary==='string'&&message.summary.trim())return message.summary;
}
export function nativeFeedbackNeedsInspection(message:NativeMessage,notice?:UIErrorCopy):boolean{
  return !!notice||['NO_RECORD','NOT_FOUND','INSPECTION_UNAVAILABLE','UNKNOWN_OUTCOME','NOT_DISPATCHED'].includes(message.status);
}

export function nativeFeedbackStop(message:NativeMessage):string|undefined{
  const stop=message.native_execution?.stop;if(!stop)return;
  if(stop.reason==='OWNER_DISCONNECTED_NO_POST_REPLAY')return stop.owner_stop_status==='EXPIRED'?'本次执行已达到时限，结果仍待核查。':'原执行连接已断开，结果仍待核查。';
  if(stop.reason==='OWNER_LOST_NO_AUTOMATIC_REPLAY')return '原执行连接已断开，结果仍待核查。';
  if(stop.reason==='MESSAGE_DEADLINE_ABORT_REQUESTED')return '本次执行已到时限，已请求停止，结果仍待核查。';
  if(stop.reason==='ORIGINAL_OBSERVATION_UNAVAILABLE_NO_POST_REPLAY')return '暂时无法核查原执行，结果仍待核查。';
}

export default function NativeMessageFeedback(props:{message:NativeMessage;replyText?:string;notice?:UIErrorCopy;
  busy?:boolean;canInspect:boolean;scopeKey:string;onInspect:()=>Promise<unknown>;onOpenLink:(href:string)=>Promise<void>}){
  const [reading,setReading]=createSignal(false);let disposed=false,root:HTMLDivElement|undefined,trigger:HTMLButtonElement|undefined,releaseFocus:(()=>void)|undefined;
  onCleanup(()=>{disposed=true;releaseFocus?.();});
  const needsInspection=()=>nativeFeedbackNeedsInspection(props.message,props.notice);
  async function inspect(){
    if(reading()||props.busy||!props.canInspect)return;
    const clicked=trigger,focused=clicked===document.activeElement,id=props.message.operation_id,scope=props.scopeKey;
    let ownsFocus=focused;const moved=(event:FocusEvent)=>{if(event.target!==clicked)ownsFocus=false;};
    const release=()=>{document.removeEventListener('focusin',moved,true);if(releaseFocus===release)releaseFocus=undefined;};
    releaseFocus?.();if(focused){document.addEventListener('focusin',moved,true);releaseFocus=release;}
    setReading(true);
    try{await props.onInspect();}
    finally{
      if(!disposed){setReading(false);if(focused)requestAnimationFrame(()=>{
        release();if(!ownsFocus||disposed||!root?.isConnected||props.message.operation_id!==id||props.scopeKey!==scope||!clicked||document.activeElement!==document.body)return;
        if(needsInspection()){if(clicked.isConnected&&root.contains(clicked))clicked.focus({preventScroll:true});return;}
        if(clicked.isConnected)return;
        const card=root.closest('.as-native-message');
        const target=card?.querySelector<HTMLElement>('.as-native-artifact:not(:disabled)')??root.querySelector<HTMLElement>(nativeFeedbackBody(props.message)?'[data-native-feedback-body]':'[data-native-feedback-status]');
        target?.focus({preventScroll:true});
      });else release();}else release();
    }
  }
  return <div ref={root} class="as-native-feedback">
    <div data-native-feedback-status tabIndex={-1}><TaskStatus value={props.message.status==='SUCCEEDED'?'RETURNED':props.message.status}/></div>
    <div data-native-feedback-body tabIndex={-1}>
      <Show when={props.replyText} fallback={<Show when={nativeFeedbackBody(props.message)}>{body=><p>{body()}</p>}</Show>}>{text=><AssistantReply text={text()} onOpenLink={props.onOpenLink}/>}</Show>
    </div>
    <Show when={nativeFeedbackStop(props.message)}>{reason=><p role="status">{uiText(reason())}</p>}</Show>
    <Show when={props.notice}>{notice=><p role="status" class="as-inline-warning">{authoredText(notice(),uiText)}</p>}</Show>
    <Show when={typeof props.message.file_review_status==='string'&&props.message.file_review_status!=='CURRENT'}><p role="status" class="as-inline-warning">{uiText('固定文件需重新核对')}</p></Show>
    <Show when={needsInspection()}>
      <Show when={!nativeFeedbackBody(props.message)&&!props.notice}><p role="status">{uiText('请核查原操作，勿重复发送。')}</p></Show>
      <button ref={trigger} type="button" class="as-text-action" aria-disabled={reading()||props.busy||!props.canInspect} aria-busy={reading()} onClick={()=>void inspect()}>{uiText(reading()?'核查中…':'核查原操作')}</button>
    </Show>
    <details class="as-native-feedback-details">
      <summary>{uiText('查看执行详情')}</summary>
      <dl style={{margin:'8px 0','overflow-wrap':'anywhere'}}>
        <dt>{uiText('原操作编号：')}</dt><dd style={{margin:'0 0 8px'}}>{props.message.operation_id}</dd>
        <dt>{uiText('执行状态')}</dt><dd style={{margin:'0 0 8px'}}>{props.message.status}</dd>
        <Show when={props.message.execution?.session_id}>{id=><><dt>{uiText('会话')}</dt><dd style={{margin:'0 0 8px'}}>{id()}</dd></>}</Show>
        <Show when={props.message.file_review_status}>{status=><><dt>{uiText('固定文件状态')}</dt><dd style={{margin:'0 0 8px'}}>{status()}</dd></>}</Show>
      </dl>
      <Show when={props.message.native_execution}>{execution=><pre class="as-json">{JSON.stringify(execution(),null,2)}</pre>}</Show>
      <Show when={props.message.error?.message}>{detail=><pre class="as-json" style={{'max-height':'min(240px, 35vh)','overflow':'auto','white-space':'pre-wrap','overflow-wrap':'anywhere'}}>{detail()}</pre>}</Show>
      <Show when={props.message.reason!==props.message.error?.message?props.message.reason:undefined}>{reason=><pre class="as-json" style={{'max-height':'min(240px, 35vh)','overflow':'auto','white-space':'pre-wrap','overflow-wrap':'anywhere'}}>{reason()}</pre>}</Show>
    </details>
  </div>;
}
