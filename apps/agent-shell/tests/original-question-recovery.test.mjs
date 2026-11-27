import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {ControlQuestionDraft,controlDraftScope,controlKey} from '../src/original-pending-controls.ts';
import {discussionDraftKey} from '../src/discussion-drafts.ts';
import {readQuestionRecoveries,assertQuestionRecoveryCurrent,questionRecoveryText,appendQuestionRecovery,markQuestionRecovered} from '../src/original-question-recovery.ts';

const fixture=JSON.parse(fs.readFileSync(new URL('./fixtures/native-r20-unsent-question-draft.json',import.meta.url),'utf8'));
function setup(){
 const values=new Map([[fixture.source.key,fixture.source.raw]]);
 const storage={values,get length(){return values.size;},key:index=>[...values.keys()][index]??null,getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};
 const session=structuredClone(fixture.session);
 return {storage,session,get row(){return readQuestionRecoveries(storage,session,[])[0];}};
}

test('actual Windows 7→8 unsubmitted legacy answer is recoverable without recreating its old question',()=>{
 const {storage,session,row}=setup();assert(row);assert.equal(row.identity.generation,7);assert.equal(session.generation,8);
 assert.deepEqual(row.draft,fixture.source.draft);assert.deepEqual(row.questions,[]);assert.equal(row.raw,fixture.source.raw);
 const text=questionRecoveryText(row,fixture.operation,session,fixture.conversation,'zh');
 assert(text.includes(fixture.operation.request.raw_text));assert(text.includes('交付复核'));assert(text.includes(fixture.source.draft.custom[0]));
 assert.equal(storage.values.size,1,'Discovery and formatting never mutate the answer or dispatch a response');
});

test('current live form is unique; a disconnected generation can keep its answers as data',()=>{
 const x=setup();Object.assign(x.session,{generation:7,active_operation_id:fixture.source.identity.parent,pending_questions:[fixture.question]});
 assert.deepEqual(readQuestionRecoveries(x.storage,x.session,[]),[]);
 x.session.owner_status='DISCONNECTED';x.session.connected_owner=false;assert.equal(readQuestionRecoveries(x.storage,x.session,[]).length,1);
});

test('successful answers and unresolved response outcomes never become a second reply prompt',()=>{
 const {storage,session,row}=setup();for(const status of ['RETURNED','COMPLETED','ATTEMPTED','UNKNOWN_OUTCOME','ACCEPTED']){
  assert.deepEqual(readQuestionRecoveries(storage,session,[{identity:row.identity,status,choice:'reply'}]),[]);
 }
 assert.equal(readQuestionRecoveries(storage,session,[{identity:row.identity,status:'FAILED',choice:'reply'}]).length,1);
});

test('only this exact project and native session namespace is inspected',()=>{
 const x=setup();x.storage.setItem('eeas-discussion-draft-v1:["original","other_project","ctl_'+x.session.session_ref.id+'_bad"]','broken');
 x.storage.setItem('eeas-discussion-draft-v1:["original","'+x.session.project_id+'","ctl_other_session_bad"]','broken');
 assert.equal(readQuestionRecoveries(x.storage,x.session,[]).length,1);
 x.session.session_ref={...x.session.session_ref,sha256:'f'.repeat(64)};assert.throws(()=>readQuestionRecoveries(x.storage,x.session,[]),/UNAVAILABLE/);
});

test('misplaced scope and mismatched request generation fail closed without touching originals',()=>{
 for(const mutate of [v=>v.identity.request.generation++,v=>v.identity.parent='other_parent',v=>v.identity.request.sha256='bad']){
  const x=setup(),envelope=JSON.parse(x.storage.getItem(fixture.source.key)),v=JSON.parse(envelope.text);mutate(v);envelope.text=JSON.stringify(v);const raw=JSON.stringify(envelope);x.storage.setItem(fixture.source.key,raw);
  assert.throws(()=>readQuestionRecoveries(x.storage,x.session,[]),/UNAVAILABLE/);assert.equal(x.storage.getItem(fixture.source.key),raw);
 }
});

test('an earlier response for a different question cannot hide this unsent answer',()=>{
 const {storage,session,row}=setup(),other=structuredClone(row.identity);other.request.sha256='a'.repeat(64);
 assert.notEqual(controlKey(other),controlKey(row.identity));assert.equal(readQuestionRecoveries(storage,session,[{identity:other,status:'RETURNED',choice:'reply'}]).length,1);
});

test('original parent, generation, native SID and conversation must match before copying',()=>{
 const {session,row}=setup();for(const patch of [{id:'different'},{generation:8},{original_session_id:'other_native'},{status:'RETURNED'},{status:'ACCEPTED'},{request:{...fixture.operation.request,conversation_ref:{id:'native:'+'e'.repeat(64)}}}]){
  assert.throws(()=>questionRecoveryText(row,{...fixture.operation,...patch},session,fixture.conversation,'zh'),/NOT_CONFIRMED/);
 }
});

test('copy appends to an existing message and retry does not duplicate it',()=>{
 const x=setup(),body=questionRecoveryText(x.row,fixture.operation,x.session,fixture.conversation,'en'),before='我的现有消息 😀\n';
 const merged=appendQuestionRecovery(before,body);assert(merged.startsWith(before+'\n\n'));assert(merged.includes(fixture.source.draft.custom[0]));assert.equal(appendQuestionRecovery(merged,body),merged);
 assert.throws(()=>appendQuestionRecovery('中'.repeat(15999),body),/TOO_LONG/);assert.equal(x.storage.getItem(fixture.source.key),fixture.source.raw);
});

test('a concurrent answer edit prevents stale copying or marking and preserves the new draft',()=>{
 const x=setup(),old=x.row,editor=new ControlQuestionDraft(x.storage,old.identity);editor.set({answers:[['界面复核']],custom:['另一个窗口的较新回答']});const latest=x.storage.getItem(old.key);
 assert.throws(()=>assertQuestionRecoveryCurrent(x.storage,old),/CHANGED/);assert.throws(()=>markQuestionRecovered(x.storage,old,fixture.conversation),/CHANGED/);assert.equal(x.storage.getItem(old.key),latest);
});

test('local-copy marker preserves all answer data and is reset by a new explicit answer edit',()=>{
 const x=setup(),old=x.row;markQuestionRecovered(x.storage,old,fixture.conversation);let row=x.row;assert.equal(row.copiedTo,fixture.conversation);assert.deepEqual(row.draft,old.draft);assert.deepEqual(row.identity,old.identity);
 const editor=new ControlQuestionDraft(x.storage,row.identity,['真实原问题']);editor.set({...row.draft,custom:['新的未发送回答']});row=x.row;assert.equal(row.copiedTo,undefined);assert.deepEqual(row.questions,['真实原问题']);assert.equal(row.draft.custom[0],'新的未发送回答');
 assert.equal(discussionDraftKey(controlDraftScope(old.identity)),old.key);
});

test('failed local marker write neither deletes the source nor claims a completed transfer',()=>{
 const x=setup(),row=x.row,storage={...x.storage,setItem(){throw Error('disk full');}};assert.throws(()=>markQuestionRecovered(storage,row,fixture.conversation),/disk full/);assert.equal(x.storage.getItem(row.key),row.raw);
});

test('future generations and empty answers are not recovery candidates; scan is bounded',()=>{
 const x=setup();x.session.generation=6;assert.deepEqual(readQuestionRecoveries(x.storage,x.session,[]),[]);
 x.session.generation=8;new ControlQuestionDraft(x.storage,fixture.source.identity).set({answers:[],custom:[]});assert.deepEqual(readQuestionRecoveries(x.storage,x.session,[]),[]);
 assert.throws(()=>readQuestionRecoveries({...x.storage,length:8193},x.session,[]),/UNAVAILABLE/);
});
