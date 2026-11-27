import {createSignal,Show} from 'solid-js';
import {BrandLoader} from './BrandLogo';
import TaskStatus from './TaskStatus';
import {t as uiText} from './ui-language';
import {operationRunning,operationStatus,type OperationState} from './operation-outcome';

export default function LiveOperationStatus(props:{operation:OperationState;purpose?:string;compact?:boolean;historicalRevision?:number;historical?:boolean;onBack?:()=>void;onStop:()=>Promise<unknown>;onInspect?:()=>Promise<unknown>;onError:(error:unknown)=>void}){
  const [checking,setChecking]=createSignal(false);
  const status=()=>operationStatus(props.operation),running=()=>operationRunning(props.operation);
  async function inspect(){if(checking())return;setChecking(true);try{await props.onInspect?.();}catch(error){props.onError(error);}finally{setChecking(false);}}
  return <div class="as-live-operation" data-compact={!!props.compact} role="status">
    <div class="as-live-operation-heading"><Show when={running()}><BrandLoader class="as-live-ring"/></Show><strong><TaskStatus value={status()}/></strong><Show when={props.historical}><small>{props.historicalRevision!=null?uiText('较早版本 {revision} 的处理',{revision:props.historicalRevision}):uiText('较早的处理记录')}</small></Show><Show when={props.onBack}><button type="button" class="as-operation-back" onClick={()=>{try{props.onBack?.();}catch(error){props.onError(error);}}}>{uiText('选择其他处理记录')}</button></Show></div>
    <Show when={running()} fallback={<Show when={props.onInspect}><button type="button" disabled={checking()} onClick={()=>void inspect()}>{uiText(checking()?'正在核查…':props.compact?'核查结果':'核查本次处理')}</button></Show>}><button type="button" disabled={status()==='CANCEL_REQUESTED'||props.historical} onClick={()=>void props.onStop().catch(props.onError)}>{uiText('停止')}</button></Show>
    <p>{uiText(props.historical?'仅核查旧记录，当前要求和输入保持不变。':running()?'正在处理所选资料。':['UNKNOWN_OUTCOME','TIMED_OUT','INTERRUPTED'].includes(status())?props.purpose==='PLAN'?'计划结果尚未确认，请核查原记录。':'原稿已保留。修改内容或引用后可发送新消息。':'本次处理已停止，原记录和输入已保留。')}</p>
  </div>;
}
