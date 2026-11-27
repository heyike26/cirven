import type {AgentSession,PreparedSessionOpen,SessionOpenOptions} from './agent-sessions.ts';
import {nativeReference,type NativeRef,type NativeConversationRef} from './native-collaboration.ts';

export type ConfigurationOwner={project_id:string;session_ref:NativeRef;generation:number};
export type ConfigurationPreparation={
 kind:'CONVERSATION_CONFIGURATION_PREPARATION';
 authority:{version:1;conversation_ref:NativeConversationRef;project_id:string|null;original_source:ConfigurationOwner|null;original_request_sha256:string|null;source_authority:unknown;capability_binding_sha256:string;workspace_catalog_sha256:string;tools_catalog_sha256:string};
 preparation_sha256:string;original_request:Record<string,unknown>|null;
 workspaces:{project_id:string;workspace_ref:{id:string;revision:number;binding_sha256:string};label:string;allowed_write_paths:string[]}[];
 default_execution_project_id:string|null;new_owner_capacity:number;
 read_only:true;model_calls:0;owner_started:false;permission_granted:false;automatic_message_replay:false;
};
export type ConfigurationRequest={request:PreparedSessionOpen;preparation_sha256:string;original_source:ConfigurationOwner|null;execution_project_id:string|null;confirmation_sha256:string};
const ID=/^[A-Za-z0-9_-]{1,100}$/,SHA=/^[a-f0-9]{64}$/,CID=/^(native|original|discovery):[a-f0-9]{64}$/;
export const configurationCanonical=(v:unknown):string=>Array.isArray(v)?'['+v.map(configurationCanonical).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+configurationCanonical((v as Record<string,unknown>)[k])).join(',')+'}':JSON.stringify(v);
export async function configurationHash(v:unknown){return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(configurationCanonical(v))))].map(n=>n.toString(16).padStart(2,'0')).join('');}
const check=(value:unknown)=>{if(!value)throw Error('CONNECTION_CONFIGURATION_CHANGED');};
const same=(a:unknown,b:unknown)=>configurationCanonical(a)===configurationCanonical(b);
const pid=(v:unknown)=>v===null||typeof v==='string'&&ID.test(v);
export function configurationOwner(value:Pick<AgentSession,'project_id'|'session_ref'|'generation'>|undefined):ConfigurationOwner|null{return value?{project_id:value.project_id,session_ref:{...value.session_ref},generation:value.generation}:null;}
export function validConfigurationOwner(v:unknown){if(v===null)return true;const o=v as ConfigurationOwner;return !!o&&typeof o.project_id==='string'&&ID.test(o.project_id)&&nativeReference(o.session_ref)&&Number.isSafeInteger(o.generation)&&o.generation>=0;}
export async function parseConfigurationPreparation(value:unknown,scope:NativeConversationRef,projectId:string|null,original?:ConfigurationOwner|null){
 const v=value as ConfigurationPreparation;check(v?.kind==='CONVERSATION_CONFIGURATION_PREPARATION'&&v.authority?.version===1&&same(v.authority.conversation_ref,scope)&&v.authority.project_id===projectId&&validConfigurationOwner(v.authority.original_source));
 check(original===undefined||same(v.authority.original_source,original));check(SHA.test(v.authority.capability_binding_sha256)&&SHA.test(v.preparation_sha256)&&await configurationHash(v.authority)===v.preparation_sha256);
 check(v.read_only===true&&v.model_calls===0&&v.owner_started===false&&v.permission_granted===false&&v.automatic_message_replay===false&&pid(v.default_execution_project_id)&&Number.isSafeInteger(v.new_owner_capacity)&&v.new_owner_capacity>=0);
 check(Array.isArray(v.workspaces)&&v.workspaces.length<=128&&v.workspaces.every(row=>typeof row.project_id==='string'&&ID.test(row.project_id)&&SHA.test(row.workspace_ref?.id)&&SHA.test(row.workspace_ref.binding_sha256)&&Number.isSafeInteger(row.workspace_ref.revision)&&row.workspace_ref.revision>=1));
 check(v.original_request===null||typeof v.original_request==='object'&&!Array.isArray(v.original_request));check(v.authority.original_request_sha256===(v.original_request===null?null:await configurationHash(v.original_request)));check(new TextEncoder().encode(JSON.stringify(v)).length<=524288);return structuredClone(v);
}
export async function validConfigurationRequest(value:unknown,request?:PreparedSessionOpen){try{
 const p=value as ConfigurationRequest,r=p?.request;check(p&&r&&Object.keys(p).sort().join(',')==='confirmation_sha256,execution_project_id,original_source,preparation_sha256,request'&&ID.test(r.operation_id)&&CID.test(r.conversation_ref?.id??'')&&Number.isSafeInteger(r.conversation_ref?.revision)&&Number(r.conversation_ref?.revision)>=1&&pid(r.project_id)&&pid(p.execution_project_id)&&validConfigurationOwner(p.original_source));
 check(!request||same(r,request));check(SHA.test(p.preparation_sha256)&&SHA.test(p.confirmation_sha256)&&nativeReference(r.provider_profile_ref)&&SHA.test(r.capability_binding_sha256));
 check(p.confirmation_sha256===await configurationHash({request:r,preparation_sha256:p.preparation_sha256,execution_project_id:p.execution_project_id}));return true;
 }catch{return false;}}
/** Current model changes retain the full original execution declaration.
 * Explicit settings use their own frozen options. No model or permission is
 * selected by this helper; the server rechecks all refs before retiring work. */
export async function configurationRequest(prepared:ConfigurationPreparation,base:PreparedSessionOpen,options:SessionOpenOptions):Promise<ConfigurationRequest>{
 check(base.conversation_ref&&same(base.conversation_ref,prepared.authority.conversation_ref)&&base.project_id===prepared.authority.project_id&&base.capability_binding_sha256===prepared.authority.capability_binding_sha256&&prepared.new_owner_capacity>0);
 const prior=options.preserveExecutionScope?prepared.original_request:null;
 const request={...base,...(prior??{}),operation_id:base.operation_id,project_id:base.project_id,conversation_ref:base.conversation_ref,provider_profile_ref:base.provider_profile_ref,capability_binding_sha256:base.capability_binding_sha256} as PreparedSessionOpen;
 const execution_project_id=prior?prepared.default_execution_project_id:options.registeredWorkspace?.project_id??options.workspace?.project_id??options.executionProjectId??prepared.default_execution_project_id;
 if(request.workspace_ref!==null)check(prepared.workspaces.some(row=>row.project_id===execution_project_id&&same(row.workspace_ref,request.workspace_ref)));
 const result={request,preparation_sha256:prepared.preparation_sha256,original_source:structuredClone(prepared.authority.original_source),execution_project_id,confirmation_sha256:await configurationHash({request,preparation_sha256:prepared.preparation_sha256,execution_project_id})};
 check(await validConfigurationRequest(result));return result;
}
