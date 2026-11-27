import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
const source=await readFile(new URL('../src/supervised-task-navigation.ts',import.meta.url),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
const {loadSupervisedTaskNavigation:load}=await import('data:text/javascript;base64,'+Buffer.from(compiled).toString('base64'));
const sha='a'.repeat(64),other='b'.repeat(64),clone=value=>structuredClone(value),reference=(id='session',revision=1)=>({id,revision,sha256:sha});
const profile=reference('provider'),sessionRef=reference('session');
function fixture(kind='native',project='project'){
 const taskKind={native:'native_message',original:'original_session',discovery:'discovery',planning:'planning'}[kind],objectId=kind==='native'?'operation':kind==='planning'?'proposal':'session';
 const key={kind:taskKind,project_id:project,object_id:objectId,operation_id:'operation',generation:kind==='original'?2:0},source_ref=kind==='original'?sessionRef:reference('operation');
 const base={version:1,kind,status:'AVAILABLE',reason_code:null,task_key:key,source_ref,project_id:project,operation_id:'operation',automatic_retry:false,execution_authorized:false};
 const branches={native:{request_project_id:project,conversation_project_id:project,conversation_id:'discussion',request_sha256:sha,provider_profile_ref:clone(profile),workspace_ref:null,file_source_ref:null,file_link_ref:null,record_origin:'CURRENT_HOST'},original:{session_ref:clone(sessionRef),generation:2,current_generation:2,request_sha256:sha,provider_profile_ref:clone(profile),workspace_ref:null},discovery:{object_ref:sessionRef,source_session_ref:clone(sessionRef),workspace_ref:null,proposal_ref:null},planning:{object_ref:reference('proposal',3),source_session_ref:null,workspace_ref:null,proposal_ref:null}};
 const detail={key:clone(key),task:{key:clone(key),source_ref:clone(source_ref)},navigation:{...clone(base),...clone(branches[kind])},input_text:'Original exact question',answer_text:'Original exact answer',automatic_retry:false,execution_authorized:false},calls=[];
 const message={operation_id:'operation',project_id:project,conversation_id:'discussion',status:'RETURNED',request_sha256:sha,record_origin:'CURRENT_HOST',provider_profile_ref:clone(profile),file_source_ref:null,file_link_ref:null,automatic_retry:false,text:'Original exact answer'};
 const conversation={kind:'NATIVE_CONVERSATION',project_id:project,conversation_id:'discussion',messages:[]};
 const session={project_id:project,session_ref:clone(sessionRef),generation:2,owner_status:'READY',native_status:'idle',connected_owner:true,configuration_current:true,workspace_kind:'APP_INTERNAL_READONLY',last_event_seq:0,automatic_post_replay:false,provider_profile_ref:clone(profile),workspace_ref:null,messages:[],todo:[],children:[],pending_questions:[],pending_permissions:[]};
 const operation={id:'operation',kind:'MESSAGE',status:'RETURNED',session_ref:clone(sessionRef),request_sha256:sha,operation_ref:{id:'operation',request_sha256:sha},request:{operation_id:'operation',session_ref:clone(sessionRef),generation:2,raw_text:detail.input_text},automatic_replay:false};
 const object={project_id:project,...clone(branches[kind].object_ref??sessionRef),current_revision:branches[kind].object_ref?.revision??1,payload:{workspace_ref:null}};
 const transport={get:async(path,signal)=>{calls.push({kind:'get',path,signal});if(path.includes('/operations/'))return clone(operation);return path.includes('/agent-sessions/')?clone(session):clone(object);},inspectNativeMessage:async id=>{calls.push({kind:'inspect',id});return clone(message);},getNativeConversation:async target=>{calls.push({kind:'conversation',target});return clone(conversation);}};
 return{detail,calls,message,conversation,session,operation,object,transport,expected:{key:clone(key),source_ref:clone(source_ref)}};
}
const bad=/SUPERVISED_TASK_REFERENCE_CHANGED/;
test('native navigation uses only typed host reads and returns the exact operation outside the recent conversation window',async()=>{
 for(const project of ['project',null]){const f=fixture('native',project),before=JSON.stringify(f.detail);const target=await load(f.detail,f.transport,{expectedTask:f.expected});assert.equal(target.kind,'native');assert.equal(target.navigation.project_id,project);assert.equal(target.message.operation_id,'operation');assert.deepEqual(target.conversation.messages,[]);assert.equal(target.input,f.detail.input_text);assert.equal(JSON.stringify(f.detail),before);assert.deepEqual(f.calls.map(row=>row.kind),['inspect','conversation']);assert.deepEqual(f.calls[1].target,{projectId:project,conversationId:'discussion',operationId:'operation'});}
});
test('exact task key and source basis are checked before any read, independent of JSON field order',async()=>{
 for(const mutate of [f=>f.detail.key.generation++,f=>f.detail.task.key.project_id='foreign',f=>f.detail.navigation.source_ref.sha256=other,f=>f.detail.navigation.task_key.operation_id='foreign',f=>f.expected.source_ref.revision++,f=>f.detail.navigation.project_id='foreign',f=>f.detail.navigation.operation_id='foreign',f=>f.detail.navigation.execution_authorized=true,f=>f.detail.automatic_retry=true,f=>f.detail.navigation.extra='not allowed']){const f=fixture();mutate(f);await assert.rejects(load(f.detail,f.transport,{expectedTask:f.expected}),bad);assert.equal(f.calls.length,0);}
 const f=fixture();f.expected.key={generation:0,operation_id:'operation',object_id:'operation',project_id:'project',kind:'native_message'};assert.equal((await load(f.detail,f.transport,{expectedTask:f.expected})).kind,'native');
});
test('unavailable navigation, missing native bridge, or absent conversation cannot create a fresh discussion',async()=>{
 for(const status of ['UNAVAILABLE','unknown']){const f=fixture();f.detail.navigation.status=status;await assert.rejects(load(f.detail,f.transport),/SUPERVISED_TASK_(NAVIGATION_UNAVAILABLE|REFERENCE_CHANGED)/);assert.equal(f.calls.length,0);}
 const f=fixture();delete f.transport.getNativeConversation;await assert.rejects(load(f.detail,f.transport),/TRANSPORT_UNAVAILABLE/);assert.equal(f.calls.length,0);
 const g=fixture();g.detail.navigation.conversation_id=null;await assert.rejects(load(g.detail,g.transport),bad);assert.equal(g.calls.length,0);
});
test('foreign native owner, request digest, provider or file association cannot replace the captured operation',async()=>{
 for(const mutate of [f=>f.message.project_id='other',f=>f.message.conversation_id='other',f=>f.message.operation_id='other',f=>f.message.request_sha256=other,f=>f.message.provider_profile_ref=reference('other'),f=>f.message.file_link_ref={...reference(sha),project_id:'project'},f=>f.message.record_origin='HISTORICAL_READONLY',f=>f.message.automatic_retry=true]){const f=fixture();mutate(f);await assert.rejects(load(f.detail,f.transport),bad);assert.equal(f.calls.length,1);}
});
test('conversation history may be bounded, but every returned row remains in the exact captured discussion',async()=>{
 for(const mutate of [f=>f.conversation.project_id='other',f=>f.conversation.conversation_id='other',f=>f.conversation.messages.push({...f.message,project_id:'other'}),f=>f.conversation.messages.push({...f.message,request_sha256:other})]){const f=fixture();mutate(f);await assert.rejects(load(f.detail,f.transport),bad);}
 const f=fixture();f.detail.navigation.status='READONLY';f.detail.navigation.record_origin='HISTORICAL_READONLY';f.message.record_origin='HISTORICAL_READONLY';assert.equal((await load(f.detail,f.transport)).readOnly,true);
});
test('original conversation reads the exact session and operation; old generation is displayed read-only',async()=>{
 const f=fixture('original');let target=await load(f.detail,f.transport);assert.equal(target.kind,'original');assert.equal(target.readOnly,false);assert.deepEqual(f.calls.map(row=>row.path),['/api/projects/project/agent-sessions/session','/api/projects/project/agent-sessions/session/operations/operation']);
 const g=fixture('original');g.detail.navigation.status='READONLY';g.detail.navigation.current_generation=3;g.session.generation=3;target=await load(g.detail,g.transport);assert.equal(target.readOnly,true);assert.equal(target.operation.request.generation,2);
});
test('original session/provider/operation/request generation changes reject without loading or posting anything new',async()=>{
 for(const mutate of [f=>f.session.project_id='foreign',f=>f.session.session_ref.sha256=other,f=>f.session.generation++,f=>f.session.provider_profile_ref=reference('other'),f=>f.operation.id='foreign',f=>f.operation.request_sha256=other,f=>f.operation.operation_ref.id='foreign',f=>f.operation.request.generation++,f=>f.operation.request.session_ref.sha256=other,f=>f.operation.request.raw_text='Substituted question']){const f=fixture('original');mutate(f);await assert.rejects(load(f.detail,f.transport),bad);assert.ok(f.calls.every(row=>row.kind==='get'));}
});
test('discovery and planning read exact immutable revisions, preserving historical payload instead of latest',async()=>{
 for(const kind of ['discovery','planning']){const f=fixture(kind);f.object.current_revision=f.object.revision+1;const target=await load(f.detail,f.transport);assert.equal(target.kind,kind);assert.equal(target.readOnly,true);const expected=kind==='discovery'?'/api/projects/project/discovery/sessions/session?revision=1':'/api/projects/project/planning/proposals/proposal?revision=3';assert.equal(f.calls[0].path,expected);assert.equal(target[kind==='discovery'?'session':'proposal'].revision,f.object.revision);}
});
test('object identity, project, revision, digest and workspace are independently bound',async()=>{
 for(const kind of ['discovery','planning'])for(const mutate of [f=>f.object.project_id='foreign',f=>f.object.id='foreign',f=>f.object.revision++,f=>f.object.sha256=other,f=>f.object.payload.workspace_ref={id:sha,revision:1,binding_sha256:sha}]){const f=fixture(kind);mutate(f);await assert.rejects(load(f.detail,f.transport),bad);assert.equal(f.calls.length,1);}
});
test('discovery PLAN wrapper loads both exact referenced revisions and refuses a substituted linked proposal',async()=>{
 const f=fixture('discovery'),proposalRef=reference('proposal',7),proposal={project_id:'project',...proposalRef,current_revision:8,payload:{}};f.detail.navigation.proposal_ref=proposalRef;f.object.payload.proposal_ref=proposalRef;f.transport.get=async path=>{f.calls.push({kind:'get',path});return clone(path.includes('/planning/')?proposal:f.object);};
 let target=await load(f.detail,f.transport);assert.equal(target.proposal.revision,7);assert.equal(f.calls[1].path,'/api/projects/project/planning/proposals/proposal?revision=7');
 f.object.payload.proposal_ref={...proposalRef,revision:8};f.calls.length=0;await assert.rejects(load(f.detail,f.transport),bad);assert.equal(f.calls.length,1);
 f.object.payload.proposal_ref=proposalRef;proposal.sha256=other;await assert.rejects(load(f.detail,f.transport),bad);
});
test('planning source discovery reference is read from the captured payload, without searching for a similar project',async()=>{
 const f=fixture('planning');f.detail.navigation.source_session_ref=sessionRef;f.object.payload.discovery_ref=sessionRef;assert.equal((await load(f.detail,f.transport)).kind,'planning');
 f.object.payload.discovery_ref={...sessionRef,revision:2};await assert.rejects(load(f.detail,f.transport),bad);assert.ok(f.calls.every(row=>row.path==='/api/projects/project/planning/proposals/proposal?revision=3'));
});
test('new user intent and AbortSignal stop delayed navigation without publishing a late target or issuing the next read',async()=>{
 for(const mode of ['intent','signal','rejection']){const f=fixture('native'),controller=new AbortController();let current=true,release,reject;const waiting=new Promise((yes,no)=>{release=yes;reject=no;});f.transport.inspectNativeMessage=async()=>{f.calls.push({kind:'inspect'});return waiting;};const pending=load(f.detail,f.transport,{current:()=>current,signal:controller.signal});await Promise.resolve();if(mode==='signal')controller.abort();else current=false;if(mode==='rejection')reject(Error('Late external read failure'));else release(f.message);assert.equal(await pending,undefined);assert.equal(f.calls.length,1);}
});
test('two explicit targets can resolve out of order; the replaced owner receives no target',async()=>{
 const first=fixture('original'),second=fixture('planning');let generation=1,release;first.transport.get=async()=>new Promise(resolve=>release=resolve);const pending=load(first.detail,first.transport,{current:()=>generation===1});await Promise.resolve();generation=2;const target=await load(second.detail,second.transport,{current:()=>generation===2});assert.equal(target.kind,'planning');release(first.session);assert.equal(await pending,undefined);
});
test('captured scalar references cannot be retargeted by mutating the caller detail during a delayed read',async()=>{
 const f=fixture('original');let release;const initial=f.transport.get;f.transport.get=async(path,signal)=>{if(path.endsWith('/session')){f.calls.push({kind:'get',path});await new Promise(resolve=>release=resolve);return clone(f.session);}return initial(path,signal);};const pending=load(f.detail,f.transport);await Promise.resolve();f.detail.navigation.operation_id='foreign';f.detail.navigation.session_ref.id='foreign';release();const target=await pending;assert.equal(target.navigation.operation_id,'operation');assert.equal(target.navigation.session_ref.id,'session');assert.equal(f.calls.at(-1).path,'/api/projects/project/agent-sessions/session/operations/operation');
});
test('loader forwards only the supplied abort signal to fixed non-host GET routes and never invokes a model or write transport',async()=>{
 const f=fixture('planning'),controller=new AbortController();await load(f.detail,f.transport,{signal:controller.signal});assert.equal(f.calls[0].signal,controller.signal);assert.equal(f.calls[0].kind,'get');assert.ok(!source.includes("'POST'"));
 const g=fixture('planning');await assert.rejects(load(g.detail,{get:async()=>{throw Error('Real read failure');}}),/Real read failure/);
});

test('legacy raw-null/file-project mismatch keeps both original scopes and stays read-only',async()=>{
 const f=fixture('native',null);f.detail.navigation.status='READONLY';f.detail.navigation.reason_code='LEGACY_PROJECT_SCOPE_MISMATCH';f.detail.navigation.conversation_project_id='file_project';f.message.project_id='file_project';f.message.request_project_id=null;f.conversation.project_id='file_project';
 const target=await load(f.detail,f.transport);assert.equal(target.readOnly,true);assert.equal(target.navigation.project_id,null);assert.equal(target.navigation.request_project_id,null);assert.equal(target.message.project_id,'file_project');assert.equal(f.calls[1].target.projectId,'file_project');
 f.detail.navigation.status='AVAILABLE';f.calls.length=0;await assert.rejects(load(f.detail,f.transport),bad);assert.equal(f.calls.length,0);
 f.detail.navigation.status='READONLY';f.message.request_project_id='file_project';await assert.rejects(load(f.detail,f.transport),bad);
});
test('an available task cannot borrow another request scope, and an unavailable review is never adapted as ordinary chat',async()=>{
 const f=fixture();f.detail.navigation.request_project_id='foreign';await assert.rejects(load(f.detail,f.transport),bad);assert.equal(f.calls.length,0);
 const g=fixture();g.detail.navigation.status='UNAVAILABLE';g.detail.navigation.reason_code='NATIVE_REVIEW_NAVIGATION_NOT_ADAPTED';await assert.rejects(load(g.detail,g.transport),/NAVIGATION_UNAVAILABLE/);assert.equal(g.calls.length,0);
});
test('a missing historical source may remain read-only without inventing a new source session',async()=>{
 const f=fixture('planning');f.detail.navigation.status='READONLY';f.detail.navigation.reason_code='SOURCE_SESSION_UNAVAILABLE';f.object.payload.discovery_ref=sessionRef;
 const target=await load(f.detail,f.transport);assert.equal(target.readOnly,true);assert.equal(target.navigation.source_session_ref,null);assert.equal(f.calls.length,1);
});

test('task-detail text uses the producer code-point limit without rejecting valid non-BMP user content',async()=>{
 const f=fixture();f.detail.input_text='🙂'.repeat(64000);assert.equal((await load(f.detail,f.transport)).input,f.detail.input_text);
 const g=fixture();g.detail.input_text='🙂'.repeat(64001);await assert.rejects(load(g.detail,g.transport),bad);assert.equal(g.calls.length,0);
});
