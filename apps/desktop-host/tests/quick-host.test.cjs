'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {QuickJournal,quickMarks}=require('../quick-journal.cjs');
const {QuickHost,geometryForSnapshot,questionText}=require('../quick-host.cjs');
const reference=item=>({id:item.id,revision:item.revision,sha256:item.sha256});
const pageRef={id:'page-a',revision:1,sha256:'a'.repeat(64)};
function fixture(t,request=async(_url,body)=>({operation_id:body.operation_id,status:'RETURNED',text:'原结果'})) {
  const dataDir=fs.mkdtempSync(path.join(os.tmpdir(),'eeas-quick-host-'));
  t.after(()=>fs.rmSync(dataDir,{recursive:true,force:true}));
  const journal=new QuickJournal({dataDir,hostGeneration:'original-host'}),calls=[],events=[];
  const host=new QuickHost({journal,getCompanion:()=>({capture:async()=>({operation_id:'frozen-a',current:true,
    observation:{status:'READY_OBSERVATION',context_ref:pageRef,window:{title:'原生页面A'}}})}),
    request:async(url,body)=>{calls.push({url,body});return request(url,body,journal);},
    onChanged:()=>events.push('changed'),onResult:()=>events.push('result')});
  host.setScope({projectId:'project-a',conversationId:'conversation-a'});t.after(()=>host.close());
  return {dataDir,journal,host,calls,events};
}
async function draft(value,id='draft-a',delivery='DRAFT') {
  const capture=await value.host.capture({intent:'请解释这里',kind:'ask'});
  const record=value.host.save({draftId:id,expectedRevision:0,capture,comment:'请解释并修改这一处',regions:[],marks:[],delivery});
  return {record,capture};
}

test('oversized receipt projection keeps the complete reply tail across journal reopen and still enforces total storage bound',async t=>{
 const text='完整回复段落。'.repeat(40000)+'FINAL_REPLY_TAIL';
 const value=fixture(t,async(_url,body)=>({operation_id:body.operation_id,status:'RETURNED',text,extra:'x'.repeat(300000)}));
 const {capture}=await draft(value);
 const result=await value.host.send({operationId:'long-reply',draftId:'draft-a',capture,comment:'请解释并修改这一处',regions:[],marks:[]});
 assert.equal(result.text,text);
 const restored=new QuickJournal({dataDir:value.dataDir,hostGeneration:'next-host'});
 assert.equal(restored.command('long-reply').message.text,text);
 const before=fs.readFileSync(restored.file),record=restored.command('long-reply');
 assert.throws(()=>restored.observe('long-reply',{operation_id:'long-reply',status:'RETURNED',text:'x'.repeat(5*1024*1024)}),/storage is full/);
 assert.deepEqual(fs.readFileSync(restored.file),before);assert.deepEqual(restored.command('long-reply'),record);
});

test('confirmed inbox and acknowledgement survive restart without sending or changing scope',async t=>{
  const value=fixture(t),{record}=await draft(value);
  value.host.setScope({projectId:'project-b',conversationId:'conversation-b'});
  value.host.acknowledge(reference(record));
  const restored=new QuickJournal({dataDir:value.dataDir,hostGeneration:'next-host'});
  assert.equal(restored.list()[0].scope.projectId,'project-a');
  assert.equal(restored.list()[0].comment,'请解释并修改这一处');
  assert.equal(restored.list()[0].acknowledged.sha256,record.sha256);
  assert.equal(value.calls.length,0);
  assert.throws(()=>restored.prepareSend(reference(record),{operationId:'new-command',text:'修改'}),/earlier host/);
});

test('closed host does not query an already submitted command through send',async t=>{
  const value=fixture(t),{capture,record}=await draft(value);
  await value.host.send({operationId:'before_close',draftId:record.id,capture,comment:record.comment,regions:[],marks:[]});
  assert.equal(value.calls.length,1);value.host.close();
  await assert.rejects(()=>value.host.send({operationId:'before_close',draftId:record.id}),/正在关闭/);
  assert.equal(value.calls.length,1);
});
test('a save cannot promote a restored old page into the new host',async t=>{
  const value=fixture(t),{record}=await draft(value);
  const restored=new QuickJournal({dataDir:value.dataDir,hostGeneration:'next-host'});
  const saved=restored.save({draftId:'recovered-intent',expectedRevision:0,comment:'保留我的意见',delivery:'EDITING',
    capture:{snapshot:record.snapshot,scope:record.scope,hostGeneration:'original-host'}});
  assert.throws(()=>restored.prepareSend(reference(saved),{operationId:'no-authority-promotion',text:'修改'}),/earlier host/);
});
test('CAS conflict preserves original and a failed atomic replacement cannot claim saved',async t=>{
  const value=fixture(t),{record,capture}=await draft(value);
  assert.throws(()=>value.host.save({draftId:record.id,expectedRevision:0,capture,comment:'覆盖旧稿',regions:[],delivery:'DRAFT'}),/changed/);
  const before=fs.readFileSync(value.journal.file);
  const rename=fs.renameSync;fs.renameSync=()=>{throw Object.assign(Error('disk fixture'),{code:'ENOSPC'});};
  try {assert.throws(()=>value.host.save({draftId:'new-failed-draft',expectedRevision:0,capture,comment:'不能丢',regions:[],delivery:'DRAFT'}),/disk fixture/);}
  finally {fs.renameSync=rename;}
  assert.deepEqual(fs.readFileSync(value.journal.file),before);
  assert.equal(value.journal.find('new-failed-draft'),undefined);
  assert.equal(value.journal.get(reference(record)).comment,'请解释并修改这一处');
  assert.ok(fs.readdirSync(value.dataDir).some(name=>name.endsWith('.tmp')));
});
test('actual private journal contains no preview pixels, native handles, tokens or UIA content',async t=>{
  const value=fixture(t),capture=await value.host.capture({kind:'ask',intent:'页面问题'});
  capture.snapshot.preview={status:'AVAILABLE',data_url:'data:image/png;base64,c2VjcmV0',image_ref:{sha256:'b'.repeat(64)}};
  capture.snapshot.observation.observations={accessibility:{text:'NEVER_JOURNAL_FULL_UIA'}};
  capture.snapshot.observation.window.hwnd='NEVER_JOURNAL_HWND';
  value.host.save({draftId:'private',expectedRevision:0,capture,comment:'我的意见',delivery:'DRAFT'});
  assert.doesNotMatch(fs.readFileSync(value.journal.file,'utf8'),/data_url|base64|NEVER_JOURNAL/);
});
test('corrupt journal is retained and rejected instead of silently reset',async t=>{
  const value=fixture(t);await draft(value);const raw=fs.readFileSync(value.journal.file,'utf8');
  fs.writeFileSync(value.journal.file,raw.replace('请解释并修改这一处','被更改'));
  assert.throws(()=>new QuickJournal({dataDir:value.dataDir}),/integrity failed/);
  assert.match(fs.readFileSync(value.journal.file,'utf8'),/被更改/);
});
test('SEND is durably indexed before its only POST; ack/reopen do not submit another command',async t=>{
  const value=fixture(t,async(url,body,journal)=>{
    assert.ok(url.endsWith('/native-messages'));
    const reopened=new QuickJournal({dataDir:path.dirname(journal.file),hostGeneration:'probe'});
    assert.equal(reopened.command(body.operation_id).status,'DISPATCHING');
    return {operation_id:body.operation_id,status:'RETURNED',text:'真实通路的测试回执'};
  });
  const {record}=await draft(value);
  value.host.setScope({projectId:'project-b',conversationId:'conversation-b'});
  const result=await value.host.submitDraft({ref:reference(record),text:'补充限制'});
  assert.equal(result.status,'RETURNED');assert.equal(value.calls.length,1);
  assert.equal(value.calls[0].body.conversation_id,'conversation-a');
  assert.deepEqual(value.calls[0].body.context_ref,pageRef);
  assert.match(value.calls[0].body.raw_text,/补充限制/);
  const current=value.host.list().records[0];value.host.acknowledge(reference(current));
  assert.equal(value.calls.length,1);
  assert.equal(value.host.list().records[0].message.text,'真实通路的测试回执');
});
test('exact SEND history survives restart as a read-only durable reply without clone, capture, request or write',async t=>{
  const value=fixture(t),{record}=await draft(value);
  const sent=await value.host.submitDraft({ref:reference(record)}),current=value.journal.find(record.id);
  const journal=new QuickJournal({dataDir:value.dataDir,hostGeneration:'restarted-host'}),before=fs.readFileSync(journal.file);
  let snapshots=0,requests=0;
  const host=new QuickHost({journal,getCompanion:()=>null,loadSnapshot:async()=>{snapshots++;throw Error('History must not load a live snapshot');},
    request:async()=>{requests++;throw Error('History must not request');}});t.after(()=>host.close());
  host.setScope({projectId:'project-a',conversationId:'conversation-a',generation:999});
  journal.save=()=>{throw Error('History must not clone a draft');};
  host.captures.add=()=>{throw Error('History must not authorize a capture');};
  const restored=await host.load({draftRef:reference(current)});
  assert.equal(restored.id,current.id);assert.equal(restored.revision,current.revision);assert.equal(restored.comment,record.comment);
  assert.equal(restored.readonly,true);assert.equal(restored.status,'READONLY');assert.equal(restored.operationId,sent.operation_id);
  assert.equal(restored.message.status,'RETURNED');assert.equal(restored.message.text,'原结果');
  assert.deepEqual(restored.scope,current.scope);assert.deepEqual(restored.snapshot,current.snapshot);
  assert.equal(restored.capture.restored,true);assert.throws(()=>host.authorized(restored.capture),/不属于这次/);
  restored.message.text='仅修改调用方副本';assert.equal(host.loadReadonlyResult(sent.operation_id,current.id).text,'原结果');
  assert.deepEqual(fs.readFileSync(journal.file),before);assert.equal(journal.latestEditing(),undefined);
  assert.equal(snapshots,0);assert.equal(requests,0);assert.equal(host.timers.size,0);
});
test('read-only history keeps persisted pending states and missing receipts unresolved without auto inspection or dispatch',async t=>{
  const value=fixture(t);
  for(const status of [null,'REQUESTING','RUNNING','QUEUED','CANCEL_REQUESTED','UNKNOWN_OUTCOME']) {
    const id='history-'+(status??'DISPATCHING'),{record}=await draft(value,id);
    value.journal.prepareSend(reference(record),{operationId:id,text:'原问题'});
    if(status)value.journal.observe(id,{operation_id:id,status,text:'原回执 '+status});
    const current=value.journal.find(id),before=fs.readFileSync(value.journal.file);
    const loaded=await value.host.load({draftRef:reference(current)});
    assert.equal(loaded.readonly,true);assert.equal(loaded.operationId,id);
    assert.equal(loaded.message.status,status??'UNKNOWN_OUTCOME');
    assert.deepEqual(fs.readFileSync(value.journal.file),before);
  }
  assert.equal(value.calls.length,0);assert.equal(value.host.timers.size,0);
});
test('read-only history never promotes a volatile returned reply after persistence failure; only an explicit GET can persist it',async t=>{
  const value=fixture(t,async(url,body)=>{
    assert.equal(body,undefined);return {operation_id:url.split('/').at(-1),status:'RETURNED',text:'GET取得完整回复'};
  }),{record}=await draft(value);
  const operationId='readonly-persistence';value.journal.prepareSend(reference(record),{operationId,text:'原问题'});
  value.journal.observe(operationId,{operation_id:operationId,status:'REQUESTING',text:'持久等待状态'});
  const current=value.journal.find(record.id),before=fs.readFileSync(value.journal.file),observe=value.journal.observe;
  value.journal.observe=()=>{throw Error('fixture disk full');};
  const volatile=value.host.recordResult(operationId,{operation_id:operationId,status:'RETURNED',text:'尚未保存的回复'});
  assert.equal(volatile.local_persistence_status,'FAILED');assert.equal(value.host.observed.get(operationId).status,'RETURNED');
  let loaded=await value.host.load({draftRef:reference(current)});
  assert.equal(loaded.message.status,'REQUESTING');assert.equal(loaded.message.text,'持久等待状态');
  assert.deepEqual(fs.readFileSync(value.journal.file),before);assert.equal(value.calls.length,0);
  const failed=await value.host.inspect(operationId);assert.equal(failed.local_persistence_status,'FAILED');
  assert.equal(value.host.loadReadonlyResult(operationId,current.id).status,'REQUESTING');
  value.journal.observe=observe;await value.host.inspect(operationId);
  loaded=value.host.loadReadonlyResult(operationId,current.id);assert.equal(loaded.status,'RETURNED');assert.equal(loaded.text,'GET取得完整回复');
  assert.equal(value.calls.length,2);assert.ok(value.calls.every(call=>call.body===undefined));assert.equal(value.host.timers.size,0);
});
test('SEND history requires exact project, workspace, session and discussion while host generation does not change its association',async t=>{
  const value=fixture(t),scope={projectId:'project-a',workspaceId:'workspace-a',sessionId:'session-a',conversationId:'conversation-a'};
  value.host.setScope(scope);const {record}=await draft(value);
  value.journal.prepareSend(reference(record),{operationId:'scoped-history',text:'原问题'});
  value.journal.observe('scoped-history',{operation_id:'scoped-history',status:'COMPLETED',text:'已保存'});
  const current=value.journal.find(record.id),before=fs.readFileSync(value.journal.file);
  for(const key of ['projectId','workspaceId','sessionId','conversationId']) {
    value.host.setScope({...scope,[key]:key+'-other'});
    await assert.rejects(value.host.load({draftRef:reference(current)}),/another discussion/);
    assert.throws(()=>value.host.loadReadonlyResult('scoped-history',current.id),/another discussion/);
  }
  value.host.setScope(scope);assert.equal((await value.host.load({draftRef:reference(current)})).message.status,'COMPLETED');
  assert.deepEqual(fs.readFileSync(value.journal.file),before);assert.equal(value.calls.length,0);
});
test('SEND history rejects changed exact references and mismatched original-operation identities',async t=>{
  const value=fixture(t),{record}=await draft(value);
  value.journal.prepareSend(reference(record),{operationId:'identity-history',text:'原问题'});
  value.journal.observe('identity-history',{operation_id:'identity-history',status:'RETURNED',text:'原回复'});
  const current=value.journal.find(record.id),before=fs.readFileSync(value.journal.file);
  await assert.rejects(value.host.load({draftRef:{...reference(current),sha256:'b'.repeat(64)}}),/unavailable|integrity/);
  assert.throws(()=>value.host.loadReadonlyResult('identity-history','another-draft'),/operation differ/);
  const command=value.journal.state.commands['identity-history'];
  const originalMessage=command.message;command.message={...originalMessage,operation_id:'other-operation'};
  assert.throws(()=>value.host.loadReadonlyResult('identity-history',current.id),/reply and original operation differ/);
  command.message=originalMessage;
  const originalDraft=command.draftId;command.draftId='missing-draft';
  assert.throws(()=>value.host.loadReadonlyResult('identity-history',current.id),/question and original operation differ/);
  command.draftId=originalDraft;
  assert.deepEqual(fs.readFileSync(value.journal.file),before);assert.equal(value.calls.length,0);
});
test('DRAFT loading still creates an editable private clone and delayed snapshot loading cannot cross a scope change or close',async t=>{
  const value=fixture(t),{record}=await draft(value);
  const normal=await value.host.load({draftRef:reference(record)});
  assert.notEqual(normal.id,record.id);assert.equal(normal.status,'EDITING');assert.equal(normal.readonly,undefined);
  assert.equal(normal.comment,record.comment);assert.equal(value.host.authorized(normal.capture),normal.capture);
  assert.equal(value.journal.get(reference(record)).delivery,'DRAFT');
  for(const mutation of ['scope','scope-back','close']) {
    let release;value.host.loadSnapshot=()=>new Promise(resolve=>{release=resolve;});
    const before=fs.readFileSync(value.journal.file),pending=value.host.load({draftRef:reference(record)});
    assert.equal(typeof release,'function');
    if(mutation==='close')value.host.close();
    else {value.host.setScope({projectId:'other-project',conversationId:'other-discussion'});if(mutation==='scope-back')value.host.setScope({projectId:'project-a',conversationId:'conversation-a'});}
    release({current:true,observation:{context_ref:pageRef}});
    await assert.rejects(pending,/当前讨论已变化/);assert.deepEqual(fs.readFileSync(value.journal.file),before);
    if(mutation!=='close')value.host.setScope({projectId:'project-a',conversationId:'conversation-a'});
  }
  assert.equal(value.calls.length,0);
});
test('timeout remains UNKNOWN; 404 inspection never repeats POST or treats it as not executed',async t=>{
  const value=fixture(t,async(url,body)=>{if(body)throw Error('wire outcome unknown');throw Object.assign(Error('not observed'),{status:404});});
  const {record}=await draft(value);
  const sent=await value.host.submitDraft({ref:reference(record)});
  assert.equal(sent.status,'UNKNOWN_OUTCOME');
  const result=await value.host.inspect(sent.operation_id);
  assert.equal(result.status,'UNKNOWN_OUTCOME');
  assert.equal(value.calls.filter(call=>call.body).length,1);
  assert.equal(value.calls.filter(call=>!call.body).length,1);
});
test('new host restores intent but cannot dispatch saved authority; reopen confirmed entry preserves original',async t=>{
  const value=fixture(t),{record}=await draft(value);
  const restored=await value.host.load({draftId:record.id});
  assert.notEqual(restored.id,record.id);assert.equal(restored.restored,true);
  assert.equal(value.journal.get(reference(record)).delivery,'DRAFT');
  assert.equal(value.host.list().records.length,1);
  assert.equal(value.journal.latestEditing().comment,record.comment);
});
test('malformed geometry and oversized/invalid fields cannot turn a frame into input authority',async t=>{
  const value=fixture(t),{capture}=await draft(value);
  assert.throws(()=>value.host.save({draftId:undefined,expectedRevision:0,capture,comment:'x',delivery:'EDITING'}),/identity/);
  assert.throws(()=>value.host.save({draftId:'overlarge',expectedRevision:0,capture,comment:'x'.repeat(16001),delivery:'DRAFT'}),/bounded/);
  assert.throws(()=>value.host.save({draftId:'outside',expectedRevision:0,capture,comment:'x',regions:[{x:.9,y:0,width:.5,height:1}],delivery:'DRAFT'}),/region/);
  assert.throws(()=>value.host.save({draftId:'spoofed',expectedRevision:0,capture:{...capture},comment:'x',delivery:'DRAFT'}),/不属于这次/);
});
test('geometry requires byte-bound independent visible-frame proof, never metadata alone',()=>{
  const image={sha256:'a'.repeat(64),width:100,height:80};
  const snapshot={current:true,preview:{status:'AVAILABLE',image_ref:image},observation:{observations:{visual:{
    width:100,height:80,image_ref:image,pixel_source:'UNKNOWN'}}}};
  assert.equal(geometryForSnapshot(snapshot),undefined);
  const frame=snapshot.observation.observations.visual;
  frame.pixel_source='VISIBLE_TARGET_RECTANGLE';frame.pixel_source_proof={status:'VISIBLE_TARGET_RECTANGLE',
    capture_method:'PILLOW_ACTUAL_DESKTOP_CROP',coordinate_space:'PHYSICAL_PIXELS_OWN_THREAD_PMV2',
    image_sha256:image.sha256,self_exclusion_verified:false,target_bounds:{x:-100,y:30,width:100,height:80}};
  assert.deepEqual(geometryForSnapshot(snapshot).physicalBounds,{x:-100,y:30,width:100,height:80});
  frame.pixel_source_proof.image_sha256='b'.repeat(64);assert.equal(geometryForSnapshot(snapshot),undefined);
});

test('actual WGC exact-HWND producer fields bind image, window lifetime and inset capture bounds',()=>{
  const image={sha256:'a'.repeat(64),width:98,height:76},target={id:'b'.repeat(64),revision:1,sha256:'c'.repeat(64)};
  const targetBounds={x:-100,y:30,width:100,height:80},bounds={x:-99,y:32,width:98,height:76};
  const snapshot={current:true,preview:{status:'AVAILABLE',image_ref:image},observation:{
    window_ref:target,window_lifetime_sha256:target.id,window:{bounds:targetBounds},observations:{visual:{
      width:98,height:76,image_ref:image,target_ref:target,pixel_source:'EXACT_TARGET_WINDOW',
      automatic_input_authorized:false,visual_coordinate_input_authorized:false,
      pixel_source_proof:{status:'EXACT_TARGET_WINDOW',capture_method:'WINDOWS_GRAPHICS_CAPTURE_EXACT_HWND',
        coordinate_space:'PHYSICAL_PIXELS_OWN_THREAD_PMV2',self_exclusion_verified:true,image_sha256:image.sha256,
        target_identity_sha256:target.id,target_bounds:targetBounds,capture_bounds:bounds}}}}};
  assert.deepEqual(geometryForSnapshot(snapshot),{physicalBounds:bounds,pixelWidth:98,pixelHeight:76,verified:true,pixelSource:'EXACT_TARGET_WINDOW'});
  const mutations=[
    s=>s.current=false,
    s=>s.observation.observations.visual.pixel_source_proof.status='VERIFIED_TARGET_PIXELS',
    s=>s.observation.observations.visual.pixel_source_proof.capture_method='METADATA_ONLY',
    s=>s.observation.observations.visual.pixel_source_proof.coordinate_space='DIP',
    s=>s.observation.observations.visual.pixel_source_proof.self_exclusion_verified=false,
    s=>s.observation.observations.visual.pixel_source_proof.image_sha256='d'.repeat(64),
    s=>s.observation.window_lifetime_sha256='d'.repeat(64),
    s=>s.observation.observations.visual.target_ref={...target,revision:2},
    s=>s.observation.observations.visual.pixel_source_proof.target_bounds={...targetBounds,x:0},
    s=>s.observation.observations.visual.pixel_source_proof.capture_bounds={...bounds,x:-102},
    s=>delete s.observation.observations.visual.pixel_source_proof.capture_bounds,
  ];
  for(const mutate of mutations){const changed=structuredClone(snapshot);mutate(changed);assert.equal(geometryForSnapshot(changed),undefined);}
  assert.equal(snapshot.observation.observations.visual.visual_coordinate_input_authorized,false);
});

test('exact historical reopen preserves its text and topic while newer edits remain intact',async t=>{
  const value=fixture(t),{record,capture}=await draft(value,'versioned','EDITING');
  value.host.save({draftId:record.id,expectedRevision:1,capture,comment:'最新的人工编辑',delivery:'EDITING'});
  value.host.setScope({projectId:'project-b',conversationId:'conversation-b'});
  assert.equal(await value.host.load(),undefined);
  const restored=await value.host.load({draftRef:reference(record)});
  assert.notEqual(restored.id,record.id);assert.equal(restored.comment,record.comment);
  assert.equal(restored.capture.scope.conversationId,'conversation-a');
  assert.equal(value.journal.find(record.id).comment,'最新的人工编辑');
  assert.equal(value.journal.get(reference(record)).comment,record.comment);
  assert.throws(()=>value.journal.prepareSend(reference(record),{operationId:'old-version',text:'旧版'}),/changed/);
  assert.equal(value.calls.length,0);
});
test('a corrupt existing archive prevents replacement and preserves the original head',async t=>{
  const value=fixture(t),{record,capture}=await draft(value,'corrupt-history','EDITING');
  value.journal.archive(record);
  const file=value.journal.historyPath(reference(record));
  fs.writeFileSync(file,fs.readFileSync(file,'utf8').replace(record.comment,'错误的旧稿'));
  assert.throws(()=>value.host.save({draftId:record.id,expectedRevision:1,capture,comment:'新版',delivery:'EDITING'}),/integrity/);
  assert.equal(value.journal.find(record.id).comment,record.comment);
  assert.match(fs.readFileSync(file,'utf8'),/错误的旧稿/);
});
test('known execution is retained when result persistence or UI notifications fail',async t=>{
  const value=fixture(t),{record}=await draft(value);
  value.journal.observe=()=>{throw Object.assign(Error('disk full'),{code:'ENOSPC'});};
  value.host.onChanged=()=>{throw Error('local notification failed');};
  value.host.onResult=async()=>{throw Error('local renderer failed');};
  const result=await value.host.submitDraft({ref:reference(record)});
  assert.equal(result.status,'RETURNED');assert.equal(result.local_persistence_status,'FAILED');
  assert.equal(value.host.list().records[0].message.status,'RETURNED');
  assert.equal(value.calls.length,1);
  const reopened=new QuickJournal({dataDir:value.dataDir,hostGeneration:'restart'});
  assert.equal(reopened.find(record.id).delivery,'SEND');
  assert.equal(reopened.command(result.operation_id).status,'DISPATCHING');
});
test('restart presents unacknowledged dispatch and recovers solely through its original GET',async t=>{
  const value=fixture(t),{record}=await draft(value);
  value.journal.prepareSend(reference(record),{operationId:'unfinished',text:'原意见'});
  const recovered=new QuickHost({journal:new QuickJournal({dataDir:value.dataDir,hostGeneration:'restart'}),
    getCompanion:()=>null,request:async(url,body)=>{
      assert.equal(body,undefined);assert.equal(url,'/api/desktop-host/native-messages/unfinished');
      return {operation_id:'unfinished',status:'RETURNED',text:'原操作已完成'};
    }});t.after(()=>recovered.close());
  recovered.setScope({projectId:'project-b',conversationId:'conversation-b'});
  assert.equal(recovered.list().records[0].message.status,'UNKNOWN_OUTCOME');
  await recovered.restoreObservations();
  assert.equal(recovered.list().records[0].message.status,'RETURNED');
  assert.equal(recovered.list().records[0].scope.conversationId,'conversation-a');
  assert.equal(value.calls.length,0);
});
test('long result traces keep terminal status and the durable original operation link',async t=>{
  const value=fixture(t,async(_url,body)=>({operation_id:body.operation_id,status:'RETURNED',text:'已完成',
    events:[{text:'真实执行事件'.repeat(90000)}]}));
  const {record}=await draft(value);const result=await value.host.submitDraft({ref:reference(record)});
  assert.equal(result.status,'RETURNED');
  const saved=value.journal.find(record.id).message;
  assert.equal(saved.status,'RETURNED');assert.equal(saved.events_count,1);
  assert.equal(saved.local_projection.original_operation_id,result.operation_id);
  assert.equal(saved.local_projection.complete,false);
});
test('plain saved questions need no old screen authority but changed drawings must be resaved',async t=>{
  const value=fixture(t);
  const plain=value.host.save({draftId:'plain',expectedRevision:0,comment:'一个普通问题',delivery:'DRAFT'});
  const reopened=new QuickJournal({dataDir:value.dataDir,hostGeneration:'restart'});
  assert.equal(reopened.prepareSend(reference(plain),{operationId:'plain-op',text:'普通问题'}).dispatchable,true);
  const {record,capture}=await draft(value,'changed-mark');
  await assert.rejects(value.host.send({draftId:record.id,operationId:'not-sent',capture,comment:record.comment,
    marks:[{type:'rect',x:0,y:0,width:.2,height:.2}],regions:[]}),/不一致/);
  assert.equal(value.calls.length,0);
});

test('on-image text survives actual journal history and restart without repeating it in the comment',async t=>{
  const value=fixture(t,async(url,body)=>({operation_id:body?.operation_id??url.split('/').at(-1),status:'RETURNED',text:'原结果'})),
    capture=await value.host.capture({kind:'annotate',intent:'图上批注'});
  const mark={type:'text',x:.25,y:.4,text:'  这里的假设为什么成立？\n请保留这句：🧪  '};
  const record=value.host.save({draftId:'image-text',expectedRevision:0,capture,comment:'',marks:[mark],delivery:'EDITING'});
  const confirmed=value.host.save({draftId:record.id,expectedRevision:1,capture,comment:'',marks:[mark],delivery:'DRAFT'});
  const reopened=new QuickJournal({dataDir:value.dataDir,hostGeneration:'next-host'});
  assert.deepEqual(reopened.get(reference(record)).marks,[{kind:'text',x:.25,y:.4,text:mark.text}]);
  assert.deepEqual(reopened.get(reference(confirmed)).marks,reopened.get(reference(record)).marks);
  assert.equal(value.calls.length,0);
  assert.throws(()=>reopened.prepareSend(reference(confirmed),{operationId:'old-basis',text:mark.text}),/earlier host/);
  const result=await value.host.submitDraft({ref:reference(confirmed)});
  assert.equal(result.status,'RETURNED');assert.equal(value.calls.length,1);
  assert.match(value.calls[0].body.raw_text,/这里的假设为什么成立/);
  assert.deepEqual(value.calls[0].body.context_ref,pageRef);
  assert.equal(value.calls[0].body.conversation_id,'conversation-a');
  await value.host.submitDraft({ref:reference(value.journal.find(record.id))});
  assert.equal(value.calls.filter(call=>call.body).length,1);
});
test('shape-only feedback needs actual intent, including explicit inbox supplement, before its single POST',async t=>{
  const value=fixture(t),capture=await value.host.capture({kind:'annotate',intent:'图上批注'});
  const record=value.host.save({draftId:'shape-only',expectedRevision:0,capture,comment:'',
    marks:[{type:'rect',x:.1,y:.2,width:.2,height:.2}],delivery:'DRAFT'});
  await assert.rejects(value.host.submitDraft({ref:reference(record)}),/请补充/);
  assert.equal(value.calls.length,0);assert.equal(value.journal.find(record.id).delivery,'DRAFT');
  await value.host.submitDraft({ref:reference(record),text:'这里的边界条件不完整，请解释'});
  assert.equal(value.calls.length,1);assert.match(value.calls[0].body.raw_text,/边界条件/);
  assert.match(value.calls[0].body.raw_text,/"kind":"rect"/);
});
test('text marks reject hidden authority, malformed Unicode, conflicting kinds and oversized content',()=>{
  const note={type:'text',x:0,y:1,text:'🧪'.repeat(2000)};
  assert.equal(Array.from(quickMarks([note])[0].text).length,2000);
  for(const changed of [{...note,text:'🧪'.repeat(2001)},{...note,text:'\ud800'},{...note,text:'  '},
    {...note,x:NaN},{...note,y:1.01},{...note,command:'write-file'},
    {...note,kind:'rect'},{type:'rect',x:0,y:0,width:.1,height:.1,text:'smuggled'}])
    assert.throws(()=>quickMarks([changed]));
  assert.equal(quickMarks(Array.from({length:8},()=>note)).length,8);
  assert.throws(()=>quickMarks(Array.from({length:9},()=>note)),/text budget/);
  assert.throws(()=>questionText({comment:'',regions:[],marks:[note]}),/原页面依据/);
  assert.throws(()=>questionText({comment:'',regions:[],marks:Array.from({length:8},()=>note),contextRef:pageRef}),/缩小/);
});
