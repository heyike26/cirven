import {render} from 'solid-js/web';
import {createSignal} from 'solid-js';
import OriginalPendingControls from '__SRC__/OriginalPendingControls.tsx';
import {agentSessionsApi} from '__SRC__/agent-sessions.ts';
import {DiscussionDraftLease,discussionDraftKey} from '__SRC__/discussion-drafts.ts';
import {sameNativeReference} from '__SRC__/native-collaboration.ts';
import {sameReview} from '__SRC__/native-review.ts';
import {sameOriginalFileInput,originalFileChoice} from '__SRC__/original-file-input.ts';
import {assertQuestionRecoveryCurrent,questionRecoveryText,appendQuestionRecovery,type QuestionDraftRecovery} from '__SRC__/original-question-recovery.ts';
import data from '__FIXTURE__';
import '__SRC__/styles.css';
import '__SRC__/agent-studio.css';
import '__SRC__/native-collaboration.css';
import '__SRC__/brand-and-spacing.css';
import '__SRC__/chat-workbench-layout.css';
const locale=new URL(location.href).searchParams.get('lang')||'zh',calls:any[]=[],candidate=data.source;
const target={kind:'conversation' as const,projectId:null,id:data.conversation},key=discussionDraftKey(target);
if(!localStorage.getItem(candidate.key))localStorage.setItem(candidate.key,candidate.raw);
if(!localStorage.getItem(key))localStorage.setItem(key,JSON.stringify({text:'当前已写的消息 😀\n',attachments:[]}));
let paused=false,release:(()=>void)|undefined,failSave=false,failMarker=false,navEpoch=0,readCount=0;
agentSessionsApi.operation=async(_session:any,id:string)=>{calls.push({method:'GET_OPERATION',id});if(paused)await new Promise<void>(r=>release=r);return structuredClone(data.operation);};
agentSessionsApi.pendingReply=async()=>{calls.push({method:'FORBIDDEN_REPLY'});throw Error('No old reply allowed');};
const sourceStorage={get length(){return localStorage.length;},key:(i:number)=>localStorage.key(i),getItem:(k:string)=>{if(k===candidate.key)readCount++;return localStorage.getItem(k);},setItem:(k:string,v:string)=>{if(failMarker&&k===candidate.key)throw Error('injected marker storage failure');localStorage.setItem(k,v);},removeItem:(k:string)=>localStorage.removeItem(k),clear:()=>localStorage.clear()};
function Harness(){
 const [originalSession,setOwner]=createSignal<any>(structuredClone(data.session));
 const [scope,setScope]=createSignal<any>({conversation_ref:{id:data.conversation,revision:1},project_id:data.session.project_id});
 const threadScope=scope,threadWriteAllowed=()=>scope()?.conversation_ref.id===data.conversation,navigation={capture:()=>{const epoch=navEpoch;return()=>epoch===navEpoch;}};
 let lease=new DiscussionDraftLease(localStorage,target);
 const [prompt,setPrompt]=createSignal(lease.read().text),[nativeDraftAttachments,setAttachments]=createSignal<any[]>([]),[error,setError]=createSignal('');
 const uiLocale=()=>locale,currentOriginalFileInput=()=>originalFileChoice(data.session.project_id,data.operation.request.file_input.link_ref,undefined);
 function saveComposerDraft(){if(failSave)throw Error('injected destination save failure');lease.save({text:prompt(),attachments:nativeDraftAttachments()});}
 function focusComposer(){document.querySelector<HTMLTextAreaElement>('.as-composer textarea')?.focus();}
 __CALLBACK__
 (window as any).qa={calls,pause:()=>{paused=true;},release:()=>{paused=false;release?.();},edit:(text:string)=>{setPrompt(text);saveComposerDraft();},failSave:()=>{failSave=true;},failMarker:()=>{failMarker=true;},sourceChanged:()=>{const envelope=JSON.parse(localStorage.getItem(candidate.key)!);const value=JSON.parse(envelope.text);value.draft.custom=['别的窗口较新的回答'];envelope.text=JSON.stringify(value);localStorage.setItem(candidate.key,JSON.stringify(envelope));window.dispatchEvent(new StorageEvent('storage',{key:candidate.key}));},navigate:()=>{navEpoch++;setScope({conversation_ref:{id:'native:'+'e'.repeat(64),revision:1}});setPrompt('切换后的当前输入');},otherOperation:()=>setOwner((s:any)=>({...s,active_operation_id:"another_live_operation"})),poll:()=>setOwner((s:any)=>({...s})),reads:()=>readCount,source:()=>JSON.parse(JSON.parse(localStorage.getItem(candidate.key)!).text),target:()=>JSON.parse(localStorage.getItem(key)!),originalSource:candidate.raw};
 return <div class="as-app-frame"><main class="as-agent" style={{height:'100dvh',width:'100%',flex:'1 1 auto','min-width':'0','max-width':'760px',margin:'auto',display:'flex','flex-direction':'column'}}><div class="as-agent-content" style={{display:'flex','flex-direction':'column',height:'100%','min-height':'0'}}><header class="as-agent-header"><strong>{locale==='en'?'Continue the same discussion':'继续当前讨论'}</strong></header><div style={{flex:'1 1 0','min-height':'32px'}}/><OriginalPendingControls session={originalSession()} locale={locale} storage={sourceStorage} onUpdated={setOwner} onRecoverAnswer={restoreQuestionAnswer}/><p role="alert" class="fixture-error">{error()}</p><form class="as-composer" onSubmit={e=>{e.preventDefault();calls.push({method:'FORBIDDEN_SEND'});}}><textarea aria-label={locale==='en'?'Current message':'当前消息'} style={{width:'100%','box-sizing':'border-box'}} value={prompt()} onInput={e=>{setPrompt(e.currentTarget.value);saveComposerDraft();}}/></form></div></main></div>;
}
render(()=><Harness/>,document.getElementById('app')!);
