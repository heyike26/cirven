import test from 'node:test';
import assert from 'node:assert/strict';
import {ApiClient,ApiError} from '../src/api.ts';
import {DirectActionApi} from '../src/direct-actions.ts';
test('typed failed ordinary outcome is displayed from the exact project/op HTTP422; unrelated validation errors remain errors and never retry',async()=>{
 const calls=[];let value={project_id:'p',operation_id:'op',status:'FAILED',replayed:false,interaction:{mode:'NONE',questions:[]},error:{type:'SourceConflict',message:'原文件已变化'}};const client=new ApiClient(async(url,options)=>{calls.push({url,options});return new Response(JSON.stringify(url==='/api/bootstrap'?{csrf_token:'c'}:value),{status:url==='/api/bootstrap'?200:422});});const direct=new DirectActionApi(client);const result=await direct.run('p','修改已选文件',{write:true},'op');assert.equal(result.status,'FAILED');assert.equal(calls.length,2);const body=JSON.parse(calls[1].options.body);assert.equal(body.user_requested_write,true);assert.equal(body.budget.max_calls,1);assert.ok(!calls[1].url.includes('discovery'));value={detail:[{loc:['body','instruction'],msg:'invalid'}]};await assert.rejects(()=>direct.run('p','',{write:false},'other'),error=>error instanceof ApiError&&error.status===422);assert.equal(calls.length,3);
});
