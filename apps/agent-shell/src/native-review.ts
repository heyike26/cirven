import type {NativeRef,FileLinkRef,QuickScope} from './native-collaboration';
import {assertNativeToolsCatalog,chooseMCPTools,type MCPToolSelectionDraft,type MCPToolSelectionItem,type NativeToolsCatalog} from './native-tools.ts';
export type ReviewPlanBasisRef={kind:'REPORT_PLAN';file_link_ref:FileLinkRef;proposal_ref:NativeRef;source_snapshot_sha256:string;candidate_sha256:string};
export type ReviewToolSelection={kind:'BUILTIN_READONLY';tool_ref:NativeRef}|{kind:'USER_MCP';mcp_tool_selection:MCPToolSelectionDraft};
export type NativeReviewSelection={skill_ref:NativeRef|null;tool_selection:ReviewToolSelection|null;capability_binding_sha256:string;basis_ref:ReviewPlanBasisRef|null};
export type NativeReadonlyReviewInput={version:1;selection:NativeReviewSelection;process_start_consent:boolean};
export type CapabilityTaskSelection={capability_binding_sha256:string;skill_ref:NativeRef|null;mcp_tool_selection:MCPToolSelectionItem[]};
export type NativePageSelection=NativeReviewSelection|CapabilityTaskSelection;
export type NativeCapabilityTaskInput={version:2;kind:'CAPABILITY_TASK';selection:CapabilityTaskSelection;process_start_consent:boolean;remote_mcp_connect_consent:boolean;task_root_operation_id:string|null};
export type NativeReviewInput=NativeReadonlyReviewInput|NativeCapabilityTaskInput;
export type NativeReviewCatalog={schema:'eeas.native-review-capabilities.v1';capability_binding_sha256:string;skills:{id:string;title:string;config_ref:NativeRef;selectable:boolean;reason?:string}[];tools:{id:string;title:string;selection:ReviewToolSelection;selectable:boolean;reason?:string}[];basis_ref:ReviewPlanBasisRef|null;permission_hint:{required_effects:string[];can_submit:boolean;reason?:string;selection_sha256?:string;[key:string]:unknown}};
export type NativeCapabilityTaskCatalog={schema:'eeas.native-capability-task-capabilities.v2';kind:'CAPABILITY_TASK';capability_binding_sha256:string;skills:NativeReviewCatalog['skills'];mcp_catalog:NativeToolsCatalog;permission_hint:NativeReviewCatalog['permission_hint'];read_only:false;effects_applied:false;legacy_review_catalog?:NativeReviewCatalog};
export type NativePageCatalog=NativeReviewCatalog|NativeCapabilityTaskCatalog;
export type ReviewControl={parentOperationId:string;kind:'guidance'|'permission'|'question'|'question-reject';body:{operation_id:string;expected_review_ref:NativeRef;expected_native_message_ref?:never;raw_text?:string;request_ref?:{id:string;sha256:string;generation:number};reply?:'once'|'reject';answers?:string[][]}|{operation_id:string;expected_native_message_ref:NativeRef;expected_review_ref?:never;request_ref?:never;reply?:never;answers?:never;raw_text:string;intent?:'AUTO'|'STEER'|'STATUS'};inspect?:boolean};
export type NativeReviewBridge={getNativeReviewCatalog?:(body:{scope:QuickScope;selection?:NativePageSelection|null;kind?:'CAPABILITY_TASK'})=>Promise<NativePageCatalog>;controlNativeReview?:(input:ReviewControl)=>Promise<{operation_id:string;status:string;[key:string]:unknown}>};
const hash=(s:unknown):s is string=>typeof s==='string'&&/^[a-f0-9]{64}$/.test(s);
const exact=(v:unknown,keys:string[]):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join(',')===[...keys].sort().join(',');
const ref=(v:unknown):v is NativeRef=>exact(v,['id','revision','sha256'])&&typeof v.id==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(v.id)&&Number.isSafeInteger(v.revision)&&Number(v.revision)>0&&hash(v.sha256);
export function reviewInput(value:unknown):NativeReviewInput|null{
 if(value==null)return null;
 if((value as {version?:unknown})?.version===2)return capabilityTaskInput(value);
 if(new TextEncoder().encode(JSON.stringify(value)).length>8192||!exact(value,['version','selection','process_start_consent'])||value.version!==1||typeof value.process_start_consent!=='boolean')throw new Error('技能与工具选择无效');
 const s=value.selection;if(!exact(s,['skill_ref','tool_selection','capability_binding_sha256','basis_ref'])||!hash(s.capability_binding_sha256)||s.skill_ref!==null&&!ref(s.skill_ref)||s.skill_ref===null&&s.tool_selection===null)throw new Error('技能与工具选择无效');
 const t=s.tool_selection;
 if(t!==null){if(!t||typeof t!=='object')throw new Error('工具来源无效');
  if((t as ReviewToolSelection).kind==='BUILTIN_READONLY'){if(!exact(t,['kind','tool_ref'])||!ref(t.tool_ref)||t.tool_ref.id!=='planning_acceptance_coverage'||t.tool_ref.revision!==1||s.basis_ref===null)throw new Error('此工具需要原任务计划依据');}
  else{if(!exact(t,['kind','mcp_tool_selection'])||t.kind!=='USER_MCP')throw new Error('工具来源无效');const d=t.mcp_tool_selection as MCPToolSelectionDraft;if(!exact(d,['capability_binding_sha256','items'])||d.capability_binding_sha256!==s.capability_binding_sha256||!Array.isArray(d.items)||d.items.length!==1)throw new Error('工具来源无效');const item=d.items[0];if(!exact(item,['server_id','config_ref','probe_ref','tool_id','name','input_schema_sha256'])||typeof item.server_id!=='string'||!/^[A-Za-z0-9_-]{1,100}$/.test(item.server_id)||!ref(item.config_ref)||!exact(item.probe_ref,['id','sha256'])||typeof item.probe_ref.id!=='string'||!/^[A-Za-z0-9_-]{1,100}$/.test(item.probe_ref.id)||!hash(item.probe_ref.sha256)||typeof item.tool_id!=='string'||!/^[A-Za-z0-9_-]{1,200}$/.test(item.tool_id)||typeof item.name!=='string'||!item.name.length||item.name.length>256||!hash(item.input_schema_sha256))throw new Error('工具来源无效');}}
 if(s.basis_ref!==null){const b=s.basis_ref;if(!exact(b,['kind','file_link_ref','proposal_ref','source_snapshot_sha256','candidate_sha256'])||b.kind!=='REPORT_PLAN'||!hash(b.source_snapshot_sha256)||!hash(b.candidate_sha256)||!ref(b.proposal_ref)||!exact(b.file_link_ref,['id','project_id','revision','sha256'])||!hash(b.file_link_ref.id)||typeof b.file_link_ref.project_id!=='string'||!/^[A-Za-z0-9_-]{1,100}$/.test(b.file_link_ref.project_id)||!hash(b.file_link_ref.sha256)||!Number.isSafeInteger(b.file_link_ref.revision)||Number(b.file_link_ref.revision)<1)throw new Error('原任务计划依据无效');}
 return structuredClone(value) as unknown as NativeReadonlyReviewInput;
}
function capabilityTaskInput(value:unknown):NativeCapabilityTaskInput{
 if(!exact(value,['version','kind','selection','process_start_consent','remote_mcp_connect_consent','task_root_operation_id'])||value.version!==2||value.kind!=='CAPABILITY_TASK'||typeof value.process_start_consent!=='boolean'||typeof value.remote_mcp_connect_consent!=='boolean'||value.task_root_operation_id!==null&&(typeof value.task_root_operation_id!=='string'||!/^[A-Za-z0-9_-]{1,100}$/.test(value.task_root_operation_id)))throw new Error('技能与工具选择无效');
 const s=value.selection;
 if(!exact(s,['capability_binding_sha256','skill_ref','mcp_tool_selection'])||!hash(s.capability_binding_sha256)||s.skill_ref!==null&&!ref(s.skill_ref)||!Array.isArray(s.mcp_tool_selection)||s.mcp_tool_selection.length>64||s.skill_ref===null&&!s.mcp_tool_selection.length||new TextEncoder().encode(JSON.stringify(s)).length>98304)throw new Error('技能与工具选择无效');
 const ids=new Set<string>(),servers=new Map<string,string>();
 for(const item of s.mcp_tool_selection){
  if(!exact(item,['server_id','config_ref','probe_ref','tool_id','name','input_schema_sha256'])||typeof item.server_id!=='string'||!/^[A-Za-z0-9_-]{1,100}$/.test(item.server_id)||!ref(item.config_ref)||item.config_ref.id!==item.server_id||!exact(item.probe_ref,['id','sha256'])||typeof item.probe_ref.id!=='string'||!/^[A-Za-z0-9_-]{1,100}$/.test(item.probe_ref.id)||!hash(item.probe_ref.sha256)||typeof item.tool_id!=='string'||!/^[A-Za-z0-9_-]{1,200}$/.test(item.tool_id)||ids.has(item.tool_id)||typeof item.name!=='string'||!item.name.length||item.name.length>256||!hash(item.input_schema_sha256))throw new Error('工具来源无效');
  ids.add(item.tool_id);const basis=JSON.stringify([item.config_ref.id,item.config_ref.revision,item.config_ref.sha256,item.probe_ref.id,item.probe_ref.sha256]);if(servers.has(item.server_id)&&servers.get(item.server_id)!==basis)throw new Error('工具来源无效');servers.set(item.server_id,basis);
 }
 return structuredClone(value) as unknown as NativeCapabilityTaskInput;
}
export function isCapabilityTaskSelection(value:NativePageSelection):value is CapabilityTaskSelection{return Object.hasOwn(value,'mcp_tool_selection');}
export function pageReviewInput(selection:NativePageSelection|null,taskRoot:string|null=null):NativeReviewInput|null{return selection?reviewInput(isCapabilityTaskSelection(selection)?{version:2,kind:'CAPABILITY_TASK',selection,process_start_consent:false,remote_mcp_connect_consent:false,task_root_operation_id:taskRoot}:{version:1,selection,process_start_consent:false}):null;}
export function resetReviewConsent(value:NativeReviewInput):NativeReviewInput{return value.version===2?{...value,process_start_consent:false,remote_mcp_connect_consent:false}:{...value,process_start_consent:false};}
export function capabilityTaskSelection(catalog:NativeCapabilityTaskCatalog,skillId:string,toolIds:string[]):CapabilityTaskSelection|null{
 if(!skillId&&!toolIds.length)return null;
 const skill=catalog.skills.find(row=>row.id===skillId);if(skillId&&!skill?.selectable)throw new Error('所选技能或工具已变化，请重新选择');
 const tools=chooseMCPTools(assertNativeToolsCatalog(catalog.mcp_catalog),toolIds);
 if(tools.capability_binding_sha256!==catalog.capability_binding_sha256)throw new Error('所选技能或工具已变化，请重新选择');
 return capabilityTaskInput({version:2,kind:'CAPABILITY_TASK',selection:{capability_binding_sha256:catalog.capability_binding_sha256,skill_ref:skill?.config_ref??null,mcp_tool_selection:tools.items},process_start_consent:false,remote_mcp_connect_consent:false,task_root_operation_id:null}).selection;
}
export function reviewSelection(catalog:NativeReviewCatalog,skillId:string,toolId:string):NativeReviewSelection|null{
 if(!skillId&&!toolId)return null;
 const skill=catalog.skills.find(row=>row.id===skillId),tool=catalog.tools.find(row=>row.id===toolId);
 if(skillId&&!skill?.selectable||toolId&&!tool?.selectable)throw new Error('所选技能或工具已变化，请重新选择');
 return (reviewInput({version:1,selection:{skill_ref:skill?.config_ref??null,tool_selection:tool?.selection??null,capability_binding_sha256:catalog.capability_binding_sha256,basis_ref:catalog.basis_ref??null},process_start_consent:false}) as NativeReadonlyReviewInput).selection;
}
export function sameReview(a:unknown,b:unknown):boolean{const canonical=(v:unknown):string=>v===null||typeof v!=='object'?JSON.stringify(v):Array.isArray(v)?'['+v.map(canonical).join(',')+']':'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical((v as Record<string,unknown>)[k])).join(',')+'}';return canonical(a??null)===canonical(b??null);}
export function readReviewDraft(storage:Pick<Storage,'getItem'>,conversation:string):NativeReviewInput|null{try{const v=reviewInput(JSON.parse(storage.getItem('eeas-native-review-draft:'+conversation)??'null'));return v?resetReviewConsent(v):null;}catch{return null;}}
export function saveReviewDraft(storage:Pick<Storage,'setItem'>,conversation:string,value:NativeReviewInput|null){storage.setItem('eeas-native-review-draft:'+conversation,JSON.stringify(value));}
function reviewEffects(value:NativeReviewInput|null,catalog?:NativePageCatalog,hintSelection?:NativePageSelection|null):string[]{
 if(!value)return [];
 const current=value.version===1&&catalog?.schema==='eeas.native-capability-task-capabilities.v2'?catalog.legacy_review_catalog:catalog;
 const hint=current?.permission_hint,expires=typeof hint?.expires_at==='string'?Date.parse(hint.expires_at):NaN;
 if(hint&&sameReview(value.selection,hintSelection)&&(!Number.isFinite(expires)||expires>Date.now()))return hint.required_effects;
 return value.version===1?value.selection.tool_selection!==null?['PROCESS_START']:[]:value.selection.mcp_tool_selection.length?['PROCESS_START','REMOTE_MCP_CONNECT']:[];
}
export function needsReviewProcess(value:NativeReviewInput|null,catalog?:NativePageCatalog,hintSelection?:NativePageSelection|null):boolean{return reviewEffects(value,catalog,hintSelection).includes('PROCESS_START');}
export function needsReviewRemote(value:NativeReviewInput|null,catalog?:NativePageCatalog,hintSelection?:NativePageSelection|null):boolean{return reviewEffects(value,catalog,hintSelection).includes('REMOTE_MCP_CONNECT');}

export function consentedReviewInput(value:NativeReviewInput|null,catalog?:NativePageCatalog,hintSelection?:NativePageSelection|null):NativeReviewInput|null{if(!value)return null;const checked=reviewInput(value)!;return checked.version===2?{...checked,process_start_consent:needsReviewProcess(checked,catalog,hintSelection)&&checked.process_start_consent,remote_mcp_connect_consent:needsReviewRemote(checked,catalog,hintSelection)&&checked.remote_mcp_connect_consent}:{...checked,process_start_consent:needsReviewProcess(checked,catalog,hintSelection)&&checked.process_start_consent};}
