'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{Module}=require('node:module');
const file=path.join(__dirname,'quick-supervisor-direct.test.cjs'),raw=fs.readFileSync(file,'utf8'),boundary=raw.indexOf('\ntest(');
assert.ok(boundary>0);const setup=new Module(file);setup.filename=file;setup.paths=Module._nodeModulePaths(path.dirname(file));setup._compile(raw.slice(0,boundary)+'\nmodule.exports={fixture,digest};',file);
const {fixture,digest}=setup.exports;
const observed='2026-10-19T12:00:00Z';
function taskFor(body){return {key:{kind:'native_message',project_id:null,object_id:body.operation_id,operation_id:body.operation_id,generation:1},label:'总管问题',source_status:'RETURNED',source_ref:{id:body.operation_id,revision:1,sha256:digest({request_sha256:digest(body),status:'RETURNED'})},classification:'EXECUTION_RETURNED',observation_status:'CURRENT',observed_at:observed};}
async function prepare(t){
  const f=fixture(t);await f.surface.openActivity({settings:{enabled:true},observer:{status:'READY'},tasks:[]});await f.client.start();
  await f.client.ask({operationId:'original_question',text:'旧问题',providerProfileRef:f.profile});
  const post=f.calls.find(row=>row.body),task=taskFor(post.body);await f.surface.openActivity({settings:{enabled:true},observer:{status:'READY'},tasks:[task]});
  return {...f,task,selection:{operation_id:'original_question',request_sha256:digest(post.body),question:'旧问题',answer_text:'原回答',status:'RETURNED'}};
}
function select(f){const state=f.surface.get();return f.send({kind:'activity-open',epoch:state.epoch,activity_revision:state.activity.revision,token:state.activity.items[0].token});}
test('owned coordinator task selection remains inside the same floating surface and preserves the shared draft',async t=>{
  const f=await prepare(t);f.client.edit('仍在输入的新问题');await f.client.flush();const count=f.calls.length;
  f.surface.onOpenTask=async key=>{assert.deepEqual(key,f.task.key);return {status:'SUPERVISOR',selection:f.selection};};
  assert.equal((await select(f)).status,'SUPERVISOR');assert.equal(f.surface.get().visible,true);assert.equal(f.surface.get().presentation,'ACTIVITY');
  assert.deepEqual(f.surface.get().supervisor.selection,f.selection);assert.equal(f.client.view.draft,'仍在输入的新问题');assert.equal(f.calls.length,count);assert.deepEqual(f.pageCalls,[]);await f.client.close();
});
test('background selected-answer refresh does not reopen a dismissed selection, while an explicit same-task selection has a new display intent',async t=>{
  const f=await prepare(t);f.surface.onOpenTask=async()=>({status:'SUPERVISOR',selection:f.selection});await select(f);
  const revision=f.surface.get().supervisor.selection_revision;
  const update={...f.selection,answer_text:'同一原操作的新答复'};f.surface.updateSupervisorConversation(f.store.snapshot(),f.profile,update);
  assert.equal(f.surface.get().supervisor.selection_revision,revision);assert.equal(f.surface.get().supervisor.selection.answer_text,update.answer_text);
  f.surface.updateSupervisorConversation(f.store.snapshot(),f.profile,{...update,operation_id:'foreign'});
  assert.equal(f.surface.get().supervisor.selection.answer_text,update.answer_text);
  await select(f);assert.equal(f.surface.get().supervisor.selection_revision,revision+1);await f.client.close();
});
test('foreign operation or changed digest cannot replace the floating selection',async t=>{
  const f=await prepare(t);
  for(const extra of [{operation_id:'another'},{request_sha256:'b'.repeat(64)},{question:'x'.repeat(8001)}]){
    f.surface.onOpenTask=async()=>({status:'SUPERVISOR',selection:{...f.selection,...extra}});
    assert.equal((await select(f)).status,'ACTION_FAILED');assert.equal(f.surface.get().visible,true);assert.equal(f.surface.get().supervisor.selection,null);
  }
  await f.client.close();
});
test('an unavailable original destination leaves the floating coordinator visible',async t=>{
  const f=await prepare(t);f.surface.onOpenTask=async()=>({status:'UNAVAILABLE'});
  assert.equal((await select(f)).status,'UNAVAILABLE');assert.equal(f.surface.get().visible,true);await f.client.close();
});
