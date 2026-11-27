import {createSignal,Show} from 'solid-js';
import StudioPopover from './StudioPopover';
import type {ConversationSidebarItem} from './conversation-sidebar';

/** Shared contextual entry for the sidebar and management list. */
export default function ConversationActions(props:{item:ConversationSidebarItem;locale?:string;busy?:boolean;portalMount?:HTMLElement;onOrganize:(item:ConversationSidebarItem,action:'archive'|'restore')=>Promise<unknown>;onRequest?:(item:ConversationSidebarItem,action:'move'|'delete',returnTarget:HTMLElement)=>unknown}){
 const [open,setOpen]=createSignal(false),en=()=>props.locale?.startsWith('en');let trigger:HTMLButtonElement|undefined,handoff=false;
 function request(action:'move'|'delete'){handoff=true;setOpen(false);if(trigger)props.onRequest?.(props.item,action,trigger);}
 return <StudioPopover portalMount={props.portalMount} open={open()} onOpenChange={value=>{if(value)handoff=false;setOpen(value);}} onCloseAutoFocus={event=>{if(handoff)event.preventDefault();}} label={en()?'Chat actions':'会话操作'} placement="bottom-start" class="as-conversation-actions-popover" trigger={<button ref={trigger} type="button" class="as-conversation-row-more" aria-label={(en()?'Manage chat: ':'管理会话：')+props.item.title} aria-haspopup="dialog" aria-expanded={open()} disabled={props.busy} onClick={()=>{handoff=false;setOpen(value=>!value);}}><svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="4" cy="10" r="1"/><circle cx="10" cy="10" r="1"/><circle cx="16" cy="10" r="1"/></svg></button>}>
  <Show when={props.onRequest}><button type="button" disabled={props.busy} onClick={()=>request('move')}>{en()?'Move to project…':'移动到项目…'}</button></Show>
  <button type="button" disabled={props.busy} onClick={()=>{setOpen(false);void props.onOrganize(props.item,props.item.organization?.archived?'restore':'archive').catch(()=>{});}}>{en()?props.item.organization?.archived?'Restore':'Archive':props.item.organization?.archived?'恢复':'归档'}</button>
  <Show when={props.onRequest}><button type="button" class="as-conversation-delete-action" disabled={props.busy} onClick={()=>request('delete')}>{en()?'Delete…':'删除…'}</button></Show>
 </StudioPopover>;
}
