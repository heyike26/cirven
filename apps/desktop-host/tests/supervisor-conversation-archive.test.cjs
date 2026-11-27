'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {SupervisorConversation,supervisorConversationDigest:digest,MAX_SUPERVISOR_STATE_BYTES}=require('../supervisor-conversation.cjs');
const {supervisorArchiveDirectory,SUPERVISOR_ARCHIVE_LIMITS,planSupervisorSegment,writeSupervisorSegment}=require('../supervisor-conversation-archive.cjs');
const profile={id:'archive_profile',revision:1,sha256:'a'.repeat(64)};
const receipt=body=>({operation_id:body.operation_id,request_sha256:digest(body),project_id:null,conversation_id:body.conversation_id,context_ref:null,file_source_ref:null,file_link_ref:null,provider_profile_ref:body.provider_profile_ref,automatic_retry:false,status:'RETURNED',text:'保存后的真实原回执内容'});
function fixture(t,dispatch){
  const dataDir=fs.mkdtempSync(path.join(os.tmpdir(),'cirven-supervisor-archive-'));t.after(()=>fs.rmSync(dataDir,{recursive:true,force:true}));const calls=[],saved=new Map();
  const request=async(route,body)=>{calls.push({route,body});if(route==='/api/supervisor')return {tasks:[],observer:{status:'READY'}};if(body)saved.set(body.operation_id,body);return dispatch?dispatch(route,body,saved):receipt(body??saved.get(route.split('/').at(-1)));};
  const host=new SupervisorConversation({dataDir,request});return {host,dataDir,calls,saved,request,ask:(operationId,text='核查原任务 '+operationId,followupRef)=>host.ask({operationId,text,providerProfileRef:profile,...followupRef?{followupRef}:{}})};
}
const fill=(host,count=100,answer='本地已知状态')=>{for(let i=0;i<count;i++)host.persistLocal({question:'本地问题 '+i,answer});};
// Independently construct an old valid on-disk head. Repeating hundreds of
// setup fsyncs on the WSL D mount adds minutes without testing a new boundary.
function seed(f,{locals=0,operations=0,firstQuestion='最早原问题😀\r\n保留空格  ',firstId='old_original'}={}){
  const state=JSON.parse(JSON.stringify(f.host.state));state.operations=[];state.local=[];let sequence=0;
  for(let i=0;i<operations;i++){
    const operation_id=i===0?firstId:'seed_operation_'+i,question=i===0?firstQuestion:'已完成旧问题 '+i;
    const body={operation_id,raw_text:'独立历史夹具 '+question,context_ref:null,file_source_ref:null,file_link_ref:null,project_id:null,conversation_id:state.conversation_id,provider_profile_ref:profile};
    state.operations.push({operation_id,question,provider_profile_ref:profile,input_sha256:digest({operation_id,text:question,provider_profile_ref:profile}),request_sha256:digest(body),context_sha256:digest({fixture:i}),status:'RETURNED',source_status:'RETURNED',text:'保存后的真实原回执内容',created_at:new Date(1700000000000+i).toISOString(),sequence:++sequence});
  }
  for(let i=0;i<locals;i++)state.local.push({id:'seed_local_'+i,question:'本地问题 '+i,text:'本地已知状态',created_at:new Date(1700000000000+operations+i).toISOString(),sequence:++sequence});
  state.history_sequence=sequence;fs.writeFileSync(f.host.file,JSON.stringify({version:1,state,sha256:digest(state)})+'\n');f.host=new SupervisorConversation({dataDir:f.dataDir,request:f.request});
  f.ask=(operationId,text='核查原任务 '+operationId,followupRef)=>f.host.ask({operationId,text,providerProfileRef:profile,...followupRef?{followupRef}:{}});return f.host;
}
function allHistory(host){let cursor=null,messages=[],pages=0;do{const page=host.readHistoryPage({cursor,limit:7});messages=[...page.messages,...messages];cursor=page.next_cursor;assert.ok(++pages<1000);}while(cursor);return messages;}
function archiveFiles(host){const root=supervisorArchiveDirectory(host.file);return fs.existsSync(root)?fs.readdirSync(root).filter(name=>name.endsWith('.json')).map(name=>path.join(root,name)):[];}

test('more than the previous 64 local rows continue transparently with immutable segments and complete paged history after restart',t=>{
  const f=fixture(t);seed(f,{locals:64});f.host.saveDraft({text:'仍未发出的用户文字😀',expectedRevision:0});fill(f.host,1);
  const snapshot=f.host.snapshot();assert.ok(snapshot.history.archived_records>0);assert.equal(snapshot.draft.text,'仍未发出的用户文字😀');assert.ok(f.host.state.local.length<=64);assert.ok(fs.statSync(f.host.file).size<=MAX_SUPERVISOR_STATE_BYTES);assert.equal(f.calls.length,0);
  const before=new Map(archiveFiles(f.host).map(file=>[file,fs.readFileSync(file)]));fill(f.host,32);for(const [file,bytes]of before)assert.deepEqual(fs.readFileSync(file),bytes);
  const restored=new SupervisorConversation({dataDir:f.dataDir,request:f.request}),messages=allHistory(restored);assert.equal(messages.length,(64+1+32)*2);assert.equal(new Set(messages.map(row=>row.id)).size,messages.length);assert.equal(messages[0].text,'本地问题 0');assert.equal(messages.at(-2).text,'本地问题 31');assert.equal(restored.snapshot().draft.text,'仍未发出的用户文字😀');
});

test('old archived original operations keep exact lookups, duplicate id idempotency, changed-input rejection and selected follow-up',async t=>{
  const f=fixture(t);seed(f,{operations:128});const old=f.host.lookupOperation('old_original');await f.ask('operation_after_128');
  assert.ok(f.host.snapshot().history.archived_operations>0);assert.ok(!f.host.state.operations.some(row=>row.operation_id==='old_original'));
  const bytes=fs.readFileSync(f.host.file),calls=f.calls.length,display=f.host.displayForOperation('old_original',old.request_sha256);assert.equal(display.question,'最早原问题😀\r\n保留空格  ');assert.equal(display.status,'RETURNED');
  await f.ask('old_original','最早原问题😀\r\n保留空格  ');await f.host.inspect('old_original');assert.equal(f.calls.length,calls);assert.deepEqual(fs.readFileSync(f.host.file),bytes);
  assert.throws(()=>f.ask('old_original','替换旧请求'),/SUPERVISOR_OPERATION_CONFLICT/);
  const followup={operation_id:old.operation_id,request_sha256:old.request_sha256};await f.ask('followup_archived','继续解释旧问题',followup);
  assert.match(f.saved.get('followup_archived').raw_text,/最早原问题/);
  const reopened=new SupervisorConversation({dataDir:f.dataDir,request:f.request});assert.deepEqual(reopened.lookupOperation('old_original'),old);assert.equal(reopened.state.operations.find(row=>row.operation_id==='followup_archived').followup_ref.request_sha256,old.request_sha256);
});

test('UNKNOWN remains live and exact while unrelated terminal history archives, with no automatic replay on restart',async t=>{
  let unavailable=true;const f=fixture(t,(route,body,saved)=>{if((body?.operation_id??route.split('/').at(-1))==='unknown_original'&&unavailable)throw Error('fixture unavailable');return receipt(body??saved.get(route.split('/').at(-1)));});
  seed(f,{locals:64});assert.equal((await f.ask('unknown_original','未知的原问题')).status,'UNKNOWN');fill(f.host,1);assert.equal(f.host.state.operations.find(row=>row.operation_id==='unknown_original').status,'UNKNOWN');assert.ok(!f.host.state.archive.segments.some(row=>row.operations.includes('unknown_original')));
  const oldCalls=f.calls.length,reopened=new SupervisorConversation({dataDir:f.dataDir,request:f.request});assert.equal(f.calls.length,oldCalls);
  await reopened.ask({operationId:'unknown_original',text:'未知的原问题',providerProfileRef:profile});assert.equal(f.calls.length,oldCalls);
  assert.throws(()=>reopened.ask({operationId:'new_key',text:'未知的原问题',providerProfileRef:profile}),/SUPERVISOR_UNKNOWN_INPUT_REPLAY_BLOCKED/);
  unavailable=false;assert.equal((await reopened.inspect('unknown_original')).status,'RETURNED');assert.equal(f.calls.filter(row=>row.body?.operation_id==='unknown_original').length,1);
});

test('active operation and newer draft remain in head while local history is sealed and the eventual result is adopted once',async t=>{
  let release;const gate=new Promise(resolve=>release=resolve),f=fixture(t,async(_route,body)=>{await gate;return receipt(body);});
  seed(f,{locals:64});const asking=f.ask('active_original');await new Promise(resolve=>setImmediate(resolve));f.host.saveDraft({text:'执行期间新增输入',expectedRevision:f.host.snapshot().draft.revision});fill(f.host,1);
  assert.equal(f.host.state.operations.find(row=>row.operation_id==='active_original').status,'CLAIMED');assert.ok(!f.host.state.archive.segments.some(ref=>ref.operations.includes('active_original')));
  release();assert.equal((await asking).status,'RETURNED');assert.equal(f.host.snapshot().draft.text,'执行期间新增输入');assert.equal(f.calls.filter(row=>row.body).length,1);
});

test('cursor is stable across draft writes, rejects altered history basis, and never chooses an external path',t=>{
  const f=fixture(t);seed(f,{locals:64});fill(f.host,1);const first=f.host.readHistoryPage({limit:8});assert.ok(first.next_cursor);f.host.saveDraft({text:'只改变草稿',expectedRevision:0});const second=f.host.readHistoryPage({cursor:first.next_cursor,limit:8});assert.equal(second.messages.length,16);assert.equal(second.basis_sha256,first.basis_sha256);
  fill(f.host,1);assert.throws(()=>f.host.readHistoryPage({cursor:second.next_cursor}),/SUPERVISOR_HISTORY_CHANGED/);
  for(const cursor of ['../outside','=',Buffer.from(JSON.stringify({version:1,conversation_id:f.host.state.conversation_id,basis_sha256:f.host.historyBasis(),before_sequence:1,path:'../outside'})).toString('base64url')])assert.throws(()=>f.host.readHistoryPage({cursor}),/SUPERVISOR_HISTORY_REQUEST_INVALID/);
  for(const limit of [0,25,1.5,'8'])assert.throws(()=>f.host.readHistoryPage({limit}),/SUPERVISOR_HISTORY_REQUEST_INVALID/);
});

test('a real stale writer cannot publish archive pointers or erase the newer draft',t=>{
  const f=fixture(t);seed(f,{locals:64});const stale=new SupervisorConversation({dataDir:f.dataDir,request:f.request});f.host.saveDraft({text:'较新实例的原输入',expectedRevision:0});const head=fs.readFileSync(f.host.file);
  assert.throws(()=>stale.persistLocal({question:'触发封存的第65项',answer:'仅本地'}),/SUPERVISOR_STORAGE_CONFLICT/);assert.deepEqual(fs.readFileSync(f.host.file),head);assert.equal(archiveFiles(f.host).length,0);
});

test('head publication failure leaves a complete unreferenced orphan, restart retains exact old history, explicit local retry reuses bytes',t=>{
  const f=fixture(t);seed(f,{locals:64});f.host.saveDraft({text:'崩溃前输入',expectedRevision:0});const before=fs.readFileSync(f.host.file),rename=fs.renameSync;
  fs.renameSync=(source,target)=>{if(target===f.host.file)throw Object.assign(Error('simulated head crash boundary'),{code:'EIO'});return rename(source,target);};
  try{assert.throws(()=>f.host.persistLocal({question:'第65项',answer:'新本地状态'}),/simulated head crash boundary/);}finally{fs.renameSync=rename;}
  assert.deepEqual(fs.readFileSync(f.host.file),before);assert.ok(archiveFiles(f.host).length>0);const orphanBytes=new Map(archiveFiles(f.host).map(file=>[file,fs.readFileSync(file)]));
  const reopened=new SupervisorConversation({dataDir:f.dataDir,request:f.request});assert.equal(allHistory(reopened).length,128);assert.equal(reopened.snapshot().draft.text,'崩溃前输入');assert.equal(reopened.snapshot().history.archived_records,0);
  reopened.persistLocal({question:'第65项',answer:'新本地状态'});assert.equal(allHistory(reopened).length,130);for(const [file,bytes]of orphanBytes)assert.deepEqual(fs.readFileSync(file),bytes);assert.equal(f.calls.length,0);
});

test('corrupted archived bytes fail only the requested history, preserve current draft and never issue requests',t=>{
  const f=fixture(t);seed(f,{locals:64});fill(f.host,1);const file=archiveFiles(f.host)[0],raw=fs.readFileSync(file);raw[Math.floor(raw.length/2)]^=1;fs.writeFileSync(file,raw);const before=fs.readFileSync(f.host.file);
  assert.throws(()=>allHistory(f.host),/SUPERVISOR_ARCHIVE_INVALID/);assert.deepEqual(fs.readFileSync(f.host.file),before);f.host.saveDraft({text:'坏段不吞掉当前输入',expectedRevision:0});assert.equal(f.host.snapshot().draft.text,'坏段不吞掉当前输入');assert.equal(f.calls.length,0);
});

test('archive symlinks never redirect writes or page reads to another directory',t=>{
  const f=fixture(t);seed(f,{locals:64});const external=fs.mkdtempSync(path.join(os.tmpdir(),'cirven-supervisor-external-'));t.after(()=>fs.rmSync(external,{recursive:true,force:true}));fs.symlinkSync(external,supervisorArchiveDirectory(f.host.file),'dir');const before=fs.readFileSync(f.host.file);
  assert.throws(()=>f.host.persistLocal({question:'不跟随目录链接',answer:'状态'}),/SUPERVISOR_ARCHIVE_INVALID/);assert.deepEqual(fs.readFileSync(f.host.file),before);assert.deepEqual(fs.readdirSync(external),[]);
});

test('snapshot does not read any archive body and repeated metadata ownership lookups use the verified exact-SHA cache',async t=>{
  const f=fixture(t);seed(f,{operations:128,firstId:'cached_0'});await f.ask('cached_after_128');assert.ok(f.host.snapshot().history.archived_operations>0);
  const first=f.host.lookupOperation('cached_0'),original=fs.readFileSync;let reads=0;
  fs.readFileSync=(...args)=>{reads++;return original(...args);};
  try{for(let i=0;i<100;i++){f.host.snapshot();assert.deepEqual(f.host.lookupOperation('cached_0'),first);}}finally{fs.readFileSync=original;}
  assert.equal(reads,0);assert.ok(f.host.archivedMetadata.size<=8192);
});

test('ownership metadata retains no bodies, has a worst-case 495-byte value, and old page LRU eviction does not evict live ownership',t=>{
  const f=fixture(t);seed(f,{operations:6});const state=JSON.parse(JSON.stringify(f.host.state)),segments=[];
  for(const value of state.operations){const segment=planSupervisorSegment(state.conversation_id,[{kind:'operation',value}]);writeSupervisorSegment(f.host.file,state.conversation_id,segment);segments.push(segment.ref);}
  state.schema='cirven.supervisor-conversation.v2';state.operations=[];state.archive={version:1,segments,bytes:segments.reduce((sum,row)=>sum+row.bytes,0),record_count:6,operation_count:6};
  fs.writeFileSync(f.host.file,JSON.stringify({version:2,state,sha256:digest(state)})+'\n');f.host=new SupervisorConversation({dataDir:f.dataDir,request:f.request});
  const old=f.host.lookupOperation('old_original');allHistory(f.host);const read=fs.readFileSync;let reads=0;
  fs.readFileSync=(...args)=>{reads++;return read(...args);};try{for(let i=0;i<100;i++)assert.deepEqual(f.host.lookupOperation('old_original'),old);}finally{fs.readFileSync=read;}
  assert.equal(reads,0);assert.equal(f.calls.length,0);
  const worst={operation_id:'a'.repeat(100),status:'PROCESSING',request_sha256:'f'.repeat(64),created_at:'2026-10-26T23:59:59.999999999+08:00',followup_ref:{operation_id:'b'.repeat(100),request_sha256:'e'.repeat(64)},question:'PRIVATE QUESTION',text:'PRIVATE ANSWER',raw_text:'PRIVATE WIRE'};
  const metadata=f.host.operationMetadata(worst);assert.equal(Buffer.byteLength(JSON.stringify(metadata)),495);assert.doesNotMatch(JSON.stringify(metadata),/PRIVATE|question|raw_text/);
  const maxValues=Array.from({length:8192},()=>metadata);assert.equal(maxValues.reduce((sum,row)=>sum+Buffer.byteLength(JSON.stringify(row)),0),4055040);
  assert.throws(()=>f.host.operationMetadata({...worst,created_at:worst.created_at+' '}),/SUPERVISOR_ARCHIVE_INVALID/);
});

test('legacy v1 head is not rewritten on open; its first ordinary save adds stable sequence metadata without changing request identity',async t=>{
  const f=fixture(t);await f.ask('legacy_original');const envelope=JSON.parse(fs.readFileSync(f.host.file));delete envelope.state.history_sequence;for(const row of [...envelope.state.operations,...envelope.state.local])delete row.sequence;envelope.sha256=digest(envelope.state);fs.writeFileSync(f.host.file,JSON.stringify(envelope)+'\n');const before=fs.readFileSync(f.host.file),request=f.host.lookupOperation('legacy_original').request_sha256,calls=f.calls.length;
  const reopened=new SupervisorConversation({dataDir:f.dataDir,request:f.request});assert.deepEqual(fs.readFileSync(reopened.file),before);reopened.saveDraft({text:'旧格式继续输入',expectedRevision:0});assert.equal(reopened.lookupOperation('legacy_original').request_sha256,request);assert.equal(reopened.state.operations[0].sequence,1);assert.equal(f.calls.length,calls);
});
test('a pre-sequence v1 history cursor survives its first ordinary draft save without a false history-change notice',t=>{
 const f=fixture(t);seed(f,{locals:16});const envelope=JSON.parse(fs.readFileSync(f.host.file));delete envelope.state.history_sequence;for(const row of envelope.state.local)delete row.sequence;envelope.sha256=digest(envelope.state);fs.writeFileSync(f.host.file,JSON.stringify(envelope)+'\n');
 const old=new SupervisorConversation({dataDir:f.dataDir,request:f.request}),first=old.readHistoryPage({limit:3});assert.ok(first.next_cursor);old.saveDraft({text:'旧版第一次保存草稿',expectedRevision:0});const second=old.readHistoryPage({cursor:first.next_cursor,limit:3});assert.equal(first.basis_sha256,second.basis_sha256);assert.equal(second.messages.length,6);assert.equal(f.calls.length,0);
});
test('a draft that seals a nearly full head preserves the logical cursor through cold reopen, while a real new history record rejects it',t=>{
 const f=fixture(t);seed(f,{locals:64});const envelope=JSON.parse(fs.readFileSync(f.host.file));for(const row of envelope.state.local)row.text='a'.repeat(3700);envelope.sha256=digest(envelope.state);fs.writeFileSync(f.host.file,JSON.stringify(envelope)+'\n');f.host=new SupervisorConversation({dataDir:f.dataDir,request:f.request});
 const beforeBytes=fs.statSync(f.host.file).size;assert.ok(beforeBytes>240000&&beforeBytes<MAX_SUPERVISOR_STATE_BYTES);assert.equal(f.host.state.history_basis_sha256,undefined);const page=f.host.readHistoryPage({limit:8});assert.ok(page.next_cursor);
 f.host.saveDraft({text:'问'.repeat(8000),expectedRevision:0});assert.ok(f.host.snapshot().history.archived_records>0);assert.equal(f.host.state.schema,'cirven.supervisor-conversation.v2');assert.equal(f.host.state.history_basis_sha256,page.basis_sha256);assert.equal(f.host.readHistoryPage({cursor:page.next_cursor,limit:8}).basis_sha256,page.basis_sha256);
 const reopened=new SupervisorConversation({dataDir:f.dataDir,request:f.request});assert.equal(reopened.snapshot().draft.text,'问'.repeat(8000));assert.equal(reopened.readHistoryPage({cursor:page.next_cursor,limit:8}).record_count,8);reopened.persistLocal({question:'真实新讨论',answer:'新的原记录'});assert.throws(()=>reopened.readHistoryPage({cursor:page.next_cursor,limit:8}),/SUPERVISOR_HISTORY_CHANGED/);assert.equal(f.calls.length,0);
});

test('archive limits are explicit and do not imply unlimited disk or process memory',()=>{
  assert.deepEqual(SUPERVISOR_ARCHIVE_LIMITS,{segment_bytes:131072,segment_records:32,segments:256,bytes:33554432,directory_entries:512,cache_segments:4});
});

test('first archive requires a v2 envelope/schema; forged v1 archives and mismatched versions fail before any write or request',t=>{
  const f=fixture(t);seed(f,{locals:64});assert.equal(JSON.parse(fs.readFileSync(f.host.file)).version,1);fill(f.host,1);
  const envelope=JSON.parse(fs.readFileSync(f.host.file));assert.equal(envelope.version,2);assert.equal(envelope.state.schema,'cirven.supervisor-conversation.v2');
  const before=fs.readFileSync(f.host.file);assert.equal(new SupervisorConversation({dataDir:f.dataDir,request:f.request}).readHistoryPage({limit:1}).record_count,1);assert.deepEqual(fs.readFileSync(f.host.file),before);
  for(const [version,schema]of [[1,'cirven.supervisor-conversation.v1'],[1,'cirven.supervisor-conversation.v2'],[2,'cirven.supervisor-conversation.v1']]){
    const forged=JSON.parse(before);forged.version=version;forged.state.schema=schema;forged.sha256=digest(forged.state);fs.writeFileSync(f.host.file,JSON.stringify(forged)+'\n');const bytes=fs.readFileSync(f.host.file);
    assert.throws(()=>new SupervisorConversation({dataDir:f.dataDir,request:f.request}),/SUPERVISOR_STORAGE_INVALID/);assert.deepEqual(fs.readFileSync(f.host.file),bytes);
  }
  assert.equal(f.calls.length,0);
});
