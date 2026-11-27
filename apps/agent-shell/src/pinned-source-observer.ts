import { api, ApiError, type ApiClient } from './api.ts';
import type { SourceRef } from './contracts';
import { exactFileSource, sameSelectedFile } from './companion-files.ts';
import { deliverySource } from './planning-delivery.ts';
export type PinnedObservation={pinned_ref:SourceRef;status:'CURRENT'|'STALE'|'MISSING'|'BINDING_CHANGED'|'NOT_RECHECKED';current_ref?:SourceRef;observed_at?:string;reason?:string};
const key=(ref:SourceRef|undefined)=>ref?JSON.stringify([ref.project_id,ref.workspace_id,ref.binding_revision,ref.binding_sha256,ref.path,ref.revision,ref.sha256]):'';
/** Observe a pinned reference by GET. Never adopt it, grant scope, or dispatch. */
export class PinnedSourceObserver{
 private client:Pick<ApiClient,'file'>;private receive:(value:PinnedObservation|undefined)=>void;private ref?:SourceRef;private generation=0;private controller?:AbortController;private stopped=false;
 constructor(receive:(value:PinnedObservation|undefined)=>void,client:Pick<ApiClient,'file'>=api){this.receive=receive;this.client=client;}
 update(ref:SourceRef|undefined){if(this.stopped||key(ref)===key(this.ref))return;this.ref=ref;this.generation++;this.controller?.abort();if(!ref){this.receive(undefined);return;}this.receive({pinned_ref:ref,status:'NOT_RECHECKED',reason:'正在读取实际当前原件'});void this.refresh();}
 async refresh(){if(this.stopped||!this.ref)return;const ref=this.ref,epoch=++this.generation;this.controller?.abort();const controller=new AbortController();this.controller=controller;try{const actual=await this.client.file(ref.project_id,ref.workspace_id,ref.path,controller.signal);if(this.stopped||controller.signal.aborted||epoch!==this.generation)return;if(!actual.current||!deliverySource(actual.source_ref)||!sameSelectedFile(actual.source_ref,ref))throw new Error('当前原件未返回匹配的真实文件身份');const current=actual.source_ref;this.receive({pinned_ref:ref,current_ref:current,observed_at:new Date().toISOString(),status:current.binding_revision!==ref.binding_revision||current.binding_sha256!==ref.binding_sha256?'BINDING_CHANGED':exactFileSource(current,ref)?'CURRENT':'STALE'});}catch(error){if(this.stopped||controller.signal.aborted||epoch!==this.generation)return;this.receive({pinned_ref:ref,status:error instanceof ApiError&&error.status===404?'MISSING':'NOT_RECHECKED',reason:error instanceof Error?error.message:String(error)});}}
 stop(){this.stopped=true;this.generation++;this.controller?.abort();}
}
export function pinnedObservationLabel(ref:SourceRef,observation:PinnedObservation|undefined,translate:(message:string)=>string=message=>message){
 const prefix=`${translate('固定引用')} v${ref.revision} · `;
 if(!observation||!exactFileSource(observation.pinned_ref,ref))return prefix+translate('当前原件未核对');
 if(observation.status==='STALE')return prefix+translate('原件已变')+(observation.current_ref?`${translate('，读取时为 v')}${observation.current_ref.revision}`:'');
 if(observation.status==='CURRENT')return prefix+translate('读取时原件一致');
 if(observation.status==='MISSING')return prefix+translate('原件缺失');
 if(observation.status==='BINDING_CHANGED')return prefix+translate('目录绑定已变');
 return prefix+translate('当前原件未核对');
}
