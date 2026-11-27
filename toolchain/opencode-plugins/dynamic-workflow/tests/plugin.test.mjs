import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import plugin from '../index.mjs';
import {RUNTIME,canonical,digest,aliases,chooseAlias,validateConfig,safeEvent} from '../protocol.mjs';

const auto=()=>({model:{mode:'AUTO',value:null},depth:{mode:'AUTO',value:null}});
const owner={project_id:'project',session_ref:{id:'conversation',revision:1,sha256:'a'.repeat(64)},generation:1};
const catalog=[{id:'fast',provider_id:'wire-fast',model_id:'model-fast',variants:[null,'low','high'],read_only:true},
  {id:'deep',provider_id:'wire-deep',model_id:'model-deep',variants:[null,'high'],read_only:true}];
async function fixture(extra={}){
  const base=process.env.EEAS_WORKFLOW_TEST_ROOT??os.tmpdir();await fs.mkdir(base,{recursive:true});
  const dir=await fs.mkdtemp(path.join(base,'workflow-plugin-'));
  const config={schema:'eeas.dynamic-workflow.plugin-config.v1',runtime:RUNTIME,owner_ref:owner,catalog,nodes:[{id:'review',title:'审阅',agent_policy:auto()}],dependencies:[],plan_ref:null,
    policy_path:path.join(dir,'policy.json'),state_path:path.join(dir,'projection.json'),sink:null,...extra};
  const writePolicy=async(nodes=config.nodes,revision=1,dependencies=[])=>{
    const body={schema:'eeas.dynamic-workflow.policy.v1',owner_ref:owner,revision,plan_ref:config.plan_ref,nodes,dependencies};
    const value={...body,sha256:digest(body)};await fs.writeFile(config.policy_path,canonical(value));return value;
  };
  await writePolicy();const raw=canonical(config),configPath=path.join(dir,'config.json');await fs.writeFile(configPath,raw);
  const options={configPath,configSha256:digest(raw)},hooks=await plugin.server({},options);
  return {dir,config,options,hooks,rows:aliases(config),writePolicy,graph:async()=>JSON.parse(await hooks.tool.eeas_workflow_graph.execute({},{}))};
}

test('plugin is a single supported V1 server module, registers bounded aliases and preserves every original agent/config field',async()=>{
  assert.deepEqual(Object.keys(plugin).sort(),['id','server']);const f=await fixture();
  try{
    const native={agent:{build:{model:'original/build',permission:{write:'ask'}},plan:{mode:'primary'}},permission:{'*':'deny'},plugin:['existing']};
    const original=structuredClone(native);await f.hooks.config(native);
    assert.deepEqual(native.agent.build,original.agent.build);assert.deepEqual(native.agent.plan,original.agent.plan);
    assert.deepEqual(native.permission,original.permission);assert.deepEqual(native.plugin,original.plugin);
    for(const row of f.rows){const actual=native.agent[row.alias];assert.equal(actual.model,row.provider_id+'/'+row.model_id);assert.equal(actual.variant,row.variant??undefined);assert.equal(actual.mode,'subagent');assert.equal(actual.permission['*'],'deny');assert.equal(actual.permission.task,'deny');}
    await assert.rejects(f.hooks.config(native),/COLLISION/);
  }finally{await f.hooks.dispose();}
});

test('model and depth manual locks act independently at next native task boundary, while original task is untouched',async()=>{
  const f=await fixture();try{
    const requested=f.rows.find(row=>row.route_id==='fast'&&row.variant==='low'),args={subagent_type:requested.alias,prompt:'PRIVATE_PROMPT',description:'real task'};
    const pins={model:{mode:'MANUAL',value:'deep'},depth:{mode:'MANUAL',value:'high'}};
    await f.writePolicy([{id:'review',agent_policy:pins}],2);
    await f.hooks['tool.execute.before']({tool:'task',sessionID:'ses_parent',callID:'call_one'},{args});
    const selected=f.rows.find(row=>row.alias===args.subagent_type);assert.equal(selected.route_id,'deep');assert.equal(selected.variant,'high');assert.equal(args.prompt,'PRIVATE_PROMPT');
    const graph=await f.graph();assert.equal(graph.actual.dispatches[0].effect_status,'SELECTED_NOT_YET_OBSERVED');assert.equal(graph.actual.nodes.length,0);assert.doesNotMatch(canonical(graph),/PRIVATE_PROMPT/);
    const original={subagent_type:'explore',task_id:'ses_existing',prompt:'untouched'};await f.hooks['tool.execute.before']({tool:'task'},{args:original});assert.deepEqual(original,{subagent_type:'explore',task_id:'ses_existing',prompt:'untouched'});
    const onlyModel={model:{mode:'MANUAL',value:'deep'},depth:{mode:'AUTO',value:null}};
    assert.equal(chooseAlias(f.rows,requested.alias,onlyModel).route_id,'deep');
    const onlyDepth={model:{mode:'AUTO',value:null},depth:{mode:'MANUAL',value:'high'}};
    assert.equal(chooseAlias(f.rows,requested.alias,onlyDepth).route_id,'fast');assert.equal(chooseAlias(f.rows,requested.alias,onlyDepth).variant,'high');
    assert.throws(()=>chooseAlias(f.rows,requested.alias,{...onlyModel,depth:{mode:'MANUAL',value:'unavailable'}}),/UNAVAILABLE/);
  }finally{await f.hooks.dispose();}
});

test('policy hash/rollback/removed-node/resumed-child conflicts never dispatch an unpinned task and new node requires reconnect',async()=>{
  const f=await fixture();try{
    const row=f.rows[0],invoke=call=>f.hooks['tool.execute.before']({tool:'task',sessionID:'ses_parent',callID:call},{args:{subagent_type:row.alias}});
    await f.writePolicy([{id:'review',agent_policy:auto()},{id:'future',agent_policy:auto()}],3);await invoke('call_current');
    assert.equal((await f.graph()).plan.nodes.find(n=>n.id==='future').activation,'RECONNECT_REQUIRED');
    await assert.rejects(f.hooks['tool.execute.before']({tool:'task',sessionID:'ses_parent',callID:'call_resume'},{args:{subagent_type:row.alias,task_id:'ses_old'}}),/OWNER_CHECK/);
    await f.writePolicy(f.config.nodes,2);await assert.rejects(invoke('call_old'),/VERSION_CONFLICT/);
    await f.writePolicy([],4);await assert.rejects(invoke('call_removed'),/NODE_REMOVED/);
    await fs.writeFile(f.config.policy_path,'{}');await assert.rejects(invoke('call_corrupt'),/SOURCE_INVALID/);
    assert.equal(f.rows.length,5,'policy updates cannot pretend to register new aliases in a running original');
  }finally{await f.hooks.dispose();}
});

test('actual task attempts, lineage and model evidence remain separate; idle never becomes success; prompt/output/error text is omitted',async()=>{
  const f=await fixture();try{
    const emit=(type,properties,id='evt_'+Math.random().toString(36).slice(2))=>f.hooks.event({event:{id,type,properties}});
    await emit('session.created',{info:{id:'ses_child',parentID:'ses_root',title:'PRIVATE_TITLE'}});
    for(const callID of ['call_one','call_two'])await emit('message.part.updated',{part:{id:'part_'+callID,sessionID:'ses_root',type:'tool',tool:'task',callID,
      state:{status:'completed',input:{prompt:'PRIVATE_PROMPT',subagent_type:f.rows[0].alias},output:'PRIVATE_OUTPUT',metadata:{parentSessionId:'ses_root',sessionId:'ses_child'}}}});
    await emit('message.updated',{info:{id:'msg_child',sessionID:'ses_child',role:'assistant',providerID:'wire-deep',modelID:'model-deep',variant:'high'},parts:[{text:'PRIVATE_TEXT'}]});
    await emit('session.idle',{sessionID:'ses_child'});await emit('session.error',{sessionID:'ses_root',error:{message:'PRIVATE_ERROR'}});
    const graph=await f.graph();assert.equal(graph.actual.edges.filter(e=>e.kind==='LINEAGE').length,1);assert.equal(graph.actual.edges.filter(e=>e.kind==='DELEGATION').length,2);
    const child=graph.actual.nodes.find(node=>node.id==='ses_child');assert.equal(child.observed_status,'idle');assert.equal(child.observed_model.variant,'high');assert.equal(child.observed_model.evidence.kind,'ASSISTANT_MESSAGE');
    assert.doesNotMatch(canonical(graph),/PRIVATE_|SUCCESS|VALIDATED/);assert.equal(graph.coverage.subtree_complete,false);assert.equal(graph.effects.model_calls,0);
    await emit('message.part.updated',{part:{id:'part_conflict',sessionID:'ses_root',type:'tool',tool:'task',callID:'call_one',state:{status:'running',metadata:{parentSessionId:'ses_root',sessionId:'ses_foreign'}}}});
    assert.equal((await f.graph()).persistence_status,'FAILED');assert.equal((await f.graph()).actual.edges.find(e=>e.call_id==='call_one').target,'ses_child');
  }finally{await f.hooks.dispose();}
});

test('strong-token fixed local sink sends only allowlisted metadata, deduplicates exact source event, never redirects or proxies',async()=>{
  const requests=[];const sink=http.createServer((req,res)=>{let body='';req.on('data',chunk=>body+=chunk);req.on('end',()=>{requests.push({path:req.url,auth:req.headers.authorization,body:JSON.parse(body)});res.writeHead(204);res.end();});});
  await new Promise(resolve=>sink.listen(0,'127.0.0.1',resolve));process.env.EEAS_WORKFLOW_EVENT_TOKEN='a'.repeat(48);
  const f=await fixture({sink:{url:'http://127.0.0.1:'+sink.address().port+'/api/dynamic-workflow/plugin-events',token_env:'EEAS_WORKFLOW_EVENT_TOKEN'}});
  try{
    const event={id:'event_status',type:'session.status',properties:{sessionID:'ses_original',status:{type:'busy',attempt:99,message:'PRIVATE_RETRY_MESSAGE'}}};
    await f.hooks.event({event});await f.hooks.event({event});await f.hooks.event({event:{type:'message.updated',properties:{info:{id:'msg',sessionID:'ses_original',role:'user'},parts:[{text:'PRIVATE_INPUT'}]}}});
    assert.equal(requests.length,1);assert.equal(requests[0].auth,'Bearer '+'a'.repeat(48));assert.equal(requests[0].path,'/api/dynamic-workflow/plugin-events');
    assert.deepEqual(Object.keys(requests[0].body).sort(),['event_id','event_type','native_session_id','observed_at','owner_ref','schema_version','source_event_id','status'].sort());
    assert.doesNotMatch(canonical(requests[0].body),/PRIVATE|attempt/);assert.equal((await f.graph()).wake_status,'ACCEPTED_REOBSERVATION_HINT');
    for(const url of ['http://localhost:3000/api/dynamic-workflow/plugin-events','https://127.0.0.1:3000/api/dynamic-workflow/plugin-events','http://127.0.0.1:3000/execute','http://127.0.0.1:3000/api/dynamic-workflow/plugin-events?q=secret'])assert.throws(()=>validateConfig({...f.config,sink:{url,token_env:'EEAS_WORKFLOW_EVENT_TOKEN'}}),/SINK/);
    assert.equal(safeEvent({type:'session.error',properties:{error:{message:'secret'}}}),null);
  }finally{await f.hooks.dispose();delete process.env.EEAS_WORKFLOW_EVENT_TOKEN;await new Promise(resolve=>sink.close(resolve));}
});

test('projection is CAS protected; restart archives old observation without adopting it as current or replaying a task',async()=>{
  const f=await fixture();const original=await fs.readFile(f.config.state_path,'utf8');
  await f.hooks.dispose();const second=await plugin.server({},f.options);
  try{
    const graph=JSON.parse(await second.tool.eeas_workflow_graph.execute({},{}));assert.ok(graph.previous_epoch_ref);assert.equal(graph.actual.nodes.length,0);assert.equal(graph.coverage.gap,true);
    assert.equal(await fs.readFile(path.join(f.dir,graph.previous_epoch_ref.filename),'utf8'),original);
    await fs.writeFile(f.config.state_path,'USER_CHANGED_BYTES');
    await second.event({event:{type:'session.idle',properties:{sessionID:'ses_new'}}});assert.equal(await fs.readFile(f.config.state_path,'utf8'),'USER_CHANGED_BYTES');
    assert.equal(JSON.parse(await second.tool.eeas_workflow_graph.execute({},{})).persistence_status,'FAILED');
  }finally{await second.dispose();}
});
