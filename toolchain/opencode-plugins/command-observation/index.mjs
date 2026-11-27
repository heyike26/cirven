// Observe the pinned original loop only. This module never executes a command.
import fs from 'node:fs';
import path from 'node:path';
import {canonical,digest,fail,identity,ordinaryChain,checkChain,samePath,inside,
  snapshot,putBlob,readJSON,writeOnce,mkdirOwned,validateRelative} from './files.mjs';
import {createBudget,MAX_ACTIVE} from './budget.mjs';

export const ID='eeas.command-observation',VERSION='0.1.0';
export const MAX_FILES=32,MAX_SOURCE_BYTES=1024*1024,MAX_OUTPUT_BYTES=65536,MAX_CALLS=32;
const obj=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const safe=v=>typeof v==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/.test(v);
const sha=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const eq=(a,b)=>canonical(a)===canonical(b),clone=v=>JSON.parse(canonical(v));
const now=()=>new Date().toISOString();
const reason=e=>typeof e?.code==='string'&&/^[A-Z0-9_]{1,64}$/.test(e.code)?e.code:'OBSERVATION_FAILED';
const ref=(r,key)=>obj(r)&&safe(r.id)&&Number.isSafeInteger(r.revision)&&r.revision>=1&&sha(r[key]);
function validate(c,directory){
  if(!obj(c)||Object.keys(c).sort().join(',')!=='native_session_id,schema_version,scope,scope_sha256'||c.schema_version!==1||!obj(c.scope))fail('INVALID_CONFIG');
  const s=c.scope;
  if(Object.keys(s).sort().join(',')!=='budget_root,generation,ledger_root,project_id,root_identity,session_ref,source_paths,workspace_ref,workspace_root'||
    !safe(s.project_id)||!ref(s.session_ref,'sha256')||!ref(s.workspace_ref,'binding_sha256')||!Number.isSafeInteger(s.generation)||s.generation<1||
    !obj(s.root_identity)||Object.keys(s.root_identity).sort().join(',')!=='dev,ino'||!/^\d+$/.test(s.root_identity.dev)||!/^\d+$/.test(s.root_identity.ino)||
    !sha(c.scope_sha256)||digest(canonical(s))!==c.scope_sha256||c.native_session_id!==null&&!safe(c.native_session_id))fail('INVALID_SCOPE');
  if(!Array.isArray(s.source_paths)||s.source_paths.length>MAX_FILES||new Set(s.source_paths).size!==s.source_paths.length)fail('INVALID_DECLARED_INPUTS');
  for(const rel of s.source_paths){validateRelative(rel);if(/[?*\[\]]/.test(rel)||rel.split('/').some(p=>['.git','.eeas-sdk-state','.eeas-runtime'].includes(p.toLowerCase())))fail('INVALID_DECLARED_INPUTS');}
  for(const p of [s.workspace_root,s.ledger_root,s.budget_root])ordinaryChain(p);
  if(!samePath(directory,s.workspace_root)||!eq(identity(fs.lstatSync(s.workspace_root,{bigint:true})),s.root_identity))fail('ROOT_IDENTITY_MISMATCH');
  if(inside(s.workspace_root,s.ledger_root)||inside(s.ledger_root,s.workspace_root)||inside(s.workspace_root,s.budget_root)||inside(s.budget_root,s.workspace_root)||inside(s.ledger_root,s.budget_root)||inside(s.budget_root,s.ledger_root))fail('PRIVATE_SCOPE_OVERLAP');
  return c;
}
function outputPrefix(text){
  const raw=Buffer.from(text,'utf8');let cut=Math.min(raw.length,MAX_OUTPUT_BYTES);
  while(cut>0&&cut<raw.length&&(raw[cut]&0xc0)===0x80)cut--;
  return {text:raw.subarray(0,cut).toString('utf8'),bytes:cut,sha256:digest(raw.subarray(0,cut)),capture_truncated:cut<raw.length,
    original_sha256:digest(raw),original_bytes:raw.length};
}
export function createObserver({directory,configPath}){
  const configParents=ordinaryChain(path.dirname(configPath)).chain;
  const initial=validate(readJSON(configPath,65536),directory),scope=clone(initial.scope),scopeSha=initial.scope_sha256;
  if(inside(scope.workspace_root,configPath))fail('CONFIG_INSIDE_WORKSPACE');
  const ledger=scope.ledger_root,parents=ordinaryChain(ledger).chain,budgetParents=ordinaryChain(scope.budget_root).chain;
  const calls=mkdirOwned(ledger,'calls');mkdirOwned(ledger,'blobs');
  const budget=createBudget(scope.budget_root,digest(canonical({project_id:scope.project_id,session_ref:scope.session_ref,workspace_ref:scope.workspace_ref})),{ledgerRoot:ledger,scopeSha});
  let native=initial.native_session_id,closed=false;const active=new Map();
  function storage(){checkChain(parents);checkChain(budgetParents);}
  function save(filename,value){storage();const raw=Buffer.from(canonical(value)+'\n');if(raw.length>262144)fail('RECORD_TOO_LARGE');writeOnce(filename,raw);}
  const ready={schema:'eeas.command-observation.ready.v1',scope_sha256:scopeSha,plugin_id:ID,native_session_id:native};
  try{save(path.join(ledger,'ready.json'),ready);}catch(e){if(e.code!=='EEXIST')throw e;const old=readJSON(path.join(ledger,'ready.json'));if(!eq({...old,native_session_id:null},{...ready,native_session_id:null})||old.native_session_id!==null&&old.native_session_id!==native)fail('READY_CONFLICT');}
  function current(){
    if(closed)fail('OBSERVER_CLOSED');checkChain(configParents);storage();
    const c=validate(readJSON(configPath,65536),directory);
    if(c.scope_sha256!==scopeSha||!eq(c.scope,scope)||native!==null&&c.native_session_id!==native)fail('SCOPE_CHANGED');
    if(c.native_session_id!==null)native=c.native_session_id;return c;
  }
  const base=input=>({plugin:{id:ID,version:VERSION},scope_sha256:scopeSha,project_id:scope.project_id,session_ref:scope.session_ref,
    generation:scope.generation,workspace_ref:scope.workspace_ref,native_session_id:input.sessionID,call_id:input.callID,tool:'bash'});
  function capture(){
    const rows=[];let bytes=0;
    for(const rel of scope.source_paths){
      const snap=snapshot(scope.workspace_root,rel);
      // A self-consistent new tree is still not the admitted workspace. Bind
      // the actual parent chain used for this read, not just an earlier check.
      const heldRoot=snap.parents.find(row=>samePath(row.path,scope.workspace_root));
      if(!heldRoot||!eq({dev:heldRoot.dev,ino:heldRoot.ino},scope.root_identity))fail('ROOT_IDENTITY_MISMATCH');
      bytes+=snap.value.bytes;if(bytes>MAX_SOURCE_BYTES)fail('SOURCE_BYTE_LIMIT');
      putBlob(ledger,snap);rows.push({path:rel,snapshot:snap.value});
    }
    return rows;
  }
  function finish(call,{result=null,why=null}={}){
    if(!active.has(call.key))return;
    const reasons=[...call.reasons];if(why)reasons.push(why);
    let after=null,out=null,exit=null,returned=false;
    if(result!==null){
      try{
        current();
        if(!obj(result)||!obj(result.metadata)||typeof result.output!=='string'||Buffer.byteLength(result.output)>1024*1024||typeof result.metadata.truncated!=='boolean'||
          result.metadata.exit!==null&&(!Number.isSafeInteger(result.metadata.exit)||Math.abs(result.metadata.exit)>4294967296))fail('ORIGINAL_RETURN_UNVERIFIED');
        out={...outputPrefix(result.output),original_truncated:result.metadata.truncated,original_output_path:typeof result.metadata.outputPath==='string'&&result.metadata.outputPath.length<=32768?result.metadata.outputPath:null};
        exit=result.metadata.exit;returned=true;
      }catch(e){reasons.push(reason(e));}
      if(call.pre){try{current();after=capture();}catch(e){reasons.push(reason(e));}}
    }
    if(!call.pre)reasons.push('NO_PERMISSION_AFTER_PRE_SNAPSHOT');
    if(exit===null)reasons.push('NO_INTEGER_EXIT_OBSERVED');
    const packet={schema:'eeas.command-observation.record.v1',...call.base,args_sha256:call.args_sha256,
      before_record_sha256:call.before_sha,pre_record_sha256:call.pre?.pre_sha256??null,
      status:returned&&Number.isSafeInteger(exit)?'EXITED':'UNKNOWN_OUTCOME',exit_code:exit,
      tool_returned:returned,started_at:call.started_at,finished_at:now(),workdir:call.pre?.workdir??null,
      basis_scope:'DECLARED_FILES_ONLY',all_dependencies_captured:false,
      sources:scope.source_paths.map(rel=>({path:rel,before:call.pre?.sources.find(x=>x.path===rel)?.snapshot??null,after:after?.find(x=>x.path===rel)?.snapshot??null})),
      output:out,reason_codes:[...new Set(reasons)].slice(0,32)};
    packet.record_sha256=digest(canonical(packet));
    try{save(path.join(call.dir,'result.json'),packet);}finally{call.lease.release();active.delete(call.key);}
  }
  function before(input,output){
    if(input?.tool!=='bash'||!safe(input?.sessionID)||!safe(input?.callID)||closed)return;
    const c=current();if(c.native_session_id===null||input.sessionID!==native)return;
    const key=digest(canonical({native_session_id:native,call_id:input.callID}));
    if(active.has(key)){active.get(key).reasons.push('REENTRANT_CALL');return;}
    if(active.size>=MAX_ACTIVE){budget.notice('ACTIVE_CAPTURE_CAPACITY');return;}
    storage();let count=0;const members=fs.opendirSync(calls);
    try{while(members.readSync()){if(++count>=MAX_CALLS){budget.notice('CALL_CAPACITY');return;}}}finally{members.closeSync();}
    const dir=path.join(calls,key);
    if(fs.existsSync(dir)){ordinaryChain(dir);return;} // No second observation or command replay.
    const lease=budget.acquire(key);if(!lease)return;
    try{if(!budget.reserve(key)){lease.release();return;}}catch(e){lease.release();throw e;}
    try{fs.mkdirSync(dir,{mode:0o700});}catch(e){lease.release();throw e;}
    const args=clone(output?.args??null);
    const call={key,dir,lease,base:base(input),args,args_sha256:digest(canonical(args)),started_at:now(),reasons:[],pre:null,before_sha:null};
    active.set(key,call);
    try{
      if(!obj(args)||Buffer.byteLength(canonical(args))>32768||typeof args.command!=='string'||!args.command||args.command.length>1024||Object.keys(args).some(k=>!['command','workdir','timeout','description'].includes(k))||
        args.workdir!==undefined&&typeof args.workdir!=='string'||args.timeout!==undefined&&(!Number.isSafeInteger(args.timeout)||args.timeout<=0))fail('ORIGINAL_INPUT_UNVERIFIED');
      const packet={schema:'eeas.command-observation.before.v1',...call.base,args,args_sha256:call.args_sha256,started_at:call.started_at};
      packet.before_sha256=digest(canonical(packet));call.before_sha=packet.before_sha256;save(path.join(dir,'before.json'),packet);
    }catch(e){call.reasons.push(reason(e));finish(call,{why:'BEFORE_UNAVAILABLE'});}
  }
  function shellEnv(input){
    if(closed||input?.sessionID!==native||!safe(input?.callID))return;
    const key=digest(canonical({native_session_id:native,call_id:input.callID})),call=active.get(key);if(!call)return;
    if(call.pre){call.reasons.push('REENTRANT_PRE_SNAPSHOT');return;}
    try{
      current();if(typeof input.cwd!=='string'||!inside(scope.workspace_root,input.cwd))fail('COMMAND_CWD_OUTSIDE_WORKSPACE');ordinaryChain(input.cwd);
      // This hook runs after the original permission succeeds and before spawn.
      const packet={schema:'eeas.command-observation.pre.v1',...call.base,args_sha256:call.args_sha256,before_record_sha256:call.before_sha,
        captured_at:now(),workdir:path.resolve(input.cwd),sources:capture()};
      packet.pre_sha256=digest(canonical(packet));save(path.join(call.dir,'pre.json'),packet);call.pre=packet;
    }catch(e){call.reasons.push(reason(e));}
  }
  const guarded=fn=>async(...args)=>{try{return fn(...args);}catch{/* Observing must never change or repeat an original command. */}};
  return {'tool.execute.before':guarded(before),'shell.env':guarded(shellEnv),
    'tool.execute.after':guarded((input,result)=>{
      if(input?.tool!=='bash'||input.sessionID!==native||!safe(input.callID))return;
      const call=active.get(digest(canonical({native_session_id:native,call_id:input.callID})));if(!call)return;
      if(digest(canonical(input.args??null))!==call.args_sha256){finish(call,{why:'TOOL_ARGS_CHANGED'});return;}
      finish(call,{result});
    }),event:guarded(({event})=>{
      const p=event?.properties,part=event?.type==='message.part.updated'?p?.part:null;
      if(part?.type==='tool'&&part.tool==='bash'&&part.sessionID===native&&safe(part.callID)&&part.state?.status==='error'){
        const call=active.get(digest(canonical({native_session_id:native,call_id:part.callID})));if(call)finish(call,{why:'ORIGINAL_TOOL_ERROR'});
      }
      if(event?.type==='session.idle'&&p?.sessionID===native)for(const call of [...active.values()])finish(call,{why:'IDLE_WITHOUT_AFTER'});
    }),dispose:async()=>{if(closed)return;for(const call of [...active.values()]){try{finish(call,{why:'OWNER_CLOSED_BEFORE_AFTER'});}catch{}}closed=true;}};
}
export async function server(ctx){return createObserver({directory:ctx?.directory,configPath:process.env.EEAS_COMMAND_OBSERVATION_CONFIG});}
export default {id:ID,server};
