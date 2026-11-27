'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),vm=require('node:vm');
const {createRequire}=require('node:module');
const {QuickJournal,quickControlBody}=require('../quick-journal.cjs'),{QuickHost}=require('../quick-host.cjs');
const ref=id=>({id,revision:1,sha256:'a'.repeat(64)});
const body=(id='child',text='不要修改原件，先核对来源')=>({operation_id:id,expected_native_message_ref:ref('parent'),raw_text:text,intent:'AUTO'});
function host(t,request){const dir=fs.mkdtempSync(path.join(os.tmpdir(),'ordinary52-')),calls=[],journal=new QuickJournal({dataDir:dir,hostGeneration:'current'}),value=new QuickHost({journal,getCompanion:()=>null,request:async(url,data)=>{calls.push({url,data});return request?request(url,data):{operation_id:data?.operation_id??url.split('/').at(-1),status:'ACCEPTED'};}});t.after(()=>{value.close();fs.rmSync(dir,{recursive:true,force:true});});return {dir,journal,value,calls};}
test('ordinary and review control refs are exclusive; invalid control never reaches transport',async t=>{
 const f=host(t);
 for(const value of [{...body(),expected_review_ref:ref('review')},{...body(),intent:'GUESS'},{...body(),expected_native_message_ref:{...ref('parent'),revision:2}},{...body(),url:'https://unknown.invalid'}, {...body(),raw_text:' '}])await assert.rejects(f.value.reviewControl({parentOperationId:'parent',kind:'guidance',body:value}));
 assert.throws(()=>quickControlBody('permission',{...body(),request_ref:ref('permission'),reply:'once'}));assert.equal(f.calls.length,0);
 const input={parentOperationId:'parent',kind:'guidance',body:body()};await f.value.reviewControl(input);assert.deepEqual(f.calls[0],{url:'/api/desktop-host/native-messages/parent/guidance',data:body()});
 await assert.rejects(f.value.reviewControl({...input,body:body('child','changed')}),/differs/);assert.equal(f.calls.length,1);
});
test('unknown POST, duplicate action and restart only GET the identical ordinary receipt',async t=>{
 const f=host(t,async()=>{throw Error('lost acknowledgement');}),input={parentOperationId:'parent',kind:'guidance',body:body()};
 assert.equal((await f.value.reviewControl(input)).status,'UNKNOWN_OUTCOME');await f.value.reviewControl(input);
 assert.deepEqual(f.calls.map(x=>!!x.data),[true,false]);assert.equal(f.journal.pendingReviewControls('parent').length,1);
 const restored=new QuickJournal({dataDir:f.dir,hostGeneration:'restarted'}),calls=[];
 const other=new QuickHost({journal:restored,getCompanion:()=>null,request:async(url,data)=>{calls.push({url,data});return {operation_id:'child',status:'RETURNED',role:'LOCAL_STATUS',local_status:{active_operation_id:'initial'}};}});t.after(()=>other.close());
 await other.reviewControl({...input,inspect:true});assert.equal(calls.length,1);assert.equal(calls[0].data,undefined);assert.match(calls[0].url,/parent\/controls\/child$/);assert.equal(restored.reviewControl('child').result.role,'LOCAL_STATUS');assert.equal(f.calls.filter(x=>x.data).length,1);
});
test('shared journal allowance bounds ordinary and legacy review controls without rewriting old signatures',t=>{
 const f=host(t);const original={operation_id:'legacy',expected_review_ref:ref('review'),raw_text:'old exact text'};
 f.journal.prepareReviewControl('review-parent',{operationId:'legacy',kind:'guidance',body:original});const signed=structuredClone(f.journal.reviewControl('legacy'));
 for(let i=0;i<15;i++)f.journal.prepareReviewControl('parent',{operationId:'pending-'+i,kind:'guidance',body:body('pending-'+i)});
 assert.throws(()=>f.journal.prepareReviewControl('parent',{operationId:'over',kind:'guidance',body:body('over')}),/full/);
 assert.deepEqual(new QuickJournal({dataDir:f.dir,hostGeneration:'next'}).reviewControl('legacy'),signed);
 for(const row of f.journal.pendingReviewControls())f.journal.observeReviewControl(row.operationId,{operation_id:row.operationId,status:'RETURNED'});
 for(let i=16;i<128;i++){const id='done-'+i;f.journal.prepareReviewControl('parent',{operationId:id,kind:'guidance',body:body(id)});f.journal.observeReviewControl(id,{operation_id:id,status:'RETURNED'});}
 assert.throws(()=>f.journal.prepareReviewControl('parent',{operationId:'over-total',kind:'guidance',body:body('over-total')}),/full/);assert.equal(Object.keys(f.journal.state.reviewControls).length,128);
});
function surfaceFixture(){
 const filename=path.join(__dirname,'quick-surface.test.cjs'),requireFrom=createRequire(filename),context={require:id=>id==='node:test'?()=>{}:requireFrom(id),__dirname,Buffer,URL,console,setTimeout,clearTimeout,setImmediate,queueMicrotask,process};
 vm.runInNewContext(fs.readFileSync(filename,'utf8')+'\nglobalThis.fixture52=fixture;',context,{filename});return context.fixture52;
}
const fixture=surfaceFixture();
async function running(t,options={}){let latest;const calls=[];const f=fixture({onInspect:async id=>latest??{operation_id:id,status:'PROCESSING',native_message_ref:ref(id),native_message_phase:'RUNNING',control_capabilities:{can_guide:true,can_status:true,can_cancel:true}},onReviewControl:async request=>{calls.push(structuredClone(request));return options.control?options.control(request):{operation_id:request.body.operation_id,status:'ACCEPTED'};}});t.after(()=>f.surface.dispose());await f.surface.ready;await f.surface.openAsk();await f.edit('原始冻结问题');await f.command('send');await f.surface.openAsk();const parent=f.surface.draft.operationId;latest={operation_id:parent,status:'PROCESSING',native_message_ref:ref(parent),native_message_phase:'RUNNING',control_capabilities:{can_guide:true,can_status:true,can_cancel:true}};f.surface.publishResult(latest);return {...f,calls,parent,setLatest:v=>latest=v};}
test('actual Quick same send action guides the same parent without capture, resend, focus or changing its original text',async t=>{
 const f=await running(t),capture=f.surface.draft.capture,original=f.surface.draft.comment,count=f.events.filter(x=>x[0]==='capture').length;
 assert.equal(f.surface.get().guidanceEditable,true);await f.send({kind:'review-guidance-edit',epoch:f.surface.epoch,text:'改为比较两种方案，保留我的手改'});assert.equal(f.surface.get().canSend,true);await f.command('send');
 assert.equal(f.calls.length,1);assert.equal(f.calls[0].parentOperationId,f.parent);assert.deepEqual(JSON.parse(JSON.stringify(f.calls[0].body.expected_native_message_ref)),ref(f.parent));assert.equal(f.calls[0].body.intent,'AUTO');assert.equal(f.calls[0].body.expected_review_ref,undefined);
 assert.equal(f.surface.draft.capture,capture);assert.equal(f.surface.draft.comment,original);assert.equal(f.surface.draft.guidanceText,'');assert.equal(f.events.filter(x=>x[0]==='capture').length,count);assert.equal(f.events.filter(x=>x[0]==='send').length,1);assert.equal(f.events.filter(x=>x[0]==='expand').length,0);assert.equal(f.surface.draft.phase,'RUNNING');
});
test('actual Quick unknown control is never reconstructed; typed-ahead text survives its later ACK',async t=>{
 let resolve;const waiting=new Promise(done=>resolve=done);const f=await running(t,{control:async request=>request.inspect?{operation_id:request.body.operation_id,status:'ACCEPTED'}:waiting});
 await f.send({kind:'review-guidance-edit',epoch:f.surface.epoch,text:'first correction'});const sending=f.command('send');await new Promise(setImmediate);await f.send({kind:'review-guidance-edit',epoch:f.surface.epoch,text:'typed while backend works'});resolve({operation_id:f.calls[0].body.operation_id,status:'UNKNOWN_OUTCOME'});await sending;
 const id=f.calls[0].body.operation_id;await f.command('send');assert.equal(f.calls[1].inspect,true);assert.equal(f.calls[1].body.operation_id,id);assert.equal(f.surface.draft.guidanceText,'typed while backend works');assert.equal(f.events.filter(x=>x[0]==='send').length,1);
});
test('actual Quick STATUS receipt updates local status while the original task keeps running; unsupported scope cannot send',async t=>{
 const f=await running(t,{control:async request=>({operation_id:request.body.operation_id,status:'RETURNED',role:'LOCAL_STATUS',local_status:{active_operation_id:'initial'}})});
 await f.send({kind:'review-guidance-edit',epoch:f.surface.epoch,text:'现在进度如何？'});await f.command('send');assert.equal(f.surface.get().notice,'任务仍在运行');assert.equal(f.surface.draft.phase,'RUNNING');assert.equal(f.events.filter(x=>x[0]==='send').length,1);
 f.surface.publishResult({operation_id:f.parent,status:'PROCESSING',native_message_ref:ref(f.parent),control_capabilities:{can_guide:false,can_status:false,can_cancel:true}});await f.send({kind:'review-guidance-edit',epoch:f.surface.epoch,text:'preserved'});const result=await f.command('send');assert.equal(result.status,'ACTION_FAILED');assert.equal(f.calls.length,1);assert.equal(f.surface.draft.guidanceText,'preserved');
});
test('Quick accepts exact native observation receipts without inventing independent replies or changing the original running task',async t=>{
 for(const status of ['OBSERVED','NEXT_TURN_ASSOCIATED','COHORT_RETURNED']){
  const f=await running(t,{control:async request=>({operation_id:request.body.operation_id,status})});await f.send({kind:'review-guidance-edit',epoch:f.surface.epoch,text:'新的约束'});await f.command('send');assert.equal(f.surface.draft.guidanceText,'');assert.equal(f.surface.draft.reviewControl,null);assert.equal(f.surface.draft.phase,'RUNNING');assert.equal(f.surface.get().notice,status==='COHORT_RETURNED'?'任务已返回':'补充已送达');assert.equal(f.events.filter(x=>x[0]==='send').length,1);assert.equal(f.surface.draft.result.text,undefined);
 }
});
test('bounded durable control receipts preserve ordinary scope and local status instead of turning long traces into assistant replies',async t=>{
 const f=host(t,async(_url,data)=>({operation_id:data.operation_id,status:'RETURNED',trace:'x'.repeat(300000),native_message_ref:ref('parent'),native_message_phase:'RUNNING',control_capabilities:{can_guide:true,can_status:true,can_cancel:true},native_message_controls_total:17,native_message_controls_truncated:true,role:'LOCAL_STATUS',local_status:{active_operation_id:'initial'},dispatch_status:'RETURNED',semantic_acceptance:false}));
 await f.value.reviewControl({parentOperationId:'parent',kind:'guidance',body:body()});const saved=new QuickJournal({dataDir:f.dir,hostGeneration:'next'}).reviewControl('child').result;assert.equal(saved.local_projection.complete,false);assert.deepEqual(saved.native_message_ref,ref('parent'));assert.equal(saved.role,'LOCAL_STATUS');assert.equal(saved.local_status.active_operation_id,'initial');assert.equal(saved.native_message_controls_total,17);assert.equal(saved.native_message_controls_truncated,true);assert.equal(saved.dispatch_status,'RETURNED');assert.equal(saved.trace,undefined);
});
test('reopening Quick keeps the running target; explicit new question captures another page without cancelling or resending the original',async t=>{
 const f=await running(t),original=f.surface.draft,capture=original.capture,count=f.events.filter(x=>x[0]==='capture').length;
 const nextCapture={...capture,snapshot:{...capture.snapshot,operation_id:'capture_B',observation:{...capture.snapshot.observation,window:{title:'软件 B 的页面'}}}};
 f.surface.onCapture=async value=>{f.events.push(['capture',value]);return nextCapture;};
 await f.surface.openAsk();assert.equal(f.surface.draft,original);assert.equal(f.surface.draft.capture,capture);assert.equal(f.events.filter(x=>x[0]==='capture').length,count);
 const opened=await f.command('new-question');assert.equal(opened.draft.phase,'EDITING');assert.notEqual(f.surface.draft.id,original.id);assert.equal(f.surface.draft.operationId,null);assert.equal(f.surface.draft.capture,nextCapture);assert.equal(original.operationId,f.parent);assert.equal(original.phase,'RUNNING');assert.equal(original.capture,capture);
 assert.equal(f.events.filter(x=>x[0]==='capture').length,count+1);assert.equal(f.events.filter(x=>x[0]==='send').length,1);assert.equal(f.events.filter(x=>x[0]==='retain').at(-1)[1].operationId,f.parent);assert.equal(f.calls.length,0);
 await f.edit('只询问软件 B 的页面');await f.command('send');assert.equal(f.events.filter(x=>x[0]==='send').length,2);assert.notEqual(f.surface.draft.operationId,f.parent);assert.equal(original.phase,'RUNNING');
});
test('new question preserves unsent follow-up including text entered during the durable retention check',async t=>{
 const f=await running(t),original=f.surface.draft,count=f.events.filter(x=>x[0]==='capture').length;
 await f.send({kind:'review-guidance-edit',epoch:f.surface.epoch,text:'尚未发出的补充'});assert.equal((await f.command('new-question')).status,'ACTION_FAILED');assert.equal(f.surface.draft,original);assert.equal(original.guidanceText,'尚未发出的补充');assert.equal(f.events.filter(x=>x[0]==='retain').length,0);
 await f.send({kind:'review-guidance-edit',epoch:f.surface.epoch,text:''});let resolve;f.surface.onRetainSubmitted=()=>new Promise(done=>resolve=done);const switching=f.command('new-question');await new Promise(setImmediate);
 await f.send({kind:'review-guidance-edit',epoch:f.surface.epoch,text:'保存核验时继续输入'});resolve({status:'RETAINED',operationId:f.parent,ref:{id:original.id,revision:2,sha256:'a'.repeat(64)}});assert.equal((await switching).status,'ACTION_FAILED');assert.equal(f.surface.draft,original);assert.equal(original.guidanceText,'保存核验时继续输入');assert.equal(f.events.filter(x=>x[0]==='capture').length,count);assert.equal(f.events.filter(x=>x[0]==='send').length,1);assert.equal(f.calls.length,0);
});
