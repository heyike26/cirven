'use strict';
// AUTHORED_NOT_RUN. Actual readonly native collection/private state/controller;
// original API and process ownership are explicit UNIT boundaries.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {pathToFileURL,fileURLToPath}=require('node:url');
const {NativeEditor}=require('../native-editor.cjs'),{PrivateState,CompanionController,draftKey}=require('../controller.cjs');
const {METHODS,keyOf}=require('../editor-observer.cjs'),{canonical,hash}=require('../client.cjs');
const copy=v=>JSON.parse(JSON.stringify(v)),digest=v=>hash(canonical(v));
function scene(t){const base=process.env.EEAS_TEST_TMPDIR||process.env.TMPDIR||os.tmpdir();assert.ok(!/^(?:\/mnt\/d(?:\/|$)|d:[\\/])/i.test(base),'No D-drive test temporary is allowed');if(fs.existsSync(base))assert.ok(!/^(?:\/mnt\/d(?:\/|$)|d:[\\/])/i.test(fs.realpathSync(base)));fs.mkdirSync(base,{recursive:true});const folder=fs.mkdtempSync(path.join(base,'cirven-observer-'));t.after(()=>fs.rmSync(folder,{recursive:true,force:true}));
 const root=path.join(folder,'workspace');fs.mkdirSync(root);const filename=path.join(root,'a.ts');fs.writeFileSync(filename,'SAVED_ORIGINAL');
 const uri=value=>{const u=new URL(value);return {scheme:u.protocol.slice(0,-1),authority:u.host,query:u.search.slice(1),fragment:u.hash.slice(1),fsPath:fileURLToPath(u),toString:()=>value};};
 const doc={uri:uri(pathToFileURL(filename).href),version:1,isDirty:false,isClosed:false,text:'SAVED_ORIGINAL',getText(){return this.text;}};
 const events=new Map(),workspace={textDocuments:[doc]};let disposed=0;for(const name of ['onDidOpenTextDocument','onDidChangeTextDocument','onDidSaveTextDocument','onDidCloseTextDocument'])workspace[name]=fn=>{events.set(name,fn);return {dispose(){disposed++;}};};
 const host=new NativeEditor({Uri:{parse:uri},workspace}),state=new PrivateState(path.join(folder,'private')),c=new CompanionController({state});t.after(()=>c.dispose());
 c.selected={project_id:'qa',session_ref:{id:'original',revision:1,sha256:'a'.repeat(64)},generation:1,workspace_ref:{id:'b'.repeat(64),revision:1,binding_sha256:'c'.repeat(64)}};
 const calls=[],records=new Map();let revision=0,lose=false;const actor='paired_client';
 c.client={ticket:{client_id:actor},methods:METHODS,supportedMethods:METHODS,close(){},rpc:async(method,scope,payload)=>{calls.push({method,payload:copy(payload)});
  if(method==='session.editor.observer.prepare'){const st=fs.statSync(root,{bigint:true}),basis={scope:{...scope,workspace_ref:payload.workspace_ref},observer_id:keyOf(scope,payload.workspace_ref,actor,payload.editor_window_id),revision,runtime_id:'unit_runtime',action:payload.action,editor_client_id:actor,editor_window_id:payload.editor_window_id,editor_process_id:process.pid,editor_parent_process_id:process.ppid,root_identity:{device:st.dev.toString(),inode:st.ino.toString()},root_path_sha256:digest(root),process_identity:{pid:process.pid,parent_pid:process.ppid,birth:'1700000000.0',parent_birth:'1699999900.0'}};
   return {status:'OK',result:{kind:'EDITOR_OBSERVER_PREPARATION',...basis,preparation_sha256:digest(basis),root_uri:pathToFileURL(root).href,read_only:true,workspace_writes:0,model_calls:0,native_posts:0,automatic_replay:false}};}
  if(method==='session.editor.observer.operation')return {status:'OK',result:copy(records.get(payload.operation_id))};
  assert.equal(method,'session.editor.observer.command');const r={...payload,session_ref:scope.session_ref,generation:scope.generation,editor_client_id:actor},slot=Object.values(JSON.parse(fs.readFileSync(state.filename)).editor_observers)[0];assert.equal(slot.stage,'INTENT');assert.ok(slot.intents.some(i=>i.request.operation_id===r.operation_id));assert.doesNotMatch(JSON.stringify(slot),/UNSAVED_SECRET/);
  revision++;const dirty=payload.observation.documents.filter(d=>d.is_dirty),observer={id:slot.id,project_id:scope.project_id,workspace_ref:payload.workspace_ref,editor_client_id:actor,editor_window_id:payload.editor_window_id,phase:payload.action==='RETIRE'?'RETIRED':'ACTIVE',revision,observation_state:payload.observation.availability,current:true,dirty_count:dirty.length,dirty_paths:dirty.map(d=>d.path),observed_documents:payload.observation.documents.length,capture_ref:payload.observation.capture_ref??null};
  const result={kind:'EDITOR_OBSERVER_ACTION',id:r.operation_id,request_sha256:digest(r),session_ref:scope.session_ref,generation:scope.generation,editor_client_id:actor,editor_window_id:payload.editor_window_id,status:'RETURNED',action:payload.action,observer,automatic_replay:false,permission_granted:false,atomic_editor_write_guarantee:false,observation_time_scope:'AT_ORIGINAL_COMMAND_ONLY',workspace_writes:0,model_calls:0,native_posts:0};records.set(r.operation_id,copy(result));if(lose){lose=false;throw Error('UNIT lost metadata ACK');}return {status:'OK',result};
 }};c.editorObservers.bind(host,()=>true);
 return {c,state,host,doc,workspace,events,root,filename,calls,folder,get disposed(){return disposed;},lose(){lose=true;},entry:()=>c.editorObservers.snapshot().entries[0]};}
test('readonly capture records local dirty metadata without saving, reloading, copying text or changing the original draft',async t=>{
 const x=scene(t);x.c.setDraft(draftKey(x.c.selected),0,'KEEP_ROOT_DRAFT');x.doc.text='UNSAVED_SECRET';x.doc.isDirty=true;x.doc.version++;
 await assert.rejects(x.c.editorObservers.ensureSelected(),{code:'DIRTY_EDITOR'});assert.equal(x.entry().dirtyCount,1);assert.equal(x.doc.text,'UNSAVED_SECRET');assert.equal(fs.readFileSync(x.filename,'utf8'),'SAVED_ORIGINAL');assert.equal(x.c.snapshot().draft.text,'KEEP_ROOT_DRAFT');assert.equal(x.c.state.value.requests.length,0);
});
test('ongoing dirty typing is coalesced, while an explicit clean save transition publishes once',async t=>{
 const x=scene(t);await x.c.editorObservers.ensureSelected();x.doc.isDirty=true;x.doc.text='UNSAVED_SECRET';x.doc.version++;
 for(let i=0;i<20;i++){x.doc.version++;x.events.get('onDidChangeTextDocument')({document:x.doc});}await new Promise(r=>setTimeout(r,160));await x.c.editorObservers.chain;
 assert.equal(x.calls.filter(r=>r.method==='session.editor.observer.command').length,2);
 for(let i=0;i<20;i++){x.doc.version++;x.events.get('onDidChangeTextDocument')({document:x.doc});}await new Promise(r=>setTimeout(r,160));assert.equal(x.calls.filter(r=>r.method==='session.editor.observer.command').length,2);
 x.doc.isDirty=false;x.events.get('onDidSaveTextDocument')(x.doc);await new Promise(r=>setTimeout(r,160));await x.c.editorObservers.chain;assert.equal(x.calls.filter(r=>r.method==='session.editor.observer.command').length,3);assert.equal(x.entry().dirtyCount,0);
});
test('lost observer ACK keeps one private intent and uses only original operation inspection',async t=>{
 const x=scene(t);x.lose();await assert.rejects(x.c.editorObservers.ensureSelected(),{code:'ORIGINAL_REQUEST_UNRESOLVED'});assert.equal(x.entry().pending,true);
 await x.c.editorObservers.act(x.entry().token,'inspect');assert.equal(x.entry().pending,false);assert.equal(x.calls.filter(r=>r.method==='session.editor.observer.command').length,1);assert.equal(x.calls.filter(r=>r.method==='session.editor.observer.operation').length,1);
});
test('namespace replacement preserves complete local unsaved capture but never declares a clean observed root',async t=>{
 const x=scene(t);await x.c.editorObservers.ensureSelected();const entry=Object.values(x.state.value.editor_observers)[0],st=fs.statSync(x.root,{bigint:true}),p={root_uri:entry.root_uri,root_identity:{device:st.dev.toString(),inode:st.ino.toString()}};
 fs.renameSync(x.root,x.root+'-retained');fs.mkdirSync(x.root);fs.writeFileSync(x.filename,'REPLACEMENT');x.doc.text='UNSAVED_SECRET';x.doc.isDirty=true;
 const captured=x.host.observeWorkspace(p);assert.equal(captured.availability,'UNAVAILABLE');assert.equal(captured.unavailable_reason,'NAMESPACE_CHANGED');assert.equal(captured.documents_complete,true);assert.equal(captured.documents[0].is_dirty,true);assert.equal(fs.readFileSync(x.filename,'utf8'),'REPLACEMENT');
});
test('private recovery preserves old intent bytes and cannot silently publish as an old live window',async t=>{
 const x=scene(t);x.lose();await assert.rejects(x.c.editorObservers.ensureSelected());const bytes=fs.readFileSync(x.state.filename),state=new PrivateState(path.dirname(x.state.filename));state.recover(path.basename(x.state.filename));const c=new CompanionController({state});t.after(()=>c.dispose());c.client=x.c.client;c.editorObservers.bind(x.host,()=>true);
 const entry=c.editorObservers.snapshot().entries[0];assert.equal(entry.recovered,true);assert.equal(entry.pending,true);assert.deepEqual(fs.readFileSync(x.state.filename),bytes);
 await assert.rejects(c.editorObservers.serial(()=>c.editorObservers.publish(entry.key,{force:true})),{code:'EDITOR_ORIGINAL_PROCESS_UNCONFIRMED'});assert.equal(x.calls.filter(r=>r.method==='session.editor.observer.command').length,1);
});
test('partial large documents stay unavailable and event subscriptions dispose without closing any document',async t=>{
 const x=scene(t);x.doc.text='x'.repeat(524289);await assert.rejects(x.c.editorObservers.ensureSelected(),{code:'EDITOR_OBSERVATION_REQUIRED'});assert.equal(x.entry().availability,'UNAVAILABLE');assert.equal(x.doc.isClosed,false);
 x.c.editorObservers.dispose();assert.equal(x.disposed,4);assert.equal(x.doc.text.length,524289);
});
test('typing while a clean observation ACK is in flight prevents the original send preflight from succeeding',async t=>{
 const x=scene(t);await x.c.editorObservers.ensureSelected();const original=x.c.client.rpc;let release;
 x.c.client.rpc=async(...args)=>{const result=await original(...args);if(args[0]==='session.editor.observer.command'&&!release)return new Promise(resolve=>{release=()=>resolve(result);});return result;};
 const pending=x.c.editorObservers.ensureSelected();while(!release)await new Promise(r=>setImmediate(r));
 x.doc.text='UNSAVED_SECRET';x.doc.isDirty=true;x.doc.version++;x.events.get('onDidChangeTextDocument')({document:x.doc});release();
 await assert.rejects(pending,{code:'EDITOR_DOCUMENT_CHANGED'});assert.equal(x.doc.text,'UNSAVED_SECRET');assert.equal(fs.readFileSync(x.filename,'utf8'),'SAVED_ORIGINAL');
});
test('the original session send keeps its private draft and does not dispatch a model message after dirty capture',async t=>{
 const x=scene(t),key=draftKey(x.c.selected);x.c.setDraft(key,0,'KEEP_UNSENT_MESSAGE');x.doc.text='UNSAVED_SECRET';x.doc.isDirty=true;x.doc.version++;
 await assert.rejects(x.c.send(key,1,'build'),{code:'DIRTY_EDITOR'});assert.equal(x.c.state.value.drafts[key].text,'KEEP_UNSENT_MESSAGE');assert.equal(x.calls.filter(v=>v.method==='session.message').length,0);
});


// Group transport/server are explicit UNIT boundaries. Actual readonly Native
// capture, private intent, source controller and event scheduling are retained.
function enableGroups(x,{lose=false,remoteDirty=false}={}){
 const {METHODS:groups}=require('../editor-capture.cjs'),base=x.c.client.rpc,originals=new Map();let receive,streamWindow;
 x.c.client.methods=[...METHODS,...groups];x.c.client.supportedMethods=x.c.client.methods;
 x.c.client.observeCaptures=(window,callback)=>{receive=callback;streamWindow=window;let active=true;return {ready:Promise.resolve(),get active(){return active;},dispose(){active=false;}};};
 const makeStatus=intent=>({kind:'EDITOR_CAPTURE_STATUS',id:intent.id,capture_ref:intent.capture_ref,scope:intent.scope,editor_client_id:intent.editor_client_id,request_sha256:intent.request_sha256,state:remoteDirty?'DIRTY':'READY',required_windows:1,ready_windows:remoteDirty?0:1,waiting_windows:0,dirty_windows:remoteDirty?1:0,unavailable_windows:0,expires_at_ms:intent.expires_at_ms,read_only:true,permission_granted:false,automatic_replay:false,workspace_writes:0,model_calls:0,native_posts:0,atomic_editor_write_guarantee:false,status_scope:'CURRENT_METADATA_SNAPSHOT_NOT_EXECUTION_LEASE'});
 x.c.client.rpc=async(method,scope,payload,id)=>{
  if(method==='session.editor.observer.get'){x.calls.push({method,payload});return {status:'OK',result:{current_scope:scope,current_scope_is_execution_authorization:false}};}
  if(!method.startsWith('session.editor.capture.'))return base(method,scope,payload,id);
  x.calls.push({method,payload:copy(payload)});
  if(method==='session.editor.capture.request'){
   const raw={...payload,session_ref:scope.session_ref,generation:scope.generation,editor_client_id:'paired_client'},slot=Object.values(JSON.parse(fs.readFileSync(x.state.filename)).editor_observers)[0];assert.equal(slot.capture.status,'PENDING');assert.equal(slot.capture.request_sha256,digest(raw));
   const intent={kind:'EDITOR_CAPTURE_COHORT',id:payload.operation_id,capture_ref:digest(raw),scope:{...scope,workspace_ref:payload.workspace_ref},editor_client_id:raw.editor_client_id,request_sha256:digest(raw),required_windows:1,expires_at_ms:Date.now()+12000,created_at_ms:Date.now(),read_only:true,permission_granted:false,automatic_replay:false,workspace_writes:0,model_calls:0,native_posts:0,atomic_editor_write_guarantee:false,observation_time_scope:'IMMUTABLE_CAPTURE_INTENT_NOT_CURRENT_CLEAN_STATE'};originals.set(intent.id,intent);if(lose){lose=false;throw Error('UNIT lost group ACK');}return {status:'OK',result:intent};
  }
  const intent=originals.get(payload.operation_id);assert.ok(intent);
  if(method==='session.editor.capture.operation')return {status:'OK',result:intent};
  if(method==='session.editor.capture.wait'){
   const entry=Object.values(x.state.value.editor_observers)[0];receive({type:'editor-capture',instance_id:'UNIT_STREAM',window_id:streamWindow,observer_id:entry.id,capture_ref:intent.capture_ref,expires_at_ms:intent.expires_at_ms});
   for(let i=0;i<100&&Object.values(x.state.value.editor_observers)[0].last?.observer.capture_ref!==intent.capture_ref;i++)await new Promise(r=>setImmediate(r));
   assert.equal(Object.values(x.state.value.editor_observers)[0].last?.observer.capture_ref,intent.capture_ref);return {status:'OK',result:makeStatus(intent)};
  }return {status:'OK',result:{...makeStatus(intent),state:'WAITING',ready_windows:0,waiting_windows:1,dirty_windows:0}};
 };return {deliver:frame=>receive(frame),get window(){return streamWindow;}};
}
test('group preflight captures its own original slot without holding the producer queue or clearing the draft',async t=>{
 const x=scene(t);enableGroups(x);x.c.setDraft(draftKey(x.c.selected),0,'KEEP_GROUP_DRAFT');const result=await x.c.editorObservers.ensureSelected();assert.equal(result.state,'READY');assert.equal(result.permission_granted,false);
 const slot=Object.values(x.state.value.editor_observers)[0];assert.equal(slot.capture,null);assert.equal(slot.last_capture.last_status.state,'READY');assert.equal(slot.capture_refs.length,1);assert.equal(x.c.snapshot().draft.text,'KEEP_GROUP_DRAFT');assert.equal(x.c.state.value.requests.length,0);
});
test('lost group creation ACK recovers the original cohort instead of creating another request',async t=>{
 const x=scene(t);enableGroups(x,{lose:true});await assert.rejects(x.c.editorObservers.ensureSelected(),{code:'ORIGINAL_REQUEST_UNRESOLVED'});assert.equal(Object.values(x.state.value.editor_observers)[0].capture.status,'UNKNOWN');
 const ready=await x.c.editorObservers.ensureSelected();assert.equal(ready.state,'READY');assert.equal(x.calls.filter(v=>v.method==='session.editor.capture.request').length,1);assert.equal(x.calls.filter(v=>v.method==='session.editor.capture.operation').length,1);
});
test('duplicate group notice is metadata-only and cannot create another publication for the same captured purpose',async t=>{
 const x=scene(t),stream=enableGroups(x);await x.c.editorObservers.ensureSelected();const slot=Object.values(x.state.value.editor_observers)[0],count=x.calls.filter(v=>v.method==='session.editor.observer.command').length;
 stream.deliver({window_id:stream.window,observer_id:slot.id,capture_ref:slot.last_capture.receipt.capture_ref,expires_at_ms:Date.now()+1000});await x.c.editorObservers.chain;assert.equal(x.calls.filter(v=>v.method==='session.editor.observer.command').length,count);
});
test('remote dirty status blocks the original send while retaining the exact local message draft',async t=>{
 const x=scene(t);enableGroups(x,{remoteDirty:true});const key=draftKey(x.c.selected);x.c.setDraft(key,0,'KEEP_REMOTE_CONFLICT_DRAFT');await assert.rejects(x.c.send(key,1,'build'),{code:'EDITOR_CAPTURE_DIRTY'});assert.equal(x.c.state.value.drafts[key].text,'KEEP_REMOTE_CONFLICT_DRAFT');assert.equal(x.calls.filter(v=>v.method==='session.message').length,0);
});

// AUTHORED_NOT_RUN: actual PrivateState, observer/recovery controller and Native
// readonly capture; the following explicit UNIT boundary replaces paired API.
function enableRecovery(x){
 const recoveryMethods=require('../editor-observer-recovery.cjs').METHODS,old=copy(Object.values(x.state.value.editor_observers)[0]);
 const client=x.c.client,legacy=client.rpc,actor='new_paired_client',calls=[],records=new Map();let phase='ACTIVE',revision=old.last.observer.revision,lose=false,reject=false,invalid=false;
 client.ticket={client_id:actor};client.methods=[...METHODS,...recoveryMethods];client.supportedMethods=client.methods;
 client.rpc=async(method,scope,payload)=>{if(!recoveryMethods.includes(method))return legacy(method,scope,payload);calls.push({method,payload:copy(payload)});
  if(method===recoveryMethods[0]){const stat=fs.statSync(x.root,{bigint:true}),identity={pid:process.pid,parent_pid:process.ppid,birth:'1700000000.0',parent_birth:'1699999900.0'},basis={scope:{...scope,workspace_ref:old.workspace_ref},observer_id:old.id,revision,phase,runtime_id:'unit_recovery_runtime',editor_client_id:actor,editor_window_id:x.state.value.slot,process_identity:identity,original_process_identity:identity,original_editor_client_id:old.actor,original_editor_window_id:old.window,original_process_state:'SAME_PROCESS',root_identity:{device:stat.dev.toString(),inode:stat.ino.toString()},root_path_sha256:digest(x.root)};
   return {status:'OK',result:{kind:'EDITOR_OBSERVER_RECOVERY_PREPARATION',...basis,preparation_sha256:digest(basis),root_uri:pathToFileURL(x.root).href,read_only:true,workspace_writes:0,model_calls:0,native_posts:0,permission_granted:false,automatic_replay:false,current_scope_is_execution_authorization:false}};}
  if(method===recoveryMethods[2])return {status:'OK',result:copy(records.get(payload.operation_id))};
  assert.equal(method,recoveryMethods[1]);const persisted=JSON.parse(fs.readFileSync(x.state.filename)).editor_observers[old.id];assert.equal(persisted.recovery.status,'PENDING');assert.equal(persisted.actor,old.actor);assert.doesNotMatch(JSON.stringify(payload),/UNSAVED_SECRET|KEEP_ROOT_DRAFT/);
  if(reject)return {status:'ERROR',error:{code:'API_REQUEST_REJECTED'}};
  const request={...payload,session_ref:scope.session_ref,generation:scope.generation,editor_client_id:actor};phase='RETIRED';revision++;
  const result={kind:'EDITOR_OBSERVER_RECOVERY',id:request.operation_id,scope:{...scope,workspace_ref:old.workspace_ref},request_sha256:digest(request),observer_id:old.id,editor_client_id:actor,editor_window_id:x.state.value.slot,original_editor_client_id:old.actor,original_editor_window_id:old.window,original_process_state:'SAME_PROCESS',phase,revision,status:'RETURNED',workspace_writes:0,model_calls:0,native_posts:0,permission_granted:false,automatic_replay:false,atomic_editor_write_guarantee:false,original_evidence_retained:true,observation_time_scope:'AT_ORIGINAL_COMMAND_ONLY'};records.set(request.operation_id,copy(result));if(lose){lose=false;throw Error('UNIT lost original recovery ACK');}if(invalid)result.request_sha256='f'.repeat(64);return {status:'OK',result};
 };
 return {old,calls,records,lose(){lose=true;},reject(value=true){reject=value;},invalid(){invalid=true;},metadataRetired(){phase='RETIRED';revision++;}};
}
test('new paired actor explicitly ends the same-process metadata guard while preserving original evidence and draft',async t=>{
 const x=scene(t);await x.c.editorObservers.ensureSelected();x.c.setDraft(draftKey(x.c.selected),0,'KEEP_ROOT_DRAFT');const r=enableRecovery(x),entry=x.entry();assert.equal(entry.actorReady,true);assert.equal(entry.recoveryRequired,true);
 await x.c.editorObservers.act(entry.token,'retire');const v=x.state.value.editor_observers[r.old.id];assert.equal(v.stage,'RETIRED');assert.equal(v.actor,r.old.actor);assert.deepEqual(v.last,r.old.last);assert.equal(v.recovery.receipt.original_evidence_retained,true);assert.equal(x.c.snapshot().draft.text,'KEEP_ROOT_DRAFT');assert.equal(fs.readFileSync(x.filename,'utf8'),'SAVED_ORIGINAL');assert.equal(x.c.state.value.requests.length,0);
});
test('recovery refuses current unsaved documents and never publishes a retirement command',async t=>{
 const x=scene(t);await x.c.editorObservers.ensureSelected();const r=enableRecovery(x);x.doc.isDirty=true;x.doc.text='UNSAVED_SECRET';x.doc.version++;
 await assert.rejects(x.c.editorObservers.act(x.entry().token,'retire'),{code:'DIRTY_EDITOR'});assert.equal(r.calls.filter(c=>c.method.endsWith('.command')).length,0);assert.equal(x.state.value.editor_observers[r.old.id].stage,'OBSERVED');assert.equal(x.doc.text,'UNSAVED_SECRET');
});
test('lost recovery ACK retains its original operation and a repeated action only inspects it',async t=>{
 const x=scene(t);await x.c.editorObservers.ensureSelected();const r=enableRecovery(x);r.lose();await assert.rejects(x.c.editorObservers.act(x.entry().token,'retire'),{code:'ORIGINAL_REQUEST_UNRESOLVED'});
 const original=copy(x.state.value.editor_observers[r.old.id].recovery);assert.equal(original.status,'UNKNOWN');await x.c.editorObservers.act(x.entry().token,'inspect');assert.equal(r.calls.filter(c=>c.method.endsWith('.command')).length,1);assert.equal(r.calls.filter(c=>c.method.endsWith('.operation')).length,1);assert.equal(x.state.value.editor_observers[r.old.id].recovery.request_id,original.request_id);
});
test('metadata-only retired proof preserves foreign UNKNOWN without claiming its original request succeeded',async t=>{
 const x=scene(t);await x.c.editorObservers.ensureSelected();const r=enableRecovery(x);r.lose();await assert.rejects(x.c.editorObservers.act(x.entry().token,'retire'));
 x.c.client.ticket={client_id:'third_pair'};
 // A third pair has no right to read the previous actor receipt. Inject its
 // separately validated read preparation at the explicit UNIT transport edge.
 const prior=x.c.client.rpc;x.c.client.rpc=async(method,scope,payload)=>{const envelope=await prior(method,scope,payload);if(method.endsWith('.prepare')){envelope.result.editor_client_id='third_pair';const {kind,preparation_sha256,root_uri,read_only,workspace_writes,model_calls,native_posts,permission_granted,automatic_replay,current_scope_is_execution_authorization,...basis}=envelope.result;envelope.result.preparation_sha256=digest(basis);}return envelope;};
 await x.c.editorObservers.act(x.entry().token,'inspect');const v=x.state.value.editor_observers[r.old.id];assert.equal(v.stage,'RETIRED');assert.equal(v.recovery.status,'UNKNOWN');assert.equal(v.recovery.receipt,null);assert.equal(v.recovery_state.basis,'CURRENT_METADATA_ONLY_ORIGINAL_UNKNOWN_RETAINED');assert.equal(r.calls.filter(c=>c.method.endsWith('.command')).length,1);
});
test('known rejection and malformed ACK remain distinct and do not cause an automatic recovery replay',async t=>{
 const x=scene(t);await x.c.editorObservers.ensureSelected();const r=enableRecovery(x);r.reject();await assert.rejects(x.c.editorObservers.act(x.entry().token,'retire'),{code:'EDITOR_OBSERVATION_REQUIRED'});assert.equal(x.state.value.editor_observers[r.old.id].recovery.status,'REJECTED');
 r.reject(false);r.invalid();await assert.rejects(x.c.editorObservers.act(x.entry().token,'retire'),{code:'EDITOR_OBSERVATION_INVALID'});assert.equal(x.state.value.editor_observers[r.old.id].recovery.status,'UNKNOWN');assert.equal(x.state.value.editor_observers[r.old.id].recovery_history.length,1);assert.equal(r.calls.filter(c=>c.method.endsWith('.command')).length,2);
});
test('explicit private recovery copies the original uncertain recovery intent without editing its source file',async t=>{
 const x=scene(t);await x.c.editorObservers.ensureSelected();const r=enableRecovery(x);r.lose();await assert.rejects(x.c.editorObservers.act(x.entry().token,'retire'));
 const before=fs.readFileSync(x.state.filename),next=new PrivateState(path.dirname(x.state.filename));next.recover(path.basename(x.state.filename));const v=next.value.editor_observers[r.old.id];assert.equal(v.recovery.status,'UNKNOWN');assert.equal(v.actor,r.old.actor);assert.deepEqual(v.recovery.request,x.state.value.editor_observers[r.old.id].recovery.request);assert.deepEqual(fs.readFileSync(x.state.filename),before);
});
