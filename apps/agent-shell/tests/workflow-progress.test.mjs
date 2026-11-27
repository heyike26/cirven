import test from 'node:test';
import assert from 'node:assert/strict';
import {workflowTodoIsCurrent} from '../src/workflow-progress.ts';
const todo=[{content:'Check the result 中文 😀',status:'completed',priority:'high'}];
const user=(id,operation_id)=>({info:{id,role:'user',sessionID:'sdk'},parts:[],conversation_input_origin:{operation_id}});
const tool=(parent,end=1,patch={})=>({info:{id:'assistant_'+end,role:'assistant',parentID:parent,sessionID:'sdk'},parts:[{type:'tool',tool:'todowrite',state:{status:'completed',metadata:{todos:todo},time:{end},...patch}}]});
const base=()=>({generation:1,observation_is_current:true,original_session_id:'sdk',work_mode_message:{operation_id:'first',generation:1,agent:'workflow'},messages:[user('u1','first'),tool('u1')],todo});
test('same SDK generation and identical completed todos do not establish a new task origin',()=>{const s=base();assert.equal(workflowTodoIsCurrent(s),true);s.work_mode_message={...s.work_mode_message,operation_id:'second'};s.messages.push(user('u2','second'));assert.equal(workflowTodoIsCurrent(s),false);s.messages.push(tool('u2',2));assert.equal(workflowTodoIsCurrent(s),true);});
test('actual completed output, root owner and unambiguous latest write are required',()=>{
 for(const change of [s=>s.todo=[{...todo[0],status:'pending'}],s=>s.messages=[user('u1','first'),tool('u1',1,{status:'running'})],s=>s.messages[1].info.sessionID='another_sdk',s=>s.messages.push(tool('u1',1)),s=>s.messages[1].parts[0].state.metadata={},s=>s.observation_is_current=false,s=>s.generation++]){const s=base();change(s);assert.equal(workflowTodoIsCurrent(s),false);}
});
test('precise recorded native message permits standalone owners, never an arbitrary latest user',()=>{const s=base();delete s.messages[0].conversation_input_origin;assert.equal(workflowTodoIsCurrent(s),false);s.work_mode_message.native_message_id='u1';assert.equal(workflowTodoIsCurrent(s),true);s.work_mode_message.native_message_id='another';assert.equal(workflowTodoIsCurrent(s),false);});
test('only observed guidance admitted for this frozen task and generation can own its updated todo',()=>{const s=base();s.messages.push(user('g1','guidance'),tool('g1',2));assert.equal(workflowTodoIsCurrent(s),false);s.guidance=[{operation_id:'guidance',generation:1,original_active_operation_id:'first',native_user_observed:true}];assert.equal(workflowTodoIsCurrent(s),true);for(const patch of [{generation:2},{original_active_operation_id:'second'},{native_user_observed:false}])assert.equal(workflowTodoIsCurrent({...s,guidance:[{...s.guidance[0],...patch}]}),false);});
