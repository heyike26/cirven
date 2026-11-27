import type {SupervisorTaskKey,SupervisorRef} from './global-supervisor';
import type {NativeMessage,NativeRef,FileLinkRef} from './native-collaboration';
import type {AgentSession,AgentSessionOperation} from './agent-sessions';
import type {DiscoverySession} from './discovery-contracts';
import type {PlanningProposal,SourceRef} from './contracts';

type WorkspaceRef={id:string;revision:number;binding_sha256:string};
interface NavigationBase {
  version:1;kind:'native'|'original'|'discovery'|'planning';status:'AVAILABLE'|'READONLY';reason_code:string|null;
  task_key:SupervisorTaskKey;source_ref:SupervisorRef;project_id:string|null;operation_id:string;
  automatic_retry:false;execution_authorized:false;
}
export interface NativeTaskNavigation extends NavigationBase {
  kind:'native';request_project_id:string|null;conversation_project_id:string|null;conversation_id:string;request_sha256:string;provider_profile_ref:NativeRef|null;
  workspace_ref:WorkspaceRef|null;file_source_ref:SourceRef|null;file_link_ref:FileLinkRef|null;
  record_origin:'CURRENT_HOST'|'HISTORICAL_READONLY';
}
export interface OriginalTaskNavigation extends NavigationBase {
  kind:'original';project_id:string;session_ref:NativeRef;generation:number;current_generation:number;
  request_sha256:string;provider_profile_ref:NativeRef|null;workspace_ref:WorkspaceRef|null;
}
export interface ObjectTaskNavigation extends NavigationBase {
  kind:'discovery'|'planning';project_id:string;object_ref:NativeRef;source_session_ref:NativeRef|null;
  workspace_ref:WorkspaceRef|null;proposal_ref:NativeRef|null;
}
export type SupervisedTaskNavigation=NativeTaskNavigation|OriginalTaskNavigation|ObjectTaskNavigation;
export interface SupervisedNativeConversation {
  kind:'NATIVE_CONVERSATION';project_id:string|null;conversation_id:string;messages:NativeMessage[];
  [key:string]:unknown;
}
type CommonTarget={readOnly:boolean;input:string;answer:string};
export type SupervisedTaskTarget=CommonTarget&(
  {kind:'native';navigation:NativeTaskNavigation;message:NativeMessage;conversation:SupervisedNativeConversation}|
  {kind:'original';navigation:OriginalTaskNavigation;session:AgentSession;operation:AgentSessionOperation}|
  {kind:'discovery';navigation:ObjectTaskNavigation;session:DiscoverySession;proposal?:PlanningProposal}|
  {kind:'planning';navigation:ObjectTaskNavigation;proposal:PlanningProposal}
);
export interface SupervisedTaskReadTransport {
  get:(path:string,signal?:AbortSignal)=>Promise<unknown>;
  inspectNativeMessage?:(operationId:string)=>Promise<unknown>;
  getNativeConversation?:(target:{projectId:string|null;conversationId:string;operationId:string})=>Promise<unknown>;
}
export interface SupervisedTaskLoadOptions {
  signal?:AbortSignal;current?:()=>boolean;expectedTask?:{key:SupervisorTaskKey;source_ref?:SupervisorRef};
}
const ID=/^[A-Za-z0-9_-]{1,100}$/,OBJECT_ID=/^[A-Za-z0-9_-]{1,200}$/,SHA=/^[a-f0-9]{64}$/;
function fail(code='SUPERVISED_TASK_REFERENCE_CHANGED'):never{throw Object.assign(new Error(code),{code});}
function check(value:unknown):asserts value{if(!value)fail();}
function object(value:unknown):Record<string,unknown>{check(value&&typeof value==='object'&&!Array.isArray(value));return value as Record<string,unknown>;}
function exact(value:Record<string,unknown>,keys:string[]){check(Object.keys(value).sort().join(',')===[...keys].sort().join(','));}
function id(value:unknown,long=false):string{check(typeof value==='string'&&(long?OBJECT_ID:ID).test(value));return value;}
function integer(value:unknown,min=0):number{check(typeof value==='number'&&Number.isSafeInteger(value)&&value>=min);return value;}
function hash(value:unknown):string{check(typeof value==='string'&&SHA.test(value));return value;}
function text(value:unknown):string{check(typeof value==='string'&&Array.from(value).length<=64000);return value;}
function ref(value:unknown):NativeRef{const row=object(value);exact(row,['id','revision','sha256']);return{id:id(row.id,true),revision:integer(row.revision,1),sha256:hash(row.sha256)};}
function workspace(value:unknown):WorkspaceRef{const row=object(value);exact(row,['id','revision','binding_sha256']);return{id:hash(row.id),revision:integer(row.revision,1),binding_sha256:hash(row.binding_sha256)};}
function optional<T>(value:unknown,parse:(value:unknown)=>T):T|null{return value===null?null:parse(value);}
function equal(a:unknown,b:unknown):boolean{
  const canonical=(value:unknown):string=>value!==null&&typeof value==='object'?Array.isArray(value)?'['+value.map(canonical).join(',')+']':'{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical((value as Record<string,unknown>)[key])).join(',')+'}':JSON.stringify(value);
  return canonical(a)===canonical(b);
}
function key(value:unknown):SupervisorTaskKey{
  const row=object(value);check(Object.keys(row).every(name=>['kind','project_id','object_id','operation_id','generation'].includes(name)));
  check(typeof row.kind==='string'&&/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(row.kind));
  return{kind:row.kind,project_id:row.project_id==null?null:id(row.project_id),object_id:id(row.object_id,true),operation_id:row.operation_id==null?null:id(row.operation_id),generation:integer(row.generation)};
}
function fileSource(value:unknown):SourceRef{
  const row=object(value);exact(row,['kind','project_id','workspace_id','binding_revision','binding_sha256','path','revision','sha256']);
  check(row.kind==='local_file'&&typeof row.path==='string'&&row.path.length>0&&row.path.length<=32768&&!/[\u0000-\u001f]/.test(row.path));
  return{kind:'local_file',project_id:id(row.project_id),workspace_id:hash(row.workspace_id),binding_revision:integer(row.binding_revision,1),binding_sha256:hash(row.binding_sha256),path:row.path,revision:integer(row.revision,1),sha256:hash(row.sha256)};
}
function fileLink(value:unknown):FileLinkRef{const row=object(value);exact(row,['id','revision','sha256','project_id']);return{id:hash(row.id),revision:integer(row.revision,1),sha256:hash(row.sha256),project_id:id(row.project_id)};}
const common=['version','kind','status','reason_code','task_key','source_ref','project_id','operation_id','automatic_retry','execution_authorized'];
function navigation(detail:Record<string,unknown>,expected?:SupervisedTaskLoadOptions['expectedTask']):SupervisedTaskNavigation{
  const selected=key(detail.key),task=object(detail.task),taskKey=key(task.key),source=ref(task.source_ref),raw=object(detail.navigation);
  check(equal(selected,taskKey)&&detail.automatic_retry===false&&detail.execution_authorized===false);
  if(expected)check(equal(selected,key(expected.key))&&equal(source,ref(expected.source_ref)));
  check(raw.version===1&&['native','original','discovery','planning'].includes(String(raw.kind))&&raw.automatic_retry===false&&raw.execution_authorized===false&&equal(key(raw.task_key),selected)&&equal(ref(raw.source_ref),source));
  check(raw.project_id===selected.project_id&&raw.operation_id===selected.operation_id);
  if(raw.status==='UNAVAILABLE')fail('SUPERVISED_TASK_NAVIGATION_UNAVAILABLE');
  check(raw.status==='AVAILABLE'||raw.status==='READONLY');check(raw.reason_code===null||typeof raw.reason_code==='string'&&/^[A-Z0-9_]{1,100}$/.test(raw.reason_code));
  const base:NavigationBase={version:1,kind:raw.kind as NavigationBase['kind'],status:raw.status,reason_code:raw.reason_code as string|null,task_key:selected,source_ref:source,project_id:selected.project_id??null,operation_id:id(raw.operation_id),automatic_retry:false,execution_authorized:false};
  if(raw.kind==='native'){
    exact(raw,[...common,'request_project_id','conversation_project_id','conversation_id','request_sha256','provider_profile_ref','workspace_ref','file_source_ref','file_link_ref','record_origin']);
    check(selected.kind==='native_message'&&selected.object_id===selected.operation_id&&['CURRENT_HOST','HISTORICAL_READONLY'].includes(String(raw.record_origin)));
    const target:NativeTaskNavigation={...base,kind:'native',request_project_id:raw.request_project_id===null?null:id(raw.request_project_id),conversation_project_id:raw.conversation_project_id===null?null:id(raw.conversation_project_id),conversation_id:id(raw.conversation_id),request_sha256:hash(raw.request_sha256),provider_profile_ref:optional(raw.provider_profile_ref,ref),workspace_ref:optional(raw.workspace_ref,workspace),file_source_ref:optional(raw.file_source_ref,fileSource),file_link_ref:optional(raw.file_link_ref,fileLink),record_origin:raw.record_origin as NativeTaskNavigation['record_origin']};
    check(target.request_project_id===target.project_id);
    if(target.request_project_id!==target.conversation_project_id)check(target.request_project_id===null&&target.conversation_project_id!==null&&target.status==='READONLY');
    if(target.file_source_ref)check(target.file_source_ref.project_id===target.conversation_project_id);
    if(target.file_link_ref)check(target.file_link_ref.project_id===target.conversation_project_id);
    return target;
  }
  if(raw.kind==='original'){
    exact(raw,[...common,'session_ref','generation','current_generation','request_sha256','provider_profile_ref','workspace_ref']);
    check(selected.kind==='original_session'&&base.project_id!==null);const session=ref(raw.session_ref);check(session.id===selected.object_id&&equal(session,source)&&raw.generation===selected.generation);
    return{...base,kind:'original',project_id:base.project_id,session_ref:session,generation:integer(raw.generation),current_generation:integer(raw.current_generation),request_sha256:hash(raw.request_sha256),provider_profile_ref:optional(raw.provider_profile_ref,ref),workspace_ref:optional(raw.workspace_ref,workspace)};
  }
  exact(raw,[...common,'object_ref','source_session_ref','workspace_ref','proposal_ref']);check(selected.kind===raw.kind&&base.project_id!==null);
  const objectRef=ref(raw.object_ref),session=optional(raw.source_session_ref,ref),proposal=optional(raw.proposal_ref,ref);check(objectRef.id===selected.object_id);
  if(raw.kind==='discovery')check(equal(session,objectRef));
  return{...base,kind:raw.kind as 'discovery'|'planning',project_id:base.project_id,object_ref:objectRef,source_session_ref:session,workspace_ref:optional(raw.workspace_ref,workspace),proposal_ref:proposal};
}
function versioned(value:unknown,pid:string,expected:NativeRef):Record<string,unknown>{const row=object(value);check(row.project_id===pid&&row.id===expected.id&&row.revision===expected.revision&&row.sha256===expected.sha256);if(row.current_revision!==undefined)check(integer(row.current_revision,1)>=expected.revision);object(row.payload);return row;}
function nativeMessage(value:unknown,target:NativeTaskNavigation,exactOperation=true):NativeMessage{
  const row=object(value);check(row.project_id===target.conversation_project_id&&row.conversation_id===target.conversation_id&&typeof row.status==='string');id(row.operation_id);hash(row.request_sha256);check(row.automatic_retry===false);
  if(exactOperation){check(row.operation_id===target.operation_id&&row.request_sha256===target.request_sha256&&row.record_origin===target.record_origin&&equal(row.provider_profile_ref??null,target.provider_profile_ref)&&equal(row.file_source_ref??null,target.file_source_ref)&&equal(row.file_link_ref??null,target.file_link_ref));if(Object.hasOwn(row,'request_project_id'))check(row.request_project_id===target.request_project_id);}
  return row as NativeMessage;
}

/** Read one captured target. A newer navigation intent owns publication and cancels this result. */
export async function loadSupervisedTaskNavigation(detailValue:unknown,transport:SupervisedTaskReadTransport,options:SupervisedTaskLoadOptions={}):Promise<SupervisedTaskTarget|undefined>{
  const current=()=>!options.signal?.aborted&&(options.current?.()??true),cancelled=Symbol('cancelled');
  if(!current())return;
  try{
    const detail=object(detailValue),target=navigation(detail,options.expectedTask),input=text(detail.input_text),answer=text(detail.answer_text),base={readOnly:target.status==='READONLY',input,answer};
    const read=async(get:()=>Promise<unknown>)=>{if(!current())throw cancelled;const value=await get();if(!current())throw cancelled;return value;};
    const get=(path:string)=>read(()=>transport.get(path,options.signal)),project=target.project_id===null?'':`/api/projects/${encodeURIComponent(target.project_id)}`;
    if(target.kind==='native'){
      if(!transport.inspectNativeMessage||!transport.getNativeConversation)fail('SUPERVISED_TASK_TRANSPORT_UNAVAILABLE');
      const message=nativeMessage(await read(()=>transport.inspectNativeMessage!(target.operation_id)),target);
      const raw=object(await read(()=>transport.getNativeConversation!({projectId:target.conversation_project_id,conversationId:target.conversation_id,operationId:target.operation_id})));
      check(raw.kind==='NATIVE_CONVERSATION'&&raw.project_id===target.conversation_project_id&&raw.conversation_id===target.conversation_id&&Array.isArray(raw.messages)&&raw.messages.length<=40);
      for(const row of raw.messages)nativeMessage(row,target,false);
      const included=raw.messages.find(row=>object(row).operation_id===target.operation_id);if(included)nativeMessage(included,target);
      return{...base,kind:'native',navigation:target,message,conversation:raw as unknown as SupervisedNativeConversation,readOnly:base.readOnly||target.record_origin==='HISTORICAL_READONLY'};
    }
    if(target.kind==='original'){
      const root=`${project}/agent-sessions/${encodeURIComponent(target.session_ref.id)}`,session=object(await get(root));
      check(session.project_id===target.project_id&&equal(ref(session.session_ref),target.session_ref)&&session.generation===target.current_generation&&session.automatic_post_replay===false&&Array.isArray(session.messages)&&Array.isArray(session.todo)&&Array.isArray(session.children)&&Array.isArray(session.pending_questions)&&Array.isArray(session.pending_permissions));
      check(typeof session.owner_status==='string'&&typeof session.native_status==='string'&&typeof session.connected_owner==='boolean'&&typeof session.configuration_current==='boolean'&&typeof session.workspace_kind==='string');integer(session.last_event_seq);
      check(equal(session.provider_profile_ref??null,target.provider_profile_ref)&&equal(session.workspace_ref??null,target.workspace_ref));
      const operation=object(await get(`${root}/operations/${encodeURIComponent(target.operation_id)}`)),request=object(operation.request);
      check(operation.id===target.operation_id&&operation.kind==='MESSAGE'&&typeof operation.status==='string'&&/^[A-Z][A-Z0-9_]{0,63}$/.test(operation.status)&&operation.automatic_replay===false&&operation.request_sha256===target.request_sha256&&equal(operation.operation_ref,{id:target.operation_id,request_sha256:target.request_sha256})&&equal(ref(operation.session_ref),target.session_ref)&&equal(ref(request.session_ref),target.session_ref)&&request.operation_id===target.operation_id&&request.generation===target.generation&&request.raw_text===input);
      if(operation.generation!==undefined)check(operation.generation===target.generation);
      return{...base,kind:'original',navigation:target,session:session as AgentSession,operation:operation as AgentSessionOperation,readOnly:base.readOnly||target.current_generation!==target.generation||!session.connected_owner||!session.configuration_current};
    }
    const path=target.kind==='discovery'?'discovery/sessions':'planning/proposals',row=versioned(await get(`${project}/${path}/${encodeURIComponent(target.object_ref.id)}?revision=${target.object_ref.revision}`),target.project_id,target.object_ref),payload=object(row.payload);
    check(equal(payload.workspace_ref??null,target.workspace_ref));const historical=typeof row.current_revision==='number'&&row.current_revision!==target.object_ref.revision||row.source_current===false||row.review_status==='NEEDS_REVIEW';
    if(target.kind==='planning'){
      check((target.source_session_ref!==null?equal(payload.discovery_ref,target.source_session_ref):payload.discovery_ref==null||base.readOnly)&&target.proposal_ref===null);
      return{...base,kind:'planning',navigation:target,proposal:row as PlanningProposal,readOnly:base.readOnly||historical};
    }
    let proposal:PlanningProposal|undefined,proposalHistorical=false;
    if(target.proposal_ref){
      check(equal(payload.proposal_ref,target.proposal_ref));
      proposal=versioned(await get(`${project}/planning/proposals/${encodeURIComponent(target.proposal_ref.id)}?revision=${target.proposal_ref.revision}`),target.project_id,target.proposal_ref) as PlanningProposal;
      proposalHistorical=proposal.current_revision!==undefined&&proposal.current_revision!==target.proposal_ref.revision||proposal.review_status==='NEEDS_REVIEW';
    }
    return{...base,kind:'discovery',navigation:target,session:row as DiscoverySession,...proposal?{proposal}:{},readOnly:base.readOnly||historical||proposalHistorical};
  }catch(error){if(error===cancelled||!current())return;throw error;}
}
