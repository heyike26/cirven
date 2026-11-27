import {createHash} from 'node:crypto';

export const ID='eeas.dynamic-workflow';
export const VERSION='0.2.0';
export const RUNTIME={version:'1.19.0',source_commit:'733b2c627e5aec39cb33cc22373bdce33053ef2c'};
const IDENT=/^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/;
export const object=value=>!!value&&typeof value==='object'&&!Array.isArray(value);
export function canonical(value){
  if(value===null||typeof value==='boolean'||typeof value==='string')return JSON.stringify(value);
  if(typeof value==='number'&&Number.isFinite(value))return JSON.stringify(value);
  if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';
  if(object(value))return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical(value[key])).join(',')+'}';
  throw new Error('WORKFLOW_NON_JSON_VALUE');
}
export const digest=value=>createHash('sha256').update(typeof value==='string'?value:canonical(value)).digest('hex');
const keys=(value,allowed)=>object(value)&&Object.keys(value).every(key=>allowed.includes(key));
export function reference(ref){return keys(ref,['id','revision','sha256'])&&typeof ref.id==='string'&&IDENT.test(ref.id)&&Number.isSafeInteger(ref.revision)&&ref.revision>=1&&/^[a-f0-9]{64}$/.test(ref.sha256);}
export function owner(value){return keys(value,['project_id','session_ref','generation'])&&typeof value.project_id==='string'&&IDENT.test(value.project_id)&&reference(value.session_ref)&&Number.isSafeInteger(value.generation)&&value.generation>=0;}
export function policy(value){
  if(!keys(value,['model','depth'])||!['model','depth'].every(key=>keys(value[key],['mode','value'])&&
    (value[key].mode==='AUTO'?value[key].value===null:value[key].mode==='MANUAL'&&typeof value[key].value==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}$/.test(value[key].value))))throw new Error('WORKFLOW_POLICY_INVALID');
  return value;
}
export function nodes(value){
  if(!Array.isArray(value)||value.length>64)throw new Error('WORKFLOW_NODES_INVALID');
  const ids=new Set();
  for(const node of value){
    if(!keys(node,['id','title','agent_policy'])||typeof node.id!=='string'||!IDENT.test(node.id)||ids.has(node.id)||
      (node.title!==undefined&&(typeof node.title!=='string'||node.title.length>200||/[\u0000-\u001f\u007f]/.test(node.title))))throw new Error('WORKFLOW_NODE_INVALID');
    policy(node.agent_policy);ids.add(node.id);
  }
  return value;
}
export function dependencies(value,nodeRows){
  const ids=new Set(nodeRows.map(node=>node.id));const seen=new Set();
  if(!Array.isArray(value)||value.length>256)throw new Error('WORKFLOW_DEPENDENCIES_INVALID');
  for(const edge of value){
    if(!keys(edge,['source','target'])||!ids.has(edge.source)||!ids.has(edge.target)||edge.source===edge.target||seen.has(edge.source+'\0'+edge.target))throw new Error('WORKFLOW_DEPENDENCY_INVALID');
    seen.add(edge.source+'\0'+edge.target);
  }
  const visiting=new Set(),done=new Set();
  const visit=id=>{if(visiting.has(id))throw new Error('WORKFLOW_DEPENDENCY_CYCLE');if(done.has(id))return;visiting.add(id);for(const edge of value)if(edge.source===id)visit(edge.target);visiting.delete(id);done.add(id);};
  for(const id of ids)visit(id);
  return value;
}
export function catalog(value){
  if(!Array.isArray(value)||value.length>16)throw new Error('WORKFLOW_CATALOG_INVALID');
  const ids=new Set();
  for(const route of value){
    if(!keys(route,['id','provider_id','model_id','variants','read_only'])||typeof route.id!=='string'||!IDENT.test(route.id)||ids.has(route.id)||
      typeof route.provider_id!=='string'||!IDENT.test(route.provider_id)||typeof route.model_id!=='string'||!/^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}$/.test(route.model_id)||
      route.read_only!==true||!Array.isArray(route.variants)||!route.variants.length||route.variants.length>8||
      new Set(route.variants).size!==route.variants.length||route.variants.some(item=>item!==null&&(typeof item!=='string'||!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/.test(item))))throw new Error('WORKFLOW_ROUTE_INVALID');
    ids.add(route.id);
  }
  return value;
}
export function aliases(config){
  const result=[];
  for(const node of config.nodes)for(const route of config.catalog)for(const variant of route.variants){
    result.push({alias:'eeas_wf_'+digest([node.id,route.id,variant]).slice(0,24),node_id:node.id,route_id:route.id,
      provider_id:route.provider_id,model_id:route.model_id,variant,read_only:true});
  }
  if(result.length>256)throw new Error('WORKFLOW_ALIAS_CAPACITY');
  return result;
}
export function validateConfig(value){
  if(!keys(value,['schema','runtime','owner_ref','catalog','nodes','dependencies','plan_ref','policy_path','state_path','sink','model_replan'])||
    value.schema!=='eeas.dynamic-workflow.plugin-config.v1'||canonical(value.runtime)!==canonical(RUNTIME)||!owner(value.owner_ref)||
    (value.plan_ref!==null&&!reference(value.plan_ref))||typeof value.policy_path!=='string'||typeof value.state_path!=='string'||
    (value.model_replan!==undefined&&typeof value.model_replan!=='boolean'))throw new Error('WORKFLOW_CONFIG_INVALID');
  catalog(value.catalog);nodes(value.nodes);dependencies(value.dependencies,value.nodes);aliases(value);
  if(value.sink!==null){
    if(!keys(value.sink,['url','token_env'])||value.sink.token_env!=='EEAS_WORKFLOW_EVENT_TOKEN')throw new Error('WORKFLOW_SINK_INVALID');
    let url;try{url=new URL(value.sink.url);}catch{throw new Error('WORKFLOW_SINK_INVALID');}
    if(url.protocol!=='http:'||url.hostname!=='127.0.0.1'||!url.port||url.username||url.password||url.search||url.hash||url.pathname!=='/api/dynamic-workflow/plugin-events')throw new Error('WORKFLOW_SINK_INVALID');
  }
  if(value.model_replan===true&&(!value.plan_ref||!value.sink))throw new Error('WORKFLOW_MODEL_REPLAN_SCOPE_REQUIRED');
  return value;
}
export function validatePolicy(value,config,prior){
  if(!keys(value,['schema','owner_ref','revision','sha256','plan_ref','nodes','dependencies'])||value.schema!=='eeas.dynamic-workflow.policy.v1'||
    canonical(value.owner_ref)!==canonical(config.owner_ref)||!Number.isSafeInteger(value.revision)||value.revision<1||
    !/^[a-f0-9]{64}$/.test(value.sha256)||(value.plan_ref!==null&&!reference(value.plan_ref)))throw new Error('WORKFLOW_POLICY_SOURCE_INVALID');
  const {sha256,...body}=value;if(digest(body)!==sha256)throw new Error('WORKFLOW_POLICY_HASH_MISMATCH');
  nodes(value.nodes);dependencies(value.dependencies,value.nodes);
  if(prior&&(value.revision<prior.revision||(value.revision===prior.revision&&value.sha256!==prior.sha256)))throw new Error('WORKFLOW_POLICY_VERSION_CONFLICT');
  if(config.plan_ref&&value.plan_ref?.id!==config.plan_ref.id)throw new Error('WORKFLOW_PLAN_ID_CONFLICT');
  return value;
}
export function chooseAlias(rows,requested,nodePolicy){
  const original=rows.find(row=>row.alias===requested);if(!original)return null;
  policy(nodePolicy);
  const allowed=rows.filter(row=>row.node_id===original.node_id&&
    (nodePolicy.model.mode!=='MANUAL'||row.route_id===nodePolicy.model.value)&&
    (nodePolicy.depth.mode!=='MANUAL'||(row.variant??'default')===nodePolicy.depth.value));
  if(!allowed.length)throw new Error('WORKFLOW_MANUAL_ROUTE_UNAVAILABLE');
  return allowed.find(row=>row.alias===requested)??allowed.find(row=>row.variant===original.variant)??allowed[0];
}

// These are observational shapes, never provider/task completion authority.
export function safeEvent(event){
  if(!object(event)||typeof event.type!=='string'||!object(event.properties))return null;
  const p=event.properties,valid=id=>typeof id==='string'&&/^[A-Za-z0-9_-]{1,120}$/.test(id);
  const nativeId=p.sessionID??p.info?.sessionID??p.info?.id??p.part?.sessionID;
  if(!valid(nativeId))return null;
  const source=valid(event.id)?event.id:undefined;
  if(event.type==='session.status'&&['busy','idle','retry'].includes(p.status?.type))return {event_type:'SESSION_STATUS',native_session_id:nativeId,status:p.status.type,...(source?{source_event_id:source}:{})};
  if(event.type==='session.idle')return {event_type:'SESSION_IDLE',native_session_id:nativeId,...(source?{source_event_id:source}:{})};
  if(event.type==='session.error')return {event_type:'SESSION_ERROR',native_session_id:nativeId,...(source?{source_event_id:source}:{})};
  return null;
}
