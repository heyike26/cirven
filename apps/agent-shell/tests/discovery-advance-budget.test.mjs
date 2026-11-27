import test from 'node:test';
import assert from 'node:assert/strict';
import {ApiClient} from '../src/api.ts';
import {DiscoveryApi} from '../src/discovery-api.ts';

const session={id:'work',project_id:'p',revision:1,sha256:'a'.repeat(64)};
const reply=(data,status=200)=>new Response(JSON.stringify(data),{status});
function scene(status=200){const calls=[];const api=new DiscoveryApi(new ApiClient(async(url,init)=>{calls.push({url,method:init?.method??'GET',body:init?.body?JSON.parse(init.body):undefined});return url==='/api/bootstrap'?reply({csrf_token:'owned'}):reply(status===401?{error:{code:'UNAUTHORIZED',message:'response lost after possible dispatch'}}:{status:'PROCESSING'},status);}));return{api,calls};}
test('a selected wait limit is frozen in the new requirements request without changing legacy omitted defaults',async()=>{
 for(const value of [undefined,120,300,600]){const s=scene();await s.api.advance(session,'Unchanged explicit full task',value===undefined?{}:{timeoutSeconds:value},'new-owned-operation');const write=s.calls.find(v=>v.method==='POST');assert.deepEqual(write.body.budget,value===undefined?undefined:{timeout_seconds:value});assert.equal(write.body.operation_id,'new-owned-operation');assert.equal(write.body.expected_revision,1);assert.equal(s.calls.filter(v=>v.method==='POST').length,1);}
});
test('unsupported or non-numeric wait settings are rejected before any bootstrap or mutation',()=>{
 for(const value of [121,'300',null,NaN,Infinity,-1]){const s=scene();assert.throws(()=>s.api.advance(session,'Full task',{timeoutSeconds:value},'never-dispatched'),/2、5 或 10/);assert.deepEqual(s.calls,[]);}
});
test('an uncertain authorization response does not cause automatic replay of a requirements model request',async()=>{
 const s=scene(401);await assert.rejects(()=>s.api.advance(session,'Retain original request',{timeoutSeconds:300},'original-key'),e=>e.status===401);assert.deepEqual(s.calls.map(v=>[v.method,v.url]),[['GET','/api/bootstrap'],['POST','/api/projects/p/discovery/sessions/work/advance']]);
});
