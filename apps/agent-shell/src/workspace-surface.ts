export type SurfaceRef={id:string;revision:number;sha256:string};
export type SurfaceBounds={x:number;y:number;width:number;height:number};
export type SurfacePresentation='DESKTOP'|'MODELESS_POPOVER'|'FULL_WORKSPACE'|'DOCUMENT'|'APPLICATION';
export type WorkspaceSurface={status:'WINDOWS_ONLY'|'UNBOUND'|'ACTIVE';surface_ref?:SurfaceRef;session_ref?:SurfaceRef;type:'DESKTOP';capabilities:{native_desktop:boolean|string;implementation:'IMPLEMENTED_NOT_QUALIFIED';modeless_region:boolean|string};input:{epoch:number;enabled:boolean;input_root:'WINDOWS_DESKTOP_REGION'};presentation:SurfacePresentation;fit_policy?:'WINDOWS_WORKAREA';visible_all_verified?:boolean;viewport?:unknown;frame?:unknown;dpi?:unknown;verified_input_blocked?:boolean;receipt?:unknown;reason?:string;error?:{message:string;code?:string}};
export type SurfaceLayout={bounds:SurfaceBounds;presentation:SurfacePresentation;popover_bounds:SurfaceBounds[]};
export type WorkspaceBridge={version:1;getWorkspaceSurface:()=>Promise<WorkspaceSurface>;bindWorkspaceSurface:(bounds:SurfaceBounds)=>Promise<WorkspaceSurface>;updateWorkspaceSurface:(body:SurfaceLayout&{surface_ref:SurfaceRef;expected_input_epoch:number})=>Promise<WorkspaceSurface>};
export function workspaceBridge(host:unknown):WorkspaceBridge|undefined{const bridge=(host as {eeasDesktop?:Partial<WorkspaceBridge>})?.eeasDesktop;return bridge?.version===1&&['getWorkspaceSurface','bindWorkspaceSurface','updateWorkspaceSurface'].every(key=>typeof bridge[key as keyof WorkspaceBridge]==='function')?bridge as WorkspaceBridge:undefined;}
export function surfaceBounds(rect:SurfaceBounds):SurfaceBounds|undefined{if(![rect.x,rect.y,rect.width,rect.height].every(Number.isFinite)||rect.width<32||rect.height<32)return;return{x:rect.x,y:rect.y,width:rect.width,height:rect.height};}
/** Popup CSS bounds remain exact floats; trusted main maps them to physical
 * pixels. No rounding may expand an occluder beyond the measured workspace. */
export function surfaceIntersection(rect:SurfaceBounds,frame:SurfaceBounds):SurfaceBounds|undefined{if(![rect.x,rect.y,rect.width,rect.height,frame.x,frame.y,frame.width,frame.height].every(Number.isFinite)||rect.width<=0||rect.height<=0||frame.width<=0||frame.height<=0)return;const x=Math.max(rect.x,frame.x),y=Math.max(rect.y,frame.y),right=Math.min(rect.x+rect.width,frame.x+frame.width),bottom=Math.min(rect.y+rect.height,frame.y+frame.height);if(right<=x||bottom<=y)return;return{x,y,width:right-x,height:bottom-y};}
/** Native regions, not a screenshot/player or global input synthesizer. The
 * implementation status is preserved; ACTIVE alone does not qualify Windows.
 */
function sameAuthority(a?:WorkspaceSurface,b?:WorkspaceSurface){return !!a&&!!b&&a.input.epoch===b.input.epoch&&a.surface_ref?.id===b.surface_ref?.id&&a.surface_ref?.revision===b.surface_ref?.revision&&a.surface_ref?.sha256===b.surface_ref?.sha256;}
function referenceConflict(error:unknown){const value=error as {message?:string;code?:string};return /stale desktop (?:layout preparation|surface or input epoch)|INPUT_EPOCH_MISMATCH|STALE_SURFACE/i.test(`${value?.code??''} ${value?.message??String(error)}`);}
export class WorkspaceSurfaceController{
 private bridge:WorkspaceBridge;private result:(surface?:WorkspaceSurface,error?:string)=>void;private latest?:SurfaceLayout;private generation=0;private processing=false;private stopped=false;private current?:WorkspaceSurface;private applied='';private referenceRevision=0;private refreshedRevision=0;
 constructor(bridge:WorkspaceBridge,result:(surface?:WorkspaceSurface,error?:string)=>void){this.bridge=bridge;this.result=result;}
 layout(value:SurfaceLayout){this.latest=structuredClone(value);this.generation++;void this.run();}
 /** Read actual host authority, even if geometry is unchanged. No lease/proof
  * is reconstructed from a Control DTO and no unavailable surface is bound. */
 refreshReference(value:SurfaceLayout){this.latest=structuredClone(value);this.referenceRevision++;this.generation++;void this.run();}
 stop(){this.stopped=true;this.generation++;}
 private async run(){if(this.processing||this.stopped)return;this.processing=true;
 try{outer:while(this.latest&&!this.stopped){const generation=this.generation,layout=this.latest,signature=JSON.stringify(layout),refresh=this.referenceRevision!==this.refreshedRevision,referenceRevision=this.referenceRevision;if(signature===this.applied&&!refresh)break;
   if(!this.current||refresh){const read=await this.bridge.getWorkspaceSurface();if(this.stopped)break;if(generation!==this.generation)continue;this.current=read;this.refreshedRevision=referenceRevision;}
   if(this.current.status==='WINDOWS_ONLY'){this.result(this.current);this.applied=signature;break;}
   if(!this.current.surface_ref){if(refresh||layout.presentation!=='DESKTOP'){this.result(this.current);break;}this.current=await this.bridge.bindWorkspaceSurface(layout.bounds);if(this.stopped)break;}
   if(generation!==this.generation)continue;
   if(this.current.status!=='ACTIVE'||!this.current.surface_ref){this.result(this.current);break;}
   if(signature===this.applied&&this.current.presentation===layout.presentation&&(layout.presentation!=='FULL_WORKSPACE'||this.current.verified_input_blocked===true)){this.result(this.current);break;}
   for(let retry=0;;retry++){
    const before=this.current,request={...layout,surface_ref:before.surface_ref!,expected_input_epoch:before.input.epoch};
    try{this.current=await this.bridge.updateWorkspaceSurface(request);}catch(error){
     const read=await this.bridge.getWorkspaceSurface();if(this.stopped)break outer;if(generation!==this.generation){this.current=undefined;continue outer;}
     this.current=read;if(retry!==0||!referenceConflict(error)||sameAuthority(before,read)||read.status!=='ACTIVE'||!read.surface_ref)throw error;
     continue;
    }
    if(this.stopped)break outer;
    if(this.current.presentation!==layout.presentation)throw new Error('宿主工作面展示回执与请求不一致');
    if(layout.presentation==='FULL_WORKSPACE'&&this.current.verified_input_blocked!==true)throw new Error('宿主尚未验证中间电脑输入已阻断');
    this.applied=signature;if(generation!==this.generation)continue outer;this.result(this.current);break outer;
   }
  }}catch(error){if(!this.stopped)this.result(this.current,error instanceof Error?error.message:String(error));}finally{this.processing=false;/* A failed OS operation never starts an unbounded retry loop. */}
 }
}
