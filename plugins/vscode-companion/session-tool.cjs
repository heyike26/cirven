'use strict';
const {canonical,hash}=require('./client.cjs');
const ID=/^[A-Za-z0-9_-]{1,100}$/,SHA=/^[a-f0-9]{64}$/;
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
function validRef(ref){return object(ref)&&Object.keys(ref).sort().join(',')==='call_id,message_id,part_id,sha256'&&
  ['message_id','part_id','call_id'].every(k=>typeof ref[k]==='string'&&ID.test(ref[k]))&&SHA.test(ref.sha256??'');}
const token=(scope,ref)=>validRef(ref)?hash(canonical({scope,tool_ref:ref})):null;
function find(session,scope,expected){if(!session||session.kind==='NATIVE_CONVERSATION')return null;
  const matches=[];for(const message of session.messages||[])for(const part of message.parts||[])
    if(part.type==='tool'&&validRef(part.tool_ref)&&part.tool_ref.part_id===part.id&&part.tool_ref.message_id===message.info?.id&&token(scope,part.tool_ref)===expected)matches.push(part.tool_ref);
  return matches.length===1?matches[0]:null;}
function validate(value,scope,ref){
  const wrong=()=>{throw Object.assign(new Error('原工具记录已变化。'),{code:'TOOL_CHANGED'});};
  if(!object(value)||canonical(value.scope)!==canonical(scope)||canonical(value.tool_ref)!==canonical(ref)||
    value.read_only!==true||value.model_calls!==0||value.tool_invocations!==0||value.source!=='EXACT_SAVED_ORIGINAL_TOOL_PART'||
    typeof value.tool!=='string'||value.tool.length>200||typeof value.status!=='string'||value.status.length>40)wrong();
  for(const [key,max]of[['input',8000],['output',16000],['error',8000]])if(!object(value[key])||typeof value[key].text!=='string'||
    Array.from(value[key].text).length>max||typeof value[key].truncated!=='boolean')wrong();
  if(Buffer.byteLength(JSON.stringify(value))>262144)wrong();
  return JSON.parse(JSON.stringify(value));
}
module.exports={validRef,token,find,validate};
