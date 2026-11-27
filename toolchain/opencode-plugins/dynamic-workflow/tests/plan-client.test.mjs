import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import {createPlanClient} from '../plan-client.mjs';
import {planTools} from '../plan-tools.mjs';
import {z} from '../vendor/zod/index.js';
import {canonical,digest} from '../protocol.mjs';

const own={project_id:'project',session_ref:{id:'owner',revision:1,sha256:'a'.repeat(64)},generation:2};
const initial={id:'workflow',revision:1,sha256:'b'.repeat(64)},next={...initial,revision:2,sha256:'c'.repeat(64)};
const args={operation_id:'original_op',expected_ref:initial,plan:{title:'Plan',goal:'Review evidence',nodes:[{id:'review',title:'Review'}],questions:[],external_refs:[]},reason:'New evidence'};
async function fixture({mode='success',syncFailure=false}={}){
  const root=process.env.EEAS_WORKFLOW_TEST_ROOT??os.tmpdir();await fs.mkdir(root,{recursive:true});const directory=await fs.mkdtemp(path.join(root,'workflow-plan-'));
  const requests=[],receipts=new Map();let behavior=mode;
  const server=http.createServer(async(req,res)=>{
    let raw='';for await(const chunk of req)raw+=chunk;const body=raw?JSON.parse(raw):null;
    requests.push({method:req.method,path:req.url,body,authorization:req.headers.authorization});
    assert.equal(req.headers.authorization,'Bearer '+'p'.repeat(48),'event token cannot grant plan access');
    if(req.method==='POST'){
      const result={write_status:'COMMITTED',operation_id:body.operation_id,core_operation_id:'internal_exact_op',plugin_request_sha256:digest(body),project_id:own.project_id,workflow_ref:next,runtime_activation:[{session_id:own.session_ref.id,generation:2,status:'NEXT_DELEGATION'}]};
      receipts.set(body.operation_id,result);
      if(behavior==='drop'){req.socket.destroy();return;}
      if(behavior==='persist-failure')await fs.writeFile(path.join(directory,'projection.json.plan-operations.json.lock'),'simulated disk obstruction');
      if(behavior==='mismatch')result.plugin_request_sha256='f'.repeat(64);
      res.writeHead(200,{'Content-Type':'application/json'});res.end(canonical(result));
    }else{
      if(behavior==='get-forbidden'){res.writeHead(403);res.end('{}');return;}
      if(behavior==='get-missing'){res.writeHead(404);res.end('{}');return;}
      const result=receipts.get(req.url.split('/').at(-1));res.writeHead(result?200:404,{'Content-Type':'application/json'});res.end(canonical(result??{}));
    }
  });await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const config={owner_ref:own,plan_ref:initial,state_path:path.join(directory,'projection.json'),model_replan:true,sink:{url:'http://127.0.0.1:'+server.address().port+'/api/dynamic-workflow/plugin-events'}};
  const read=filename=>fs.readFile(filename,'utf8');process.env.EEAS_WORKFLOW_PLAN_TOKEN='p'.repeat(48);process.env.EEAS_WORKFLOW_EVENT_TOKEN='e'.repeat(48);
  const options={config,read,onCommitted:async()=>{if(syncFailure)throw new Error('local sync unavailable');return {policy_sync_status:'OBSERVED'};}};
  const client=await createPlanClient(options);
  return {directory,config,options,client,requests,setMode:value=>behavior=value,
    close:async()=>{await client.close();await new Promise(resolve=>server.close(resolve));delete process.env.EEAS_WORKFLOW_PLAN_TOKEN;delete process.env.EEAS_WORKFLOW_EVENT_TOKEN;}};
}

test('real pinned Zod raw shape validates complete MODEL-only plan args and never accepts USER/VERIFIER/execute payload',async()=>{
  const f=await fixture();try{
    const tools=planTools(f.client),schema=z.object(tools.eeas_workflow_replan.args).strict();
    const generated=z.toJSONSchema(schema);assert.equal(generated.properties.plan.type,'object');assert.ok(generated.properties.plan.properties.nodes.items.properties.agent_policy);
    assert.deepEqual(schema.parse(args),args);
    for(const change of [{...args,actor:'USER'},{...args,plan:{...args.plan,nodes:[{...args.plan.nodes[0],status:'PASSED'}]}},{...args,plan:{...args.plan,questions:[{id:'q',text:'Question',answer:'pretend human'}]}}])await assert.rejects(tools.eeas_workflow_replan.execute(change));
    assert.equal(f.requests.length,0);
    const value=JSON.parse(await tools.eeas_workflow_replan.execute(args));assert.equal(value.outcome,'COMMITTED');assert.equal(value.automatic_execution,false);assert.equal(value.policy_sync_status,'OBSERVED');
    assert.equal(f.requests.length,1);assert.equal(f.requests[0].method,'POST');assert.equal(f.requests[0].path,'/api/dynamic-workflow/plugin-plan');
    assert.deepEqual(Object.keys(f.requests[0].body).sort(),['schema_version','owner_ref','operation_id','expected_ref','plan','reason'].sort());
    const replay=await f.client.replan(args);assert.equal(replay.outcome,'COMMITTED');assert.equal(f.requests.length,1);
    await assert.rejects(f.client.replan({...args,reason:'Different request same operation'}),/OPERATION_CONFLICT/);
  }finally{await f.close();}
});

test('uncertain write survives restart and can only GET exact original operation, never repost or accept new operation',async()=>{
  const f=await fixture({mode:'drop'});let reopened;
  try{
    const result=await f.client.replan(args);assert.equal(result.outcome,'UNKNOWN');assert.equal(f.requests.length,1);
    const blocked=await f.client.replan({...args,operation_id:'new_op'});assert.equal(blocked.operation_id,args.operation_id);assert.equal(f.requests.length,1);
    await f.client.close();reopened=await createPlanClient(f.options);f.setMode('get-forbidden');
    assert.equal((await reopened.inspect(args.operation_id)).outcome,'UNKNOWN','GET 403 cannot prove the original write was rejected');
    f.setMode('success');const observed=await reopened.replan(args);assert.equal(observed.outcome,'COMMITTED');
    assert.equal(f.requests.filter(item=>item.method==='POST').length,1);assert.equal(f.requests.filter(item=>item.method==='GET').length,2);
    assert.ok(f.requests.filter(item=>item.method==='GET').every(item=>item.path==='/api/dynamic-workflow/plugin-plan/operations/'+args.operation_id));
    await reopened.close();const before=f.requests.length;assert.equal((await reopened.replan({...args,operation_id:'closed_late'})).outcome,'CLOSED');assert.equal(f.requests.length,before);
  }finally{await reopened?.close();await f.close();}
});

test('committed plan remains known through local receipt/sync failure; pre-send persistence failure sends nothing',async()=>{
  const f=await fixture({mode:'persist-failure',syncFailure:true});let reopened;
  try{
    const value=await f.client.replan(args);assert.equal(value.outcome,'COMMITTED');assert.equal(value.local_persistence_status,'FAILED');assert.equal(value.policy_sync_status,'UNCONFIRMED');assert.match(value.message,/勿重复提交/);
    const local=await f.client.inspect(args.operation_id);assert.equal(local.outcome,'COMMITTED');assert.equal(local.local_persistence_status,'FAILED');assert.equal(f.requests.length,1);
    await f.client.close();await fs.unlink(path.join(f.directory,'projection.json.plan-operations.json.lock'));f.setMode('success');
    reopened=await createPlanClient(f.options);assert.equal((await reopened.inspect(args.operation_id)).outcome,'COMMITTED');assert.equal(f.requests.filter(r=>r.method==='POST').length,1);
    await fs.writeFile(path.join(f.directory,'projection.json.plan-operations.json.lock'),'intent-save unavailable');
    const noSend=await reopened.replan({...args,operation_id:'next_op',expected_ref:next});assert.equal(noSend.outcome,'NOT_SENT');assert.equal(f.requests.filter(r=>r.method==='POST').length,1);
  }finally{await reopened?.close();await f.close();}
});

test('wrong response checksum is UNKNOWN, 404 remains UNKNOWN, and event token alone cannot activate plan writer',async()=>{
  const f=await fixture({mode:'mismatch'});try{
    assert.equal((await f.client.replan(args)).outcome,'UNKNOWN');f.setMode('get-missing');
    assert.equal((await f.client.inspect(args.operation_id)).outcome,'UNKNOWN','missing lookup is not proof of non-execution');
    // Simulate absent lookup without issuing a second write.
    const row=JSON.parse(await fs.readFile(path.join(f.directory,'projection.json.plan-operations.json'),'utf8')).operations[0];assert.equal(row.status,'UNKNOWN');
    delete process.env.EEAS_WORKFLOW_PLAN_TOKEN;await assert.rejects(createPlanClient({...f.options,config:{...f.config,state_path:path.join(f.directory,'new.json')}}),/PLAN_TOKEN_REQUIRED/);
    assert.equal(f.requests.filter(r=>r.method==='POST').length,1);
  }finally{await f.close();}
});

test('corrupt persisted outcome cannot become a known receipt or cause a restart POST',async()=>{
  const f=await fixture();try{
    await f.client.replan(args);await f.client.close();
    const filename=path.join(f.directory,'projection.json.plan-operations.json'),value=JSON.parse(await fs.readFile(filename,'utf8'));
    value.operations[0].receipt.workflow_ref.revision=200;await fs.writeFile(filename,canonical(value));
    await assert.rejects(createPlanClient(f.options),/JOURNAL_INTEGRITY/);assert.equal(f.requests.length,1);
    const {sha256,...basis}=value;basis.operations[0].receipt.project_id='foreign';
    await fs.writeFile(filename,canonical({...basis,sha256:digest(basis)}));
    await assert.rejects(createPlanClient(f.options),/RECEIPT_INTEGRITY/);assert.equal(f.requests.length,1);
  }finally{await f.close();}
});
