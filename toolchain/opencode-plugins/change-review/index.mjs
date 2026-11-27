// OpenCode 1.19.0 / 733b2c6: official before/after observers; no tool or model.
import fs from 'node:fs';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {absolute,canonical,checkChain,digest,fail,identity,inside,MAX_SIDE_BYTES,mkdirOwned,
  ordinaryChain,permitted,putBlob,readJSON,relative,samePath,snapshot,utf8,validateRelative,writeOnce} from './files.mjs';
import {expectedFor,toolIntent,verifyPatch} from './intent.mjs';
import {createBudget,MAX_ACTIVE} from './budget.mjs';
import {lineageReader} from './lineage.mjs';

export const ID='eeas.change-review',VERSION='0.1.1';
const safeId=v=>typeof v==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/.test(v);
const hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const record=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const code=e=>typeof e?.code==='string'&&/^[A-Z0-9_]{1,64}$/.test(e.code)?e.code:'OBSERVATION_FAILED';
const now=()=>new Date().toISOString();
const clone=v=>JSON.parse(canonical(v));
const equal=(a,b)=>canonical(a)===canonical(b);
const ref=(r,key)=>record(r)&&safeId(r.id)&&Number.isSafeInteger(r.revision)&&r.revision>=1&&hash(r[key]);
function validateConfig(c,directory){
  if(!record(c)||c.schema_version!==1||!record(c.scope)||Object.keys(c).some(k=>!['schema_version','scope','scope_sha256','native_session_id'].includes(k)))fail('INVALID_CONFIG');
  const s=c.scope;
  if(Object.keys(s).sort().join(',')!==['project_id','session_ref','generation','workspace_ref','workspace_root','root_identity','allowed_write_paths','ledger_root','budget_root'].sort().join(',')||
    !safeId(s.project_id)||!ref(s.session_ref,'sha256')||!ref(s.workspace_ref,'binding_sha256')||!Number.isSafeInteger(s.generation)||s.generation<1||
    !record(s.root_identity)||Object.keys(s.root_identity).sort().join(',')!=='dev,ino'||!/^\d+$/.test(s.root_identity.dev)||!/^\d+$/.test(s.root_identity.ino)||
    !hash(c.scope_sha256)||digest(canonical(s))!==c.scope_sha256||c.native_session_id!==null&&!safeId(c.native_session_id))fail('INVALID_SCOPE');
  absolute(s.workspace_root);absolute(s.ledger_root);absolute(s.budget_root);
  if(!samePath(directory,s.workspace_root))fail('DIRECTORY_MISMATCH');
  if(inside(s.workspace_root,s.ledger_root)||inside(s.ledger_root,s.workspace_root))fail('LEDGER_OVERLAPS_WORKSPACE');
  if(inside(s.workspace_root,s.budget_root)||inside(s.budget_root,s.workspace_root)||inside(s.ledger_root,s.budget_root)||inside(s.budget_root,s.ledger_root))fail('BUDGET_OVERLAPS_SCOPE');
  if(s.allowed_write_paths!=='*'){
    if(!Array.isArray(s.allowed_write_paths)||!s.allowed_write_paths.length||s.allowed_write_paths.length>128)fail('INVALID_WRITE_GRANTS');
    for(const g of s.allowed_write_paths){if(typeof g!=='string')fail('INVALID_WRITE_GRANTS');validateRelative(g.endsWith('/')?g.slice(0,-1):g);}
    if(new Set(s.allowed_write_paths).size!==s.allowed_write_paths.length)fail('INVALID_WRITE_GRANTS');
  }
  ordinaryChain(s.workspace_root);ordinaryChain(s.ledger_root);ordinaryChain(s.budget_root);
  if(!equal(identity(fs.lstatSync(s.workspace_root,{bigint:true})),s.root_identity))fail('ROOT_IDENTITY_MISMATCH');
  return c;
}
export function createObserver({directory,configPath,resolveSession}){
  absolute(configPath);const configParents=ordinaryChain(path.dirname(configPath)).chain;
  const first=validateConfig(readJSON(configPath,512*1024),directory),scope=clone(first.scope),scopeSha=first.scope_sha256;
  if(inside(scope.workspace_root,configPath))fail('CONFIG_INSIDE_WORKSPACE');
  const ledger=scope.ledger_root,ledgerParents=ordinaryChain(ledger).chain,budgetParents=ordinaryChain(scope.budget_root).chain;
  const budgetIdentity=digest(canonical({project_id:scope.project_id,session_ref:scope.session_ref,workspace_ref:scope.workspace_ref}));
  const calls=mkdirOwned(ledger,'calls'),claims=mkdirOwned(scope.budget_root,'claims'),conflicts=mkdirOwned(scope.budget_root,'conflicts');mkdirOwned(ledger,'blobs');
  let native=first.native_session_id,closed=false;const active=new Map(),nonce=randomUUID();
  const storage=()=>{checkChain(ledgerParents);checkChain(budgetParents);};
  const budget=createBudget(scope.budget_root,budgetIdentity,{ledgerRoot:ledger,scopeSha});
  const save=(filename,value)=>{storage();const bytes=Buffer.from(canonical(value)+'\n');if(bytes.length>256*1024)fail('RECORD_TOO_LARGE');writeOnce(filename,bytes);};
  const ready={schema:'eeas.change-review.ready.v1',scope_sha256:scopeSha,plugin_id:ID,native_session_id:native};
  try{save(path.join(ledger,'ready.json'),ready);}catch(e){
    if(e.code!=='EEXIST')throw e;const old=readJSON(path.join(ledger,'ready.json'));
    if(old.schema!==ready.schema||old.scope_sha256!==scopeSha||old.plugin_id!==ID||old.native_session_id!==null&&old.native_session_id!==native)fail('READY_CONFLICT');
  }
  function current(){
    if(closed)fail('OBSERVER_CLOSED');checkChain(configParents);storage();
    const c=validateConfig(readJSON(configPath,512*1024),directory);
    if(c.scope_sha256!==scopeSha||!equal(c.scope,scope))fail('SCOPE_CHANGED');
    if(native!==null&&native!==c.native_session_id)fail('NATIVE_SESSION_CHANGED');
    if(c.native_session_id!==null)native=c.native_session_id;
    return c;
  }
  const originalLineage=lineageReader({scope,scopeSha,current,resolveSession});
  function saveLineage(proof){
    if(!proof)return;
    const children=mkdirOwned(ledger,'children'),filename=path.join(children,proof.reference.id+'.json'),wrapper={value:proof.value,sha256:proof.reference.sha256};
    if(Buffer.byteLength(canonical(wrapper))+1>8192)fail('CHILD_LINEAGE_TOO_LARGE');
    try{save(filename,wrapper);}catch(error){if(error.code!=='EEXIST'||!equal(readJSON(filename,8192),wrapper))fail('CHILD_LINEAGE_CHANGED');}
  }
  function base(input){return {plugin:{id:ID,version:VERSION},scope_sha256:scopeSha,project_id:scope.project_id,session_ref:scope.session_ref,
    generation:scope.generation,workspace_ref:scope.workspace_ref,native_session_id:input.sessionID,call_id:input.callID,tool:input.tool};}
  function release(call){
    for(const filename of call.claims){
      try{storage();const row=readJSON(filename,8192);if(row.nonce===nonce&&row.call_key===call.key){ordinaryChain(filename,{file:true});fs.unlinkSync(filename);}}catch{}
    }
    call.lease?.release();
  }
  function finish(call,{success=false,reason=null,status=null,output=null}={}){
    if(!active.has(call.key))return;
    const reasons=[...call.reasons];if(reason)reasons.push(reason);const after=new Map();
    if(success){
      try{
        current();
        const collisions=fs.readdirSync(conflicts);if(collisions.length>5120)fail('CONFLICT_CAPACITY');
        if(collisions.some(name=>name.includes('.'+call.key+'.')||name.endsWith('.'+call.key+'.json')))reasons.push('CONCURRENT_PATH_CLAIM');
        if(digest(canonical(call.args))!==call.args_sha256)fail('TOOL_ARGS_CHANGED');
        let bytes=call.beforeBytes;
        for(const rel of call.intent.paths){
          const before=call.before.get(rel);if(before)checkChain(before.parents);
          const snap=snapshot(scope.workspace_root,rel);bytes+=snap.value.bytes;if(bytes>512*1024)fail('CALL_TOO_LARGE');
          putBlob(ledger,snap);after.set(rel,snap);
          if(before?.value.stat&&snap.value.stat&&(before.value.stat.dev!==snap.value.stat.dev||before.value.stat.ino!==snap.value.stat.ino))reasons.push('FILE_IDENTITY_CHANGED');
        }
      }catch(e){reasons.push(code(e));}
    }
    let expected=new Map();
    if(success){try{expected=expectedFor(call.intent,call.before);}catch(e){reasons.push(code(e));}}
    const rows=call.intent.paths.map(rel=>{
      const before=call.before.get(rel),post=after.get(rel),why=[];
      if(!before||!post)why.push('SNAPSHOT_UNAVAILABLE');
      else if(!expected.has(rel)||expected.get(rel)===null&&post.value.state!=='ABSENT'||Buffer.isBuffer(expected.get(rel))&&(post.value.state!=='UTF8'||!expected.get(rel).equals(post.buffer)))why.push('INTENT_MISMATCH');
      return {path:rel,status:'PARTIAL',reason_codes:why,before:before?.value??null,after:post?.value??null};
    });
    if(success){
      const md=output?.metadata;
      if(!record(output)||typeof output.output!=='string'||typeof output.title!=='string'||!record(md))reasons.push('RETURN_SHAPE_UNVERIFIED');
      else try{
        if(call.input.tool==='write'){
          const rel=call.intent.paths[0],b=call.before.get(rel),a=after.get(rel);
          if(typeof md.filepath!=='string'||!samePath(md.filepath,path.join(scope.workspace_root,rel))||md.exists!==(b?.value.state==='UTF8'))fail('TOOL_METADATA_MISMATCH');
          if(b?.value.state==='UTF8'&&(!a||!call.permissionPatch||!verifyPatch(utf8(b.buffer),a.buffer?utf8(a.buffer):'',call.permissionPatch)))fail('WRITE_BASE_NOT_ATTESTED');
        }else if(call.input.tool==='edit'){
          const rel=call.intent.paths[0],b=call.before.get(rel),a=after.get(rel),fd=md.filediff;
          if(!record(fd)||typeof fd.file!=='string'||!samePath(fd.file,path.join(scope.workspace_root,rel))||!b||!a||!verifyPatch(b.buffer?utf8(b.buffer):'',a.buffer?utf8(a.buffer):'',fd.patch))fail('TOOL_PATCH_MISMATCH');
        }else{
          if(!Array.isArray(md.files)||md.files.length!==call.intent.operations.length)fail('TOOL_METADATA_MISMATCH');
          for(const op of call.intent.operations){
            const row=md.files.find(f=>typeof f.filePath==='string'&&samePath(f.filePath,path.join(scope.workspace_root,op.path)));
            const target=op.move??op.path,b=call.before.get(op.path),a=after.get(target);
            if(!row||row.type!==(op.move?'move':op.type)||op.move&&(!row.movePath||!samePath(row.movePath,path.join(scope.workspace_root,op.move)))||!b||!a||
              !verifyPatch(b.buffer?utf8(b.buffer):'',a.buffer?utf8(a.buffer):'',row.patch))fail('TOOL_PATCH_MISMATCH');
          }
        }
      }catch(e){reasons.push(code(e));}
    }
    const unique=[...new Set(reasons)].slice(0,32);
    for(const row of rows){row.reason_codes=[...new Set([...row.reason_codes,...unique])];row.status=success&&!row.reason_codes.length?'COMPLETE':'PARTIAL';}
    const outcome=status??(success&&rows.length&&rows.every(r=>r.status==='COMPLETE')?'COMPLETE':success?'PARTIAL':'UNKNOWN');
    const result={schema:'eeas.change-review.record.v1',...call.base,args_sha256:call.args_sha256,status:outcome,reason_codes:unique,
      tool_returned_successfully:success,started_at:call.started_at,finished_at:now(),files:rows};
    result.record_sha256=digest(canonical(result));
    try{save(path.join(call.dir,'result.json'),result);}finally{release(call);active.delete(call.key);}
  }
  async function before(input,output){
    // OpenCode 1.19.0 puts optional search args directly into JsonValue
    // permission metadata. Undefined defaults make GET /permission fail 400.
    // Spell out only the original effective defaults, for this bound owner;
    // permission prompts and explicit search paths remain entirely native.
    if(['glob','grep'].includes(input?.tool)){
      if(closed||!safeId(input?.sessionID)||!safeId(input?.callID)||!record(output?.args))return;
      let c;try{c=current();}catch{return;}
      if(c.native_session_id===null)return;
      await originalLineage(input.sessionID);
      if(output.args.path===undefined)output.args.path=scope.workspace_root;
      if(input.tool==='grep'&&output.args.include===undefined)output.args.include='';
      return;
    }
    if(!['edit','write','apply_patch'].includes(input?.tool)||!safeId(input?.sessionID)||!safeId(input?.callID)||closed)return;
    let c;try{c=current();}catch{return;}
    if(c.native_session_id===null)return;
    const lineage=await originalLineage(input.sessionID);
    const key=digest(canonical({native_session_id:input.sessionID,call_id:input.callID}));
    if(active.has(key)){active.get(key).reasons.push('REENTRANT_CALL');return;}
    if(active.size>=MAX_ACTIVE){budget.notice('ACTIVE_CAPTURE_CAPACITY');return;}
    storage();const entries=fs.readdirSync(calls,{withFileTypes:true});if(entries.length>=128)return;
    const dir=path.join(calls,key);
    let lease=null;
    if(!fs.existsSync(dir)){
      lease=budget.acquire(key);if(!lease)return;
      try{if(!budget.reserve(key)){lease.release();return;}}catch(e){lease.release();throw e;}
    }
    try{fs.mkdirSync(dir,{mode:0o700});}catch(e){
      lease?.release();if(e.code!=='EEXIST')throw e;
      // Never capture a second before or resume an interrupted write as success.
      ordinaryChain(dir);if(!fs.existsSync(path.join(dir,'result.json'))){
        const old=readJSON(path.join(dir,'before.json'));
        if(old.scope_sha256!==scopeSha||old.native_session_id!==input.sessionID||old.call_id!==input.callID)fail('CALL_CONFLICT');
        const {before_sha256,...packet}=old;if(!hash(before_sha256)||digest(canonical(packet))!==before_sha256)fail('BEFORE_HASH_MISMATCH');
        const result={schema:'eeas.change-review.record.v1',...base(input),args_sha256:old.args_sha256,status:'UNKNOWN',reason_codes:['ORPHANED_BEFORE_NO_REPLAY'],
          tool_returned_successfully:false,started_at:old.started_at,finished_at:now(),files:old.files.map(f=>({path:f.path,before:f.before,status:'PARTIAL',reason_codes:['ORPHANED_BEFORE_NO_REPLAY'],after:null}))};
        result.record_sha256=digest(canonical(result));save(path.join(dir,'result.json'),result);
      }return;
    }
    if(!lease)fail('BUDGET_UNKNOWN');
    const call={key,dir,lease,input:clone(input),base:{...base(input),...(lineage?{child_lineage_ref:lineage.reference}:{})},args:null,args_sha256:null,started_at:now(),before:new Map(),beforeBytes:0,reasons:[],claims:[],intent:{paths:[],operations:[],args:null}};
    active.set(key,call);
    try{
      saveLineage(lineage); // Covered by this call's permanent shared reservation.
      call.intent=toolIntent(input.tool,output?.args,scope.workspace_root);
      call.args=clone(output.args);call.args_sha256=digest(canonical(call.args));call.intent.args=call.args;
      // Check ALL grants before reading even the first path of a multi-file call.
      if(call.intent.paths.some(rel=>!permitted(rel,scope.allowed_write_paths)))fail('PATH_NOT_GRANTED');
      for(const rel of call.intent.paths){
        const filename=path.join(claims,digest(rel)+'.json');
        try{save(filename,{schema:'eeas.change-review.claim.v1',budget_identity_sha256:budgetIdentity,scope_sha256:scopeSha,call_key:key,nonce});call.claims.push(filename);}
        catch(e){
          if(e.code!=='EEXIST')throw e;
          const old=readJSON(filename,8192);if(old.schema!=='eeas.change-review.claim.v1'||old.budget_identity_sha256!==budgetIdentity||!hash(old.scope_sha256)||!hash(old.call_key))fail('CLAIM_CONFLICT');
          save(path.join(conflicts,digest(rel)+'.'+old.call_key+'.'+key+'.json'),{schema:'eeas.change-review.conflict.v1',scope_sha256:scopeSha,existing_call_key:old.call_key,new_call_key:key});
          call.reasons.push('CONCURRENT_PATH_CLAIM');for(const other of active.values())if(other!==call&&other.intent.paths.includes(rel))other.reasons.push('CONCURRENT_PATH_CLAIM');
        }
        const snap=snapshot(scope.workspace_root,rel);call.beforeBytes+=snap.value.bytes;if(call.beforeBytes>256*1024)fail('CALL_TOO_LARGE');putBlob(ledger,snap);call.before.set(rel,snap);
      }
      const captured={schema:'eeas.change-review.before.v1',...call.base,args_sha256:call.args_sha256,status:'BEFORE_CAPTURED',reason_codes:[...new Set(call.reasons)],started_at:call.started_at,
        files:call.intent.paths.map(rel=>({path:rel,before:call.before.get(rel)?.value??null,parents:call.before.get(rel)?.parents??[]}))};
      captured.before_sha256=digest(canonical(captured));save(path.join(dir,'before.json'),captured);
    }catch(e){call.reasons.push(code(e));finish(call,{status:'REJECTED'});}
  }
  const guarded=fn=>async(...args)=>{try{return await fn(...args);}catch{/* Observation failure must never replay or change a tool result. */}};
  return {
    'tool.execute.before':guarded(before),
    'tool.execute.after':guarded((input,output)=>{
      if(!safeId(input?.sessionID)||!safeId(input?.callID))return;
      const key=digest(canonical({native_session_id:input.sessionID,call_id:input.callID})),call=active.get(key);if(!call)return;
      if(input.tool!==call.input.tool||digest(canonical(input.args??null))!==call.args_sha256){finish(call,{status:'PARTIAL',reason:'TOOL_ARGS_CHANGED'});return;}
      finish(call,{success:true,output});
    }),
    'permission.ask':guarded(input=>{
      if(input?.type!=='edit'||!safeId(input.sessionID)||!safeId(input.callID))return;
      const call=active.get(digest(canonical({native_session_id:input.sessionID,call_id:input.callID})));
      if(call&&call.input.tool==='write'&&typeof input.metadata?.filepath==='string'&&samePath(input.metadata.filepath,path.join(scope.workspace_root,call.intent.paths[0]))&&typeof input.metadata?.diff==='string'&&Buffer.byteLength(input.metadata.diff)<=MAX_SIDE_BYTES)call.permissionPatch=input.metadata.diff;
    }),
    event:guarded(({event})=>{
      const p=event?.properties;
      if(event?.type==='permission.asked'&&p?.permission==='edit'&&safeId(p.sessionID)&&safeId(p.tool?.callID)){
        const call=active.get(digest(canonical({native_session_id:p.sessionID,call_id:p.tool.callID})));
        if(call&&call.input.tool==='write'&&typeof p.metadata?.filepath==='string'&&samePath(p.metadata.filepath,path.join(scope.workspace_root,call.intent.paths[0]))&&typeof p.metadata.diff==='string'&&Buffer.byteLength(p.metadata.diff)<=MAX_SIDE_BYTES)call.permissionPatch=p.metadata.diff;
      }
      const part=event?.type==='message.part.updated'?p?.part:null;
      if(part?.type==='tool'&&safeId(part.sessionID)&&safeId(part.callID)&&part.state?.status==='error'){
        const call=active.get(digest(canonical({native_session_id:part.sessionID,call_id:part.callID})));if(call)finish(call,{status:'FAILED',reason:'ORIGINAL_TOOL_ERROR'});
      }
      if(event?.type==='session.idle'&&safeId(p?.sessionID))for(const call of [...active.values()])if(call.input.sessionID===p.sessionID)finish(call,{reason:'IDLE_WITHOUT_SUCCESS'});
    }),
    dispose:async()=>{if(closed)return;closed=true;for(const call of [...active.values()]){try{finish(call,{reason:'OBSERVER_CLOSED_BEFORE_SUCCESS'});}catch{}}},
  };
}
export async function server(ctx){
  const resolveSession=ctx?.client?.session?.get?async(id,{signal})=>{
    const response=await ctx.client.session.get({path:{id},query:{directory:ctx.directory},signal,throwOnError:true});
    if(response?.error||!response?.data)fail('CHILD_LINEAGE_UNAVAILABLE');return response.data;
  }:undefined;
  return createObserver({directory:ctx?.directory,configPath:process.env.EEAS_CHANGE_REVIEW_CONFIG,resolveSession});
}
export default {id:ID,server};
