import {createEffect,createMemo,createSignal,For,onCleanup,Show,type JSX} from 'solid-js';
import {Dialog} from '@kobalte/core/dialog';
import {api} from './api';
import {parseHistoryPage,mergeHistoryItems,labelHistoryItems,type HistoryItem} from './conversation-history';
import './conversation-manager.css';
import ConversationActions from './ConversationActions';
export type HistorySelectionResult='OPENED'|'FAILED'|'DEFERRED';

export default function ConversationManager(props:{open:boolean;onOpenChange:(open:boolean)=>void;locale:string;refreshVersion:number;busy:boolean;status?:()=>JSX.Element;onOpen:(item:HistoryItem)=>Promise<HistorySelectionResult>;onOrganize:(item:HistoryItem,action:'archive'|'restore')=>Promise<unknown>;onRequestOrganize?:(item:HistoryItem,action:'move'|'delete',returnTarget:HTMLElement)=>unknown;returnTarget?:HTMLElement;fallbackFocus?:()=>void}){
 const en=()=>props.locale.startsWith('en'),[tab,setTab]=createSignal<'active'|'archived'>('active'),[query,setQuery]=createSignal(''),[items,setItems]=createSignal<HistoryItem[]>([]),[cursor,setCursor]=createSignal<string|null>(null),[partial,setPartial]=createSignal(false),[busy,setBusy]=createSignal(false),[error,setError]=createSignal(false),[openError,setOpenError]=createSignal(false);
 let content:HTMLDivElement|undefined;
 let epoch=0,controller:AbortController|undefined,previousTab=tab(),openingRequest=0,opening=false;
 onCleanup(()=>{openingRequest++;epoch++;controller?.abort();});
 async function load(more=false){
  const after=more?cursor():null;if(more&&!after)return;const own=++epoch;controller?.abort();controller=new AbortController();setBusy(true);setError(false);
  try{const value=parseHistoryPage(await api.request('/api/conversations?limit=50&state='+tab()+(after?'&cursor='+encodeURIComponent(after):''),'GET',undefined,controller.signal),props.locale);if(own!==epoch||!props.open)return;if(!value.organizationSupported||value.items.some(item=>!item.organization||item.organization.archived!==(tab()==='archived')))throw Error('ORGANIZATION_REFERENCE_UNAVAILABLE');setItems(previous=>mergeHistoryItems(more?previous:[],labelHistoryItems(value.items,localStorage)));setCursor(value.nextCursor);setPartial(value.partial);}
  catch{if(own===epoch&&!controller.signal.aborted)setError(true);}
  finally{if(own===epoch)setBusy(false);}
 }
 createEffect(()=>{if(!props.open){openingRequest++;opening=false;epoch++;controller?.abort();setBusy(false);setOpenError(false);return;}const currentTab=tab();if(previousTab!==currentTab){previousTab=currentTab;setItems([]);setCursor(null);setPartial(false);}props.refreshVersion;void load();});
 const filtered=createMemo(()=>{const words=query().normalize('NFKC').toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);return items().filter(item=>words.every(word=>(item.title+' '+(item.projectName??(en()?'Chats':'聊天'))).normalize('NFKC').toLocaleLowerCase().includes(word)));});
 async function open(item:HistoryItem){
  if(props.busy||busy()||opening)return false;const own=++openingRequest;opening=true;setOpenError(false);
  try{const result=await props.onOpen(item);if(own!==openingRequest||!props.open||props.busy)return false;if(result==='OPENED'){props.onOpenChange(false);return true;}if(result!=='DEFERRED')setOpenError(true);return false;}
  catch{if(own===openingRequest&&props.open&&!props.busy)setOpenError(true);return false;}
  finally{if(own===openingRequest)opening=false;}
 }
 return <Dialog open={props.open} onOpenChange={props.onOpenChange} modal><Dialog.Portal><Dialog.Overlay class="as-conversation-manager-overlay"/><div class="as-conversation-manager-position"><Dialog.Content ref={content} class="as-conversation-manager" aria-describedby={undefined} onCloseAutoFocus={event=>{event.preventDefault();const target=props.returnTarget;if(target?.isConnected&&target.getClientRects().length&&!target.closest('[hidden],[inert]'))target.focus({preventScroll:true});else props.fallbackFocus?.();}}>
  <header><Dialog.Title>{en()?'Manage chats':'会话管理'}</Dialog.Title><Dialog.CloseButton aria-label={en()?'Close chat management':'关闭会话管理'}>×</Dialog.CloseButton></header>
  <div class="as-conversation-manager-tabs" role="tablist" aria-label={en()?'Chat status':'会话状态'} onKeyDown={event=>{if(event.isComposing||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const next=event.key==='Home'?'active':event.key==='End'?'archived':tab()==='active'?'archived':'active';setTab(next);(event.currentTarget as HTMLElement).querySelector<HTMLButtonElement>('[data-state='+next+']')?.focus();}}><For each={['active','archived'] as const}>{state=><button type="button" role="tab" data-state={state} aria-selected={tab()===state} tabIndex={tab()===state?0:-1} aria-controls="as-managed-conversations" onClick={()=>setTab(state)}>{state==='active'?(en()?'Chats':'会话'):(en()?'Archived':'已归档')}</button>}</For></div>
  <label class="as-conversation-manager-search"><span class="as-visually-hidden">{en()?'Search loaded chats':'搜索已加载会话'}</span><input value={query()} maxLength={512} placeholder={en()?'Search projects or chats':'搜索项目或会话'} onInput={event=>setQuery(event.currentTarget.value)}/></label>
  <Show when={props.status}>{props.status?.()}</Show>
  <Show when={error()}><div class="as-conversation-manager-error" role="alert"><span>{en()?'Could not refresh chats. Existing records are retained.':'暂未刷新会话，原记录保留。'}</span><button type="button" disabled={busy()} onClick={()=>void load()}>{en()?'Refresh':'刷新'}</button></div></Show>
  <Show when={openError()}><p class="as-conversation-manager-error" role="alert">{en()?'This chat could not be opened. Your input is kept.':'会话暂时无法打开，当前输入保留。'}</p></Show>
  <div id="as-managed-conversations" class="as-conversation-manager-list" role="tabpanel" aria-label={tab()==='active'?(en()?'Chats':'会话'):(en()?'Archived chats':'已归档会话')} aria-busy={busy()}>
   <For each={filtered()}>{item=><div class="as-conversation-manager-row" data-conversation-id={item.id}><Show when={tab()==='active'} fallback={<div class="as-conversation-manager-name"><strong title={item.title}>{item.title}</strong><small title={item.projectName??(en()?'Chats':'聊天')}>{item.projectName??(en()?'Chats':'聊天')}</small></div>}><button type="button" class="as-conversation-manager-name" disabled={props.busy} onClick={()=>void open(item)}><strong title={item.title}>{item.title}</strong><small title={item.projectName??(en()?'Chats':'聊天')}>{item.projectName??(en()?'Chats':'聊天')}</small></button></Show><ConversationActions portalMount={content} item={item} locale={props.locale} busy={props.busy||busy()||!item.organization} onOrganize={(value,action)=>props.onOrganize(value as HistoryItem,action)} onRequest={props.onRequestOrganize?(value,action,target)=>props.onRequestOrganize!(value as HistoryItem,action,target):undefined}/></div>}</For>
   <Show when={!filtered().length&&!busy()&&!error()}><p>{query().trim()?(en()?'No matching loaded chats':'没有匹配的已加载会话'):tab()==='archived'?(en()?'No archived chats':'暂无已归档会话'):(en()?'No chats yet':'暂无会话')}</p></Show>
   <Show when={busy()&&!items().length}><p role="status">{en()?'Loading chats…':'正在读取会话…'}</p></Show>
  </div>
  <footer><span>{partial()?(en()?'Showing loaded chats only':'仅显示已加载会话'):''}</span><Show when={cursor()}><button type="button" disabled={busy()} onClick={()=>void load(true)}>{en()?'Load more':'加载更多'}</button></Show></footer>
 </Dialog.Content></div></Dialog.Portal></Dialog>;
}
