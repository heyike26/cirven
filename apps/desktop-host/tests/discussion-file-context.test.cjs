'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {QuickJournal,quickDigest}=require('../quick-journal.cjs');
const {QuickHost}=require('../quick-host.cjs');
const {NativeCompanion}=require('../native-companion.cjs');
const a={projectId:'project_a',workspaceId:'workspace_a',sessionId:'session_a',conversationId:'conversation_a'};
const link=(digit='a',project='project_a')=>({project_id:project,id:digit.repeat(64),revision:1,sha256:'f'.repeat(64)});
function fixture(t){const dataDir=fs.mkdtempSync(path.join(os.tmpdir(),'discussion-file-'));t.after(()=>fs.rmSync(dataDir,{recursive:true,force:true}));const journal=new QuickJournal({dataDir,hostGeneration:'host_a'});const host=new QuickHost({journal,getCompanion:()=>({}),request:async()=>assert.fail('selection has no model or API call')});t.after(()=>host.close());host.setScope(a);return {dataDir,journal,host};}
test('file selections are exact discussion/workspace/session records, and same-project switches or closing never borrow one',t=>{
 const f=fixture(t);f.host.setFileContext({file_link_ref:link(),filename:'相同名字.docx'});
 for(const next of [{...a,conversationId:'other'},{...a,sessionId:'other'},{...a,workspaceId:'other'},{projectId:'other',conversationId:'other'},{}]){f.host.setScope(next);assert.equal(f.host.scope.fileLinkRef,undefined);}
 f.host.setScope(a);assert.deepEqual(f.host.scope.fileLinkRef,link());
 const b={...a,conversationId:'conversation_b'};f.host.setScope(b);f.host.setFileContext({file_link_ref:link(),filename:'相同名字.docx'});
 const restored=new QuickHost({journal:new QuickJournal({dataDir:f.dataDir,hostGeneration:'restart'}),getCompanion:()=>({}),request:async()=>assert.fail('restart must not execute')});t.after(()=>restored.close());
 for(const scope of [a,b]){restored.setScope(scope);assert.deepEqual(restored.scope.fileLinkRef,link());}
 assert.equal(Object.keys(f.journal.state.scopedFileContexts).length,2);
});
test('legacy project-only cache survives as a choice but is never silently promoted into a discussion',t=>{
 const f=fixture(t),next=structuredClone(f.journal.state);next.fileContexts={project_a:{fileLinkRef:link(),filename:'历史稿.docx'}};f.journal.commit(next);
 const host=new QuickHost({journal:new QuickJournal({dataDir:f.dataDir,hostGeneration:'restart'}),getCompanion:()=>({}),request:async()=>assert.fail('no execution')});t.after(()=>host.close());host.setScope(a);
 assert.equal(host.scope.fileLinkRef,undefined);assert.equal(host.fileContextState().selected,null);assert.equal(host.fileContextState().legacy_candidates[0].filename,'历史稿.docx');
 assert.equal(Object.keys(host.journal.state.scopedFileContexts??{}).length,0);
});
test('clear checks exact current scope and exact old reference, preserving newer selection and frozen drafts',t=>{
 const f=fixture(t);f.host.setFileContext({file_link_ref:link(),filename:'A.docx'});const capture=f.host.emptyCapture();
 const draft=f.host.save({draftId:'old_draft',expectedRevision:0,capture,comment:'保留正文和批注',delivery:'DRAFT'});
 f.host.setFileContext({file_link_ref:link('b'),filename:'B.docx'});
 assert.throws(()=>f.host.clearFileContext({scope:a,expectedRef:link()}),/newer selection/);assert.deepEqual(f.host.scope.fileLinkRef,link('b'));
 f.host.setScope({...a,conversationId:'other'});assert.throws(()=>f.host.clearFileContext({scope:a,expectedRef:link('b')}),/讨论已变化/);
 f.host.setScope(a);assert.equal(f.host.clearFileContext({scope:a,expectedRef:link('b')}).selected,null);
 assert.equal(f.journal.find(draft.id).comment,'保留正文和批注');assert.deepEqual(f.journal.find(draft.id).scope.fileLinkRef,link());
});
test('a late open acknowledgement saves only its original discussion and never replaces the new active target',t=>{
 const f=fixture(t),b={...a,conversationId:'conversation_b'};f.host.setScope(b);f.host.setFileContext({file_link_ref:link('b'),filename:'B.docx'});
 f.host.setFileContext({file_link_ref:link(),filename:'A.docx'},{scope:a});assert.deepEqual(f.host.scope.fileLinkRef,link('b'));
 assert.deepEqual(f.journal.fileContext(a).fileLinkRef,link());assert.deepEqual(f.journal.fileContext(b).fileLinkRef,link('b'));
});
test('failed selection persistence never claims saved or removes the old association',t=>{
 const f=fixture(t);f.host.setFileContext({file_link_ref:link(),filename:'A.docx'});const before=fs.readFileSync(f.journal.file),commit=f.journal.commit;
 f.journal.commit=()=>{throw Error('fixture disk full');};assert.throws(()=>f.host.setFileContext({file_link_ref:link('b')}),/disk full/);assert.throws(()=>f.host.clearFileContext({scope:a,expectedRef:link()}),/disk full/);f.journal.commit=commit;
 assert.deepEqual(fs.readFileSync(f.journal.file),before);assert.deepEqual(f.host.scope.fileLinkRef,link());
});
test('direct conversations without a supplied session remain explicit and workspace-separated',t=>{
 const f=fixture(t);f.host.setScope({projectId:'project_a',workspaceId:'one'});const first={...f.host.scope};assert.ok(first.conversationId);f.host.setFileContext({file_link_ref:link()});
 f.host.setScope({projectId:'project_a',workspaceId:'two'});assert.notEqual(f.host.scope.conversationId,first.conversationId);assert.equal(f.host.scope.fileLinkRef,undefined);
 f.host.setScope({projectId:'project_a',workspaceId:'one'});assert.deepEqual(f.host.scope.fileLinkRef,link());
});
test('native saved-file question carries exact project/link/conversation and performs no screen capture or file write',async()=>{
 const calls=[];const host=new NativeCompanion({window:{},request:async(route,body)=>{calls.push({route,body});return {operation_id:body.operation_id,status:'NOT_CONFIGURED'};},platform:'win32'});
 host.capture=()=>assert.fail('saved file questions do not capture the screen');
 const body={operationId:'saved_question',rawText:'按刚才保存的批注继续',projectId:'project_a',fileLinkRef:link(),conversationId:'conversation_a'};
 const result=await host.submitMessage(body);assert.equal(result.status,'NOT_CONFIGURED');assert.equal(calls.length,1);assert.deepEqual(calls[0].body.file_link_ref,link());assert.equal(calls[0].body.context_ref,null);assert.equal(calls[0].body.project_id,'project_a');
 await assert.rejects(()=>host.submitMessage({...body,fileLinkRef:link('b')}),/input differs/);assert.equal(calls.length,1);
 await assert.rejects(()=>host.submitMessage({...body,operationId:'other',projectId:'project_b'}),/another question project/);
});
