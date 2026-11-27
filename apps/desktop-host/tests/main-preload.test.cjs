'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {EventEmitter}=require('node:events');
test('discussion history exposes only scoped typed reads',async()=>{
  const value=preload(),scope={projectId:null},target={projectId:'project',conversationId:'discussion',operationId:'original'};
  await value.bridge.getNativeConversations(scope);await value.bridge.getNativeConversation(target);
  assert.deepEqual(value.calls.map(row=>row.channel),['eeas:native-conversation-directory','eeas:native-conversation-get']);
  assert.equal(value.calls[0].body,scope);assert.equal(value.calls[1].body,target);assert.equal(value.bridge.fetch,undefined);
});
test('context-file opening passes an exact source and scope through a narrow IPC without exposing paths',async()=>{
  const value=preload(),source={kind:'FILE_SNAPSHOT',project_id:'original-project',object_id:'a'.repeat(64),
    revision:1,sha256:'b'.repeat(64),file_identity_sha256:'c'.repeat(64),
    link_ref:{project_id:'original-project',id:'d'.repeat(64),revision:1,sha256:'e'.repeat(64)}},
    scope={projectId:'current-project',conversationId:'current-discussion'};
  await value.bridge.openContextFile(source,scope);
  assert.deepEqual(value.calls.map(row=>row.channel),['eeas:open-context-file']);
  assert.equal(value.calls[0].body.source,source);assert.equal(value.calls[0].body.scope,scope);
  assert.equal(value.bridge.openPath,undefined);assert.equal(value.bridge.shell,undefined);
});
async function settle(){for(let n=0;n<15;n++)await Promise.resolve();}
function preload(){
  let bridge;const emitter=new EventEmitter(),calls=[];
  emitter.invoke=async(channel,...args)=>{calls.push({channel,body:args[0],args});return {status:'OBSERVED'};};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../preload.cjs'),'utf8'),{
    require:id=>{assert.equal(id,'electron');return {contextBridge:{exposeInMainWorld:(_name,value)=>{bridge=value;}},ipcRenderer:emitter};},
  });return {bridge,emitter,calls};
}
test('close bridge waits for its sole real draft owner before acknowledging SAVED',async()=>{
  const value=preload();let finish;const dispose=value.bridge.onDraftFlushRequested(()=>new Promise(resolve=>{finish=resolve;}));
  assert.throws(()=>value.bridge.onDraftFlushRequested(()=>Promise.resolve()),/One draft/);
  value.emitter.emit('eeas:flush-drafts',null,{id:'1'.repeat(32)});await settle();
  assert.equal(value.calls.length,0);finish();await settle();
  assert.equal(value.calls[0].channel,'eeas:close-flush-complete');
  assert.equal(value.calls[0].body.status,'SAVED');assert.equal(value.calls[0].body.id,'1'.repeat(32));
  dispose();assert.equal(value.emitter.listenerCount('eeas:flush-drafts'),0);
});
test('failed or unknown draft writes acknowledge FAILED and malformed requests authorize nothing',async()=>{
  const value=preload();let calls=0;
  value.bridge.onDraftFlushRequested(async()=>{calls++;throw Error('write outcome unknown');});
  for(const body of [null,{id:'../other'},{id:'z'.repeat(32)}])value.emitter.emit('eeas:flush-drafts',null,body);
  await settle();assert.equal(calls,0);assert.equal(value.calls.length,0);
  value.emitter.emit('eeas:flush-drafts',null,{id:'a'.repeat(32)});await settle();
  assert.equal(calls,1);assert.equal(value.calls[0].body.status,'FAILED');
});
test('quick drafts retain exact references and expose separate save acknowledgement and explicit send',async()=>{
  const value=preload(),ref={id:'draft',revision:2,sha256:'c'.repeat(64)};
  await value.bridge.ackQuickDraft(ref);await value.bridge.openQuickDraft(ref);
  await value.bridge.submitQuickDraft({ref,text:'补充条件'});
  assert.deepEqual(value.calls.map(item=>item.channel),['eeas:quick-draft-ack','eeas:quick-draft-open','eeas:quick-draft-submit']);
  assert.equal(value.calls[1].body,ref);assert.equal(value.calls[2].body.ref,ref);
  assert.equal(value.bridge.invoke,undefined);assert.equal(value.bridge.shell,undefined);
});

test('language has a narrow preference API and a disposable live event listener',async()=>{
  const value=preload();await value.bridge.getUILanguage();await value.bridge.setUILanguage('en');
  assert.deepEqual(value.calls.map(item=>item.channel),['eeas:ui-language-get','eeas:ui-language-set']);
  assert.equal(value.calls[1].body,'en');
  let observed;const dispose=value.bridge.onUILanguageChanged(state=>{observed=state;});
  const state={preference:'en',resolved_locale:'en'};value.emitter.emit('eeas:ui-language-changed',null,state);
  assert.equal(observed,state);dispose();assert.equal(value.emitter.listenerCount('eeas:ui-language-changed'),0);
  assert.throws(()=>value.bridge.onUILanguageChanged(null),/listener required/);
  assert.equal(value.bridge.reload,undefined);assert.equal(value.bridge.invoke,undefined);
});
test('capability reads expose only exact typed MCP, owner tools and original operation channels',async()=>{
  const value=preload(),ref={id:'review-local',revision:2,sha256:'a'.repeat(64)},scope={project_id:'project',session_ref:ref,generation:1};
  await value.bridge.readAgentMCP(ref.id,ref);await value.bridge.getAgentTools(scope);await value.bridge.inspectAgentCapabilityOperation('save-once');
  assert.deepEqual(value.calls.map(item=>item.channel),['eeas:agent-mcp-read','eeas:agent-tools-read','eeas:agent-capability-operation']);
  assert.equal(value.calls[0].args[1],ref);assert.equal(value.calls[1].body,scope);assert.equal(value.calls[2].body,'save-once');
  assert.equal(value.bridge.invoke,undefined);assert.equal(value.bridge.fetch,undefined);
});

test('coordinator bridge has typed calls and disposable shared-store/open events',async()=>{
  const value=preload(),profile={id:'profile',revision:1,sha256:'a'.repeat(64)};
  await value.bridge.getSupervisorConversation();await value.bridge.saveSupervisorDraft({text:'draft',expectedRevision:0});
  await value.bridge.askSupervisor({operationId:'question-one',text:'question',providerProfileRef:profile});
  await value.bridge.inspectSupervisor('question-one');await value.bridge.saveSupervisorLocalAnswer({question:'status',answer:'local'});
  assert.deepEqual(value.calls.map(row=>row.channel),['eeas:supervisor-conversation-get','eeas:supervisor-draft-save','eeas:supervisor-ask','eeas:supervisor-inspect','eeas:supervisor-local-save']);
  let snapshot,opens=0;const release=value.bridge.onSupervisorConversation(raw=>{snapshot=raw;}),close=value.bridge.onSupervisorOpen(()=>{opens++;});
  const state={revision:2};value.emitter.emit('eeas:supervisor-conversation-changed',null,state);value.emitter.emit('eeas:supervisor-open',null);
  assert.equal(snapshot,state);assert.equal(opens,1);release();close();
  assert.equal(value.emitter.listenerCount('eeas:supervisor-conversation-changed'),0);assert.equal(value.emitter.listenerCount('eeas:supervisor-open'),0);
  assert.equal(value.bridge.invoke,undefined);assert.equal(value.bridge.shell,undefined);
});
test('history and old-operation reads use two precise read-only channels without an arbitrary invoke',async()=>{
 const value=preload(),page={cursor:null,limit:24},ref={operation_id:'old',request_sha256:'a'.repeat(64)};
 await value.bridge.getSupervisorHistoryPage(page);await value.bridge.getSupervisorOperation(ref);
 assert.deepEqual(value.calls.map(row=>row.channel),['eeas:supervisor-history-read','eeas:supervisor-operation-read']);assert.equal(value.calls[0].body,page);assert.equal(value.calls[1].body,ref);assert.equal(value.bridge.invoke,undefined);
});

// AUTHORED_NOT_RUN: preload UNIT, no Main/API invocation.
test('capture-channel readiness exposes no generic call, scope, actor or execution primitive',async()=>{
 const value=preload();await value.bridge.ensureEditorCaptureChannel({path:'IGNORED',action:'SAVE'});assert.equal(value.calls.length,1);assert.equal(value.calls[0].channel,'eeas:editor-capture-channel-ensure');assert.equal(value.calls[0].args.length,0);assert.equal(value.bridge.invoke,undefined);assert.equal(value.bridge.request,undefined);
});

// AUTHORED_NOT_RUN batch39: narrow typed private recovery IPC only.
test('native source recovery exposes preparation, original command and original query without a generic API bridge',async()=>{
 const value=preload(),source={parentOperationId:'native_parent',expectedNativeRef:{id:'native_parent',revision:1,sha256:'a'.repeat(64)}};
 const request={operation_id:'native_recovery_'+'b'.repeat(48),expected_native_ref:source.expectedNativeRef,preparation_sha256:'c'.repeat(64)};
 await value.bridge.prepareNativeContextRecovery(source);
 await value.bridge.restartNativeContextRecovery({parentOperationId:source.parentOperationId,request});
 await value.bridge.inspectNativeContextRecovery({parentOperationId:source.parentOperationId,operationId:request.operation_id,requestSha256:'d'.repeat(64)});
 assert.deepEqual(value.calls.map(c=>c.channel),['eeas:native-context-recovery-prepare','eeas:native-context-recovery-restart','eeas:native-context-recovery-operation']);
 assert.equal(value.calls[0].body,source);assert.equal(value.calls[1].body.request,request);
 assert.equal(value.bridge.request,undefined);assert.equal(value.bridge.fetch,undefined);
});
