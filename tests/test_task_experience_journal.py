"""AUTHORED_NOT_RUN: actual TS journal/actions + UNIT storage/transport/locks.

No browser, OS locking, native owner, SDK/model or runtime qualification. Faults
represent controlled boundaries; source identity receipts are explicit UNIT data.
"""
from pathlib import Path
import json,subprocess
import pytest

ROOT=Path(__file__).resolve().parents[1]
HARNESS=r'''
import {ExperienceJournal,ExperienceActions,experienceJournal,experienceUnresolved,flushExperienceDrafts,experienceLocalKey} from '__JOURNAL__';
import {configurationHash as hash,configurationCanonical as canonical} from '__CANONICAL__';
import {webcrypto} from 'node:crypto';
if(!globalThis.crypto)Object.defineProperty(globalThis,'crypto',{value:webcrypto});
const H='a'.repeat(64),R='b'.repeat(64),ref=(id,revision=1)=>({id,revision,sha256:H});
const session={project_id:'project',session_ref:ref('e'.repeat(32)),generation:1,workspace_ref:{id:H,revision:1,binding_sha256:R}};
const same=(a,b)=>canonical(a)===canonical(b),clone=v=>structuredClone(v);
function check(v,message='Experience journal scenario failed'){if(!v)throw Error(message);}
async function rejects(run){let failed=false;try{await run();}catch{failed=true;}check(failed,'Expected a protected failure');}
class Memory{
 map=new Map();fail=false;onSet;
 getItem(key){return this.map.get(key)??null;}
 setItem(key,value){if(this.fail)throw Error('UNIT_STORAGE_UNAVAILABLE');this.map.set(key,value);this.onSet?.(key,value);}
}
const tails=new Map(),locks={request(name,options,run){const next=(tails.get(name)??Promise.resolve()).catch(()=>{}).then(()=>{if(options.signal?.aborted)throw Error('UNIT_LOCK_ABORTED');return run();});tails.set(name,next);return next;}};
const draft=(action='PROPOSE',note='Original human draft 😀')=>({action,generation:1,root_sha256:R,candidate_ref:null,review_ref:null,fields:{note}});
const proposal=(operation='candidate',root='work')=>({session_ref:session.session_ref,generation:1,workspace_ref:session.workspace_ref,root_operation_id:root,root_sha256:R,operation_id:operation,title:'Explicit candidate',observation:'Observed original failure',proposed_use:'Check exact original evidence',limitations:'Not validated',sources:[],proposal:{kind:'SKILL',id:'retain-original',content:'---\nname: retain-original\ndescription: Preserve original work\n---\nKeep the original evidence.'}});
const validate=(operation='validate')=>({operation_id:operation,candidate_ref:ref('candidate'),review_ref:ref('review'),validation_confirmed:true,process_boundary_acknowledged:true,allowed_effects:['PROCESS_START']});
const source=(operation='withdraw',action='WITHDRAW')=>({operation_id:operation,candidate_ref:ref('candidate'),expected_revision:0,action,confirmed:true,statement:'Explicit source decision; keep original work'});
const pending=(operation='pause',mode='PAUSE',revision=0)=>({operation_id:operation,activation_operation_id:'activate',activation_request_sha256:R,expected_control_revision:revision,mode,expected_current_configuration_ref:null,confirmed:true});
async function packet(owner,action,input){return {kind:'EXPERIENCE_REQUEST_PREPARATION',version:1,project_id:owner.project_id,session_id:owner.session_ref.id,session_ref:owner.session_ref,action,request:clone(input),request_sha256:await hash({UNIT_SERVER_REQUEST:input}),read_only:true,metadata_writes:0,model_calls:0,processes_started:0,permission_granted:false,automatic_replay:false,authority_checked_on_actual_command:true};}
function receipt(p){const r=p.request,base={kind:p.action,operation_id:r.operation_id,request_sha256:p.action==='RECOVER_CONFIGURATION'?r.request_sha256:p.request_sha256,automatic_replay:false};
 if(p.action==='PROPOSE'||p.action==='REVIEW')return {...base,record_ref:ref(r.operation_id),server_output:'NEVER_CACHE_SERVER_OUTPUT'};
 if(p.action==='TOOL_VALIDATE'||p.action==='SKILL_VALIDATE')return {...base,candidate_ref:r.candidate_ref,review_ref:r.review_ref,status:'PASS',qualified:true,result_sha256:R,answer:'NEVER_CACHE_SERVER_OUTPUT'};
 if(p.action==='SOURCE_DECISION')return {...base,decision_ref:ref(r.operation_id),decision_state:r.action==='FORGET'?'FORGOTTEN':r.action==='RESTORE'?'ACTIVE':'WITHDRAWN'};
 if(p.action==='PENDING_CONTROL')return {...base,candidate_ref:ref('candidate'),decision_ref:ref(r.operation_id),decision_state:r.mode==='COMPLETE_PAUSED_METADATA'?null:r.mode==='RESUME_PAUSED'?'ACTIVE':'PAUSED',control_record_kind:r.mode==='COMPLETE_PAUSED_METADATA'?'COMPLETION_PACKET_RESERVED_ONLY':'CONTROL_DECISION',capability_control:{revision:r.expected_control_revision+(r.mode==='COMPLETE_PAUSED_METADATA'?0:1),effective_state:r.mode==='RESUME_PAUSED'?'ACTIVE':'PAUSED'},observation:{status:'APPLIED_CURRENT'},local_finalization_needed:false,configuration_finalization_needed:false};
 return {...base,observation:{status:'APPLIED_CURRENT'},local_finalization_needed:false};
}
async function scene(action='PROPOSE',storage=new Memory(),root='work'){
 const j=new ExperienceJournal(session,root,storage,locks);await j.validate();j.edit(draft(action));await j.flush();const calls=[],saved=new Map();let dispatchHook,prepareHook,lookupHook;
 const reader={async prepare(owner,action,input){calls.push({kind:'PREPARE',action,input:clone(input)});if(prepareHook)await prepareHook(owner,action,input);return packet(owner,action,input);},async dispatch(owner,p){calls.push({kind:'DISPATCH',packet:clone(p)});const value=dispatchHook?await dispatchHook(p):receipt(p);saved.set(p.request.operation_id,clone(value));return value;},async lookup(owner,p){calls.push({kind:'LOOKUP',packet:clone(p)});if(lookupHook)return lookupHook(p);const value=saved.get(p.request.operation_id);if(!value)throw Object.assign(Error('UNIT_NOT_FOUND'),{status:404});return clone(value);}};
 return {j,storage,reader,calls,saved,actions:new ExperienceActions(j,reader),hooks:{dispatch:fn=>dispatchHook=fn,prepare:fn=>prepareHook=fn,lookup:fn=>lookupHook=fn},sent:()=>calls.filter(c=>c.kind==='DISPATCH'),attempt:()=>j.value().attempts[0]};
}
async function preparedAttempt(s,action,input){const p=await packet(s.j.owner,action,input),a={packet:p,witness_sha256:await hash(p),phase:'PREPARED',initial_unknown:false,last_lookup:'NOT_CHECKED',summary:null,retained_note:null,transmissions:0};await s.j.serial(async()=>s.j.save(a));return a;}
'''

CASES={
'durable_original_packet_and_dispatch_marker_before_wire':r'''
const s=await scene();const input=proposal();s.hooks.dispatch(p=>{const raw=JSON.parse(s.storage.getItem(s.j.key)),a=raw.attempts[0];check(a.phase==='PENDING'&&a.transmissions===1&&same(a.packet,p)&&same(a.packet.request,input));return receipt(p);});
await s.actions.submit('PROPOSE',input);check(s.sent().length===1&&s.attempt().phase==='OBSERVED');check(s.j.draft().fields.note==='Original human draft 😀');check(!s.storage.getItem(s.j.key).includes('NEVER_CACHE_SERVER_OUTPUT'));check(!JSON.stringify(s.j.view()).includes('"packet"'));
''',
'packet_write_failure_keeps_unsent_original_and_requires_explicit_first_dispatch':r'''
const s=await scene();s.hooks.prepare(()=>{s.storage.fail=true;});await rejects(()=>s.actions.submit('PROPOSE',proposal()));check(s.sent().length===0&&s.j.unsaved()&&s.attempt().phase==='PREPARED');s.storage.fail=false;s.hooks.prepare(undefined);await flushExperienceDrafts();const a=s.attempt();await rejects(()=>s.actions.dispatchPrepared(a.witness_sha256,false));await s.actions.dispatchPrepared(a.witness_sha256,true);check(s.sent().length===1&&s.attempt().packet.request.operation_id==='candidate');
''',
'restart_pending_is_unknown_and_never_first_dispatch_replayed':r'''
const s=await scene('TOOL_VALIDATE');s.hooks.dispatch(()=>{throw Error('UNIT_UNKNOWN');});await s.actions.submit('TOOL_VALIDATE',validate());const raw=JSON.parse(s.storage.getItem(s.j.key));raw.attempts[0].phase='PENDING';s.storage.setItem(s.j.key,JSON.stringify(raw));
const j=new ExperienceJournal(session,'work',s.storage,locks);await j.validate();check(j.value().attempts[0].phase==='UNKNOWN');const a=new ExperienceActions(j,s.reader),w=j.value().attempts[0].witness_sha256;await rejects(()=>a.dispatchPrepared(w,true));await a.inspect(w);check(s.sent().length===1&&j.value().attempts[0].last_lookup==='NOT_FOUND'&&j.value().attempts[0].initial_unknown);
''',
'restart_prepared_zero_reservations_can_only_explicitly_submit_same_packet':r'''
const s=await scene();const a=await preparedAttempt(s,'PROPOSE',proposal());const j=new ExperienceJournal(session,'work',s.storage,locks);await j.validate();check(!j.unsaved());await new ExperienceActions(j,s.reader).dispatchPrepared(a.witness_sha256,true);check(s.sent().length===1&&same(s.sent()[0].packet,a.packet));
''',
'unknown_positive_does_not_allow_a_new_nonce_for_same_validation':r'''
const s=await scene('TOOL_VALIDATE');s.hooks.dispatch(()=>{throw Error('UNIT_UNKNOWN');});await s.actions.submit('TOOL_VALIDATE',validate());await rejects(()=>s.actions.submit('TOOL_VALIDATE',validate('new_nonce')));check(s.sent().length===1&&s.j.value().attempts.length===1);
''',
'withdrawal_remains_available_while_restore_is_unknown':r'''
const s=await scene('SOURCE_DECISION');s.hooks.dispatch(p=>{if(p.request.action==='RESTORE')throw Error('UNIT_UNKNOWN_RESTORE');return receipt(p);});await s.actions.submit('SOURCE_DECISION',source('restore','RESTORE'));await s.actions.submit('SOURCE_DECISION',source('withdraw','WITHDRAW'));check(s.sent().length===2&&s.j.value().attempts[0].initial_unknown&&s.j.value().attempts[1].summary.decision_state==='WITHDRAWN');
''',
'negative_pause_remains_available_while_resume_is_unknown':r'''
const s=await scene('PENDING_CONTROL');s.hooks.dispatch(p=>{if(p.request.mode==='RESUME_PAUSED')throw Error('UNIT_UNKNOWN_RESUME');return receipt(p);});await s.actions.submit('PENDING_CONTROL',pending('resume','RESUME_PAUSED',1));await s.actions.submit('PENDING_CONTROL',pending('pause','PAUSE',2));check(s.sent().length===2&&s.j.value().attempts[1].summary.control_effective_state==='PAUSED');
''',
'editing_during_readonly_preparation_preserves_new_draft_and_prevents_wire':r'''
const s=await scene();let enter,release;const entered=new Promise(r=>enter=r),held=new Promise(r=>release=r);s.hooks.prepare(()=>{enter();return held;});const work=s.actions.submit('PROPOSE',proposal());await entered;s.j.edit(draft('PROPOSE','New human input while preparing'));release();await rejects(()=>work);check(s.sent().length===0&&s.j.draft().fields.note==='New human input while preparing');await flushExperienceDrafts();check(JSON.parse(s.storage.getItem(s.j.key)).draft.fields.note==='New human input while preparing');
''',
'reentrant_input_during_save_is_flushed_without_clearing_latest_dirty_draft':r'''
const s=await scene();let once=false;const off=s.j.subscribe(()=>{if(s.j.unsaved()&&s.j.running()&&!once){once=true;s.j.edit(draft('PROPOSE','Latest reentrant human input'));}});s.j.edit(draft('PROPOSE','Earlier input'));await flushExperienceDrafts();off();check(!s.j.unsaved()&&JSON.parse(s.storage.getItem(s.j.key)).draft.fields.note==='Latest reentrant human input');
''',
'cross_window_conflict_keep_local_and_preserve_saved_copy_and_receipt':r'''
const s=await scene();const other=new ExperienceJournal(session,'work',s.storage,locks);await other.validate();await s.actions.submit('PROPOSE',proposal());s.j.edit(draft('PROPOSE','Saved other-window draft'));await s.j.flush();other.edit(draft('PROPOSE','Local manual draft'));await rejects(()=>other.flush());await other.resolve('LOCAL');check(other.draft().fields.note==='Local manual draft'&&other.value().copies.some(c=>c.draft.fields.note==='Saved other-window draft')&&other.value().attempts.length===1);
''',
'cross_window_conflict_use_saved_and_preserve_local_copy':r'''
const s=await scene();const other=new ExperienceJournal(session,'work',s.storage,locks);await other.validate();s.j.edit(draft('PROPOSE','Saved other-window draft'));await s.j.flush();other.edit(draft('PROPOSE','Local manual draft'));await rejects(()=>other.flush());await other.resolve('SAVED');check(other.draft().fields.note==='Saved other-window draft'&&other.value().copies.some(c=>c.draft.fields.note==='Local manual draft'));
''',
'received_result_write_failure_preserves_confirmation_and_never_resends':r'''
const s=await scene();s.hooks.dispatch(p=>{s.storage.fail=true;return receipt(p);});await rejects(()=>s.actions.submit('PROPOSE',proposal()));check(s.sent().length===1&&s.j.unsaved()&&s.attempt().summary.record_ref.id==='candidate');s.storage.fail=false;await flushExperienceDrafts();check(!s.j.unsaved()&&s.sent().length===1&&JSON.parse(s.storage.getItem(s.j.key)).attempts[0].phase==='OBSERVED');
''',
'tampered_local_packet_witness_blocks_commands_without_rewriting_corrupt_original':r'''
const s=await scene();await preparedAttempt(s,'PROPOSE',proposal());const raw=JSON.parse(s.storage.getItem(s.j.key));raw.attempts[0].packet.request.observation='TAMPERED';const corrupt=JSON.stringify(raw);s.storage.setItem(s.j.key,corrupt);const j=new ExperienceJournal(session,'work',s.storage,locks);await rejects(()=>j.validate());await rejects(()=>new ExperienceActions(j,s.reader).dispatchPrepared(raw.attempts[0].witness_sha256,true));check(s.storage.getItem(s.j.key)===corrupt&&s.sent().length===0);
''',
'later_missing_receipt_keeps_original_proof_and_reports_latest_not_found':r'''
const s=await scene();await s.actions.submit('PROPOSE',proposal());const a=s.attempt();s.hooks.lookup(()=>{throw Object.assign(Error('UNIT_MISSING'),{status:404});});await s.actions.inspect(a.witness_sha256);check(s.attempt().summary.record_ref.id==='candidate'&&s.attempt().last_lookup==='NOT_FOUND'&&experienceUnresolved(s.attempt())&&s.sent().length===1);
''',
'withdrawn_projection_keeps_hash_proof_but_never_cached_answer_or_permission':r'''
const s=await scene('TOOL_VALIDATE');await s.actions.submit('TOOL_VALIDATE',validate());const a=s.attempt();s.hooks.lookup(p=>({...receipt(p),result_sha256:undefined,review_ref:undefined,qualified:false,content_withheld:true,source_policy:{availability:'FORGOTTEN',revision:1}}));await s.actions.inspect(a.witness_sha256);check(s.attempt().summary.result_sha256===R&&s.attempt().summary.qualified===false&&s.attempt().summary.source_availability==='FORGOTTEN');check(!s.storage.getItem(s.j.key).includes('NEVER_CACHE_SERVER_OUTPUT'));
''',
'only_known_paused_metadata_completion_can_reuse_exact_original_packet_explicitly':r'''
const s=await scene('PENDING_CONTROL');let first=true,done=false;s.hooks.dispatch(p=>{if(first){first=false;throw Error('UNIT_UNKNOWN_METADATA');}done=true;return receipt(p);});s.hooks.lookup(p=>done?receipt(p):({...receipt(p),observation:{status:'UNKNOWN_NO_SAVED_NATIVE_RECEIPT'},configuration_finalization_needed:true}));await s.actions.submit('PENDING_CONTROL',pending('complete','COMPLETE_PAUSED_METADATA',1));const a=s.attempt();await rejects(()=>s.actions.completeKnownMetadata(a.witness_sha256,false));await s.actions.completeKnownMetadata(a.witness_sha256,true);check(s.sent().length===2&&same(s.sent()[0].packet,s.sent()[1].packet)&&s.attempt().transmissions===2&&!experienceUnresolved(s.attempt()));
''',
'unknown_tool_validation_cannot_be_replayed_as_metadata_recovery':r'''
const s=await scene('TOOL_VALIDATE');s.hooks.dispatch(()=>{throw Error('UNIT_UNKNOWN_TOOL');});s.hooks.lookup(p=>({...receipt(p),status:'UNKNOWN_OR_IN_PROGRESS',result_sha256:undefined,qualified:false}));await s.actions.submit('TOOL_VALIDATE',validate());await rejects(()=>s.actions.completeKnownMetadata(s.attempt().witness_sha256,true));check(s.sent().length===1);
''',
'positive_admission_reserves_final_slots_for_negative_control':r'''
const s=await scene();const state=s.j.value();for(let i=0;i<240;i++){const p=await packet(s.j.owner,'PROPOSE',proposal('old_'+i));const summary={kind:'PROPOSE',operation_id:p.request.operation_id,request_sha256:p.request_sha256,record_ref:ref(p.request.operation_id),decision_ref:null,candidate_ref:null,review_ref:null,result_sha256:null,status:null,native_status:null,decision_state:null,qualified:null,content_withheld:false,source_availability:null,source_revision:null,control_revision:null,control_effective_state:null,local_finalization_needed:null,configuration_finalization_needed:null,control_record_kind:null};state.attempts.push({packet:p,witness_sha256:await hash(p),phase:'OBSERVED',initial_unknown:false,last_lookup:'FOUND',summary,retained_note:null,transmissions:1});}s.storage.setItem(s.j.key,JSON.stringify(state));const j=new ExperienceJournal(session,'work',s.storage,locks);await j.validate();const a=new ExperienceActions(j,s.reader);await rejects(()=>a.submit('PROPOSE',proposal('new_positive')));await a.submit('SOURCE_DECISION',source('last_negative'));check(s.sent().length===1&&j.value().attempts.length===241);
''',
'network_owner_stays_cached_while_hidden_and_other_work_journals_open':r'''
const storage=new Memory(),j=await experienceJournal(storage,session,'work1',locks);const calls=[];let enter,release;const entered=new Promise(r=>enter=r),held=new Promise(r=>release=r),reader={prepare:async(o,a,r)=>{enter();await held;return packet(o,a,r);},dispatch:async(o,p)=>{calls.push(p);return receipt(p);}};const running=new ExperienceActions(j,reader).submit('SOURCE_DECISION',source());await entered;for(let i=2;i<=9;i++)await experienceJournal(storage,session,'work'+i,locks);check(await experienceJournal(storage,session,'work1',locks)===j&&j.running());release();await running;check(calls.length===1&&!j.running());
''',
'normal_flush_includes_collapsed_unobserved_draft_without_network':r'''
const s=await scene();s.j.edit(draft('PROPOSE','Unsaved input in a collapsed work section'));await flushExperienceDrafts();check(JSON.parse(s.storage.getItem(s.j.key)).draft.fields.note==='Unsaved input in a collapsed work section'&&s.calls.length===0);
''',
'retention_never_allows_same_unknown_input_under_new_nonce':r'''
const s=await scene('TOOL_VALIDATE');s.hooks.dispatch(p=>{if(p.request.operation_id==='validate')throw Error('UNIT_UNKNOWN');return receipt(p);});await s.actions.submit('TOOL_VALIDATE',validate());await s.actions.retain(s.attempt().witness_sha256,'Keep the unknown original; this is not proof it was unsent');await rejects(()=>s.actions.submit('TOOL_VALIDATE',validate('duplicate')));const next={...validate('distinct_review'),review_ref:ref('different_review')};await s.actions.submit('TOOL_VALIDATE',next);check(s.sent().length===2&&s.attempt().initial_unknown&&s.attempt().retained_note!==null);
''',
'stable_private_owner_keeps_old_generation_but_isolates_workspace_revision':r'''
const s=await scene();const later={...session,generation:2},j=new ExperienceJournal(later,'work',s.storage,locks);await j.validate();check(j.key===s.j.key&&j.draft().generation===1);const changed={...later,workspace_ref:{...session.workspace_ref,revision:2}},other=new ExperienceJournal(changed,'work',s.storage,locks);await other.validate();check(experienceLocalKey(changed,'work')!==s.j.key&&other.draft()===null&&s.storage.getItem(s.j.key)!==null);
''',
'draft_copy_capacity_preserves_all_existing_copies_and_current_input':r'''
const s=await scene();for(let i=1;i<=4;i++)await s.j.select(draft('PROPOSE','Draft '+i));const before=s.storage.getItem(s.j.key);await rejects(()=>s.j.select(draft('PROPOSE','Draft 5')));check(s.storage.getItem(s.j.key)===before&&s.j.draft().fields.note==='Draft 4'&&s.j.value().copies.length===4);
''',
}


# batch90 additions remain AUTHORED_NOT_RUN, including their transport fixtures.
CASES.update({
'saved_unknown_result_with_hash_still_blocks_same_validation':r'''
for(const status of ['UNKNOWN','UNKNOWN_OR_IN_PROGRESS','CANCELLED']){const s=await scene('TOOL_VALIDATE',new Memory(),status);s.hooks.dispatch(p=>({...receipt(p),status,qualified:false}));await s.actions.submit('TOOL_VALIDATE',validate());check(experienceUnresolved(s.attempt()));await rejects(()=>s.actions.submit('TOOL_VALIDATE',validate('new_nonce')));check(s.sent().length===1);await rejects(()=>s.actions.completeKnownMetadata(s.attempt().witness_sha256,true));}
''',
'cleanup_unknown_is_unresolved_even_with_overall_fail_and_saved_hash':r'''
const s=await scene('SKILL_VALIDATE');s.hooks.dispatch(p=>({...receipt(p),status:'FAIL',qualified:false,cases:[{status:'UNKNOWN'}]}));await s.actions.submit('SKILL_VALIDATE',{...validate(),provider_profile_ref:ref('profile')});check(experienceUnresolved(s.attempt())&&s.attempt().summary.status==='UNKNOWN');await rejects(()=>s.actions.submit('SKILL_VALIDATE',{...validate('new_nonce'),provider_profile_ref:ref('profile')}));check(s.sent().length===1);
''',
'withheld_execution_unknown_remains_unresolved_without_body':r'''
const s=await scene('TOOL_VALIDATE');s.hooks.dispatch(p=>({...receipt(p),status:'SOURCE_BLOCKED',execution_status:'UNKNOWN',qualified:false,content_withheld:true}));await s.actions.submit('TOOL_VALIDATE',validate());check(experienceUnresolved(s.attempt()));await s.actions.submit('SOURCE_DECISION',source());check(s.sent().length===2);
''',
'successful_stage_releases_only_exact_snapshot_and_restores_hidden_fields':r'''
const s=await scene();const original={...s.j.draft(),fields:{...s.j.draft().fields,hidden_original:'Preserve other transport fields 😀'}};s.j.edit(original);await s.actions.submit('PROPOSE',proposal());const a=s.attempt();check(s.j.view().attempts[0].has_draft_snapshot);await s.j.releaseRecordedDraft(a.witness_sha256);check(s.j.draft()===null&&s.j.value().copies.length===0);await s.j.restoreRecordedDraft(a.witness_sha256);check(same(s.j.draft(),original)&&s.sent().length===1);
''',
'late_result_release_keeps_newer_manual_input':r'''
const s=await scene();s.hooks.dispatch(p=>{s.j.edit(draft('PROPOSE','New manual input during dispatch'));return receipt(p);});await s.actions.submit('PROPOSE',proposal());await s.j.releaseRecordedDraft(s.attempt().witness_sha256);check(s.j.draft().fields.note==='New manual input during dispatch'&&s.attempt().draft_snapshot.fields.note==='Original human draft 😀');
''',
'unknown_execution_cannot_release_original_draft':r'''
const s=await scene('TOOL_VALIDATE');s.hooks.dispatch(p=>({...receipt(p),status:'UNKNOWN',qualified:false}));await s.actions.submit('TOOL_VALIDATE',validate());await rejects(()=>s.j.releaseRecordedDraft(s.attempt().witness_sha256));check(s.j.draft()!==null&&s.sent().length===1);
''',
'scope_lease_lost_during_prepare_prevents_first_dispatch':r'''
const s=await scene();let current=true;s.hooks.prepare(()=>{current=false;});await rejects(()=>s.actions.submit('PROPOSE',proposal(),()=>current));check(s.sent().length===0&&s.j.draft().fields.note==='Original human draft 😀');
''',
'explicit_candidate_switch_parks_unsent_draft_without_losing_original_packet':r'''
const s=await scene();await s.actions.submit('PROPOSE',proposal());s.j.edit(draft('PROPOSE','Unsent candidate edit'));await s.j.flush();await s.j.parkDraft();check(s.j.draft()===null&&s.j.value().attempts.length===1&&s.j.view().copies.length===1);await s.j.copyAction(s.j.view().copies[0].id);check(s.j.draft().fields.note==='Unsent candidate edit'&&s.sent().length===1);
''',
})

@pytest.mark.parametrize('case',list(CASES))
def test_actual_experience_journal_scenario(tmp_path,case):
    source=HARNESS.replace('__JOURNAL__',str(ROOT/'apps/agent-shell/src/task-experience-journal.ts')).replace('__CANONICAL__',str(ROOT/'apps/agent-shell/src/conversation-configuration.ts'))
    entry=tmp_path/'scenario.ts';bundle=tmp_path/'scenario.cjs'
    entry.write_text(source+'\nasync function main(){\n'+CASES[case]+'\nconsole.log(JSON.stringify({case:'+json.dumps(case)+',status:"UNIT_PASS",model_calls:0}));}\nmain().catch(e=>{console.error(e);process.exitCode=1;});\n')
    node=ROOT/'.node-tools/node_modules/node/bin/node';builder=tmp_path/'build.cjs'
    builder.write_text('require('+json.dumps(str(ROOT/'apps/agent-shell/node_modules/esbuild'))+').buildSync({entryPoints:['+json.dumps(str(entry))+'],bundle:true,platform:"node",format:"cjs",outfile:'+json.dumps(str(bundle))+'});')
    built=subprocess.run([str(node),str(builder)],cwd=ROOT,capture_output=True,text=True,timeout=60);assert built.returncode==0,built.stderr
    result=subprocess.run([str(node),str(bundle)],cwd=ROOT,capture_output=True,text=True,timeout=60);assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['case']==case
