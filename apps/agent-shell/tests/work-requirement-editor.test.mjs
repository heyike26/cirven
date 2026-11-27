// AUTHORED_NOT_RUN: real journal/actions and selected-basis functions.
// Storage, Web Locks, saved read API and HTTP are explicit UNIT boundaries.
// This file does not certify OpenCode, Windows, browser rendering or test coverage.
import test from 'node:test';
import assert from 'node:assert/strict';
import {authoredErrorCopy,authoredText} from '../src/authored-ui-copy.ts';
import {configurationHash as hash} from '../src/conversation-configuration.ts';
import {workRequirementScope} from '../src/original-work-requirements.ts';
import {WorkRequirementJournal,WorkRequirementActions,workRequirementDraft,
 workRequirementDraftMatches,workRequirementCanSave,workRequirementUnchanged,
 workRequirementLocalKey,workRequirementPendingLocal,flushWorkRequirementDrafts,
 workRequirementJournal} from '../src/work-requirement-editor.ts';

const session={project_id:'project',session_ref:{id:'session',revision:1,sha256:'a'.repeat(64)},generation:2,
 workspace_ref:{id:'b'.repeat(64),revision:1,binding_sha256:'c'.repeat(64)},original_session_id:'ses_root'};
const root='original_root',ref={id:'requirements',revision:1,sha256:'d'.repeat(64)};
const clone=structuredClone;
const http=status=>Object.assign(Error('UNIT HTTP response'),{status});
function memory(){
 const values=new Map(),tails=new Map();let reject=false;
 return {values,fail:value=>{reject=value;},storage:{getItem:key=>values.get(key)??null,
  setItem:(key,value)=>{if(reject)throw Error('UNIT storage quota');values.set(key,value);}},
  locks:{request:(key,options,run)=>{const next=(tails.get(key)??Promise.resolve()).catch(()=>{}).then(()=>{
   if(options.signal.aborted)throw Error('UNIT lock aborted');return run();});tails.set(key,next);return next;}}};
}
function view(){return {kind:'ORIGINAL_WORK_REQUIREMENTS',version:1,project_id:session.project_id,
 scope:workRequirementScope(session,root),query_sha256:'e'.repeat(64),root_basis:{},root_sha256:'f'.repeat(64),
 root_status:'UNKNOWN_OUTCOME',label:'Fix shipping fee',revision:0,links:[],impacts:[],
 catalog:{entries:[],offset:0,limit:8,next_offset:null,scope:'SAME_EXECUTION_PROJECT_ONLY',included:false},
 selected:{ref:clone(ref),title:'Shipping fee',confirmed:true,origin:'manual',current_revision:1,
 requirements:[{id:'R1',text_excerpt:'Charge per block',text_length:16,text_sha256:'1'.repeat(64),text_truncated:false,source_count:1},
  {id:'R2',text_excerpt:'Keep receipt',text_length:12,text_sha256:'2'.repeat(64),text_truncated:false,source_count:1}],
 acceptance:[{id:'A1',text_excerpt:'1001g is 700',text_length:12,text_sha256:'3'.repeat(64),text_truncated:false,requirement_ids:['R1']}],
 sources:[{id:'R1',status:'CURRENT'},{id:'R2',status:'CURRENT'}],detail:null},selected_issue:null,
 read_only:true,model_calls:0,commands_started:0,source_files_written:0,automatic_adoption:false,
 whole_task_acceptance:'NOT_ASSESSED',current_workspace_verified:false,target_environment_verified:false,
 requirement_source_scope:'REGISTERED_DOCUMENT_VERSIONS_ONLY',coverage:'EXPLICIT_SELECTED_REQUIREMENTS_ONLY_NOT_TEST_CASE_COVERAGE_OR_TARGET_ACCEPTANCE'};}
const selection=()=>({requirement_ref:clone(ref),requirement_ids:['R1'],acceptance_ids:['A1']});
async function receipt(request){return {kind:'ORIGINAL_WORK_REQUIREMENT_OPERATION',version:1,
 project_id:session.project_id,session_ref:clone(session.session_ref),root_operation_id:request.root_operation_id,
 root_sha256:request.root_sha256,operation_id:request.operation_id,request_sha256:await hash(request),
 revision:request.expected_revision+1,action:request.action,requirement_ref:clone(request.requirement_ref),
 requirement_ids:[...request.requirement_ids],acceptance_ids:[...request.acceptance_ids],
 selection_sha256:request.action==='ASSOCIATE'?'4'.repeat(64):null,recorded_at:'2026-11-03T10:00:00Z',
 metadata_only:true,model_calls:0,commands_started:0,source_files_written:0,permission_granted:false,
 original_task_status_changed:false,whole_task_acceptance:'NOT_ASSESSED'};}
async function fixture({draft=true}={}){
 const m=memory(),j=new WorkRequirementJournal(session,root,m.storage,m.locks),initial=view(),events=[];
 if(draft){j.edit(workRequirementDraft(initial,selection()));await j.flush();}
 let fresh=clone(initial),post=async body=>receipt(body),lookup=async()=>{throw http(404);},serial=0;
 const reader={inspect:async(s,r,options)=>{events.push({kind:'read',session:clone(s),root:r,options:clone(options)});return clone(fresh);},
  operation:async(s,r,sha,id,sum)=>{events.push({kind:'lookup',session:clone(s),root:r,sha,id,sum});return lookup(s,r,sha,id,sum);}};
 const transport={request:async(path,method,body,_signal,retry)=>{
  events.push({kind:'post',path,method,body:clone(body),signal:_signal,retry});
  const saved=JSON.parse(m.storage.getItem(j.key));
  assert.equal(saved.attempt.phase,'PENDING');assert.deepEqual(saved.attempt.request,body);
  assert.equal(saved.attempt.checksum,await hash(body));return post(body);
 }};
 const actions=new WorkRequirementActions(j,reader,transport,()=>`link_${++serial}`);
 return {m,j,initial,reader,transport,actions,events,post:fn=>{post=fn;},lookup:fn=>{lookup=fn;},fresh:v=>{fresh=clone(v);},
  restart:()=>new WorkRequirementJournal(session,root,m.storage,m.locks),posts:()=>events.filter(e=>e.kind==='post')};
}

test('normal close waits for the original link queue before its readonly preflight returns',async()=>{
 const f=await fixture();let release,entered;const held=new Promise(r=>release=r),started=new Promise(r=>entered=r);
 f.reader.inspect=async()=>{entered();await held;return clone(f.initial);};
 const submitting=f.actions.submit(session,f.initial,'ASSOCIATE');await started;
 let closed=false;const flushing=flushWorkRequirementDrafts().then(()=>closed=true);
 await new Promise(r=>setImmediate(r));assert.equal(closed,false);
 release();await submitting;await flushing;assert(closed);assert.equal(f.posts().length,1);
});

test('draft identity survives generation changes and isolates root, session, project and workspace',()=>{
 assert.equal(workRequirementLocalKey(session,root),workRequirementLocalKey({...session,generation:3},root));
 for(const [s,r] of [[session,'other_root'],[{...session,project_id:'other'},root],
  [{...session,session_ref:{...session.session_ref,id:'other_session'}},root],
  [{...session,workspace_ref:{...session.workspace_ref,binding_sha256:'9'.repeat(64)}},root]])
  assert.notEqual(workRequirementLocalKey(session,root),workRequirementLocalKey(s,r));
 assert.throws(()=>workRequirementLocalKey({...session,session_ref:{...session.session_ref,revision:2}},root),/ORIGINAL_WORK_REQUIREMENTS_CHANGED/);
});
test('incomplete and missing selections survive restart without becoming valid submissions',async()=>{
 const f=await fixture(),c=selection();c.requirement_ids=['MISSING'];f.j.edit(workRequirementDraft(f.initial,c));await f.j.flush();
 assert.deepEqual(f.restart().value().draft.choice,c);assert(!workRequirementCanSave(f.initial,f.j.value().draft));assert.equal(f.posts().length,0);
});
test('drafts keep fingerprints and IDs without copying requirement text or source bodies',async()=>{
 const f=await fixture(),raw=f.m.storage.getItem(f.j.key);assert(!raw.includes('Charge per block'));assert(!raw.includes('1001g is 700'));
 assert(raw.includes('text_sha256'));assert(raw.includes('R1'));assert(!raw.includes('Keep receipt'));
});
test('selected-only review ignores unselected text and source changes',async()=>{
 const f=await fixture(),next=clone(f.initial);next.selected.requirements[1].text_sha256='5'.repeat(64);next.selected.sources[1].status='SUPERSEDED';
 assert(workRequirementDraftMatches(next,f.j.value().draft));assert(workRequirementCanSave(next,f.j.value().draft));
});
test('selected source changes, current generation and newer bundle require fresh explicit review',async()=>{
 const f=await fixture();for(const update of [v=>v.selected.sources[0].status='SUPERSEDED',v=>v.scope.generation++,
  v=>v.selected.current_revision++,v=>v.selected.requirements[0].text_sha256='5'.repeat(64)]){
  const next=clone(f.initial);update(next);assert(!workRequirementDraftMatches(next,f.j.value().draft));assert(!workRequirementCanSave(next,f.j.value().draft));
 }
 assert.equal(f.posts().length,0);
});
test('acceptance parents must be selected while duplicate legacy parent references remain valid',async()=>{
 const f=await fixture(),v=clone(f.initial);v.selected.acceptance[0].requirement_ids=['R1','R1'];
 const d=workRequirementDraft(v,selection());assert(workRequirementCanSave(v,d));
 v.selected.acceptance[0].requirement_ids.push('R2');assert(!workRequirementCanSave(v,workRequirementDraft(v,selection())));
});
test('human-confirmed historical proposal retains origin and can be explicitly linked',async()=>{
 const f=await fixture(),v=clone(f.initial);v.selected.origin='mock';assert(workRequirementCanSave(v,workRequirementDraft(v,selection())));
 v.selected.confirmed=false;assert(!workRequirementCanSave(v,workRequirementDraft(v,selection())));
});
test('unchanged active choices are compared as sets without creating a redundant request',async()=>{
 const f=await fixture(),r={...workRequirementScope(session,root),operation_id:'existing',root_sha256:f.initial.root_sha256,
 expected_revision:0,action:'ASSOCIATE',requirement_ref:ref,requirement_ids:['R2','R1'],acceptance_ids:['A1']};
 const v=clone(f.initial);v.links=[await receipt(r)];const c={...selection(),requirement_ids:['R1','R2']};
 assert(workRequirementUnchanged(v,workRequirementDraft(v,c)));
});
test('switching versions retains the old draft and explicitly restores it',async()=>{
 const f=await fixture(),v=clone(f.initial);v.selected.ref={...ref,revision:2,sha256:'6'.repeat(64)};v.selected.current_revision=2;
 f.j.edit(workRequirementDraft(v,{...selection(),requirement_ref:v.selected.ref}));await f.j.flush();
 const row=f.j.value().recovery[0];assert.deepEqual(row.draft.choice.requirement_ref,ref);
 await f.j.restore(row.id);assert.deepEqual(f.j.value().draft.choice.requirement_ref,ref);
 assert.equal(f.j.value().recovery[0].draft.choice.requirement_ref.revision,2);
 await f.j.deleteCopy(row.id);assert.equal(f.j.value().recovery.length,0);
});
test('quota failure keeps hidden draft dirty and blocks the shared navigation/normal close barrier',async()=>{
 const f=await fixture(),c=selection();c.requirement_ids=['R2'];c.acceptance_ids=[];f.j.edit(workRequirementDraft(f.initial,c));f.m.fail(true);
 await assert.rejects(()=>f.j.flush());await assert.rejects(()=>flushWorkRequirementDrafts(),/WORK_REQUIREMENT_DRAFT_UNSAVED/);
 assert.deepEqual(f.j.value().draft.choice,c);assert(f.j.unsaved());f.m.fail(false);await flushWorkRequirementDrafts();assert(!f.j.unsaved());
});
test('two windows retain both drafts and the losing writer does not overwrite stored choices',async()=>{
 const f=await fixture(),other=f.restart(),c=selection();c.requirement_ids=['R2'];c.acceptance_ids=[];
 other.edit(workRequirementDraft(f.initial,c));f.j.edit(workRequirementDraft(f.initial,{...selection(),acceptance_ids:[]}));await f.j.flush();
 await assert.rejects(()=>other.flush(),/WORK_REQUIREMENT_DRAFT_CONFLICT/);assert.equal(f.restart().value().draft.choice.requirement_ids[0],'R1');
 await other.resolve('LOCAL');assert.equal(other.value().draft.choice.requirement_ids[0],'R2');
 assert(other.value().recovery.some(r=>r.draft.choice.requirement_ids[0]==='R1'));assert(!other.unsaved());
});
test('one durable exact intent precedes the first POST and receipt updates no task acceptance',async()=>{
 const f=await fixture(),a=await f.actions.submit(session,f.initial,'ASSOCIATE');assert.equal(a.phase,'RECORDED');
 assert.equal(f.posts().length,1);const p=f.posts()[0];assert.equal(p.retry,false);assert.equal(p.method,'POST');
 assert.match(p.path,/work-requirements\/links$/);assert.equal(Object.keys(p.body).length,11);
 assert.equal(a.receipt.whole_task_acceptance,'NOT_ASSESSED');assert.equal(a.receipt.permission_granted,false);
 assert.equal(f.j.value().draft.revision,1);assert.equal(f.restart().value().attempt.phase,'RECORDED');
});
test('intent persistence failure prevents every positive POST and preserves the frozen request',async()=>{
 const f=await fixture();f.m.fail(true);await assert.rejects(()=>f.actions.submit(session,f.initial,'ASSOCIATE'));
 assert.equal(f.posts().length,0);assert.equal(f.j.value().attempt.phase,'PENDING');assert(f.j.unsaved());
 f.m.fail(false);await f.j.flush();assert.equal(f.restart().value().attempt.phase,'PENDING');
});
test('fresh preflight revision or selected source changes block submission without clearing draft',async()=>{
 for(const change of [v=>v.revision++,v=>v.selected.sources[0].status='SUPERSEDED']){
  const f=await fixture(),next=clone(f.initial);change(next);f.fresh(next);
  await assert.rejects(()=>f.actions.submit(session,f.initial,'ASSOCIATE'),/WORK_REQUIREMENT_REVIEW_REQUIRED/);
  assert.equal(f.posts().length,0);assert.deepEqual(f.j.value().draft.choice,selection());
 }
});
test('lost acknowledgement remains UNKNOWN across restart and original-key inspection performs no new POST',async()=>{
 const f=await fixture();let accepted;f.post(async body=>{accepted=await receipt(body);throw Error('UNIT connection lost after commit');});
 await f.actions.submit(session,f.initial,'ASSOCIATE');assert.equal(f.restart().value().attempt.phase,'UNKNOWN');
 f.lookup(async()=>accepted);const a=await f.actions.inspect({...session,generation:3});assert.equal(a.phase,'RECORDED');assert.equal(f.posts().length,1);
 const lookup=f.events.find(e=>e.kind==='lookup');assert.equal(lookup.session.generation,3);assert.equal(lookup.id,accepted.operation_id);
 assert.equal(lookup.sum,accepted.request_sha256);assert.equal(f.j.value().draft.scope.generation,2);
});
test('explicit retry inspects first and transmits the same frozen packet only after not-found',async()=>{
 const f=await fixture();f.post(async()=>{throw Error('UNIT lost transport');});await f.actions.submit(session,f.initial,'ASSOCIATE');
 const packet=clone(f.posts()[0].body);f.post(body=>receipt(body));const a=await f.actions.retry(session);
 assert.equal(a.phase,'RECORDED');assert.equal(f.posts().length,2);assert.deepEqual(f.posts()[1].body,packet);
 const kinds=f.events.map(e=>e.kind);assert.deepEqual(kinds,['read','post','lookup','read','post']);
});
test('retry finding a recorded original request returns its receipt without another POST',async()=>{
 const f=await fixture();let accepted;f.post(async body=>{accepted=await receipt(body);throw Error('UNIT lost ack');});
 await f.actions.submit(session,f.initial,'ASSOCIATE');f.lookup(async()=>accepted);assert.equal((await f.actions.retry(session)).phase,'RECORDED');
 assert.equal(f.posts().length,1);
});
test('not-found never authorizes retry in a new generation or changed head',async()=>{
 for(const change of [v=>v.scope.generation++,v=>v.revision++]){
  const f=await fixture();f.post(async()=>{throw Error('UNIT lost ack');});await f.actions.submit(session,f.initial,'ASSOCIATE');
  const next=clone(f.initial);change(next);f.fresh(next);await assert.rejects(()=>f.actions.retry(session),/WORK_REQUIREMENT_REVIEW_REQUIRED/);
  assert.equal(f.posts().length,1);assert.equal(f.j.value().attempt.phase,'UNKNOWN');assert.equal(f.j.value().attempt.last_observation,'NOT_FOUND');
 }
});
test('408 and 429 are unknown outcomes and later 409 cannot downgrade prior uncertainty',async()=>{
 for(const status of [408,429]){const f=await fixture();f.post(async()=>{throw http(status);});
  assert.equal((await f.actions.submit(session,f.initial,'ASSOCIATE')).phase,'UNKNOWN');f.post(async()=>{throw http(409);});
  assert.equal((await f.actions.retry(session)).phase,'UNKNOWN');assert(f.j.value().attempt.initial_transport_unknown);
 }
});
test('an initial explicit 4xx remains rejected and can be retained without pretending it succeeded',async()=>{
 const f=await fixture();f.post(async()=>{throw http(422);});assert.equal((await f.actions.submit(session,f.initial,'ASSOCIATE')).phase,'REJECTED');
 await f.j.retain(f.initial);assert.equal(f.j.value().attempt,null);assert.equal(f.j.value().retained[0].attempt.phase,'REJECTED');
 assert.equal(f.j.value().retained[0].disposition,null);assert(!workRequirementPendingLocal(f.j.value()));
});
test('advanced-basis disposition keeps unknown request, checksum and outcome intact',async()=>{
 const f=await fixture();f.post(async()=>{throw Error('UNIT lost ack');});await f.actions.submit(session,f.initial,'ASSOCIATE');
 await f.actions.inspect(session);const before=f.j.value().attempt;
 await assert.rejects(()=>f.j.retain(f.initial),/WORK_REQUIREMENT_PENDING/);const next=clone(f.initial);next.revision=1;
 await f.j.retain(next);const row=f.j.value().retained[0];assert.deepEqual(row.attempt,before);
 assert.equal(row.disposition.revision,1);assert.equal(row.attempt.phase,'UNKNOWN');assert.equal(f.posts().length,1);
});
test('malformed or mismatched receipt preserves UNKNOWN instead of manufacturing acceptance',async()=>{
 const f=await fixture();f.post(async body=>({...await receipt(body),permission_granted:true}));
 await assert.rejects(()=>f.actions.submit(session,f.initial,'ASSOCIATE'),/WORK_REQUIREMENT_RECEIPT_CHANGED/);
 assert.equal(f.j.value().attempt.phase,'UNKNOWN');assert.equal(f.restart().value().attempt.phase,'UNKNOWN');assert.equal(f.posts().length,1);
});
test('verified receipt survives local persistence failure as RECORDED dirty state',async()=>{
 const f=await fixture();f.post(async body=>{const ack=await receipt(body);f.m.fail(true);return ack;});
 await assert.rejects(()=>f.actions.submit(session,f.initial,'ASSOCIATE'));assert.equal(f.j.value().attempt.phase,'RECORDED');assert(f.j.unsaved());
 assert.equal(f.restart().value().attempt.phase,'PENDING');f.m.fail(false);await f.j.flush();assert.equal(f.restart().value().attempt.phase,'RECORDED');
 assert.equal(f.posts().length,1);
});
test('late acknowledgement preserves newer edits instead of clearing or rebasing their draft',async()=>{
 const f=await fixture();let release,started;const entered=new Promise(resolve=>{started=resolve;});
 f.post(body=>{started();return new Promise(resolve=>{release=async()=>resolve(await receipt(body));});});
 const active=f.actions.submit(session,f.initial,'ASSOCIATE');await entered;const c={...selection(),requirement_ids:['R2'],acceptance_ids:[]};
 f.j.edit(workRequirementDraft(f.initial,c));await release();await active;
 assert.deepEqual(f.j.value().draft.choice,c);assert.equal(f.j.value().draft.revision,0);assert.equal(f.j.value().attempt.phase,'RECORDED');
});
test('remove works without a draft and only submits an explicit empty metadata selection',async()=>{
 const f=await fixture({draft:false}),r={...workRequirementScope(session,root),operation_id:'old',root_sha256:f.initial.root_sha256,
 expected_revision:0,action:'ASSOCIATE',...selection()},old=await receipt(r),v=clone(f.initial);v.revision=1;v.links=[old];f.fresh(v);
 const a=await f.actions.submit(session,v,'REMOVE',old);assert.equal(a.phase,'RECORDED');assert.equal(f.j.value().draft,null);
 assert.deepEqual(f.posts()[0].body.requirement_ids,[]);assert.deepEqual(f.posts()[0].body.acceptance_ids,[]);assert.equal(a.receipt.selection_sha256,null);
});
test('conflict resolution preserves a locally verified receipt even when neither side has a draft',async()=>{
 const f=await fixture({draft:false}),r={...workRequirementScope(session,root),operation_id:'remove_original',root_sha256:f.initial.root_sha256,
 expected_revision:0,action:'REMOVE',requirement_ref:ref,requirement_ids:[],acceptance_ids:[]},ack=await receipt(r),sum=await hash(r);
 const a={request:r,checksum:sum,phase:'PENDING',initial_transport_unknown:false,last_observation:'NOT_CHECKED',error_code:null,receipt:null};
 f.j.saveAttempt(a);const other=f.restart();other.saveAttempt({...a,request:{...r,operation_id:'other'},checksum:await hash({...r,operation_id:'other'})});
 assert.throws(()=>f.j.accept(a,ack),/WORK_REQUIREMENT_DRAFT_CONFLICT/);assert.equal(f.j.value().attempt.phase,'RECORDED');
 await f.j.resolve('SAVED');const kept=f.j.value().retained.find(row=>row.attempt.checksum===sum);
 assert.equal(kept.attempt.phase,'RECORDED');assert.deepEqual(kept.attempt.receipt,ack);assert.equal(f.j.value().draft,null);assert(!f.j.unsaved());
});
test('conflict merge keeps unknown initial transport even if another window saved an older pending status',async()=>{
 const f=await fixture();f.post(async()=>{throw Error('UNIT lost ack');});await f.actions.submit(session,f.initial,'ASSOCIATE');
 const old=f.j.value();old.revision++;old.attempt.phase='PENDING';old.attempt.initial_transport_unknown=false;f.m.values.set(f.j.key,JSON.stringify(old));
 await f.j.resolve('LOCAL');assert.equal(f.j.value().attempt.phase,'UNKNOWN');assert(f.j.value().attempt.initial_transport_unknown);
});
test('retained unknown receipt can be inspected without altering the newer draft or POST count',async()=>{
 const f=await fixture();let ack;f.post(async body=>{ack=await receipt(body);throw Error('UNIT lost ack');});
 await f.actions.submit(session,f.initial,'ASSOCIATE');await f.actions.inspect(session);const advanced=clone(f.initial);advanced.revision=1;
 await f.j.retain(advanced);const c={...selection(),requirement_ids:['R2'],acceptance_ids:[]};f.j.edit(workRequirementDraft(advanced,c));await f.j.flush();
 const before=clone(f.j.value().draft),sum=f.j.value().retained[0].attempt.checksum;f.lookup(async()=>ack);
 assert.equal((await f.actions.inspectRetained({...session,generation:3},sum)).phase,'RECORDED');
 assert.deepEqual(f.j.value().draft,before);assert.equal(f.j.value().attempt,null);assert.equal(f.posts().length,1);
 assert.equal(f.j.value().retained[0].disposition.revision,1);
});
test('conflicting unresolved attempt blocks new keys and can explicitly restore its original recovery',async()=>{
 const f=await fixture();f.post(async()=>{throw Error('UNIT lost ack');});await f.actions.submit(session,f.initial,'ASSOCIATE');
 const original=f.j.value().attempt,saved=f.j.value();saved.attempt=null;saved.revision++;f.m.values.set(f.j.key,JSON.stringify(saved));
 await f.j.resolve('SAVED');assert.equal(f.j.value().attempt,null);assert(workRequirementPendingLocal(f.j.value()));
 await assert.rejects(()=>f.actions.submit(session,f.initial,'ASSOCIATE'),/WORK_REQUIREMENT_PENDING/);assert.equal(f.posts().length,1);
 assert.equal((await f.actions.inspectRetained(session,original.checksum)).phase,'UNKNOWN');await f.j.restoreAttempt(original.checksum);
 assert.deepEqual(f.j.value().attempt.request,original.request);assert.equal(f.j.value().retained.length,0);
 f.post(body=>receipt(body));assert.equal((await f.actions.retry(session)).phase,'RECORDED');assert.equal(f.posts().length,2);
});
test('retained acknowledgement persistence failure remains verified in memory and can flush later',async()=>{
 const f=await fixture();let ack;f.post(async body=>{ack=await receipt(body);throw Error('UNIT lost ack');});
 await f.actions.submit(session,f.initial,'ASSOCIATE');await f.actions.inspect(session);const next=clone(f.initial);next.revision++;
 await f.j.retain(next);const sum=f.j.value().retained[0].attempt.checksum;f.lookup(async()=>{f.m.fail(true);return ack;});
 await assert.rejects(()=>f.actions.inspectRetained(session,sum));assert.equal(f.j.value().retained[0].attempt.phase,'RECORDED');assert(f.j.unsaved());
 f.m.fail(false);await f.j.flush();assert.equal(f.restart().value().retained[0].attempt.phase,'RECORDED');
});
test('tampered frozen request is rejected locally before original lookup or positive POST',async()=>{
 const f=await fixture();f.post(async()=>{throw Error('UNIT lost ack');});await f.actions.submit(session,f.initial,'ASSOCIATE');
 const saved=f.j.value();saved.attempt.request.requirement_ids=['R2'];f.m.values.set(f.j.key,JSON.stringify(saved));
 const j=f.restart(),a=new WorkRequirementActions(j,f.reader,f.transport);await assert.rejects(()=>a.inspect(session),/WORK_REQUIREMENT_LOCAL_INVALID/);
 assert.equal(f.events.filter(e=>e.kind==='lookup').length,0);assert.equal(f.posts().length,1);
});
test('unavailable lock prevents submission without invoking any transport',async()=>{
 const f=await fixture({draft:false}),j=new WorkRequirementJournal(session,root,f.m.storage,null);
 const a=new WorkRequirementActions(j,f.reader,f.transport);await assert.rejects(()=>a.submit(session,f.initial,'ASSOCIATE'),/WORK_REQUIREMENT_LOCK_UNAVAILABLE/);
 assert.equal(f.events.length,0);
});
test('dirty hidden journal is reused across remount and included in the shared flush barrier',async()=>{
 const m=memory(),before=Object.getOwnPropertyDescriptor(globalThis,'navigator');
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{locks:m.locks}});
 try{const j=workRequirementJournal(m.storage,session,root);j.edit(workRequirementDraft(view(),selection()));
  assert.equal(workRequirementJournal(m.storage,{...session,generation:3},root),j);await flushWorkRequirementDrafts();
  assert(!j.unsaved());assert.deepEqual(workRequirementJournal(m.storage,session,root).value().draft.choice,selection());
 }finally{if(before)Object.defineProperty(globalThis,'navigator',before);else delete globalThis.navigator;}
});

test('bounded HTTP timeout aborts the original signal and leaves a durable unknown request',async t=>{
 const f=await fixture();t.mock.timers.enable({apis:['setTimeout']});t.after(()=>t.mock.timers.reset());
 let started;const entered=new Promise(resolve=>{started=resolve;});f.post(()=>{started();return new Promise(()=>{});});
 const active=f.actions.submit(session,f.initial,'ASSOCIATE');await entered;t.mock.timers.tick(25000);
 assert.equal((await active).phase,'UNKNOWN');assert(f.posts()[0].signal.aborted);assert.equal(f.posts().length,1);
 assert.equal(f.restart().value().attempt.phase,'UNKNOWN');
});
test('oversized draft blocks persistence and retains input until explicitly reduced',async()=>{
 const f=await fixture(),original=f.j.value().draft,large=clone(original);large.basis.extra='x'.repeat(524289);f.j.edit(large);
 await assert.rejects(()=>f.j.flush(),/WORK_REQUIREMENT_LOCAL_CAPACITY/);assert(f.j.unsaved());assert.equal(f.j.value().draft.basis.extra.length,524289);
 assert.deepEqual(f.restart().value().draft,original);f.j.edit(original);await f.j.flush();assert(!f.j.unsaved());
});
test('four draft copies are retained and a fifth replacement never silently prunes one',async()=>{
 const f=await fixture();for(let revision=2;revision<=5;revision++){
  const v=clone(f.initial);v.selected.ref={...ref,revision,sha256:String(revision).repeat(64)};v.selected.current_revision=revision;
  f.j.edit(workRequirementDraft(v,{...selection(),requirement_ref:v.selected.ref}));await f.j.flush();
 }
 const before=f.j.value(),v=clone(f.initial);v.selected.ref={...ref,revision:6,sha256:'6'.repeat(64)};v.selected.current_revision=6;
 assert.throws(()=>f.j.edit(workRequirementDraft(v,{...selection(),requirement_ref:v.selected.ref})),/WORK_REQUIREMENT_DRAFT_CAPACITY/);
 assert.deepEqual(f.j.value(),before);assert.equal(before.recovery.length,4);assert(!f.j.unsaved());
});
test('full attempt history refuses new disposition while preserving all original unknowns',async()=>{
 const f=await fixture();f.post(async()=>{throw Error('UNIT lost ack');});await f.actions.submit(session,f.initial,'ASSOCIATE');await f.actions.inspect(session);
 const saved=f.j.value();for(let i=0;i<8;i++){
  const request={...saved.attempt.request,operation_id:'retained_'+i};saved.retained.push({attempt:{...saved.attempt,request,checksum:await hash(request)},disposition:{root_sha256:f.initial.root_sha256,generation:2,revision:1}});
 }
 f.m.values.set(f.j.key,JSON.stringify(saved));const j=f.restart(),v=clone(f.initial);v.revision=1;
 await assert.rejects(()=>j.retain(v),/WORK_REQUIREMENT_RECOVERY_CAPACITY/);assert.deepEqual(j.value(),saved);assert.equal(f.posts().length,1);
});
test('normal navigation error presents a translated recovery message and keeps raw error intact',()=>{
 const error=Error('WORK_REQUIREMENT_DRAFT_UNSAVED'),copy=authoredErrorCopy(error);
 assert.equal(typeof copy,'object');assert.equal(authoredText(copy),'关联选择尚未保存，请在对应要求中核对；原输入保留。');
 assert.equal(error.message,'WORK_REQUIREMENT_DRAFT_UNSAVED');assert(!authoredText(copy).includes('WORK_REQUIREMENT_'));
});
