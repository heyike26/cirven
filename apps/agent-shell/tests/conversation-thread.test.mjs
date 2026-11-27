import {createRequire} from 'node:module';
const require=createRequire(import.meta.url),{transformSync}=require('esbuild'),ts=require('typescript');
function javascriptBody(body){const code=transformSync('async function __fixture(){\n'+body+'\n}',{loader:'ts',target:'node22'}).code;const source=ts.createSourceFile('actual-callback.js',code,ts.ScriptTarget.Latest,true,ts.ScriptKind.JS),fn=source.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='__fixture');if(!fn?.body)throw Error('Actual callback wrapper missing');return fn.body.getText(source).slice(1,-1);}
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {ConversationThreadClient,parseThreadScope,parseThreadHistory,threadDraftSources} from '../src/conversation-thread.ts';
import * as thread from '../src/conversation-thread.ts';
import {configurationOwner} from '../src/conversation-configuration.ts';
const id='native:'+'a'.repeat(64),ref={id:'frame',revision:1,sha256:'b'.repeat(64)},identity={kind:'native',project_id:null,conversation_id:'old_chat'};
const scope=()=>({version:1,conversation_ref:{id,revision:2},project_id:'new_project',archived:false,sources:[{id,identity,original_source:{...identity,workspace_ref:null}}],next_message:{project_id:'new_project',conversation_id:'new_chat'},execution_started:false,files_moved:false,automatic_input_authorized:false,file_write_authorized:false});
const message=(extra={})=>({id:'c'.repeat(64),role:'user',text:'User {"JSON":true} 😀',text_truncated:false,text_sha256:'d'.repeat(64),observed_at:'2026-10-19T03:00:00.123456+00:00',source:{alias_id:id,...identity},operation_id:'old_operation',history_role:'READONLY_DATA_NO_EXECUTION_OR_FILE_GRANT',...extra});
const page=(extra={})=>({version:1,conversation_ref:{id,revision:2},project_id:'new_project',messages:[message()],next_cursor:null,coverage:{model_calls:0,complete:true,source_partial:false},execution_authorized:false,automatic_input_authorized:false,file_write_authorized:false,...extra});
// AUTHORED_NOT_RUN: metadata route is not an SDK owner or a fabricated execution.
test('new metadata-only chat retains its real origin alias and never needs an SDK source',()=>{
 const value={...scope(),project_id:null,sources:[{id,identity,original_source:null}],next_message:{project_id:null,conversation_id:'old_chat'},metadata_source:{kind:'conversation',project_id:null,workspace_ref:null,conversation_ref:{id,revision:2},metadata_role:'CONVERSATION_METADATA_NO_EXECUTION_OR_FILE_GRANT'},current_owner:{status:'LEGACY'}};
 const parsed=parseThreadScope(value,id);assert.equal(parsed.sources[0].original_source,null);assert.equal(thread.threadOriginalOwner(parsed,null),undefined);assert.deepEqual(threadDraftSources(parsed),[{kind:'native',projectId:null,id:'old_chat'}]);
 assert.throws(()=>parseThreadScope({...value,sources:[]},id));assert.throws(()=>parseThreadScope({...value,metadata_source:{...value.metadata_source,workspace_ref:{id:'FAKE'}}},id));
});
test('stable chat keeps old execution sources and the new logical address separately',()=>{
 const parsed=parseThreadScope(scope(),id);assert.equal(parsed.project_id,'new_project');assert.equal(parsed.sources[0].identity.project_id,null);
 assert.equal(parsed.next_message.conversation_id,'new_chat');assert.deepEqual(threadDraftSources(parsed),[{kind:'native',projectId:null,id:'old_chat'}]);
 const history=parseThreadHistory(page(),parsed);assert.equal(history.messages[0].text,'User {"JSON":true} 😀');assert.equal(history.messages[0].observed_at,'2026-10-19T03:00:00.123456+00:00');
});
test('head, project, source identity and execution flags cannot be silently substituted',()=>{
 for(const change of [{conversation_ref:{id,revision:1}},{project_id:'old_project'},{execution_authorized:true},{automatic_input_authorized:true},{file_write_authorized:true},{messages:[message({source:{alias_id:id,...identity,conversation_id:'other_chat'}})]},{messages:[message(),message()]}])assert.throws(()=>parseThreadHistory(page(change),parseThreadScope(scope(),id)),/UNAVAILABLE/);
 for(const change of [{next_message:{project_id:null,conversation_id:'wrong'}},{sources:[{id,identity:{...identity,kind:'original',session_id:'sdk'},original_source:null}]},{execution_started:true}])assert.throws(()=>parseThreadScope({...scope(),...change},id),/UNAVAILABLE/);
});
test('typed images remain metadata and ordinary user JSON stays text; malformed image references fail closed',()=>{
 const input={kind:'SCREENSHOT',context_ref:ref,label:'Saved screenshot',regions:[{x:0,y:0,width:.5,height:.5}],image_sha256:'e'.repeat(64),user_note:'My note'};
 const parsed=parseThreadHistory(page({messages:[message({input_attachments:[input]})]}),parseThreadScope(scope(),id));assert.deepEqual(parsed.messages[0].input_attachments,[input]);
 for(const value of [{...input,image_sha256:'wrong'},{...input,regions:[{x:2,y:0,width:1,height:1}]},{...input,label:{internal:'JSON'}}])assert.throws(()=>parseThreadHistory(page({messages:[message({input_attachments:[value]})]}),parseThreadScope(scope(),id)));
});
test('selection writes only exact source metadata once; scope/history and old-page requests are reads',async()=>{
 const calls=[],client=new ConversationThreadClient({request:async(path,method='GET',body,signal,retry)=>{calls.push({path,method,body,retry});return path.includes('/history')?page():scope();}});
 const row={id,source:{...identity,workspace_ref:null}},selected=await client.select(row);await client.history(selected);await client.scope(id);await client.history(selected,'opaque cursor+');
 assert.equal(calls.filter(call=>call.method==='POST').length,1);assert.deepEqual(calls[0].body,{source:row.source});assert.equal(calls[0].retry,false);assert.ok(calls[3].path.endsWith('cursor=opaque%20cursor%2B'));
 assert.ok(calls.every(call=>!call.path.includes('agent-sessions')&&!call.path.includes('native-messages')));
});

const profile=n=>({id:'at02_fixture',revision:n,sha256:String(n).repeat(64)});
// bind_original persists generation0; checked_alias/_scope never rewrite it.
const originalSource=(n,project='new_project')=>({id:'original:'+String(n).repeat(64),identity:{kind:'original',project_id:project,session_id:'owner_'+n},original_source:{kind:'original',project_id:project,session_ref:{id:'owner_'+n,revision:1,sha256:String(n+2).repeat(64)},generation:0,provider_profile_ref:profile(n),workspace_ref:null}});
const original1=()=>originalSource(1),original2=()=>originalSource(2);
const currentOwner=()=>({status:'CURRENT',provider_generation:2,connection_operation_id:'connect_v2',provider_profile_ref:profile(2),original_source:{project_id:'new_project',session_ref:original2().original_source.session_ref,generation:2}});
const ownerScope=(owner=currentOwner(),sources=[original1(),original2()])=>({...scope(),sources,current_owner:owner});
const loaded=(source,generation=Number(source.identity.session_id.at(-1)))=>({...structuredClone(source.original_source),generation,owner_status:'READY',connected_owner:true});

// Exercise the actual App owner-resolution prefix, up to its first workspace/
// draft adoption. It makes real helper/transport calls with isolated transports.
const appText=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8');
const installStart=appText.indexOf('  async function installThread('),installBody=appText.indexOf('\n',installStart)+1;
const installEnd=appText.indexOf('    const workspaceRef=',installBody);
assert.ok(installStart>=0&&installEnd>installBody);
const ownerPrefix=appText.slice(installBody,installEnd).replace('let original:AgentSession|undefined;','let original;');
const AsyncFunction=Object.getPrototypeOf(async()=>{}).constructor;
const installOwner=new AsyncFunction('row','scope','navigationCurrent','signal','project','projects','allProjects','userProjects','api','agentSessionsApi','threadOriginalOwner','verifyThreadOriginalOwner','migration',javascriptBody(ownerPrefix+'\nreturn {publicSource,original};'));
async function appOwner(value,get=async(_pid,ref)=>loaded([original1(),original2()].find(source=>source.original_source.session_ref.id===ref.id))){
 const calls=[],project={id:'new_project'};
 const result=await installOwner({source:{project_id:'new_project'}},value,()=>true,undefined,()=>project,()=>[project],()=>[project],items=>items,
  {projects:async()=>{throw Error('unexpected catalog');},workspaces:async()=>{calls.push({method:'GET',kind:'workspaces'});return [];}},
  {get:async(pid,ref)=>{calls.push({method:'GET',kind:'owner',pid,ref});return get(pid,ref);}},thread.threadOriginalOwner,thread.verifyThreadOriginalOwner);
 return {result,calls};
}

test('CURRENT selection survives the typed projection, JSON restart and source order changes',async()=>{
 for(const sources of [[original1(),original2()],[original2(),original1()]]){
  const raw=ownerScope(currentOwner(),sources),parsed=parseThreadScope(raw,id),before=structuredClone(raw.sources);
  assert.deepEqual(parsed.current_owner,raw.current_owner);
  const restarted=JSON.parse(JSON.stringify(parsed));
  assert.equal(thread.threadOriginalOwner(restarted,'new_project').id,original2().id);
  const {result,calls}=await appOwner(parsed);
  assert.equal(result.original.session_ref.id,'owner_2');
  assert.deepEqual(calls.filter(call=>call.kind==='owner').map(call=>call.ref),[original2().original_source.session_ref]);
  assert.ok(calls.every(call=>call.method==='GET'));assert.deepEqual(raw.sources,before);
 }
});
test('scope refresh reads the persisted CURRENT projection without selecting or starting an owner',async()=>{
 const calls=[],client=new ConversationThreadClient({request:async(path,method='GET')=>{calls.push({path,method});return ownerScope();}});
 const first=await client.scope(id),restartedClient=new ConversationThreadClient({request:async(path,method='GET')=>{calls.push({path,method});return JSON.parse(JSON.stringify(ownerScope(currentOwner(),[original2(),original1()])));}});
 const refreshed=await restartedClient.scope(id);
 assert.equal(thread.threadOriginalOwner(first,null).id,thread.threadOriginalOwner(refreshed,null).id);
 assert.deepEqual(refreshed.current_owner,currentOwner());assert.equal(calls.length,2);
 assert.ok(calls.every(call=>call.method==='GET'&&call.path.endsWith('/scope')));
});
test('PENDING and unknown/corrupt owner protocols cannot fall back or adopt a writable old owner',async()=>{
 const pending={status:'PENDING',provider_generation:2,connection_operation_id:'connect_v2'};
 const parsed=parseThreadScope(ownerScope(pending),id);assert.deepEqual(parsed.current_owner,pending);
 let ownerReads=0;
 assert.equal((await appOwner(parsed,async()=>{ownerReads++;return loaded(original1());})).result.original,undefined);
 assert.equal(ownerReads,0);
 for(const bad of [null,[],{},'LEGACY',{status:'UNKNOWN_OUTCOME'},{status:'PENDING',provider_generation:0,connection_operation_id:'connect_v2'},
  {...pending,connection_operation_id:''},{...currentOwner(),provider_generation:1.5},{...currentOwner(),provider_profile_ref:null},
  {...currentOwner(),original_source:{...currentOwner().original_source,generation:0}}]){
  assert.throws(()=>parseThreadScope(ownerScope(bad),id),/UNAVAILABLE/);
 }
 // A typed value cannot bypass the parser and silently use the legacy branch.
 assert.throws(()=>thread.threadOriginalOwner({...parsed,current_owner:{status:'UNKNOWN_OUTCOME'}},null),/UNAVAILABLE/);
});
test('CURRENT must uniquely identify a member by immutable project, session and profile refs',()=>{
 const owner=currentOwner();
 for(const change of [
  {original_source:{...owner.original_source,project_id:'foreign_project'}},
  {original_source:{...owner.original_source,session_ref:{...owner.original_source.session_ref,id:'foreign_owner'}}},
  {original_source:{...owner.original_source,session_ref:{...owner.original_source.session_ref,revision:2}}},
  {original_source:{...owner.original_source,session_ref:{...owner.original_source.session_ref,sha256:'f'.repeat(64)}}},
  {provider_profile_ref:profile(1)},
 ])assert.throws(()=>thread.threadOriginalOwner(parseThreadScope(ownerScope({...owner,...change}),id),null),/UNAVAILABLE/);
 const duplicate={...original2(),id:'original:'+'f'.repeat(64)};
 assert.throws(()=>thread.threadOriginalOwner(parseThreadScope(ownerScope(owner,[original2(),duplicate]),id),null),/UNAVAILABLE/);
});
test('actual App rejects changed GET generation/profile/ref/project before draft adoption',async()=>{
 const parsed=parseThreadScope(ownerScope(),id),exact=loaded(original2());
 for(const change of [
  {generation:1},{provider_profile_ref:profile(1)},{provider_profile_ref:undefined},{project_id:'foreign_project'},
  {session_ref:{...exact.session_ref,sha256:'f'.repeat(64)}},
 ])await assert.rejects(appOwner(parsed,async()=>({...exact,...change})),/UNAVAILABLE/);
 const source=appText.slice(installBody,appText.indexOf('  async function openLegacyHistoryConversation',installBody));
 assert.ok(source.indexOf('verifyThreadOriginalOwner(')>=0&&source.indexOf('verifyThreadOriginalOwner(')<source.indexOf('saveComposerDraft()'));
});
test('multiple legacy actors require explicit choice while a sole actor remains readable',async()=>{
 for(const owner of [undefined,{status:'LEGACY'}]){
  const raw={...scope(),sources:[original1(),original2()],...(owner?{current_owner:owner}:{})},parsed=parseThreadScope(raw,id);
  assert.equal(thread.threadOriginalOwner(parsed,'new_project'),undefined);
  const foreign=originalSource(2,'old_project'),moved=parseThreadScope({...raw,sources:[foreign,original1()]},id);
  assert.equal(thread.threadOriginalOwner(moved,'old_project'),undefined);
  const legacyOnly=parseThreadScope({...scope(),sources:[foreign]},id);
  assert.equal(thread.threadOriginalOwner(legacyOnly,'old_project').id,foreign.id);
 }
});
test('actual bind_original alias0 remains immutable when CURRENT first opens at generation1 or loads generation2',async()=>{
 const alias=original2(),before=structuredClone(alias);
 for(const generation of [1,2]){
  const authority={...currentOwner(),original_source:{...currentOwner().original_source,generation}};
  const parsed=parseThreadScope(ownerScope(authority,[original1(),alias]),id);
  const {result}=await appOwner(parsed,async()=>loaded(alias,generation));
  assert.equal(result.original.generation,generation);
  assert.equal(result.publicSource.original_source.generation,0);
  assert.deepEqual(alias,before);assert.deepEqual(parsed.sources[1],before);
 }
});
test('legacy alias0 accepts fresh nonnegative GET generation but still verifies immutable identity/profile',async()=>{
 for(const current_owner of [undefined,{status:'LEGACY'}]){
  const raw={...scope(),sources:[original1()],...(current_owner?{current_owner}:{})},parsed=parseThreadScope(raw,id);
  for(const generation of [0,1,2]){
   const {result}=await appOwner(parsed,async()=>loaded(original1(),generation));
   assert.equal(result.original.generation,generation);assert.equal(result.publicSource.original_source.generation,0);
  }
  for(const generation of [-1,1.5,NaN,'1',undefined])await assert.rejects(appOwner(parsed,async()=>({...loaded(original1()),generation})),/UNAVAILABLE/);
  await assert.rejects(appOwner(parsed,async()=>({...loaded(original1()),provider_profile_ref:profile(2)})),/UNAVAILABLE/);
 }
});
test('CURRENT fresh generation can never be replaced by a later GET without a new authority projection',async()=>{
 const alias=original2(),first={...currentOwner(),original_source:{...currentOwner().original_source,generation:1}};
 const parsed=parseThreadScope(ownerScope(first,[alias]),id);
 await assert.rejects(appOwner(parsed,async()=>loaded(alias,2)),/UNAVAILABLE/);
 const refreshed=parseThreadScope(ownerScope(currentOwner(),[alias]),id);
 assert.equal((await appOwner(refreshed,async()=>loaded(alias,2))).result.original.generation,2);
});

function appBody(name,next){
 const source=ts.createSourceFile('App.tsx',appText,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);let found;
 const visit=node=>{if(ts.isFunctionDeclaration(node)&&node.name?.text===name)found=node;else ts.forEachChild(node,visit);};visit(source);
 assert.ok(found?.body,'Actual App callback missing: '+name);return found.body.getText(source).slice(1,-1);
}
const adoptBody=appBody('adoptOriginalConnection','  async function ensureOriginalForSend');
const ensureBody=appBody('ensureOriginalForSend','  async function inspectOriginalConnection');
const inspectBody=appBody('inspectOriginalConnection','  async function discardRejectedConnection');
const useBody=appBody('useOriginalSession','  function changeConversationAgentMode').split('    const managementWasOpen=')[0];
function connectionConsumer(next,incoming){
 const first={...currentOwner(),provider_generation:1,provider_profile_ref:profile(1),original_source:{project_id:'new_project',session_ref:original1().original_source.session_ref,generation:1}};
 const initial=parseThreadScope(ownerScope(first),id),pending={key:'original_connect_key',phase:'CONFIRMED'};
 const effects=[],state={scope:initial,owner:loaded(original1()),draft:'Keep this unsent draft.',pending,scopeWrites:0,ownerWrites:0,ack:0,modeWrites:0,calls:[],scopeReplies:[],panelOpen:true,errors:[]};
 const panelActive=value=>{state.panelOpen=value;for(const effect of effects)effect();};
 let advanceOriginalGeneration=()=>{};
 const context={
  threadClient:{scope:async()=>{state.calls.push('scope_GET');return state.scopeReplies.length?state.scopeReplies.shift():next;}},
  threadOriginalOwner:thread.threadOriginalOwner,verifyThreadOriginalOwner:thread.verifyThreadOriginalOwner,
  sameNativeReference:(a,b)=>!!a&&!!b&&a.id===b.id&&a.revision===b.revision&&a.sha256===b.sha256,
  allProjects:()=>[{id:'new_project'}],api:{projects:async()=>{state.calls.push('projects_GET');return [{id:'new_project'}];}},
  setProjects:()=>{state.calls.push('setProjects');},setThreadScope:value=>{state.scopeWrites++;state.scope=value;},
  selectOriginalSession:value=>{state.ownerWrites++;state.owner=value;advanceOriginalGeneration();},threadScope:()=>state.scope,
  originalConnection:{acknowledge:async()=>{state.ack++;state.pending=null;},
   open:async()=>{state.calls.push('original_open');return {bound:true,session:incoming};},
   inspect:async()=>{state.calls.push('original_inspect');return {bound:true,session:incoming};},retry:async()=>{throw Error('No retry authorized in this test');}},
  originalSession:()=>state.owner,providers:()=>[{profile_ref:profile(2)}],providerId:()=>profile(2).id,
  originalInCurrentProject:()=>true,originalSessionCanResumeForSend:()=>false,originalSessionReady:()=>true,
  setConnectionNotice:()=>{},uiLocale:()=> 'en',saveComposerDraft:()=>{state.calls.push('saveDraft');},
  openProviders:async()=>{throw Error('Unexpected settings');},workspace:()=>undefined,historySelectedItem:()=>undefined,
  prompt:()=>state.draft,nativeDraftAttachments:()=>[],currentDiscussionDraft:()=>({text:state.draft,attachments:[]}),configurationOwner,quickScope:()=>({providerProfileRef:profile(2)}),originalAgent:()=> 'build',
  navigation:{capture:()=>()=>true},action:async(_label,work)=>work(),sameReview:(a,b)=>JSON.stringify(a)===JSON.stringify(b),
  pendingConnection:()=>({id,pending:state.pending,error:false}),pendingOwnerSelection:()=>({id,pending:null,error:false}),selectedThreadId:()=>id,
  changeConversationAgentMode:()=>{state.modeWrites++;return true;},batch:work=>work(),setToolMode:()=>{},
  showAgentChat:()=>{},sessionManagementOpen:()=>state.panelOpen,closeSessionManagement:()=>panelActive(false),refreshThread:async()=>{},
  authoredErrorCopy:reason=>reason.message,setError:value=>state.errors.push(value),createEffect:effect=>{effects.push(effect);effect();},
 };
 const run=(body,names,args)=>new AsyncFunction(...names,...Object.keys(context),javascriptBody(body))(...args,...Object.values(context));
 context.adoptOriginalConnection=(...args)=>run(adoptBody,['id','value','current'],args);
 const panel=appText.slice(appText.indexOf('      <AgentSessionPanel ')),handler=panel.match(/onSelect=\{([A-Za-z]+)\}/)?.[1];
 assert.equal(handler,'selectOriginalSessionFromPanel');
 const sharedStart=appText.indexOf('  let originalPanelSelectionRequest='),sharedEnd=appText.indexOf('  async function useOriginalSession',sharedStart);
 // The real token/effect/handler lexical scope must survive multiple calls.
 const shared=sharedStart<0?undefined:appText.slice(sharedStart,sharedEnd).replace('selectOriginalSessionFromPanel(value?:AgentSession)','selectOriginalSessionFromPanel(value)');
 const panelController=shared?new Function(...Object.keys(context),javascriptBody('let originalGeneration=0;\n'+shared+'\nreturn {select:selectOriginalSessionFromPanel,advance:()=>{originalGeneration++;}};'))(...Object.values(context)):undefined;
 if(panelController)advanceOriginalGeneration=panelController.advance;
 const select=panelController?.select??(value=>run(appBody(handler,'  async function useOriginalSession'),['value'],[value]));
 return {state,pending,initial,panelActive,adoptElsewhere:(value,scope)=>{context.setThreadScope(scope);context.selectOriginalSession(value);},
  complete:()=>run(ensureBody,['scope','text','attachments','agent'],[initial,state.draft,[],'build']),
  inspect:()=>run(inspectBody,['retry'],[false]),
  use:()=>run(useBody+"\nreturn 'LEGACY_PATH_NOT_UNDER_TEST';",['value','agent'],[incoming,'build']),
  panelSelect:(...args)=>select(args.length?args[0]:incoming)};
}
const pendingOwnerScope=()=>parseThreadScope(ownerScope({status:'PENDING',provider_generation:2,connection_operation_id:'connect_v2'}),id);
function assertNoAdoption(h){
 assert.equal(h.state.ack,0);assert.equal(h.state.pending,h.pending);assert.equal(h.state.scopeWrites,0);assert.equal(h.state.ownerWrites,0);
 assert.equal(h.state.scope,h.initial);assert.equal(h.state.owner.session_ref.id,'owner_1');assert.equal(h.state.draft,'Keep this unsent draft.');
}
test('actual connection completion callback rejects historical hint/PENDING before setters or ack and preserves the input',async()=>{
 for(const [next,value] of [[parseThreadScope(ownerScope(),id),loaded(original1())],[pendingOwnerScope(),loaded(original2())]]){
  const h=connectionConsumer(next,value);assert.equal(await h.complete(),false);assertNoAdoption(h);
  assert.equal(h.state.calls.filter(call=>call==='original_open').length,1);assert.equal(h.state.calls.filter(call=>call==='scope_GET').length,1);
 }
 const valid=connectionConsumer(parseThreadScope(ownerScope(),id),loaded(original2()));assert.equal(await valid.complete(),true);
 assert.equal(valid.state.ack,1);assert.equal(valid.state.pending,null);assert.equal(valid.state.owner.session_ref.id,'owner_2');assert.equal(valid.state.draft,'Keep this unsent draft.');
});
test('actual inspect callback preserves pending for stale/PENDING and acknowledges only the exact current v2 once',async()=>{
 for(const [next,value] of [[parseThreadScope(ownerScope(),id),loaded(original1())],[pendingOwnerScope(),loaded(original2())],
  [parseThreadScope(ownerScope(),id),loaded(original2(),1)]]){
  const h=connectionConsumer(next,value);await h.inspect();assertNoAdoption(h);
  assert.equal(h.state.calls.filter(call=>call==='original_inspect').length,1);assert.equal(h.state.calls.filter(call=>call==='scope_GET').length,1);
 }
 const valid=connectionConsumer(parseThreadScope(ownerScope(),id),loaded(original2()));await valid.inspect();
 assert.equal(valid.state.ack,1);assert.equal(valid.state.owner.session_ref.id,'owner_2');assert.equal(valid.state.pending,null);
});
test('actual settings owner adoption refuses historical/PENDING/stale generation without changing work mode or draft',async()=>{
 for(const [next,value] of [[parseThreadScope(ownerScope(),id),loaded(original1())],[pendingOwnerScope(),loaded(original2())],
  [parseThreadScope(ownerScope(),id),loaded(original2(),1)]]){
  const h=connectionConsumer(next,value);assert.equal(await h.use(),false);assertNoAdoption(h);assert.equal(h.state.modeWrites,0);
  assert.equal(h.state.calls.filter(call=>call==='scope_GET').length,1);
 }
 const valid=connectionConsumer(parseThreadScope(ownerScope(),id),loaded(original2()));assert.equal(await valid.use(),true);
 assert.equal(valid.state.modeWrites,1);assert.equal(valid.state.owner.session_ref.id,'owner_2');assert.equal(valid.state.ack,0);
});
test('actual Panel onSelect rejects historical/PENDING while fresh LOAD generation and disconnect observations remain admissible',async()=>{
 for(const [next,value] of [[parseThreadScope(ownerScope(),id),loaded(original1())],[pendingOwnerScope(),loaded(original2())],
  [parseThreadScope(ownerScope(),id),loaded(original2(),1)]]){
  const h=connectionConsumer(next,value);await h.panelSelect();assertNoAdoption(h);assert.equal(h.state.modeWrites,0);
 }
 const reloaded=connectionConsumer(parseThreadScope(ownerScope(),id),loaded(original2(),2));
 reloaded.state.owner=loaded(original2(),1);await reloaded.panelSelect();
 assert.equal(reloaded.state.owner.generation,2);assert.equal(reloaded.state.ownerWrites,1);assert.equal(reloaded.state.ack,0);assert.equal(reloaded.state.modeWrites,0);
 const disconnected=connectionConsumer(parseThreadScope(ownerScope(),id),{...loaded(original2()),connected_owner:false,owner_status:'DISCONNECTED'});
 await disconnected.panelSelect();assert.equal(disconnected.state.owner.owner_status,'DISCONNECTED');assert.equal(disconnected.state.ownerWrites,1);
 const unchanged=connectionConsumer(parseThreadScope(ownerScope(),id),undefined);
 // Panel's project-change effect may emit undefined; a canonical owner is not
 // silently dropped. Noncanonical settings keep their existing clear behavior.
 await unchanged.panelSelect();assertNoAdoption(unchanged);assert.equal(unchanged.state.calls.length,0);
 unchanged.state.scope=undefined;await unchanged.panelSelect();assert.equal(unchanged.state.owner,undefined);
});
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
test('overlapping actual Panel callbacks keep the newest generation in either response order',async()=>{
 for(const oldReturnsFirst of [false,true]){
  const h=connectionConsumer(parseThreadScope(ownerScope(),id),loaded(original2())),old=deferred(),latest=deferred();
  h.state.scopeReplies.push(old.promise,latest.promise);
  const p1=h.panelSelect(loaded(original2(),1)),p2=h.panelSelect(loaded(original2(),2));
  const firstScope=parseThreadScope(ownerScope({...currentOwner(),original_source:{...currentOwner().original_source,generation:1}}),id);
  if(oldReturnsFirst){old.resolve(firstScope);await p1;assert.equal(h.state.ownerWrites,0);}
  latest.resolve(parseThreadScope(ownerScope(),id));await p2;
  assert.equal(h.state.owner.generation,2);assert.equal(h.state.ownerWrites,1);assert.equal(h.state.scope.current_owner.original_source.generation,2);
  if(!oldReturnsFirst){old.resolve(firstScope);await p1;}
  assert.equal(h.state.owner.generation,2);assert.equal(h.state.ownerWrites,1);assert.equal(h.state.errors.length,0);
  assert.equal(h.state.pending,h.pending);assert.equal(h.state.ack,0);assert.equal(h.state.draft,'Keep this unsent draft.');
 }
});
test('undefined selection and closing the Panel invalidate pending actual callbacks including a close/reopen',async()=>{
 for(const action of ['undefined','close','close-reopen']){
  const h=connectionConsumer(parseThreadScope(ownerScope(),id),loaded(original2())),waiting=deferred();h.state.scopeReplies.push(waiting.promise);
  const pending=h.panelSelect();
  if(action==='undefined')await h.panelSelect(undefined);else {h.panelActive(false);if(action==='close-reopen')h.panelActive(true);}
  waiting.resolve(parseThreadScope(ownerScope(),id));await pending;assertNoAdoption(h);assert.equal(h.state.errors.length,0);
 }
 const closed=connectionConsumer(parseThreadScope(ownerScope(),id),loaded(original2()));closed.panelActive(false);
 await closed.panelSelect();assertNoAdoption(closed);assert.equal(closed.state.calls.length,0);
 const staleError=connectionConsumer(parseThreadScope(ownerScope(),id),loaded(original2())),old=deferred();
 staleError.state.scopeReplies.push(old.promise);const pending=staleError.panelSelect();staleError.panelActive(false);old.reject(Error('Old request failed'));
 await pending;assertNoAdoption(staleError);assert.equal(staleError.state.errors.length,0);
});
test('a non-Panel owner adoption invalidates an older Panel reply in the same thread without another Panel request',async()=>{
 const h=connectionConsumer(parseThreadScope(ownerScope(),id),loaded(original2(),1)),waiting=deferred();h.state.scopeReplies.push(waiting.promise);
 const pending=h.panelSelect();h.adoptElsewhere(loaded(original2(),2),parseThreadScope(ownerScope(),id));
 waiting.resolve(parseThreadScope(ownerScope({...currentOwner(),original_source:{...currentOwner().original_source,generation:1}}),id));await pending;
 assert.equal(h.state.owner.generation,2);assert.equal(h.state.scope.current_owner.original_source.generation,2);
 assert.equal(h.state.ownerWrites,1);assert.equal(h.state.scopeWrites,1);assert.equal(h.state.ack,0);assert.equal(h.state.pending,h.pending);
});
test('same-thread revision change invalidates an older Panel success and error without a navigation replacement',async()=>{
 for(const reject of [false,true]){
  const h=connectionConsumer(parseThreadScope(ownerScope(),id),loaded(original2())),waiting=deferred();h.state.scopeReplies.push(waiting.promise);
  const pending=h.panelSelect(),newScope={...h.state.scope,conversation_ref:{...h.state.scope.conversation_ref,revision:3}};h.state.scope=newScope;
  if(reject)waiting.reject(Error('Old revision failed'));else waiting.resolve(parseThreadScope(ownerScope(),id));await pending;
  assert.equal(h.state.scope,newScope);assert.equal(h.state.ownerWrites,0);assert.equal(h.state.scopeWrites,0);assert.equal(h.state.errors.length,0);
  assert.equal(h.state.draft,'Keep this unsent draft.');assert.equal(h.state.pending,h.pending);
 }
});

// batch34 AUTHORED_NOT_RUN. Existing App prefix and exact stored actor refs.
test('SELECTED legacy owner is shared independently of source order and logical project',async()=>{
 const selected={status:'SELECTED',selection_operation_id:'choose_original',provider_profile_ref:profile(2),original_source:currentOwner().original_source};
 for(const sources of [[original1(),original2()],[original2(),original1()]]){const parsed=parseThreadScope(ownerScope(selected,sources),id);assert.equal(thread.threadOriginalOwner(parsed,null).id,original2().id);const observed=await appOwner(parsed);assert.equal(observed.result.original.session_ref.id,'owner_2');}
});
test('SELECTION_REQUIRED opens metadata without reading or adopting any old actor',async()=>{
 const parsed=parseThreadScope(ownerScope({status:'SELECTION_REQUIRED',selection_operation_id:'choice_unavailable'}),id);let reads=0;const observed=await appOwner(parsed,async()=>{reads++;throw Error('must not read an arbitrary old actor');});assert.equal(reads,0);assert.equal(observed.result.original,undefined);
});
test('SELECTED choice with changed generation cannot adopt the GET from a different generation',async()=>{
 const selected={status:'SELECTED',selection_operation_id:'choice',provider_profile_ref:profile(2),original_source:currentOwner().original_source};await assert.rejects(appOwner(parseThreadScope(ownerScope(selected),id),async()=>loaded(original2(),3)),/UNAVAILABLE/);
});
test('corrupt SELECTED owner cannot fall back to a sole legacy actor',()=>{
 const selected={status:'SELECTED',selection_operation_id:'choice',provider_profile_ref:profile(2),original_source:currentOwner().original_source};assert.throws(()=>parseThreadScope(ownerScope(selected,[original1()]),id),/UNAVAILABLE/);
});
