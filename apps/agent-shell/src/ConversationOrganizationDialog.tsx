import {createEffect,createSignal,For,Show,untrack,type JSX} from 'solid-js';
import {Dialog} from '@kobalte/core/dialog';
import type {HistoryItem} from './conversation-history';
import type {OrganizationIntent} from './conversation-organization';
import type {SidebarProject} from './conversation-sidebar';
import './conversation-organization-dialog.css';

export type OrganizationDialogTarget={item:HistoryItem;action:'move'|'delete';returnTarget?:HTMLElement};
export default function ConversationOrganizationDialog(props:{target?:OrganizationDialogTarget;locale:string;projects:readonly SidebarProject[];busy:boolean;onClose:()=>void;onConfirm:(item:HistoryItem,intent:OrganizationIntent)=>Promise<unknown>;fallbackFocus:()=>void;status?:()=>JSX.Element}){
 let returnTarget:HTMLElement|undefined,projectSelect:HTMLSelectElement|undefined;
 const en=()=>props.locale.startsWith('en'),[projectId,setProjectId]=createSignal(''),[error,setError]=createSignal(''),[submitting,setSubmitting]=createSignal(false);
 createEffect(()=>{const target=props.target;if(target)returnTarget=target.returnTarget;untrack(()=>{setProjectId('');setError('');});});
 const canMove=()=>projectId()!==(props.target?.item.projectId??'')&&(projectId()===''||props.projects.some(item=>item.id===projectId()));
 const working=()=>props.busy||submitting();
 async function confirm(){const target=props.target;if(!target||working()||target.action==='move'&&!canMove())return;setSubmitting(true);setError('');try{await props.onConfirm(target.item,target.action==='move'?{action:'move',targetProjectId:projectId()||null}:{action:'delete',confirmed:true});if(props.target===target)props.onClose();}catch{if(props.target===target)setError(en()?'The change is not confirmed. Check its status below before trying again.':'整理尚未确认，请先核查下方状态，再继续操作。');}finally{setSubmitting(false);}}
 return <Dialog open={!!props.target} onOpenChange={open=>{if(!open)props.onClose();}} modal><Dialog.Portal><Dialog.Overlay class="as-conversation-operation-overlay"/><div class="as-conversation-operation-position"><Dialog.Content class="as-conversation-operation" onOpenAutoFocus={event=>{if(props.target?.action==='move'&&projectSelect){event.preventDefault();projectSelect.focus({preventScroll:true});}}} onCloseAutoFocus={event=>{event.preventDefault();const target=returnTarget;if(target?.isConnected&&target.getClientRects().length&&!target.closest('[hidden],[inert]'))target.focus({preventScroll:true});else props.fallbackFocus();}}>
  <header><Dialog.Title>{props.target?.action==='move'?(en()?'Move chat':'移动会话'):(en()?'Delete chat?':'删除会话？')}</Dialog.Title><Dialog.CloseButton aria-label={en()?'Close':'关闭'}>×</Dialog.CloseButton></header>
  <p class="as-conversation-operation-title" title={props.target?.item.title}>{props.target?.item.title}</p>
  <Dialog.Description>{props.target?.action==='move'?(en()?'Keep this conversation and its draft together in another project.':'将这段会话与草稿一起归到另一个项目。'):(en()?'Remove this chat from your conversations. It cannot be restored. Project files and execution records stay; running tasks continue. Your local draft is kept.':'从会话列表中删除此会话，无法恢复。项目文件和执行记录保留，在途任务继续，当前本机草稿也会保留。')}</Dialog.Description>
  <Show when={props.target?.action==='move'}><label class="as-conversation-operation-project"><span>{en()?'Destination':'目标项目'}</span><select ref={projectSelect} title={projectId()===''?(en()?'Chats':'聊天'):props.projects.find(project=>project.id===projectId())?.name} value={projectId()} onChange={event=>setProjectId(event.currentTarget.value)} disabled={working()}><option value="" disabled={props.target?.item.projectId==null}>{en()?'Chats':'聊天'}</option><For each={props.projects}>{project=><option value={project.id} disabled={project.id===props.target?.item.projectId}>{project.name|| (en()?'Untitled project':'未命名项目')}</option>}</For></select></label><p class="as-conversation-operation-note">{en()?'Files and running tasks keep their original location and permissions.':'文件与在途任务仍保留原位置和权限。'}</p></Show>
  <Show when={error()}><p role="alert">{error()}</p></Show>
  <Show when={props.status}>{props.status?.()}</Show>
  <footer><button type="button" onClick={props.onClose}>{en()?'Cancel':'取消'}</button><button type="button" data-confirm-organization={props.target?.action} class={props.target?.action==='delete'?'as-conversation-operation-danger':'as-conversation-operation-primary'} disabled={working()||props.target?.action==='move'&&!canMove()} onClick={()=>void confirm()}>{working()?(en()?'Saving…':'保存中…'):props.target?.action==='move'?(en()?'Move':'移动'):(en()?'Delete chat':'删除会话')}</button></footer>
 </Dialog.Content></div></Dialog.Portal></Dialog>;
}
