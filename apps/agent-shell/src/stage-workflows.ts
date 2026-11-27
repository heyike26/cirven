import {api, operationId} from './api';
import type {ManualFileReceipt} from './workflow-manual-files';

export type StageKind = 'init'|'requirements'|'alternatives'|'cases'|'design'|'development'|'testing'|'delivery'|'custom';
export const stageNames: Record<StageKind, [string,string]> = {
  init:['工程初始化','Project brief'],requirements:['需求梳理','Requirements'],alternatives:['方案论证','Design decisions'],
  cases:['测试用例','Test cases'],design:['详细设计','Detailed design'],development:['开发','Development'],
  testing:['测试','Testing'],delivery:['交付','Delivery'],custom:['自定义节点','Custom stage']
};
export type Capabilities={skills:string[];tools:string[];mcp:string[]};
export type Kernel='opencode'|'claude-code';
export type NodeConfig={kernel:Kernel;max_subagents:number;capabilities:Capabilities};
export type StageNode=NodeConfig & {id:string;kind:StageKind;name:string;instructions:string;inputs:string[]};
export type Definition={id:string;name:string;nodes:StageNode[];revision:number};
export type StageTemplate={id:string;edit_targets?:{target:string;columns:string[];repeatable:boolean;required?:boolean}[];export_extension?:string;warnings?:string[];name:string;text:string;sha256:string;format:string};
export type Row={id:string;title:string;[field:string]:unknown};
export type Artifact={template_edits?:{target:string;values:string[]}[];summary:string;items:Row[];template_output?:string;[field:string]:unknown};
export type Annotation={id:string;target:string;comment:string;status:string;created_operation_id?:string;anchor?:{kind?:string;quote?:string;field?:string;points?:number[][]}};
export type FollowUpType='instruction'|'requirement';
export type StageFollowUp={id:string;text:string;at:string;source_id?:string;processed_by?:string;withdrawn_at?:string};
export type FollowUpStatus='queued'|'running'|'processed'|'withdrawn';
export type DialogueReference={annotationId:string;followUpId?:never}|{followUpId:string;annotationId?:never};
export type StageDialogue={id:string;question:string;answer?:string;error?:string;at:string;status:'pending'|'answered'|'failed';annotation_id?:string;follow_up_id?:string;follow_up_status?:FollowUpStatus};
export type WorkflowOrbScope={project_id:string;run_id:string;node_id:string;stage_session_id:string};
export type CoverageSelector='ur'|'or';
export type CoverageTarget={node_id:string;stage_name:string;stage_session_id:string;operation_id?:string|null;row_id:string;title:string;version:number;archived:boolean};
export type CoverageResult=CoverageTarget&{status:'passed'|'unverified'|'reported_failed'|'blocked'|'not_executed';basis?:'manual'|'mixed'};
export type CoverageCase=CoverageTarget&{approved:boolean;results:CoverageResult[]};
export type CoverageImplementationKind='alternatives'|'design'|'development';
export type CoverageImplementationLink=CoverageTarget&{
 status:'approved'|'review'|'registered'|'registered_review'|'unverified';basis_ids?:string[];
};
export type CoverageActionProof=CoverageResult&{
 proof:'missing'|'unconfirmed'|'stale'|'failed'|'blocked'|'verified';receipt_id?:string;at?:string|null;
};
export type CoverageCriterion={
 index:number;text:string;actions:{
  id:string;method:string;binding:'explicit'|'single'|'design';case:CoverageTarget&{approved:boolean};
  scenarios:(CoverageImplementationLink&{scenario_id:string;path_kind:string;condition:string})[];
  results:CoverageActionProof[];
 }[];
};
export type CoverageReport={
  historical:boolean;withdrawn:boolean;ambiguous:boolean;version_uncertain:boolean;sources:{id:string;text?:string|null}[];
  requirements:(CoverageTarget&{approved:boolean;cases:CoverageCase[];criteria?:CoverageCriterion[];
    implementation?:Partial<Record<CoverageImplementationKind,CoverageImplementationLink[]>>})[];
};
export type CoverageQuery=WorkflowOrbScope&{selector:CoverageSelector;row_id:string};
export type CoveragePhase='requirements'|'alternatives'|'design'|'development'|'cases'|'testing'|'trace';
export type CoverageState='unverified'|'failed'|'unavailable'|'stale'|'missing'|'review'|'covered';
export type CoverageOverviewRow=Pick<CoverageQuery,'node_id'|'stage_session_id'|'selector'|'row_id'>&{
  title:string;stage_name:string;states:Partial<Record<CoveragePhase,CoverageState>>;
};
export type CoverageOverview={revision:number;phases:CoveragePhase[];pending_sources:number;unavailable_sources:number;rows:CoverageOverviewRow[]};
export type StageInputSnapshot={node_id:string;stage_session_id:string;status:'approved';artifact:Artifact};
export type CoverageRecord={stage:RunStage;historical:boolean;inputs:StageInputSnapshot[]};
export type BenchFirmware={id:string;path:string;sha256:string};
type BenchDevice={label:string;identity?:string;link?:string;firmware_label?:string};
export type BenchDraft={host?:string;fixture?:string;devices:(BenchDevice&{firmware_id?:string})[]};
export type BenchContext={files:BenchFirmware[];previous_bench?:{
 at:string;status:'failed'|'blocked';setup:BenchDraft;
}};
export type BenchReceipt={host?:string;fixture?:string;devices:(BenchDevice&{firmware?:BenchFirmware&{
 node_id:string;stage_session_id:string;approval_sha256:string}})[]};
export type ManualCheck={id:string;kind:'manual_check';source:'local_user';case_id:string;action_id:string;status:'passed'|'failed'|'blocked';environment:string;observation:string;at:string;bench?:BenchReceipt;attachments?:ManualFileReceipt[]};
export type PlanningOrigin={run_id:string;node_id:string;stage_session_id:string;approval_sha256:string};
export type PlanningSource=PlanningOrigin&{
  operation_id:string;stage_kind:StageKind;stage_name:string;approved_at:string;historical:boolean;artifact:Artifact;source_urs:Record<string,string>;
};
export type ReworkTarget={id:string;name:string;affected:{id:string;name:string}[]};
export type ReworkPreview={revision:number;case_ids:string[];targets:ReworkTarget[]};
export type ReworkEvidence={
 node_id:string;stage_session_id:string;error:string|null;total:number;offset:number;next_offset:number|null;
 records:{id:string;kind:string;case_id:string;action_id?:string;status:string;method?:string;
  path?:string;exit_code?:number;detail?:string;environment?:string;observation?:string;
  at?:string;recorded_at?:string;attachments?:ManualFileReceipt[];bench?:BenchReceipt;server?:string;tool?:string;
  subject?:TestSubject;control?:ContainerContrast;hardware_readback?:HardwareReadback;readback_issue?:ReadbackIssue}[];
};
export type HardwareReadback={
 source:'connector_reported';device_id:string|null;sampled_at:string|null;dispatched_at:string;received_at:string;observed_preview?:string;
};
export type ReadbackIssue='missing_readback'|'wrong_device'|'invalid_sample_time'|'outside_dispatch';
type WorkflowEvidence={id:string;kind:string;path?:string;sha256?:string;case_id?:string;design_id?:string;status?:string;server?:string;tool?:string;recorded_at?:string};
type ConnectorRetry={
  operation_id:string|null;
  evidence:{id:string;server:string;tool?:string;case_id?:string;recorded_at?:string;previous:boolean}[];
};
export type RunStage={
  node_id:string;stage_session_id:string|null;status:string;artifact:Artifact|null;questions:string[];
  execution_state?:'running'|'stopping'|'interrupted';
  execution_origin?:{operation_id:string;kernel:Kernel|null;at:string|null};
  stop_request?:{status:'requested'|'stopped'|'unconfirmed';operation_id:string;request_id:string;at:string};
  connector_retry?:ConnectorRetry;
  annotations:Annotation[];notes:{id:string;text:string;at:string}[];log:{event:string;at:string;[field:string]:unknown}[];
  follow_ups?:StageFollowUp[];
  source_urs?:Record<string,string>;
  test_input_issues?:Record<string,string[]>;
  dialogue?:StageDialogue[];
  approval_sha256?:string;operation_id?:string;reused_from?:PlanningOrigin;approval_issues?:string[];rework_request?:{reason:string;case_ids:string[];from_node_id:string;at:string};verified_evidence:WorkflowEvidence[];progress?:{completed:number;total:number;message?:string;at?:string};user_input:Record<string,unknown>;template:StageTemplate|null;config:NodeConfig;error:string|null;
};
type TestSubject={path:string;sha256:string;file_receipt_id:string;node_id:string;stage_session_id:string;approval_sha256:string};
type ContainerOutput={
 input_sha256?:string;expected_output_sha256?:string;observed_output_sha256?:string;
 output_matches?:boolean;output_truncated?:boolean;output_preview?:string;output_preview_partial?:boolean;
 subject?:TestSubject;
};
type ContainerContrast=ContainerOutput&{exit_code:number;expected_exit_code:number;limit?:string|null};
export type ExecutionRecord={
  operation_id:string;status:string;at:string;annotation_count:number;
  kernel?:Kernel|null;
  live?:boolean;started_at?:string|null;
  inputs?:{node_id:string;stage_session_id:string;approval_sha256:string}[];
  evidence:(WorkflowEvidence&ContainerOutput&{
   action_id?:string;previous_sha256?:string|null;size?:number;exit_code?:number;source_manifest_sha256?:string;
   control?:ContainerContrast;hardware_readback?:HardwareReadback;readback_issue?:ReadbackIssue;
  })[];
};
export type StageRun={id:string;project_id:string;conversation_id:string;definition:Definition;revision:number;active_node_id:string|null;status:string;stages:RunStage[];created_at:string;updated_at:string};
export type StageIndex={definitions:Definition[];runs:StageRun[];owner_conversation_id?:string;historical_node_id?:string|null;historical_stage?:RunStage|null;historical_inputs?:StageInputSnapshot[];improved_definition_ids?:string[]};
export type Scope={project_id:string;conversation_id:string};
export type TransitionKind='start'|'approve'|'rework'|'revise';
export type TransitionPlan=
 |{kind:'start';definition_id:string;expected_definition_revision:number}
 |{kind:Exclude<TransitionKind,'start'>;run_id:string;node_id:string;stage_session_id:string;
   expected_revision:number;payload:Record<string,unknown>};
export type PendingTransition={operation_id:string;request_conversation_id:string;plan:TransitionPlan;draft_edit?:string};
export type TransitionReceipt={
 operation_id:string;kind:TransitionKind;project_id:string;request_conversation_id:string;
 run_id:string;run_revision:number;source_node_id:string|null;source_stage_session_id:string|null;
 previous:string;definition_id:string;definition_revision:number;at:string;target_current:boolean;target_historical:boolean;
 target:{node_id:string;stage_session_id:string;stage_name:string}|null;
};
export type StageDestination={project:string;owner:string;run:string;node:string;historical?:boolean};
export type Catalog=Record<keyof Capabilities,{id:string;name:string;revision?:number;sha256?:string}[]>;
export type RevisionBasis=WorkflowOrbScope&{revision:number};
export type RevisionChange='added'|'modified'|'removed';
export type RevisionRow={id:string;title:string;change:RevisionChange};
export type RevisionSummary={
 counts:Record<RevisionChange,number>;rows:RevisionRow[];artifact_changes:number;reordered:boolean;next_offset:number|null;
 previous_status:string;current_status:string;
};
export type RevisionSelection={group:'artifact'}|{group:'row';row_id:string};
export type RevisionValue={present:true;value:unknown}|{present:false};
export type RevisionField={field:string;before:RevisionValue;after:RevisionValue};
export type ImprovementRequest={
 approval_sha256:string;source_field:'lessons'|'plugin_suggestions';source_index:number;target_node_id:string;
 category:keyof Capabilities;change:'add'|'remove';capability_id:string;capability_sha256?:string;
 mode:'copy'|'update';copy_name?:string;
};
export type ImprovementReceipt={
 operation_id:string;at:string;source:{run_id:string;node_id:string;conversation_id:string;approval_sha256:string;field:ImprovementRequest['source_field'];index:number};
 definition:{id:string;name:string;revision:number};target_name:string;target_node_id:string;
 category:keyof Capabilities;change:ImprovementRequest['change'];capability_id:string;
};
export type DefinitionImprovement=Pick<ImprovementReceipt,'operation_id'|'at'|'target_name'|'category'|'change'|'capability_id'>&{
 revision:number;state:'matches'|'changed'|'node_removed';
};
export type DefinitionImprovementHistory={records:DefinitionImprovement[];next_before:number|null};
export type DefinitionImprovementSource={
 run_id:string;node_id:string;stage_name:string;stage_session_id:string;approval_sha256:string;approved_at:string;historical:boolean;
 field:ImprovementRequest['source_field'];quote:string;
};
export const emptyCatalog=():Catalog=>({skills:[],tools:[],mcp:[]});
export function stageModeKey(s:Scope){return 'cirven-stage-view-v1:'+JSON.stringify([s.project_id,s.conversation_id]);}
export function draftKey(run:Pick<StageRun,'project_id'|'conversation_id'|'id'>,node:string,session:string|null){return 'cirven-stage-input-v1:'+JSON.stringify([run.project_id,run.conversation_id,run.id,node,session]);}
export function selectStage(index:StageIndex, conversationId:string,preferred?:string|null){
  const child=index.runs.find(r=>r.stages.some(s=>s.stage_session_id===conversationId));
  const run=child??index.runs.find(r=>r.id===preferred)??index.runs[0];
  const page=child?.stages.find(s=>s.stage_session_id===conversationId)?.node_id??run?.active_node_id??run?.stages.at(-1)?.node_id;
  return {run,page};
}
const route='/api/stage-workflows';
function revisionPath(basis:RevisionBasis,params:Record<string,string>){
 const query=new URLSearchParams({project_id:basis.project_id,conversation_id:basis.stage_session_id,
   revision:String(basis.revision),...params});
 return route+'/runs/'+encodeURIComponent(basis.run_id)+'/stages/'+encodeURIComponent(basis.node_id)+'/revision-difference?'+query;
}
export type DeliveryBundleReference={
  run_id:string;approval_sha256:string;bytes:number;chunk_bytes:number;sha256_chunks:string[];
};

function deliveryPath(run:StageRun,stage:RunStage,approval_sha256:string,format:'receipt'|'bundle'|'bundle-reference'){
  const query=new URLSearchParams({project_id:run.project_id,conversation_id:stage.stage_session_id!,approval_sha256});
  return route+'/runs/'+encodeURIComponent(run.id)+'/stages/'+encodeURIComponent(stage.node_id)+'/delivery-'+format+'?'+query;
}
export const stageApi={
  list(scope:Scope,signal?:AbortSignal){return api.request<StageIndex>(route+'?'+new URLSearchParams(scope),'GET',undefined,signal);},
  capabilities(signal?:AbortSignal){return api.request<{capabilities:Catalog}>('/api/workflow-kernels','GET',undefined,signal);},
  run(scope:Scope,id:string,signal?:AbortSignal){return api.request<StageRun>(route+'/runs/'+encodeURIComponent(id)+'?'+new URLSearchParams(scope),'GET',undefined,signal);},
  transitionReceipt(scope:Scope,operation:string,signal:AbortSignal){
    return api.request<TransitionReceipt>(route+'/transition-receipts/'+encodeURIComponent(operation)+'?'+
      new URLSearchParams(scope),'GET',undefined,signal);
  },
  submitTransition(project_id:string,request:PendingTransition){
    const plan=request.plan,scope={project_id,conversation_id:request.request_conversation_id},op=request.operation_id;
    return plan.kind==='start'
      ?stageApi.start(scope,plan.definition_id,plan.expected_definition_revision,op)
      :stageApi.act({...scope,id:plan.run_id,revision:plan.expected_revision},plan.node_id,plan.kind,plan.payload,op);
  },
  planningSource(run:StageRun,stage:RunStage,operation:string,signal:AbortSignal){
    const query=new URLSearchParams({project_id:run.project_id,conversation_id:stage.stage_session_id!,operation_id:operation});
    return api.request<PlanningSource>(route+'/runs/'+encodeURIComponent(run.id)+'/stages/'+
      encodeURIComponent(stage.node_id)+'/planning-source?'+query,'GET',undefined,signal);
  },
  reworkPreview(run:StageRun,stage:RunStage,signal:AbortSignal){
    const query=new URLSearchParams({project_id:run.project_id,conversation_id:stage.stage_session_id!});
    return api.request<ReworkPreview>(route+'/runs/'+encodeURIComponent(run.id)+'/stages/'+
      encodeURIComponent(stage.node_id)+'/rework-preview?'+query,'GET',undefined,signal);
  },
  reworkEvidence(run:StageRun,stage:RunStage,revision:number|undefined,offset:number,signal:AbortSignal){
    const query=new URLSearchParams({project_id:run.project_id,conversation_id:stage.stage_session_id!,offset:String(offset)});
    if(revision!==undefined)query.set('revision',String(revision));
    return api.request<ReworkEvidence>(route+'/runs/'+encodeURIComponent(run.id)+'/stages/'+
      encodeURIComponent(stage.node_id)+'/rework-evidence?'+query,'GET',undefined,signal);
  },
  revisionSummary(basis:RevisionBasis,offset:number,signal:AbortSignal){
    return api.request<RevisionSummary>(revisionPath(basis,{offset:String(offset)}),'GET',undefined,signal);
  },
  revisionFields(basis:RevisionBasis,selection:RevisionSelection,signal:AbortSignal){
    return api.request<{fields:RevisionField[]}>(revisionPath(basis,selection),'GET',undefined,signal);
  },
  definitionImprovements(scope:Scope,definition:Definition,before:number|null,signal:AbortSignal){
    const query=new URLSearchParams({...scope,revision:String(definition.revision)});
    if(before!==null)query.set('before',String(before));
    return api.request<DefinitionImprovementHistory>(route+'/definitions/'+encodeURIComponent(definition.id)+'/improvements?'+query,'GET',undefined,signal);
  },
  definitionImprovementSource(scope:Scope,definition:Definition,operation:string,signal:AbortSignal){
    const query=new URLSearchParams({...scope,revision:String(definition.revision)});
    return api.request<DefinitionImprovementSource>(route+'/definitions/'+encodeURIComponent(definition.id)+'/improvements/'+encodeURIComponent(operation)+'?'+query,'GET',undefined,signal);
  },
  improvements(run:StageRun,stage:RunStage,approval_sha256:string,signal?:AbortSignal){
    const query=new URLSearchParams({project_id:run.project_id,conversation_id:stage.stage_session_id!,approval_sha256});
    return api.request<ImprovementReceipt[]>(route+'/runs/'+encodeURIComponent(run.id)+'/stages/'+encodeURIComponent(stage.node_id)+'/improvements?'+query,'GET',undefined,signal);
  },
  improve(run:StageRun,stage:RunStage,request:ImprovementRequest,operation_id:string){
    return api.request<{receipt:ImprovementReceipt;definition:Definition}>(route+'/runs/'+encodeURIComponent(run.id)+'/stages/'+encodeURIComponent(stage.node_id)+'/improvements','POST',{
      ...request,project_id:run.project_id,conversation_id:stage.stage_session_id,operation_id
    });
  },
  coverage(origin:CoverageQuery,signal?:AbortSignal){
    const {project_id,run_id,node_id,stage_session_id,selector,row_id}=origin;
    const query=new URLSearchParams({project_id,conversation_id:stage_session_id,selector,row_id});
    return api.request<CoverageReport>(route+'/runs/'+encodeURIComponent(run_id)+'/stages/'+encodeURIComponent(node_id)+'/coverage?'+query,'GET',undefined,signal);
  },
  coverageOverview(scope:Scope,runId:string,signal:AbortSignal){
    return api.request<CoverageOverview>(route+'/runs/'+encodeURIComponent(runId)+'/coverage-overview?'+new URLSearchParams(scope),
      'GET',undefined,signal);
  },
  coverageRecord(origin:Pick<CoverageQuery,'project_id'|'run_id'>,
    target:Pick<CoverageTarget,'node_id'|'stage_session_id'|'row_id'|'operation_id'>,signal?:AbortSignal){
    const query=new URLSearchParams({project_id:origin.project_id,conversation_id:target.stage_session_id,row_id:target.row_id});
    if(target.operation_id)query.set('operation_id',target.operation_id);
    return api.request<CoverageRecord>(route+'/runs/'+encodeURIComponent(origin.run_id)+'/stages/'+encodeURIComponent(target.node_id)+'/coverage-record?'+query,'GET',undefined,signal);
  },
  executionRecord(run:StageRun,stage:RunStage,operation_id:string,signal:AbortSignal){
    const query=new URLSearchParams({project_id:run.project_id,conversation_id:stage.stage_session_id!,operation_id});
    return api.request<ExecutionRecord>(route+'/runs/'+encodeURIComponent(run.id)+'/stages/'+encodeURIComponent(stage.node_id)+'/execution-record?'+query,'GET',undefined,signal);
  },
  benchFirmware(run:StageRun,stage:RunStage,action:{caseId:string;id:string},signal:AbortSignal){
    const query=new URLSearchParams({project_id:run.project_id,conversation_id:stage.stage_session_id!});
    query.set('case_id',action.caseId);query.set('action_id',action.id);
    return api.request<BenchContext>(route+'/runs/'+encodeURIComponent(run.id)+'/stages/'+encodeURIComponent(stage.node_id)+'/bench-firmware?'+query,'GET',undefined,signal);
  },
  manualFileHref(run:StageRun,stage:Pick<RunStage,'node_id'|'stage_session_id'>,receipt:string,file:string){
    const query=new URLSearchParams({project_id:run.project_id,conversation_id:stage.stage_session_id!});
    return route+'/runs/'+encodeURIComponent(run.id)+'/stages/'+encodeURIComponent(stage.node_id)+
      '/manual-files/'+encodeURIComponent(receipt)+'/'+encodeURIComponent(file)+'?'+query;
  },
  evidenceFileHref(run:StageRun,session:string,id:string){
    const query=new URLSearchParams({project_id:run.project_id,conversation_id:session});
    return route+'/runs/'+encodeURIComponent(run.id)+'/files/'+encodeURIComponent(id)+'?'+query;
  },
  start(scope:Scope,definition_id:string,expected_definition_revision:number,operation_id:string=operationId()){return api.request<StageRun>(route+'/runs','POST',{...scope,definition_id,expected_definition_revision,operation_id});},
  define(scope:Scope,definition:Definition,operation_id:string=operationId()){const {revision,...body}=definition;return api.request<Definition>(route+'/definitions','POST',{...scope,definition:body,expected_revision:revision,operation_id});},
  act(run:Pick<StageRun,'project_id'|'conversation_id'|'id'|'revision'>,node_id:string,action:string,payload:Record<string,unknown>,operation_id:string=operationId(),conversation=run.conversation_id){
    return api.request<StageRun>(route+'/runs/'+encodeURIComponent(run.id)+'/actions','POST',{project_id:run.project_id,conversation_id:conversation,expected_revision:run.revision,operation_id,node_id,action,payload});
  },
  supplement(run:StageRun,stage:RunStage,action:'supplement'|'revise',text:string,sourceType:FollowUpType,operation_id:string){
    return api.request<StageRun>(route+'/runs/'+encodeURIComponent(run.id)+'/actions','POST',{
      project_id:run.project_id,conversation_id:stage.stage_session_id,expected_revision:run.revision,
      operation_id,node_id:stage.node_id,action,payload:sourceType==='requirement'?{text,source_type:sourceType}:{text}
    });
  },
  ask(run:StageRun,stage:RunStage,question:string,reference:DialogueReference|undefined,operation_id:string){
    return api.request<StageRun>(route+'/runs/'+encodeURIComponent(run.id)+'/dialogue','POST',{
      project_id:run.project_id,conversation_id:stage.stage_session_id,node_id:stage.node_id,operation_id,question,
      annotation_id:reference?.annotationId,follow_up_id:reference?.followUpId
    });
  },
  deliveryReceipt(run:StageRun,stage:RunStage,approval_sha256:string,signal:AbortSignal){
    return api.bytes(deliveryPath(run,stage,approval_sha256,'receipt'),signal);
  },
  deliveryBundle(run:StageRun,stage:RunStage,approval_sha256:string,signal:AbortSignal){
    return api.download(deliveryPath(run,stage,approval_sha256,'bundle'),signal);
  },
  deliveryBundleReference(run:StageRun,stage:RunStage,approval_sha256:string,signal:AbortSignal){
    return api.request<DeliveryBundleReference>(deliveryPath(run,stage,approval_sha256,'bundle-reference'),'GET',undefined,signal);
  },
  template(scope:Scope,file:File,signal?:AbortSignal){
    const form=new FormData();form.set('project_id',scope.project_id);form.set('conversation_id',scope.conversation_id);form.set('file',file);
    return api.form<StageTemplate>(route+'/template',form,signal);
  }
};
export const blankNode=(kind:StageKind='custom'):StageNode=>({id:'node-'+operationId(),kind,name:stageNames[kind][0],instructions:'',inputs:[],kernel:'opencode',max_subagents:0,capabilities:{skills:[],tools:[],mcp:[]}});
export function validateNodes(nodes:StageNode[]):boolean{
  const seen=new Set<string>();
  for(const n of nodes){
    if(!n.name.trim()||seen.has(n.id)||n.inputs.some(input=>!seen.has(input)))return false;
    seen.add(n.id);
  }
  return nodes.length>0;
}

export function templateDownload(run:StageRun,stage:RunStage,plain=false){
  if(stage.status!=='approved'||!stage.stage_session_id||!stage.approval_sha256||!stage.template||!stage.artifact?.template_output)return undefined;
  const query=new URLSearchParams({project_id:run.project_id,conversation_id:stage.stage_session_id,
    stage_session_id:stage.stage_session_id,approval_sha256:stage.approval_sha256,format:plain?'text':'template'});
  return route+'/runs/'+encodeURIComponent(run.id)+'/stages/'+encodeURIComponent(stage.node_id)+'/template-export?'+query;
}

export function approvedDeliverableDownload(run:StageRun,stage:RunStage,itemId:string){
  if(stage.status!=='approved'||!stage.stage_session_id||!stage.approval_sha256)return undefined;
  const query=new URLSearchParams({project_id:run.project_id,conversation_id:stage.stage_session_id,
    approval_sha256:stage.approval_sha256,item_id:itemId});
  return route+'/runs/'+encodeURIComponent(run.id)+'/stages/'+encodeURIComponent(stage.node_id)+'/delivery-file?'+query;
}
