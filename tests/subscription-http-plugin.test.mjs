// AUTHORED_NOT_RUN: actual plugin + final HTTP wrapper, UNIT fetch/broker only.
// No original CLI/auth module, login, network, model or native qualification.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {createHash} from 'node:crypto';
import {ModelAuthorityPlugin} from '../toolchain/opencode-plugins/model-authority/index.mjs';
const stable=v=>Array.isArray(v)?'['+v.map(stable).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}':JSON.stringify(v);
const sha=v=>createHash('sha256').update(typeof v==='string'?v:stable(v)).digest('hex');
const ENDPOINT='https://chatgpt.com/backend-api/codex/responses';
async function setup(t,adapter=false){
 const directory=fs.mkdtempSync(path.join(os.tmpdir(),'cirven-http-meter-')),filename=path.join(directory,'scope.json'),oldEnv=process.env.EEAS_MODEL_AUTHORITY_CONFIG,oldFetch=globalThis.fetch;
 let data={schema:'eeas.model-authority-scope.v1',plugin_id:'eeas.model-authority',plugin_manifest_sha256:'a'.repeat(64),owner_ref:{project_id:'p',session_ref:{id:'a'.repeat(32),revision:1,sha256:'b'.repeat(64)}},generation:1,directory,native_session_id:null,broker_url:'http://127.0.0.1:12345',broker_key:'c'.repeat(64),subscription_http:{model_id:'UNIT_MODEL',transport:'ORIGINAL_CODEX_HTTP_FALLBACK'}};
 if(adapter){data.owner_ref={project_id:'p',execution_ref:{id:'original-execution',revision:1,sha256:'b'.repeat(64)}};data.call_protocol='ADAPTER_EXECUTION_V1';}
 const write=()=>fs.writeFileSync(filename,stable({...data,scope_sha256:sha(data)}),{mode:0o600});write();process.env.EEAS_MODEL_AUTHORITY_CONFIG=filename;
 const requests=[],forwards=[],passthrough=[];let behavior='OK',reservations=0,plugin;
 globalThis.fetch=async(input,init)=>{const url=String(input);
  if(url.startsWith('http://127.0.0.1:12345/')){const body=JSON.parse(init.body);requests.push(body);if(body.schema.endsWith('-call.v1'))return Response.json({schema:body.schema.replace('-call.v1','-receipt.v1'),admitted:true,step:body.step,generation:body.generation,root_native_session_id:body.root_native_session_id,native_session_id:body.native_session_id,user_message_id:body.user_message_id,model_calls_reserved:0,plan_sha256:sha(body)});
   reservations++;if(behavior==='UNKNOWN')throw Error('UNIT host reply lost');if(behavior==='REJECT'||reservations>2)return Response.json({error:'UNIT exhausted'},{status:403});if(behavior==='CHANGE_SCOPE'){data.generation++;write();}
   return Response.json({admitted:true,...body,schema:body.schema.replace('-http-hop.v1','-http-hop-receipt.v1'),model_calls_reserved:0,reserved:reservations,limit:2,reservation_sha256:behavior==='BAD_RECEIPT'?'bad':'d'.repeat(64)});}
  if(url===ENDPOINT){const response=new Response('UNIT original response');forwards.push({input,init,response});return response;}
  passthrough.push({input,init});return new Response('UNIT original auth refresh or metadata');
 };
 const nativeFetch=globalThis.fetch;
 t.after(async()=>{await plugin?.dispose?.();globalThis.fetch=oldFetch;if(oldEnv===undefined)delete process.env.EEAS_MODEL_AUTHORITY_CONFIG;else process.env.EEAS_MODEL_AUTHORITY_CONFIG=oldEnv;fs.rmSync(directory,{recursive:true,force:true});});
 plugin=await ModelAuthorityPlugin({directory});data.native_session_id='ses_root';write();
 const incoming={sessionID:'ses_root',agent:'build',model:{providerID:'openai',id:'UNIT_MODEL'},message:{id:'msg_original',text:'UNIT_PRIVATE_SOURCE'}};
 async function plan(){await plugin['chat.params'](incoming,{});const output={headers:{authorization:'Bearer UNIT_NATIVE_AUTH','content-type':'application/json'}};await plugin['chat.headers'](incoming,output);return output.headers;}
 const body=JSON.stringify({model:'UNIT_MODEL',stream:true,input:'UNIT_PRIVATE_SOURCE 😀'}),send=headers=>globalThis.fetch(ENDPOINT,{method:'POST',headers,body});
 return {directory,filename,incoming,plugin,plan,send,body,requests,forwards,passthrough,nativeFetch,behavior:value=>behavior=value,reservations:()=>reservations};
}

test('the original HTTP retry reserves again and a third attempt cannot forward',async t=>{
 const x=await setup(t),headers=await x.plan();assert.equal(headers['x-opencode-title'],'true');const result=await x.send(headers);assert.equal(result,x.forwards[0].response);await x.send(headers);await assert.rejects(()=>x.send(headers));
 assert.equal(x.forwards.length,2);assert.equal(x.requests.filter(p=>p.schema==='eeas.model-authority-http-hop.v1').length,3);assert.deepEqual(x.requests.filter(p=>p.hop).map(p=>p.hop),[1,2,3]);
 for(const f of x.forwards){assert.equal(f.init.body,x.body);assert.equal(f.init.headers.get('authorization'),'Bearer UNIT_NATIVE_AUTH');assert.equal(f.init.headers.get('x-cirven-model-plan'),null);assert.equal(f.init.headers.get('x-opencode-title'),null);assert.equal(f.init.redirect,'error');}
 assert.doesNotMatch(JSON.stringify(x.requests),/UNIT_PRIVATE_SOURCE|UNIT_NATIVE_AUTH/);assert.equal(x.requests.find(p=>p.hop).request_bytes,Buffer.byteLength(x.body));
});

test('an unknown broker response consumes one reservation and causes no hidden retry',async t=>{
 const x=await setup(t),headers=await x.plan();x.behavior('UNKNOWN');await assert.rejects(()=>x.send(headers),/reply lost/);assert.equal(x.reservations(),1);assert.equal(x.forwards.length,0);assert.equal(x.requests.length,2);
});

test('rejection and malformed reservation proof never reach original transport',async t=>{
 const x=await setup(t),headers=await x.plan();x.behavior('BAD_RECEIPT');await assert.rejects(()=>x.send(headers),/RECEIPT_CHANGED/);x.behavior('REJECT');await assert.rejects(()=>x.send(headers));assert.equal(x.forwards.length,0);
});

test('unmarked inference, another endpoint and changed model cannot leak private markers',async t=>{
 const x=await setup(t);await assert.rejects(()=>fetch('https://api.openai.com/v1/responses',{method:'POST',body:x.body}),/UNEXPECTED_ROUTE/);await assert.rejects(()=>x.send({authorization:'Bearer UNIT_NATIVE_AUTH'}),/PLAN_REQUIRED/);const headers=await x.plan();await assert.rejects(()=>fetch('https://other.invalid/responses',{method:'POST',headers,body:x.body}),/UNEXPECTED_ROUTE/);await assert.rejects(()=>fetch(ENDPOINT,{method:'POST',headers,body:'{"model":"OTHER","stream":true}'}),/MODEL_CHANGED/);assert.equal(x.forwards.length,0);assert.equal(x.reservations(),0);assert.equal(x.passthrough.length,0);
});

test('original account refresh passes unchanged and does not consume inference allowance',async t=>{
 const x=await setup(t),init={method:'POST',body:'UNIT_REFRESH_BODY'};await fetch('https://auth.openai.com/oauth/token',init);assert.equal(x.passthrough[0].init,init);assert.equal(x.reservations(),0);assert.equal(x.requests.length,0);
});

test('a scope change after saved reservation cannot forward and cannot refund',async t=>{
 const x=await setup(t),headers=await x.plan();x.behavior('CHANGE_SCOPE');await assert.rejects(()=>x.send(headers),/OWNER_CHANGED/);assert.equal(x.reservations(),1);assert.equal(x.forwards.length,0);
});

test('disposal restores the original fetch and only follows successful installation readiness',async t=>{
 const x=await setup(t),ready=JSON.parse(fs.readFileSync(path.join(x.directory,'ready.json')));assert.equal(ready.subscription_http,'INSTALLED_ORIGINAL_CODEX_HTTP_FALLBACK');await x.plugin.dispose();assert.equal(globalThis.fetch,x.nativeFetch);
});

test('API profiles keep the prior hook with no fetch replacement or HTTP claims',async t=>{
 const x=await setup(t);await x.plugin.dispose();const data=JSON.parse(fs.readFileSync(x.filename));delete data.scope_sha256;delete data.subscription_http;const filename=x.filename+'.api';fs.writeFileSync(filename,stable({...data,scope_sha256:sha(data)}));process.env.EEAS_MODEL_AUTHORITY_CONFIG=filename;fs.unlinkSync(path.join(x.directory,'ready.json'));const original=globalThis.fetch,plugin=await ModelAuthorityPlugin({directory:x.directory});assert.equal(globalThis.fetch,original);assert.equal(plugin['chat.headers'],undefined);assert.equal(JSON.parse(fs.readFileSync(path.join(x.directory,'ready.json'))).subscription_http,undefined);
});


test('adapter execution protocol keeps its real execution_ref through plan and final HTTP reservation',async t=>{
 const x=await setup(t,true),headers=await x.plan();await x.send(headers);assert.equal(x.forwards.length,1);assert.equal(x.requests[0].schema,'eeas.adapter-model-authority-call.v1');assert.equal(x.requests[1].schema,'eeas.adapter-model-authority-http-hop.v1');assert.ok(x.requests[1].owner_ref.execution_ref);assert.equal(x.requests[1].owner_ref.session_ref,undefined);assert.doesNotMatch(JSON.stringify(x.requests),/UNIT_PRIVATE_SOURCE|UNIT_NATIVE_AUTH/);
});
