import type {SurfaceRef,WorkspaceSurface} from './workspace-surface';
export type DesktopExecutionRef={project_id:string;execution_id:string;request_sha256:string;task_ref?:SurfaceRef};
export type DesktopControl={owner:'USER'|'AGENT';can_takeover?:boolean;control_epoch:number;control_ref:SurfaceRef;surface_ref:SurfaceRef|null;input_epoch:number|null;lease_ref:SurfaceRef|null;capabilities:{delegation_ready:boolean;desktop_input_routes_qualified:boolean;reason?:string};queued_actions_enabled:boolean;source_current:boolean;cancellation?:{status?:string;execution_id?:string;[key:string]:unknown};[key:string]:unknown};
export type DelegateDesktopBody={operation_id:string;surface_ref:SurfaceRef;expected_input_epoch:number;expected_control_epoch:number;execution_ref:DesktopExecutionRef;user_requested_delegation:true};
export type TakeoverDesktopBody={operation_id:string;expected_control_epoch:number;surface_ref?:SurfaceRef;expected_input_epoch?:number};
export type DesktopControlBridge={version:1;getDesktopControl():Promise<DesktopControl>;delegateDesktopControl(body:DelegateDesktopBody):Promise<DesktopControl>;takeoverDesktopControl(body:TakeoverDesktopBody):Promise<DesktopControl>};
export type DesktopControlSnapshot={control?:DesktopControl;pending?:'DELEGATE'|'TAKEOVER'|'REFRESH';error?:string};
export function desktopControlBridge(host:unknown):DesktopControlBridge|undefined{const value=(host as {eeasDesktop?:Partial<DesktopControlBridge>})?.eeasDesktop;return value?.version===1&&['getDesktopControl','delegateDesktopControl','takeoverDesktopControl'].every(key=>typeof value[key as keyof DesktopControlBridge]==='function')?value as DesktopControlBridge:undefined;}
const hash=/^[a-f0-9]{64}$/;
function sameRef(a?:SurfaceRef|null,b?:SurfaceRef|null){return !!a&&!!b&&a.id===b.id&&a.revision===b.revision&&a.sha256===b.sha256;}
function validRef(value?:SurfaceRef|null){return !!value&&hash.test(value.id)&&Number.isSafeInteger(value.revision)&&value.revision>0&&hash.test(value.sha256);}
function validView(value:DesktopControl){if(value.current===false)throw new Error('历史控制回执已失效，需要刷新当前归属');if(!['USER','AGENT'].includes(value.owner)||!Number.isSafeInteger(value.control_epoch)||value.control_epoch<1||!validRef(value.control_ref)||typeof value.capabilities?.delegation_ready!=='boolean'||typeof value.capabilities?.desktop_input_routes_qualified!=='boolean'||typeof value.source_current!=='boolean'||typeof value.queued_actions_enabled!=='boolean')throw new Error('桌面控制状态回执不完整');return value;}
/** Held/revoked authority is backend evidence; an issued pending delegation
 * also allows user withdrawal, without declaring Agent ownership. */
export function takeoverAvailable(value?:DesktopControl,pending?:DesktopControlSnapshot['pending']){return value?.can_takeover===true||pending==='DELEGATE';}
export function delegationReason(value?:DesktopControl,surface?:WorkspaceSurface,execution?:DesktopExecutionRef){
 if(!value)return '桌面控制接口尚未连接，当前由用户操作';
 if(!value.capabilities.delegation_ready||!value.capabilities.desktop_input_routes_qualified)return value.capabilities.reason??'桌面输入通路尚未完成真实资格验证';
 if(surface?.status!=='ACTIVE'||!sameRef(value.surface_ref,surface.surface_ref)||value.input_epoch!==surface.input.epoch||!value.source_current)return '当前工作面身份或输入版本尚未同步';
 if(!surface.input.enabled)return '当前交互面板已暂停下层电脑输入';
 if(!execution?.project_id||!execution.execution_id||!hash.test(execution.request_sha256))return '尚未取得可授权的真实 Agent 执行与请求来源';
 return '';
}
/** Only guarded main-process IPC can change an actual lease. No browser fetch,
 * OS input synthesis, local owner persistence or automatic model request. */
export class DesktopControlController{
 private source?:WorkspaceSurface;private generation=0;private request=0;private stopped=false;private value:DesktopControlSnapshot={};
 private bridge:DesktopControlBridge;private changed:(value:DesktopControlSnapshot)=>void;
 constructor(bridge:DesktopControlBridge,changed:(value:DesktopControlSnapshot)=>void){this.bridge=bridge;this.changed=changed;}
 snapshot(){return this.value;}
 setSource(surface?:WorkspaceSurface){const before=JSON.stringify([this.source?.surface_ref,this.source?.session_ref,this.source?.input.epoch]);this.source=surface;const after=JSON.stringify([surface?.surface_ref,surface?.session_ref,surface?.input.epoch]);if(before!==after){this.generation++;this.publish({...this.value,pending:undefined});}}
 stop(){this.stopped=true;this.generation++;}
 private publish(value:DesktopControlSnapshot){if(this.stopped)return;this.value=value;this.changed(value);}
 async refresh(){if(this.stopped||this.value.pending)return;const generation=this.generation,request=++this.request;this.publish({...this.value,pending:'REFRESH',error:undefined});try{const view=validView(await this.bridge.getDesktopControl());if(generation===this.generation&&request===this.request)this.publish({control:view});}catch(error){if(generation===this.generation&&request===this.request)this.publish({...this.value,pending:undefined,error:error instanceof Error?error.message:String(error)});}}
 async delegate(operationId:string,execution?:DesktopExecutionRef){if(this.stopped||this.value.pending)return;const control=this.value.control,reason=delegationReason(control,this.source,execution);if(reason)throw new Error(reason);const generation=this.generation,request=++this.request;this.publish({...this.value,pending:'DELEGATE',error:undefined});try{const view=validView(await this.bridge.delegateDesktopControl({operation_id:operationId,surface_ref:control!.surface_ref!,expected_input_epoch:control!.input_epoch!,expected_control_epoch:control!.control_epoch,execution_ref:execution!,user_requested_delegation:true}));if(generation===this.generation&&request===this.request)this.publish({control:view});}catch(error){await this.failedMutation(error,generation,request);}}
 async takeover(operationId:string){if(this.stopped||this.value.pending==='TAKEOVER')return;const generation=this.generation,request=++this.request;this.publish({...this.value,pending:'TAKEOVER',error:undefined});try{const control=validView(await this.bridge.getDesktopControl());if(this.stopped||generation!==this.generation||request!==this.request)return;const view=validView(await this.bridge.takeoverDesktopControl({operation_id:operationId,expected_control_epoch:control.control_epoch,...(control.surface_ref&&Number.isSafeInteger(control.input_epoch)?{surface_ref:control.surface_ref,expected_input_epoch:control.input_epoch!}:{})}));if(generation===this.generation&&request===this.request)this.publish({control:view});}catch(error){await this.failedMutation(error,generation,request);}}
 private async failedMutation(error:unknown,generation:number,request:number){let control=this.value.control;try{const current=validView(await this.bridge.getDesktopControl());control=current;}catch{/* Preserve the original mutation error and last confirmed receipt. */}if(!this.stopped&&generation===this.generation&&request===this.request)this.publish({control,error:error instanceof Error?error.message:String(error)});}
}
