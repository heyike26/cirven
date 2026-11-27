'use strict';
const {canonical,hash}=require('./client.cjs');
const ID=/^[A-Za-z0-9_-]{1,100}$/,SHA=/^[a-f0-9]{64}$/;
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
function validRef(ref){return object(ref)&&Object.keys(ref).sort().join(',')==='id,root_native_session_id,sha256'&&
  ID.test(ref.id??'')&&ID.test(ref.root_native_session_id??'')&&ref.id!==ref.root_native_session_id&&SHA.test(ref.sha256??'');}
const token=(scope,ref)=>validRef(ref)?hash(canonical({scope,child_ref:ref})):null;
const draftKey=(scope,ref)=>validRef(ref)?hash(canonical({scope,child_id:ref.id,root_native_session_id:ref.root_native_session_id})):null;
function receipt(value,entry){const wrong=()=>{throw Object.assign(new Error('原子任务回执身份不符。'),{code:'CHILD_RECEIPT_CHANGED'});};
 if(!object(value)||value.id!==entry.operation_id||value.kind!==entry.child_kind||value.request_sha256!==entry.child_request_sha256||
   canonical(value.session_ref)!==canonical(entry.scope.session_ref)||value.generation!==entry.scope.generation||value.automatic_replay!==false)wrong();
 if(value.status==='UNKNOWN_OUTCOME'||value.status==='DISPATCHING'||value.status==='RECORDED_NOT_DISPATCHED')return 'UNKNOWN';
 if(value.status==='FAILED'&&value.native_post_attempted===false)return 'FAILED';
 if(!['ACCEPTED','RETURNED','CANCELLED','COHORT_RETURNED','FAILED'].includes(value.status)||value.native_dispatch_acknowledged!==true||canonical(value.child_ref)!==canonical(entry.child_target)||
   value.original_session_id!==entry.child_target.id||value.root_original_session_id!==entry.child_target.root_native_session_id||
   (value.original_active_operation_id??null)!==entry.child_parent)wrong();
 return 'ACCEPTED';}
function find(session,scope,expected){if(!session||session.kind==='NATIVE_CONVERSATION')return null;
  const matches=(session.children||[]).filter(child=>validRef(child.child_ref)&&child.child_ref.id===child.id&&
    child.parentID===child.child_ref.root_native_session_id&&token(scope,child.child_ref)===expected);
  return matches.length===1?matches[0].child_ref:null;}
function validate(value,scope,ref){const wrong=()=>{throw Object.assign(new Error('原子任务身份已变化。'),{code:'CHILD_CHANGED'});};
  if(!object(value)||value.kind!=='ORIGINAL_CHILD_VIEW'||canonical(value.scope)!==canonical(scope)||canonical(value.child_ref)!==canonical(ref)||
    value.read_only!==true||value.model_calls!==0||value.native_posts!==0||value.new_owner_started!==false||value.transcript_copied!==false||
    !object(value.coverage)||value.coverage.history_complete!==false||!Array.isArray(value.messages)||value.messages.length>40||
    typeof value.observation_is_current!=='boolean'||Buffer.byteLength(JSON.stringify(value))>524288)wrong();
  let text=0;for(const message of value.messages){if(!object(message)||message.info?.sessionID!==ref.id||!ID.test(message.info?.id??'')||
    !['user','assistant'].includes(message.info?.role)||!Array.isArray(message.parts)||message.parts.length>80)wrong();
    for(const part of message.parts){if(part.type==='text'){if(typeof part.text!=='string'||Array.from(part.text).length>16000)wrong();text+=Array.from(part.text).length;}
      else if(!['tool','file'].includes(part.type))wrong();if(part.tool_ref!==undefined)wrong();}}
  if(text>64000)wrong();return JSON.parse(JSON.stringify(value));
}
module.exports={validRef,token,draftKey,receipt,find,validate};
