import test from 'node:test';
import assert from 'node:assert/strict';
import {ApiClient,ApiError} from '../src/api.ts';
import {PlanningDeliveryApi,saveDeliveryHistory} from '../src/planning-delivery.ts';
import {DeliveryHistoryRecovery,upsertDeliveryFeed,verifyDeliveryTarget} from '../src/delivery-recovery.ts';
import {chatDeliveryFiles,openChatDeliveryFile} from '../src/chat-artifacts.ts';
import {authoredText} from '../src/authored-ui-copy.ts';

const basis={id:'guide-session',revision:2,sha256:'a'.repeat(64)},target={projectId:'guide-project',kind:'STAGE',basis};
const source={kind:'local_file',project_id:target.projectId,workspace_id:'workspace',binding_revision:1,binding_sha256:'b'.repeat(64),path:'outputs/guide.docx',revision:1,sha256:'c'.repeat(64)};
const receipt=(key='api-stage',ref=basis)=>({operation_id:key,status:'STAGE_REPORT',session_ref:ref,pending_count:3,blocking_count:0,report:{artifact:{id:'word',sha256:source.sha256,filename:'guide.docx'}},diagrams:[],artifacts:[],project_files:[{path:source.path,sha256:source.sha256,file_source_ref:source}],file_checks:[{path:source.path,status:'CURRENT'}],basis_current:ref.revision===basis.revision,current_review_status:ref.revision===basis.revision?'CURRENT':'SOURCE_OR_VERSION_CHANGED',execution_authorized:false,historical_receipt_preserved:true});
const entry=(key='api-stage',ref=basis)=>({operation_id:key,session_ref:ref,created_at:'2026-10-28T00:00:00Z',status:'STAGE_REPORT',basis_current:true,current_review_status:'CURRENT'});
const directory=(entries=[entry()],ref=basis,pid=target.projectId)=>({project_id:pid,session_ref:ref,entries,complete:true});
function storage(){const values=new Map();return{getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};}
function deferred(){let resolve;const promise=new Promise(done=>resolve=done);return{promise,resolve};}
function transport(handler){const calls=[];const api=new PlanningDeliveryApi(new ApiClient(async(url,options)=>{calls.push({url,method:options?.method??'GET',signal:options?.signal});if(url==='/api/bootstrap')return new Response(JSON.stringify({csrf_token:'fixture'}));const value=await handler(url,options);return value instanceof Response?value:new Response(JSON.stringify(value));}));return{api,calls};}
const listURL=`/api/projects/${target.projectId}/planning-delivery/sessions/${basis.id}/operations?revision=2&sha256=${basis.sha256}`;
const opURL=key=>`/api/projects/${target.projectId}/planning-delivery/operations/${key}`;

test('API/another renderer stages restore from empty local indexes after a fresh recovery instance, including original historical versions',async()=>{
 const old={...basis,revision:1,sha256:'d'.repeat(64)},saved=new Map([['api-stage',receipt()],['old-stage',receipt('old-stage',old)]]),f=transport(url=>url.includes('/sessions/')?directory([entry(),entry('old-stage',old)]):saved.get(url.split('/').at(-1))),local=storage();let cards=[];
 const restore=async()=>{const r=new DeliveryHistoryRecovery(f.api,local,item=>cards=upsertDeliveryFeed(cards,item));await r.restore([target]);r.stop();};
 await restore();assert.equal(cards.length,2);assert.equal(chatDeliveryFiles(cards[0].artifact.data).all.length,1);const prior=cards.find(v=>v.artifact.artifactId==='old-stage');assert.deepEqual(prior.artifact.data.session_ref,old);assert.match(prior.body,/历史来源保留/);const ids=cards.map(v=>v.id);cards=[];await restore();assert.deepEqual(cards.map(v=>v.id),ids);assert.equal(local.getItem('eeas-delivery-history:guide-project:STAGE:guide-session'),null);assert.ok(f.calls.some(v=>v.url===listURL));assert.ok(f.calls.every(v=>v.method==='GET'));
});

test('server and local keys merge once; UNKNOWN/missing and partial local records remain queryable without automatic POST',async()=>{
 const local=storage();for(const key of ['api-stage','unknown-local','failed-local'])saveDeliveryHistory(local,target.projectId,'STAGE',basis.id,key);saveDeliveryHistory(local,'foreign','STAGE',basis.id,'foreign-key');
 const f=transport(url=>{if(url.includes('/sessions/'))return directory();if(url===opURL('unknown-local'))return new Response(JSON.stringify({error:{code:'NOT_FOUND',message:'No committed result'}}),{status:404});if(url===opURL('failed-local'))return{project_id:target.projectId,session_ref:basis,operation_id:'failed-local',kind:'STAGE',status:'FAILED_OR_PARTIAL',automatic_model_replay:false};return receipt();});let cards=[];const r=new DeliveryHistoryRecovery(f.api,local,item=>cards=upsertDeliveryFeed(cards,item));await r.restore([target]);
 assert.equal(cards.length,3);assert.equal(cards.find(v=>v.id.endsWith('unknown-local')).status,'NO_RECORD');assert.equal(cards.find(v=>v.id.endsWith('failed-local')).artifact.data.status,'FAILED_OR_PARTIAL');assert.equal(f.calls.filter(v=>v.url===opURL('api-stage')).length,1);assert.ok(f.calls.every(v=>v.method==='GET'));assert.ok(!f.calls.some(v=>v.url.includes('foreign')));r.stop();
});

test('explicit history refresh discovers a new stage without changing session; unforced effects do not poll',async()=>{
 let entries=[],cards=[];const f=transport(url=>url.includes('/sessions/')?directory(entries):receipt()),r=new DeliveryHistoryRecovery(f.api,storage(),item=>cards=upsertDeliveryFeed(cards,item));await r.restore([target]);entries=[entry()];await r.restore([target]);assert.equal(cards.length,0);await r.restore([target],{refresh:true});assert.equal(cards.length,1);assert.equal(f.calls.filter(v=>v.url===listURL).length,2);r.stop();
});

test('a slow directory and slow operation cannot insert cards after project/session navigation or stop',async()=>{
 for(const phase of ['directory','operation']){
  const gate=deferred(),started=deferred(),received=[],f=transport(async url=>{if(url===listURL){if(phase==='directory'){started.resolve();return gate.promise;}return directory();}if(url===opURL('api-stage')){started.resolve();return gate.promise;}return directory([],{...basis,id:'other-session'});});const r=new DeliveryHistoryRecovery(f.api,storage(),item=>received.push(item));const pending=r.restore([target]);await started.promise;await r.restore([{...target,basis:{...basis,id:'other-session'}}]);gate.resolve(phase==='directory'?directory():receipt());await pending;assert.equal(received.length,0);r.stop();
 }
 const gate=deferred(),started=deferred(),received=[],f=transport(async()=>{started.resolve();return gate.promise;}),r=new DeliveryHistoryRecovery(f.api,storage(),item=>received.push(item));const pending=r.restore([target]);await started.promise;r.stop();gate.resolve(directory());await pending;assert.equal(received.length,0);
});

test('malformed, incomplete, stale-scope or ambiguous server catalogs never authorize another operation GET',async()=>{
 const bad=[{...directory(),project_id:'foreign'},{...directory(),session_ref:{...basis,sha256:'f'.repeat(64)}},{...directory(),complete:false},directory([entry('foreign',{...basis,id:'other'})]),directory([entry(),entry()]),directory([entry('../escape')]),directory([entry('future',{...basis,revision:3})]),directory([{...entry(),basis_current:'true'}]),directory([{...entry(),current_review_status:{raw:true}}]),directory([{...entry(),created_at:undefined}]),directory(Array.from({length:65},(_,i)=>entry('op'+i)))];
 for(const value of bad){const f=transport(()=>value);await assert.rejects(()=>f.api.sessionOperations(target.projectId,basis));assert.equal(f.calls.filter(v=>v.url.includes('/operations/')&&!v.url.includes('/sessions/')).length,0);}
});

test('catalog 409 and invalid responses preserve prior qualified cards and local unknown keys; no stale basis replacement or generation',async()=>{
 const local=storage();saveDeliveryHistory(local,target.projectId,'STAGE',basis.id,'local-unknown');let broken=false,cards=[];const f=transport(url=>{if(url.includes('/sessions/'))return broken?new Response(JSON.stringify({error:{code:'STALE_REFERENCE',message:'Refresh session'}}),{status:409}):directory();if(url===opURL('local-unknown'))return new Response('{}',{status:404});return receipt();});const r=new DeliveryHistoryRecovery(f.api,local,item=>cards=upsertDeliveryFeed(cards,item));await r.restore([target]);broken=true;await assert.rejects(()=>r.restore([target],{refresh:true}),e=>e instanceof ApiError&&e.status===409);assert.equal(cards.length,2);assert.equal(cards.find(v=>v.artifact)?.artifact.data.operation_id,'api-stage');assert.match(local.getItem('eeas-delivery-history:guide-project:STAGE:guide-session'),/local-unknown/);assert.ok(f.calls.every(v=>v.method==='GET'));r.stop();
});

test('listed claim exact revision/hash is checked against operation result, not replaced by a newer head',async()=>{
 const f=transport(url=>url.includes('/sessions/')?directory():receipt('api-stage',{...basis,sha256:'d'.repeat(64)})),cards=[],r=new DeliveryHistoryRecovery(f.api,storage(),item=>cards.push(item));await r.restore([target]);assert.equal(cards.length,1);assert.equal(cards[0].status,'INSPECTION_UNAVAILABLE');assert.equal(cards[0].artifact,undefined);r.stop();
});

test('unknown directory status remains an original-key inspection, while a failed operation refresh retains an existing real file card',async()=>{
 let mode='success',cards=[];const f=transport(url=>url.includes('/sessions/')?directory([{...entry(),status:'UNKNOWN',basis_current:null,current_review_status:null}]):mode==='success'?receipt():{operation_id:'api-stage',status:'UNKNOWN',automatic_model_replay:false});const r=new DeliveryHistoryRecovery(f.api,storage(),item=>cards=upsertDeliveryFeed(cards,item));await r.restore([target]);assert.equal(cards.length,1);const original=cards[0].artifact;mode='unknown';await r.restore([target],{refresh:true});assert.equal(cards.length,1);assert.equal(cards[0].artifact,original);assert.equal(cards[0].deliveryInspection.operationId,'api-stage');assert.equal(chatDeliveryFiles(cards[0].artifact.data).all.length,1);assert.ok(f.calls.every(v=>v.method==='GET'));r.stop();
});


test('tainted local same-project keys cannot adopt another session or unbound legacy incomplete result; original keys remain inspectable',async()=>{
 const local=storage();for(const key of ['wrong-session','legacy'])saveDeliveryHistory(local,target.projectId,'STAGE',basis.id,key);const f=transport(url=>url.includes('/sessions/')?directory([]):{operation_id:url.split('/').at(-1),project_id:target.projectId,...url.endsWith('legacy')?{}:{session_ref:{...basis,id:'foreign-session'}},kind:'STAGE',status:'FAILED_OR_PARTIAL',automatic_model_replay:false}),cards=[],r=new DeliveryHistoryRecovery(f.api,local,item=>cards.push(item));await r.restore([target]);assert.equal(cards.length,2);for(const item of cards){assert.equal(item.status,'INSPECTION_UNAVAILABLE');assert.equal(item.artifact,undefined);assert.ok(item.deliveryInspection.operationId);}assert.match(local.getItem('eeas-delivery-history:guide-project:STAGE:guide-session'),/legacy/);
 const partial={operation_id:'partial',project_id:target.projectId,kind:'STAGE',session_ref:basis,status:'FAILED_OR_PARTIAL',automatic_model_replay:false};assert.equal(verifyDeliveryTarget(partial,target,'partial'),partial);for(const change of [{project_id:'other'},{kind:'EXPORT'},{session_ref:{...basis,id:'other'}},{session_ref:undefined},{proposal_ref:basis}])assert.throws(()=>verifyDeliveryTarget({...partial,...change},target,'partial'));r.stop();
});

test('a catalog claim constrains incomplete observations to the full original revision/hash too',async()=>{
 const partial={operation_id:'api-stage',project_id:target.projectId,kind:'STAGE',session_ref:{...basis,revision:1,sha256:'e'.repeat(64)},status:'PROCESSING',automatic_model_replay:false},f=transport(url=>url.includes('/sessions/')?directory():partial),cards=[],r=new DeliveryHistoryRecovery(f.api,storage(),item=>cards.push(item));await r.restore([target]);assert.equal(cards.length,1);assert.equal(cards[0].status,'INSPECTION_UNAVAILABLE');assert.equal(cards[0].artifact,undefined);r.stop();
});


test('a failed refresh is visible in default copy while original files/actions survive, and later success updates the same card',async()=>{
 let mode='success',cards=[];const f=transport(url=>url.includes('/sessions/')?directory():mode==='success'?receipt():new Response(JSON.stringify({error:{code:'UNAVAILABLE',message:'RAW_PRIVATE_DIAGNOSTIC_NOT_DEFAULT'}}),{status:503}));const r=new DeliveryHistoryRecovery(f.api,storage(),item=>cards=upsertDeliveryFeed(cards,item));await r.restore([target]);const first=cards[0],artifact=first.artifact,files=chatDeliveryFiles(artifact.data),opened=[],context={dispatch:async(...args)=>opened.push(args)};assert.equal(files.all.length,1);
 mode='failure';await r.restore([target],{refresh:true});const item=cards[0],visible=authoredText(item.body_copy??item.body);assert.equal(item.id,first.id);assert.equal(item.artifact,artifact);assert.equal(item.deliveryTarget,first.deliveryTarget);assert.equal(item.title,'交付回执尚未核对');assert.equal(item.status,'INSPECTION_UNAVAILABLE');assert.match(visible,/暂时无法核对/);assert.match(visible,/未核对.*文件.*保留/);assert.ok(!visible.includes('RAW_PRIVATE_DIAGNOSTIC'));assert.ok(!visible.includes('原操作编号'));assert.deepEqual(chatDeliveryFiles(item.artifact.data),files);await openChatDeliveryFile(context,item.artifact,item.artifact.data,files.all[0]);assert.equal(opened.length,1);assert.equal(opened[0][0],'delivery.file.open');assert.deepEqual(opened[0][2].file.file_source_ref,source);
 mode='success';await r.restore([target],{refresh:true});assert.equal(cards.length,1);assert.equal(cards[0].id,first.id);assert.equal(cards[0].status,'STAGE_REPORT');assert.equal(cards[0].deliveryInspection,undefined);assert.ok(!authoredText(cards[0].body_copy??cards[0].body).includes('暂时无法核对'));r.stop();
});
