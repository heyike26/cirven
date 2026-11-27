import test from 'node:test';
import assert from 'node:assert/strict';
import {ApiError,ApiClient}from'../src/api.ts';
import {PlanningDeliveryApi}from'../src/planning-delivery.ts';
import {unavailableDeliveryItem,DeliveryHistoryRecovery,upsertDeliveryFeed}from'../src/delivery-recovery.ts';
const target={projectId:'qa-project',kind:'EXPORT',basis:{id:'qa-proposal',revision:4,sha256:'a'.repeat(64)}};
test('concise status separates exact diagnostics and operation keys without filtering arbitrary text',()=>{
 const raw='Error invoking remote method \'eeas:literal-user-text\': RAW_BACKEND_66 中文 <tag> '+ 'long detail '.repeat(1500),error=new ApiError(404,'NOT_FOUND',raw),item=unavailableDeliveryItem(target,'original-A',error);
 assert.equal(item.status,'NO_RECORD');assert.equal(item.title,'交付回执尚未核对');assert.match(item.body,/没有记录不证明未生成/);assert(!item.body.includes('original-A'));assert(!item.body.includes('RAW_BACKEND_66'));assert.deepEqual(item.deliveryInspection,{operationId:'original-A',diagnostic:raw});assert.equal(error.message,raw);assert.equal(item.deliveryTarget,target);assert.equal(item.artifact,undefined);
 const arbitrary='用户原文：Error invoking remote method \'eeas:my-document\': 保留全部。';assert.equal(unavailableDeliveryItem(target,'original-B',arbitrary).deliveryInspection.diagnostic,arbitrary);
});
test('explicit checks use actual original GET URLs per operation and stale scope cannot replace another card',async()=>{
 let bootstrapCalls=0;const calls=[],client=new ApiClient(async(url,options)=>{if(String(url)==='/api/bootstrap'){bootstrapCalls++;return new Response(JSON.stringify({csrf_token:'qa-fixture-only'}),{status:200,headers:{'Content-Type':'application/json'}});}calls.push({url:String(url),method:options.method??'GET',body:options.body});return new Response(JSON.stringify({error:{code:'NOT_FOUND',message:'RAW_BACKEND_66 '+url}}),{status:404,headers:{'Content-Type':'application/json'}});});
 let cards=['original-A','original-B'].map(key=>unavailableDeliveryItem(target,key,new ApiError(404,'NOT_FOUND','before '+key)));const recovery=new DeliveryHistoryRecovery(new PlanningDeliveryApi(client),{getItem:()=>null},item=>cards=upsertDeliveryFeed(cards,item));
 for(const key of ['original-B','original-A'])await assert.rejects(recovery.read(target,key),ApiError);
 assert.deepEqual(calls.map(x=>[x.url,x.method]),[['/api/projects/qa-project/planning-delivery/operations/original-B','GET'],['/api/projects/qa-project/planning-delivery/operations/original-A','GET']]);assert(calls.every(x=>x.body===undefined));assert.equal(bootstrapCalls,1);assert.equal(cards.length,2);assert.deepEqual(cards.map(x=>x.deliveryInspection.operationId),['original-A','original-B']);assert(cards.every(x=>x.status==='NO_RECORD'&&!x.artifact));
 const before=cards;await assert.rejects(recovery.read(target,'original-A',()=>false),ApiError);assert.equal(cards,before);recovery.stop();
});
