'use strict';
// AUTHORED_NOT_RUN. Real controller/validators/virtual documents, UNIT transport.
// No real SDK, editor buffer write, Windows or VSCode host claim.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {PrivateState,CompanionController,draftKey,scopeOf}=require('../controller.cjs');
const {canonical,hash}=require('../client.cjs'),child=require('../session-child.cjs');
const {validateDirectory,validateFile,SnapshotDocuments,entryToken}=require('../session-diff.cjs');
const {fixture}=require('./diff-fixture.cjs');
function scene(t){const f=fixture({full:true}),root=fs.mkdtempSync(path.join(process.env.EEAS_TEST_TMPDIR||os.tmpdir(),'cirven-child-diff-'));
 t.after(()=>fs.rmSync(root,{recursive:true,force:true}));const c=new CompanionController({state:new PrivateState(root)});t.after(()=>c.dispose());
 const reference={id:'ses_child',root_native_session_id:'ses_root',sha256:'b'.repeat(64)},scope=scopeOf(f.session),token=child.token(scope,reference);
 c.selected={...f.session,children:[{id:reference.id,parentID:reference.root_native_session_id,child_ref:reference}]};
 // Establish root scope before the read; no network or private-draft mutation.
 c.snapshot();c.childDetail={token,value:{messages:[]}};
 const snapshot={...f.directory.snapshot_ref,id:hash(canonical({scope:f.scope,child_ref:reference}))},source={source_native_session_id:reference.id,child_lineage_ref:{id:'3'.repeat(64),revision:1,sha256:'4'.repeat(64)}};
 const entry={...f.entry,...source},directory={...f.directory,child_ref:reference,snapshot_ref:snapshot,files:[entry]},file={...f.file,...source,child_ref:reference,snapshot_ref:snapshot};
 return {c,f,reference,scope,token,entry,directory,file};}
test('child diff reads exact refs, preserves both private drafts and uses bounded immutable virtual documents',async t=>{
 const x=scene(t),{c}=x,calls=[];c.client={methods:['session.child.diff','session.child.diff.file'],rpc:async(method,scope,payload)=>{calls.push({method,scope,payload});return {status:'OK',result:method==='session.child.diff'?x.directory:x.file};}};
 c.setDraft(draftKey(c.selected),0,'ROOT_UNSAVED');c.setChildDraft(x.token,0,'CHILD_UNSAVED');
 await c.loadChildDiffs(c.snapshot().delegation.diffs.scopeToken);const d=c.snapshot().delegation.diffs;
 const value=await c.readChildDiff(0,d.files[0].token);assert.deepEqual(value,x.file);assert.equal(calls.length,2);
 assert.deepEqual(calls[1].payload,{workspace_ref:x.f.scope.workspace_ref,child_ref:x.reference,snapshot_ref:x.directory.snapshot_ref,diff_ref:x.entry.diff_ref});
 assert.equal(c.snapshot().draft.text,'ROOT_UNSAVED');assert.equal(c.snapshot().delegation.draft.text,'CHILD_UNSAVED');assert.equal(c.state.value.requests.length,0);
 const docs=new SnapshotDocuments(),uris=docs.add(value),uri=v=>{const u=new URL(v);return {scheme:u.protocol.slice(0,-1),authority:u.host,path:decodeURIComponent(u.pathname),query:'',fragment:''};};
 assert.equal(docs.provide(uri(uris.before)),x.file.before);assert.equal(docs.provide(uri(uris.after)),x.file.after);assert.deepEqual([...docs.entries.values()][0].provenance.child_ref,x.reference);
});
test('child namespace, source native id and lineage must agree across directory and content',t=>{
 const x=scene(t);assert.deepEqual(validateDirectory(x.directory,x.f.scope,x.reference),x.directory);
 assert.throws(()=>validateDirectory(x.directory,x.f.scope),{code:'CHILD_CHANGED'});
 assert.throws(()=>validateDirectory({...x.directory,snapshot_ref:x.f.directory.snapshot_ref},x.f.scope,x.reference),{code:'CHILD_CHANGED'});
 for(const delta of [{source_native_session_id:'ses_sibling'},{child_lineage_ref:{...x.entry.child_lineage_ref,sha256:'5'.repeat(64)}},{child_ref:{...x.reference,id:'ses_sibling'}}])assert.throws(()=>validateFile({...x.file,...delta},x.f.scope,x.directory,x.entry));
 const sibling={...x.directory,child_ref:{...x.reference,id:'ses_sibling'}};
 assert.notEqual(entryToken(x.f.scope,x.directory,x.entry),entryToken(x.f.scope,sibling,x.entry));
 const docs=new SnapshotDocuments(),a=docs.add(x.file),b=docs.add({...x.file,child_ref:sibling.child_ref});assert.notEqual(a.before,b.before);
});
test('root and child share one in-flight diff read with no retry queue; stale child reply cannot open a new selection',async t=>{
 const x=scene(t),{c}=x;let finish;const calls=[];
 c.client={methods:['session.child.diff','session.child.diff.file'],rpc:(method)=>{calls.push(method);return new Promise(resolve=>{finish=resolve;});}};
 const first=c.loadChildDiffs(c.snapshot().delegation.diffs.scopeToken);await c.loadDiffs(c.snapshot().diffs.scopeToken);assert.deepEqual(calls,['session.child.diff']);
 c.childDetail=null;c.snapshot();finish({status:'OK',result:x.directory});await first;assert.equal(c.snapshot().delegation.diffs.loaded,false);assert.equal(calls.length,1);
 c.childDetail={token:x.token,value:{messages:[]}};c.client.rpc=async()=>({status:'OK',result:x.directory});await c.loadChildDiffs(c.snapshot().delegation.diffs.scopeToken);
 const token=c.snapshot().delegation.diffs.files[0].token;c.client.rpc=()=>new Promise(resolve=>{finish=resolve;});const opening=c.readChildDiff(0,token);
 c.selected={...c.selected,generation:3};c.snapshot();finish({status:'OK',result:x.file});assert.equal(await opening,null);assert.equal(c.snapshot().delegation.diffs.loaded,false);
});
