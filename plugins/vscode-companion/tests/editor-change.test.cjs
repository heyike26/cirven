'use strict';
// AUTHORED_NOT_RUN. Actual private slots, validators, guarded filesystem reads
// and controller. VSCode edit/transport are explicit UNIT boundaries.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {pathToFileURL,fileURLToPath}=require('node:url');
const {PrivateState,CompanionController,draftKey,scopeOf}=require('../controller.cjs');
const diff=require('../session-diff.cjs'),{canonical,hash}=require('../client.cjs');
const {NativeEditor}=require('../native-editor.cjs'),editor=require('../editor-change.cjs');
const {fixture}=require('./diff-fixture.cjs');
const copy=v=>JSON.parse(JSON.stringify(v)),digest=v=>hash(canonical(v));
function scene(t){const folder=fs.mkdtempSync(path.join(process.env.EEAS_TEST_TMPDIR||process.env.TMPDIR||os.tmpdir(),'cirven-native-edit-'));assert.ok(!folder.startsWith('/mnt/d/'));t.after(()=>fs.rmSync(folder,{recursive:true,force:true}));
 const workspace=path.join(folder,'workspace');fs.mkdirSync(workspace);const filename=path.join(workspace,'a.ts'),f=fixture({full:true});fs.writeFileSync(filename,f.file.after);f.entry.path=f.file.path='a.ts';
 const state=new PrivateState(path.join(folder,'private')),c=new CompanionController({state});t.after(()=>c.dispose());c.selected=f.session;c.snapshot();c.diffs.directory=f.directory;
 const selection=diff.reviewSelection(f.scope,f.directory,f.entry),token='4'.repeat(64);c.reviewContext={scope:scopeOf(c.selected),ownerToken:diff.ownerToken(c.selected),child:false,childToken:null,directory:f.directory,entryToken:diff.entryToken(f.scope,f.directory,f.entry),selection,token,value:{diff:f.file,preparation_sha256:'5'.repeat(64),matches_after:true,matches_before:false,review_revision:0}};
 const uri=value=>{const u=new URL(value);return {scheme:u.protocol.slice(0,-1),authority:u.host,query:u.search.slice(1),fragment:u.hash.slice(1),fsPath:fileURLToPath(u),toString:()=>value};};
 const doc={uri:uri(pathToFileURL(filename).href),version:10,eol:2,isDirty:false,isClosed:false,text:f.file.after.slice(1),getText(){return this.text;},positionAt(offset){return {offset};},save:()=>assert.fail('The helper must not force save')};
 const edits=[];let applyCount=0,applyImpl;
 const vscode={Uri:{parse:uri},EndOfLine:{LF:1,CRLF:2},Range:class{constructor(start,end){this.start=start;this.end=end;}},TextEdit:{replace:(range,newText)=>({range,newText}),setEndOfLine:newEol=>({newEol})},WorkspaceEdit:class{set(uri,value){this.uri=uri;this.edits=value;}},workspace:{textDocuments:[doc],openTextDocument:async()=>doc,applyEdit:async edit=>{applyCount++;const slot=Object.values(JSON.parse(fs.readFileSync(state.filename)).editor_changes)[0];assert.equal(slot.stage,'NATIVE_INTENT');assert.equal(slot.native_intent,true);if(applyImpl)return applyImpl(edit);for(const e of edit.edits){if('newText'in e)doc.text=e.newText;if(e.newEol)doc.eol=e.newEol;}doc.version++;doc.isDirty=true;edits.push(edit);return true;}},window:{showTextDocument:async()=>({document:doc})}};
 const calls=[],actor='paired_client';let lease,original,controlRecords=new Map(),armImpl;
 function project(){const out=copy(lease);out.first_reservation_reply=false;delete out.editor_uri;return out;}
 function register(payload,scope){original={...payload,session_ref:scope.session_ref,generation:scope.generation,editor_client_id:actor};const stat=fs.statSync(workspace,{bigint:true}),source={kind:'local_file',project_id:scope.project_id,workspace_id:f.scope.workspace_ref.id,binding_revision:f.scope.workspace_ref.revision,binding_sha256:f.scope.workspace_ref.binding_sha256,path:'a.ts',revision:1,sha256:f.file.after_sha256};
 const grant={scope:f.scope,selection,direction:original.direction,editor_client_id:actor,editor_window_id:original.editor_window_id,editor_nonce:original.editor_nonce,path:'a.ts',source_ref:source,file_location:{kind:'WORKSPACE_FILE',path:'a.ts',name:'a.ts',file_key:'6'.repeat(64),parent_ref:{device:stat.dev.toString(),inode:stat.ino.toString()}},saved_basis:{exists:true,sha256:source.sha256,byte_length:Buffer.byteLength(f.file.after)},target_exists:true,target_sha256:f.file.before_sha256,source_editor_sha256:hash(f.file.after.slice(1)),target_editor_sha256:hash(f.file.before.slice(1)),source_has_bom:true,target_has_bom:true,mode:'TEXT_REPLACE'};
 lease={kind:'EDITOR_CHANGE',id:original.operation_id,session_ref:scope.session_ref,generation:scope.generation,request_sha256:digest(original),phase:'READY',revision:2,grant,lease_ref:{id:digest({project_id:scope.project_id,session_id:scope.session_ref.id,operation_id:original.operation_id}),revision:1,sha256:digest(grant)},editor_client_id:actor,editor_window_id:original.editor_window_id,automatic_replay:false,workspace_writes:0,model_calls:0,native_posts:0,first_reservation_reply:true,editor_uri:pathToFileURL(filename).href};return copy(lease);}
 function respond(payload,scope){const request={...payload,session_ref:scope.session_ref,generation:scope.generation,editor_client_id:actor};lease.phase={ARM:'ARMED',BUFFER_CHANGED:'BUFFER_CHANGED',RESOLVE_SAVED:'SAVED',RESOLVE_CANCELLED:'CANCELLED',RESOLVE_MANUAL_SAVED:'MANUAL_SAVED'}[payload.action];lease.revision++;
 const value={id:request.operation_id,kind:'EDITOR_CHANGE_CONTROL',session_ref:scope.session_ref,generation:scope.generation,request_sha256:digest(request),status:'RETURNED',automatic_replay:false,first_control_reply:true,editor_change:project()};controlRecords.set(request.operation_id,{...copy(value),first_control_reply:false});return value;}
 c.client={ticket:{client_id:actor},methods:['session.editor.reserve','session.editor.get','session.editor.control','session.editor.control.operation'],close(){},rpc:async(method,scope,payload)=>{calls.push({method,scope,payload:copy(payload)});if(method==='session.editor.reserve')return {status:'OK',result:register(payload,scope)};if(method==='session.editor.get')return {status:'OK',result:project()};if(method==='session.editor.control.operation')return {status:'OK',result:controlRecords.get(payload.operation_id)};const result=respond(payload,scope);if(payload.action==='ARM'&&armImpl)return armImpl(result);return {status:'OK',result};}};
 return {c,f,folder,workspace,filename,doc,vscode,host:new NativeEditor(vscode),token,calls,edits,get applyCount(){return applyCount;},setApply(fn){applyImpl=fn;},setArm(fn){armImpl=fn;},readLease:()=>project()};}
test('one versioned native buffer edit follows persisted intent, retains both saved file and root draft, then resolves an explicit save',async t=>{
 const x=scene(t);x.c.setDraft(draftKey(x.c.selected),0,'KEEP_ROOT_INPUT');await x.c.editor.apply(x.token,'RESTORE_BEFORE',x.host);
 assert.equal(x.applyCount,1);assert.equal(x.doc.text,x.f.file.before.slice(1));assert.equal(x.doc.isDirty,true);assert.equal(fs.readFileSync(x.filename,'utf8'),x.f.file.after);assert.equal(x.c.snapshot().draft.text,'KEEP_ROOT_INPUT');assert.deepEqual(x.calls.map(v=>v.method),['session.editor.reserve','session.editor.control','session.editor.control']);
 const slot=Object.values(x.c.state.value.editor_changes)[0];assert.equal(slot.stage,'BUFFER_CHANGED');assert.equal(slot.native_returned,true);assert.ok(!JSON.stringify(x.c.state.value.editor_changes).includes('const old = 1'));
 // Explicit UNIT save stands in for the user's native Save. No helper save call.
 fs.writeFileSync(x.filename,'\ufeff'+x.doc.text);x.doc.isDirty=false;
 await x.c.editor.resolve(x.c.editor.snapshot().entries[0].token,'RESOLVE_SAVED',x.host);assert.equal(Object.values(x.c.state.value.editor_changes)[0].stage,'CLOSED');assert.equal(x.applyCount,1);assert.equal(x.c.state.value.requests.at(-1).status,'OK');
});
test('typing while ARM is in flight prevents native apply and keeps manual content',async t=>{
 const x=scene(t);let done;x.setArm(result=>new Promise(resolve=>{done=()=>resolve({status:'OK',result});}));const pending=x.c.editor.apply(x.token,'RESTORE_BEFORE',x.host);
 while(!done)await new Promise(resolve=>setImmediate(resolve));x.doc.text='MY_UNSAVED_EDIT';x.doc.version++;x.doc.isDirty=true;done();
 await assert.rejects(pending,{code:'SOURCE_CHANGED'});assert.equal(x.applyCount,0);assert.equal(x.doc.text,'MY_UNSAVED_EDIT');assert.equal(fs.readFileSync(x.filename,'utf8'),x.f.file.after);assert.equal(Object.values(x.c.state.value.editor_changes)[0].native_intent,false);
});
test('lost ARM reply recovers only original metadata and never turns a recovered reply into native permission',async t=>{
 const x=scene(t);x.setArm(()=>{throw Object.assign(Error('UNIT lost ARM acknowledgement'),{code:'ECONNRESET'});});await x.c.editor.apply(x.token,'RESTORE_BEFORE',x.host);
 assert.equal(x.applyCount,0);assert.equal(x.c.state.value.requests.at(-1).status,'UNKNOWN');await x.c.editor.inspect(x.c.editor.snapshot().entries[0].token);
 assert.equal(x.applyCount,0);assert.equal(x.c.state.value.requests.at(-1).status,'OK');assert.equal(x.calls.filter(v=>v.method==='session.editor.control').length,1);assert.ok(x.calls.some(v=>v.method==='session.editor.control.operation'));
});
test('unknown native completion keeps the intent and prevents cancellation from a clean preimage',async t=>{
 const x=scene(t);x.setApply(()=>{throw Error('UNIT native completion unknown');});await assert.rejects(x.c.editor.apply(x.token,'RESTORE_BEFORE',x.host));
 const slot=Object.values(x.c.state.value.editor_changes)[0];assert.equal(slot.native_intent,true);assert.equal(slot.native_returned,false);assert.equal(slot.stage,'UNKNOWN');
 await assert.rejects(x.c.editor.resolve(x.c.editor.snapshot().entries[0].token,'RESOLVE_CANCELLED',x.host),{code:'EDITOR_NATIVE_OUTCOME_UNKNOWN'});assert.equal(x.calls.filter(v=>v.method==='session.editor.control').length,1);assert.equal(fs.readFileSync(x.filename,'utf8'),x.f.file.after);
});
test('private recovery retains original actor and intent but performs no native action or live-window takeover',async t=>{
 const x=scene(t);await x.c.editor.apply(x.token,'RESTORE_BEFORE',x.host);const old=x.c.state.filename,oldBytes=fs.readFileSync(old),state=new PrivateState(path.dirname(old));state.recover(path.basename(old));
 const recovered=new CompanionController({state});t.after(()=>recovered.dispose());recovered.client=x.c.client;
 const entry=recovered.editor.snapshot().entries[0];assert.equal(entry.recovered,true);assert.equal(entry.ownerEnded,false);assert.equal(x.applyCount,1);assert.deepEqual(fs.readFileSync(old),oldBytes);
 await assert.rejects(recovered.editor.resolve(entry.token,'RESOLVE_CANCELLED',x.host),{code:'EDITOR_ORIGINAL_PROCESS_UNCONFIRMED'});assert.equal(x.applyCount,1);
});
test('replacement parent with identical file bytes is rejected before native intent',async t=>{
 const x=scene(t);x.vscode.workspace.openTextDocument=async()=>{fs.renameSync(x.workspace,x.workspace+'-retained');fs.mkdirSync(x.workspace);fs.writeFileSync(x.filename,x.f.file.after);return x.doc;};
 await assert.rejects(x.c.editor.apply(x.token,'RESTORE_BEFORE',x.host),{code:'EDITOR_PARENT_CHANGED'});assert.equal(x.applyCount,0);assert.equal(x.calls.filter(v=>v.method==='session.editor.control').length,0);assert.equal(fs.readFileSync(x.filename,'utf8'),x.f.file.after);
});
