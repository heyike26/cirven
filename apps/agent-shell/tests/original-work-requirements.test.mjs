// AUTHORED_NOT_RUN: actual typed wire reader; API transport is a UNIT boundary.
import test from 'node:test';
import assert from 'node:assert/strict';
import {OriginalWorkRequirementsApi,workRequirementScope} from '../src/original-work-requirements.ts';
import {configurationHash as hash} from '../src/conversation-configuration.ts';
const session={project_id:'project',session_ref:{id:'session',revision:1,sha256:'a'.repeat(64)},generation:2,workspace_ref:{id:'b'.repeat(64),revision:1,binding_sha256:'c'.repeat(64)},original_session_id:'ses_root'},root='original_root';
const rawHash=async value=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)))].map(n=>n.toString(16).padStart(2,'0')).join('');
async function scene(){const scope=workRequirementScope(session,root),basis={project_id:session.project_id,session_ref:session.session_ref,workspace_ref:session.workspace_ref,root_operation_id:root,root_generation:1,native_session_id:'ses_root',native_message_id:'msg_user',request_sha256:'d'.repeat(64)},rootSha=await hash(basis),reference={id:'requirements',revision:1,sha256:'e'.repeat(64)},link={kind:'ORIGINAL_WORK_REQUIREMENT_OPERATION',version:1,project_id:'project',session_ref:session.session_ref,root_operation_id:root,root_sha256:rootSha,operation_id:'link_original',request_sha256:'f'.repeat(64),revision:1,action:'ASSOCIATE',requirement_ref:reference,requirement_ids:['R1'],acceptance_ids:['A1'],selection_sha256:'1'.repeat(64),recorded_at:'2026-11-03T10:00:00Z',metadata_only:true,model_calls:0,commands_started:0,source_files_written:0,permission_granted:false,original_task_status_changed:false,whole_task_acceptance:'NOT_ASSESSED'},calls=[];
 const impact={operation_id:link.operation_id,requirement_ref:reference,requirement_ids:['R1'],acceptance_ids:['A1'],selection_sha256:link.selection_sha256,automatic_adoption:false,title:'Shipping fee',current_ref:reference,review_status:'CURRENT',changed_requirement_ids:[],changed_acceptance_ids:[],stale_source_requirement_ids:[],sources:[{id:'R1',status:'UNSOURCED'}],current_bundle_confirmed:true,requires_explicit_adoption:false};
 const selected={ref:reference,title:'Shipping fee',confirmed:true,origin:'manual',current_revision:1,requirements:[{id:'R1',text_excerpt:'Charge each block',text_length:17,text_sha256:await rawHash('Charge each block'),text_truncated:false,source_count:0}],acceptance:[{id:'A1',text_excerpt:'1001g is 700',text_length:12,text_sha256:await rawHash('1001g is 700'),text_truncated:false,requirement_ids:['R1']}],sources:[{id:'R1',status:'UNSOURCED'}],detail:null};
 const base={kind:'ORIGINAL_WORK_REQUIREMENTS',version:1,project_id:'project',scope,root_basis:basis,root_sha256:rootSha,root_status:'UNKNOWN_OUTCOME',label:'Fix shipping fee',revision:1,links:[link],impacts:[impact],selected,selected_issue:null,read_only:true,model_calls:0,commands_started:0,source_files_written:0,automatic_adoption:false,whole_task_acceptance:'NOT_ASSESSED',current_workspace_verified:false,target_environment_verified:false,requirement_source_scope:'REGISTERED_DOCUMENT_VERSIONS_ONLY',coverage:'EXPLICIT_SELECTED_REQUIREMENTS_ONLY_NOT_TEST_CASE_COVERAGE_OR_TARGET_ACCEPTANCE'};
 let tamper=value=>value;
 const client={request:async(path,method,body,_signal,retry)=>{calls.push({path,method,body,retry});if(path.endsWith('/operations/inspect'))return structuredClone(tamper(link));return tamper({...structuredClone(base),query_sha256:await hash(body),catalog:{entries:[],offset:body.catalog_offset,limit:8,next_offset:null,scope:'SAME_EXECUTION_PROJECT_ONLY',included:body.include_catalog}});}};
 return {api:new OriginalWorkRequirementsApi(client),base,calls,rootSha,reference,link,change:fn=>{tamper=fn;}};
}
test('saved work inspection sends one exact readonly request without catalog or execution',async()=>{
 const f=await scene(),r=await f.api.inspect(session,root);assert.equal(r.root_status,'UNKNOWN_OUTCOME');assert.equal(r.scope.generation,2);assert.equal(r.root_basis.root_generation,1);assert.equal(f.calls.length,1);assert.match(f.calls[0].path,/work-requirements\/inspect$/);assert.equal(f.calls[0].retry,false);assert.equal(f.calls[0].body.include_catalog,false);assert.equal(r.whole_task_acceptance,'NOT_ASSESSED');
});
test('foreign root or scope hashes cannot be displayed as this original work',async()=>{
 const f=await scene();for(const change of [v=>({...v,root_sha256:'2'.repeat(64)}),v=>({...v,scope:{...v.scope,generation:3}}),v=>({...v,root_basis:{...v.root_basis,root_operation_id:'foreign'}})]){f.change(change);await assert.rejects(()=>f.api.inspect(session,root),/ORIGINAL_WORK_REQUIREMENTS_CHANGED/);}
});
test('metadata association cannot be relabeled as accepted task or write permission',async()=>{
 const f=await scene();for(const change of [v=>({...v,whole_task_acceptance:'PASSED'}),v=>({...v,current_workspace_verified:true}),v=>({...v,links:[{...v.links[0],permission_granted:true}]}),v=>({...v,impacts:[{...v.impacts[0],automatic_adoption:true}]})]){f.change(change);await assert.rejects(()=>f.api.inspect(session,root),/ORIGINAL_WORK_REQUIREMENTS_CHANGED/);}
});
test('unchanged selected items on newer bundle retain their original reference and explicit adoption boundary',async()=>{
 const f=await scene();f.change(v=>({...v,impacts:[{...v.impacts[0],current_ref:{...f.reference,revision:2,sha256:'3'.repeat(64)},review_status:'SELECTED_UNCHANGED_NEWER_BUNDLE',requires_explicit_adoption:true}]}));const r=await f.api.inspect(session,root);assert.deepEqual(r.links[0].requirement_ref,f.reference);assert.equal(r.impacts[0].current_ref.revision,2);
 f.change(v=>({...v,impacts:[{...v.impacts[0],review_status:'SELECTED_UNCHANGED_NEWER_BUNDLE',changed_requirement_ids:['R1']}]}));await assert.rejects(()=>f.api.inspect(session,root));
});
test('full selected text is checked against the immutable excerpt fingerprint',async()=>{
 const f=await scene();f.change(v=>({...v,selected:{...v.selected,detail:{kind:'requirement',id:'R1',text:'Charge each block'}}}));const r=await f.api.inspect(session,root,{selected:f.reference,detail:{kind:'requirement',id:'R1'}});assert.equal(r.selected.detail.text,'Charge each block');
 f.change(v=>({...v,selected:{...v.selected,detail:{kind:'requirement',id:'R1',text:'Foreign text'}}}));await assert.rejects(()=>f.api.inspect(session,root,{selected:f.reference,detail:{kind:'requirement',id:'R1'}}));
});
test('a selected requirement from another version cannot replace the requested frozen view',async()=>{
 const f=await scene();await assert.rejects(()=>f.api.inspect(session,root,{selected:{...f.reference,revision:2,sha256:'4'.repeat(64)}}));
});
test('original receipt lookup is readonly and preserves original operation identity',async()=>{
 const f=await scene(),r=await f.api.operation(session,root,f.rootSha,f.link.operation_id,f.link.request_sha256);assert.deepEqual(r,f.link);assert.equal(f.calls[0].body.generation,2);assert.equal(f.calls[0].body.request_sha256,f.link.request_sha256);assert.match(f.calls[0].path,/operations\/inspect$/);assert.equal(f.calls.length,1);
});
test('changed receipt and mismatched source-status IDs are rejected without retries',async()=>{
 const f=await scene();f.change(v=>({...v,request_sha256:'5'.repeat(64)}));await assert.rejects(()=>f.api.operation(session,root,f.rootSha,f.link.operation_id,f.link.request_sha256));
 f.change(v=>({...v,selected:{...v.selected,sources:[{id:'FOREIGN',status:'CURRENT'}]}}));await assert.rejects(()=>f.api.inspect(session,root));assert.equal(f.calls.length,2);
});
