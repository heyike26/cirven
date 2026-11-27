import type {ArtifactEnvelope,Scope} from './contracts';
import type {DiscoverySession} from './discovery-contracts';
import {panelMode,type InteractionMode,type InteractionRequest} from './interaction-policy.ts';
export type InteractionPanel={key:string;request:InteractionRequest;artifact:ArtifactEnvelope;visible:boolean;height:number;position:{x:number;y:number};updated_at:number};
export const panelKey=(artifact:ArtifactEnvelope)=>JSON.stringify([artifact.scope.projectId,artifact.scope.sessionId??'',artifact.type,artifact.artifactId,artifact.revision,artifact.contentHash??'']);
function sameJSON(a:unknown,b:unknown,depth=0):boolean{
 if(a===b)return true;if(depth>64||!a||!b||typeof a!=='object'||typeof b!=='object'||Array.isArray(a)!==Array.isArray(b))return false;
 const keys=Object.keys(a);return keys.length===Object.keys(b).length&&keys.every(key=>Object.hasOwn(b,key)&&sameJSON((a as Record<string,unknown>)[key],(b as Record<string,unknown>)[key],depth+1));
}
function discoveryResult(artifact:ArtifactEnvelope,result:unknown):DiscoverySession|undefined{
 const value=result as DiscoverySession|undefined;
 if(artifact.type!=='discovery'||!value||value.id!==artifact.artifactId||value.project_id!==artifact.scope.projectId||!Number.isSafeInteger(value.revision)||!Number.isSafeInteger(value.current_revision)||value.current_revision<value.revision||!/^[a-f0-9]{64}$/.test(value.sha256??'')||!value.payload||!Array.isArray(value.answers)||typeof value.source_current!=='boolean'||!value.readiness||value.readiness.execution_authorized!==false)return;
 return value;
}
/** Same immutable basis, fresh server observations. Payload/candidate refs and
 * all draft/view state stay owned by the existing mounted exact-version pane. */
export function observeArtifactResult(artifact:ArtifactEnvelope,result:unknown):ArtifactEnvelope|undefined{
 const value=discoveryResult(artifact,result),old=artifact.data as DiscoverySession;
 if(!value||!old||!old.payload||!Array.isArray(old.answers)||value.revision!==artifact.revision||value.sha256!==artifact.contentHash||!sameJSON(old.payload,value.payload))return;
 const eligible=old.payload.status==='CONFIRMED'&&value.source_current;
 if(value.readiness.requirements_confirmed!==eligible||value.readiness.can_plan&&!eligible)return;
 if(value.answers.some(answer=>!answer||typeof answer.id!=='string'||typeof answer.question_id!=='string'||typeof answer.text!=='string'))return;
 // Saved answers are append-only at this immutable basis. A late observation
 // cannot make a previously observed answer disappear.
 const answers=new Map(old.answers.map(answer=>[answer.id,answer]));for(const answer of value.answers)if(!answers.has(answer.id))answers.set(answer.id,answer);
 const data={...old,answers:[...answers.values()],source_current:value.source_current,readiness:value.readiness,current_revision:Math.max(old.current_revision,value.current_revision)};
 if(sameJSON(old,data))return;
 return {...artifact,data};
}
/** Confirmation is a new immutable version, never an in-place payload merge. */
export function confirmedArtifactResult(artifact:ArtifactEnvelope,result:unknown):ArtifactEnvelope|undefined{
 const value=discoveryResult(artifact,result),old=artifact.data as DiscoverySession,confirmation=(value?.payload as DiscoverySession['payload']&{confirmation?:{basis_revision?:number;basis_sha256?:string;execution_authorized?:boolean}}|undefined)?.confirmation;
 if(!value||!old?.payload||value.revision<=artifact.revision||value.sha256===artifact.contentHash||value.payload.status!=='CONFIRMED'||value.readiness.requirements_confirmed!==value.source_current||value.readiness.can_plan&&!value.source_current||confirmation?.basis_revision!==artifact.revision||confirmation?.basis_sha256!==artifact.contentHash||confirmation?.execution_authorized!==false||!sameJSON(old.payload.candidate,value.payload.candidate)||!sameJSON(old.payload.source_receipt,value.payload.source_receipt)||!sameJSON(old.payload.workspace_ref,value.payload.workspace_ref))return;
 return {...artifact,revision:value.revision,contentHash:value.sha256,data:value};
}
/** Stable keys keep each renderer mounted while hidden: unsent answers, selection,
 * graph viewport and annotations are retained rather than reconstructed. */
export class InteractionState{
 readonly records=new Map<string,InteractionPanel>();private scope:Scope={projectId:''};private epoch=0;private changed:()=>void;
 constructor(changed:()=>void){this.changed=changed;}
 owner(scope:Scope){if(this.scope.projectId===scope.projectId&&this.scope.workspaceId===scope.workspaceId&&this.scope.sessionId===scope.sessionId)return;this.scope={...scope};this.epoch++;for(const panel of this.records.values())panel.visible=false;this.changed();}
 lease(){const epoch=this.epoch;return()=>epoch===this.epoch;}
 request(artifact:ArtifactEnvelope,mode:InteractionMode,reason:string,explicit:boolean,lease?:()=>boolean){
  if(!panelMode(mode)||lease&&!lease()||artifact.scope.projectId!==this.scope.projectId||artifact.scope.sessionId&&artifact.scope.sessionId!==this.scope.sessionId)return;
  const key=panelKey(artifact),old=this.records.get(key);if(!explicit&&[...this.records.values()].some(item=>item.artifact.scope.projectId===artifact.scope.projectId&&item.artifact.scope.sessionId===artifact.scope.sessionId&&item.artifact.type===artifact.type&&item.artifact.artifactId===artifact.artifactId&&(item.request.status==='DISMISSED'||item.artifact.revision!==artifact.revision||item.artifact.contentHash!==artifact.contentHash)))return;
  if(mode==='FULL_WORKSPACE')for(const item of this.records.values())if(item.request.mode==='FULL_WORKSPACE')item.visible=false;
  const request:InteractionRequest={id:old?.request.id??crypto.randomUUID(),tool_id:'eeas.'+artifact.type,artifact_ref:{id:artifact.artifactId,revision:artifact.revision,sha256:artifact.contentHash},reason,mode,required:false,status:'DISPLAYED'};
  const value:InteractionPanel={key,artifact,request,visible:true,height:old?.height??540,position:old?.position??{x:24,y:24},updated_at:Date.now()};this.records.set(key,value);this.changed();return key;
 }
 observe(target:ArtifactEnvelope,result:unknown){
  if(target.scope.projectId!==this.scope.projectId||target.scope.sessionId&&target.scope.sessionId!==this.scope.sessionId)return;
  const item=this.records.get(panelKey(target));if(!item)return;const artifact=observeArtifactResult(item.artifact,result);if(!artifact)return;
  item.artifact=artifact;this.changed();
 }
 collapse(key:string){const item=this.records.get(key);if(!item)return;item.visible=false;item.request.status='DISMISSED';this.changed();}
 expand(key:string){const item=this.records.get(key);if(!item)return;return this.request(item.artifact,item.request.mode,item.request.reason,true);}
 resize(key:string,height:number){const item=this.records.get(key);if(!item||!Number.isFinite(height))return;item.height=Math.max(220,Math.min(1400,Math.round(height)));this.changed();}
 move(key:string,position:{x:number;y:number}){const item=this.records.get(key);if(!item||![position.x,position.y].every(Number.isFinite))return;item.position={x:Math.max(0,Math.round(position.x)),y:Math.max(0,Math.round(position.y))};this.changed();}
 current(){return [...this.records.values()].filter(item=>item.artifact.scope.projectId===this.scope.projectId&&(!item.artifact.scope.sessionId||item.artifact.scope.sessionId===this.scope.sessionId));}
 blocking(){return this.current().find(item=>item.visible&&item.request.mode==='FULL_WORKSPACE');}
}
