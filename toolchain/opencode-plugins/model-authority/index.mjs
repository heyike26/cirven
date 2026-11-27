// Pinned OpenCode 733b2c6 chat.params: metadata admission only, no model/tool.
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {installSubscriptionHTTP} from './subscription-http.mjs';
const stable=v=>Array.isArray(v)?'['+v.map(stable).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}':JSON.stringify(v);
const sha=v=>createHash('sha256').update(v).digest('hex');
const id=/^[A-Za-z0-9_-]{1,100}$/;
function ordinary(filename,limit){
 if(typeof filename!=='string'||!path.isAbsolute(filename))throw Error('MODEL_AUTHORITY_SCOPE_REQUIRED');
 for(let current=filename;;current=path.dirname(current)){
  const info=fs.lstatSync(current);if(info.isSymbolicLink())throw Error('MODEL_AUTHORITY_LINKED_SCOPE');
  if(current===path.dirname(current))break;
 }
 const before=fs.statSync(filename);if(!before.isFile()||before.nlink!==1||before.size>limit)throw Error('MODEL_AUTHORITY_SCOPE_INVALID');
 const bytes=fs.readFileSync(filename),after=fs.statSync(filename);
 if(before.dev!==after.dev||before.ino!==after.ino||before.size!==after.size||before.mtimeMs!==after.mtimeMs)throw Error('MODEL_AUTHORITY_SCOPE_CHANGED');return bytes;
}
export const ModelAuthorityPlugin=async input=>{
 const filename=process.env.EEAS_MODEL_AUTHORITY_CONFIG;
 const scope=()=>{
  const value=JSON.parse(ordinary(filename,16384)),{scope_sha256,...body}=value;
  if(sha(stable(body))!==scope_sha256||body.schema!=='eeas.model-authority-scope.v1'||body.plugin_id!=='eeas.model-authority')throw Error('MODEL_AUTHORITY_SCOPE_INTEGRITY');
  const actual=path.resolve(input.directory),expected=path.resolve(value.directory);
  if(process.platform==='win32'?actual.toLowerCase()!==expected.toLowerCase():actual!==expected)throw Error('MODEL_AUTHORITY_DIRECTORY_CHANGED');
  const url=new URL(value.broker_url);
  if(url.protocol!=='http:'||url.hostname!=='127.0.0.1'||url.pathname!=='/'||!url.port||url.username||url.password||url.search||url.hash||!/^[a-f0-9]{64}$/.test(value.broker_key))throw Error('MODEL_AUTHORITY_BROKER_DENIED');
  return value;
 };
 const initial=scope(),readyPath=path.join(path.dirname(filename),'ready.json');
 const ask=async(value,body)=>{
  const response=await fetch(value.broker_url+'/v1/call',{method:'POST',headers:{Authorization:'Bearer '+value.broker_key,'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(12000),redirect:'error'});
  const reader=response.body?.getReader();if(!reader)throw Error('MODEL_AUTHORITY_REPLY_MISSING');let bytes=0,chunks=[];
  try{for(;;){const row=await reader.read();if(row.done)break;bytes+=row.value.byteLength;if(bytes>4096)throw Error('MODEL_AUTHORITY_REPLY_TOO_LARGE');chunks.push(row.value);}}
  finally{await reader.cancel().catch(()=>{});}
  if(!response.ok)throw Error('MODEL_AUTHORITY_REJECTED');return JSON.parse(Buffer.concat(chunks).toString('utf8'));
 };
 const protocol=initial.call_protocol===undefined?'eeas.model-authority':initial.call_protocol==='ADAPTER_EXECUTION_V1'?'eeas.adapter-model-authority':null;if(!protocol)throw Error('MODEL_AUTHORITY_PROTOCOL_UNSUPPORTED');
 const meter=installSubscriptionHTTP({initial,scope,ask});
 const receipt=stable({schema:'eeas.model-authority-ready.v1',plugin_id:initial.plugin_id,plugin_manifest_sha256:initial.plugin_manifest_sha256,initial_scope_sha256:initial.scope_sha256,...(meter?{subscription_http:'INSTALLED_ORIGINAL_CODEX_HTTP_FALLBACK'}:{})});
 try{try{fs.writeFileSync(readyPath,receipt,{flag:'wx',mode:0o600});}catch(error){if(error.code!=='EEXIST'||ordinary(readyPath,4096).toString()!==receipt)throw error;}}
 catch(error){meter?.dispose();throw error;}
 let step=0,busy=0;
 return {...(meter?{'chat.headers':(incoming,output)=>meter.headers(incoming,output),dispose:async()=>meter.dispose()}:{}),'chat.params':async incoming=>{
  if(busy>=4||step>=1048576)throw Error('MODEL_AUTHORITY_CAPACITY');
  const value=scope();
  if(!/^ses_[A-Za-z0-9_-]{1,100}$/.test(value.native_session_id??'')||!/^ses_[A-Za-z0-9_-]{1,100}$/.test(incoming.sessionID??'')||!/^msg_[A-Za-z0-9_-]{1,100}$/.test(incoming.message?.id??'')||!id.test(incoming.agent??''))throw Error('MODEL_AUTHORITY_ORIGINAL_CONTEXT_REQUIRED');
  // Neither the user message body nor any model parameter is copied or changed.
  const body={schema:protocol+'-call.v1',owner_ref:value.owner_ref,generation:value.generation,
   root_native_session_id:value.native_session_id,native_session_id:incoming.sessionID,
   user_message_id:incoming.message.id,agent:incoming.agent,step:++step};
  busy++;
  try{
   const result=await ask(value,body);
   if(result.schema!==protocol+'-receipt.v1'||result.admitted!==true||result.step!==body.step||result.generation!==body.generation||result.root_native_session_id!==body.root_native_session_id||result.native_session_id!==body.native_session_id||result.user_message_id!==body.user_message_id||result.model_calls_reserved!==0)throw Error('MODEL_AUTHORITY_REPLY_MISMATCH');
   meter?.register(incoming,body,result);
  }finally{busy--;}
 }};
};
export default ModelAuthorityPlugin;
