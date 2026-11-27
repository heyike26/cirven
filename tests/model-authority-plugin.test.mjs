// AUTHORED_NOT_RUN: actual plugin callback, UNIT fetch/SDK boundaries.
// No provider call, SDK process, model, subscription or Windows acceptance.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {createHash} from 'node:crypto';
import {ModelAuthorityPlugin} from '../toolchain/opencode-plugins/model-authority/index.mjs';
const stable=v=>Array.isArray(v)?'['+v.map(stable).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}':JSON.stringify(v);
const sha=v=>createHash('sha256').update(stable(v)).digest('hex');
function setup(t,fetcher){
 const directory=fs.mkdtempSync(path.join(os.tmpdir(),'eeas-model-authority-')),filename=path.join(directory,'scope.json'),before=process.env.EEAS_MODEL_AUTHORITY_CONFIG,oldFetch=globalThis.fetch;
 let body={schema:'eeas.model-authority-scope.v1',plugin_id:'eeas.model-authority',plugin_manifest_sha256:'a'.repeat(64),owner_ref:{project_id:'p',session_ref:{id:'a'.repeat(32),revision:1,sha256:'b'.repeat(64)}},generation:1,directory,native_session_id:null,broker_url:'http://127.0.0.1:12345',broker_key:'c'.repeat(64)};
 function write(){fs.writeFileSync(filename,stable({...body,scope_sha256:sha(body)}),{mode:0o600});}write();process.env.EEAS_MODEL_AUTHORITY_CONFIG=filename;
 const requests=[];globalThis.fetch=async(url,options)=>{const packet=JSON.parse(options.body);requests.push({url,options,packet});return fetcher(packet,options);};
 t.after(()=>{globalThis.fetch=oldFetch;if(before===undefined)delete process.env.EEAS_MODEL_AUTHORITY_CONFIG;else process.env.EEAS_MODEL_AUTHORITY_CONFIG=before;fs.rmSync(directory,{recursive:true,force:true});});
 return {directory,filename,body,requests,bind:()=>{body.native_session_id='ses_root';write();}};
}
const incoming=(session='ses_root')=>({sessionID:session,agent:'build',model:{id:'UNIT_MODEL_KEEP'},message:{id:'msg_original',text:'PRIVATE_SOURCE_BODY'}});
const ok=p=>Response.json({schema:'eeas.model-authority-receipt.v1',admitted:true,step:p.step,generation:p.generation,root_native_session_id:p.root_native_session_id,native_session_id:p.native_session_id,user_message_id:p.user_message_id,model_calls_reserved:0});

test('callback sends identities only and leaves original model parameters untouched',async t=>{
 const x=setup(t,ok),plugin=await ModelAuthorityPlugin({directory:x.directory});x.bind();const input=incoming(),output={options:{model:'KEEP_EXACT_MODEL'},maxOutputTokens:37},before=structuredClone({input,output});
 await plugin['chat.params'](input,output);assert.deepEqual({input,output},before);assert.equal(x.requests.length,1);
 const packet=x.requests[0].packet;assert.deepEqual(Object.keys(packet).sort(),['schema','owner_ref','generation','root_native_session_id','native_session_id','user_message_id','agent','step'].sort());
 assert.doesNotMatch(JSON.stringify(packet),/PRIVATE_SOURCE|UNIT_MODEL|maxOutputTokens/);assert.equal(x.requests[0].options.redirect,'error');assert.equal(x.requests[0].options.headers.Authorization,'Bearer '+'c'.repeat(64));
 const ready=JSON.parse(fs.readFileSync(path.join(x.directory,'ready.json')));assert.equal(ready.plugin_id,'eeas.model-authority');
});

test('an unbound original session cannot reach even the loopback callback',async t=>{
 const x=setup(t,()=>assert.fail('No host call')),plugin=await ModelAuthorityPlugin({directory:x.directory});await assert.rejects(()=>plugin['chat.params'](incoming(),{}),/ORIGINAL_CONTEXT_REQUIRED/);assert.equal(x.requests.length,0);
});

test('rejection or unavailable host never retries or changes the selected model',async t=>{
 const x=setup(t,()=>Response.json({error:'UNIT source withdrawn'},{status:409})),plugin=await ModelAuthorityPlugin({directory:x.directory});x.bind();const output={options:{provider:'KEEP'}};
 await assert.rejects(()=>plugin['chat.params'](incoming(),output),/MODEL_AUTHORITY_REJECTED/);assert.equal(x.requests.length,1);assert.deepEqual(output,{options:{provider:'KEEP'}});
 globalThis.fetch=async()=>{throw Error('UNIT host unavailable');};await assert.rejects(()=>plugin['chat.params'](incoming(),output),/UNIT host unavailable/);assert.deepEqual(output,{options:{provider:'KEEP'}});
});

test('late or broadened receipt cannot release the original native plan',async t=>{
 let changed={step:99};const x=setup(t,p=>Response.json({...JSON.parse(JSON.stringify({schema:'eeas.model-authority-receipt.v1',admitted:true,step:p.step,generation:p.generation,root_native_session_id:p.root_native_session_id,native_session_id:p.native_session_id,user_message_id:p.user_message_id,model_calls_reserved:0})),...changed})),plugin=await ModelAuthorityPlugin({directory:x.directory});x.bind();
 for(changed of [{step:99},{generation:2},{native_session_id:'ses_foreign'},{user_message_id:'msg_foreign'},{admitted:false},{model_calls_reserved:1}])await assert.rejects(()=>plugin['chat.params'](incoming(),{}),/REPLY_MISMATCH/);
});

test('reply body is bounded before JSON adoption',async t=>{
 const x=setup(t,()=>new Response('x'.repeat(4097))),plugin=await ModelAuthorityPlugin({directory:x.directory});x.bind();await assert.rejects(()=>plugin['chat.params'](incoming(),{}),/REPLY_TOO_LARGE/);assert.equal(x.requests.length,1);
});

test('four in-flight native plans have separate steps and a fifth cannot accumulate work',async t=>{
 const waiting=[],x=setup(t,p=>new Promise(resolve=>waiting.push(()=>resolve(ok(p))))),plugin=await ModelAuthorityPlugin({directory:x.directory});x.bind();
 const pending=Array.from({length:4},()=>plugin['chat.params'](incoming(),{}));await assert.rejects(()=>plugin['chat.params'](incoming(),{}),/CAPACITY/);assert.equal(x.requests.length,4);waiting.forEach(release=>release());await Promise.all(pending);assert.deepEqual(x.requests.map(r=>r.packet.step),[1,2,3,4]);
});

test('a linked scope is rejected while keeping the original file',async t=>{
 const x=setup(t,()=>assert.fail('No callback')),saved=x.filename+'.original';fs.renameSync(x.filename,saved);fs.linkSync(saved,x.filename);
 await assert.rejects(()=>ModelAuthorityPlugin({directory:x.directory}),/SCOPE_INVALID/);assert.equal(fs.existsSync(saved),true);
});
