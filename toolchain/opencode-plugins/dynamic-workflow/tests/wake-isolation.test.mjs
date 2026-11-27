import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import net from 'node:net';
import {pathToFileURL} from 'node:url';
import {RUNTIME,canonical,digest} from '../protocol.mjs';

const plugin=(await import(process.env.EEAS_WORKFLOW_NATIVE_TEST_BUNDLE
  ?pathToFileURL(path.join(process.env.EEAS_WORKFLOW_NATIVE_TEST_BUNDLE,'index.mjs')).href
  :new URL('../index.mjs',import.meta.url).href)).default;

const owner={project_id:'project',session_ref:{id:'session',revision:1,sha256:'a'.repeat(64)},generation:1};
const planRef={id:'workflow',revision:1,sha256:'b'.repeat(64)};
const node={id:'review',title:'Review original evidence',agent_policy:{model:{mode:'AUTO',value:null},depth:{mode:'AUTO',value:null}}};
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(check){const end=Date.now()+2500;while(!await check()){assert.ok(Date.now()<end,'Expected real callback before deadline');await sleep(10);}}
async function setup(port,{modelReplan=false}={}){
  const base=process.env.EEAS_WORKFLOW_TEST_ROOT??os.tmpdir();await fs.mkdir(base,{recursive:true});
  const dir=await fs.mkdtemp(path.join(base,'wake-isolation-'));
  const config={schema:'eeas.dynamic-workflow.plugin-config.v1',runtime:RUNTIME,owner_ref:owner,
    catalog:[{id:'route',provider_id:'wire',model_id:'actual-model',variants:[null],read_only:true}],nodes:[node],dependencies:[],
    plan_ref:planRef,policy_path:path.join(dir,'policy.json'),state_path:path.join(dir,'projection.json'),
    sink:{url:'http://127.0.0.1:'+port+'/api/dynamic-workflow/plugin-events',token_env:'EEAS_WORKFLOW_EVENT_TOKEN'},
    ...(modelReplan?{model_replan:true}:{})};
  async function policy(ref,revision){const body={schema:'eeas.dynamic-workflow.policy.v1',owner_ref:owner,revision,plan_ref:ref,nodes:[node],dependencies:[]};await fs.writeFile(config.policy_path,canonical({...body,sha256:digest(body)}));}
  await policy(planRef,1);const raw=canonical(config),configPath=path.join(dir,'config.json');await fs.writeFile(configPath,raw);
  const hooks=await plugin.server({}, {configPath,configSha256:digest(raw)});
  return {config,hooks,policy,graph:async()=>JSON.parse(await hooks.tool.eeas_workflow_graph.execute({},{})),
    emit:()=>hooks.event({event:{id:'event_busy',type:'session.status',properties:{sessionID:'native_parent',status:{type:'busy'}}}})};
}
async function serve(handler){const sockets=new Set(),server=http.createServer(handler);server.on('connection',socket=>{sockets.add(socket);socket.on('close',()=>sockets.delete(socket));});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));return {server,port:server.address().port,sockets,close:async()=>{for(const socket of sockets)socket.destroy();await new Promise(resolve=>server.close(resolve));}};}
process.env.EEAS_WORKFLOW_EVENT_TOKEN='e'.repeat(48);
process.env.EEAS_WORKFLOW_PLAN_TOKEN='p'.repeat(48);

test('stalled hint cannot block source persistence, committed plan callback or original task routing; close cancels own request',async()=>{
  let f,hints=0,posts=0;
  const sink=await serve((req,res)=>{let raw='';req.on('data',chunk=>raw+=chunk);req.on('end',async()=>{
    if(req.url==='/api/dynamic-workflow/plugin-events'){hints++;return;}
    assert.equal(req.url,'/api/dynamic-workflow/plugin-plan');assert.equal(req.headers.authorization,'Bearer '+'p'.repeat(48));posts++;
    const body=JSON.parse(raw),ref={...planRef,revision:2,sha256:'c'.repeat(64)};await f.policy(ref,2);
    const reply=canonical({operation_id:body.operation_id,project_id:owner.project_id,workflow_ref:ref,write_status:'COMMITTED',plugin_request_sha256:digest(body),runtime_activation:[]});
    res.writeHead(200,{'Content-Type':'application/json','Content-Length':Buffer.byteLength(reply)});res.end(reply);
  });});
  try{
    f=await setup(sink.port,{modelReplan:true});let sourceSaved=false;
    const emitted=f.emit().then(()=>{sourceSaved=true;});await until(()=>hints===1);
    assert.equal(sourceSaved,true,'Advisory request cannot hold source persistence/event completion');await emitted;
    const persisted=JSON.parse(await fs.readFile(f.config.state_path));assert.equal(persisted.actual.nodes[0].observed_status,'busy');
    const started=Date.now();
    const result=JSON.parse(await f.hooks.tool.eeas_workflow_replan.execute({operation_id:'exact_replan',expected_ref:planRef,
      plan:{title:'Review',goal:'Original evidence',nodes:[node],questions:[],external_refs:[]},reason:'Refine'},{}));
    assert.equal(result.outcome,'COMMITTED');assert.equal(result.policy_sync_status,'OBSERVED');assert.equal(posts,1);
    const graph=await f.graph(),args={subagent_type:graph.plan.nodes[0].aliases[0].alias};
    await f.hooks['tool.execute.before']({tool:'task',sessionID:'native_parent',callID:'call_next'},{args});
    assert.ok(Date.now()-started<800,'Advisory HTTP must not own the task/plan queue');
    await f.hooks.tool.eeas_workflow_replan.execute({operation_id:'exact_replan',expected_ref:planRef,
      plan:{title:'Review',goal:'Original evidence',nodes:[node],questions:[],external_refs:[]},reason:'Refine'},{});
    assert.equal(posts,1,'A known committed plan is never posted again');
    const closeStart=Date.now();await f.hooks.dispose();assert.ok(Date.now()-closeStart<800,'Dispose must settle own in-flight hint');
    const frozen=await fs.readFile(f.config.state_path,'utf8');await sleep(30);assert.equal(await fs.readFile(f.config.state_path,'utf8'),frozen);
  }finally{await f?.hooks.dispose();await sink.close();}
});

test('200 JSON acknowledgement and duplicate original event complete independently of response body',async()=>{
  let count=0;const sink=await serve((req,res)=>{req.resume();req.on('end',()=>{count++;res.writeHead(200,{'Content-Type':'application/json','Content-Length':100000});res.write('{"accepted":true');});});
  let f;try{f=await setup(sink.port);await f.emit();await f.emit();await until(async()=>(await f.graph()).wake_status==='ACCEPTED_REOBSERVATION_HINT');assert.equal(count,1);assert.equal((await f.graph()).actual.nodes[0].observed_status,'busy');}
  finally{await f?.hooks.dispose();await sink.close();}
});

test('absolute hint deadline closes an active slow-header socket without waiting for inactivity or blocking projection',async()=>{
  let closed=false,timer;const server=net.createServer(socket=>{socket.on('data',()=>{});socket.on('error',()=>{});socket.on('close',()=>{closed=true;clearInterval(timer);});socket.write('HTTP/1.1 200 OK\r\nX-Slow: ');timer=setInterval(()=>socket.write('x'),40);});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));let f;
  try{f=await setup(server.address().port);const started=Date.now();await f.emit();assert.equal((await f.graph()).actual.nodes[0].observed_status,'busy');await until(()=>closed);assert.ok(Date.now()-started<1800);await until(async()=>(await f.graph()).wake_status==='UNAVAILABLE');}
  finally{await f?.hooks.dispose();clearInterval(timer);await new Promise(resolve=>server.close(resolve));}
});
