import {nativePreview,nativeSnapshot,sameNativeReference,type NativeCollaborationBridge,type NativeContextSnapshot,type NativeDraftAttachment,type NativeInputThumbnail} from './native-collaboration';
type ImageInput=Pick<NativeDraftAttachment,'context_ref'|'preview_sha256'>;
export type InputThumbnail={status:'LOADING'|'READY'|'ERROR';url?:string};
const key=(item:ImageInput)=>JSON.stringify([item.context_ref.id,item.context_ref.revision,item.context_ref.sha256,item.preview_sha256]);
/** Metadata is durable elsewhere. Preview pixels live only in a bounded, scoped cache. */
export class NativeInputPreviews{
 private scope='';private epoch=0;private active=0;private queue:{id:string;epoch:number;run:()=>Promise<void>}[]=[];
 private thumbnails=new Map<string,InputThumbnail>();private disposed=false;private fullFlight?:Promise<void>;
 constructor(private bridge:NativeCollaborationBridge|undefined,private changed:()=>void){}
 update(scope:string){if(scope===this.scope)return;this.scope=scope;this.epoch++;this.queue=[];this.thumbnails.clear();this.changed();}
 get(item:ImageInput){return this.thumbnails.get(key(item));}
 retain(items:readonly ImageInput[]){const wanted=new Set(items.map(key));this.queue=this.queue.filter(entry=>wanted.has(entry.id));let changed=false;for(const id of this.thumbnails.keys())if(!wanted.has(id)){this.thumbnails.delete(id);changed=true;}if(changed)this.changed();}
 request(item:ImageInput,retry=false){
  if(!item.preview_sha256||this.disposed)return;
  const id=key(item);if(this.thumbnails.has(id)&&(!retry||this.thumbnails.get(id)?.status==='LOADING'))return;
  const epoch=this.epoch;
  if(!this.thumbnails.has(id)&&this.thumbnails.size>=12)return; // Overflow stays an explicit original-image action; never churn visible previews.
  const entry:InputThumbnail={status:'LOADING'};this.thumbnails.set(id,entry);this.changed();
  this.queue.push({id,epoch,run:async()=>{
   try{if(!this.bridge?.getNativeInputPreview)throw Error('INPUT_PREVIEW_UNAVAILABLE');
    const value=await this.bounded(this.bridge.getNativeInputPreview({contextRef:item.context_ref,imageSha256:item.preview_sha256!,view:'THUMBNAIL'})) as NativeInputThumbnail;
    if(!sameNativeReference(value.context_ref,item.context_ref)||value.image_sha256!==item.preview_sha256||value.automatic_input_authorized!==false||typeof value.data_url!=='string'||value.data_url.length>720000||!/^data:image\/png;base64,[A-Za-z0-9+/]+=*$/.test(value.data_url))throw Error('INPUT_PREVIEW_SOURCE_CHANGED');
    if(!this.disposed&&epoch===this.epoch&&this.thumbnails.get(id)===entry){this.thumbnails.set(id,{status:'READY',url:value.data_url});this.changed();}
   }catch{if(!this.disposed&&epoch===this.epoch&&this.thumbnails.get(id)===entry){this.thumbnails.set(id,{status:'ERROR'});this.changed();}}
  }});this.pump();
 }
 async full(item:ImageInput,accept:()=>boolean=()=>true):Promise<NativeContextSnapshot>{
  if(!item.preview_sha256||!this.bridge?.getNativeInputPreview)throw Error('INPUT_PREVIEW_UNAVAILABLE');
  const epoch=this.epoch,valid=()=>!this.disposed&&epoch===this.epoch&&accept();
  while(this.fullFlight){await this.fullFlight;if(!valid())throw Error('INPUT_PREVIEW_SCOPE_CHANGED');}
  if(!valid())throw Error('INPUT_PREVIEW_SCOPE_CHANGED');
  let release!:()=>void;const flight=new Promise<void>(resolve=>{release=resolve;});this.fullFlight=flight;
  try{const value=nativeSnapshot(await this.bounded(this.bridge.getNativeInputPreview({contextRef:item.context_ref,imageSha256:item.preview_sha256,view:'FULL'})));
   if(!valid())throw Error('INPUT_PREVIEW_SCOPE_CHANGED');
   if(!sameNativeReference(value.observation.context_ref,item.context_ref)||value.preview?.image_ref?.sha256!==item.preview_sha256||!nativePreview(value)||value.current!==false||value.automatic_input_authorized!==false)throw Error('INPUT_PREVIEW_SOURCE_CHANGED');
   return value;
  }finally{if(this.fullFlight===flight)this.fullFlight=undefined;release();}
 }
 private pump(){while(!this.disposed&&this.active<2&&this.queue.length){const next=this.queue.shift()!;if(next.epoch!==this.epoch)continue;this.active++;void next.run().finally(()=>{this.active--;this.pump();});}}
 private async bounded<T>(promise:Promise<T>):Promise<T>{let timer:ReturnType<typeof setTimeout>|undefined;try{return await Promise.race([promise,new Promise<never>((_resolve,reject)=>{timer=setTimeout(()=>reject(Error('INPUT_PREVIEW_TIMEOUT')),16000);})]);}finally{if(timer!==undefined)clearTimeout(timer);}}
 dispose(){this.disposed=true;this.epoch++;this.queue=[];this.thumbnails.clear();}
}
