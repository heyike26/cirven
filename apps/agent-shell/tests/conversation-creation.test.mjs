import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {ConversationCreationClient} from '../src/conversation-creation.ts';
import {parseHistoryPage} from '../src/conversation-history.ts';
import {parseThreadScope,parseThreadHistory} from '../src/conversation-thread.ts';
const id='native:'+'a'.repeat(64),key='eeas-conversation-creation-v1';
const canonical=v=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
const sha=v=>createHash('sha256').update(canonical(v)).digest('hex');
const storage=()=>{const data=new Map();return {getItem:k=>data.get(k)??null,setItem:(k,v)=>data.set(k,v),data};};
const locks=()=>{let tail=Promise.resolve();return {request:(_n,_o,work)=>{const next=tail.then(work);tail=next.catch(()=>{});return next;}};};
const receipt=body=>({version:1,operation_id:body.operation_id,request_sha256:sha(body),conversation_ref:{id,revision:1},project_id:body.project_id,next_message:{project_id:body.project_id,conversation_id:'chat_new'},created_at:'2026-10-20T08:00:00+00:00',execution_started:false,messages_created:0,model_calls:0,file_write_authorized:false,automatic_input_authorized:false});
function harness(handler,disk=storage(),lock=locks(),timeout=1000){const calls=[],api={request:async(...args)=>{calls.push(args);return handler(...args);}},client=new ConversationCreationClient(api,disk,()=>{},lock,()=> 'create_original_key',timeout);return {client,calls,api,disk,lock};}

test('create journals original input before POST and retains confirmed identity until UI adoption is acknowledged',async()=>{
 let h;h=harness((_path,_method,body)=>{assert.deepEqual(JSON.parse(h.disk.getItem(key)).pending.request,body);return receipt(body);});
 const seed={kind:'native',projectId:null,id:'landing_draft'},result=await h.client.create('project_b',seed);
 assert.equal(h.calls.length,1);assert.equal(h.calls[0][4],false);assert.equal(h.client.pending.phase,'CONFIRMED');assert.deepEqual(h.client.pending.seedOwner,seed);
 await assert.rejects(()=>h.client.create(null),/CHECK_REQUIRED/);assert.equal(h.calls.length,1);
 await assert.rejects(()=>h.client.acknowledge('other',id),/CHECK_REQUIRED/);
 await h.client.acknowledge(result.operation_id,id);assert.equal(h.client.pending,null);
});
test('lost acknowledgement survives a new client and only GETs the exact original creation',async()=>{
 let saved;const h=harness((path,method,body)=>{if(method==='POST'){saved=receipt(body);throw Error('network lost');}assert.equal(path,'/api/conversation-creations/create_original_key');return saved;});
 await assert.rejects(()=>h.client.create(null));const restarted=new ConversationCreationClient(h.api,h.disk,()=>{},h.lock);
 assert.equal(restarted.pending.phase,'ATTEMPTED');const result=await restarted.inspect();assert.deepEqual(result,saved);assert.equal(restarted.pending.phase,'CONFIRMED');assert.deepEqual(h.calls.map(c=>c[1]),['POST','GET']);
});
test('a missing original result permits only an explicit retry with the same key and target',async()=>{
 let attempt=0;const h=harness((_path,method,body)=>{if(method==='GET')throw Object.assign(Error('missing'),{status:404});if(++attempt===1)throw Error('lost');return receipt(body);});
 await assert.rejects(()=>h.client.create('project_b'));await assert.rejects(()=>h.client.retry(),/CHECK_REQUIRED/);await assert.rejects(()=>h.client.inspect(),/NOT_FOUND/);assert.equal(h.client.pending.phase,'NOT_FOUND');
 await h.client.retry();assert.deepEqual(h.calls[0][2],h.calls[2][2]);assert.deepEqual(h.calls.map(c=>c[1]),['POST','GET','POST']);
});
test('storage or cross-window contention cannot admit a second creation',async()=>{
 const disk=storage(),lock=locks(),h=harness((_p,_m,body)=>receipt(body),disk,lock),other=new ConversationCreationClient(h.api,disk,()=>{},lock);
 const results=await Promise.allSettled([h.client.create(null),other.create(null)]);assert.equal(results.filter(r=>r.status==='fulfilled').length,1);assert.equal(h.calls.length,1);
 const denied=harness(()=>assert.fail('must not POST'),{getItem:()=>null,setItem:()=>{throw Error('disk');}});await assert.rejects(()=>denied.client.create(null));assert.equal(denied.calls.length,0);
 const unavailable=harness(()=>assert.fail('must not POST'),storage(),null);await assert.rejects(()=>unavailable.client.create(null),/LOCK_UNAVAILABLE/);
});
test('forged target, hash, route and execution flags never become a confirmed chat',async()=>{
 for(const change of [{project_id:'foreign'},{request_sha256:'0'.repeat(64)},{next_message:{project_id:'foreign',conversation_id:'wrong'}},{execution_started:true},{model_calls:1},{file_write_authorized:true},{conversation_ref:{id,revision:2}}]){
  const h=harness((_p,_m,body)=>({...receipt(body),...change}));await assert.rejects(()=>h.client.create(null),/MISMATCH/);assert.equal(h.client.pending.phase,'ATTEMPTED');assert.equal(h.calls.length,1);
 }
});
test('failure to clear a confirmed index keeps success recoverable and never reposts',async()=>{
 const disk=storage(),write=disk.setItem;let fail=true;disk.setItem=(k,v)=>{if(fail&&JSON.parse(v).pending===null)throw Error('disk full');write(k,v);};
 const h=harness((_p,_m,body)=>receipt(body),disk);await h.client.create(null);await assert.rejects(()=>h.client.acknowledge('create_original_key',id));assert.equal(h.client.pending.phase,'CONFIRMED');assert.equal(h.calls.length,1);fail=false;await h.client.acknowledge('create_original_key',id);assert.equal(h.client.pending,null);
});
test('late POST completion after timeout stays unknown until a readonly lookup',async()=>{
 let finish,saved;const h=harness((_p,method,body)=>method==='POST'?new Promise(resolve=>{saved=receipt(body);finish=()=>resolve(saved);}):saved,storage(),locks(),10);
 await assert.rejects(()=>h.client.create(null),/CHECK_REQUIRED/);finish();await new Promise(r=>setImmediate(r));assert.equal(h.client.pending.phase,'ATTEMPTED');await h.client.inspect();assert.deepEqual(h.calls.map(c=>c[1]),['POST','GET']);
});
test('a metadata-only chat is selectable and reopenable without a fake frame, operation or message',()=>{
 const source={kind:'conversation',project_id:null,workspace_ref:null,conversation_ref:{id,revision:1},metadata_role:'CONVERSATION_METADATA_NO_EXECUTION_OR_FILE_GRANT'};
 const raw={id,kind:'native',source,project_id:null,project_name:null,title:null,updated_at:'2026-10-20T08:00:00Z',read_only:false,organization:{revision:1,archived:false},conversation_ref:{id,revision:1},conversation_origin:{kind:'native',project_id:null,conversation_id:'chat_new'}};
 const page={version:1,items:[raw],next_cursor:null,organization_supported:true,execution_authorized:false,coverage:{model_calls:0,partial:false,complete:true,has_more:false,errors:[]}};
 const parsed=parseHistoryPage(page,'en');assert.equal(parsed.items[0].title,'Untitled chat');assert.equal(parsed.items[0].lastOperationId,undefined);
 const scope={version:1,conversation_ref:{id,revision:1},project_id:null,archived:false,title:null,metadata_source:source,sources:[{id,identity:raw.conversation_origin,original_source:null}],next_message:{project_id:null,conversation_id:'chat_new'},execution_started:false,files_moved:false,automatic_input_authorized:false,file_write_authorized:false};
 const selected=parseThreadScope(scope,id);assert.deepEqual(selected.metadata_source,source);assert.equal(selected.sources[0].original_source,null);
 assert.deepEqual(parseThreadHistory({version:1,conversation_ref:scope.conversation_ref,project_id:null,messages:[],next_cursor:null,execution_authorized:false,automatic_input_authorized:false,file_write_authorized:false,coverage:{complete:true,model_calls:0,source_partial:false}},selected).messages,[]);
 for(const change of [{project_id:'foreign'},{conversation_ref:{id,revision:2}},{file_write_authorized:true},{metadata_role:'EXECUTION'}]){
  assert.throws(()=>parseHistoryPage({...page,items:[{...raw,source:{...source,...change}}]},'en'));
  assert.throws(()=>parseThreadScope({...scope,metadata_source:{...source,...change}},id));
 }
});
