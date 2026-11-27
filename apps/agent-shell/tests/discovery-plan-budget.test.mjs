import {operationStatus} from '../src/operation-outcome.ts';
import {clearDiscoveryPending} from '../src/discovery-pending.ts';
import {createRequire as makeRequire} from 'node:module';
const require=makeRequire(import.meta.url);
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{createRequire}=require('node:module'),{pathToFileURL}=require('node:url');
const app=path.dirname(path.dirname(require('node:url').fileURLToPath(import.meta.url))),R=path.resolve(app,'../..'),req=createRequire(path.join(app,'package.json')),ts=req('typescript');
const transpile=code=>ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText;
const apiPath=path.join(R,'apps/agent-shell/src/api.ts'),proposed=path.join(app,'src');
const reply=(data,status=200)=>new Response(JSON.stringify(data),{status});
const session={id:'work',project_id:'p',revision:4,sha256:'a'.repeat(64),payload:{}};
let actualApi,DiscoveryApi,isPlanWaitSeconds,action,helpers;
test.before(async()=>{
 actualApi=await import(pathToFileURL(apiPath));const module={exports:{}};
 new Function('require','module','exports',transpile(fs.readFileSync(path.join(proposed,'discovery-api.ts'),'utf8')))(name=>{assert.equal(name,'./api.ts');return actualApi;},module,module.exports);
 ({DiscoveryApi,isPlanWaitSeconds}=module.exports);
 const source=fs.readFileSync(path.join(proposed,'App.tsx'),'utf8'),parsed=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
 function visit(node){if(ts.isCallExpression(node)&&node.expression.getText(parsed)==='registry.registerAction'){const descriptor=node.arguments[0];if(ts.isObjectLiteralExpression(descriptor)&&descriptor.properties.some(p=>ts.isPropertyAssignment(p)&&p.name.getText(parsed)==='actionId'&&p.initializer.text==='discovery.plan'))action=transpile('module.exports=('+node.arguments[1].getText(parsed)+');');}ts.forEachChild(node,visit);}
 visit(parsed);assert.ok(action,'execute exact App registered callback, not a copy');
 const names=['releaseDiscoveryOperation','presentDiscoveryResult','applyDiscoveryObservation','inspectDiscoveryOperation','stopObserver'],found=[];function collect(n){if(ts.isFunctionDeclaration(n)&&names.includes(n.name?.text))found.push(n.getText(parsed));ts.forEachChild(n,collect);}collect(parsed);assert.equal(found.length,names.length);helpers=transpile(found.join('\n'));
});
function controller(client,{saveFails=false,cleanupFails=false,displayFails=false,inspectFails=false}={}){
 let observed=[],active,scopedError,currentSession=session,saved=[],inspected=[];const storage=new Map(),api=new DiscoveryApi(client);
 const context={operationStatus,clearDiscoveryPending,batch:fn=>fn(),navigation:{capture:()=>()=>true},operationId:()=> 'new-plan-once',project:()=>({id:'p'}),activeSession:()=>currentSession,providers:()=>[],providerId:()=>'',
  observeDiscovery:(pid,sid,key,details)=>{if(saveFails)throw Error('local quota');storage.set('eeas-discovery-operation:'+pid,JSON.stringify({id:key,sessionId:sid}));observed.push([pid,sid,key,details]);active={id:key,projectId:pid,sessionId:sid,kind:'discovery',status:'REQUESTING',...details};},
  discoveryApi:api,isPlanWaitSeconds,ApiError:actualApi.ApiError,activeOperation:()=>active,setDiscoveryError:v=>scopedError=v,setNotice:()=>{},setActiveOperation:v=>{active=typeof v==='function'?v(active):v;},
  localStorage:{getItem:k=>storage.get(k)??null,removeItem:k=>{if(cleanupFails)throw Error('local cleanup unavailable');storage.delete(k);}},
  rememberSession:value=>{currentSession=value;saved.push(value);if(displayFails)throw Error('view unavailable');},inspect:value=>{if(inspectFails)throw Error('panel unavailable');inspected.push(value);},envelope:(...args)=>args,refreshObjects:async()=>{}};
 const module={exports:{}};new Function(...Object.keys(context),'module','let operationObserver,operationTimer;'+helpers+action)(...Object.values(context),module);
 return {run:module.exports,observed,state:()=>({active,scopedError,saved,storage,inspected})};
}

test('real DiscoveryApi sends explicit wait primitive only; legacy callers omit budget',async()=>{
 const writes=[],api=new DiscoveryApi(new actualApi.ApiClient(async(url,options)=>{if(url==='/api/bootstrap')return reply({csrf_token:'fixture'});writes.push(JSON.parse(options.body));return reply({status:'PROCESSING'});}));
 for(const value of [120,300,600])await api.plan(session,undefined,'once-'+value,undefined,value);
 assert.deepEqual(writes.map(x=>x.budget),[{timeout_seconds:120},{timeout_seconds:300},{timeout_seconds:600}]);assert.ok(writes.every(x=>x.expected_revision===4&&Object.keys(x.budget).length===1));
 await api.plan(session,undefined,'legacy');assert.equal(writes.at(-1).budget,undefined);
 for(const value of [0,1,119,301,601,'600',NaN])assert.throws(()=>api.plan(session,undefined,'invalid',undefined,value),/等待预算/);
 assert.equal(writes.length,4);
});
test('actual App callback freezes selected 600 before asynchronous bootstrap and field mutation',async()=>{
 let release;const boot=new Promise(done=>{release=done;}),writes=[];
 const client=new actualApi.ApiClient(async(url,options)=>{if(url==='/api/bootstrap'){await boot;return reply({csrf_token:'fixture'});}writes.push(JSON.parse(options.body));return reply({status:'PROCESSING',operation_id:'new-plan-once'});});
 const c=controller(client),input={timeoutSeconds:600},pending=c.run({data:session},input);input.timeoutSeconds=120;release();await pending;
 assert.equal(writes.length,1);assert.equal(writes[0].budget.timeout_seconds,600);assert.deepEqual(c.observed,[['p','work','new-plan-once',{basisRevision:4,discoveryPurpose:'PLAN'}]]);
 const bad=controller(client);await assert.rejects(()=>bad.run({data:session},{timeoutSeconds:601}),/等待预算/);assert.equal(bad.observed.length,0);assert.equal(writes.length,1);
});
test('lost planning POST is not reissued; explicit inspect reads exact original operation',async()=>{
 const requests=[],client=new actualApi.ApiClient(async(url,options)=>{if(url==='/api/bootstrap')return reply({csrf_token:'fixture'});requests.push({url,method:options.method});if(options.method==='POST')throw Error('fixture lost response');return reply({operation_id:'new-plan-once',session_id:'work',kind:'PLAN',basis_revision:4,status:'UNKNOWN_OUTCOME',events:[]});}),c=controller(client);
 assert.equal(await c.run({data:session},{timeoutSeconds:300}),undefined);assert.equal(c.state().active.status,'UNKNOWN_OUTCOME');assert.equal(c.state().active.discoveryPurpose,'PLAN');assert.equal(await c.run({data:session},{timeoutSeconds:300}),undefined);assert.deepEqual(requests,[{url:'/api/projects/p/discovery/sessions/work/plan',method:'POST'},{url:'/api/projects/p/discovery/operations/new-plan-once',method:'GET'}]);
});
test('this planning POST does not automatically resend on unauthorized response',async()=>{
 const calls=[],api=new DiscoveryApi(new actualApi.ApiClient(async(url,options)=>{calls.push({url,method:options.method??'GET'});return url==='/api/bootstrap'?reply({csrf_token:'fixture'}):reply({error:{code:'UNAUTHORIZED',message:'fixture reject'}},401);}));
 await assert.rejects(()=>api.plan(session,undefined,'not-reposted',undefined,300),error=>error.status===401);assert.deepEqual(calls,[{url:'/api/bootstrap',method:'GET'},{url:'/api/projects/p/discovery/sessions/work/plan',method:'POST'}]);
});
test('the App UI action defaults to five minutes without changing legacy client defaults',async()=>{
 const writes=[],client=new actualApi.ApiClient(async(url,options)=>{if(url==='/api/bootstrap')return reply({csrf_token:'fixture'});writes.push(JSON.parse(options.body));return reply({status:'PROCESSING'});});
 await controller(client).run({data:session},{});assert.equal(writes.length,1);assert.deepEqual(writes[0].budget,{timeout_seconds:300});
});

test('a local recovery save failure prevents planning POST and keeps the scoped original input',async()=>{
 const writes=[],client=new actualApi.ApiClient(async(url,options)=>{writes.push(url);return reply({status:'PROCESSING'});}),c=controller(client,{saveFails:true});assert.equal(await c.run({data:session},{timeoutSeconds:300}),undefined);assert.deepEqual(writes,[]);assert.equal(c.state().scopedError.sessionId,'work');
});

const completed={session:{...session,revision:5,sha256:'b'.repeat(64)},proposal:{id:'proposal',revision:1,sha256:'c'.repeat(64)}};
test('successful plan stays known and returns the saved proposal despite local cleanup/display/panel failures',async()=>{
 for(const options of [{cleanupFails:true},{displayFails:true},{inspectFails:true}]){
  const requests=[],client=new actualApi.ApiClient(async(url,init)=>{if(url==='/api/bootstrap')return reply({csrf_token:'fixture'});requests.push({url,method:init.method});return reply(completed);}),c=controller(client,options);
  assert.deepEqual(await c.run({data:session},{}),completed);const state=c.state();assert.equal(state.active,undefined);assert.deepEqual(state.saved,[completed.session]);assert.equal(requests.length,1);assert.equal(requests[0].method,'POST');assert.ok(state.scopedError);
 }
});
test('lost plan response uses the exact completed GET and preserves known success when cleanup fails',async()=>{
 const requests=[],client=new actualApi.ApiClient(async(url,init)=>{if(url==='/api/bootstrap')return reply({csrf_token:'fixture'});requests.push({url,method:init.method});if(init.method==='POST')throw Error('response lost');return reply({operation_id:'new-plan-once',session_id:'work',kind:'PLAN',basis_revision:4,status:'COMPLETED',result:completed,events:[]});}),c=controller(client,{cleanupFails:true});
 assert.deepEqual(await c.run({data:session},{}),completed);const state=c.state();assert.equal(state.active,undefined);assert.deepEqual(state.saved,[completed.session]);assert.equal(requests.length,2);assert.deepEqual(requests.map(x=>x.method),['POST','GET']);assert.ok(state.scopedError);assert.ok(state.storage.has('eeas-discovery-operation:p'));
});
