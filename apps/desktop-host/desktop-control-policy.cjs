'use strict';
const {exactKeys}=require('./ipc-policy.cjs');
const id=/^[A-Za-z0-9_-]{1,100}$/,hash=/^[a-f0-9]{64}$/;
function ref(value){exactKeys(value,['id','revision','sha256']);if(!id.test(value.id)||!Number.isSafeInteger(value.revision)||value.revision<1||!hash.test(value.sha256))throw new Error('Invalid control source reference');return {...value};}
function epoch(value,min){if(!Number.isSafeInteger(value)||value<min)throw new Error('Invalid control epoch');return value;}
function delegateInput(value){
 exactKeys(value,['operation_id','surface_ref','expected_input_epoch','expected_control_epoch','execution_ref','user_requested_delegation']);
 if(!id.test(value.operation_id)||value.user_requested_delegation!==true)throw new Error('An explicit user delegation is required');
 const execution=value.execution_ref;exactKeys(execution,['project_id','execution_id','request_sha256',...(Object.hasOwn(execution??{},'task_ref')?['task_ref']:[])]);
 if(!id.test(execution.project_id)||!id.test(execution.execution_id)||!hash.test(execution.request_sha256))throw new Error('Exact execution source required');
 return {...value,surface_ref:ref(value.surface_ref),expected_input_epoch:epoch(value.expected_input_epoch,0),expected_control_epoch:epoch(value.expected_control_epoch,1),execution_ref:{...execution,...(execution.task_ref?{task_ref:ref(execution.task_ref)}:{})}};
}
function takeoverInput(value){
 const scoped=Object.hasOwn(value??{},'surface_ref');exactKeys(value,['operation_id','expected_control_epoch',...(scoped?['surface_ref','expected_input_epoch']:[])]);
 if(!id.test(value.operation_id))throw new Error('Exact takeover operation required');
 return {...value,expected_control_epoch:epoch(value.expected_control_epoch,1),...(scoped?{surface_ref:ref(value.surface_ref),expected_input_epoch:epoch(value.expected_input_epoch,0)}:{})};
}
module.exports={delegateInput,takeoverInput};
