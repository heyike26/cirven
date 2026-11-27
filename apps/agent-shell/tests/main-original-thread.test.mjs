import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
import {dispatchOriginalMessage as dispatchOriginalMessageSource,originalSessionCanResumeForSend,originalSessionReady,originalGuidanceText,AgentSessionOutcomeError} from '../src/agent-sessions.ts';
const originalIndexTestLocks={request:async(_name,_options,run)=>run()};
const dispatchOriginalMessage=(client,storage,session,text,agent,key,input={})=>dispatchOriginalMessageSource(client,storage,session,text,agent,key,input,originalIndexTestLocks);

import {nativeInputAttachments,sameNativeReference} from '../src/native-collaboration.ts';
import {sameReview} from '../src/native-review.ts';
import {originalFileInput,sameOriginalFileInput} from '../src/original-file-input.ts';
import {originalWorkChoiceMatches,sameOriginalWorkChoice,originalWorkStatusText} from '../src/original-work-input.ts';
import {threadOriginalOwner,verifyThreadOriginalOwner} from '../src/conversation-thread.ts';
import {configurationOwner} from '../src/conversation-configuration.ts';
import {authoredCopy,authoredErrorCopy} from '../src/authored-ui-copy.ts';

const source=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8'),tree=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX),app=tree.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name.text==='App'),sender=['currentDiscussionDraft','conversationReadingState','readingScopeIdentity','readingScopeCurrent','selectedThreadId','threadOrganizationPending','threadSendingBlocked','threadWriteAllowed','originalInCurrentProject','adoptOriginalConnection','ensureOriginalForSend','submitOriginalPrompt'].map(name=>app.body.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name.text===name).getText(tree)).join('\n');
const ref={id:'owner',revision:1,sha256:'a'.repeat(64)},frame={id:'frame',revision:1,sha256:'b'.repeat(64)},cid='native:'+'c'.repeat(64);
const ready=(project_id='b',extra={})=>({project_id,session_ref:ref,generation:1,owner_status:'READY',connected_owner:true,configuration_current:true,observation_is_current:true,native_status:'idle',active_operation_id:null,...extra});
const attachment=()=>({context_ref:frame,label:'Saved screenshot',comment:'My note',regions:[],preview_sha256:'d'.repeat(64)});
function harness(options={}){
 const values=new Map(),storage={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};
 const state={text:'Exact user {"JSON":true} 😀\n',attachments:[attachment()],scope:{conversation_ref:{id:cid,revision:2},project_id:'b'},session:ready(),owned:true,errors:[],calls:[],operations:[],...options.state};
 if(state.scope)state.scope={sources:[],...state.scope};
 const bindings={contextChoice:()=>undefined,originalWorkInputChoice:()=>undefined,originalWorkChoiceMatches,sameOriginalWorkChoice,originalWorkStatusText,threadOriginalOwner,verifyThreadOriginalOwner,configurationOwner,pendingOwnerSelection:()=>({}),studioDisposed:false,originalFileInput,sameOriginalFileInput,currentOriginalFileInput:()=>undefined,conversationReading:{follow:(conversationId,operationId,current)=>{state.reading={conversationId,operationId,current};}},sameNativeReference,creationAffectsCurrent:()=>false,connectionAffectsCurrent:()=>false,providers:()=>state.profiles??[],providerId:()=>state.providerId??'',openProviders:async()=>state.calls.push('setup'),setCreationNotice:value=>state.calls.push('creation notice'),setConnectionNotice:value=>state.calls.push('connection notice'),quickScope:()=>({providerProfileRef:state.profiles?.find(p=>p.profile_ref.id===state.providerId)?.profile_ref}),workspace:()=>undefined,historySelectedItem:()=>undefined,
  threadClient:{scope:async()=>({...state.scope,sources:[{id:'original:'+'1'.repeat(64),identity:{kind:'original',project_id:state.connected?.project_id,session_id:state.connected?.session_ref.id},original_source:{kind:'original',project_id:state.connected?.project_id,session_ref:state.connected?.session_ref,generation:state.connected?.generation,provider_profile_ref:state.connected?.provider_profile_ref??null,workspace_ref:state.connected?.workspace_ref??null}}]})},setThreadScope:value=>state.scope=value,selectOriginalSession:value=>state.session=value,originalConnection:{open:async(...args)=>{state.calls.push('connect');state.connected=options.connect?await options.connect(state,args):ready('b',{provider_profile_ref:state.profiles[0].profile_ref});return {session:state.connected,bound:true};},acknowledge:async()=>state.calls.push('connection acknowledged')},api:{projects:async()=>[]},setProjects:()=>{},
  dispatchOriginalMessage,originalSessionCanResumeForSend,originalSessionReady,originalGuidanceText,AgentSessionOutcomeError,nativeInputAttachments,sameReview,authoredCopy,authoredErrorCopy,
  resumeOriginalOwner:async()=>{
   state.calls.push('load same owner');
   const resumed=options.resume?await options.resume(state):ready('b',{generation:2});
   if(resumed)state.session=resumed;
   return resumed;
  },
  organizationVersion:()=>0,threadAccess:()=>state.access??'ready',selectedDiscussion:()=>state.selection,organizationClient:{get pending(){return state.pending;}},originalSession:()=>state.session,prompt:()=>state.text,nativeDraftAttachments:()=>state.attachments,threadScope:()=>state.scope,draftAttachments:()=>[],uiLocale:()=> 'zh',allProjects:()=>[{id:'a'},{id:'b'},{id:'internal',description:'eeas.original-agent-session.internal-project.v1'}],projects:()=>[{id:'a'},{id:'b'}],project:()=>({id:state.scope?.project_id}),originalAgent:()=> 'build',localStorage:storage,
  navigation:{capture:()=>()=>state.owned},saveComposerDraft:()=>{if(options.saveFailure)throw new Error('quota');state.calls.push('save');},showOriginalSessionManager:async()=>state.calls.push('setup'),setError:value=>state.errors.push(value),setNotice:value=>state.calls.push('notice'),setPrompt:value=>state.text=value,setNativeDraftAttachments:value=>state.attachments=typeof value==='function'?value(state.attachments):value,
  setOriginalOperations:fn=>state.operations=fn(state.operations),operationId:()=> 'one_operation',action:async(_label,run)=>run(),refreshThread:async()=>state.calls.push('history'),observeOriginalSession:()=>state.calls.push('observe'),uiText:key=>key,
  agentSessionsApi:{send:async(session,text,agent,key,input)=>{state.calls.push({session,text,agent,key,input:structuredClone(input)});if(options.send)return options.send(state);return {id:key,status:'ACCEPTED',kind:session.active_operation_id?'GUIDANCE':'MESSAGE',request_sha256:'e'.repeat(64),automatic_replay:false};}},
 };
 const run=new Function(...Object.keys(bindings),ts.transpileModule(sender+'\nreturn submitOriginalPrompt;',{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText)(...Object.values(bindings));return{run,state,storage};
}
test('actual Main sends one exact instruction and selected screenshot through the existing public sender and durable input index',async()=>{
 const h=harness(),raw=h.state.text;await h.run();const posted=h.state.calls.filter(item=>typeof item==='object');assert.equal(posted.length,1);assert.equal(posted[0].text,raw);assert.equal(posted[0].session.project_id,'b');assert.deepEqual(posted[0].input.conversationRef,{id:cid,revision:2});assert.equal(posted[0].input.inputAttachments[0].image_sha256,'d'.repeat(64));assert.equal(h.state.text,'');assert.equal(h.state.attachments.length,0);
 const index=JSON.parse(h.storage.getItem('eeas-original-operation:owner'));assert.equal(index[0].input,raw);assert.equal(index[0].conversation_input.inputAttachments.length,1);
});
test('moved chat cannot create a new task on an old idle project owner, while its in-flight guidance keeps that owner',async()=>{
 const idle=harness({state:{session:ready('a')}});await idle.run();assert.ok(idle.state.calls.includes('setup'));assert.equal(idle.state.calls.filter(item=>typeof item==='object').length,0);assert.ok(idle.state.text);assert.equal(idle.state.attachments.length,1);
 const busy=harness({state:{session:ready('a',{active_operation_id:'old_task_a'})}});await busy.run();const call=busy.state.calls.find(item=>typeof item==='object');assert.equal(call.session.project_id,'a');assert.equal(call.session.active_operation_id,'old_task_a');assert.equal(call.input.conversationRef.id,cid);
});
test('late accepted reply and editing during dispatch cannot clear a different conversation or newer draft',async()=>{
 let resolve;const h=harness({send:()=>new Promise(done=>resolve=done)}),pending=h.run();await new Promise(setImmediate);h.state.owned=false;h.state.text='New conversation draft';h.state.attachments=[attachment()];resolve({id:'one_operation',status:'ACCEPTED',request_sha256:'e'.repeat(64),automatic_replay:false});await pending;assert.equal(h.state.text,'New conversation draft');assert.equal(h.state.attachments.length,1);assert.ok(!h.state.calls.includes('observe'));
 let accepted;const edited=harness({send:()=>new Promise(done=>accepted=done)}),waiting=edited.run();await new Promise(setImmediate);edited.state.text='New input in this same chat';accepted({id:'one_operation',status:'ACCEPTED',request_sha256:'e'.repeat(64),automatic_replay:false});await waiting;assert.equal(edited.state.text,'New input in this same chat');assert.equal(edited.state.attachments.length,1);
});
test('default Chats only reuses an internal owner; an ordinary user project remains a different execution scope',async()=>{
 const internal=harness({state:{scope:{conversation_ref:{id:cid,revision:1},project_id:null},session:ready('internal')}});await internal.run();assert.equal(internal.state.calls.filter(item=>typeof item==='object').length,1);
 const user=harness({state:{scope:{conversation_ref:{id:cid,revision:1},project_id:null},session:ready('a')}});await user.run();assert.equal(user.state.calls.filter(item=>typeof item==='object').length,0);assert.ok(user.state.calls.includes('setup'));
});


test('invalid material or unsaved draft stays visible without unhandled rejection or a model request',async()=>{
 for(const options of [{state:{attachments:[{...attachment(),preview_sha256:'wrong'}]}},{saveFailure:true},{saveFailure:true,state:{session:ready('a')}}]){
  const h=harness(options),text=h.state.text;await h.run();assert.equal(h.state.text,text);assert.equal(h.state.attachments.length,1);assert.equal(h.state.calls.filter(item=>typeof item==='object').length,0);assert.match(h.state.errors.at(-1),/尚未发送/);
 }
});


test('actual Main blocks deleted, unverified and same-chat unresolved organization without dispatch or clearing draft',async()=>{
 for(const state of [{access:'deleted'},{access:'checking'},{access:'unavailable'},{pending:{conversationId:cid}}]){const h=harness({state}),text=h.state.text;await h.run();assert.equal(h.state.text,text);assert.equal(h.state.attachments.length,1);assert.equal(h.state.calls.length,0);assert.match(h.state.errors.at(-1),/会话|核对/);}
 const other=harness({state:{pending:{conversationId:'native:'+'f'.repeat(64)}}});await other.run();assert.equal(other.state.calls.filter(item=>typeof item==='object').length,1);
});


test('a restored draft without a verified conversation scope cannot create a new public owner or send',async()=>{
 for(const access of ['unavailable','ready']){const h=harness({state:{scope:undefined,selection:{key:{kind:'conversation',id:cid}},access}}),text=h.state.text;await h.run();assert.equal(h.state.text,text);assert.equal(h.state.calls.length,0);assert.equal(h.state.attachments.length,1);}
});

test('first Main send opens an original owner in the logical target, then sends the exact initial text and typed image',async()=>{const profile={profile_ref:{id:'selected_model',revision:1,sha256:'f'.repeat(64)}},h=harness({state:{session:undefined,profiles:[profile],providerId:'selected_model'}}),text=h.state.text;await h.run();const call=h.state.calls.find(c=>typeof c==='object');assert.equal(call.text,text);assert.equal(call.session.project_id,'b');assert.deepEqual(call.input.conversationRef,{id:cid,revision:2});assert.equal(call.input.inputAttachments.length,1);assert.ok(h.state.calls.includes('connect'));assert.ok(h.state.calls.includes('connection acknowledged'));assert.equal(h.state.text,'');});
test('editing while initial connection opens keeps the updated draft and does not automatically send it',async()=>{let finish;const profile={profile_ref:{id:'selected_model',revision:1,sha256:'f'.repeat(64)}},h=harness({state:{session:undefined,profiles:[profile],providerId:'selected_model'},connect:()=>new Promise(resolve=>finish=resolve)}),waiting=h.run();await new Promise(setImmediate);h.state.text='Newly typed while connecting';finish(ready('b',{provider_profile_ref:profile.profile_ref}));await waiting;assert.equal(h.state.calls.filter(c=>typeof c==='object').length,0);assert.equal(h.state.text,'Newly typed while connecting');assert.equal(h.state.attachments.length,1);});


const disconnected=()=>ready('b',{owner_status:'DISCONNECTED',connected_owner:false,observation_is_current:false,
 pending_questions:[],pending_permissions:[],automatic_post_replay:false});
test('first send after desktop restart loads the original idle owner and sends once on its new generation',async()=>{
 const h=harness({state:{session:disconnected()}}),text=h.state.text;
 await h.run();
 assert.equal(h.state.calls.filter(call=>call==='load same owner').length,1);
 assert.equal(h.state.calls.includes('connect'),false);
 const sent=h.state.calls.filter(call=>typeof call==='object');
 assert.equal(sent.length,1);
 assert.deepEqual(sent[0].session.session_ref,ref);
 assert.equal(sent[0].session.generation,2);
 assert.equal(sent[0].text,text);
});

test('an edited draft or unresolved LOAD keeps the original input and makes no send or configuration change',async()=>{
 const edited=harness({state:{session:disconnected()},resume:async state=>{state.text='Revised while restoring';return ready('b',{generation:2});}});
 const unknown=harness({state:{session:disconnected()},resume:async()=>undefined});
 for(const h of [edited,unknown]){
  await h.run();
  assert.equal(h.state.calls.filter(call=>call==='load same owner').length,1);
  assert.equal(h.state.calls.includes('connect'),false);
  assert.equal(h.state.calls.some(call=>typeof call==='object'),false);
  assert.equal(h.state.attachments.length,1);
 }
 assert.equal(edited.state.text,'Revised while restoring');
 assert.ok(unknown.state.text.includes('Exact user'));
});

test('explicit Main send binds reading to its exact discussion and operation, retaining the navigation lease',async()=>{
 const h=harness();await h.run();assert.equal(h.state.reading.conversationId,cid);assert.equal(h.state.reading.operationId,'one_operation');assert.equal(h.state.reading.current(),true);h.state.owned=false;assert.equal(h.state.reading.current(),false);
 const rejected=harness({saveFailure:true});await rejected.run();assert.equal(rejected.state.reading,undefined);
});
