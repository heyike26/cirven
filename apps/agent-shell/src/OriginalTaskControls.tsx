import {createEffect,createSignal,Show,untrack} from 'solid-js';
import {originalSessionCanStop,type AgentSession} from './agent-sessions';
import {OriginalTaskStops,type TaskStopStorage,type TaskStopClient} from './original-task-stop';
import type {ControlLocks} from './original-pending-controls';
import {t as uiText} from './ui-language';

export default function OriginalTaskControls(props:{session?:AgentSession;onUpdated:(s:AgentSession)=>void;onFinished?:()=>void;storage?:TaskStopStorage;client?:TaskStopClient;locks?:ControlLocks}){
 let host:HTMLElement|undefined;
 let focusOwner='',idleChecked='';
 const[version,setVersion]=createSignal(0),[busy,setBusy]=createSignal(false),[error,setError]=createSignal('');
 const stops=new OriginalTaskStops(props.storage??{getItem:key=>localStorage.getItem(key),setItem:(key,value)=>localStorage.setItem(key,value)},props.client,props.locks??(typeof navigator==='undefined'?undefined:navigator.locks));
 const target=()=>props.session?.active_operation_id??props.session?.work_mode_message?.operation_id;
 const state=()=>{version();try{const s=props.session,id=target();return {attempt:s&&id?stops.read(s,id):undefined,broken:false};}catch{return {attempt:undefined,broken:true};}};
 const identity=(s:AgentSession,id:string)=>JSON.stringify([s.project_id,s.session_ref.id,s.session_ref.revision,s.session_ref.sha256,s.generation,id]);
 const same=(s:AgentSession,id:string)=>!!props.session&&target()===id&&identity(props.session,id)===identity(s,id);
 async function act(manual=true){
  const s=props.session,id=target();if(!s||!id||busy()||state().broken)return;
  const initiatedHere=typeof document!=='undefined'&&!!host?.contains(document.activeElement);
  if(manual&&initiatedHere)focusOwner=identity(s,id);
  setBusy(true);setError('');
  try{
   if(!state().attempt)await stops.stop(s,id);
   if(!same(s,id))return;
   setVersion(v=>v+1);
   const next=await stops.inspect(s,id);
   if(same(s,id)&&next.generation===s.generation){
    const finished=!next.active_operation_id&&['CANCELLED','RETURNED'].includes(stops.read(s,id)?.targetStatus??'');
    // Disabling the clicked button may move focus to body while the request runs.
    // Preserve a later deliberate focus change to another input or task.
    const returnFocus=finished&&focusOwner===identity(s,id)&&(document.activeElement===document.body||host?.contains(document.activeElement));
    props.onUpdated(next);if(returnFocus)props.onFinished?.();if(finished)focusOwner='';
   }
  }catch{if(same(s,id))setError(uiText('停止结果尚未核实；保留原请求，只核查，不重复停止。'));}
  finally{setVersion(v=>v+1);setBusy(false);}
 }
 const terminal=()=>state().attempt?.targetStatus;
 const stopping=()=>state().attempt?.acknowledged&&originalSessionCanStop(props.session)
  &&!!props.session?.active_operation_id&&['busy','retry','DISPATCHING'].includes(props.session.native_status);
 createEffect(()=>{
  const s=props.session,id=target(),a=state().attempt;
  if(!s||!id||s.active_operation_id||!a||busy()||['CANCELLED','RETURNED'].includes(a.targetStatus??''))return;
  const stamp=identity(s,id)+a.key;
  if(idleChecked!==stamp){idleChecked=stamp;untrack(()=>void act(false));}
 });
 const visible=()=>!!props.session&&(!!props.session.active_operation_id&&originalSessionCanStop(props.session)||!!state().attempt||state().broken);
 return <Show when={visible()}><section ref={host} class="as-original-task-controls" aria-label={uiText('当前任务控制')}>
  <span role="status">{uiText(terminal()==='CANCELLED'?'当前任务已停止':terminal()==='RETURNED'?'当前任务已返回':stopping()?'正在停止当前任务':state().attempt?'停止结果待核查':'正在处理当前任务')}</span>
  <Show when={!['CANCELLED','RETURNED'].includes(terminal()??'')}><button type="button" disabled={busy()||state().broken||!state().attempt&&!originalSessionCanStop(props.session)} onClick={()=>void act()}>{uiText(busy()?'正在核查…':state().attempt?'核查停止结果':'停止当前任务')}</button></Show>
  <Show when={state().broken||error()}><p role="alert">{error()||uiText('停止记录暂未核实，未重复请求')}</p></Show>
 </section></Show>;
}
