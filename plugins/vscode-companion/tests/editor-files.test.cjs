'use strict';
// AUTHORED_NOT_RUN. Real private slots/files/controller/readonly observations.
// OS effects and Main RPC are explicit UNIT boundaries, not Windows qualification.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {pathToFileURL,fileURLToPath}=require('node:url');
const {PrivateState,CompanionController,draftKey,scopeOf}=require('../controller.cjs');
const {NativeEditor}=require('../native-editor.cjs'),{canonical,hash}=require('../client.cjs');
const diff=require('../session-diff.cjs'),files=require('../editor-files.cjs'),{fixture}=require('./diff-fixture.cjs');
const copy=v=>JSON.parse(JSON.stringify(v)),digest=v=>hash(canonical(v));
function scene(t,{status='deleted'}={}){
 const folder=fs.mkdtempSync(path.join(process.env.EEAS_TEST_TMPDIR||process.env.TMPDIR||os.tmpdir(),'cirven-file-handoff-'));assert.ok(!folder.startsWith('/mnt/d/'));t.after(()=>fs.rmSync(folder,{recursive:true,force:true}));
 const workspace=path.join(folder,'workspace');fs.mkdirSync(workspace);const filename=path.join(workspace,'a.ts'),f=fixture({full:true,status});f.entry.path=f.file.path='a.ts';if(status==='added')fs.writeFileSync(filename,f.file.after);
 const state=new PrivateState(path.join(folder,'private')),c=new CompanionController({state});t.after(()=>c.dispose());c.selected=f.session;c.snapshot();c.diffs.directory=f.directory;
 const selection=diff.reviewSelection(f.scope,f.directory,f.entry),token='4'.repeat(64);c.reviewContext={scope:scopeOf(c.selected),ownerToken:diff.ownerToken(c.selected),child:false,childToken:null,directory:f.directory,entryToken:diff.entryToken(f.scope,f.directory,f.entry),selection,token,value:{diff:f.file,preparation_sha256:'5'.repeat(64),matches_after:true,matches_before:false,review_revision:0}};
 const uri=value=>{const u=new URL(value);return {scheme:u.protocol.slice(0,-1),authority:u.host,query:u.search.slice(1),fragment:u.hash.slice(1),fsPath:fileURLToPath(u),toString:()=>value};};
 const vscode={Uri:{parse:uri},workspace:{textDocuments:[],openTextDocument:async()=>assert.fail('file actions must not open/reload documents'),applyEdit:()=>assert.fail('file action is not text WorkspaceEdit')},window:{showTextDocument:()=>assert.fail('no forced open')}};
 const calls=[],actor='paired_client',records=new Map();let lease,original,loseReply=false,unavailable=false,prepareHook=null;
 const location=()=>{const st=fs.statSync(workspace,{bigint:true});return {kind:'WORKSPACE_FILE_SLOT',project_id:f.scope.project_id,path:'a.ts',name:'a.ts',parent_ref:{project_id:f.scope.project_id,workspace_id:f.scope.workspace_ref.id,binding_revision:f.scope.workspace_ref.revision,binding_sha256:f.scope.workspace_ref.binding_sha256,device:st.dev.toString(),inode:st.ino.toString()}};};
 const saved=()=>{const raw=fs.existsSync(filename)?fs.readFileSync(filename):null;return {exists:raw!==null,sha256:raw===null?null:hash(raw),byte_length:raw?.length||0};};
 const project=()=>{const v=copy(lease);v.first_reservation_reply=false;delete v.editor_uri;return v;};
 const snapshot=()=>unavailable?{availability:'UNAVAILABLE',saved:null,location:null,editor_text_sha256:null,workspace_binding_sha256:f.scope.workspace_ref.binding_sha256,metadata_sha256:null,file_identity:null,metadata_recovery_version:1,unavailable_reason:'UNIT_ORIGINAL_ROOT_UNAVAILABLE'}:{availability:'OBSERVED',saved:saved(),location:location(),editor_text_sha256:fs.existsSync(filename)?hash(fs.readFileSync(filename,'utf8').replace(/^\ufeff/,'')):null,workspace_binding_sha256:f.scope.workspace_ref.binding_sha256,metadata_sha256:null,file_identity:null,metadata_recovery_version:1};
 c.client={ticket:{client_id:actor},methods:['session.editor.reserve','session.editor.get','session.editor.control','session.editor.control.operation',...files.METHODS],close(){},rpc:async(method,scope,payload)=>{
  calls.push({method,scope:copy(scope),payload:copy(payload)});
  if(method==='session.editor.reserve'){
   original={...payload,session_ref:scope.session_ref,generation:scope.generation,editor_client_id:actor};const b=saved(),w=f.scope.workspace_ref;
   const grant={scope:f.scope,selection,direction:original.direction,editor_client_id:actor,editor_window_id:original.editor_window_id,editor_nonce:original.editor_nonce,path:'a.ts',source_ref:b.exists?{kind:'local_file',project_id:scope.project_id,workspace_id:w.id,binding_revision:w.revision,binding_sha256:w.binding_sha256,path:'a.ts',revision:1,sha256:b.sha256}:null,file_location:location(),saved_basis:b,target_exists:status==='deleted',target_sha256:status==='deleted'?f.file.before_sha256:null,source_editor_sha256:hash(f.file.after.replace(/^\ufeff/,'')),target_editor_sha256:hash(f.file.before.replace(/^\ufeff/,'')),source_has_bom:f.file.after.startsWith('\ufeff'),target_has_bom:f.file.before.startsWith('\ufeff'),mode:status==='deleted'?'FILE_CREATE':'FILE_DELETE'};
   lease={kind:'EDITOR_CHANGE',id:original.operation_id,session_ref:scope.session_ref,generation:scope.generation,request_sha256:digest(original),phase:'READY',revision:2,grant,lease_ref:{id:digest({project_id:scope.project_id,session_id:scope.session_ref.id,operation_id:original.operation_id}),revision:1,sha256:digest(grant)},editor_client_id:actor,editor_window_id:original.editor_window_id,automatic_replay:false,workspace_writes:0,model_calls:0,native_posts:0,first_reservation_reply:true,editor_uri:pathToFileURL(filename).href};return {status:'OK',result:copy(lease)};
  }
  if(method==='session.editor.get')return {status:'OK',result:project()};
  if(method==='session.editor.file.operation')return {status:'OK',result:copy(records.get(payload.operation_id))};
  if(method==='session.editor.file.prepare'){
   const basis={scope:f.scope,lease_ref:lease.lease_ref,revision:lease.revision,target_operation_id:original.operation_id,action:payload.action,snapshot:snapshot()},result={kind:'EDITOR_FILE_PREPARATION',...basis,preparation_sha256:digest(basis),editor_client_id:actor,editor_window_id:original.editor_window_id,editor_uri:pathToFileURL(filename).href,read_only:true,automatic_replay:false,workspace_writes:0,model_calls:0,native_posts:0};if(prepareHook)prepareHook();return {status:'OK',result};
  }
  assert.equal(method,'session.editor.file.command');const request={...payload,session_ref:scope.session_ref,generation:scope.generation,editor_client_id:actor};
  const persisted=JSON.parse(fs.readFileSync(state.filename)),slot=Object.values(persisted.editor_changes)[0];assert.equal(slot.stage,'FILE_INTENT');assert.ok(persisted.requests.some(r=>r.file_request?.operation_id===request.operation_id));assert.equal(files.requestValid(request,slot),true);
  const mutating=['COMMIT','UNDO'].includes(payload.action);let resultStatus;
  if(mutating){const create=payload.action==='COMMIT'?lease.grant.target_exists:lease.grant.saved_basis.exists;if(create)fs.writeFileSync(filename,payload.action==='COMMIT'?f.file.before:f.file.after,{flag:'wx'});else fs.unlinkSync(filename);lease.phase='BUFFER_CHANGED';lease.revision+=2;resultStatus=payload.action==='COMMIT'?'APPLIED':'UNDONE';}
  else{lease.phase=payload.action==='MANUAL_RESOLVE'?'MANUAL_SAVED':records.size&&[...records.values()].at(-1).file_result.status==='UNDONE'?'CANCELLED':'SAVED';lease.revision++;resultStatus=payload.action==='MANUAL_RESOLVE'?'MANUALLY_RESOLVED':'CONFIRMED';}
  const value={kind:'EDITOR_FILE_ACTION',id:payload.operation_id,session_ref:scope.session_ref,generation:scope.generation,request_sha256:digest(request),status:'RETURNED',automatic_replay:false,automatic_file_replay:false,file_effect:mutating?resultStatus:'NO_FILE_CHANGE',editor_change:project(),file_result:{status:resultStatus,workspace_writes:mutating?1:0,native_text_undo:false,metadata_recovery_recorded:mutating,observed_saved:unavailable?undefined:saved()}};records.set(payload.operation_id,copy(value));
  if(loseReply){loseReply=false;throw Error('UNIT lost original file ACK');}return {status:'OK',result:value};
 }};
 return {c,f,folder,workspace,filename,vscode,host:new NativeEditor(vscode),token,calls,setLose(){loseReply=true;},setUnavailable(){unavailable=true;},setPrepareHook(fn){prepareHook=fn;},entry:()=>c.editor.snapshot().entries[0],slot:()=>Object.values(c.state.value.editor_changes)[0]};
}
test('missing deleted file is restored once; explicit file Undo holds the original handoff until confirmation',async t=>{
 const x=scene(t);x.c.setDraft(draftKey(x.c.selected),0,'KEEP_PRIVATE_INPUT');await x.c.editor.apply(x.token,'RESTORE_BEFORE',x.host);
 assert.equal(fs.readFileSync(x.filename,'utf8'),x.f.file.before);assert.equal(x.slot().stage,'BUFFER_CHANGED');assert.equal(x.entry().fileExists,true);assert.equal(x.entry().fileStatus,'APPLIED');
 await x.c.editor.files.act(x.entry().token,'UNDO',x.host);assert.equal(fs.existsSync(x.filename),false);assert.equal(x.slot().stage,'BUFFER_CHANGED');
 await x.c.editor.files.act(x.entry().token,'CONFIRM',x.host);assert.equal(x.slot().stage,'CLOSED');assert.equal(x.c.snapshot().draft.text,'KEEP_PRIVATE_INPUT');
 assert.deepEqual(x.calls.filter(r=>r.method==='session.editor.file.command').map(r=>r.payload.action),['COMMIT','UNDO','CONFIRM']);assert.ok(!JSON.stringify(x.c.state.value.editor_changes).includes('const old = 1'));
});
test('deleting an added file never discards a dirty editor or executes the text edit API',async t=>{
 const x=scene(t,{status:'added'}),uri=x.host.uri(pathToFileURL(x.filename).href);x.vscode.workspace.textDocuments=[{uri,version:1,isDirty:true,isClosed:false,getText:()=> 'KEEP_MY_UNSAVED_CONTENT'}];
 await assert.rejects(x.c.editor.apply(x.token,'RESTORE_BEFORE',x.host),{code:'DIRTY_EDITOR'});assert.equal(fs.readFileSync(x.filename,'utf8'),x.f.file.after);assert.equal(x.calls.filter(r=>r.method==='session.editor.file.command').length,0);
});
test('lost file ACK queries only the original operation, with no creation replay or draft loss',async t=>{
 const x=scene(t);x.setLose();await x.c.editor.apply(x.token,'RESTORE_BEFORE',x.host);assert.equal(x.c.state.value.requests.at(-1).status,'UNKNOWN');assert.equal(fs.readFileSync(x.filename,'utf8'),x.f.file.before);
 await x.c.editor.inspect(x.entry().token);assert.equal(x.c.state.value.requests.at(-1).status,'OK');assert.equal(x.entry().fileStatus,'APPLIED');assert.equal(x.calls.filter(r=>r.method==='session.editor.file.command').length,1);assert.equal(x.calls.filter(r=>r.method==='session.editor.file.operation').length,1);
});
test('manual release preserves an unknown original result and tolerates an unavailable original namespace without touching a replacement',async t=>{
 const x=scene(t);x.setLose();await x.c.editor.apply(x.token,'RESTORE_BEFORE',x.host);const old=x.c.state.value.requests.at(-1).request_id;
 fs.renameSync(x.workspace,x.workspace+'-retained');fs.mkdirSync(x.workspace);fs.writeFileSync(x.filename,'REPLACEMENT_DO_NOT_TOUCH');x.setUnavailable();
 await x.c.editor.files.act(x.entry().token,'MANUAL_RESOLVE',x.host);assert.equal(x.slot().stage,'CLOSED');assert.equal(x.c.state.value.requests.find(r=>r.request_id===old).status,'UNKNOWN');assert.equal(fs.readFileSync(x.filename,'utf8'),'REPLACEMENT_DO_NOT_TOUCH');assert.equal(fs.readFileSync(path.join(x.workspace+'-retained','a.ts'),'utf8'),x.f.file.before);
});
test('an absent-file clean tab is preserved until the user closes it',async t=>{
 const x=scene(t),uri=x.host.uri(pathToFileURL(x.filename).href),doc={uri,version:1,isDirty:false,isClosed:false,getText:()=>''};x.vscode.workspace.textDocuments=[doc];
 await assert.rejects(x.c.editor.apply(x.token,'RESTORE_BEFORE',x.host),{code:'EDITOR_ABSENT_DOCUMENT_OPEN'});assert.equal(doc.isClosed,false);assert.equal(fs.existsSync(x.filename),false);
});
test('prepare cannot authorize a file operation after the editor or current conversation changed',async t=>{
 const x=scene(t);x.setPrepareHook(()=>{x.c.epoch++;});await assert.rejects(x.c.editor.apply(x.token,'RESTORE_BEFORE',x.host),{code:'EDITOR_CHANGE_CHANGED'});assert.equal(fs.existsSync(x.filename),false);assert.equal(x.calls.filter(r=>r.method==='session.editor.file.command').length,0);
});
test('private file intent recovery preserves old receipts and blocks takeover while the original window is alive',async t=>{
 const x=scene(t);x.setLose();await x.c.editor.apply(x.token,'RESTORE_BEFORE',x.host);const bytes=fs.readFileSync(x.c.state.filename),state=new PrivateState(path.dirname(x.c.state.filename));state.recover(path.basename(x.c.state.filename));const c=new CompanionController({state});t.after(()=>c.dispose());c.client=x.c.client;
 assert.deepEqual(fs.readFileSync(x.c.state.filename),bytes);assert.equal(c.state.value.requests.at(-1).status,'UNKNOWN');assert.equal(c.editor.snapshot().entries[0].ownerEnded,false);
 await assert.rejects(c.editor.files.act(c.editor.snapshot().entries[0].token,'MANUAL_RESOLVE',x.host),{code:'EDITOR_ORIGINAL_PROCESS_UNCONFIRMED'});assert.equal(x.calls.filter(r=>r.method==='session.editor.file.command').length,1);
});

test('a saved file changed after readonly preparation is retained without deletion',async t=>{
 const x=scene(t,{status:'added'});x.setPrepareHook(()=>fs.writeFileSync(x.filename,'NEW_MANUAL_SAVED_CONTENT'));
 await assert.rejects(x.c.editor.apply(x.token,'RESTORE_BEFORE',x.host),{code:'SOURCE_CHANGED'});assert.equal(fs.readFileSync(x.filename,'utf8'),'NEW_MANUAL_SAVED_CONTENT');assert.equal(x.calls.filter(r=>r.method==='session.editor.file.command').length,0);
});
test('file Undo observes a replacement parent before sending any inverse file command',async t=>{
 const x=scene(t);await x.c.editor.apply(x.token,'RESTORE_BEFORE',x.host);
 x.setPrepareHook(()=>{fs.renameSync(x.workspace,x.workspace+'-retained');fs.mkdirSync(x.workspace);fs.writeFileSync(x.filename,x.f.file.before);});
 await assert.rejects(x.c.editor.files.act(x.entry().token,'UNDO',x.host),{code:'EDITOR_PARENT_CHANGED'});assert.equal(x.calls.filter(r=>r.method==='session.editor.file.command').length,1);assert.equal(fs.readFileSync(x.filename,'utf8'),x.f.file.before);
});
