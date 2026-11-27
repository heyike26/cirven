import type {ManualCaptureRequest,ManualCapture,ManualCaptureRef,ManualOperationReceipt,ManualWorkspace} from './manual-harness';
import {createSignal} from 'solid-js';
import {api,operationId,type ApiClient} from './api.ts';
import type {SourceRef} from './contracts';
import type {DiscoveryCandidate,DiscoveryOperation,DiscoverySession,ProviderRef} from './discovery-contracts';
import type {Workspace} from './contracts';
import {workspaceRef} from './discovery-api.ts';
import type {Annotation} from './annotations';
import type {TextAnnotation} from './text-feedback';
import type {BitmapSubmission} from './BitmapFeedbackEditor';
import type {BitmapRef} from './native-document';
import {validateOCRObservation,type OCRCapabilities,type OCRObservation} from './ocr.ts';
import type {CaptureInput,CompetitionCapture,DiagramArtifact,FeedbackReceipt,FeedbackView,RevisionRef,WordResult,WritingProfile,WritingRule,WritingSource,WritingInventory,WritingImportRequest,WritingSummaryInventory,WritingOperationReceipt} from './workbench-contracts';
export class WorkbenchApi{
  private readonly client:ApiClient;
  constructor(client:ApiClient=api){this.client=client;}
  private readonly profileVersion=createSignal<Record<string,number>>({});
  private readonly summaryReads=new Map<string,{version:number;promise:Promise<WritingSummaryInventory>}>();
  private readonly profileReads=new Map<string,{version:number;promise:Promise<WritingInventory>}>();
  profileInventoryVersion(pid:string){return this.profileVersion[0]()[pid]??0;}
  private changedProfiles(pid:string){this.profileVersion[1](values=>({...values,[pid]:(values[pid]??0)+1}));}
  profilesRoot(pid:string){return `${this.client.projectPath(pid)}/template-profiles`;}
  profileInventory(pid:string,signal?:AbortSignal):Promise<WritingInventory>{
    const version=this.profileInventoryVersion(pid),pending=this.profileReads.get(pid);
    if(!signal&&pending?.version===version)return pending.promise;
    const promise=this.client.request<WritingInventory>(this.profilesRoot(pid),'GET',undefined,signal).then(value=>({profiles:value.profiles,pending_imports:value.pending_imports??[]}));
    if(!signal){const read={version,promise};this.profileReads.set(pid,read);void promise.finally(()=>{if(this.profileReads.get(pid)===read)this.profileReads.delete(pid);}).catch(()=>{});}
    return promise;
  }
  async writingOperation(pid:string,id:string,requestSha:string){const result=await this.client.request<WritingOperationReceipt>(`${this.profilesRoot(pid)}/operations/${encodeURIComponent(id)}?request_sha256=${encodeURIComponent(requestSha)}`);if(result.status==='COMPLETED')this.changedProfiles(pid);return result;}
  profileSummaries(pid:string,signal?:AbortSignal){const version=this.profileInventoryVersion(pid),pending=this.summaryReads.get(pid);if(!signal&&pending?.version===version)return pending.promise;const promise=this.client.request<WritingSummaryInventory>(this.profilesRoot(pid)+'?summaries=true','GET',undefined,signal);if(!signal){const read={version,promise};this.summaryReads.set(pid,read);void promise.finally(()=>{if(this.summaryReads.get(pid)===read)this.summaryReads.delete(pid);}).catch(()=>{});}return promise;}
  async profiles(pid:string,signal?:AbortSignal){return(await this.profileInventory(pid,signal)).profiles;}
  profile(pid:string,id:string,signal?:AbortSignal,revision?:number){return this.client.request<WritingProfile>(`${this.profilesRoot(pid)}/${encodeURIComponent(id)}${revision===undefined?'':'?revision='+revision}`,'GET',undefined,signal);}
  async createProfile(pid:string,name:string,sources:WritingSource[],key=operationId()){const result=await this.client.request<WritingProfile>(this.profilesRoot(pid),'POST',{operation_id:key,name,sources});this.changedProfiles(pid);return result;}
  async importProfile(pid:string,request:WritingImportRequest){const result=await this.client.request<WritingProfile>(`${this.profilesRoot(pid)}/imports`,'POST',request,undefined,false);this.changedProfiles(pid);return result;}
  async reviewProfile(profile:Pick<WritingProfile,'project_id'|'id'|'revision'>,ids:string[],reason:string,edits:WritingRule[]=[],key:string=operationId()){const result=await this.client.request<WritingProfile>(`${this.profilesRoot(profile.project_id)}/${encodeURIComponent(profile.id)}/review`,'POST',{operation_id:key,expected_revision:profile.revision,accepted_rule_ids:ids,reason,edits},undefined,false);this.changedProfiles(profile.project_id);return result;}
  manualRoot(pid:string,id:string){return `${this.client.projectPath(pid)}/planning/proposals/${encodeURIComponent(id)}/harness/manual-changes`;}
  inspectManual(pid:string,id:string,target:RevisionRef,workspace:ManualWorkspace){return this.client.request<unknown>(`${this.manualRoot(pid,id)}?revision=${target.revision}&sha256=${encodeURIComponent(target.sha256)}&workspace_id=${encodeURIComponent(workspace.id)}&binding_revision=${workspace.revision}&binding_sha256=${encodeURIComponent(workspace.binding_sha256)}`);}
  captureManual(pid:string,id:string,request:ManualCaptureRequest){return this.client.request<ManualCapture>(this.manualRoot(pid,id),'POST',request,undefined,false);}
  manualOperation(pid:string,id:string,key:string,requestSha:string){return this.client.request<ManualOperationReceipt>(`${this.manualRoot(pid,id)}/operations/${encodeURIComponent(key)}?request_sha256=${encodeURIComponent(requestSha)}`);}
  manualCapture(pid:string,id:string,ref:ManualCaptureRef){return this.client.request<ManualCapture>(`${this.manualRoot(pid,id)}/${encodeURIComponent(ref.id)}?revision=${ref.revision}&sha256=${encodeURIComponent(ref.sha256)}`);}
  capturesRoot(pid:string){return `${this.client.projectPath(pid)}/competition/sources`;}
  async captures(pid:string,signal?:AbortSignal){return(await this.client.request<{sources:CompetitionCapture[]}>(this.capturesRoot(pid),'GET',undefined,signal)).sources;}
  capture(pid:string,input:CaptureInput,key=operationId()){return this.client.request<CompetitionCapture>(this.capturesRoot(pid),'POST',{operation_id:key,...input});}
  snapshotUrl(pid:string,id:string){return `${this.capturesRoot(pid)}/${encodeURIComponent(id)}/snapshot`;}
  sessionRoot(session:DiscoverySession){return `${this.client.projectPath(session.project_id)}/discovery/sessions/${encodeURIComponent(session.id)}`;}
  diagram(session:DiscoverySession,id:string){return this.client.request<DiagramArtifact>(`${this.sessionRoot(session)}/diagrams/${encodeURIComponent(id)}/artifact?revision=${session.revision}`);}
  report(session:DiscoverySession,options:{template?:SourceRef;experience?:RevisionRef;diagrams?:RevisionRef[]}={},key=operationId()){return this.client.request<WordResult>(`${this.sessionRoot(session)}/reports`,'POST',{operation_id:key,expected_revision:session.revision,format:'docx',...options.template?{template_ref:options.template}:{},...options.experience?{experience_ref:options.experience}:{},diagram_refs:options.diagrams??[]});}
  feedbackRoot(pid:string){return `${this.client.projectPath(pid)}/workbench/feedback`;}
  ocrCapabilities(signal?:AbortSignal){return this.client.request<OCRCapabilities>('/api/workbench/ocr/capabilities','GET',undefined,signal);}
  async recognize(pid:string,bitmap:BitmapRef,signal?:AbortSignal){const value=await this.client.request<OCRObservation>(`${this.client.projectPath(pid)}/workbench/ocr`,'POST',{bitmap_ref:bitmap},signal);return validateOCRObservation(value,pid,bitmap);}
  async ocrObservation(pid:string,ref:RevisionRef,bitmap:BitmapRef,signal?:AbortSignal){const value=await this.client.request<OCRObservation>(`${this.client.projectPath(pid)}/workbench/ocr/${encodeURIComponent(ref.id)}`,'GET',undefined,signal);validateOCRObservation(value,pid,bitmap);if(value.observation_ref.id!==ref.id||value.observation_ref.revision!==ref.revision||value.observation_ref.sha256!==ref.sha256)throw new Error('已保存的识别版本凭据不符');return value;}
  async feedbackList(pid:string,id:string){return(await this.client.request<{feedback:FeedbackView[]}>(`${this.feedbackRoot(pid)}?${new URLSearchParams({session_id:id})}`)).feedback;}
  recordFeedback(session:DiscoverySession,value:Annotation|Omit<BitmapSubmission,'delivery'>|Omit<TextAnnotation,'delivery'>|{tool:'TEXT_EDIT';comment:string;edited_candidate:DiscoveryCandidate},key=operationId()){return this.client.request<FeedbackReceipt>(this.feedbackRoot(session.project_id),'POST',{operation_id:key,artifact_ref:{kind:'DISCOVERY',id:session.id,revision:session.revision,sha256:session.sha256},...value});}
  reviseFeedback(pid:string,value:FeedbackView,options:{profile?:ProviderRef;workspace?:Workspace},key=operationId()){return this.client.request<DiscoverySession|DiscoveryOperation>(`${this.feedbackRoot(pid)}/${encodeURIComponent(value.feedback_ref.id)}/revise`,'POST',{operation_id:key,expected_revision:value.payload.revision_target_ref.revision,...options.profile?{provider_profile_ref:options.profile}:{},...options.workspace?{workspace_ref:workspaceRef(options.workspace)}:{}});}
}
export const workbenchApi=new WorkbenchApi();
