'use strict';
// Explicit public connection configuration. No second execution loop.
const {createHash}=require('node:crypto');
const METHODS=['conversation.configuration.prepare','conversation.configure','conversation.configuration.operation','conversation.configuration.continue','conversation.configuration.adoption.prepare','conversation.configuration.adoption.command','conversation.configuration.adoption.operation'];
const ID=/^[A-Za-z0-9_-]{1,100}$/,SHA=/^[a-f0-9]{64}$/;
const stable=v=>Array.isArray(v)?'['+v.map(stable).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}':JSON.stringify(v);
const digest=v=>createHash('sha256').update(stable(v)).digest('hex'),same=(a,b)=>stable(a)===stable(b);
function fail(code='CONFIGURATION_CHANGED'){throw Object.assign(Error('执行配置已变化，输入保留，请重新核对。'),{code,public:true});}
function check(v,code){if(!v)fail(code);}
function object(v){return v&&typeof v==='object'&&!Array.isArray(v);}
function keys(v,names){check(object(v)&&Object.keys(v).every(k=>names.includes(k)),'INVALID_PARAMS');}
function ref(v){check(object(v)&&ID.test(v.id??'')&&Number.isSafeInteger(v.revision)&&v.revision>=1&&SHA.test(v.sha256??''));return v;}
function source(v){if(v===null)return null;keys(v,['project_id','session_ref','generation']);check(ID.test(v.project_id??'')&&Number.isSafeInteger(v.generation)&&v.generation>=0);ref(v.session_ref);return v;}
function observationPaths(v){return Array.isArray(v)&&v.length<=32&&new Set(v).size===v.length&&v.every(p=>typeof p==='string'&&p.length>0&&p.length<=1024&&!/[\\:*?\[\]\x00-\x1f\x7f]/.test(p)&&!p.startsWith('/')&&p.split('/').every(part=>part!==''&&part!=='.'&&part!=='..'&&!['.git','.eeas-sdk-state','.eeas-runtime'].includes(part.toLowerCase())&&!part.startsWith('.eeas-write-')));}
function requestBody(s,v){
 const fields=['operation_id','conversation_ref','workspace_ref','provider_profile_ref','workflow_ref','workflow_provider_refs','capability_binding_sha256','title','ttl_seconds','max_calls','max_output_tokens','allowed_effects','command_allowlist','mcp_tool_allowlist','mcp_tool_selection','network_mode','project_id'];keys(v,[...fields,'command_observation_paths']);check(Object.keys(v).length===fields.length+(Object.hasOwn(v,'command_observation_paths')?1:0)&&fields.every(k=>Object.hasOwn(v,k))&&(!Object.hasOwn(v,'command_observation_paths')||observationPaths(v.command_observation_paths))&&ID.test(v.operation_id??'')&&same(v.conversation_ref,s.conversation_ref)&&v.project_id===s.project_id);ref(v.provider_profile_ref);if(v.workflow_ref!==null)ref(v.workflow_ref);
 check(v.workspace_ref===null||object(v.workspace_ref)&&SHA.test(v.workspace_ref.id??'')&&SHA.test(v.workspace_ref.binding_sha256??'')&&Number.isSafeInteger(v.workspace_ref.revision)&&v.workspace_ref.revision>=1);
 check(Array.isArray(v.workflow_provider_refs)&&v.workflow_provider_refs.length<=16);v.workflow_provider_refs.forEach(ref);check(SHA.test(v.capability_binding_sha256??'')&&typeof v.title==='string'&&v.title.length<=200);
 for(const [field,min,max] of [['ttl_seconds',30,3600],['max_calls',1,128],['max_output_tokens',64,16384]])check(Number.isSafeInteger(v[field])&&v[field]>=min&&v[field]<=max);
 check(Array.isArray(v.allowed_effects)&&v.allowed_effects.length<=4&&new Set(v.allowed_effects).size===v.allowed_effects.length&&v.allowed_effects.every(x=>['PROCESS_START','REMOTE_MCP_CONNECT','BACKGROUND_COMMAND','WORKSPACE_WRITE'].includes(x)));
 check(Array.isArray(v.command_allowlist)&&v.command_allowlist.length<=16&&new Set(v.command_allowlist).size===v.command_allowlist.length&&v.command_allowlist.every(x=>typeof x==='string'&&x.trim()&&x.length<=1024&&!/[\x00-\x1f*?\[\]]/.test(x)));
 check(Array.isArray(v.mcp_tool_allowlist)&&v.mcp_tool_allowlist.length<=64&&v.mcp_tool_allowlist.every(x=>typeof x==='string'&&/^[A-Za-z0-9_-]{1,200}$/.test(x))&&(v.mcp_tool_selection===null||Array.isArray(v.mcp_tool_selection)&&v.mcp_tool_selection.length<=64));
 check(['AS_CONFIGURED','STRICT_OFFLINE'].includes(v.network_mode)&&Buffer.byteLength(JSON.stringify(v))<=98304);return v;
}
function confirmation(request,preparation_sha256,execution_project_id){return digest({request,preparation_sha256,execution_project_id});}
function configuredBody(s,p){
 keys(p,['request','preparation_sha256','original_source','execution_project_id','confirmation_sha256']);const request=requestBody(s,p.request);source(p.original_source);check(SHA.test(p.preparation_sha256??'')&&(p.execution_project_id===null||ID.test(p.execution_project_id??''))&&p.confirmation_sha256===confirmation(request,p.preparation_sha256,p.execution_project_id));return p;
}
function originalReference(v,allowed,grants){keys(v,['operation_id','project_id','request_sha256','confirmation_sha256']);check(ID.test(v.operation_id??'')&&(v.project_id===null||ID.test(v.project_id??''))&&SHA.test(v.request_sha256??'')&&SHA.test(v.confirmation_sha256??''));check(allowed(v.project_id,grants),'EXECUTION_SCOPE_DENIED');return v;}
function adoptionPreparation(value,s,original,allowed,grants){check(value?.kind==='CONFIGURATION_ADOPTION_PREPARATION'&&same(value.preparation,{original,conversation_ref:s.conversation_ref,project_id:s.project_id})&&same(value.authority?.original,original)&&same(value.authority?.conversation_ref,s.conversation_ref)&&value.authority.project_id===s.project_id&&value.preparation_sha256===digest(value.authority)&&typeof value.adoption_allowed==='boolean'&&value.read_only===true&&value.model_calls===0&&value.owner_started===false&&value.permission_granted===false&&value.automatic_message_replay===false&&Buffer.byteLength(JSON.stringify(value))<=131072);check(allowed(value.authority.execution_project_id,grants),'EXECUTION_SCOPE_DENIED');const previous=source(value.authority.original_source);if(previous)check(allowed(previous.project_id,grants),'EXECUTION_SCOPE_DENIED');const candidate=value.authority.candidate?.source;if(candidate)check(allowed(source(candidate).project_id,grants),'EXECUTION_SCOPE_DENIED');return value;}
function disposition(value,s,operation,expected,expectedConfirmation,allowed,grants){if(!value)return undefined;const {basis_sha256,...body}=value;check(value.kind==='SUPERSEDED_CONFIGURATION'&&basis_sha256===digest(body)&&same(value.original,{operation_id:operation,project_id:s.project_id,request_sha256:expected,confirmation_sha256:expectedConfirmation})&&value.current_scope?.conversation_ref?.id===s.conversation_ref.id&&Number.isSafeInteger(value.current_scope.conversation_ref.revision)&&value.current_scope.conversation_ref.revision>=1&&ID.test(value.current?.operation_id??'')&&SHA.test(value.current.request_sha256??'')&&Number.isSafeInteger(value.current.provider_generation)&&value.current.provider_generation>=1&&value.read_only===true&&value.owner_started===false&&value.model_calls===0&&value.permission_granted===false&&value.automatic_message_replay===false&&value.old_execution_outcome_changed===false);const selected=source(value.original_source);ref(value.provider_profile_ref);if(![value.current_scope.project_id,value.current.project_id,selected.project_id].every(pid=>allowed(pid,grants)))return undefined;return value;}
async function adoptionDispatch(method,s,p,h){const {get,post,allowed,grants}=h,base='/api/conversations/'+encodeURIComponent(s.conversation_ref.id)+'/configuration-adoption';
 if(method===METHODS[4]){keys(p,['original']);const original=originalReference(p.original,allowed,grants);return adoptionPreparation(await post(base+'/prepare',{original,conversation_ref:s.conversation_ref,project_id:s.project_id}),s,original,allowed,grants);}
 let operation,expected,original,confirmationSha,rejected;
 if(method===METHODS[5]){keys(p,['operation_id','preparation','preparation_sha256','confirmation_sha256']);keys(p.preparation,['original','conversation_ref','project_id']);original=originalReference(p.preparation.original,allowed,grants);check(ID.test(p.operation_id??'')&&same(p.preparation.conversation_ref,s.conversation_ref)&&p.preparation.project_id===s.project_id&&SHA.test(p.preparation_sha256??'')&&p.confirmation_sha256===digest({operation_id:p.operation_id,preparation:p.preparation,preparation_sha256:p.preparation_sha256}));
  adoptionPreparation(await post(base+'/prepare',p.preparation),s,original,allowed,grants);operation=p.operation_id;expected=digest(p);confirmationSha=p.confirmation_sha256;try{await post(base,p);}catch(error){rejected=error;/* Read the original metadata command only; never start a loop on loss. */}
 }else{check(method===METHODS[6]);keys(p,['operation_id','request_sha256','original','confirmation_sha256']);operation=p.operation_id;expected=p.request_sha256;confirmationSha=p.confirmation_sha256;original=originalReference(p.original,allowed,grants);check(ID.test(operation??'')&&SHA.test(expected??'')&&SHA.test(confirmationSha??''));}
 let value;try{value=await get(base+'-operations/'+encodeURIComponent(operation));}catch(error){if(rejected&&[400,401,403,404,409,413,422].includes(rejected.status)&&error?.status===404)fail('API_REQUEST_REJECTED');throw error;}check(value?.kind==='CONFIGURATION_ADOPTION'&&value.operation_id===operation&&value.request_sha256===expected&&same(value.original,original)&&same(value.conversation_ref,s.conversation_ref)&&value.project_id===s.project_id&&value.confirmation_sha256===confirmationSha&&value.accepted===true&&typeof value.current==='boolean'&&value.model_calls===0&&value.owner_started===false&&value.permission_granted===false&&value.automatic_message_replay===false);return value;
}
async function dispatch(method,s,p,h){
 const {get,post,current,checkedOwner,allowed,grants}=h,base='/api/conversations/'+encodeURIComponent(s.conversation_ref.id);
 if(METHODS.slice(4).includes(method))return adoptionDispatch(method,s,p,h);
 if(method===METHODS[0]){
  keys(p,['original_source']);const view=await current();await checkedOwner(view);const hint=source(p.original_source??null);if(hint)check(allowed(hint.project_id,grants),'EXECUTION_SCOPE_DENIED');
  const value=await post(base+'/connection-configuration/prepare',{conversation_ref:s.conversation_ref,original_source:hint});
  check(value?.kind==='CONVERSATION_CONFIGURATION_PREPARATION'&&same(value.authority?.conversation_ref,s.conversation_ref)&&value.authority.project_id===s.project_id&&value.preparation_sha256===digest(value.authority)&&value.read_only===true&&value.model_calls===0&&value.owner_started===false&&value.permission_granted===false&&value.automatic_message_replay===false);
  const actual=source(value.authority.original_source);if(actual)check(allowed(actual.project_id,grants),'EXECUTION_SCOPE_DENIED');
  check(Array.isArray(value.workspaces)&&value.workspaces.length<=128&&Array.isArray(value.mcp_servers)&&value.mcp_servers.length<=128&&Buffer.byteLength(JSON.stringify(value))<=524288);
  const workspaces=value.workspaces.filter(row=>allowed(row.project_id,grants));if(workspaces.length!==value.workspaces.length)fail('EXECUTION_SCOPE_DENIED');
  const after=await current();check(same(after.current_owner??null,view.current_owner??null));return value;
 }
 const operation=method===METHODS[1]?p.request?.operation_id:p.operation_id;
 check(ID.test(operation??''),'INVALID_PARAMS');
 let expected,expectedConfirmation,executionPid;
 if(method===METHODS[1]){
  configuredBody(s,p);await checkedOwner(await current());executionPid=p.execution_project_id;check(allowed(executionPid,grants),'EXECUTION_SCOPE_DENIED');
  const native={...p.request};delete native.operation_id;delete native.project_id;expected=digest(native);expectedConfirmation=p.confirmation_sha256;
  try{await post(base+'/connection-configuration',p);}catch{/* Only inspect the frozen original key; never repeat POST. */}
 }else{
  check(method===METHODS[2]||method===METHODS[3]);keys(p,['operation_id','request_sha256','confirmation_sha256']);check(SHA.test(p.request_sha256??'')&&SHA.test(p.confirmation_sha256??''));expected=p.request_sha256;expectedConfirmation=p.confirmation_sha256;
 }
 const route='/api/agent-sessions/open-operations/'+encodeURIComponent(operation)+(s.project_id===null?'':'?project_id='+encodeURIComponent(s.project_id));
 function checked(value){
  check(value?.version===1&&value.operation_id===operation&&value.request_sha256===expected&&same(value.conversation_ref,s.conversation_ref)&&value.organization_project_id===s.project_id&&value.execution_confirmation_sha256===expectedConfirmation&&value.owner_started_by_query===false&&value.model_calls_by_query===0&&value.source_changed_by_query===false&&value.automatic_post_replay===false);
  if(value.session&&value.execution_project_id!==null)check(value.session.project_id===value.execution_project_id,'EXECUTION_SCOPE_DENIED');
  check(allowed(value.execution_project_id,grants),'EXECUTION_SCOPE_DENIED');if(executionPid!==undefined)check(value.execution_project_id===executionPid);return value;
 }
 let value=checked(await get(route));
 if(method===METHODS[3]&&!value.configuration_disposition&&['FAILED_NOT_STARTED','CANDIDATE_READY','CANDIDATE_CLOSED'].includes(value.status)){
  // Read the original receipt and both grants before this explicit action.
  // The API repeats known-not-started admission under its canonical lock.
  const previous=source(value.configuration_original_source);if(previous)check(allowed(previous.project_id,grants),'EXECUTION_SCOPE_DENIED');
  const adoption=value.configuration_adoption;
  if(adoption?.current===true){
   check(ID.test(adoption.operation_id??'')&&SHA.test(adoption.authority_sha256??'')&&adoption.conversation_ref?.id===s.conversation_ref.id&&Number.isSafeInteger(adoption.conversation_ref.revision)&&adoption.conversation_ref.revision>=1&&adoption.execution_project_id===value.execution_project_id);
   check(allowed(adoption.project_id,grants),'EXECUTION_SCOPE_DENIED');
   const view=await get(base+'/scope');check(view?.version===1&&view.project_id===adoption.project_id&&same(view.conversation_ref,adoption.conversation_ref)&&Array.isArray(view.sources)&&view.sources.length<=64);await checkedOwner(view);
  }else await checkedOwner(await current());
  try{await post(base+'/connection-configuration/continue',{operation_id:operation,project_id:s.project_id,request_sha256:expected,confirmation_sha256:expectedConfirmation});}catch{/* Resolve only the same original business key. */}
  value=checked(await get(route));
 }
 const superseded=disposition(value.configuration_disposition,s,operation,expected,expectedConfirmation,allowed,grants);
 const ready=value.session?.connected_owner===true&&value.session.owner_status==='READY'&&value.session.configuration_current===true&&value.session.observation_is_current===true;
 return {kind:'CANONICAL_CONFIGURATION',operation_id:operation,conversation_ref:s.conversation_ref,request_sha256:expected,confirmation_sha256:expectedConfirmation,execution_project_id:value.execution_project_id,...(value.configuration_disposition&&!superseded?{configuration_replaced_without_access:true}:{}),...(superseded?{configuration_disposition:superseded}:{}),...(value.configuration_adoption&&allowed(value.configuration_adoption.project_id,grants)?{configuration_adoption:value.configuration_adoption}:{}),conversation_bound:value.conversation_bound===true,owner_ready:ready,status:['FAILED_NOT_STARTED','CANDIDATE_READY','CANDIDATE_CLOSED'].includes(value.status)?value.status:value.conversation_bound===true&&ready?'RETURNED':'UNKNOWN_OUTCOME',owner_started_by_query:false,model_calls_by_query:0,automatic_post_replay:false};
}
module.exports={METHODS,dispatch,requestBody,configuredBody,confirmation};
