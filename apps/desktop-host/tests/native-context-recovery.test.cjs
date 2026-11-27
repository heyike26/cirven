'use strict';
// AUTHORED_NOT_RUN: actual Main handler source, UNIT authenticated IPC/HTTP.
// No Electron application, native stop, SDK, browser or Windows acceptance.
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
function harness(respond){
 const source=fs.readFileSync(path.join(__dirname,'../main.cjs'),'utf8');
 const start=source.indexOf("  for(const action of ['prepare','restart','operation'])ipcMain.handle('eeas:native-context-recovery-'");
 const end=source.indexOf("  ipcMain.handle('eeas:control-get'",start);
 assert.ok(start>0&&end>start);const handlers=new Map(),calls=[];
 vm.runInNewContext(source.slice(start,end),{ipcMain:{handle:(name,handler)=>handlers.set(name,handler)},
  admitted:event=>{if(event?.trusted!==true)throw Error('UNIT not authenticated');},
  exactKeys:(value,keys)=>{assert.ok(value&&typeof value==='object'&&!Array.isArray(value));assert.deepEqual(Object.keys(value).sort(),Array.from(keys).sort());},
  request:async(route,body)=>{calls.push({route,body});return respond(route,body);},
 });
 return {calls,invoke:(action,body,event={trusted:true})=>handlers.get('eeas:native-context-recovery-'+action)(event,body)};
}
const ref={id:'native_parent',revision:1,sha256:'a'.repeat(64)},key='native_recovery_'+'b'.repeat(48),checksum='c'.repeat(64);

test('Main requires its authenticated renderer and rejects injected paths or expanded authority',async()=>{
 const h=harness(()=>assert.fail('No API expected'));
 await assert.rejects(()=>h.invoke('prepare',{parentOperationId:'native_parent',expectedNativeRef:ref},{trusted:false}));
 await assert.rejects(()=>h.invoke('prepare',{parentOperationId:'../foreign',expectedNativeRef:ref}));
 await assert.rejects(()=>h.invoke('restart',{parentOperationId:'native_parent',request:{operation_id:key,expected_native_ref:ref,preparation_sha256:checksum,raw_text:'NEVER'}}));
 assert.equal(h.calls.length,0);
});

test('missing original recovery is a read-only NOT_PROVEN observation with no POST',async()=>{
 const h=harness(()=>{throw Object.assign(Error('UNIT missing'),{status:404});});
 const r=await h.invoke('operation',{parentOperationId:'native_parent',operationId:key,requestSha256:checksum});
 assert.equal(r.kind,'NATIVE_CONTEXT_RECOVERY_NOT_FOUND');assert.equal(r.original_execution_outcome,'NOT_PROVEN');
 assert.equal(r.native_posts,0);assert.equal(r.model_calls,0);assert.equal(h.calls.length,1);assert.equal(h.calls[0].body,undefined);
 assert.equal(h.calls[0].route,'/api/desktop-host/native-messages/native_parent/context-recovery/'+key);
});

test('original lookup checks parent, key and body checksum before exposing a recovered result',async()=>{
 const h=harness(()=>({parent_operation_id:'foreign_parent',operation_id:key,request_sha256:checksum}));
 await assert.rejects(()=>h.invoke('operation',{parentOperationId:'native_parent',operationId:key,requestSha256:checksum}));
 assert.equal(h.calls.length,1);assert.equal(h.calls[0].body,undefined);
});
