import test from 'node:test';
import assert from 'node:assert/strict';
import {OriginalTaskStops,taskStopStorageKey} from '../src/original-task-stop.ts';
import {AgentSessionsApi} from '../src/agent-sessions.ts';
import {ApiClient} from '../src/api.ts';
const ref={id:'test_owner',revision:1,sha256:'a'.repeat(64)};
const session=(patch={})=>({project_id:'test_project',session_ref:ref,generation:1,owner_status:'READY',connected_owner:true,configuration_current:true,observation_is_current:true,native_status:'busy',active_operation_id:'task_one',automatic_post_replay:false,messages:[],todo:[],children:[],pending_questions:[],pending_permissions:[],last_event_seq:1,...patch});
const storage=()=>{const values=new Map();return {values,getItem:k=>values.get(k)??null,setItem:(k,v)=>{values.set(k,v);}};};
const receipt=(id,status='RETURNED',patch={})=>({id,status,request_sha256:'b'.repeat(64),automatic_replay:false,...patch});
function scene(overrides={}){
 const calls=[],s=session(),store=storage();
 const client={interruptTask:async(s,target,key)=>{calls.push(['POST',target,key]);return receipt(key,'RETURNED',{original_abort_acknowledged:true});},operation:async(s,key)=>{calls.push(['GET',key]);return receipt(key,key==='task_one'?'CANCELLED':'RETURNED',{original_abort_acknowledged:true});},get:async()=>{calls.push(['SESSION']);return session({active_operation_id:null});},...overrides};
 return {s,store,client,calls,stops:new OriginalTaskStops(store,client)};
}
test('stop persists the exact target intent before one POST and repeated requests never POST again',async()=>{
 const q=scene();await q.stops.stop(q.s,'task_one');const a=q.stops.read(q.s,'task_one');assert.equal(a.acknowledged,true);assert.equal(q.calls.length,1);assert.equal(q.calls[0][1],'task_one');
 await new OriginalTaskStops(q.store,q.client).stop(q.s,'task_one');assert.equal(q.calls.length,1);
 const next=await q.stops.inspect(q.s,'task_one');assert.equal(next.active_operation_id,null);assert.equal(q.stops.read(q.s,'task_one').targetStatus,'CANCELLED');assert.deepEqual(q.calls.slice(1),[['GET',a.key],['SESSION'],['GET','task_one']]);
});
test('unknown result survives recreation and recovery only reads the original stop and task',async()=>{
 const q=scene({interruptTask:async()=>{throw Error('possible dispatch');}});
 await assert.rejects(q.stops.stop(q.s,'task_one'));const a=q.stops.read(q.s,'task_one');assert.equal(a.status,'UNKNOWN_OUTCOME');
 const resumed=new OriginalTaskStops(q.store,{...q.client,interruptTask:async()=>{throw Error('must not post');}});await resumed.stop(q.s,'task_one');await resumed.inspect(q.s,'task_one');assert.equal(q.stops.read(q.s,'task_one').key,a.key);
 assert.deepEqual(q.calls,[['GET',a.key],['SESSION'],['GET','task_one']]);
});
test('failed intent persistence or malformed existing state prevents the stop',async()=>{
 for(const raw of [null,'bad json',JSON.stringify({version:1,target:'another task'})]){
  const q=scene();if(raw!==null)q.store.values.set(taskStopStorageKey(q.s,'task_one'),raw);else q.store.setItem=()=>{throw Error('disk full');};
  await assert.rejects(q.stops.stop(q.s,'task_one'));assert.equal(q.calls.length,0);
 }
});
test('session observation settles the exact native task before its terminal receipt is read',async()=>{
 let observed=false;const q=scene({get:async()=>{observed=true;return session({active_operation_id:null});},operation:async(s,key)=>receipt(key,key==='task_one'?(observed?'CANCELLED':'ACCEPTED'):'RETURNED',{original_abort_acknowledged:true})});
 await q.stops.stop(q.s,'task_one');await q.stops.inspect(q.s,'task_one');assert.equal(q.stops.read(q.s,'task_one').targetStatus,'CANCELLED');
});
test('concurrent clicks and controllers cannot dispatch a second stop while the first is unresolved',async()=>{
 let release;const wait=new Promise(done=>release=done),q=scene({interruptTask:async()=>{await wait;throw Error('unknown');}});
 const first=q.stops.stop(q.s,'task_one');await assert.rejects(new OriginalTaskStops(q.store,q.client).stop(q.s,'task_one'));release();await assert.rejects(first);assert.equal(q.stops.read(q.s,'task_one').status,'UNKNOWN_OUTCOME');
});
test('stale target and disconnected owners cannot stop a different live task',async()=>{
 const q=scene();for(const s of [session({active_operation_id:'task_two'}),session({generation:0}),session({connected_owner:false})])await assert.rejects(q.stops.stop(s,'task_one'));assert.equal(q.calls.length,0);
 // Wire order does not alter the persistent identity.
 assert.equal(taskStopStorageKey(q.s,'task_one'),taskStopStorageKey(session({session_ref:{sha256:ref.sha256,revision:1,id:ref.id}}),'task_one'));
});
test('production client binds the exact task at the backend without a broad session abort',async()=>{
 const calls=[];const client=new AgentSessionsApi(new ApiClient(async(url,init)=>{if(String(url).endsWith('/bootstrap'))return Response.json({csrf_token:'test'});calls.push({url,body:JSON.parse(init.body)});return Response.json(receipt('stop_key'));}));
 await client.interruptTask(session(),'task_one','stop_key');assert.deepEqual(calls,[{url:'/api/projects/test_project/agent-sessions/test_owner/interrupt',body:{operation_id:'stop_key',session_ref:ref,generation:1,expected_active_operation_id:'task_one'}}]);
 await assert.rejects(async()=>client.interruptTask(session({active_operation_id:'task_two'}),'task_one','other'));assert.equal(calls.length,1);
});
