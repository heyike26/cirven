import fs from 'node:fs/promises';
import path from 'node:path';
import http from 'node:http';
import {randomUUID} from 'node:crypto';
import {canonical,digest,reference,object} from './protocol.mjs';

const OPERATION=/^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/;
const UNKNOWN=new Set(['DISPATCHING','UNKNOWN']);
export async function createPlanClient({config,read,onCommitted=async()=>({policy_sync_status:'UNCONFIRMED'}),timeoutMs=8000}){
  if(config.model_replan!==true||!config.plan_ref||!config.sink)throw new Error('WORKFLOW_MODEL_REPLAN_NOT_ADMITTED');
  const token=process.env.EEAS_WORKFLOW_PLAN_TOKEN;
  if(!token||! /^[A-Za-z0-9_-]{32,256}$/.test(token))throw new Error('WORKFLOW_PLAN_TOKEN_REQUIRED');
  const endpoint=new URL(config.sink.url),filename=config.state_path+'.plan-operations.json';
  let closed=false,closing=false,chain=Promise.resolve(),lastHash=null;
  const active=new Set(),memory=new Map();
  let journal={schema:'eeas.dynamic-workflow.plan-operations.v1',owner_ref:config.owner_ref,revision:0,operations:[]};
  try{
    const raw=await read(filename),saved=JSON.parse(raw);
    if(saved.schema!==journal.schema||canonical(saved.owner_ref)!==canonical(config.owner_ref)||!Array.isArray(saved.operations)||saved.operations.length>128)throw new Error('WORKFLOW_PLAN_JOURNAL_OWNER_CONFLICT');
    const {sha256,...basis}=saved;
    if(!Number.isSafeInteger(saved.revision)||saved.revision<1||digest(basis)!==sha256)throw new Error('WORKFLOW_PLAN_JOURNAL_INTEGRITY');
    journal=saved;lastHash=digest(raw);
    for(const row of saved.operations){
      if(!object(row)||!OPERATION.test(row.operation_id)||!/^[a-f0-9]{64}$/.test(row.request_sha256)||!['DISPATCHING','UNKNOWN','COMMITTED','REJECTED'].includes(row.status)||memory.has(row.operation_id))throw new Error('WORKFLOW_PLAN_JOURNAL_INVALID');
      if(row.status==='COMMITTED'&&(!object(row.receipt)||row.receipt.write_status!=='COMMITTED'||row.receipt.operation_id!==row.operation_id||
        row.receipt.plugin_request_sha256!==row.request_sha256||row.receipt.project_id!==config.owner_ref.project_id||!reference(row.receipt.workflow_ref)||row.receipt.workflow_ref.id!==config.plan_ref.id))throw new Error('WORKFLOW_PLAN_RECEIPT_INTEGRITY');
      memory.set(row.operation_id,row);
    }
  }catch(error){if(error.code!=='ENOENT')throw error;}
  async function persist(){
    const lock=await fs.open(filename+'.lock','wx',0o600),temporary=filename+'.'+randomUUID()+'.tmp';
    try{
      let prior=null;try{prior=digest(await read(filename));}catch(error){if(error.code!=='ENOENT')throw error;}
      if(prior!==lastHash)throw new Error('WORKFLOW_PLAN_JOURNAL_CAS_CONFLICT');
      const body={schema:journal.schema,owner_ref:journal.owner_ref,revision:journal.revision+1,operations:[...memory.values()]};
      const value={...body,sha256:digest(body)},raw=canonical(value)+'\n';
      if(Buffer.byteLength(raw)>1024*1024)throw new Error('WORKFLOW_PLAN_JOURNAL_CAPACITY');
      const file=await fs.open(temporary,'wx',0o600);try{await file.writeFile(raw);await file.sync();}finally{await file.close();}
      await fs.rename(temporary,filename);lastHash=digest(raw);journal=value;
    }finally{try{await fs.unlink(temporary);}catch{}await lock.close();await fs.unlink(filename+'.lock');}
  }
  async function transport(method,route,body){
    if(closed||closing)return {status:0,value:null};
    return new Promise(resolve=>{
      const raw=body===undefined?null:canonical(body);let done=false;
      const finish=value=>{if(!done){done=true;resolve(value);}};
      const req=http.request({hostname:'127.0.0.1',port:Number(endpoint.port),path:route,method,agent:false,
        headers:{Authorization:'Bearer '+token,...(raw===null?{}:{'Content-Type':'application/json','Content-Length':Buffer.byteLength(raw)})}},res=>{
          let size=0;const chunks=[];
          res.on('data',chunk=>{size+=chunk.length;if(size>3*1024*1024){res.destroy();finish({status:0,value:null});}else chunks.push(chunk);});
          res.on('end',()=>{let value=null;try{value=JSON.parse(Buffer.concat(chunks).toString('utf8'));}catch{}finish({status:res.statusCode,value});});
          res.on('error',()=>finish({status:0,value:null}));
          res.on('aborted',()=>finish({status:0,value:null}));
        });
      active.add(req);req.on('close',()=>active.delete(req));
      req.setTimeout(Math.min(10000,Math.max(1,timeoutMs)),()=>req.destroy(new Error('WORKFLOW_PLAN_REQUEST_TIMEOUT')));
      req.on('error',()=>finish({status:0,value:null}));if(raw!==null)req.write(raw);req.end();
    });
  }
  const queue=fn=>{if(closed||closing)return Promise.resolve({outcome:'CLOSED',automatic_execution:false});const next=chain.then(fn);chain=next.catch(()=>{});return next;};
  async function observeReply(row,response,method){
    const result=response.value;
    const committed=response.status>=200&&response.status<300&&object(result)&&result.write_status==='COMMITTED'&&
      result.operation_id===row.operation_id&&result.plugin_request_sha256===row.request_sha256&&
      result.project_id===config.owner_ref.project_id&&reference(result.workflow_ref)&&result.workflow_ref.id===config.plan_ref.id;
    if(committed){
      row.status='COMMITTED';row.receipt={operation_id:row.operation_id,project_id:result.project_id,workflow_ref:result.workflow_ref,
        write_status:'COMMITTED',core_operation_id:typeof result.core_operation_id==='string'?result.core_operation_id:null,
        plugin_request_sha256:row.request_sha256,response_sha256:digest(result),
        runtime_activation:Array.isArray(result.runtime_activation)?result.runtime_activation.filter(item=>object(item)&&
          typeof item.session_id==='string'&&Number.isSafeInteger(item.generation)&&['NEXT_DELEGATION','RECONNECT_REQUIRED','POLICY_SYNC_UNCONFIRMED'].includes(item.status))
          .map(item=>({session_id:item.session_id,generation:item.generation,status:item.status})):[]};
      let local='SAVED';row.local_persistence_status='SAVED';try{await persist();}catch{local='FAILED';row.local_persistence_status='FAILED';}
      let sync={policy_sync_status:'UNCONFIRMED'};
      try{const actual=await onCommitted(result);if(object(actual)&&['OBSERVED','UNCONFIRMED'].includes(actual.policy_sync_status))sync=actual;}catch{}
      return {outcome:'COMMITTED',...row.receipt,...sync,local_persistence_status:local,automatic_execution:false,
        message:local==='FAILED'?'计划已提交，本机回执保存未成功；请核查原操作，勿重复提交。':'计划已提交；执行与验收仍由原任务流程处理。'};
    }
    if(method==='POST'&&response.status>=400&&response.status<500&&![404,408].includes(response.status)){row.status='REJECTED';row.http_status=response.status;}
    else row.status='UNKNOWN';
    let local='SAVED';try{await persist();}catch{local='FAILED';}
    return {outcome:row.status,operation_id:row.operation_id,http_status:response.status||null,local_persistence_status:local,automatic_execution:false,
      message:row.status==='REJECTED'?'计划未被接受，请核对当前版本和人工约束。':'计划提交结果尚未确认；只查询这个原操作，不重新提交。'};
  }
  async function inspect(operationId){
    if(!OPERATION.test(operationId))throw new Error('WORKFLOW_PLAN_OPERATION_ID_INVALID');
    const row=memory.get(operationId);if(!row)return {outcome:'UNOBSERVED',operation_id:operationId,automatic_execution:false};
    if(row.status==='COMMITTED')return {outcome:'COMMITTED',...row.receipt,local_persistence_status:row.local_persistence_status??'PREVIOUS_RECEIPT',automatic_execution:false};
    if(row.status==='REJECTED')return {outcome:'REJECTED',operation_id:operationId,http_status:row.http_status,automatic_execution:false};
    return observeReply(row,await transport('GET','/api/dynamic-workflow/plugin-plan/operations/'+encodeURIComponent(operationId)),'GET');
  }
  return {
    replan:args=>queue(async()=>{
      if(!OPERATION.test(args.operation_id)||!reference(args.expected_ref)||args.expected_ref.id!==config.plan_ref.id)throw new Error('WORKFLOW_PLAN_SCOPE_INVALID');
      const body={schema_version:1,owner_ref:config.owner_ref,operation_id:args.operation_id,expected_ref:args.expected_ref,plan:args.plan,reason:args.reason},hash=digest(body);
      if(Buffer.byteLength(canonical(body))>2*1024*1024)throw new Error('WORKFLOW_PLAN_REQUEST_CAPACITY');
      const existing=memory.get(args.operation_id);
      if(existing){if(existing.request_sha256!==hash)throw new Error('WORKFLOW_PLAN_OPERATION_CONFLICT');return inspect(args.operation_id);}
      const unknown=[...memory.values()].find(row=>UNKNOWN.has(row.status));
      if(unknown)return {outcome:'UNKNOWN',operation_id:unknown.operation_id,automatic_execution:false,message:'已有计划提交结果未确认；先查询原操作，再提出新变更。'};
      if(memory.size>=128)throw new Error('WORKFLOW_PLAN_OPERATION_CAPACITY');
      const row={operation_id:args.operation_id,request_sha256:hash,status:'DISPATCHING'};memory.set(row.operation_id,row);
      try{await persist();}catch{memory.delete(row.operation_id);return {outcome:'NOT_SENT',local_persistence_status:'FAILED',automatic_execution:false,message:'提交记录未能保存，计划尚未发送。'};}
      return observeReply(row,await transport('POST','/api/dynamic-workflow/plugin-plan',body),'POST');
    }),
    inspect:id=>queue(()=>inspect(id)),
    close:async()=>{if(closed||closing)return;closing=true;for(const request of active)request.destroy();await chain;closed=true;}
  };
}
