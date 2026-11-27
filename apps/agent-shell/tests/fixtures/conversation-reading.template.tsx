import {render} from 'solid-js/web';
import {batch,createEffect,createSignal,For,onCleanup,Show} from 'solid-js';
import ConversationTranscript from '__SRC__/ConversationTranscript.tsx';
import {ConversationReading,type LatestMessagePosition,type ConversationReadingScope} from '__SRC__/conversation-reading.ts';
import {preserveLayoutReading} from '__SRC__/chat-workbench-layout.ts';
import {originalSessionReady,originalGuidanceText,AgentSessionOutcomeError} from '__SRC__/agent-sessions.ts';
import {sameNativeReference,nativeInputAttachments} from '__SRC__/native-collaboration.ts';
import {originalFileInput,sameOriginalFileInput} from '__SRC__/original-file-input.ts';
import {DeliveryHistoryRecovery,selectedDeliverySession,loadedDeliveryTargets} from '__SRC__/delivery-recovery.ts';
import {readComposerDraft} from '__SRC__/composer-draft.ts';
import {presentationFor} from '__SRC__/interaction-policy.ts';
import {sameReview} from '__SRC__/native-review.ts';
import {authoredCopy} from '__SRC__/authored-ui-copy.ts';
import '__SRC__/styles.css';
import '__SRC__/native-collaboration.css';
import '__SRC__/agent-studio.css';
import '__SRC__/brand-and-spacing.css';
import '__SRC__/chat-workbench-layout.css';
import './fixture.css';

function Fixture(){
 let shell:HTMLDivElement|undefined,composerInteracted=false,activityRequest=0,studioDisposed=false,threadReadGeneration=0,threadReadAbort:AbortController|undefined,nav=0,sendCount=0;
 const params=new URLSearchParams(location.search),discovery=params.has('discovery'),lang=params.get('lang')??'zh',uiLocale=()=>lang;
 const id='native:'+'a'.repeat(64),ref={id:'owner',revision:1,sha256:'b'.repeat(64)},owner={project_id:'p',session_ref:ref,generation:1,owner_status:'READY',connected_owner:true,configuration_current:true,observation_is_current:true,native_status:'idle',active_operation_id:null};
 const [threadScope,setThreadScope]=createSignal<any>(discovery?undefined:{conversation_ref:{id,revision:1},project_id:'p'}),[threadMessages,setThreadMessagesSignal]=createSignal<any[]>([]),[threadCursor,setThreadCursor]=createSignal<string|null>(null),[threadLoading,setThreadLoading]=createSignal(false),[threadError,setThreadError]=createSignal('');
 const [prompt,setPrompt]=createSignal(localStorage.getItem('reading-fixture-draft')??'当前要发的消息 {"draft":"原话"} 😀\n请在原讨论继续。'),[nativeDraftAttachments,setNativeDraftAttachments]=createSignal<any[]>([]),[latestMessage,setLatestMessage]=createSignal<LatestMessagePosition>({visible:false,top:0}),[error,setError]=createSignal('');
 const [skillDrawer,setSkillDrawer]=createSignal(false);
 let failReading=false;const readingStorage={getItem:(key:string)=>localStorage.getItem(key),setItem:(key:string,value:string)=>{if(failReading)throw Error('Fixture reading quota');localStorage.setItem(key,value);}};
 const conversationReading=new ConversationReading({container:()=>shell?.querySelector<HTMLElement>('.as-agent-content > .as-conversation')??undefined,changed:setLatestMessage,storage:readingStorage});
 onCleanup(()=>{studioDisposed=true;conversationReading.clear();});
 const [entryRevision,setEntryRevision]=createSignal(1);
 const [activeSession,setActiveSession]=createSignal<any>(discovery?{project_id:'p',id:'saved-discovery',revision:2,sha256:'d'.repeat(64)}:undefined);
 __ENTRY__
 __ENTRY_SYNC__
 const enterSaved=()=>{const stamp=nav,owner={current:()=>stamp===nav},request=++activityRequest,current=()=>!studioDisposed&&owner.current()&&request===activityRequest&&!composerInteracted;enterConversationReading(conversationReadingState(),params.has('startup')?current:undefined);};
 const scopeFor=(cid:string)=>({conversation_ref:{id:cid,revision:1},project_id:'p'});
 const row=(n:number,text?:string,role='assistant',operation_id?:string)=>({id:operation_id?operation_id+'-'+role:'history-'+n,role,text:text??('历史 '+n+'：用于核对阅读位置和段落换行。').repeat(8),text_sha256:'0'.repeat(64),source:{kind:'original',project_id:'p',session_id:ref.id,alias_id:'original:'+'b'.repeat(64)},operation_id,native_message_id:'message-'+n});
 let capturedLayoutRestore:(()=>void)|undefined;let sendAfterCommit=false,frames=0;const raf=window.requestAnimationFrame.bind(window);window.requestAnimationFrame=callback=>{frames++;return raf(callback);};onCleanup(()=>window.requestAnimationFrame=raf);
 const setThreadMessages=(value:any)=>{setThreadMessagesSignal(value);if(sendAfterCommit){sendAfterCommit=false;queueMicrotask(()=>void submitOriginalPrompt());}};
 let rows=Array.from({length:32},(_,n)=>row(n)),lastKey='',sendDelay=50,readDelay=0,deferredRead:(()=>void)|undefined;
 const backend=new Map<string,any[]>([[id,rows]]),calls:any[]=[];
 const threadClient={scope:async(cid:string)=>scopeFor(cid),history:async(scope:any,cursor:any)=>{const cid=scope.conversation_ref.id;calls.push({kind:'history',cid,cursor});if(readDelay)await new Promise(r=>setTimeout(r,readDelay));if(cursor==='defer')await new Promise<void>(r=>deferredRead=r);return{messages:[...(backend.get(cid)??[])],nextCursor:null};}};
 const threadSendingBlocked=()=>false,setHistoryItems=()=>{},setHistorySelectedItem=()=>{},originalSession=()=>owner,originalAgent=()=> 'build',draftAttachments=()=>[],currentOriginalFileInput=()=>undefined;
 const threadWriteAllowed=()=>true,ensureOriginalForSend=async()=>true,saveComposerDraft=()=>localStorage.setItem('reading-fixture-draft',prompt()),project=()=>({id:'p'}),allProjects=()=>[{id:'p'}],projects=()=>[{id:'p'}],showOriginalSessionManager=async()=>{throw Error('No unrelated management page')};
 const navigation={capture:()=>{const stamp=nav;return()=>nav===stamp;},begin:()=>{const stamp=++nav;return{current:()=>nav===stamp,signal:new AbortController().signal};}};
 const action=async(_label:string,run:()=>Promise<void>)=>run(),setNotice=()=>{},setOriginalOperations=()=>{},setConnectionNotice=()=>{},uiText=(s:string)=>s,agentSessionsApi={},observeOriginalSession=()=>{};
 const operationId=()=> 'reading-operation-'+(++sendCount);
 const dispatchOriginalMessage=async(_api:any,_storage:any,_owner:any,text:string,_agent:any,key:string,input:any)=>{
  calls.push({kind:'new-message',cid:input.conversationRef.id,key,text});lastKey=key;await new Promise(r=>setTimeout(r,sendDelay));
  const cid=input.conversationRef.id;backend.set(cid,[...(backend.get(cid)??[]),row(100,text,'user',key)]);
  return{id:key,status:'ACCEPTED',kind:'MESSAGE'};
 };
 __REFRESH__
 __SENDER__
 const grow=async(text:string)=>{const cid=threadScope()!.conversation_ref.id;const list=backend.get(cid)??[];backend.set(cid,[...list.filter(r=>!(r.operation_id===lastKey&&r.role==='assistant')),row(101,text,'assistant',lastKey)]);await refreshThread();};
 const switchChat=(cid:string)=>{nav++;threadReadGeneration++;threadReadAbort?.abort();setThreadScope(scopeFor(cid));if(!backend.has(cid))backend.set(cid,Array.from({length:18},(_,n)=>row(500+n,'另一讨论 '+n+'：请保留当前阅读位置。'.repeat(5))));setThreadMessages(backend.get(cid)!);if(new URLSearchParams(location.search).has('entry'))enterSaved();};
 const supervisorClient=undefined,quickInbox={flush:async()=>{}},flushWorkbenchDrafts=async()=>{},flushDynamicWorkflowDrafts=async()=>{},flushOriginalControlDrafts=()=>{},flushCurrentChatDraft=()=>localStorage.setItem('reading-fixture-draft',prompt());
 const closeReading=__CLOSE__;
 const setDraftReady=()=>{},setMaterials=()=>{},sessionMaterials=()=>[],restoreComposerDraft=()=>{},nativeBridge=undefined,windowMode=()=> 'workbench',compact=()=>false,setInspector=()=>{},setSidebarOpen=()=>{},presentWorkspace=()=>{},artifactStateKey=()=>undefined,workspaceStateOwner=undefined,interactions={request:()=>{}};
 __ACTIVATE__
 __INSPECT__
 __DISCOVERY_FOLLOW__
 const hostSession=(name:string)=>({project_id:'p',id:name,revision:2,sha256:'d'.repeat(64)});
 const inspectCurrent=()=>{const value=activeSession();inspect({type:'discovery',artifactId:value.id,revision:value.revision,contentHash:value.sha256,schemaVersion:1,scope:{projectId:'p'},availability:'IMPLEMENTED',data:value});};
 const inspectThread=()=>inspect({type:'report',artifactId:'fixture-reading-report',revision:1,contentHash:'f'.repeat(64),schemaVersion:1,scope:{projectId:'p'},availability:'IMPLEMENTED',data:{}});
 const followDiscovery=()=>{const node=shell!.querySelector<HTMLElement>('.as-conversation')!;node.classList.add('as-persistent-transcript');node.dataset.projectId='p';node.dataset.discoverySessionId=activeSession().id;followDiscoveryReading(activeSession(),'fixture-discovery-output',navigation.capture());};
 const outerActivate=(inside=false)=>batch(()=>{setThreadScope(undefined);activateSession(hostSession('host-discovery'));if(inside)inspectCurrent();});
 const fastScopeReturn=()=>{const value=activeSession();batch(()=>{navigation.begin();activateSession({...value,revision:3,sha256:'e'.repeat(64)});navigation.begin();activateSession(hostSession('other-host-discovery'));navigation.begin();activateSession(value);});};
 const savedCurrent=()=>{const state=conversationReadingState()!;return JSON.parse(localStorage.getItem('eeas-conversation-reading-v1')??'{"entries":[]}').entries.find((e:any)=>e.key===JSON.stringify([state.projectId,state.kind,state.id,state.revision,state.sha256??null]));};
 const [feed,setFeed]=createSignal<any[]>([]),[toolMode,setToolMode]=createSignal(''),proposals=()=>[],inspector=()=>undefined,discoverySessions=()=>activeSession()?[activeSession()]:[];
 const operationWaiters=new Map<string,()=>void>();let directoryRelease:(()=>void)|undefined;const directoryCalls:string[]=[];
 const planningDeliveryApi={sessionOperations:async()=>{directoryCalls.push('directory');await new Promise<void>(r=>directoryRelease=r);if(params.has('directory-error'))throw Error('Fixture directory unavailable');return{entries:[1,2,3,4].map(n=>({operation_id:'saved_stage_'+n,created_at:'2026-10-28T10:00:0'+n+'Z',session_ref:{id:'saved-discovery',revision:2,sha256:'d'.repeat(64)}}))};},operation:async(_pid:string,key:string)=>{directoryCalls.push(key);await new Promise<void>(r=>operationWaiters.set(key,r));return{operation_id:key,status:'PROCESSING',kind:'STAGE',project_id:'p',session_ref:{id:'saved-discovery',revision:2,sha256:'d'.repeat(64)},automatic_model_replay:false};}};
 const recordDeliveryFeed=(item:any)=>setFeed(list=>[...list,item]);
 __DELIVERY__
 __RECOVERY__
 const root=<div class="as-app-frame" onInput={()=>{composerInteracted=true;(conversationReading as any).interact?.();}} onCompositionStart={()=>{(conversationReading as any).interact?.();}}><div ref={shell} class="as-shell qa-shell" data-layout="workbench" data-chat-layout="adaptive" data-window-mode="workbench" data-agent-hidden="false" data-workbench-hidden="true" data-files-hidden="true"><aside class="as-agent"><div class="as-agent-content"><header class="as-agent-header"><strong>Cirven · {lang==='en'?'Current conversation':'当前讨论'}</strong></header>
 __JUMP__
 <main class="as-conversation" tabIndex={-1}><ConversationTranscript messages={threadMessages()} locale={lang} more={!!threadCursor()} loading={threadLoading()} error={threadError()} displayVersion={0} onMore={()=>void refreshThread(true)} onRetry={()=>void refreshThread()} onInput={()=>{}} onResult={()=>{}} resolveUser={async r=>r.text} liveText={r=>r.text}/><For each={feed()}>{item=><article class="as-history-message" data-reading-anchor={"feed:"+item.id}><h2>{item.id}</h2><p>{("已存阶段回执，尚未确认原操作状态。".repeat(50))}</p></article>}</For></main>
 <form class="as-composer" onSubmit={e=>{e.preventDefault();void submitOriginalPrompt();}}><textarea aria-label="Current message" value={prompt()} onInput={e=>setPrompt(e.currentTarget.value)}/><footer><span>{error()}</span><button class="as-composer-send" type="submit">{lang==='en'?'Send':'发送'}</button></footer></form>
 </div></aside></div></div>;
 (window as any).qa={outerActivate,inspectCurrent,inspectThread,followDiscovery,fastScopeReturn,savedCurrent,detachReading:()=>{conversationReading.clear();composerInteracted=false;},queueEntryInput:()=>{composerInteracted=false;enterSaved();shell?.querySelector('textarea')?.dispatchEvent(new InputEvent('input',{bubbles:true}));},startDirectory:()=>setToolMode('discovery'),releaseDirectory:()=>directoryRelease?.(),releaseOperation:(key:string)=>operationWaiters.get(key)?.(),directoryCalls,directoryState:()=>({ready:readingDeliveryReady(),views:deliveryDirectories()}),closeReading,failReading:()=>failReading=true,enterSaved,flushReading:()=>{(conversationReading as any).flush?.();},revision:(n:number)=>{setEntryRevision(n);setThreadScope({...threadScope(),conversation_ref:{...threadScope().conversation_ref,revision:n}});enterSaved();},observeRevision:(n:number)=>{setEntryRevision(n);setThreadScope({...threadScope(),conversation_ref:{...threadScope().conversation_ref,revision:n}});},append:()=>{const cid=threadScope()!.conversation_ref.id;backend.set(cid,[...(backend.get(cid)??[]),row(900+backend.get(cid)!.length,'迟到的已存结果。'.repeat(180))]);return refreshThread();},calls,skill:setSkillDrawer,captureLayoutRestore:()=>capturedLayoutRestore=preserveLayoutReading(shell),flushLayoutRestore:()=>capturedLayoutRestore?.(),frames:()=>frames,raceSend:()=>{sendAfterCommit=true;void refreshThread();},scope:()=>threadScope()?.conversation_ref.id,grow,refresh:()=>refreshThread(),switchChat,original:id,latest:()=>latestMessage(),draft:()=>prompt(),delay:(n:number)=>sendDelay=n,readDelay:(n:number)=>readDelay=n,older:async()=>{const cid=threadScope()!.conversation_ref.id;backend.set(cid,[...Array.from({length:6},(_,n)=>row(-n-1)),...(backend.get(cid)??[])]);await refreshThread(true);},defer:()=>{setThreadCursor('defer');return refreshThread(true);},release:()=>deferredRead?.(),destroy:()=>{studioDisposed=true;nav++;conversationReading.clear();},diagnostic:()=>({sendCount,stamp:conversationReading.stamp(),following:conversationReading.following(conversationReadingScope()!),error:error()})};
 if(discovery){setThreadMessages(rows);enterSaved();}else void refreshThread().then(()=>{if(params.has('entry'))enterSaved();});return root;
}
render(()=> <Fixture/>,document.getElementById('app')!);
