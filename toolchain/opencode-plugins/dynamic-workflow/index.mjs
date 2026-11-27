import fs from 'node:fs/promises';
import {constants} from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {randomUUID} from 'node:crypto';
import {ID,VERSION,canonical,digest,validateConfig,validatePolicy,aliases,chooseAlias,safeEvent,object} from './protocol.mjs';

async function localPath(filename){
  if(typeof filename!=='string'||!path.isAbsolute(filename)||path.normalize(filename)!==filename)throw new Error('WORKFLOW_LOCAL_PATH_INVALID');
  let current=path.parse(filename).root;
  for(const part of path.relative(current,filename).split(path.sep)){
    current=path.join(current,part);
    try{const stat=await fs.lstat(current);if(stat.isSymbolicLink())throw new Error('WORKFLOW_LINK_REJECTED');}
    catch(error){if(error.code!=='ENOENT')throw error;}
  }
  return filename;
}
async function read(filename){
  await localPath(filename);const file=await fs.open(filename,constants.O_RDONLY|(constants.O_NOFOLLOW??0));
  try{const stat=await file.stat();if(!stat.isFile()||stat.size>1024*1024)throw new Error('WORKFLOW_FILE_BOUND');return await file.readFile('utf8');}finally{await file.close();}
}
async function loadPolicy(config,prior){return validatePolicy(JSON.parse(await read(config.policy_path)),config,prior);}
function safeId(value){return typeof value==='string'&&/^[A-Za-z0-9_-]{1,120}$/.test(value);}
function observedModel(info){
  if(info?.role!=='assistant'||!safeId(info.id)||!safeId(info.providerID)||typeof info.modelID!=='string'||!/^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}$/.test(info.modelID)||
    (info.variant!==undefined&&(typeof info.variant!=='string'||!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/.test(info.variant))))return null;
  return {provider_id:info.providerID,model_id:info.modelID,variant:typeof info.variant==='string'?info.variant:null,
    evidence:{kind:'ASSISTANT_MESSAGE',message_id:info.id},status:'OBSERVED'};
}

async function server(_context,options){
  if(!object(options)||Object.keys(options).sort().join(',')!=='configPath,configSha256'||!/^[a-f0-9]{64}$/.test(options.configSha256))throw new Error('WORKFLOW_OPTIONS_INVALID');
  const raw=await read(options.configPath);if(digest(raw)!==options.configSha256)throw new Error('WORKFLOW_CONFIG_HASH_MISMATCH');
  const config=validateConfig(JSON.parse(raw));
  await localPath(config.state_path);await localPath(config.policy_path);
  if(new Set([options.configPath,config.state_path,config.policy_path]).size!==3)throw new Error('WORKFLOW_PATH_COLLISION');
  const rows=aliases(config),byAlias=new Map(rows.map(row=>[row.alias,row]));
  let currentPolicy=await loadPolicy(config),disposed=false,closing=false,chain=Promise.resolve(),lastStateHash=null,revision=0;
  const liveSockets=new Set();
  let wakeChain=Promise.resolve(),wakePending=0;
  const token=config.sink?process.env.EEAS_WORKFLOW_EVENT_TOKEN:null;
  if(config.sink&&(!token||! /^[A-Za-z0-9_-]{32,256}$/.test(token)))throw new Error('WORKFLOW_EVENT_TOKEN_REQUIRED');
  const state={schema:'eeas.dynamic-workflow.projection.v1',plugin_id:ID,plugin_version:VERSION,
    observation_epoch:randomUUID(),owner_ref:config.owner_ref,config_sha256:options.configSha256,revision:0,
    plan:{plan_ref:currentPolicy.plan_ref,nodes:[],edges:[]},actual:{nodes:[],edges:[],dispatches:[]},catalog:config.catalog,
    coverage:{mode:'EVENT_OBSERVED_ONLY',historical_replay:false,subtree_complete:false,gap:true,capacity_reached:false},
    effects:{model_calls:0,execution_loop:'ORIGINAL_OPENCODE',plan_mutation_tool:config.model_replan?'MODEL_REPLAN_ONLY':'NOT_ADMITTED',new_node_activation:'SAFE_POINT_RECONNECT_REQUIRED',alias_permissions:'READ_ONLY'},
    persistence_status:'READY',wake_status:config.sink?'READY':'NOT_CONFIGURED'};
  try{
    const old=await read(config.state_path),previous=JSON.parse(old);
    if(previous.schema!=='eeas.dynamic-workflow.projection.v1'||previous.owner_ref?.project_id!==config.owner_ref.project_id||previous.owner_ref?.session_ref?.id!==config.owner_ref.session_ref.id||!safeId(previous.observation_epoch))throw new Error('WORKFLOW_STATE_OWNER_CONFLICT');
    const history=config.state_path+'.'+previous.observation_epoch+'.history.json';
    try{await fs.writeFile(history,old,{flag:'wx',mode:0o600});}catch(error){if(error.code!=='EEXIST'||await read(history)!==old)throw error;}
    state.previous_epoch_ref={filename:path.basename(history),sha256:digest(old),generation:previous.owner_ref.generation};lastStateHash=digest(old);
  }catch(error){if(error.code!=='ENOENT')throw error;}
  const policyProjection=()=>{
    state.plan={plan_ref:currentPolicy.plan_ref,policy_ref:{revision:currentPolicy.revision,sha256:currentPolicy.sha256},
      nodes:currentPolicy.nodes.map(node=>({...node,activation:config.nodes.some(original=>original.id===node.id)?'REGISTERED':'RECONNECT_REQUIRED',
        aliases:rows.filter(row=>row.node_id===node.id)})),edges:currentPolicy.dependencies.map(edge=>({...edge,kind:'DEPENDENCY',layer:'PLAN'}))};
  };
  async function save(){
    if(disposed)return;policyProjection();state.revision=++revision;state.persistence_status='READY';
    const raw=canonical(state)+'\n',temp=config.state_path+'.'+randomUUID()+'.tmp';
    const lock=await fs.open(config.state_path+'.lock','wx',0o600);
    try{
      let prior=null;try{prior=digest(await read(config.state_path));}catch(error){if(error.code!=='ENOENT')throw error;}
      if(prior!==lastStateHash)throw new Error('WORKFLOW_STATE_CAS_CONFLICT');
      const file=await fs.open(temp,'wx',0o600);try{await file.writeFile(raw);await file.sync();}finally{await file.close();}
      await fs.rename(temp,config.state_path);lastStateHash=digest(raw);state.persistence_status='READY';
    }finally{try{await fs.unlink(temp);}catch{}await lock.close();await fs.unlink(config.state_path+'.lock');}
  }
  const enqueue=fn=>{if(disposed||closing)return Promise.resolve();const next=chain.then(()=>disposed?undefined:fn());chain=next.catch(()=>{});return next;};
  function node(id){
    if(!safeId(id))return null;let entry=state.actual.nodes.find(item=>item.id===id);
    if(!entry){if(state.actual.nodes.length>=256){state.coverage.capacity_reached=true;return null;}entry={id,layer:'ACTUAL',observed_status:'UNKNOWN',observed_model:null};state.actual.nodes.push(entry);}
    return entry;
  }
  async function wake(event){
    if(!config.sink||disposed||closing)return;
    const message={schema_version:1,event_id:digest([state.observation_epoch,event]),owner_ref:config.owner_ref,
      ...event,observed_at:new Date().toISOString()};
    const endpoint=new URL(config.sink.url),body=canonical(message);
    await new Promise(resolve=>{
      let done=false,deadline;
      const finish=status=>{
        if(done)return;done=true;clearTimeout(deadline);
        if(!disposed&&!closing)state.wake_status=status;
        resolve();
      };
      const request=http.request({hostname:'127.0.0.1',port:Number(endpoint.port),path:endpoint.pathname,method:'POST',
        headers:{Authorization:'Bearer '+token,'Content-Type':'application/json','Content-Length':Buffer.byteLength(body)},agent:false},response=>{
          // The sink only acknowledges a hint. Its body is not execution
          // evidence and must never keep the original native task waiting.
          finish(response.statusCode>=200&&response.statusCode<300?'ACCEPTED_REOBSERVATION_HINT':'REJECTED');
          response.on('error',()=>{});response.destroy();request.destroy();
        });
      liveSockets.add(request);request.on('close',()=>{liveSockets.delete(request);finish('UNAVAILABLE');});
      // An absolute deadline also covers a peer that keeps a socket active.
      // setTimeout on ClientRequest is only a socket-inactivity timeout.
      deadline=setTimeout(()=>{finish('UNAVAILABLE');request.destroy();},1000);
      request.on('error',()=>finish('UNAVAILABLE'));request.end(body);
    });
  }
  function scheduleWake(event){
    if(!config.sink||disposed||closing)return;
    if(wakePending>=64){state.wake_status='CAPACITY_REACHED';return;}
    wakePending++;
    wakeChain=wakeChain.then(async()=>{
      if(disposed||closing)return;
      await wake(event);
      if(disposed||closing)return;
      await enqueue(async()=>{try{await save();}catch{state.persistence_status='FAILED';}});
    }).catch(()=>{if(!disposed&&!closing)state.wake_status='UNAVAILABLE';}).finally(()=>{wakePending--;});
  }
  const seenWake=new Set();
  async function observe(event){
    const p=event?.properties;if(!object(p))return;
    let changed=false,wakeHint=null;
    if(['session.created','session.updated'].includes(event.type)&&safeId(p.info?.id)){
      const item=node(p.info.id);if(item&&safeId(p.info.parentID)){
        const id=digest(['LINEAGE',p.info.parentID,item.id]);
        if(!state.actual.edges.some(edge=>edge.id===id)&&state.actual.edges.length<512){node(p.info.parentID);state.actual.edges.push({id,source:p.info.parentID,target:item.id,kind:'LINEAGE',layer:'ACTUAL',evidence:{kind:event.type}});changed=true;}
      }
    }
    if(event.type==='message.updated'&&safeId(p.info?.sessionID)){
      const model=observedModel(p.info),item=node(p.info.sessionID);
      if(item&&model){item.observed_model=model;changed=true;}
    }
    const part=p.part;
    if(event.type==='message.part.updated'&&part?.type==='tool'&&part.tool==='task'&&safeId(part.sessionID)&&safeId(part.callID)){
      const metadata=part.state?.metadata,target=metadata?.sessionId;
      if(safeId(target)&&metadata.parentSessionId===part.sessionID){
        const id=digest(['DELEGATION',part.sessionID,part.callID]),entry={id,source:part.sessionID,target,kind:'DELEGATION',layer:'ACTUAL',call_id:part.callID,
          observed_tool_status:['pending','running','completed','error'].includes(part.state.status)?part.state.status:'UNKNOWN',
          evidence:{kind:'TASK_METADATA',part_id:part.id??null},requested_alias:byAlias.has(part.state.input?.subagent_type)?part.state.input.subagent_type:null};
        const prior=state.actual.edges.findIndex(edge=>edge.id===id);
        if(prior>=0&&state.actual.edges[prior].target!==target)throw new Error('WORKFLOW_DELEGATION_ID_CONFLICT');
        if(prior>=0)state.actual.edges[prior]=entry;else if(state.actual.edges.length<512)state.actual.edges.push(entry);else state.coverage.capacity_reached=true;
        node(part.sessionID);node(target);changed=true;
      }
    }
    const hint=safeEvent(event);
    if(hint){
      const item=node(hint.native_session_id);if(item){item.observed_status=hint.event_type==='SESSION_STATUS'?hint.status:hint.event_type==='SESSION_IDLE'?'idle':'error';changed=true;}
      const key=digest(hint);
      if(!seenWake.has(key)&&seenWake.size<2048){seenWake.add(key);wakeHint=hint;}
    }
    if(changed)await save();
    // Persist the source observation before issuing the advisory wake. The
    // network queue is deliberately independent of task routing/plan commits.
    if(wakeHint)scheduleWake(wakeHint);
  }
  let planClient=null,modelTools={};
  if(config.model_replan){
    const [{createPlanClient},{planTools}]=await Promise.all([import('./plan-client.mjs'),import('./plan-tools.mjs')]);
    planClient=await createPlanClient({config,read,onCommitted:async result=>enqueue(async()=>{
      currentPolicy=await loadPolicy(config,currentPolicy);
      if(canonical(currentPolicy.plan_ref)!==canonical(result.workflow_ref))return {policy_sync_status:'UNCONFIRMED'};
      await save();return {policy_sync_status:'OBSERVED',new_node_activation:'SAFE_POINT_RECONNECT_REQUIRED'};
    })});
    modelTools=planTools(planClient);
  }
  await save();
  return {
    config:async native=>{
      const prior=native.agent??{};
      for(const row of rows){
        if(Object.hasOwn(prior,row.alias))throw new Error('WORKFLOW_ALIAS_COLLISION');
        const title=config.nodes.find(node=>node.id===row.node_id)?.title??row.node_id;
        prior[row.alias]={mode:'subagent',description:'EEAS planned node '+row.node_id+' ('+title+'), route '+row.route_id+', depth '+(row.variant??'default')+'. Read only; call eeas_workflow_graph for current policy.',
          model:row.provider_id+'/'+row.model_id,...(row.variant===null?{}:{variant:row.variant}),
          permission:{'*':'deny',read:'allow',glob:'allow',grep:'allow',task:'deny',todowrite:'deny',external_directory:'deny'}};
      }
      native.agent=prior;
    },
    'tool.execute.before':async(input,output)=>{
      if(input?.tool!=='task'||!byAlias.has(output?.args?.subagent_type))return;
      if(disposed||closing)throw new Error('WORKFLOW_PLUGIN_DISPOSED');
      await enqueue(async()=>{
        currentPolicy=await loadPolicy(config,currentPolicy);
        const requested=byAlias.get(output.args.subagent_type),planNode=currentPolicy.nodes.find(node=>node.id===requested.node_id);
        if(!planNode)throw new Error('WORKFLOW_NODE_REMOVED');
        if(output.args.task_id)throw new Error('WORKFLOW_RESUMED_CHILD_ROUTE_REQUIRES_OWNER_CHECK');
        const selected=chooseAlias(rows,requested.alias,planNode.agent_policy);
        if(!safeId(input.sessionID)||!safeId(input.callID))throw new Error('WORKFLOW_TASK_IDENTITY_REQUIRED');
        const id=digest([input.sessionID,input.callID]);
        const record={id,session_id:input.sessionID,call_id:input.callID,node_id:selected.node_id,requested_alias:requested.alias,
          selected_alias:selected.alias,requested_policy:planNode.agent_policy,selected:{provider_id:selected.provider_id,model_id:selected.model_id,variant:selected.variant},
          effect_status:'SELECTED_NOT_YET_OBSERVED',policy_ref:{revision:currentPolicy.revision,sha256:currentPolicy.sha256}};
        const old=state.actual.dispatches.find(item=>item.id===id);
        if(old&&canonical(old)!==canonical(record))throw new Error('WORKFLOW_CALL_ROUTE_CONFLICT');
        if(!old){if(state.actual.dispatches.length>=512)throw new Error('WORKFLOW_DISPATCH_CAPACITY');state.actual.dispatches.push(record);}
        await save();output.args.subagent_type=selected.alias;
      });
    },
    event:async({event})=>{
      try{await enqueue(()=>observe(event));}catch{state.persistence_status='FAILED';}
    },
    tool:{...modelTools,eeas_workflow_graph:{description:'Read the current EEAS plan, exact node route aliases and actual observed agent collaboration. This read-only tool never plans, runs, retries or validates a task.',args:{},
      execute:async()=>enqueue(async()=>{currentPolicy=await loadPolicy(config,currentPolicy);policyProjection();return canonical(state);})}},
    dispose:async()=>{if(disposed||closing)return;closing=true;for(const request of liveSockets)request.destroy();await planClient?.close();await wakeChain;await chain;disposed=true;state.wake_status='DISPOSED';}
  };
}

export default {id:ID,server};
