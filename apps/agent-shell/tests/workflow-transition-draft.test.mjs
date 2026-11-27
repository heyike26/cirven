import assert from 'node:assert/strict';
import test from 'node:test';
import {draftKey} from '../src/stage-workflows';
import {readPendingTransition,settleStageDraft,settleTransitionDraft,stageDraftSettledEvent} from '../src/workflow-transition';

const owner={project_id:'project',conversation_id:'native:'+'1'.repeat(64)};
const session='native:'+'2'.repeat(64);
const key=draftKey({...owner,id:'run-test'},'test',session);
const plan={kind:'rework',run_id:'run-test',node_id:'test',stage_session_id:session,expected_revision:4,
 payload:{reason:'Check the archived output',target_node_id:'development',case_ids:['TC-1']}};
const request={operation_id:'original-operation',request_conversation_id:owner.conversation_id,plan,draft_edit:'edit-original'};

function storage(){
 const records=new Map();
 globalThis.localStorage={getItem:key=>records.get(key)??null,setItem:(key,value)=>records.set(key,value)};
 globalThis.window=new EventTarget();
 return {
  assign:(data,entry=key)=>localStorage.setItem(entry,JSON.stringify(data)),
  read:()=>JSON.parse(localStorage.getItem(key)),
 };
}

test('a recovered rework receipt consumes only the submitted edit and preserves companion fields',()=>{
 const data=storage();
 const rework={reason:plan.payload.reason,target:plan.payload.target_node_id};
 const latest={input:{},note:'New note',rework,reworkEdit:'edit-newer'};
 const events=[];
 window.addEventListener(stageDraftSettledEvent,event=>events.push(event.detail));
 data.assign(latest);
 settleTransitionDraft(owner,request);
 assert.deepEqual(data.read(),latest);
 assert.deepEqual(events,[]);
 data.assign({...latest,reworkEdit:request.draft_edit});
 settleTransitionDraft(owner,request);
 assert.deepEqual(data.read(),{input:{},note:'New note'});
 assert.deepEqual(events,[{key,field:'rework',edit:request.draft_edit}]);
});

test('older pending requests can be recovered without deleting ambiguous drafts',()=>{
 const data=storage();
 const current={input:{},followUp:'Retain the date',followUpType:'instruction'};
 const old={...request,draft_edit:undefined,plan:{...plan,kind:'revise',payload:{text:current.followUp}}};
 data.assign(current);
 data.assign(old,'pending');
 const pending=readPendingTransition('pending');
 assert.deepEqual(pending,{operation_id:old.operation_id,request_conversation_id:old.request_conversation_id,plan:old.plan});
 settleTransitionDraft(owner,pending);
 assert.deepEqual(data.read(),current);
});

test('a matching edit still requires the submitted content and request purpose',()=>{
 const data=storage();
 const current={input:{},followUp:'Keep original names',followUpType:'requirement',followUpEdit:'same-edit'};
 data.assign(current);
 settleStageDraft(key,{field:'followUp',text:current.followUp,sourceType:'instruction'},current.followUpEdit);
 assert.deepEqual(data.read(),current);
 settleStageDraft(key,{field:'followUp',text:'Alternative requirement',sourceType:'requirement'},current.followUpEdit);
 assert.deepEqual(data.read(),current);
 settleStageDraft(key,{field:'followUp',text:current.followUp,sourceType:current.followUpType},current.followUpEdit);
 assert.deepEqual(data.read(),{input:{},followUpType:'requirement'});
});
