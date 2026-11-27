import test from 'node:test';
import assert from 'node:assert/strict';
import {DeliveryHistoryRecovery,upsertDeliveryFeed,deliveryFeedItem} from '../src/delivery-recovery.ts';
const basis={id:'guide',revision:2,sha256:'a'.repeat(64)},target={projectId:'project',kind:'STAGE',basis};
const file=key=>({path:`outputs/${key}/same.docx`,sha256:'b'.repeat(64),file_source_ref:{kind:'local_file',project_id:'project',workspace_id:'workspace',binding_revision:1,binding_sha256:'c'.repeat(64),path:`outputs/${key}/same.docx`,revision:1,sha256:'b'.repeat(64)}});
const receipt=key=>({operation_id:key,status:'STAGE_REPORT',session_ref:basis,pending_count:3,blocking_count:0,report:{artifact:{id:'word-'+key,filename:'same.docx',sha256:'b'.repeat(64)}},diagrams:[],artifacts:[],project_files:[file(key)],file_checks:[{path:file(key).path,status:'CURRENT'}],basis_current:true,current_review_status:'CURRENT',execution_authorized:false});
const created='2026-10-28T08:15:00+08:00';
test('server original created_at survives operation GET and later manual recheck without replacing it with observation time',async()=>{
 let cards=[];const client={sessionOperations:async()=>({entries:[{operation_id:'second',session_ref:basis,created_at:created}]}),operation:async()=>receipt('second')};
 const r=new DeliveryHistoryRecovery(client,{getItem:()=>null},item=>cards=upsertDeliveryFeed(cards,item));await r.restore([target]);assert.equal(cards[0].deliveryHistory?.createdAt,created);await r.read(target,'second');assert.equal(cards[0].deliveryHistory?.createdAt,created);r.stop();
});

const {deliveryHistoryPlan:historyPlan,deliveryDirectoryKey,checkedDeliveryAvailable,stableDeliveryOrder}=await import('../src/delivery-history.ts');
const state=Symbol('complete fixture inspection');
const deliveryHistoryPlan=(items,directories)=>historyPlan(items,directories===state?Object.fromEntries(items.filter(i=>i.deliveryTarget).map(i=>[deliveryDirectoryKey(i.deliveryTarget),{state:'COMPLETE',operationIds:items.map(v=>v.artifact?.artifactId??v.deliveryInspection?.operationId)}])):directories);
const item=(key,time,changes={})=>({...deliveryFeedItem(target,{...receipt(key),...changes}),deliveryHistory:{createdAt:time,basis,directoryKey:JSON.stringify(['project','STAGE',basis.id,2,basis.sha256])}});
const time=n=>`2026-10-28T00:00:0${n}Z`;
const permutations=values=>values.length?values.flatMap((value,i)=>permutations(values.filter((_,j)=>i!==j)).map(tail=>[value,...tail])):[[]];
test('four concurrent operation GETs finish in reverse order; a fresh instance and every mixed-time arrival order project the same original order',async()=>{
 const entries=[1,2,3,4].map(n=>({operation_id:'op'+n,session_ref:basis,created_at:time(n)}));
 async function restore(order){let cards=[],releases=new Map(),started=0;const r=new DeliveryHistoryRecovery({sessionOperations:async()=>({entries}),operation:(pid,key)=>new Promise(resolve=>{started++;releases.set(key,()=>resolve(receipt(key)));})},{getItem:()=>null},next=>cards=upsertDeliveryFeed(cards,next));const pending=r.restore([target]);while(started<4)await new Promise(done=>setTimeout(done,0));for(const n of order){releases.get('op'+n)();await new Promise(done=>setTimeout(done,0));}await pending;r.stop();return deliveryHistoryPlan(cards,state);}
 const reverse=await restore([4,3,2,1]),restart=await restore([1,2,3,4]);assert.deepEqual(reverse.ids,restart.ids);assert.equal(reverse.groups[0].primary,'delivery:project:op4');assert.equal(reverse.ids.length,4);assert.equal(reverse.groups[0].ordinary.length,3);
 const mixed=[item('z',time(4)),item('m',undefined),item('a',time(1)),item('b','not-a-time')],expected=deliveryHistoryPlan(mixed,state).ids;
 for(const order of permutations(mixed)){assert.deepEqual(deliveryHistoryPlan(order,state).ids,expected);assert.equal(deliveryHistoryPlan(order,state).groups[0].primary,undefined);}
});
test('same basename and version retain both operation identities; exact source groups never merge project/kind/revision/hash',()=>{
 const a=item('one',time(1)),b=item('two',time(2));let plan=deliveryHistoryPlan([a,b],state);assert.equal(plan.ids.length,2);assert.equal(plan.groups[0].primary,b.id);
 const changes=[{projectId:'other'},{kind:'EXPORT'},{basis:{...basis,revision:1}},{basis:{...basis,sha256:'f'.repeat(64)}}];
 const variants=changes.map((change,i)=>{const v=structuredClone(a);v.id+='-'+i;v.deliveryTarget={...target,...change};if(change.kind)v.artifact.data={...v.artifact.data,status:'EXPORTED_CONFIRMED_PLAN_DERIVATIVES',proposal_ref:basis,session_ref:undefined};if(change.basis)v.artifact.data.session_ref=change.basis;return v;});
 assert.equal(deliveryHistoryPlan([a,b,...variants],state).groups.length,5);
});
test('current labels require actual complete checks and valid original times; missing/tied time and directory pending/failure never choose a unique newest',()=>{
 const good=item('good',time(2));assert.equal(checkedDeliveryAvailable(good),true);
 for(const delta of [{file_checks:[]},{file_checks:[...receipt('good').file_checks,{path:file('good').path,status:'UNKNOWN'}]},{basis_current:false},{current_review_status:'SOURCE_OR_VERSION_CHANGED'},{template_source_current:false},{project_files:[],file_checks:[]}])assert.equal(checkedDeliveryAvailable(item('good',time(2),delta)),false,JSON.stringify(delta));
 for(const dirs of [{},{[deliveryDirectoryKey(target)]:{state:'PENDING',operationIds:[]}},{[deliveryDirectoryKey(target)]:{state:'UNAVAILABLE',operationIds:[]}}]){const p=deliveryHistoryPlan([good,item('old',time(1))],dirs);assert.equal(p.groups[0].primary,undefined);assert.equal(p.groups[0].ordinary.length,0);}
 for(const other of [item('tie',time(2)),item('missing',undefined),item('invalid','yesterday')])assert.equal(deliveryHistoryPlan([good,other],state).groups[0].primary,undefined);
});
test('new UNKNOWN/failure and changed files stay outside ordinary history; only a genuinely newer issue makes last available wording',()=>{
 const good=item('good',time(2)),old=item('old',time(1)),failed=item('failure',time(3),{status:'FAILED_OR_PARTIAL',kind:'STAGE',project_id:'project',automatic_model_replay:false}),unknown={...item('unknown',time(4)),status:'INSPECTION_UNAVAILABLE',deliveryInspection:{operationId:'unknown',diagnostic:'fixture'}};
 const changed=item('changed',time(5),{file_checks:[{path:file('changed').path,status:'STALE'}]});
 let g=deliveryHistoryPlan([good,old,failed,unknown,changed],state).groups[0];assert.equal(g.primary,good.id);assert.equal(g.previous,true);assert.deepEqual(g.ordinary,[old.id]);for(const v of [failed,unknown,changed])assert.ok(g.ids.includes(v.id)&&!g.ordinary.includes(v.id));
 g=deliveryHistoryPlan([good,item('oldfailure',time(1),{status:'FAILED_OR_PARTIAL',kind:'STAGE',project_id:'project',automatic_model_replay:false})],state).groups[0];assert.equal(g.previous,false);
 assert.equal(deliveryHistoryPlan([good,item('same-time-failure',time(2),{status:'FAILED_OR_PARTIAL',kind:'STAGE',project_id:'project',automatic_model_replay:false})],state).groups[0].primary,undefined);
});
test('directory completeness is published only after operation inspection, failure is explicit and a late old view cannot alter it',async()=>{
 let done,states=[],cards=[],broken=false;const wait=new Promise(resolve=>done=resolve),client={sessionOperations:async()=>{if(broken)throw new Error('directory unavailable');return{entries:[{operation_id:'one',session_ref:basis,created_at:time(1)}]};},operation:async()=>{await wait;return receipt('one');}};
 const r=new DeliveryHistoryRecovery(client,{getItem:()=>null},v=>cards=upsertDeliveryFeed(cards,v),(_,v)=>states.push(v));const pending=r.restore([target]);await new Promise(resolve=>setTimeout(resolve,0));assert.deepEqual(states,['PENDING']);done();await pending;assert.deepEqual(states,['PENDING','COMPLETE']);broken=true;await assert.rejects(r.restore([target],{refresh:true}));assert.equal(states.at(-1),'UNAVAILABLE');assert.equal(cards.length,1);r.stop();
 const gate=Promise.withResolvers(),events=[],late=new DeliveryHistoryRecovery({sessionOperations:()=>gate.promise,operation:async()=>receipt('one')},{getItem:()=>null},()=>{},(_,v)=>events.push(v));const old=late.restore([target]);await new Promise(resolve=>setTimeout(resolve,0));late.resetView();gate.resolve({entries:[]});await old;assert.deepEqual(events,['PENDING']);late.stop();
});
test('interaction keeps existing operation nodes/order while new records append, and normal settled projection restores original-time priority',()=>{
 const before=['old','other'],desired=['new','old','other'];assert.deepEqual(stableDeliveryOrder(before,desired,true),['old','other','new']);assert.deepEqual(stableDeliveryOrder(before,desired,false),desired);assert.deepEqual(stableDeliveryOrder(before,['other','new'],true),['other','new']);
});

test('a retained card absent from this inspection cannot claim newest just because its old directory once succeeded',()=>{const prior=item('old',time(1)),newer=item('new',time(2));const view={[deliveryDirectoryKey(target)]:{state:'COMPLETE',operationIds:['new']}};const p=historyPlan([prior,newer],view);assert.equal(p.groups[0].primary,undefined);assert.equal(p.groups[0].ordinary.length,0);});

test('mixed result timeline keeps ordinary results and anchors each source group at its last original member',()=>{const a=item('old',time(1)),b=item('new',time(2)),ordinary={id:'new-answer',status:'RETURNED'},other={...item('other',time(3)),deliveryTarget:{...target,projectId:'other-project'}};assert.deepEqual(deliveryHistoryPlan([a,ordinary],state).ids,[a.id,ordinary.id]);assert.deepEqual(deliveryHistoryPlan([a,ordinary,b],state).ids,[ordinary.id,b.id,a.id]);assert.deepEqual(deliveryHistoryPlan([a,other,ordinary,b],state).ids,[other.id,ordinary.id,b.id,a.id]);});

test('identical original receipt GET preserves card identity across JSON key order, while real source/status changes are delivered',()=>{const prior=item('old',time(1)),copy={...prior,artifact:Object.fromEntries(Object.entries(prior.artifact).reverse())};copy.artifact.data=Object.fromEntries(Object.entries(prior.artifact.data).reverse());let list=upsertDeliveryFeed([prior],copy);assert.equal(list[0].artifact,prior.artifact);const changed={...copy,artifact:{...copy.artifact,data:{...copy.artifact.data,file_checks:[{path:file('old').path,status:'STALE'}]}}};list=upsertDeliveryFeed(list,changed);assert.notEqual(list[0].artifact,prior.artifact);assert.equal(list[0].artifact.data.file_checks[0].status,'STALE');});

test('same-operation source/currentness/inspection changes remain visible and the real open path consumes the updated full FileSource',async()=>{
 const {chatDeliveryFiles,openChatDeliveryFile}=await import('../src/chat-artifacts.ts');
 const prior=item('same',time(1)),data=structuredClone(prior.artifact.data);data.current_review_status='SOURCE_OR_VERSION_CHANGED';data.basis_current=false;data.file_checks[0].status='STALE';data.project_files[0].file_source_ref.binding_revision=2;data.project_files[0].file_source_ref.binding_sha256='d'.repeat(64);
 const incoming={...prior,artifact:{...prior.artifact,data}},list=upsertDeliveryFeed([prior],incoming),changed=list[0];assert.notEqual(changed.artifact,prior.artifact);assert.equal(checkedDeliveryAvailable(changed),false);assert.equal(deliveryHistoryPlan(list,state).groups[0].primary,undefined);
 const calls=[],context={dispatch:async(...args)=>calls.push(args)},oldFile=chatDeliveryFiles(prior.artifact.data).all[0],actual=chatDeliveryFiles(changed.artifact.data).all[0];await assert.rejects(openChatDeliveryFile(context,changed.artifact,changed.artifact.data,oldFile));assert.equal(calls.length,0);await openChatDeliveryFile(context,changed.artifact,changed.artifact.data,actual);assert.equal(calls.length,1);assert.equal(calls[0][0],'delivery.file.open');assert.equal(calls[0][2].receipt,data);assert.equal(calls[0][2].file.file_source_ref.binding_revision,2);
 const inspected=upsertDeliveryFeed(list,{...incoming,status:'INSPECTION_UNAVAILABLE',deliveryInspection:{operationId:'same',diagnostic:'fixture not checked'}})[0];assert.equal(inspected.deliveryInspection.operationId,'same');assert.equal(checkedDeliveryAvailable(inspected),false);
});
