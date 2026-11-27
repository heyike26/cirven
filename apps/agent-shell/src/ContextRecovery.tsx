import {Show} from 'solid-js';
import {contextAuthorityNotice,type AgentSession} from './agent-sessions';
import type {ContextRecoveryAttempt} from './context-recovery';

export default function ContextRecovery(props:{session:AgentSession;attempt:ContextRecoveryAttempt|null;busy:boolean;unavailable:boolean;ready:boolean;retryStop:boolean;error:string;locale:string;onStart:()=>void;onInspect:()=>void;onContinue:()=>void;onOpen:()=>void}){
 const t=(zh:string,en:string)=>props.locale.startsWith('en')?en:zh;
 const complete=()=>props.attempt?.phase==='CONFIRMED'||props.attempt?.phase==='ADOPTED';
 return <article class="as-pending-recovery" data-source-context-recovery role="status">
  <p>{complete()?t('新的对话已创建。旧聊天与草稿留在原处。','The new chat is ready. The earlier chat and drafts stay in place.'):contextAuthorityNotice(props.session,props.locale.startsWith('en'))}</p>
  <Show when={!props.attempt}><p>{t('可先停止旧执行，再新建空白对话并重新选择资料。','Stop the old execution, then start an empty chat and select sources again.')}</p></Show>
  <Show when={props.attempt?.phase==='ATTEMPTED'}><p>{props.ready?t('旧执行已确认停止，可继续创建空白对话。','The old execution stopped. You can finish creating the empty chat.'):t('恢复结果待核查；旧操作不会重发。','The recovery needs checking. The old operation will not be resent.')}</p></Show>
  <Show when={props.unavailable||props.error}><p role="alert">{props.error||t('恢复记录暂时无法核验，当前输入保留。','The recovery record could not be checked. Your input is kept.')}</p></Show>
  <div class="as-pending-actions">
   <Show when={!props.attempt}><button type="button" disabled={props.busy||props.unavailable} onClick={props.onStart}>{props.busy?t('正在处理…','Working…'):t('停止旧执行并新建对话','Stop and start a new chat')}</button></Show>
   <Show when={props.attempt?.phase==='ATTEMPTED'&&!props.ready}><button type="button" disabled={props.busy||props.unavailable} onClick={props.onInspect}>{props.busy?t('正在核查…','Checking…'):t('核查原恢复结果','Check original recovery')}</button></Show>
  <Show when={(props.ready||props.retryStop)&&!complete()||props.attempt?.phase==='NOT_FOUND'}><button type="button" disabled={props.busy||props.unavailable} onClick={props.onContinue}>{props.attempt?.phase==='NOT_FOUND'?t('重试原恢复请求','Retry original recovery'):props.retryStop?t('重试原停止请求','Retry original stop'):t('继续创建空白对话','Finish creating the empty chat')}</button></Show>
   <Show when={complete()}><button type="button" disabled={props.busy||props.unavailable} onClick={props.onOpen}>{t('打开新对话','Open the new chat')}</button></Show>
  </div>
 </article>;
}
