import {ApiClient,ApiError,api,operationId} from './api.ts';
import type {ProviderProfile,SourceRef} from './contracts';
export type InlineQuestion={id:string;text:string;reason:string;choices?:string[]};
export type DirectAction={project_id:string;operation_id:string;status:'PROCESSING'|'CANCEL_REQUESTED'|'NEEDS_INPUT'|'READY_NOT_AUTHORIZED'|'APPLIED'|'NO_CHANGE'|'FAILED'|'CANCELLED'|'UNKNOWN_OUTCOME';interaction:{mode:'NONE'|'INLINE_QUESTION';questions:InlineQuestion[]};before_ref?:SourceRef|null;after_ref?:SourceRef|null;summary?:string;diff?:string;write_performed?:boolean;current?:boolean|null;replayed:boolean;model_calls?:number;error?:{type:string;message:string};execution?:{id:string;execution_status:string;session_id:string|null};automatic_retry?:false};
export class DirectActionApi{
 private client:ApiClient;constructor(client:ApiClient=api){this.client=client;}
 private async outcome(request:Promise<DirectAction>,pid:string,key:string){try{return await request;}catch(error){if(error instanceof ApiError&&error.detail&&typeof error.detail==='object'){const value=error.detail as DirectAction;if(value.project_id===pid&&value.operation_id===key&&['FAILED','UNKNOWN_OUTCOME'].includes(value.status))return value;}throw error;}}
 path(pid:string){return this.client.projectPath(pid)+'/direct-actions';}
 run(pid:string,instruction:string,options:{file?:SourceRef;write:boolean;profile?:ProviderProfile},key:string=operationId()){return this.outcome(this.client.request<DirectAction>(this.path(pid),'POST',{operation_id:key,instruction,user_requested_write:options.write,...options.file?{file_ref:options.file}:{},...options.profile?{provider_profile_ref:options.profile.profile_ref}:{},budget:{max_calls:1,timeout_seconds:120,max_output_tokens:12000}}),pid,key);}
 get(pid:string,key:string,signal?:AbortSignal){return this.client.request<DirectAction>(this.path(pid)+'/'+encodeURIComponent(key),'GET',undefined,signal);}
 events(pid:string,key:string,signal?:AbortSignal){return this.client.request<{events:unknown[];native_events:unknown[]}>(this.path(pid)+'/'+encodeURIComponent(key)+'/events','GET',undefined,signal);}
 cancel(pid:string,key:string){return this.client.request<DirectAction>(this.path(pid)+'/'+encodeURIComponent(key)+'/cancel','POST');}
}
export const directApi=new DirectActionApi();
