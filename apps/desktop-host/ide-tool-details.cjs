'use strict';
// Explicit, read-only view of one saved original tool part; never invokes it.
const {createHash}=require('node:crypto');
const ID=/^[A-Za-z0-9_-]{1,100}$/,SHA=/^[a-f0-9]{64}$/;
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const canonical=v=>JSON.stringify(v,(_k,x)=>object(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,x[k]])):x);
const hash=v=>createHash('sha256').update(canonical(v)).digest('hex');
function fail(code='TOOL_CHANGED'){throw Object.assign(new Error('原工具记录已变化，请重新读取。'),{code,public:true});}
function reference(session,message,part){
  if(!ID.test(session?.original_session_id??'')||message?.info?.role!=='assistant'||message?.info?.sessionID!==session.original_session_id||
     !ID.test(message?.info?.id??'')||part?.type!=='tool'||part.sessionID!==session.original_session_id||
     part.messageID!==message.info.id||!ID.test(part.id??'')||!ID.test(part.callID??''))return undefined;
  return {message_id:message.info.id,part_id:part.id,call_id:part.callID,sha256:hash(part)};
}
function validRef(ref){return object(ref)&&Object.keys(ref).sort().join(',')==='call_id,message_id,part_id,sha256'&&
  ['message_id','part_id','call_id'].every(k=>typeof ref[k]==='string'&&ID.test(ref[k]))&&SHA.test(ref.sha256??'');}
function token(scope,ref){return validRef(ref)?hash({scope,tool_ref:ref}):null;}
function boundedValue(value,max){
  let remaining=max,truncated=false;
  const clip=text=>{const chars=Array.from(text);if(chars.length>remaining)truncated=true;const kept=chars.slice(0,remaining).join('');remaining-=Math.min(chars.length,remaining);return kept;};
  const visit=(v,depth)=>{if(typeof v==='string')return clip(v);if(v===null||['boolean','number'].includes(typeof v))return v;
    if(depth>=5||remaining<=0){truncated=true;return '[…]';}
    if(Array.isArray(v)){if(v.length>64)truncated=true;return v.slice(0,64).map(x=>visit(x,depth+1));}
    if(object(v)){const entries=Object.entries(v);if(entries.length>64)truncated=true;const result={};for(const [key,item]of entries.slice(0,64)){if(remaining<=0){truncated=true;break;}Object.defineProperty(result,clip(key),{value:visit(item,depth+1),enumerable:true,configurable:true});}return result;}
    return null;};
  const bounded=visit(value,0),text=typeof bounded==='string'?bounded:JSON.stringify(bounded,null,2);
  const chars=Array.from(text??'');return {text:chars.slice(0,max).join(''),truncated:truncated||chars.length>max};
}
function projectTool(session,scope,ref){
  if(!validRef(ref))fail('INVALID_PARAMS');
  const messages=session.messages.filter(m=>m?.info?.id===ref.message_id);
  if(messages.length!==1)fail();
  const parts=(messages[0].parts||[]).filter(p=>p?.id===ref.part_id);
  if(parts.length!==1||canonical(reference(session,messages[0],parts[0]))!==canonical(ref))fail();
  const part=parts[0],state=object(part.state)?part.state:{};
  return {scope,tool_ref:ref,tool:String(part.tool??'').slice(0,200),status:String(state.status??'UNKNOWN').slice(0,40),
    input:boundedValue(state.input??{},8000),output:boundedValue(state.output??'',16000),error:boundedValue(state.error??'',8000),
    title:typeof state.title==='string'?state.title.slice(0,500):null,
    time:object(state.time)?Object.fromEntries(['start','end'].filter(k=>Number.isSafeInteger(state.time[k])).map(k=>[k,state.time[k]])):null,
    observation_at:session.observation_at??null,observation_is_current:session.observation_is_current===true,
    source:'EXACT_SAVED_ORIGINAL_TOOL_PART',source_generation:'NOT_ATTRIBUTED_BY_ORIGINAL_PART',read_only:true,model_calls:0,tool_invocations:0,
    coverage:'BOUNDED_INPUT_OUTPUT_ERROR_NOT_TASK_ACCEPTANCE_OR_REPLAY'};
}
module.exports={reference,validRef,token,projectTool};
