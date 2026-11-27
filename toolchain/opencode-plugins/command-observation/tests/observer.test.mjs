import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {createObserver,server} from '../index.mjs';
import {canonical,digest} from '../files.mjs';
import {exercise} from './fixture.mjs';

function scene(t,{paths=['a.py']}={}){
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'command-observation-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const workspace=path.join(root,'code'),ledger=path.join(root,'private','ledger'),budget=path.join(root,'private','budget');
  for(const d of [workspace,ledger,budget])fs.mkdirSync(d,{recursive:true});
  fs.writeFileSync(path.join(workspace,'a.py'),'USER_MANUAL\noriginal\n');
  const stat=fs.lstatSync(workspace,{bigint:true}),scope={project_id:'p',session_ref:{id:'owner',revision:1,sha256:'a'.repeat(64)},generation:1,
    workspace_ref:{id:'b'.repeat(64),revision:1,binding_sha256:'c'.repeat(64)},workspace_root:workspace,
    root_identity:{dev:String(stat.dev),ino:String(stat.ino)},source_paths:paths,ledger_root:ledger,budget_root:budget};
  const config=path.join(root,'private','scope.json');fs.writeFileSync(config,canonical({schema_version:1,scope,scope_sha256:digest(canonical(scope)),native_session_id:'ses_one'}));
  return {root,workspace,ledger,config};
}
test('real subprocess nonzero then zero retains independent immutable receipts and manual source',async t=>{
  const s=scene(t),failed=await exercise(s.config,{exit:7,callID:'failure'}),passed=await exercise(s.config,{exit:0,callID:'retest'});
  assert.equal(failed.process_exit,7);assert.equal(failed.record.exit_code,7);assert.equal(passed.record.exit_code,0);
  assert.equal(failed.record.status,'EXITED');assert.notEqual(failed.record.record_sha256,passed.record.record_sha256);
  assert.equal(fs.readFileSync(path.join(s.workspace,'a.py'),'utf8'),'USER_MANUAL\noriginal\n');
  assert.match(passed.result.output,/actual-command-0/);assert.match(passed.result.output,/actual-stderr/);
});
test('permission-wait user edit is captured at shell.env, not earlier before',async t=>{
  const s=scene(t),v=await exercise(s.config,{changeBefore:'USER_DURING_PERMISSION\n'});
  assert.equal(v.pre.sources[0].snapshot.sha256,digest('USER_DURING_PERMISSION\n'));
  assert.equal(v.record.sources[0].before.sha256,v.record.sources[0].after.sha256);
});
test('actual command source mutation is visible without losing original pre bytes',async t=>{
  const s=scene(t),v=await exercise(s.config,{changeDuring:'USER_MANUAL\nupdated\n'});
  const row=v.record.sources[0];assert.notEqual(row.before.sha256,row.after.sha256);
  assert.equal(fs.readFileSync(path.join(s.ledger,row.before.blob),'utf8'),'USER_MANUAL\noriginal\n');
});
test('null exit and missing after do not become successful completion',async t=>{
  const s=scene(t),v=await exercise(s.config,{nullExit:true});assert.equal(v.record.status,'UNKNOWN_OUTCOME');assert.equal(v.record.exit_code,null);
  const second=await exercise(s.config,{callID:'orphan',after:false});assert.equal(second.record,null);
  const third=await exercise(s.config,{callID:'closed',after:false,close:true});assert.equal(third.record.status,'UNKNOWN_OUTCOME');
});
test('large UTF8 output has truthful bound and original hash',async t=>{
  const s=scene(t),v=await exercise(s.config,{large:true});assert.equal(v.record.output.capture_truncated,true);
  assert(v.record.output.bytes<=65536);assert.equal(Buffer.byteLength(v.record.output.text),v.record.output.bytes);
  assert.equal(v.record.output.original_sha256,digest(v.result.output));assert(!v.record.output.text.endsWith('\ufffd'));
});
test('same call across observer restart never replaces before/result',async t=>{
  const s=scene(t),v=await exercise(s.config),before=fs.readFileSync(path.join(v.dir,'result.json'));
  await exercise(s.config,{exit:7});assert.deepEqual(fs.readFileSync(path.join(v.dir,'result.json')),before);
});
test('foreign shell.env and source symlink cannot gain source qualification',async t=>{
  const s=scene(t),foreign=await exercise(s.config,{cwd:s.root});assert.equal(foreign.record.sources[0].before,null);
  fs.unlinkSync(path.join(s.workspace,'a.py'));fs.symlinkSync(path.join(s.root,'outside'),path.join(s.workspace,'a.py'));
  const bad=await exercise(s.config,{callID:'linked'});assert.equal(bad.pre,null);assert.equal(bad.record.sources[0].before,null);
});
test('concurrent original calls capture independent bases and duplicate before is marked',async t=>{
  const s=scene(t),h=createObserver({directory:s.workspace,configPath:s.config}),args={command:'true'},input={tool:'bash',sessionID:'ses_one',callID:'parallel'};
  await h['tool.execute.before'](input,{args});await h['tool.execute.before'](input,{args});
  await h['shell.env']({sessionID:'ses_one',callID:'parallel',cwd:s.workspace},{env:{}});
  const second={...input,callID:'second'};
  await h['tool.execute.before'](second,{args});
  fs.writeFileSync(path.join(s.workspace,'a.py'),'USER_PARALLEL_EDIT\n');
  await h['shell.env']({sessionID:'ses_one',callID:'second',cwd:s.workspace},{env:{}});
  const run=spawnSync('/bin/sh',['-c',args.command]);assert.equal(run.status,0);
  await h['tool.execute.after']({...input,args},{title:'true',output:'',metadata:{exit:0,truncated:false}});
  await h['tool.execute.after']({...second,args},{title:'true',output:'',metadata:{exit:0,truncated:false}});
  const key=digest(canonical({native_session_id:'ses_one',call_id:'parallel'})),record=JSON.parse(fs.readFileSync(path.join(s.ledger,'calls',key,'result.json')));
  assert(record.reason_codes.includes('REENTRANT_CALL'));assert.equal(record.exit_code,0);
  const secondKey=digest(canonical({native_session_id:'ses_one',call_id:'second'})),secondRecord=JSON.parse(fs.readFileSync(path.join(s.ledger,'calls',secondKey,'result.json')));
  assert.notEqual(record.sources[0].before.sha256,secondRecord.sources[0].before.sha256);
});
test('capture capacity does not start or repeat a command and keeps existing files',async t=>{
  const s=scene(t),h=createObserver({directory:s.workspace,configPath:s.config});
  for(let i=0;i<32;i++)fs.mkdirSync(path.join(s.ledger,'calls','reserved-'+i));
  await h['tool.execute.before']({tool:'bash',sessionID:'ses_one',callID:'capacity'},{args:{command:'MUST_NOT_BE_EXECUTED'}});
  const notice=JSON.parse(fs.readFileSync(path.join(s.ledger,'capacity.json'),'utf8'));
  assert.equal(notice.reason,'CALL_CAPACITY');assert.equal(fs.readdirSync(path.join(s.ledger,'calls')).length,32);
  assert.equal(fs.readFileSync(path.join(s.workspace,'a.py'),'utf8'),'USER_MANUAL\noriginal\n');
});
test('actual server entry signals ready before bind and accepts only the later exact session',async t=>{
  const s=scene(t),c=JSON.parse(fs.readFileSync(s.config,'utf8'));c.native_session_id=null;fs.writeFileSync(s.config,canonical(c));
  const old=process.env.EEAS_COMMAND_OBSERVATION_CONFIG;process.env.EEAS_COMMAND_OBSERVATION_CONFIG=s.config;
  t.after(()=>{if(old===undefined)delete process.env.EEAS_COMMAND_OBSERVATION_CONFIG;else process.env.EEAS_COMMAND_OBSERVATION_CONFIG=old;});
  const h=await server({directory:s.workspace}),input={tool:'bash',sessionID:'ses_one',callID:'bound'},args={command:'true'};
  const ready=JSON.parse(fs.readFileSync(path.join(s.ledger,'ready.json')));assert.equal(ready.native_session_id,null);
  await h['tool.execute.before'](input,{args});assert.equal(fs.readdirSync(path.join(s.ledger,'calls')).length,0);
  c.native_session_id='ses_one';fs.writeFileSync(s.config,canonical(c));
  await h['tool.execute.before'](input,{args});await h.dispose();
  assert.equal(fs.readdirSync(path.join(s.ledger,'calls')).length,1);
});
test('ordinary root exchange after current check cannot become admitted pre-source',async t=>{
  const s=scene(t),parked=path.join(s.root,'original-root'),impostor=path.join(s.root,'unadmitted-root');
  fs.mkdirSync(impostor);fs.writeFileSync(path.join(impostor,'a.py'),'UNADMITTED_TREE_MUST_NOT_BE_CAPTURED\n');
  const h=createObserver({directory:s.workspace,configPath:s.config}),args={command:'CONTROLLED_RETURN_ONLY_NO_COMMAND_EXECUTED'},input={tool:'bash',sessionID:'ses_one',callID:'root-exchange'};
  await h['tool.execute.before'](input,{args});
  const real=fs.lstatSync;let count=0,exchanged=false;
  fs.lstatSync=function(filename,...options){
    if(path.resolve(String(filename))===s.workspace&&++count===3){
      fs.renameSync(s.workspace,parked);fs.renameSync(impostor,s.workspace);exchanged=true;
    }
    return real.call(fs,filename,...options);
  };
  try{await h['shell.env']({sessionID:'ses_one',callID:'root-exchange',cwd:s.workspace},{env:{}});}
  finally{fs.lstatSync=real;if(exchanged){fs.renameSync(s.workspace,impostor);fs.renameSync(parked,s.workspace);}}
  assert(exchanged,'The actual ordinary-directory replacement must happen at the pinned boundary');
  // This is a controlled original-return boundary, not a real command run.
  await h['tool.execute.after']({...input,args},{title:args.command,output:'controlled return',metadata:{exit:0,truncated:false}});
  const key=digest(canonical({native_session_id:'ses_one',call_id:'root-exchange'})),dir=path.join(s.ledger,'calls',key),record=JSON.parse(fs.readFileSync(path.join(dir,'result.json')));
  assert(!fs.existsSync(path.join(dir,'pre.json')));
  assert(record.reason_codes.includes('ROOT_IDENTITY_MISMATCH'));
  assert.equal(record.sources[0].before,null);assert.equal(fs.readdirSync(path.join(s.ledger,'blobs')).length,0);
  assert.equal(fs.readFileSync(path.join(s.workspace,'a.py'),'utf8'),'USER_MANUAL\noriginal\n');
});
