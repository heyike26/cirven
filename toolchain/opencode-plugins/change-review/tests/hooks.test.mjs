import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createObserver,server} from '../index.mjs';
import {canonical,digest,identity,MAX_SIDE_BYTES,readBounded,utf8} from '../files.mjs';
import {verifyPatch} from '../intent.mjs';
import {CALL_RESERVE,RESERVATIONS,TOTAL_BYTES,EMERGENCY_BYTES,MAX_ACTIVE} from '../budget.mjs';

const tmp=process.env.EEAS_CHANGE_REVIEW_TEST_ROOT;
assert.ok(tmp&&path.isAbsolute(tmp),'Set EEAS_CHANGE_REVIEW_TEST_ROOT to an internal test directory; no new D: writes');
fs.mkdirSync(tmp,{recursive:true});
const h='a'.repeat(64);
function fixture(grants='*',native='ses_owned',resolveSession){
  const base=fs.mkdtempSync(path.join(tmp,'case-')),root=path.join(base,'workspace'),ledger=path.join(base,'private','ledger');
  fs.mkdirSync(root);fs.mkdirSync(ledger,{recursive:true});const budgetRoot=path.join(base,'private','budget');fs.mkdirSync(budgetRoot);
  const scope={project_id:'project-owned',session_ref:{id:'owner-session',revision:1,sha256:h},generation:1,
    workspace_ref:{id:'b'.repeat(64),revision:1,binding_sha256:h},workspace_root:root,root_identity:identity(fs.lstatSync(root,{bigint:true})),allowed_write_paths:grants,ledger_root:ledger,budget_root:budgetRoot};
  const config={schema_version:1,scope,scope_sha256:digest(canonical(scope)),native_session_id:native},configPath=path.join(base,'private','scope.json');
  const writeConfig=()=>{const next=configPath+'.next';fs.writeFileSync(next,canonical(config));fs.renameSync(next,configPath);};writeConfig();
  const open=()=>createObserver({directory:root,configPath,resolveSession}),observer=open();
  const key=call=>digest(canonical({native_session_id:native??config.native_session_id,call_id:call}));
  const result=call=>JSON.parse(fs.readFileSync(path.join(ledger,'calls',key(call),'result.json'),'utf8'));
  const content=side=>side.state==='ABSENT'?null:fs.readFileSync(path.join(ledger,side.blob));
  const input=(tool,call,args)=>({tool,sessionID:config.native_session_id,callID:call,...(args?{args}:{})});
  return {base,root,ledger,scope,config,configPath,writeConfig,open,observer,key,result,content,input};
}
function nextGeneration(f){
  const ledger=path.join(f.base,'private','generation-2-ledger');fs.mkdirSync(ledger);
  const scope={...f.scope,generation:2,ledger_root:ledger},config={...f.config,scope,scope_sha256:digest(canonical(scope))},configPath=path.join(f.base,'private','generation-2.json');
  fs.writeFileSync(configPath,canonical(config));
  const observer=createObserver({directory:f.root,configPath});
  return {scope,config,ledger,observer,result:id=>JSON.parse(fs.readFileSync(path.join(ledger,'calls',f.key(id),'result.json')))};
}
const patch=(before,after)=>`--- before\n+++ after\n@@ -${before?1:0},${before?before.split('\n').length-(before.endsWith('\n')?1:0):0} +${after?1:0},${after?after.split('\n').length-(after.endsWith('\n')?1:0):0} @@\n${before?before.trimEnd().split('\n').map(x=>'-'+x).join('\n')+'\n':''}${after?after.trimEnd().split('\n').map(x=>'+'+x).join('\n')+'\n':''}`;
async function edit(f,call,before,after,{args,actual=after,file='code.py',metadataPatch=patch(before,after)}={}){
  const filename=path.join(f.root,file);args??={filePath:filename,oldString:before,newString:after};
  await f.observer['tool.execute.before'](f.input('edit',call),{args});
  fs.writeFileSync(filename,actual);
  await f.observer['tool.execute.after'](f.input('edit',call,args),{title:file,output:'Edit applied successfully.',metadata:{filediff:{file:filename,patch:metadataPatch,additions:1,deletions:1},diff:metadataPatch,diagnostics:{}}});
  return f.result(call);
}
test('official-shaped edit captures immutable full UTF8 sides, hashes and exact scope',async()=>{
  const f=fixture(),old='print("旧")\n',next='print("新")\n';fs.writeFileSync(path.join(f.root,'code.py'),old);
  const r=await edit(f,'call-edit',old,next);
  assert.equal(r.status,'COMPLETE');assert.equal(r.tool_returned_successfully,true);assert.equal(r.files[0].status,'COMPLETE');
  assert.equal(f.content(r.files[0].before).toString(),old);assert.equal(f.content(r.files[0].after).toString(),next);
  assert.equal(r.files[0].before.sha256,digest(Buffer.from(old)));assert.equal(r.files[0].after.line_count,1);
  const {record_sha256,...rest}=r;assert.equal(record_sha256,digest(canonical(rest)));assert.deepEqual(r.session_ref,f.scope.session_ref);
  fs.writeFileSync(path.join(f.root,'code.py'),'later human save\n');assert.equal(f.content(r.files[0].after).toString(),next);
  assert.equal(r.native_session_id,'ses_owned');assert.ok(!('message_id' in r));await f.observer.dispose();
  const captured=JSON.parse(fs.readFileSync(path.join(f.ledger,'calls',f.key('call-edit'),'before.json'))),{before_sha256,...packet}=captured;
  assert.equal(before_sha256,digest(canonical(packet)));assert.equal(captured.files[0].parents.at(-1).path,f.root);
});
test('a verified original child shares the root capture budget and root idle cannot close its call',async()=>{
  let f;const requests=[];f=fixture('*','ses_owned',async(id,{signal})=>{requests.push(id);assert.equal(signal.aborted,false);return {id,parentID:'ses_owned',directory:f.root};});
  const filename=path.join(f.root,'code.py'),args={filePath:filename,oldString:'old\n',newString:'new\n'},native='ses_child',call='child-edit';
  fs.writeFileSync(filename,'old\n');const input={tool:'edit',sessionID:native,callID:call};
  await f.observer['tool.execute.before'](input,{args});
  const key=digest(canonical({native_session_id:native,call_id:call})),dir=path.join(f.ledger,'calls',key),before=JSON.parse(fs.readFileSync(path.join(dir,'before.json')));
  assert.equal(before.native_session_id,native);assert.match(before.child_lineage_ref.sha256,/^[a-f0-9]{64}$/);
  const proof=JSON.parse(fs.readFileSync(path.join(f.ledger,'children',before.child_lineage_ref.id+'.json')));
  assert.equal(proof.sha256,before.child_lineage_ref.sha256);assert.equal(digest(canonical(proof.value)),proof.sha256);
  assert.deepEqual(proof.value.chain,[{id:native,parent_id:'ses_owned'}]);assert.deepEqual(requests,[native]);
  await f.observer.event({event:{type:'session.idle',properties:{sessionID:'ses_owned'}}});assert.equal(fs.existsSync(path.join(dir,'result.json')),false);
  fs.writeFileSync(filename,'new\n');await f.observer['tool.execute.after']({...input,args},{title:'code.py',output:'Edited',metadata:{filediff:{file:filename,patch:patch('old\n','new\n')}}});
  const result=JSON.parse(fs.readFileSync(path.join(dir,'result.json')));assert.equal(result.status,'COMPLETE');assert.equal(result.native_session_id,native);
  assert.equal(result.child_lineage_ref.sha256,before.child_lineage_ref.sha256);assert.equal(fs.readdirSync(path.join(f.base,'private','budget','reservations')).length,1);
  assert.equal(f.content(result.files[0].before).toString(),'old\n');assert.equal(f.content(result.files[0].after).toString(),'new\n');await f.observer.dispose();
});
test('child lineage is a bounded original metadata chain and rejects cycles, foreign paths and missing SDK source',async()=>{
  for(const mode of ['cycle','foreign','missing']){let f;f=fixture('*','ses_owned',mode==='missing'?undefined:async id=>({id,parentID:mode==='cycle'?id:'ses_owned',directory:mode==='foreign'?path.dirname(f.root):f.root}));
    const args={filePath:path.join(f.root,'a.py'),content:'new\n'};await f.observer['tool.execute.before']({tool:'write',sessionID:'ses_bad',callID:'bad'},{args});
    assert.equal(fs.readdirSync(path.join(f.ledger,'calls')).length,0);assert.equal(fs.readdirSync(path.join(f.ledger,'blobs')).length,0);await f.observer.dispose();}
  let f;f=fixture('*','ses_owned',async id=>({id,parentID:id==='ses_leaf'?'ses_middle':'ses_owned',directory:f.root}));
  await f.observer['tool.execute.before']({tool:'write',sessionID:'ses_leaf',callID:'nested'},{args:{filePath:path.join(f.root,'a.py'),content:'new\n'}});
  const key=digest(canonical({native_session_id:'ses_leaf',call_id:'nested'})),before=JSON.parse(fs.readFileSync(path.join(f.ledger,'calls',key,'before.json')));
  const proof=JSON.parse(fs.readFileSync(path.join(f.ledger,'children',before.child_lineage_ref.id+'.json')));
  assert.deepEqual(proof.value.chain,[{id:'ses_leaf',parent_id:'ses_middle'},{id:'ses_middle',parent_id:'ses_owned'}]);await f.observer.dispose();
});
test('server reads only the injected original legacy SDK session GET with path, directory and deadline signal',async()=>{
  const f=fixture();await f.observer.dispose();const previous=process.env.EEAS_CHANGE_REVIEW_CONFIG,calls=[];let hooks;
  process.env.EEAS_CHANGE_REVIEW_CONFIG=f.configPath;
  try{hooks=await server({directory:f.root,client:{session:{get:async options=>{calls.push(options);return {data:{id:options.path.id,parentID:'ses_owned',directory:f.root}};}}}});
    await hooks['tool.execute.before']({tool:'write',sessionID:'ses_sdk_child',callID:'sdk-child'},{args:{filePath:path.join(f.root,'a.py'),content:'new\n'}});
    assert.equal(calls.length,1);assert.deepEqual(calls[0].path,{id:'ses_sdk_child'});assert.deepEqual(calls[0].query,{directory:f.root});
    assert.equal(calls[0].throwOnError,true);assert.ok(calls[0].signal instanceof AbortSignal);
    const key=digest(canonical({native_session_id:'ses_sdk_child',call_id:'sdk-child'}));assert.equal(JSON.parse(fs.readFileSync(path.join(f.ledger,'calls',key,'before.json'))).native_session_id,'ses_sdk_child');
  }finally{if(hooks)await hooks.dispose();if(previous===undefined)delete process.env.EEAS_CHANGE_REVIEW_CONFIG;else process.env.EEAS_CHANGE_REVIEW_CONFIG=previous;}
});
test('write new ABSENT side, BOM/CRLF bytes preserved; existing overwrite without tool base is PARTIAL',async()=>{
  const f=fixture(),filename=path.join(f.root,'new.txt'),args={filePath:filename,content:'\ufeff甲\r\n乙\r\n'};
  await f.observer['tool.execute.before'](f.input('write','new'),{args});fs.writeFileSync(filename,args.content);
  await f.observer['tool.execute.after'](f.input('write','new',args),{title:'new.txt',output:'Wrote file successfully.',metadata:{filepath:filename,exists:false}});
  const r=f.result('new');assert.equal(r.status,'COMPLETE');assert.equal(r.files[0].before.state,'ABSENT');assert.equal(r.files[0].before.sha256,null);
  assert.equal(f.content(r.files[0].after).equals(Buffer.from(args.content)),true);assert.equal(r.files[0].after.line_count,2);
  const a={filePath:filename,content:'overwritten\n'};await f.observer['tool.execute.before'](f.input('write','existing'),{args:a});fs.writeFileSync(filename,'\ufeff'+a.content);
  await f.observer['tool.execute.after'](f.input('write','existing',a),{title:'new.txt',output:'Wrote file successfully.',metadata:{filepath:filename,exists:true}});
  assert.equal(f.result('existing').status,'PARTIAL');assert.ok(f.result('existing').reason_codes.includes('WRITE_BASE_NOT_ATTESTED'));await f.observer.dispose();
});
test('literal relative edit/write resolve only the exact bound directory and preserve raw arguments',async()=>{
  for(const kind of ['edit','write'])for(const prefix of ['', './']){
    const f=fixture(),filename=path.join(f.root,'code.py'),args=kind==='edit'?{filePath:prefix+'code.py',oldString:'old\n',newString:'new\n'}:{filePath:prefix+'code.py',content:'new\n'};
    if(kind==='edit')fs.writeFileSync(filename,'old\n');
    const frozen=canonical(args),call=kind+(prefix?'dot':'plain');
    await f.observer['tool.execute.before'](f.input(kind,call),{args});
    const before=JSON.parse(fs.readFileSync(path.join(f.ledger,'calls',f.key(call),'before.json')));
    assert.equal(before.args_sha256,digest(frozen));assert.equal(before.files[0].path,'code.py');
    assert.equal(before.files[0].before.state,kind==='edit'?'UTF8':'ABSENT');
    fs.writeFileSync(filename,'new\n');
    const metadata=kind==='edit'?{filediff:{file:filename,patch:patch('old\n','new\n')}}:{filepath:filename,exists:false};
    await f.observer['tool.execute.after'](f.input(kind,call,args),{title:'code.py',output:'ok',metadata});
    assert.equal(f.result(call).status,'COMPLETE');assert.equal(canonical(args),frozen);await f.observer.dispose();
  }
  for(const filePath of ['../outside','nested/../code.py','C:code.py','././code.py','.','code.py\0','*.py']){
    const f=fixture();await f.observer['tool.execute.before'](f.input('write','bad'),{args:{filePath,content:'no'}});
    assert.equal(f.result('bad').status,'REJECTED',filePath);assert.equal(fs.readdirSync(path.join(f.ledger,'blobs')).length,0);await f.observer.dispose();
  }
});
test('write existing can attest actual before via exact official permission diff; no grant mutation',async()=>{
  const f=fixture(),filename=path.join(f.root,'a.txt');fs.writeFileSync(filename,'old\n');const args={filePath:filename,content:'new\n'};
  await f.observer['tool.execute.before'](f.input('write','w'),{args});
  const answer={status:'ask'};await f.observer['permission.ask']({type:'edit',sessionID:'ses_owned',callID:'w',metadata:{filepath:filename,diff:patch('old\n','new\n')}},answer);
  fs.writeFileSync(filename,args.content);await f.observer['tool.execute.after'](f.input('write','w',args),{title:'a',output:'Wrote file successfully.',metadata:{filepath:filename,exists:true}});
  assert.equal(f.result('w').status,'COMPLETE');assert.deepEqual(answer,{status:'ask'});await f.observer.dispose();
});
test('manual edits mixed after before/after, stale tool diff or formatter extras never attribute COMPLETE',async()=>{
  for(const mode of ['manual-after','manual-base']){
    const f=fixture(),filename=path.join(f.root,'code.py');fs.writeFileSync(filename,'old\n');
    const r=await edit(f,mode,'old\n','new\n',mode==='manual-after'?{actual:'new\nhuman extra\n'}:{metadataPatch:patch('human old\n','new\n')});
    assert.equal(r.status,'PARTIAL');assert.equal(r.files[0].status,'PARTIAL');await f.observer.dispose();
  }
});
test('all grants checked before reads: exact file != directory, subtree only with slash and explicit star list',async()=>{
  for(const grants of [['src/a.py'],['src/'],['*']]){
    const f=fixture(grants);fs.mkdirSync(path.join(f.root,'src'));const filename=path.join(f.root,'src','b.py'),args={filePath:filename,content:'x\n'};
    await f.observer['tool.execute.before'](f.input('write','g'),{args});
    if(grants[0]==='src/a.py'){assert.equal(f.result('g').status,'REJECTED');assert.ok(f.result('g').reason_codes.includes('PATH_NOT_GRANTED'));assert.equal(fs.readdirSync(path.join(f.ledger,'blobs')).length,0);}
    else {fs.writeFileSync(filename,args.content);await f.observer['tool.execute.after'](f.input('write','g',args),{title:'b',output:'ok',metadata:{filepath:filename,exists:false}});assert.equal(f.result('g').status,'COMPLETE');}
    await f.observer.dispose();
  }
});
test('outside root, symlink/hardlink, binary/NUL and >256KiB cannot be collected',async()=>{
  for(const kind of ['outside','symlink','hardlink','binary','invalidutf8','large','nul']){
    const f=fixture(),filename=path.join(f.root,'a'),outside=path.join(f.base,'outside');fs.writeFileSync(outside,'private original\n');
    if(kind==='symlink')fs.symlinkSync(outside,filename);
    if(kind==='hardlink')fs.linkSync(outside,filename);
    if(kind==='binary')fs.writeFileSync(filename,Buffer.from([0,1,2]));
    if(kind==='invalidutf8')fs.writeFileSync(filename,Buffer.from([255,254]));
    if(kind==='large')fs.writeFileSync(filename,Buffer.alloc(MAX_SIDE_BYTES+1,65));
    const args={filePath:kind==='outside'?outside:kind==='nul'?filename+'\0':filename,content:'x\n'};
    await f.observer['tool.execute.before'](f.input('write',kind),{args});const r=f.result(kind);assert.equal(r.status,'REJECTED',kind);
    assert.equal(fs.readFileSync(outside,'utf8'),'private original\n');assert.equal(fs.readdirSync(path.join(f.ledger,'blobs')).length,0,kind);await f.observer.dispose();
  }
});
test('ancestor identity replacement and atomic file replacement are PARTIAL, never follow a link',async()=>{
  for(const kind of ['ancestor','file']){
    const f=fixture(),dir=path.join(f.root,'src');fs.mkdirSync(dir);const filename=path.join(dir,'x');fs.writeFileSync(filename,'old\n');
    const args={filePath:filename,oldString:'old\n',newString:'new\n'};await f.observer['tool.execute.before'](f.input('edit',kind),{args});
    if(kind==='ancestor'){fs.renameSync(dir,dir+'-old');fs.mkdirSync(dir);fs.writeFileSync(filename,'new\n');}
    else {fs.writeFileSync(filename+'.swap','new\n');fs.renameSync(filename+'.swap',filename);}
    await f.observer['tool.execute.after'](f.input('edit',kind,args),{title:'x',output:'ok',metadata:{filediff:{file:filename,patch:patch('old\n','new\n')}}});
    const r=f.result(kind);assert.equal(r.status,'PARTIAL');assert.ok(r.reason_codes.includes(kind==='ancestor'?'ANCESTOR_CHANGED':'FILE_IDENTITY_CHANGED'));await f.observer.dispose();
  }
});
test('DISARMED loader ready, exact one session bind, no ambient/cross-session capture',async()=>{
  const f=fixture('*',null);assert.equal(JSON.parse(fs.readFileSync(path.join(f.ledger,'ready.json'))).native_session_id,null);
  const filename=path.join(f.root,'x'),args={filePath:filename,content:'x\n'};
  await f.observer['tool.execute.before']({tool:'write',sessionID:'other',callID:'no'},{args});assert.equal(fs.readdirSync(path.join(f.ledger,'calls')).length,0);
  f.config.native_session_id='ses_owned';f.writeConfig();await f.observer['tool.execute.before'](f.input('write','ok'),{args});fs.writeFileSync(filename,args.content);
  await f.observer['tool.execute.after'](f.input('write','ok',args),{title:'x',output:'ok',metadata:{filepath:filename,exists:false}});assert.equal(f.result('ok').status,'COMPLETE');
  f.config.native_session_id='ses_other';f.writeConfig();await f.observer['tool.execute.before'](f.input('write','no2'),{args});assert.equal(fs.readdirSync(path.join(f.ledger,'calls')).length,1);await f.observer.dispose();
});
test('scope/generation changes while tool runs retain before but cannot read after or COMPLETE',async()=>{
  const f=fixture(),filename=path.join(f.root,'x'),args={filePath:filename,content:'x\n'};
  await f.observer['tool.execute.before'](f.input('write','oldscope'),{args});f.config.scope.generation=2;f.config.scope_sha256=digest(canonical(f.config.scope));f.writeConfig();fs.writeFileSync(filename,args.content);
  await f.observer['tool.execute.after'](f.input('write','oldscope',args),{title:'x',output:'ok',metadata:{filepath:filename,exists:false}});
  const r=f.result('oldscope');assert.equal(r.status,'PARTIAL');assert.equal(r.files[0].after,null);assert.ok(r.reason_codes.includes('SCOPE_CHANGED'));await f.observer.dispose();
});
test('same call reentry and concurrent same path are PARTIAL; existing snapshots never overwritten',async()=>{
  const f=fixture(),filename=path.join(f.root,'x'),args={filePath:filename,content:'x\n'};
  await f.observer['tool.execute.before'](f.input('write','one'),{args});await f.observer['tool.execute.before'](f.input('write','one'),{args});await f.observer['tool.execute.before'](f.input('write','two'),{args});
  fs.writeFileSync(filename,args.content);
  for(const id of ['one','two'])await f.observer['tool.execute.after'](f.input('write',id,args),{title:'x',output:'ok',metadata:{filepath:filename,exists:false}});
  const original=fs.readFileSync(path.join(f.ledger,'calls',f.key('one'),'result.json'));
  assert.equal(f.result('one').status,'PARTIAL');assert.equal(f.result('two').status,'PARTIAL');
  await f.observer['tool.execute.before'](f.input('write','one'),{args});await f.observer['tool.execute.after'](f.input('write','one',args),{});
  assert.deepEqual(fs.readFileSync(path.join(f.ledger,'calls',f.key('one'),'result.json')),original);await f.observer.dispose();
});
test('original tool error, dispose, idle and process restart never fabricate successful after',async()=>{
  for(const kind of ['error','dispose','idle','restart']){
    const f=fixture(),filename=path.join(f.root,'x'),args={filePath:filename,content:'x\n'};await f.observer['tool.execute.before'](f.input('write','unfinished'),{args});
    if(kind==='error')await f.observer.event({event:{type:'message.part.updated',properties:{part:{type:'tool',sessionID:'ses_owned',callID:'unfinished',state:{status:'error'}}}}});
    if(kind==='idle')await f.observer.event({event:{type:'session.idle',properties:{sessionID:'ses_owned'}}});
    if(kind==='dispose')await f.observer.dispose();
    if(kind==='restart'){const next=f.open();await next['tool.execute.before'](f.input('write','unfinished'),{args});await next.dispose();}
    const r=f.result('unfinished');assert.equal(r.status,kind==='error'?'FAILED':'UNKNOWN');assert.equal(r.tool_returned_successfully,false);assert.equal(r.files[0].after,null);
    await f.observer.dispose();
  }
});
test('multi-file apply_patch add/update/delete/move supports exact metadata and preserves every side',async()=>{
  const f=fixture();fs.writeFileSync(path.join(f.root,'u'),'old\n');fs.writeFileSync(path.join(f.root,'d'),'delete\n');fs.writeFileSync(path.join(f.root,'m'),'move\n');
  const args={patchText:'*** Begin Patch\n*** Add File: a\n+added\n*** Update File: u\n@@\n-old\n+new\n*** Delete File: d\n*** Update File: m\n*** Move to: moved\n@@\n-move\n+changed\n*** End Patch'};
  await f.observer['tool.execute.before'](f.input('apply_patch','patch'),{args});
  fs.writeFileSync(path.join(f.root,'a'),'added\n');fs.writeFileSync(path.join(f.root,'u'),'new\n');fs.unlinkSync(path.join(f.root,'d'));fs.writeFileSync(path.join(f.root,'moved'),'changed\n');fs.unlinkSync(path.join(f.root,'m'));
  const files=[['a','add','','added\n'],['u','update','old\n','new\n'],['d','delete','delete\n',''],['m','move','move\n','changed\n']].map(([file,type,old,next])=>({filePath:path.join(f.root,file),relativePath:file,type,patch:patch(old,next),additions:1,deletions:1,...(type==='move'?{movePath:path.join(f.root,'moved')}:{})}));
  await f.observer['tool.execute.after'](f.input('apply_patch','patch',args),{title:'Success. Updated the following files:',output:'Success. Updated the following files:',metadata:{files,diff:'',diagnostics:{}}});
  const r=f.result('patch');assert.equal(r.status,'COMPLETE',JSON.stringify(r));assert.equal(r.files.length,5);assert.equal(r.files.find(x=>x.path==='d').after.state,'ABSENT');assert.equal(r.files.find(x=>x.path==='moved').before.state,'ABSENT');await f.observer.dispose();
});
test('absolute and dot patch headers preserve exact Add Update Delete Move snapshots and raw args',async()=>{
  for(const style of ['absolute','dot']){
    const f=fixture(),head=file=>style==='absolute'?path.join(f.root,file):'./'+file;
    for(const file of ['update.txt','delete.txt','move.txt'])fs.writeFileSync(path.join(f.root,file),'old\n');
    const args={patchText:`*** Begin Patch\n*** Add File: ${head('add.txt')}\n+new\n*** Update File: ${head('update.txt')}\n@@\n-old\n+new\n*** Delete File: ${head('delete.txt')}\n*** Update File: ${head('move.txt')}\n*** Move to: ${head('moved.txt')}\n@@\n-old\n+new\n*** End Patch`},frozen=canonical(args),call='paths-'+style;
    await f.observer['tool.execute.before'](f.input('apply_patch',call),{args});
    const captured=JSON.parse(fs.readFileSync(path.join(f.ledger,'calls',f.key(call),'before.json')));
    assert.equal(captured.args_sha256,digest(frozen));assert.deepEqual(captured.files.map(x=>x.path),['add.txt','update.txt','delete.txt','move.txt','moved.txt']);
    fs.writeFileSync(path.join(f.root,'add.txt'),'new\n');fs.writeFileSync(path.join(f.root,'update.txt'),'new\n');fs.unlinkSync(path.join(f.root,'delete.txt'));fs.unlinkSync(path.join(f.root,'move.txt'));fs.writeFileSync(path.join(f.root,'moved.txt'),'new\n');
    const files=[['add.txt','add'],['update.txt','update'],['delete.txt','delete'],['move.txt','move']].map(([file,type])=>({filePath:path.join(f.root,file),relativePath:type==='move'?'moved.txt':file,type,patch:patch(type==='add'?'':'old\n',type==='delete'?'':'new\n'),additions:type==='delete'?0:1,deletions:type==='add'?0:1,...(type==='move'?{movePath:path.join(f.root,'moved.txt')}:{})}));
    await f.observer['tool.execute.after'](f.input('apply_patch',call,args),{title:'4 files',output:'Success',metadata:{diff:files.map(x=>x.patch).join('\n'),files}});
    const result=f.result(call);assert.equal(result.status,'COMPLETE');assert.equal(result.files.length,5);assert.equal(canonical(args),frozen);
    assert.equal(result.files.find(x=>x.path==='add.txt').before.state,'ABSENT');assert.equal(result.files.find(x=>x.path==='moved.txt').before.state,'ABSENT');assert.equal(result.files.find(x=>x.path==='delete.txt').after.state,'ABSENT');assert.equal(f.content(result.files.find(x=>x.path==='update.txt').after).toString(),'new\n');await f.observer.dispose();
  }
});
test('absolute patch aliases, traversal, wildcards and equivalent duplicate targets are rejected before blobs',async()=>{
  for(const kind of ['absolute-dot','traversal','wildcard','outside','duplicate']){
    const f=fixture(),file=path.join(f.root,'a.txt');fs.writeFileSync(file,'old\n');const parsed=path.parse(file);
    const raw=kind==='absolute-dot'?parsed.root+'.'+path.sep+file.slice(parsed.root.length):kind==='traversal'?f.root+path.sep+'nested'+path.sep+'..'+path.sep+'a.txt':kind==='wildcard'?path.join(f.root,'*.txt'):kind==='outside'?path.join(f.base,'outside.txt'):file;
    const args={patchText:`*** Begin Patch\n*** Update File: ${raw}\n@@\n-old\n+new\n${kind==='duplicate'?'*** Update File: ./a.txt\n@@\n-old\n+new\n':''}*** End Patch`};
    await f.observer['tool.execute.before'](f.input('apply_patch',kind),{args});
    assert.equal(f.result(kind).status,'REJECTED',kind);assert.equal(fs.readdirSync(path.join(f.ledger,'blobs')).length,0,kind);assert.equal(fs.readFileSync(file,'utf8'),'old\n');await f.observer.dispose();
  }
});
test('multi-file scope violation reads NONE, malformed/fuzzy patch remains explicit and non-throwing',async()=>{
  const f=fixture(['ok']);fs.writeFileSync(path.join(f.root,'ok'),'old\n');
  const args={patchText:'*** Begin Patch\n*** Update File: ok\n@@\n-old\n+new\n*** Add File: forbidden\n+private\n*** End Patch'};
  await f.observer['tool.execute.before'](f.input('apply_patch','no'),{args});assert.equal(f.result('no').status,'REJECTED');assert.equal(fs.readdirSync(path.join(f.ledger,'blobs')).length,0);
  await f.observer.dispose();
});
test('persistent full reservations refuse new capture without deleting history or changing tool results',async()=>{
  const f=fixture(),reservations=path.join(f.scope.budget_root,'reservations');
  assert.equal(TOTAL_BYTES,96*1024*1024);assert.equal(EMERGENCY_BYTES,1024*1024);assert.equal(RESERVATIONS,89);
  assert.ok(RESERVATIONS*CALL_RESERVE<=TOTAL_BYTES-EMERGENCY_BYTES);
  const budgetIdentity=digest(canonical({project_id:f.scope.project_id,session_ref:f.scope.session_ref,workspace_ref:f.scope.workspace_ref}));
  for(let slot=0;slot<RESERVATIONS;slot++){
    const call_key=digest('retained-'+slot),scope_sha256=f.config.scope_sha256;
    fs.writeFileSync(path.join(reservations,String(slot).padStart(4,'0')+'.json'),canonical({schema:'eeas.change-review.reservation.v1',budget_identity_sha256:budgetIdentity,scope_sha256,slot,call_key,reservation_key:digest(canonical({scope_sha256,call_key})),reserved_bytes:CALL_RESERVE}));
  }
  const filenames=fs.readdirSync(reservations),filename=path.join(f.root,'x'),args={filePath:filename,content:'original tool still runs\n'};
  await f.observer['tool.execute.before'](f.input('write','over-budget'),{args});
  fs.writeFileSync(filename,args.content);await f.observer['tool.execute.after'](f.input('write','over-budget',args),{title:'x',output:'ok',metadata:{filepath:filename,exists:false}});
  assert.deepEqual(fs.readdirSync(reservations),filenames);assert.equal(fs.readdirSync(path.join(f.ledger,'calls')).length,0);assert.equal(fs.readdirSync(path.join(f.ledger,'blobs')).length,0);
  assert.equal(JSON.parse(fs.readFileSync(path.join(f.ledger,'capacity.json'))).reason,'LEDGER_BUDGET_EXHAUSTED');assert.equal(fs.readFileSync(filename,'utf8'),args.content);
  await f.observer.dispose();const restarted=f.open();await restarted['tool.execute.before'](f.input('write','still-full'),{args});assert.equal(fs.readdirSync(path.join(f.ledger,'calls')).length,0);await restarted.dispose();
});
test('active capture and unknown quota preserve failed/UNKNOWN evidence without automatic GC',async()=>{
  const f=fixture();
  for(let i=0;i<MAX_ACTIVE;i++)await f.observer['tool.execute.before'](f.input('write','pending-'+i),{args:{filePath:path.join(f.root,'f'+i),content:'x\n'}});
  await f.observer['tool.execute.before'](f.input('write','ninth'),{args:{filePath:path.join(f.root,'f9'),content:'x\n'}});
  assert.equal(fs.readdirSync(path.join(f.ledger,'calls')).length,MAX_ACTIVE);assert.equal(JSON.parse(fs.readFileSync(path.join(f.ledger,'capacity.json'))).reason,'ACTIVE_CAPTURE_CAPACITY');
  await f.observer.dispose();assert.equal(f.result('pending-0').status,'UNKNOWN');assert.equal(fs.readdirSync(path.join(f.scope.budget_root,'reservations')).length,MAX_ACTIVE);
  const quotaPath=path.join(f.scope.budget_root,'budget.json'),quota=JSON.parse(fs.readFileSync(quotaPath));quota.total_bytes++;fs.writeFileSync(quotaPath,canonical(quota));
  assert.throws(()=>f.open(),e=>e.code==='BUDGET_CHANGED');
});
test('all 128 actual workspace grants are accepted; 129 are rejected without widening',async()=>{
  const paths=Array.from({length:128},(_,i)=>'file-'+i),f=fixture(paths);await f.observer.dispose();
  f.config.scope.allowed_write_paths.push('file-128');f.config.scope_sha256=digest(canonical(f.config.scope));f.writeConfig();assert.throws(()=>f.open(),e=>e.code==='INVALID_WRITE_GRANTS');
});
test('generations share permanent quota and active leases while immutable old complete records stay separate',async()=>{
  const f=fixture(),filename=path.join(f.root,'old'),args={filePath:filename,content:'first generation\n'};
  await f.observer['tool.execute.before'](f.input('write','old-call'),{args});fs.writeFileSync(filename,args.content);
  await f.observer['tool.execute.after'](f.input('write','old-call',args),{title:'old',output:'ok',metadata:{filepath:filename,exists:false}});
  const oldBytes=fs.readFileSync(path.join(f.ledger,'calls',f.key('old-call'),'result.json'));
  const g=nextGeneration(f);assert.equal(f.result('old-call').status,'COMPLETE');assert.equal(fs.existsSync(path.join(g.ledger,'calls',f.key('old-call'))),false);
  for(let i=0;i<MAX_ACTIVE;i++)await f.observer['tool.execute.before'](f.input('write','first-pending-'+i),{args:{filePath:path.join(f.root,'p'+i),content:'x\n'}});
  await g.observer['tool.execute.before'](f.input('write','blocked-second-generation'),{args:{filePath:path.join(f.root,'g2'),content:'second\n'}});
  assert.equal(fs.readdirSync(path.join(g.ledger,'calls')).length,0);assert.equal(JSON.parse(fs.readFileSync(path.join(g.ledger,'capacity.json'))).reason,'ACTIVE_CAPTURE_CAPACITY');
  await f.observer.dispose();
  const second={filePath:path.join(f.root,'g2'),content:'second\n'};
  await g.observer['tool.execute.before'](f.input('write','g2-call'),{args:second});fs.writeFileSync(second.filePath,second.content);
  await g.observer['tool.execute.after'](f.input('write','g2-call',second),{title:'g2',output:'ok',metadata:{filepath:second.filePath,exists:false}});
  assert.equal(g.result('g2-call').status,'COMPLETE');assert.equal(g.result('g2-call').generation,2);assert.equal(f.result('old-call').generation,1);
  assert.deepEqual(fs.readFileSync(path.join(f.ledger,'calls',f.key('old-call'),'result.json')),oldBytes);
  assert.equal(fs.readdirSync(path.join(f.scope.budget_root,'reservations')).length,MAX_ACTIVE+2);await g.observer.dispose();
});
test('after binary/size/read failures, changed args and success-shaped errors never become COMPLETE',async()=>{
  for(const kind of ['binary','large','args','shape']){
    const f=fixture(),filename=path.join(f.root,'x'),args={filePath:filename,content:'text\n'};
    await f.observer['tool.execute.before'](f.input('write',kind),{args});
    fs.writeFileSync(filename,kind==='binary'?Buffer.from([0,1]):kind==='large'?Buffer.alloc(MAX_SIDE_BYTES+1,65):args.content);
    await f.observer['tool.execute.after'](f.input('write',kind,kind==='args'?{...args,content:'different\n'}:args),kind==='shape'?{error:'not original success'}:{title:'x',output:'ok',metadata:{filepath:filename,exists:false}});
    assert.equal(f.result(kind).status,'PARTIAL');await f.observer.dispose();
  }
});
test('independent observer instances sharing a path mark BOTH calls PARTIAL',async()=>{
  const f=fixture(),other=f.open(),filename=path.join(f.root,'x'),args={filePath:filename,content:'x\n'};
  await f.observer['tool.execute.before'](f.input('write','first'),{args});await other['tool.execute.before'](f.input('write','second'),{args});fs.writeFileSync(filename,args.content);
  await f.observer['tool.execute.after'](f.input('write','first',args),{title:'x',output:'ok',metadata:{filepath:filename,exists:false}});
  await other['tool.execute.after'](f.input('write','second',args),{title:'x',output:'ok',metadata:{filepath:filename,exists:false}});
  assert.equal(f.result('first').status,'PARTIAL');assert.equal(f.result('second').status,'PARTIAL');await f.observer.dispose();await other.dispose();
});
test('stable read detects a concurrent content change during its actual descriptor read',()=>{
  const base=fs.mkdtempSync(path.join(tmp,'read-')),filename=path.join(base,'x');fs.writeFileSync(filename,'first\n');
  const original=fs.readSync;let changed=false;
  fs.readSync=function(...args){const n=original.apply(this,args);if(!changed){changed=true;fs.writeFileSync(filename,'second-longer\n');}return n;};
  try{assert.throws(()=>readBounded(filename),e=>e.code==='READ_CHANGED');}finally{fs.readSync=original;}
});
test('exact indent-trimmed patches, no-newline and malicious ranges are verified without a diff dependency',()=>{
  assert.equal(verifyPatch('  a\n','  b\n','--- x\n+++ x\n@@ -1 +1 @@\n-a\n+b\n'),true);
  assert.equal(verifyPatch('a','b','@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b\n\\ No newline at end of file\n'),true);
  assert.equal(verifyPatch('human\na\n','b\n','@@ -1 +1 @@\n-a\n+b\n'),false);
});
test('reject bad root identity/ambient directory/grant and server has no custom tool or model client use',async()=>{
  for(const kind of ['root','directory','grant']){
    const f=fixture();if(kind==='root')f.config.scope.root_identity.ino='999999';if(kind==='grant')f.config.scope.allowed_write_paths=['.'];
    f.config.scope_sha256=digest(canonical(f.config.scope));f.writeConfig();
    assert.throws(()=>createObserver({directory:kind==='directory'?f.base:f.root,configPath:f.configPath}));await f.observer.dispose();
  }
  const f=fixture();const prior=process.env.EEAS_CHANGE_REVIEW_CONFIG;process.env.EEAS_CHANGE_REVIEW_CONFIG=f.configPath;
  try{const hooks=await server({directory:f.root,client:new Proxy({},{get(){throw new Error('must not call model/client');}})});assert.equal(hooks.tool,undefined);assert.equal(hooks.config,undefined);await hooks.dispose();}finally{if(prior===undefined)delete process.env.EEAS_CHANGE_REVIEW_CONFIG;else process.env.EEAS_CHANGE_REVIEW_CONFIG=prior;}
  await f.observer.dispose();
});

test('scoped native search defaults keep original permission metadata JSON-safe without auto-approval',async()=>{
  const f=fixture(['delivery/result.txt']);
  for(const tool of ['glob','grep']){
    const args={pattern:tool==='glob'?'delivery/**':'工单'};
    const output={args};await f.observer['tool.execute.before'](f.input(tool,'search-'+tool),output);
    assert.equal(args.path,f.root);assert.equal(args.pattern,tool==='glob'?'delivery/**':'工单');
    const metadata={pattern:args.pattern,path:args.path,...tool==='grep'?{include:args.include}:{}};
    assert.ok(Object.values(metadata).every(value=>typeof value==='string'),'original JsonValue encoder must not receive undefined');
    if(tool==='grep')assert.equal(args.include,''); // exact original no-filter truthiness branch
    assert.deepEqual(output,{args});assert.equal(fs.readdirSync(path.join(f.ledger,'calls')).length,0);
    assert.equal(fs.readdirSync(path.join(f.ledger,'blobs')).length,0);
    assert.deepEqual(f.scope.allowed_write_paths,['delivery/result.txt']);
  }
  await f.observer.dispose();
});
test('search compatibility retains explicit scope and include including invalid input for original validation',async()=>{
  const f=fixture();
  for(const tool of ['glob','grep'])for(const requested of ['subdir','../other',f.base,'',null]){
    const args={pattern:'x',path:requested,include:'*.txt'},before=structuredClone(args);
    await f.observer['tool.execute.before'](f.input(tool,'explicit'),{args});assert.deepEqual(args,before);
  }
  for(const tool of ['read','write','edit','apply_patch','bash']){
    const args={pattern:'x'};await f.observer['tool.execute.before'](f.input(tool,'other-'+tool),{args});assert.ok(!Object.hasOwn(args,'path'));assert.ok(!Object.hasOwn(args,'include'));
  }
  await f.observer.dispose();
});
test('search defaults never cross unbound, foreign, closed or changed native owner scope',async()=>{
  for(const mode of ['unbound','foreign','closed','changed']){
    const f=fixture('*',mode==='unbound'?null:'ses_owned'),input={tool:'glob',sessionID:mode==='foreign'?'ses_other':'ses_owned',callID:'search'},args={pattern:'*'};
    if(mode==='closed')await f.observer.dispose();
    if(mode==='changed'){f.config.scope.generation=2;f.config.scope_sha256=digest(canonical(f.config.scope));f.writeConfig();}
    await f.observer['tool.execute.before'](input,{args});assert.deepEqual(args,{pattern:'*'},mode);
    await f.observer.dispose();
  }
});
