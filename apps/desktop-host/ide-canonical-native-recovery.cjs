'use strict';
// Canonical membership is navigation metadata, never private execution authority.
const {createHash}=require('node:crypto');
const native=require('./ide-native-context-recovery.cjs');
const CID=/^(native|original|discovery):[a-f0-9]{64}$/,NATIVE=/^native:[a-f0-9]{64}$/,ID=/^[A-Za-z0-9_-]{1,100}$/,SHA=/^[a-f0-9]{64}$/;
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const stable=v=>Array.isArray(v)?'['+v.map(stable).join(',')+']':object(v)?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}':JSON.stringify(v);
const hash=v=>createHash('sha256').update(stable(v)).digest('hex'),same=(a,b)=>stable(a)===stable(b);
function fail(code='NATIVE_CONTEXT_RECOVERY_CHANGED'){throw Object.assign(Error('原会话或来源已变化，输入保留，请核对原记录。'),{code,public:true});}
function check(v){if(!v)fail();}
function keys(v,names){check(object(v)&&Object.keys(v).length===names.length&&Object.keys(v).every(k=>names.includes(k)));}
const project=v=>v===null||typeof v==='string'&&ID.test(v);
function scope(v){keys(v,['project_id','conversation_ref']);const r=v.conversation_ref;keys(r,['id','revision']);check(project(v.project_id)&&CID.test(r.id??'')&&Number.isSafeInteger(r.revision)&&r.revision>=1);return {project_id:v.project_id,conversation_ref:{...r}};}
function sourceRef(v){keys(v,['id','sha256']);check(NATIVE.test(v.id??'')&&SHA.test(v.sha256??''));return {...v};}
function origin(alias,reference){
 check(alias?.id===reference.id);const value=alias.identity;keys(value,['kind','project_id','conversation_id']);
 check(value.kind==='native'&&project(value.project_id)&&ID.test(value.conversation_id??'')&&hash(value)===reference.sha256&&alias.id==='native:'+hash([value.project_id,value.conversation_id]));
 return {project_id:value.project_id,conversation_id:value.conversation_id};
}
function access(pid,grants){if(!native.allowed(pid,grants))fail('NATIVE_CONTEXT_RECOVERY_ACCESS_REQUIRED');}
function historyContext(row,current,requested,grants){
 if(row.native_context_issue===undefined&&row.native_context_recovery===undefined)return {};
 const source=row.source;check(source?.kind==='native'&&ID.test(row.operation_id??''));
 const identity={kind:'native',project_id:source.project_id,conversation_id:source.conversation_id},reference={id:source.alias_id,sha256:hash(identity)};
 const exact=origin(current.sources.find(a=>a.id===reference.id),reference);
 if(!native.allowed(exact.project_id,grants))return {native_context_access_required:true};
 check(same({kind:'native',...exact},identity));
 const projected=native.context(row,row.operation_id,row.native_context_recovery?.record_current===true?'CURRENT_HOST':'HISTORICAL_READONLY');
 return {native_context_source:{origin:exact,source_ref:reference,...projected}};
}
async function dispatch(method,params,context,api){
 const requested=scope(params.scope),payload=params.payload??{},reference=sourceRef(payload.source_ref),grants=()=>typeof context.currentGrants==='function'?context.currentGrants():context.grants;
 const mode=method.slice('conversation.native.recovery.'.length);check(['prepare','command','operation'].includes(mode));
 const names=mode==='prepare'?['source_ref','parent_operation_id','expected_native_ref']:mode==='command'?['source_ref','parent_operation_id','request','preparation']:['source_ref','parent_operation_id','operation_id','request_sha256'];keys(payload,names);
 async function resolve(strict){
  access(requested.project_id,grants());const value=await api('/api/conversations/'+encodeURIComponent(requested.conversation_ref.id)+'/scope',undefined,context.signal);
  const current=scope({project_id:value?.project_id,conversation_ref:value?.conversation_ref});check(value.version===1&&current.conversation_ref.id===requested.conversation_ref.id&&current.conversation_ref.revision>=requested.conversation_ref.revision&&Array.isArray(value.sources)&&value.sources.length<=64);
  if(strict)check(same(current,requested));access(current.project_id,grants());
  const exact=origin(value.sources.find(a=>a.id===reference.id),reference);access(exact.project_id,grants());
  return {requested_scope:requested,current_scope:current,source_ref:reference,origin:exact};
 }
 let binding=await resolve(mode==='prepare');const {source_ref,...p}=payload;
 if(mode==='command'){
  const old=p.preparation?.canonical_binding;check(old&&same(old.requested_scope,requested)&&same(old.source_ref,reference)&&same(old.origin,binding.origin));
 }
 const checkedApi=async(route,body,signal)=>{
  if(body!==undefined&&!route.endsWith('/prepare')){
   const fresh=await resolve(true);check(same(fresh.origin,binding.origin));binding=fresh;
  }
  return api(route,body,signal);
 };
 const value=await native.dispatch('native.context.recovery.'+mode,{scope:binding.origin,payload:p},context,checkedApi);
 const after=await resolve(mode==='prepare');check(same(after.origin,binding.origin));
 return {...value,canonical_binding:after};
}
module.exports={dispatch,historyContext,scope,sourceRef,origin};
