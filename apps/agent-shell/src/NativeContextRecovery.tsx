import {createMemo,createSignal,onCleanup,Show} from 'solid-js';
import type {NativeMessage} from './native-collaboration';
import type {CreationReceipt} from './conversation-creation';
import type {NativeContextRecoveryClient,NativeRecoveryPreparation,NativeRecoverySource} from './native-context-recovery';

export default function NativeContextRecovery(props:{
 message:NativeMessage;client:NativeContextRecoveryClient;version:number;locale:string;
 captureCurrent:(message:NativeMessage)=>()=>boolean;
 onOpen:(receipt:CreationReceipt,current:()=>boolean)=>Promise<boolean>;
}){
 const t=(zh:string,en:string)=>props.locale.startsWith('en')?en:zh;
 const [view,setView]=createSignal<{key:string;plan?:NativeRecoveryPreparation;error?:string}>();
 const [working,setWorking]=createSignal(false);let disposed=false;
 onCleanup(()=>{disposed=true;});
 const source=createMemo<NativeRecoverySource|undefined>(()=>{
  const recovery=props.message.native_context_recovery;
  return props.message.native_context_issue&&recovery?.parent_operation_id===props.message.operation_id&&recovery.read_only===true&&recovery.model_calls===0
   ?{parent_operation_id:recovery.parent_operation_id,expected_native_ref:recovery.expected_native_ref}:undefined;
 });
 const key=()=>JSON.stringify(source());
 const local=createMemo(()=>{
  props.version;const s=source();if(!s)return {attempt:null,unavailable:false};
  try{return {attempt:props.client.pending(s),unavailable:!props.client.supported()};}
  catch{return {attempt:null,unavailable:true};}
 });
 const display=()=>view()?.key===key()?view():undefined;
 const busy=()=>working()||!!source()&&props.client.busy(source()!);
 const complete=()=>local().attempt?.receipt?.phase==='COMPLETE';
 const retry=()=>{
  const a=local().attempt;
  return a?.phase==='NOT_FOUND'||a?.receipt?.phase==='STOP_CONFIRMED'||a?.receipt?.phase==='STOP_PENDING'&&a.receipt.stop_status==='NOT_FOUND';
 };
 async function act(kind:'prepare'|'start'|'inspect'|'continue'|'adopt'|'open'){
  const s=source();if(!s||busy()||local().unavailable)return;
  const selected=key(),navigation=props.captureCurrent(props.message);
  const current=()=>!disposed&&selected===key()&&navigation();
  const plan=display()?.plan;setWorking(true);setView({key:selected,plan});
  try{
   if(kind==='prepare'){
    const fresh=await props.client.prepare(s);if(current())setView({key:selected,plan:fresh});
   }else if(kind==='start'){
    if(!plan||!current()||props.message.native_context_recovery?.record_current!==true)return;
    await props.client.start(s,plan,current);
   }else if(kind==='inspect')await props.client.inspect(s);
   else if(kind==='continue')await props.client.continue(s,current);
   else if(kind==='adopt')await props.client.adoptExisting(s,current);
   else{
    const receipt=(await props.client.inspect(s)).creation_receipt;
    if(receipt&&current()&&await props.onOpen(receipt,current))await props.client.opened(s,receipt.conversation_ref.id);
   }
  }catch(error){
   if(current())setView({key:selected,plan,error:error instanceof Error&&error.message==='NATIVE_CONTEXT_RECOVERY_NOT_FOUND'
    ?t('尚未查到原恢复记录，不能据此判断旧执行是否停止。可明确重试同一恢复请求。','The original recovery record was not found. This does not prove the execution stopped. You can retry that same request.')
    :t('恢复尚未核实，旧记录与输入保留。请核查原结果；不会自动重发。','Recovery is not verified. Earlier records and input are kept. Check the original result; it will not be resent automatically.')});
  }finally{if(!disposed)setWorking(false);}
 }
 return <Show when={source()}><article class="as-pending-recovery" data-native-context-recovery role="status" aria-busy={busy()}>
  <p>{complete()?t('空白对话已创建，旧聊天与草稿留在原处。','The empty chat is ready. Earlier chat and drafts stay in place.')
   :props.message.native_context_issue?.code==='SOURCE_CONTEXT_UNAVAILABLE'?t('来源授权暂不可核对，原任务已阻止继续使用这些资料。','Source authority is unavailable. The original task is blocked from continuing with these sources.')
   :props.message.native_context_issue?.code==='SOURCE_CONTEXT_LEGACY'?t('此旧执行上下文无法完整核验，原记录保留。','This older execution context cannot be fully verified. Its records are kept.')
   :t('来源授权已变化，原任务已阻止继续使用这些资料。','Source authority changed. The original task is blocked from continuing with these sources.')}</p>
  <Show when={!local().attempt?display()?.plan:undefined}>{plan=><p>{t('新对话位置：','New chat location: ')}<strong style={{'overflow-wrap':'anywhere'}}>{plan().target_label??t('聊天','Chats')}</strong>{t('。确认停止旧执行后，新建空白对话；旧资料、草稿和权限不带入。',' — stop the old execution, then create an empty chat. Earlier sources, drafts and permissions stay in place.')}</p>}</Show>
  <Show when={local().attempt&&!complete()}><p>{local().attempt?.receipt?.phase==='STOP_CONFIRMED'
   ?t('旧执行已确认停止，可继续创建空白对话。','The old execution stopped. You can finish creating the empty chat.')
   :t('恢复结果待核查。旧操作与不确定结果保留，不重复执行原任务。','Recovery needs checking. Original operations and uncertain outcomes are kept; the original task is not repeated.')}</p></Show>
  <Show when={local().unavailable}><p role="alert">{t('恢复入口暂不可用，原记录与输入保留。','Recovery is unavailable. Original records and input are kept.')}</p></Show>
  <Show when={display()?.error}><p role="alert">{display()?.error}</p></Show>
  <div class="as-pending-actions">
   <Show when={!local().attempt&&props.message.native_context_recovery?.record_current===true}>
    <button type="button" disabled={busy()||local().unavailable} onClick={()=>void act(display()?.plan?'start':'prepare')}>{display()?.plan?.existing_recovery?t('核查已有恢复','Check existing recovery'):display()?.plan?t('停止旧执行并新建对话','Stop and start a new chat'):t('核对恢复范围','Review recovery scope')}</button>
   </Show>
   <Show when={local().attempt&&!complete()}><button type="button" disabled={busy()||local().unavailable} onClick={()=>void act('inspect')}>{t('核查原恢复结果','Check original recovery')}</button></Show>
   <Show when={retry()&&!complete()}><button type="button" disabled={busy()||local().unavailable} onClick={()=>void act('continue')}>{local().attempt?.receipt?.phase==='STOP_CONFIRMED'?t('继续创建空白对话','Finish creating the empty chat'):t('重试原恢复请求','Retry original recovery')}</button></Show>
   <Show when={local().attempt&&!complete()&&props.message.native_context_recovery?.record_current===true}><details><summary>{t('其他恢复记录','Other recovery records')}</summary><button type="button" disabled={busy()||local().unavailable} onClick={()=>void act('adopt')}>{t('查找同一执行的已有恢复','Find existing recovery for this execution')}</button></details></Show>
   <Show when={complete()}><button type="button" disabled={busy()||local().unavailable} onClick={()=>void act('open')}>{t('打开新对话','Open the new chat')}</button></Show>
  </div>
 </article></Show>;
}
