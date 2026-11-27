import {Show} from 'solid-js';
import {originalOwnerCanResume,type OriginalResumeAttempt} from './original-connection';
import {originalSessionReady,type AgentSession} from './agent-sessions';

export default function OriginalOwnerRecovery(props:{session:AgentSession;resumeAllowed?:boolean;attempt?:OriginalResumeAttempt;busy:boolean;unavailable:boolean;hasDraft:boolean;error:string;locale:string;onResume:()=>void;onInspect:()=>void;onRestore:()=>void}){
 const t=(zh:string,en:string)=>props.locale.startsWith('en')?en:zh;
 const completed=()=>props.attempt?.phase==='RETURNED'&&props.session.generation===props.attempt.generation+1&&originalSessionReady(props.session);
 const retry=()=>props.resumeAllowed!==false&&props.attempt?.phase!=='ATTEMPTED'&&!completed()&&originalOwnerCanResume(props.session);
 return <article class="as-pending-recovery" data-original-owner-recovery role="status">
  <p>{completed()?(props.hasDraft?t('会话已恢复，旧结果待核查。当前草稿保留，处理后可取回原要求。','Reconnected; old result unconfirmed. Your draft is kept. Finish it before restoring.'):t('会话已恢复，旧结果待核查。核对当前文件和输入后再发送。','Reconnected. Check the old result, current file and input before sending.')):props.attempt?.phase==='ATTEMPTED'?t('恢复结果待核查，当前输入保留。请核查原恢复操作。','Reconnection needs checking. Input kept; check the original reconnection.'):props.session.owner_status==='EXPIRED'?t('连接已到期，旧结果待核查。输入和文件保留。','The connection expired. Old result unconfirmed; input and file kept.'):t('连接已断开，旧结果待核查。输入和文件保留。','The connection closed. Old result unconfirmed; input and file kept.')}</p>
  <Show when={props.unavailable}><p role="alert">{t('恢复记录暂时无法读取，当前输入保留。','The recovery record could not be read. Your input is kept.')}</p></Show>
  <Show when={props.error}><p role="alert">{props.error}</p></Show>
  <div class="as-pending-actions">
   <Show when={retry()}><button type="button" data-primary="true" disabled={props.busy||props.unavailable} onClick={props.onResume}>{props.busy?t('正在恢复…','Reconnecting…'):props.attempt?.phase==='FAILED'?t('重新恢复此会话','Try reconnecting this chat'):t('恢复此会话','Reconnect this chat')}</button></Show>
   <Show when={props.attempt?.phase==='ATTEMPTED'}><button type="button" disabled={props.busy||props.unavailable} onClick={props.onInspect}>{props.busy?t('正在核查…','Checking…'):t('核查原恢复结果','Check original reconnection')}</button></Show>
   <Show when={props.attempt?.phase==='FAILED'}><small>{t('原恢复已确认未完成；可明确重试，旧请求不会重发。','The original reconnection did not complete. You can retry explicitly; the old request will not be resent.')}</small></Show>
   <Show when={completed()&&props.attempt?.parent&&!props.attempt.requestRestored}><button type="button" disabled={props.busy||props.unavailable||props.hasDraft||!originalSessionReady(props.session)} onClick={props.onRestore}>{t('取回原要求','Restore original request')}</button></Show>
  </div>
 </article>;
}
