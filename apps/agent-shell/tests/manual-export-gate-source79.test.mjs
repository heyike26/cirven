import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
const fixturePath=resolve('apps/agent-shell/tests/manual-harness-source59.test.mjs');
const prefix=readFileSync(fixturePath,'utf8').split("test('original export typed gate")[0].replace("const app=process.env.EEAS_FRONTEND_APP??resolve(dirname(fileURLToPath(import.meta.url)),'..')",`const app=${JSON.stringify(resolve('apps/agent-shell'))}`);
const {harness,fixture,click,input,settle,until,exportGate,copy}=await import('data:text/javascript;base64,'+Buffer.from(prefix+'\nexport {harness,fixture,click,input,settle,until,exportGate,copy};').toString('base64'));
const workspace=()=>({id:'b'.repeat(64),project_id:'B',revision:1,binding_sha256:'a'.repeat(64),root:'D:\\QA B',allowed_write_paths:['output/']});
const options=()=>({workspace:workspace(),directory:'output/full'});
const history=s=>s.delivery.readDeliveryHistory(s.storage,'B','EXPORT','proposal_B');
const dispatch=s=>s.context.dispatch('proposal.delivery.export',s.artifact,options());
function clear(state){return {...copy(state.gate),status:'CLEAR',files:[]};}

test('known manual changes replace the primary export action with review; inspect is GET only with no ghost history',async t=>{
 const s=harness(t);await exportGate(s);assert.equal(s.button('生成完整交付文件'),undefined);assert.ok(s.button('核对人工修改'));assert.deepEqual(history(s),[]);assert.equal(s.state.exports.length,0);assert.equal(s.state.captureWrites.length,0);
 const reads=s.state.calls.filter(c=>c.path.includes('/harness/manual-changes?'));assert.equal(reads.length,1);assert.equal(reads[0].method,'GET');assert.ok(reads[0].path.includes('revision=3&sha256='));assert.ok(reads[0].path.includes('binding_revision=1'));
});

test('CLEAR inspection does not export after project navigation hides the original renderer during GET',async t=>{
 const state=fixture();state.inspection=clear(state);let release;state.holdInspect=new Promise(done=>release=done);const s=harness(t,state);await settle();click(s.button('生成完整交付文件'));await until(()=>state.calls.some(c=>c.path.includes('/harness/manual-changes?')));s.navigate('elsewhere');release();await settle();assert.equal(state.exports.length,0);assert.deepEqual(history(s),[]);assert.equal(state.opened.length,0);
});

test('late inspect cannot replace a new local reason or gate; inspect failure never dispatches export',async t=>{
 const state=fixture();state.inspection=clear(state);let release;state.holdInspect=new Promise(done=>release=done);const s=harness(t,state);await settle();click(s.button('生成完整交付文件'));await until(()=>state.calls.some(c=>c.path.includes('/harness/manual-changes?')));s.draft().set('fields','changeReason','核对期间的新草稿');release();await settle();assert.equal(state.exports.length,0);assert.deepEqual(history(s),[]);assert.equal(s.draft().fields.changeReason,'核对期间的新草稿');
 state.holdInspect=null;state.failInspect=true;click(s.button('生成完整交付文件'));await until(()=>s.all().some(n=>n.props?.role==='alert'));assert.equal(state.exports.length,0);assert.deepEqual(history(s),[]);
});

test('exact preclaim rejection removes only its newly prepared identity, preserving historical and concurrent keys',async t=>{
 const s=harness(t);await settle();s.delivery.saveDeliveryHistory(s.storage,'B','EXPORT','proposal_B','old-ghost-preserved');let failure;try{await dispatch(s);}catch(e){failure=e;}assert.equal(failure?.name,'DeliveryGateError');assert.equal(failure.preclaim,true);assert.equal(s.state.exports.length,1);assert.deepEqual(history(s),['old-ghost-preserved']);assert.equal(s.state.calls.filter(c=>c.path.includes('/planning-delivery/operations/')).length,0);
 const full=Array.from({length:20},(_,i)=>'prior-'+i);s.storage.setItem(s.delivery.deliveryHistoryKey('B','EXPORT','proposal_B'),JSON.stringify(full));await assert.rejects(()=>dispatch(s),e=>e.preclaim===true);assert.deepEqual(history(s),full,'temporary key eviction is restored without deleting old history');s.storage.setItem(s.delivery.deliveryHistoryKey('B','EXPORT','proposal_B'),JSON.stringify(['old-ghost-preserved']));
 s.delivery.saveDeliveryHistory(s.storage,'B','EXPORT','proposal_B','same-old');s.delivery.saveDeliveryHistory(s.storage,'B','EXPORT','proposal_B','other-new');s.delivery.discardPreclaimDelivery(s.storage,'B','proposal_B','same-old',{...failure,operationId:'same-old'},false);assert.deepEqual(history(s),['old-ghost-preserved','same-old','other-new']);
});

test('postclaim manual gate retains the original operation and exposes actual partial receipt; 404 never clears history',async t=>{
 const s=harness(t);await settle();s.state.postclaim=true;let failure;try{await dispatch(s);}catch(e){failure=e;}assert.equal(failure.preclaim,false);const key=failure.operationId;assert.deepEqual(history(s),[key]);assert.equal(s.state.calls.filter(c=>c.path.endsWith('/operations/'+key)).length,1);assert.equal(s.state.exports.length,1);
 const api=new s.delivery.PlanningDeliveryApi(s.client);s.state.deliveryReceipt={operation_id:'postclaim-known',status:'FAILED_OR_PARTIAL',automatic_model_replay:false,steps:{report:{status:'SAVED'}}};await assert.rejects(()=>api.export(s.state.initial,options(),'postclaim-known'),e=>e.preclaim===false&&e.receipt?.steps.report.status==='SAVED');assert.equal(s.state.exports.length,2);
});

test('a CLEAR read followed by a new manual gate shows review, not a nonexistent receipt entry',async t=>{
 const state=fixture();state.inspection=clear(state);const s=harness(t,state);await settle();click(s.button('生成完整交付文件'));await until(()=>!!s.button('核对人工修改'));assert.equal(state.exports.length,1);assert.deepEqual(history(s),[]);assert.equal(s.button('读取上次回执').props.disabled,true);assert.equal(state.calls.filter(c=>c.path.includes('/planning-delivery/operations/')).length,0);
});

test('changed head fails before preparing history or sending export',async t=>{
 const s=harness(t);await settle();s.state.head={...s.state.head,revision:4,current_revision:4,sha256:'4'.repeat(64)};await assert.rejects(()=>dispatch(s),/现行已确认/);assert.deepEqual(history(s),[]);assert.equal(s.state.exports.length,0);
});
