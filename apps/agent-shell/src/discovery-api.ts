import { api, operationId, type ApiClient } from './api.ts';
import type { DocumentRef, Workspace } from './contracts';
import type { DiscoveryCandidate, DiscoveryOperation, DiscoveryPhase, DiscoveryPlan, DiscoverySession, DiscoveryRecoveryPage, EntryMode, ProviderRef, Resolution } from './discovery-contracts';
import type {RevisionRef} from './workbench-contracts';

export type PlanWaitSeconds=120|300|600;
export const isPlanWaitSeconds=(value:unknown):value is PlanWaitSeconds=>value===120||value===300||value===600;

export const workspaceRef = (workspace?:Workspace) => workspace ? {id:workspace.id,revision:workspace.revision,binding_sha256:workspace.binding_sha256} : undefined;
export class DiscoveryApi {
  private readonly client:ApiClient;
  constructor(client:ApiClient=api){this.client=client;}
  root(projectId:string){return `${this.client.projectPath(projectId)}/discovery`;}
  path(projectId:string,id:string){return `${this.root(projectId)}/sessions/${encodeURIComponent(id)}`;}
  async list(projectId:string,signal?:AbortSignal){return(await this.client.request<{sessions:DiscoverySession[]}>(`${this.root(projectId)}/sessions`,'GET',undefined,signal)).sessions;}
  get(projectId:string,id:string,signal?:AbortSignal){return this.client.request<DiscoverySession>(this.path(projectId,id),'GET',undefined,signal);}
  create(projectId:string,value:{title:string;entry_mode:EntryMode;statement:string;materials:DocumentRef[];workspace?:Workspace},key=operationId()){return this.client.request<DiscoverySession>(`${this.root(projectId)}/sessions`,'POST',{operation_id:key,title:value.title,entry_mode:value.entry_mode,statement:value.statement,materials:value.materials,...value.workspace?{workspace_ref:workspaceRef(value.workspace)}:{}});}
  edit(session:DiscoverySession,candidate:DiscoveryCandidate,reason:string,key=operationId()){return this.client.request<DiscoverySession>(this.path(session.project_id,session.id),'PUT',{operation_id:key,expected_revision:session.revision,candidate,change_reason:reason});}
  answer(session:DiscoverySession,questionId:string,text:string,key=operationId()){return this.client.request(this.path(session.project_id,session.id)+'/answers','POST',{operation_id:key,expected_revision:session.revision,question_id:questionId,text});}
  advance(session:DiscoverySession,message:string,options:{profile?:ProviderRef;workspace?:Workspace;materials?:DocumentRef[];phase?:DiscoveryPhase;feedbackRefs?:RevisionRef[];timeoutSeconds?:PlanWaitSeconds}={},key=operationId()){if(options.timeoutSeconds!==undefined&&!isPlanWaitSeconds(options.timeoutSeconds))throw new Error('请选择 2、5 或 10 分钟的等待预算。');return this.client.request<DiscoverySession|DiscoveryOperation>(this.path(session.project_id,session.id)+'/advance','POST',{operation_id:key,expected_revision:session.revision,message,...options.profile?{provider_profile_ref:options.profile}:{},...options.workspace?{workspace_ref:workspaceRef(options.workspace)}:{},...options.materials?{materials:options.materials}:{},...options.phase?{target_phase:options.phase}:{},...options.timeoutSeconds!==undefined?{budget:{timeout_seconds:options.timeoutSeconds}}:{},...options.feedbackRefs?.length?{feedback_refs:options.feedbackRefs}:{}},undefined,false);}
  confirm(session:DiscoverySession,claims:string[],graphs:string[],resolutions:Resolution[],key=operationId()){return this.client.request<DiscoverySession>(this.path(session.project_id,session.id)+'/confirm','POST',{operation_id:key,expected_revision:session.revision,session_sha256:session.sha256,user_confirmed:true,accepted_claim_ids:claims,accepted_graph_ids:graphs,resolutions});}
  plan(session:DiscoverySession,profile?:ProviderRef,key=operationId(),experience?:RevisionRef,timeoutSeconds?:PlanWaitSeconds,coverageContract?:'ACCEPTED_CLAIMS_V1'){if(timeoutSeconds!==undefined&&!isPlanWaitSeconds(timeoutSeconds))throw new Error('请选择 2、5 或 10 分钟的等待预算。');return this.client.request<DiscoveryPlan|DiscoveryOperation>(this.path(session.project_id,session.id)+'/plan','POST',{operation_id:key,expected_revision:session.revision,...profile?{provider_profile_ref:profile}:{},...experience?{experience_ref:experience}:{},...timeoutSeconds!==undefined?{budget:{timeout_seconds:timeoutSeconds}}:{},...coverageContract?{coverage_contract:coverageContract}:{}},undefined,false);}
  operation(projectId:string,key:string,signal?:AbortSignal){return this.client.request<DiscoveryOperation>(`${this.root(projectId)}/operations/${encodeURIComponent(key)}`,'GET',undefined,signal);}
  recoverable(projectId:string,sessionId:string,before?:number,signal?:AbortSignal){return this.client.request<DiscoveryRecoveryPage>(this.path(projectId,sessionId)+'/recoverable-operations'+(before===undefined?'':'?before='+before),'GET',undefined,signal);}
  cancel(projectId:string,key:string){return this.client.request(`${this.root(projectId)}/operations/${encodeURIComponent(key)}/cancel`,'POST');}
  pendingUrl(session:DiscoverySession){return this.path(session.project_id,session.id)+'/pending/markdown';}
}
export const discoveryApi=new DiscoveryApi();
