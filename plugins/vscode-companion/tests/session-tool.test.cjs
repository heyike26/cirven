'use strict';
// Authored NOT_RUN; controller/transport boundary, not real VS Code qualification.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {PrivateState,CompanionController,draftKey,scopeOf}=require('../controller.cjs');
const tools=require('../session-tool.cjs');
function scene(t){const root=fs.mkdtempSync(path.join(process.env.EEAS_TEST_TMPDIR||os.tmpdir(),'cirven-tool-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
 const c=new CompanionController({state:new PrivateState(root)}),ref={message_id:'msg_one',part_id:'prt_one',call_id:'call_one',sha256:'b'.repeat(64)};
 c.selected={project_id:'project',session_ref:{id:'session',revision:1,sha256:'a'.repeat(64)},generation:1,messages:[{info:{id:'msg_one'},parts:[{id:'prt_one',type:'tool',tool_ref:ref}]}]};
 return {c,ref,scope:scopeOf(c.selected)};}
const response=(scope,ref)=>({scope,tool_ref:ref,tool:'bash',status:'completed',source:'EXACT_SAVED_ORIGINAL_TOOL_PART',read_only:true,model_calls:0,tool_invocations:0,input:{text:'command',truncated:false},output:{text:'original output',truncated:false},error:{text:'',truncated:false}});
test('explicit tool detail read preserves draft and does not journal or replay a tool invocation',async t=>{
 const {c,ref,scope}=scene(t),calls=[];c.client={methods:['session.tool'],rpc:async(method,s,payload)=>{calls.push({method,s,payload});return {status:'OK',result:response(s,payload.tool_ref)};}};
 const key=draftKey(c.selected);c.setDraft(key,0,'UNSENT_USER_WORK');const token=tools.token(scope,ref);
 await c.readTool(token);assert.equal(c.snapshot().tools.detail.value.output.text,'original output');
 assert.equal(c.snapshot().draft.text,'UNSENT_USER_WORK');assert.equal(c.state.value.requests.length,0);assert.equal(calls.length,1);assert.equal(calls[0].method,'session.tool');
});
test('a late detail response cannot appear in another selection or changed original part',async t=>{
 const {c,ref,scope}=scene(t);let release;c.client={methods:['session.tool'],rpc:()=>new Promise(resolve=>{release=resolve;})};
 const pending=c.readTool(tools.token(scope,ref));c.epoch++;c.selected={...c.selected,session_ref:{...c.selected.session_ref,id:'other'}};
 release({status:'OK',result:response(scope,ref)});await pending;assert.equal(c.snapshot().tools.detail,null);
});
test('missing grant and stale reference fail before contacting the desktop',async t=>{
 const {c,ref,scope}=scene(t);let calls=0;c.client={methods:[],rpc:async()=>{calls++;}};
 await assert.rejects(c.readTool(tools.token(scope,ref)),{code:'CAPABILITY_DENIED'});
 await assert.rejects(c.readTool('c'.repeat(64)),{code:'TOOL_CHANGED'});assert.equal(calls,0);
});
