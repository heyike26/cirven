// Client boundary tests; injected transport failures are not backend, browser,
// model or Windows qualification. Real domain/SQLite tests live in tests/.
import test from 'node:test';
import assert from 'node:assert/strict';
import {ContextGraphClient} from '../src/context-graph.ts';
import {ApiError} from '../src/api.ts';

function storage(){const values=new Map();return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};}
const receipt=(id,kind)=>({version:1,operation_id:id,operation_kind:kind,request_sha256:'a'.repeat(64)});
test('unknown historical event scan recovers only its original receipt without replaying the scan',async()=>{
 const disk=storage(),calls=[];let original;
 const transport={request:async(route,method='GET',body,_signal,retry)=>{calls.push({route,method,body,retry});if(body){original=body.operation_id;throw Error('lost scan response');}return receipt(original,'message_locations_scan');}};
 const first=new ContextGraphClient(transport,disk),cid='original:'+'a'.repeat(64);
 await assert.rejects(first.scanHistoryEvents(cid,cid,'opaque_original_cursor'));
 assert.equal(calls[0].body.cursor,'opaque_original_cursor');assert.equal(calls[0].retry,false);
 await new ContextGraphClient(transport,disk).recover();assert.equal(calls.length,2);assert.equal(calls[1].method,'GET');
});
test('discussion entry navigation explicitly preserves source placement and never sets a context preference',async()=>{
 const calls=[],transport={request:async(route,method,body)=>{calls.push(body);return receipt(body.operation_id,'conversation_link');}};
 const client=new ContextGraphClient(transport,storage()),ref={id:'original:'+'a'.repeat(64),revision:4};
 await client.linkConversation('target',ref,undefined,'ACTIVE',undefined,true,true);
 assert.equal(calls[0].navigation_only,true);assert.equal(calls[0].allow_reference_to_other_project,true);
 assert.equal(calls[0].conversation_ref,ref);assert.equal('preference_expected_revision' in calls[0],false);
});
test('a reviewed foreign fragment preserves original project, two revisions and unknown-operation recovery',async()=>{
 const disk=storage(),calls=[];let original;
 const transport={request:async(route,method='GET',body,_signal,retry)=>{calls.push({route,method,body,retry});if(body){original=body.operation_id;throw Error('lost acknowledgement');}return receipt(original,'read_grant');}};
 const first=new ContextGraphClient(transport,disk);
 const preview={scope_id:'target',source_ref:{kind:'NOTE_FRAGMENT',project_id:'original',object_id:'b'.repeat(64),sha256:'c'.repeat(64),offset:3,length:20},conversation_id:null,expected_revision:2,expected_grant_revision:4,review_sha256:'d'.repeat(64),restore_file_exclusions:[]};
 await assert.rejects(first.adoptReadGrant(preview));
 assert.equal(calls[0].body.source.project_id,'original');assert.equal(calls[0].body.expected_revision,2);assert.equal(calls[0].body.expected_grant_revision,4);
 assert.equal(calls[0].body.review_sha256,preview.review_sha256);assert.equal(calls[0].retry,false);
 assert.equal(disk.getItem(first.storageKey).includes('original'),false);
 await new ContextGraphClient(transport,disk).recover();assert.equal(calls.length,2);assert.equal(calls[1].method,'GET');
});
test('a damaged local operation journal preserves its bytes and blocks writes without crashing construction',async()=>{
  const disk=storage();disk.setItem('eeas.context-pending.v1','{damaged original record');let calls=0;
  const controller=new ContextGraphClient({request:async()=>{calls++;}},disk);
  assert.equal(controller.unresolved,true);assert.equal(controller.pending,null);
  await assert.rejects(controller.note('scope','Title','Text'),/CONTEXT_OPERATION_UNRESOLVED/);
  await assert.rejects(controller.recover(),/LOCAL_CONTEXT_RECORD_UNREADABLE/);
  assert.equal(disk.getItem(controller.storageKey),'{damaged original record');assert.equal(calls,0);
});
test('an unknown context write survives a new client and recovery only reads the same original operation',async()=>{
  const disk=storage(),calls=[];let original;
  const client={request:async(route,method='GET',body,_signal,retry)=>{calls.push({route,method,body,retry});if(body){original=body.operation_id;throw Error('lost response');}return receipt(original,'note');}};
  const first=new ContextGraphClient(client,disk);
  await assert.rejects(first.note('scope','Manual note','Keep my exact manual wording.'));
  assert.equal(first.pending.operation_id,original);assert.equal(calls[0].retry,false);
  const restored=new ContextGraphClient(client,disk);
  await assert.rejects(restored.note('scope','Another note','A different note.'),/CONTEXT_OPERATION_UNRESOLVED/);
  assert.equal(calls.length,1);
  await restored.recover();assert.equal(calls.length,2);assert.equal(calls[1].method,'GET');assert.ok(calls[1].route.endsWith(original));assert.equal(restored.pending,null);
});
test('a definite CAS rejection preserves backend authority without leaving an unknown write',async()=>{
  const disk=storage(),client={request:async()=>{throw new ApiError(409,'CONFLICT','A newer manual decision exists');}};
  const controller=new ContextGraphClient(client,disk);
  await assert.rejects(controller.saveScope({id:'project',project_id:'pid',kind:'PROJECT',revision:1,label:'Old label'}));assert.equal(controller.pending,null);
});
test('a mismatched operation recovery cannot release the saved pending request',async()=>{
  const disk=storage();disk.setItem('eeas.context-pending.v1',JSON.stringify({operation_id:'original',kind:'association'}));
  const controller=new ContextGraphClient({request:async()=>receipt('other','association')},disk);
  await assert.rejects(controller.recover(),/identity differs/);assert.equal(controller.pending.operation_id,'original');
});
test('a lost atomic discussion-scope preference change remains the same journaled operation and recovery sends no new link',async()=>{
 const disk=storage(),calls=[];let original;
 const transport={request:async(route,method='GET',body,_signal,retry)=>{calls.push({route,method,body,retry});
  if(body){original=body.operation_id;throw Error('Original context-link acknowledgement lost');}
  return receipt(original,'conversation_link');}};
 const first=new ContextGraphClient(transport,disk),reference={id:'native:'+'a'.repeat(64),revision:4};
 await assert.rejects(first.linkConversation('topic',reference,{revision:2},'ACTIVE',3));
 assert.equal(calls[0].body.preference_expected_revision,3);assert.equal(calls[0].body.expected_revision,2);
 assert.equal(calls[0].body.conversation_ref,reference);assert.equal(calls[0].retry,false);
 const restored=new ContextGraphClient(transport,disk);await restored.recover();
 assert.equal(calls.length,2);assert.equal(calls[1].method,'GET');assert.ok(calls[1].route.endsWith(original));
 assert.equal(restored.pending,null);
});
test('two existing panel instances cannot overwrite an unresolved operation journal',async()=>{
  const disk=storage(),calls=[];let release;
  const transport={request:(route,method,body)=>{calls.push(body);return new Promise((_resolve,reject)=>{release=()=>reject(Error('lost response'));});}};
  const one=new ContextGraphClient(transport,disk),two=new ContextGraphClient(transport,disk);
  const writing=assert.rejects(one.note('scope','First','Keep this text.'));
  await new Promise(resolve=>setImmediate(resolve));
  const original=one.pending.operation_id;
  const second=assert.rejects(two.note('scope','Second','Do not overwrite another request.'),/CONTEXT_OPERATION_UNRESOLVED/);
  assert.equal(JSON.parse(disk.getItem(one.storageKey)).operation_id,original);assert.equal(calls.length,1);release();await writing;await second;
});
