import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import test from 'node:test';

const require=createRequire(import.meta.url);
const ts=require('typescript');
const {transformSync}=require('esbuild');
const app=ts.createSourceFile('App.tsx',readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
function productionAction(id,context){
  const found=[];
  function visit(node){
    if(ts.isCallExpression(node)&&ts.isPropertyAccessExpression(node.expression)&&node.expression.name.text==='registerAction'){
      const metadata=node.arguments[0];
      if(metadata&&ts.isObjectLiteralExpression(metadata)&&metadata.properties.some(property=>ts.isPropertyAssignment(property)&&property.name.getText(app)==='actionId'&&ts.isStringLiteral(property.initializer)&&property.initializer.text===id))found.push(node);
    }
    ts.forEachChild(node,visit);
  }
  visit(app);assert.equal(found.length,1);
  let handler;
  const values={...context,registry:{registerAction(_metadata,action){handler=action;}}};
  const code=transformSync(found[0].getText(app),{loader:'tsx',format:'cjs',target:'es2022'}).code;
  new Function(...Object.keys(values),code)(...Object.values(values));
  return handler;
}
function fixture(id){
  const requests=[],updates=[],result={id:'returned-original-result'};
  const handler=productionAction(id,{api:{editProposal:async(...args)=>{requests.push(args);return result;},confirmProposal:async(...args)=>{requests.push(args);return result;}},updateProposal:async(proposal,write)=>{updates.push(proposal);return write();}});
  return {handler,requests,updates,result};
}
const proposal={id:'proposal67',project_id:'project67',revision:3,sha256:'a'.repeat(64)};
const candidate={title:'fixture task'},manualReview={capture_ref:{id:'manual67'}};
const coverage={expected_basis_sha256:'b'.repeat(64),claim_links:[],user_exclusions:[]};

test('actual save action retains the ordinary v1 path and exact manual operation',async()=>{
  const ordinary=fixture('proposal.save');
  assert.equal(await ordinary.handler({data:proposal},{candidate,reason:'v1'}),ordinary.result);
  assert.deepEqual(ordinary.updates,[proposal]);
  assert.deepEqual(ordinary.requests,[[proposal,candidate,'v1']]);
  const manual=fixture('proposal.save');
  await manual.handler({data:proposal},{candidate,reason:'keep manual edits',operationId:'manual_original_67',manualReview});
  assert.equal(manual.updates.length,0);
  assert.deepEqual(manual.requests[0].slice(0,5),[proposal,candidate,'keep manual edits','manual_original_67',manualReview]);
  assert.equal(manual.requests[0][5],undefined);
});

test('actual v2 save preserves original key and returns to its owning view',async()=>{
  const value=fixture('proposal.save');
  const returned=await value.handler({data:proposal},{candidate,reason:'joint save',operationId:'coverage_original_67',manualReview,requirementCoverage:coverage});
  assert.equal(returned,value.result);
  assert.deepEqual(value.requests,[[proposal,candidate,'joint save','coverage_original_67',manualReview,coverage]]);
  assert.equal(value.updates.length,0);
});

test('actual confirm action keeps v1 arrays and forwards exact v2 snapshot/exclusions',async()=>{
  const resolutions=[{question_id:'q1',reason:'fixture answer'}],old=fixture('proposal.confirm');
  await old.handler({data:proposal},resolutions);
  assert.deepEqual(old.requests,[[proposal,resolutions]]);assert.deepEqual(old.updates,[proposal]);
  const current=fixture('proposal.confirm');
  assert.equal(await current.handler({data:proposal},{operationId:'confirm_original_67',resolutions,coverageSha256:'c'.repeat(64),confirmedExclusionIds:['claim7']}),current.result);
  assert.deepEqual(current.requests,[[proposal,resolutions,'confirm_original_67',{coverage_sha256:'c'.repeat(64),confirmed_exclusion_ids:['claim7']}]]);
  assert.equal(current.updates.length,0);
});

test('missing original v2 operation never allocates or dispatches a replacement',async()=>{
  const save=fixture('proposal.save'),confirm=fixture('proposal.confirm');
  await assert.rejects(save.handler({data:proposal},{candidate,reason:'fixture',requirementCoverage:coverage}),/原保存操作缺失/);
  await assert.rejects(confirm.handler({data:proposal},{resolutions:[],coverageSha256:'c'.repeat(64),confirmedExclusionIds:[]}),/原确认操作缺失/);
  assert.equal(save.requests.length+confirm.requests.length,0);
  assert.equal(save.updates.length+confirm.updates.length,0);
});

test('actual discovery action sends the new contract once with the existing budget and profile',async()=>{
  const requests=[],observed=[],profile={id:'profile67',revision:1,sha256:'d'.repeat(64)},result={status:'PROCESSING'};
  const handler=productionAction('discovery.plan',{activeOperation:()=>undefined,setDiscoveryError:()=>assert.fail('actual request must preserve its pending index'),navigation:{capture:()=>()=>true},isPlanWaitSeconds:value=>[120,300,600].includes(value),operationId:()=> 'discovery_original_67',observeDiscovery:(...args)=>observed.push(args),discoveryApi:{plan:async(...args)=>{requests.push(args);return result;}},providers:()=>[{profile_ref:profile}],providerId:()=>profile.id});
  const session={id:'discovery67',project_id:'project67',revision:4};
  assert.equal(await handler({data:session},{timeoutSeconds:300}),result);
  assert.deepEqual(requests,[[session,profile,'discovery_original_67',undefined,300,'ACCEPTED_CLAIMS_V1']]);
  assert.deepEqual(observed,[['project67','discovery67','discovery_original_67',{basisRevision:4,discoveryPurpose:'PLAN'}]]);
});

test('actual discovery API method leaves legacy request bytes unchanged and adds only explicit contract',async()=>{
  const source=ts.createSourceFile('discovery-api.ts',readFileSync(new URL('../src/discovery-api.ts',import.meta.url),'utf8'),ts.ScriptTarget.Latest,true);
  const declaration=source.statements.find(node=>ts.isClassDeclaration(node)&&node.name?.text==='DiscoveryApi');
  const method=declaration.members.find(node=>ts.isMethodDeclaration(node)&&node.name.getText(source)==='plan');
  const code=transformSync('class BoundDiscoveryApi {'+method.getText(source)+'};return BoundDiscoveryApi;',{loader:'ts',target:'es2022'}).code;
  const Bound=new Function('operationId','isPlanWaitSeconds',code)(()=> 'unused_default',value=>[120,300,600].includes(value));
  const calls=[],client=new Bound();client.path=()=> '/api/projects/p/discovery/sessions/s';client.client={request:(...args)=>{calls.push(args);return Promise.resolve({});}};
  const session={project_id:'p',id:'s',revision:2};
  await client.plan(session,undefined,'legacy_67',undefined,300);
  await client.plan(session,undefined,'covered_67',undefined,300,'ACCEPTED_CLAIMS_V1');
  assert.deepEqual(calls[0],['/api/projects/p/discovery/sessions/s/plan','POST',{operation_id:'legacy_67',expected_revision:2,budget:{timeout_seconds:300}},undefined,false]);
  assert.deepEqual(calls[1][2],{operation_id:'covered_67',expected_revision:2,budget:{timeout_seconds:300},coverage_contract:'ACCEPTED_CLAIMS_V1'});
});


test('an unresolved original plan keeps its own operation and cannot dispatch a replacement',async()=>{
 const pending={id:'retained-plan',kind:'discovery',projectId:'project67',sessionId:'discovery67',basisRevision:4,discoveryPurpose:'PLAN',status:'UNKNOWN_OUTCOME'},before=structuredClone(pending);
 const handler=productionAction('discovery.plan',{navigation:{capture:()=>()=>true},isPlanWaitSeconds:value=>[120,300,600].includes(value),activeOperation:()=>pending,operationId:()=>assert.fail('must retain original ID'),observeDiscovery:()=>assert.fail('must not replace observer'),discoveryApi:{plan:()=>assert.fail('must not send another plan')}});
 assert.equal(await handler({data:{id:'discovery67',project_id:'project67',revision:4}},{timeoutSeconds:300}),undefined);assert.deepEqual(pending,before);
});
