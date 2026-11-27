import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import ts from 'typescript';
const code=ts.transpileModule(await readFile(new URL('../src/conversation-organization.ts',import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
const {ConversationOrganizationClient}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
const canonical=v=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
const sha=v=>createHash('sha256').update(canonical(v)).digest('hex');
const item={id:'native:'+'a'.repeat(64),kind:'native',projectId:null,title:'原讨论与草稿',source:{kind:'native',project_id:null,request_project_id:null,conversation_id:'chat_a',last_operation_id:'op_a',request_sha256:'b'.repeat(64),record_origin:'CURRENT_HOST',provider_profile_ref:null,workspace_ref:null},organization:{revision:0,archived:false}};
const storage=()=>{let raw=null;return{getItem:()=>raw,setItem:(_k,v)=>{raw=v;},raw:()=>raw};};
const locks=()=>{let tail=Promise.resolve();return{request:(_name,_options,fn)=>{const next=tail.then(fn);tail=next.catch(()=>{});return next;}};};
const receipt=(body,id=item.id)=>({version:1,operation_id:body.operation_id,request_sha256:sha({conversation_id:id,...body}),conversation_id:id,action:body.action,organization:{revision:body.expected_revision+1,archived:body.action==='archive'},project_id:body.source.project_id,execution_authorized:false,transcript_changed:false,files_changed:false,updated_at:'2026-10-19T10:00:00+00:00'});
function harness(fn,disk=storage(),lock=locks()){const calls=[],api={request:async(...args)=>{calls.push(args);return fn(...args);}},client=new ConversationOrganizationClient(api,disk,()=>{},lock,()=> 'test_operation');return{client,calls,disk,api,lock};}

test('archive confirms exact original discussion and preserves source; POST never automatically replays',async()=>{
 const h=harness(async(_path,_method,body)=>receipt(body)),before=structuredClone(item),r=await h.client.change(item,'archive');assert.equal(r.organization.archived,true);assert.deepEqual(item,before);assert.equal(h.calls.length,1);assert.equal(h.calls[0][1],'POST');assert.equal(h.calls[0][4],false);assert.equal(h.client.pending,null);assert.equal(JSON.parse(h.disk.raw()).pending,null);
});
test('lost committed response survives reconstruction and recovers by original GET without another POST',async()=>{
 let saved;const h=harness(async(_path,method,body)=>{if(method==='POST'){saved=receipt(body);throw Error('network response lost');}return saved;});await assert.rejects(h.client.change(item,'archive'));assert.ok(h.client.pending);const restored=new ConversationOrganizationClient(h.api,h.disk,()=>{},h.lock);assert.ok(restored.pending);assert.equal(h.calls.length,1);const r=await restored.inspect();assert.deepEqual(r,saved);assert.equal(h.calls.filter(c=>c[1]==='POST').length,1);assert.equal(restored.pending,null);
});
test('not found allows only explicit same-operation retry, with identical body and no new identity',async()=>{
 let attempts=0;const h=harness(async(_path,method,body)=>{if(method==='GET')throw {status:404};if(++attempts===1)throw Error('timeout before commit');return receipt(body);});await assert.rejects(h.client.change(item,'archive'));await assert.rejects(h.client.retry());assert.equal(attempts,1);await assert.rejects(h.client.inspect(),/ORGANIZATION_NOT_FOUND/);assert.equal(h.client.pending.phase,'NOT_FOUND');await h.client.retry();const writes=h.calls.filter(c=>c[1]==='POST');assert.equal(writes.length,2);assert.deepEqual(writes[0][2],writes[1][2]);assert.equal(h.client.pending,null);
});
test('a definitive stale revision retains the conversation and releases only this rejected intent',async()=>{
 const h=harness(async()=>{throw {status:409};});await assert.rejects(h.client.change(item,'archive'),/ORGANIZATION_REFRESH_REQUIRED/);assert.equal(h.client.pending,null);assert.equal(item.organization.revision,0);
});
test('wrong receipt scope, changed revision, or file authority stays pending',async()=>{
 for(const corrupt of [r=>({...r,conversation_id:'native:'+'c'.repeat(64)}),r=>({...r,project_id:'foreign_project'}),r=>({...r,organization:{revision:8,archived:true}}),r=>({...r,files_changed:true})]){
  const h=harness(async(_p,_m,body)=>corrupt(receipt(body)));await assert.rejects(h.client.change(item,'archive'),/ORGANIZATION_RECEIPT_MISMATCH/);assert.ok(h.client.pending);assert.equal(h.calls.length,1);
 }
});
test('journal failure and missing safe lock prevent any write request',async()=>{
 const disk={getItem:()=>null,setItem:()=>{throw Error('disk full');}},h=harness(async()=>{throw Error('MUST_NOT_CALL');},disk);await assert.rejects(h.client.change(item,'archive'));assert.equal(h.calls.length,0);
 const noLock=harness(async()=>{throw Error('MUST_NOT_CALL');},storage(),null);await assert.rejects(noLock.client.change(item,'archive'),/ORGANIZATION_LOCK_UNAVAILABLE/);assert.equal(noLock.calls.length,0);
});
test('confirmed mutation remains confirmed when local recovery cleanup fails',async()=>{
 const base=storage();let writes=0;const disk={getItem:base.getItem,setItem:(k,v)=>{if(++writes===2)throw Error('disk full after commit');base.setItem(k,v);}},h=harness(async(_p,_m,body)=>receipt(body),disk);const result=await h.client.change(item,'archive');assert.equal(result.organization.archived,true);assert.equal(h.client.error,'ORGANIZATION_RECOVERY_SAVE_FAILED');assert.ok(h.client.pending);assert.equal(h.calls.length,1);
});
test('another window cannot overwrite an unresolved intent or dispatch a different target',async()=>{
 const disk=storage(),lock=locks(),first=harness(async()=>{throw Error('unknown');},disk,lock);await assert.rejects(first.client.change(item,'archive'));const second=harness(async()=>{throw Error('MUST_NOT_DISPATCH');},disk,lock);await assert.rejects(second.client.change({...item,id:'native:'+'d'.repeat(64)},'archive'),/ORGANIZATION_CHECK_REQUIRED/);assert.equal(second.calls.length,0);assert.equal(second.client.pending.conversationId,item.id);
});
test('damaged recovery data is retained and never triggers a request',async()=>{
 const disk=storage();disk.setItem('unused','{"version":1,"pending":{"forged":true}}');const before=disk.raw(),h=harness(async()=>{throw Error('MUST_NOT_CALL');},disk);assert.equal(h.client.error,'ORGANIZATION_STORAGE_UNAVAILABLE');await assert.rejects(h.client.change(item,'archive'));assert.equal(h.calls.length,0);assert.equal(disk.raw(),before);
});
test('an unresponsive bootstrap or transport releases the UI lock and preserves one recoverable intent',async()=>{
 const calls=[],api={request:async(...args)=>{calls.push(args);return new Promise(()=>{});}},disk=storage(),client=new ConversationOrganizationClient(api,disk,()=>{},locks(),()=> 'bounded_operation',5);await assert.rejects(client.change(item,'archive'),/ORGANIZATION_CHECK_REQUIRED/);assert.equal(client.busy,false);assert.ok(client.pending);assert.equal(calls.length,1);assert.equal(calls[0][3].aborted,true);await assert.rejects(client.inspect(),/ORGANIZATION_CHECK_REQUIRED/);assert.equal(client.busy,false);assert.equal(calls.filter(call=>call[1]==='POST').length,1);assert.ok(client.pending);
});

test('archive after a project move confirms the organization project without borrowing its old execution scope',async()=>{
 const moved={...item,projectId:'target_project',organization:{revision:2,archived:false},conversationRef:{id:item.id,revision:2}};
 const h=harness(async(_p,_m,body)=>({...receipt(body),project_id:'target_project'}));const result=await h.client.change(moved,'archive');assert.equal(result.project_id,'target_project');assert.equal(h.calls[0][2].source.project_id,null);assert.equal(h.calls[0][2].expected_revision,2);
});

const receiptV2=(body,projectId=null,archived=false)=>({...receipt(body),version:2,project_id:body.action==='move'?body.target_project_id:projectId,organization:{revision:body.expected_revision+1,archived},source_project_id:body.source.project_id,conversation_removed:body.action==='delete',execution_records_retained:true,project_files_retained:true,existing_execution_scope_changed:false,execution_stop_requested:false});
test('move and deletion bind target, exact scope, archived state and source identity without mutating the old item',async()=>{
 for(const archived of [false,true]){
  const row={...item,projectId:'assigned',organization:{revision:3,archived}},original=structuredClone(row);
  const move=harness(async(_p,_m,body)=>receiptV2(body,'assigned',archived));const r=await move.client.change(row,{action:'move',targetProjectId:null});assert.equal(r.project_id,null);assert.equal(r.organization.archived,archived);assert.equal(move.calls[0][2].target_project_id,null);assert.ok(!Object.hasOwn(move.calls[0][2],'delete_scope'));
  const remove=harness(async(_p,_m,body)=>receiptV2(body,'assigned',archived));await remove.client.change(row,{action:'delete',confirmed:true});assert.equal(remove.calls[0][2].delete_scope,'CONVERSATION_ONLY_KEEP_EXECUTION_AND_FILES');assert.ok(!Object.hasOwn(remove.calls[0][2],'target_project_id'));assert.deepEqual(row,original);
 }
});
test('invalid move or unconfirmed deletion cannot create a journal or request',async()=>{
 for(const intent of [{action:'move',targetProjectId:null},{action:'move',targetProjectId:'../bad'},{action:'move'},{action:'delete'},{action:'delete',confirmed:false}]){
  const h=harness(async()=>{throw Error('MUST_NOT_CALL');});await assert.rejects(h.client.change(item,intent));assert.equal(h.calls.length,0);assert.equal(h.disk.raw(),null);
 }
});
test('v2 safety fields, archive state, target and version must all match before confirmation',async()=>{
 for(const corrupt of [r=>({...r,version:1}),r=>({...r,source_project_id:'foreign'}),r=>({...r,conversation_removed:true}),r=>({...r,execution_records_retained:false}),r=>({...r,project_files_retained:false}),r=>({...r,existing_execution_scope_changed:true}),r=>({...r,execution_stop_requested:true}),r=>({...r,organization:{...r.organization,archived:true}})]){
  const h=harness(async(_p,_m,body)=>corrupt(receiptV2(body)));await assert.rejects(h.client.change(item,{action:'move',targetProjectId:'b'}),/ORGANIZATION_RECEIPT_MISMATCH/);assert.ok(h.client.pending);assert.equal(h.calls.length,1);
 }
});
test('lost move/delete ACK survives reconstruction and resolves only the frozen original operation',async()=>{
 for(const intent of [{action:'move',targetProjectId:'b'},{action:'delete',confirmed:true}]){
  let committed;const h=harness(async(_p,method,body)=>{if(method==='POST'){committed=receiptV2(body);throw Error('lost ACK');}return committed;});await assert.rejects(h.client.change(item,intent));const restored=new ConversationOrganizationClient(h.api,h.disk,()=>{},h.lock);assert.equal(restored.error,'');const result=await restored.inspect();assert.deepEqual(result,committed);assert.equal(h.calls.filter(row=>row[1]==='POST').length,1);assert.equal(restored.pending,null);
 }
});
test('a damaged v2 target or missing explicit delete scope leaves the saved journal intact and never retries',async()=>{
 for(const intent of [{action:'move',targetProjectId:'b'},{action:'delete',confirmed:true}]){
  const h=harness(async()=>{throw Error('unknown');});await assert.rejects(h.client.change(item,intent));const saved=JSON.parse(h.disk.raw());if(intent.action==='move')saved.pending.request.target_project_id='../bad';else delete saved.pending.request.delete_scope;h.disk.setItem('unused',JSON.stringify(saved));const raw=h.disk.raw(),restored=new ConversationOrganizationClient(h.api,h.disk,()=>{},h.lock);assert.equal(restored.error,'ORGANIZATION_STORAGE_UNAVAILABLE');await assert.rejects(restored.retry());assert.equal(h.calls.length,1);assert.equal(h.disk.raw(),raw);
 }
});
