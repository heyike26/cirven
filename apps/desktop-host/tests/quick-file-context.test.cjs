'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {QuickJournal,quickScope}=require('../quick-journal.cjs');
const {QuickHost}=require('../quick-host.cjs');
const reference=x=>({id:x.id,revision:x.revision,sha256:x.sha256});
const link=(pid='project_a',digit='a')=>({project_id:pid,id:digit.repeat(64),revision:1,sha256:'f'.repeat(64)});
function fixture(t,request){
  const dataDir=fs.mkdtempSync(path.join(process.env.EEAS_QA_TMP||os.tmpdir(),'quick-file-'));t.after(()=>fs.rmSync(dataDir,{recursive:true,force:true}));
  const journal=new QuickJournal({dataDir,hostGeneration:'host_a'}),calls=[];
  const host=new QuickHost({journal,getCompanion:()=>({capture:async()=>({operation_id:'capture',current:false,observation:{status:'UNAVAILABLE'}})}),request:async(url,body)=>{calls.push({url,body});return request?request(url,body,journal):{operation_id:body.operation_id,status:'RUNNING'};}});
  host.setScope({projectId:'project_a',conversationId:'conversation_a'});t.after(()=>host.close());return {dataDir,journal,host,calls};
}
function save(f,id='draft_a',capture=f.host.emptyCapture()){return f.host.save({draftId:id,expectedRevision:0,capture,comment:'解释文稿中的这个问题',regions:[],marks:[],delivery:'DRAFT'});}

test('native Quick preserves an unconfirmed selected-context receipt and only inspects its original input',async t=>{
 const selected='d'.repeat(64);const f=fixture(t,async(_url,body)=>body?
  {operation_id:body.operation_id,status:'RUNNING'}:
  {operation_id:f.journal.list()[0].operationId,status:'RETURNED',context_selection_id:selected,text:'Confirmed selected sources'});
 f.host.setScope({projectId:'project_a',conversationId:'conversation_a',contextSelectionId:selected,contextSelectionOperationId:'selected_once'});
 const draft=save(f,'selected_context');
 const sent=await f.host.submitDraft({ref:reference(draft)});
 assert.equal(sent.status,'UNKNOWN_OUTCOME');assert.equal(sent.context_selection_mismatch,true);
 assert.equal(f.calls.filter(call=>call.body).length,1);assert.equal(f.calls[0].body.context_selection_id,selected);
 const checked=await f.host.inspect(sent.operation_id);assert.equal(checked.status,'RETURNED');
 assert.equal(checked.context_selection_id,selected);assert.equal(f.calls.filter(call=>call.body).length,1);
 assert.equal(f.journal.find(draft.id).scope.contextSelectionOperationId,'selected_once');
});

test('exact project file index survives restart; switching projects never borrows another file and old drafts remain frozen',async t=>{
  const f=fixture(t);f.host.setFileContext({file_link_ref:link(),filename:'实际 文稿.docx'});const original=save(f);
  f.host.setFileContext({file_link_ref:link('project_a','b'),filename:'另一稿.docx'});f.host.setScope({projectId:'project_b'});
  assert.equal(f.host.scope.fileLinkRef,undefined);assert.equal(f.journal.find(original.id).scope.fileLinkRef.id,'a'.repeat(64));
  const result=await f.host.submitDraft({ref:reference(original)});assert.equal(result.status,'RUNNING');assert.equal(f.calls[0].body.project_id,'project_a');assert.deepEqual(f.calls[0].body.file_link_ref,link());assert.equal(f.calls[0].body.file_source_ref,null);assert.equal(f.calls[0].body.context_ref,null);assert.match(f.calls[0].body.raw_text,/不授予写入权限/);
  const journal=new QuickJournal({dataDir:f.dataDir,hostGeneration:'next_host'}),restored=new QuickHost({journal,getCompanion:()=>({}),request:async()=>{throw Error('must not dispatch');}});t.after(()=>restored.close());restored.setScope({projectId:'project_a'});assert.equal(restored.scope.fileLinkRef,undefined,'project-only lookup must not adopt another discussion');restored.setScope({projectId:'project_a',conversationId:'conversation_a'});
  assert.equal(restored.scope.fileLinkRef.id,'b'.repeat(64));assert.equal(restored.scope.fileLabel,'另一稿.docx');assert.equal(journal.find(original.id).scope.fileLinkRef.id,'a'.repeat(64));
  assert.throws(()=>quickScope({projectId:'project_b',fileLinkRef:link()}),/another quick project/);
});

test('64-project file lookup is bounded cache only; eviction cannot delete drafts, commands or exact link history',t=>{
  const f=fixture(t);f.host.setFileContext({file_link_ref:link(),filename:'旧稿.docx'});const original=save(f);
  for(let i=0;i<66;i++){f.host.setScope({projectId:'project_'+i,conversationId:'explicit_conversation_'+i});f.host.setFileContext({file_link_ref:link('project_'+i),filename:'实际稿'+i+'.docx'});}
  assert.equal(Object.keys(f.journal.state.scopedFileContexts).length,64);assert.equal(f.journal.fileContext({projectId:'project_a',conversationId:'conversation_a'}),undefined);assert.deepEqual(f.journal.get(reference(original)).scope.fileLinkRef,link());
  assert.equal(f.journal.list().length,1);assert.equal(Object.keys(f.journal.state.commands).length,0);
});

test('file selection changes cannot implicitly resume a different file draft; clearing is a registered capture clone with same frozen screen/topic',async t=>{
  const f=fixture(t);f.host.setFileContext({file_link_ref:link(),filename:'A.docx'});const capture=f.host.emptyCapture(),old=save(f,'private_a',capture);
  f.host.save({draftId:'editing_a',expectedRevision:0,capture,comment:'人工未发意见',delivery:'EDITING'});
  f.host.setFileContext({file_link_ref:link('project_a','b'),filename:'B.docx'});assert.equal(await f.host.load(),undefined);
  assert.throws(()=>f.host.clearDraftFileContext({...capture}),/不属于/);
  const cleared=f.host.clearDraftFileContext(capture);assert.equal(cleared.snapshot,capture.snapshot);assert.equal(cleared.hostGeneration,capture.hostGeneration);assert.equal(cleared.scope.fileLinkRef,undefined);assert.equal(cleared.scope.conversationId,capture.scope.conversationId);
  const revised=f.host.save({draftId:old.id,expectedRevision:old.revision,capture:cleared,comment:old.comment,delivery:'EDITING'});assert.equal(revised.scope.fileLinkRef,undefined);assert.deepEqual(f.journal.get(reference(old)).scope.fileLinkRef,link());
  const exact=await f.host.load({draftRef:reference(old)});assert.deepEqual(exact.capture.scope.fileLinkRef,link());assert.equal(exact.capture.restored,false);
  f.host.setScope({projectId:'project_b'});await f.host.send({draftId:revised.id,operationId:'cleared_original_project',capture:cleared,comment:revised.comment,regions:[],marks:[]});
  assert.equal(f.calls.at(-1).body.project_id,'project_a');assert.equal(f.calls.at(-1).body.file_link_ref,null);assert.equal(f.calls.at(-1).body.file_source_ref,null);
});

test('cancel is journaled before exactly one original-operation POST; duplicate clicks/restart only GET and other task remains unchanged',async t=>{
  let stopPosts=0;const f=fixture(t,async(url,body,journal)=>{
    const op=body?.operation_id??url.split('/').at(url.endsWith('/cancel')?-2:-1);
    if(url.endsWith('/cancel')){stopPosts++;assert.equal(journal.command(op).cancel.status,'DISPATCHING');return {operation_id:op,status:'CANCEL_REQUESTED'};}
    return {operation_id:op,status:'RUNNING'};
  });const draft=save(f);const sent=await f.host.submitDraft({ref:reference(draft)});const other=save(f,'other');await f.host.submitDraft({ref:reference(other)});
  f.host.setScope({projectId:'elsewhere'});await Promise.all([f.host.cancel(sent.operation_id),f.host.cancel(sent.operation_id)]);assert.equal(stopPosts,1);assert.equal(f.journal.find(other.id).message.status,'RUNNING');
  const restored=new QuickHost({journal:new QuickJournal({dataDir:f.dataDir,hostGeneration:'restart'}),getCompanion:()=>({}),request:async(url,body)=>{assert.equal(body,undefined);return {operation_id:sent.operation_id,status:'CANCELLED'};}});t.after(()=>restored.close());
  const done=await restored.cancel(sent.operation_id);assert.equal(done.status,'CANCELLED');assert.equal(done.cancellation_status,'SETTLED');assert.equal(stopPosts,1);
});

test('uncertain cancel result preserves known running execution, never repeats POST, and read-only 404 is not cancellation success',async t=>{
  const f=fixture(t,async(url,body)=>{if(url.endsWith('/cancel'))throw Error('response lost');if(!body)throw Object.assign(Error('missing'),{status:404});return {operation_id:body.operation_id,status:'RUNNING'};});
  const sent=await f.host.submitDraft({ref:reference(save(f))}),first=await f.host.cancel(sent.operation_id);
  assert.equal(first.status,'RUNNING');assert.equal(first.cancellation_status,'UNKNOWN');const result=await f.host.cancel(sent.operation_id);assert.equal(result.status,'UNKNOWN_OUTCOME');assert.equal(result.cancellation_status,'UNKNOWN');assert.equal(f.calls.filter(c=>c.url.endsWith('/cancel')).length,1);assert.equal(f.journal.command(sent.operation_id).cancel.status,'UNKNOWN');
});

test('cancel preflight storage failure sends nothing and a late pending cancel response cannot replace a known returned execution',async t=>{
  let release;const wait=new Promise(resolve=>release=resolve),f=fixture(t,async(url,body)=>url.endsWith('/cancel')?wait:{operation_id:body.operation_id,status:'RUNNING'});
  const sent=await f.host.submitDraft({ref:reference(save(f))}),commit=f.journal.commit;f.journal.commit=()=>{throw Error('disk full');};await assert.rejects(f.host.cancel(sent.operation_id),/disk full/);assert.equal(f.calls.filter(c=>c.url.endsWith('/cancel')).length,0);f.journal.commit=commit;
  const stopping=f.host.cancel(sent.operation_id);f.host.recordResult(sent.operation_id,{operation_id:sent.operation_id,status:'RETURNED',text:'已完成的实际回复'});release({operation_id:sent.operation_id,status:'RUNNING'});
  const final=await stopping;assert.equal(final.status,'RETURNED');assert.equal(final.text,'已完成的实际回复');assert.equal(final.cancellation_status,'SETTLED');
});
