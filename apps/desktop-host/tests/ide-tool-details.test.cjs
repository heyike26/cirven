'use strict';
// Authored NOT_RUN for concentrated debug. Injected API transport only.
const test=require('node:test'),assert=require('node:assert/strict');
const {createIdeApiAdapter}=require('../ide-api-adapter.cjs');
const {reference}=require('../ide-tool-details.cjs');
const scope={project_id:'project',session_ref:{id:'session',revision:1,sha256:'a'.repeat(64)},generation:1};
function fixture(){const part={id:'prt_tool',type:'tool',tool:'bash',callID:'call_original',sessionID:'ses_original',messageID:'msg_original',state:{status:'error',input:{command:'python check.py'},output:'ACTUAL_SAVED_STDOUT',error:'ACTUAL_SAVED_FAILURE',title:'Validate implementation',time:{start:1,end:2}}};
 const message={info:{id:'msg_original',sessionID:'ses_original',role:'assistant'},parts:[part]};
 return {part,message,session:{...scope,original_session_id:'ses_original',messages:[message],observation_at:'2026-11-02T12:00:00Z',observation_is_current:false}};}
test('list omits tool bodies and explicit exact detail read performs only the existing session GET',async()=>{
 const f=fixture(),calls=[],dispatch=createIdeApiAdapter({request:async(route,body)=>{calls.push({route,body});assert.equal(body,undefined);return f.session;}});
 const list=await dispatch('session.get',{scope,payload:{}});
 assert.equal(JSON.stringify(list).includes('ACTUAL_SAVED_STDOUT'),false);
 const ref=list.messages[0].parts[0].tool_ref;
 const detail=await dispatch('session.tool',{scope,payload:{tool_ref:ref}});
 assert.equal(detail.input.text.includes('python check.py'),true);assert.equal(detail.output.text,'ACTUAL_SAVED_STDOUT');
 assert.equal(detail.error.text,'ACTUAL_SAVED_FAILURE');assert.equal(detail.model_calls,0);assert.equal(detail.tool_invocations,0);
 assert.equal(detail.observation_is_current,false);assert.equal(calls.length,2);assert.equal(calls[0].route,calls[1].route);
});
test('a changed part or sibling-session call cannot reuse the reviewed original tool reference',async()=>{
 const f=fixture(),ref=reference(f.session,f.message,f.part),dispatch=createIdeApiAdapter({request:async()=>f.session});
 f.part.state.output='NEW_OUTPUT';await assert.rejects(dispatch('session.tool',{scope,payload:{tool_ref:ref}}),{code:'TOOL_CHANGED'});
 f.part.sessionID='ses_sibling';assert.equal(reference(f.session,f.message,f.part),undefined);
 await assert.rejects(dispatch('session.tool',{scope,payload:{tool_ref:ref}}),{code:'TOOL_CHANGED'});
});
test('bounded explicit details retain truncation and do not execute embedded content',async()=>{
 const f=fixture();f.part.state.input={nested:{command:'<script>never execute</script>'}};f.part.state.output='😀'.repeat(18000);
 const ref=reference(f.session,f.message,f.part),dispatch=createIdeApiAdapter({request:async()=>f.session});
 const result=await dispatch('session.tool',{scope,payload:{tool_ref:ref}});
 assert.equal(Array.from(result.output.text).length,16000);assert.equal(result.output.truncated,true);
 assert.match(result.input.text,/<script>/);assert.equal(result.read_only,true);
});
