import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {OriginalSessionResumption,originalOwnerCanResume,originalOwnerNeedsRecovery} from '../src/original-connection.ts';
import {showOriginalLiveMessage} from '../src/original-live-projection.ts';
const ref={id:'owner',revision:1,sha256:'a'.repeat(64)};
const session=(extra={})=>({project_id:'p',session_ref:ref,generation:3,original_session_id:'ses_original',owner_status:'EXPIRED',connected_owner:false,configuration_current:true,observation_is_current:false,native_status:'UNKNOWN',active_operation_id:'old-unknown',pending_questions:[],pending_permissions:[{kind:'PERMISSION'}],messages:[],todo:[],children:[],last_event_seq:1,automatic_post_replay:false,...extra});
const canonical=v=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
const sha=v=>createHash('sha256').update(canonical(v)).digest('hex');
function harness({post,get,disk,timeout=1000}={}){
 disk??=(()=>{const values=new Map();return{getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v)}})();
 const calls=[],receipts=new Map();let number=0,current=session({generation:4,owner_status:'READY',connected_owner:true,observation_is_current:true,active_operation_id:null,pending_permissions:[]});
 const api={load:async(s,key,generation)=>{calls.push(['POST',key,generation]);assert.equal(JSON.parse(disk.getItem('eeas-original-resume-v1:'+canonical(['p',ref,generation]))).attempt.operationId,key);const request={operation_id:key,session_ref:ref,expected_generation:generation};const operation={id:key,kind:'LOAD',request,request_sha256:sha(request),session_ref:ref,automatic_replay:false,status:'RETURNED',loaded_generation:generation+1};receipts.set(key,operation);if(post)return post(operation);return{operation,session:current};},operation:async(s,key)=>{calls.push(['GET',key]);const value=receipts.get(key);if(!value)throw Object.assign(Error('absent'),{status:404});return get?get(value):value;},get:async()=>{calls.push(['SESSION_GET']);return current;}};
 const make=()=>new OriginalSessionResumption(api,disk,()=>{},{request:async(_k,_o,fn)=>fn()},()=>`resume-${++number}`,timeout);
 return {client:make(),make,calls,receipts,disk,api,setCurrent:s=>current=s};
}
test('explicit exact LOAD journals before one POST, keeps old UNKNOWN identity and never answers/sends',async()=>{
 const h=harness(),value=await h.client.resume(session());assert.equal(value.generation,4);assert.deepEqual(h.calls.map(x=>x[0]),['POST','GET','SESSION_GET']);
 const a=h.client.attempt(session());assert.equal(a.phase,'RETURNED');assert.equal(a.parent,'old-unknown');assert.equal(a.generation,3);assert.equal(h.client.previous(value).operationId,a.operationId);
 await assert.rejects(()=>h.client.resume(session()),/CHECK_REQUIRED/);assert.equal(h.calls.filter(c=>c[0]==='POST').length,1);
});
test('lost acknowledgement survives new client; original key GET recovers without repeating LOAD',async()=>{
 const h=harness({post:()=>{throw Error('lost');}});await assert.rejects(()=>h.client.resume(session()),/lost/);assert.equal(h.client.attempt(session()).phase,'ATTEMPTED');
 const next=h.make();await next.inspect(session());assert.equal(h.calls.filter(c=>c[0]==='POST').length,1);assert.equal(next.attempt(session()).phase,'RETURNED');
});
test('HTTP 404 and ambiguous 409 never authorize another POST; only identity-verified FAILED permits explicit new connection attempt',async()=>{
 const h=harness({post:o=>{o.status='FAILED';throw Object.assign(Error('rejected'),{status:409});}});await assert.rejects(()=>h.client.resume(session()));await assert.rejects(()=>h.client.resume(session()),/CHECK_REQUIRED/);await assert.rejects(()=>h.client.inspect(session()),/LOAD_FAILED/);assert.equal(h.client.attempt(session()).phase,'FAILED');
 await assert.rejects(()=>h.client.resume(session()));assert.equal(h.calls.filter(c=>c[0]==='POST').length,2);assert.deepEqual(h.client.attempt(session()).previousOperationIds,['resume-1']);
 const n=harness({post:()=>{throw Error('lost before commit');}});await assert.rejects(()=>n.client.resume(session()));n.receipts.clear();await assert.rejects(()=>n.make().inspect(session()));await assert.rejects(()=>n.client.resume(session()),/CHECK_REQUIRED/);assert.equal(n.calls.filter(c=>c[0]==='POST').length,1);
});
test('bad receipt, foreign owner, another native sid or later generation cannot be adopted',async()=>{
 for(const patch of [{request_sha256:'0'.repeat(64)},{kind:'MESSAGE'},{loaded_generation:5},{session_ref:{...ref,id:'other'}},{automatic_replay:true},{request:{operation_id:'foreign'}}]){const h=harness({get:o=>({...o,...patch})});await assert.rejects(()=>h.client.resume(session()),/MISMATCH/);assert.equal(h.client.attempt(session()).phase,'ATTEMPTED');}
 for(const patch of [{generation:5},{original_session_id:'ses_other'},{project_id:'other'},{session_ref:{...ref,id:'other'}}]){const h=harness();h.setCurrent(session({generation:4,...patch}));await assert.rejects(()=>h.client.resume(session()));assert.equal(h.client.attempt(session()).phase,'ATTEMPTED');}
});
test('hard expired pending/UNKNOWN supports ONLY explicit resume; live/stale config and navigation prevent it',async()=>{
 assert.equal(originalOwnerCanResume(session()),true);
 for(const patch of [{owner_status:'READY',connected_owner:true},{configuration_current:false},{original_session_id:null},{generation:0},{automatic_post_replay:true}]){const h=harness();await assert.rejects(()=>h.client.resume(session(patch)));assert.equal(h.calls.length,0);}
 const h=harness();await assert.rejects(()=>h.client.resume(session(),()=>false),/NAVIGATION_CHANGED/);assert.equal(h.calls.length,0);
});
test('storage failure prevents POST; lost cleanup retains original key',async()=>{
 const h=harness({disk:{getItem:()=>null,setItem:()=>{throw Error('full')}}});await assert.rejects(()=>h.client.resume(session()),/full/);assert.equal(h.calls.length,0);
 const n=harness(),write=n.disk.setItem;n.disk.setItem=(k,v)=>{if(JSON.parse(v).attempt.phase==='RETURNED')throw Error('full');write(k,v);};await assert.rejects(()=>n.client.resume(session()),/full/);assert.equal(n.client.attempt(session()).phase,'ATTEMPTED');n.disk.setItem=write;await n.make().inspect(session());assert.equal(n.calls.filter(c=>c[0]==='POST').length,1);
});
test('bounded wait and same-owner exclusion keep one original attempt for later inspection',async()=>{
 let release;const h=harness({post:()=>new Promise(r=>release=r),timeout:15});const waiting=h.client.resume(session());await new Promise(r=>setImmediate(r));await assert.rejects(()=>h.client.resume(session()),/BUSY/);await assert.rejects(()=>waiting,/CHECK_REQUIRED/);release();await h.make().inspect(session());assert.equal(h.calls.filter(c=>c[0]==='POST').length,1);
});
test('old tool-only failure is absent only when its exact parent already carries the saved failure',()=>{
 const msg={info:{id:'tool-old',role:'assistant',parentID:'old-user',error:{code:'Cancelled'}},parts:[{type:'tool',state:{status:'error'}}]},row={role:'user',native_message_id:'old-user',source:{kind:'original',project_id:'p',session_id:'owner'},status:'CANCELLED'};
 assert.equal(showOriginalLiveMessage(msg,session(),[row]),false);
 for(const change of [{source:{...row.source,project_id:'elsewhere'}},{source:{...row.source,session_id:'other'}},{native_message_id:'other-user'},{role:'assistant'},{status:'RETURNED'}])assert.equal(showOriginalLiveMessage(msg,session(),[{...row,...change}]),true);
 for(const extra of [{type:'text',text:'Actual error explanation stays'},{type:'file',filename:'result.txt'}])assert.equal(showOriginalLiveMessage({...msg,parts:[...msg.parts,extra]},session(),[row]),true);
 assert.equal(showOriginalLiveMessage({...msg,parts:[{type:'tool',state:{status:'running'}}]},session({owner_status:'READY',connected_owner:true,observation_is_current:true,active_operation_id:'original-parent'}),[{...row,operation_id:'original-parent'}]),true);
 assert.equal(showOriginalLiveMessage(msg,session(),[]),true);
});

test('failed LOAD advancing the backend preserves the old intent origin, and requires its original receipt before a new POST',async()=>{
 const h=harness({post:o=>{if(o.request.expected_generation===3){o.status='FAILED';throw Error('actual-start-failed-after-generation-advance');}}});
 await assert.rejects(()=>h.client.resume(session()));
 const failedOwner=session({generation:4,owner_status:'BLOCKED',connected_owner:false,active_operation_id:null,pending_permissions:[]});
 assert.equal(h.make().previous(failedOwner).phase,'ATTEMPTED');
 await assert.rejects(()=>h.make().resume(failedOwner),/CHECK_REQUIRED/);
 assert.equal(h.calls.filter(c=>c[0]==='POST').length,1);
 await assert.rejects(()=>h.make().inspect(session()),/LOAD_FAILED/);
 assert.equal(h.make().previous(failedOwner).phase,'FAILED');
 const ready=session({generation:5,owner_status:'READY',connected_owner:true,observation_is_current:true,active_operation_id:null,pending_permissions:[]});
 h.setCurrent(ready);const next=h.make();assert.equal((await next.resume(failedOwner)).generation,5);
 const a=next.previous(ready);assert.equal(a.generation,4);assert.equal(a.originGeneration,3);assert.equal(a.parent,'old-unknown');assert.equal(a.phase,'RETURNED');
 assert.equal(next.attempt(session()).phase,'FAILED');
 assert.deepEqual(h.calls.filter(c=>c[0]==='POST').map(c=>c[2]),[3,4]);
});

test('an expired recovered owner carries unrestored intent, while a different active operation has its own origin',async()=>{
 for(const active of [null,'old-unknown','different-user-turn']){
  const h=harness();await h.client.resume(session());
  const expired=session({generation:4,active_operation_id:active,pending_permissions:[]});
  h.setCurrent(session({generation:5,owner_status:'READY',connected_owner:true,observation_is_current:true,active_operation_id:null,pending_permissions:[]}));
  await h.make().resume(expired);const a=h.make().attempt(expired);
  assert.equal(a.originGeneration,active==='different-user-turn'?4:3);
  assert.equal(a.parent,active??'old-unknown');
 }
 const h=harness();await h.client.resume(session());const a=h.client.attempt(session());h.client.restored(a);
 const expired=session({generation:4,active_operation_id:null,pending_permissions:[]});
 assert.equal(h.make().previous(expired),undefined);assert.equal(originalOwnerNeedsRecovery(expired),false);
});

test('idle disconnected history needs no unfinished-work warning; failed, unknown and unrestored attempts remain actionable',()=>{
 const idle=session({active_operation_id:null,pending_permissions:[]});
 assert.equal(originalOwnerNeedsRecovery(idle),false);
 for(const owner_status of ['EXPIRED','DISCONNECTED','BLOCKED'])assert.equal(originalOwnerCanResume({...idle,owner_status}),true);
 assert.equal(originalOwnerCanResume({...idle,owner_status:'BLOCKED_PROCESS_STOP_UNCONFIRMED'}),false);
 for(const phase of ['FAILED','ATTEMPTED','RETURNED'])assert.equal(originalOwnerNeedsRecovery(idle,{phase,parent:'old',requestRestored:false}),true);
 assert.equal(originalOwnerNeedsRecovery(idle,{phase:'RETURNED',parent:'old',requestRestored:true}),false);
});

test('actual R17 expired running-tool DTO never advertises live work after its exact saved parent became UNKNOWN',()=>{
 const dto=JSON.parse(readFileSync(new URL('./fixtures/native-r17-expired-tool-projection.json',import.meta.url),'utf8'));
 assert.equal(showOriginalLiveMessage(dto.message,dto.owner,[dto.row]),false);
 const active={...dto.owner,owner_status:'READY',connected_owner:true,observation_is_current:true,active_operation_id:dto.row.operation_id};
 assert.equal(showOriginalLiveMessage(dto.message,active,[dto.row]),true);
 assert.equal(showOriginalLiveMessage(dto.message,{...active,active_operation_id:null},[dto.row]),false);
 assert.equal(showOriginalLiveMessage(dto.message,{...dto.owner,owner_status:'BLOCKED',active_operation_id:null},[dto.row]),false);
 assert.equal(showOriginalLiveMessage(dto.message,{...dto.owner,owner_status:'BLOCKED'},[dto.row]),true);
 for(const owner of [{...dto.owner,owner_status:'BLOCKED_PROCESS_STOP_UNCONFIRMED'},{...active,observation_is_current:false,active_operation_id:null}])assert.equal(showOriginalLiveMessage(dto.message,owner,[dto.row]),true);
 for(const patch of [{source:{...dto.row.source,project_id:'foreign'}},{source:{...dto.row.source,session_id:'foreign'}},{native_message_id:'unmatched'},{status:'RETURNED'}])assert.equal(showOriginalLiveMessage(dto.message,dto.owner,[{...dto.row,...patch}]),true);
 for(const part of [{type:'text',text:'Authored answer stays'},{type:'file',filename:'result.txt'}])assert.equal(showOriginalLiveMessage({...dto.message,parts:[...dto.message.parts,part]},dto.owner,[dto.row]),true);
});
