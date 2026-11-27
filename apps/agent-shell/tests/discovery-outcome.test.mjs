import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
import {operationStatus,operationRunning} from '../src/operation-outcome.ts';
import {discoveryInput,readDiscoveryPending,saveDiscoveryPending,clearDiscoveryPending,discoverySendGate} from '../src/discovery-pending.ts';

const source=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8');
const ast=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const names=['conversationReadingState','readingScopeIdentity','readingScopeCurrent','conversationReadingScope','followDiscoveryReading','presentDiscoveryResult','releaseDiscoveryOperation','restoreDiscoveryOperation','stopObserver','applyDiscoveryObservation','inspectDiscoveryOperation','observeDiscovery','advanceDiscovery'];
const functions=new Map();
function visit(node){if(ts.isFunctionDeclaration(node)&&names.includes(node.name?.text))functions.set(node.name.text,node.getText(ast));ts.forEachChild(node,visit);}visit(ast);
assert.equal(functions.size,names.length,'Exercise the actual App callbacks');
const code=ts.transpileModule([...functions.values()].join('\n'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.None}}).outputText;
const initial={id:'work',project_id:'p',revision:1,sha256:'a'.repeat(64),payload:{candidate:{summary:'Original scope'}}};
const unknown={operation_id:'one',session_id:'work',basis_revision:1,kind:'ADVANCE',status:'FAILED',native_execution:{id:'original-owner',execution_status:'UNKNOWN_OUTCOME'},events:[]};
function fixture(outcome=unknown,firstProcessing=true,options={}){
 let active={id:'one',projectId:'p',sessionId:'work',kind:'discovery',basisRevision:1,status:'PROCESSING',execution:{execution_status:'RUNNING'}},pid='p',posts=0,gets=[],saved=[],errors=[],notices=[],timers=new Set(),failRead=false,failSave=false,failCleanup=false,scopedError;
 const storage=new Map([['eeas-discovery-operation:p',JSON.stringify({id:'one',sessionId:'work'})]]);
 const reading=[];let waitSeconds=120;
 const context={studioDisposed:false,threadScope:()=>undefined,shell:undefined,conversationReading:{follow:(...args)=>reading.push(args)},batch:fn=>fn(),operationStatus,discoveryInput,readDiscoveryPending,saveDiscoveryPending,clearDiscoveryPending,discoverySendGate,AbortController,prompt:()=> 'Original message',draftAttachments:()=>[],setDiscoveryError:value=>{scopedError=value;},activeSession:()=>initial,project:()=>({id:pid}),activeOperation:()=>active,setActiveOperation:value=>{active=typeof value==='function'?value(active):value;},
  rememberSession:value=>{saved.push(value);if(options.failRemember)throw Error('session display unavailable');},setNotice:v=>notices.push(v),setError:value=>errors.push(value),localStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>{if(failSave)throw Error('quota');storage.set(k,v);},removeItem:k=>{if(failCleanup)throw Error('cleanup unavailable');storage.delete(k);}},
  setTimeout:fn=>{timers.add(fn);return fn;},clearTimeout:fn=>timers.delete(fn),ApiError:class ApiError extends Error{},authoredErrorCopy:e=>String(e),
  discoveryWaitSeconds:()=>waitSeconds,workspace:()=>({id:'directory'}),navigation:{capture:()=>()=>true},operationId:()=> 'one',materials:()=>[],providers:()=>[],providerId:()=>'',refreshObjects:async()=>{},
  discoveryApi:{operation:async(p,key)=>{gets.push([p,key]);if(failRead)throw Error('read unavailable');return firstProcessing&&gets.length===1?{...unknown,status:'PROCESSING',native_execution:{execution_status:'RUNNING'}}:outcome;},advance:async()=>{posts++;if(options.postResult)return options.postResult;throw Error('lost POST response');}}};
 const api=new Function(...Object.keys(context),`let operationObserver,operationTimer;${code};return {followDiscoveryReading,restoreDiscoveryOperation,applyDiscoveryObservation,inspectDiscoveryOperation,observeDiscovery,advanceDiscovery};`)(...Object.values(context));
 return {api,changeWait:value=>waitSeconds=value,state:()=>({active,posts,gets,saved,errors,notices,scopedError,timers,storage,reading}),failCleanup:()=>{failCleanup=true;},failSave:()=>{failSave=true;},failRead:()=>{failRead=true;},loseMarker:()=>{active=undefined;storage.clear();},navigate:()=>{pid='elsewhere';active={id:'other',projectId:pid,status:'RUNNING'};}};
}

test('terminal parent outranks stale RUNNING; native uncertainty corrects legacy FAILED',()=>{
 assert.equal(operationStatus({status:'FAILED',execution:{execution_status:'RUNNING'}}),'FAILED');
 for(const execution_status of ['UNKNOWN_OUTCOME','TIMED_OUT','INTERRUPTED']){
  const value={status:'FAILED',execution:{execution_status}};assert.equal(operationStatus(value),'UNKNOWN_OUTCOME');assert.equal(operationRunning(value),false);
 }
 assert.equal(operationStatus({status:'COMPLETED',execution:{execution_status:'RUNNING'}}),'COMPLETED');
 assert.equal(operationRunning({status:'PROCESSING',execution:{execution_status:'RUNNING'}}),true);
});

test('actual advance catch reads original key, stops polling and retains unknown key without resending',async()=>{
 const f=fixture();assert.equal(await f.api.advanceDiscovery(initial,'Original message'),undefined);
 const s=f.state();assert.equal(s.posts,1);assert.deepEqual(s.gets,[['p','one'],['p','one']]);assert.equal(s.active.status,'UNKNOWN_OUTCOME');assert.equal(s.active.execution.execution_status,'UNKNOWN_OUTCOME');assert.equal(s.timers.size,0);assert.ok(s.storage.has('eeas-discovery-operation:p'));assert.equal(s.saved.length,0);
});

test('failed read preserves uncertainty and original key, no polling or second POST',async()=>{
 const f=fixture();f.failRead();assert.equal(await f.api.advanceDiscovery(initial,'Original message'),undefined);
 const s=f.state();assert.equal(s.active.status,'UNKNOWN_OUTCOME');assert.equal(operationRunning(s.active),false);assert.equal(s.posts,1);assert.equal(s.timers.size,0);assert.ok(s.storage.has('eeas-discovery-operation:p'));
});

test('completion found by exact-key check returns the actual result and clears only that pending key',async()=>{
 const f=fixture({...unknown,status:'COMPLETED',native_execution:{execution_status:'RETURNED'},result:initial});
 assert.deepEqual(await f.api.advanceDiscovery(initial,'Original message'),initial);
 const s=f.state();assert.equal(s.posts,1);assert.equal(s.active,undefined);assert.deepEqual(s.saved,[initial]);assert.equal(s.storage.has('eeas-discovery-operation:p'),false);assert.equal(s.timers.size,0);
});

test('late old observation does not replace a different project/operation',()=>{
 const f=fixture();f.navigate();assert.equal(f.api.applyDiscoveryObservation('p','work','one',unknown),false);
 const s=f.state();assert.equal(s.active.id,'other');assert.equal(s.active.projectId,'elsewhere');assert.equal(s.saved.length,0);assert.equal(s.gets.length,0);assert.ok(s.storage.has('eeas-discovery-operation:p'));
});

test('restart observer performs read-only terminal check, preserves key and releases its timer',async()=>{
 const f=fixture();await f.api.inspectDiscoveryOperation('p','work','one');f.api.observeDiscovery('p','work','one');await new Promise(resolve=>setImmediate(resolve));
 const s=f.state();assert.equal(s.posts,0);assert.equal(s.active.status,'UNKNOWN_OUTCOME');assert.equal(s.timers.size,0);assert.ok(s.storage.has('eeas-discovery-operation:p'));
});


test('lost marker is restored only from exact current session, without model dispatch',async()=>{
 const f=fixture(unknown,false);f.loseMarker();f.api.restoreDiscoveryOperation('p','work',unknown);await new Promise(resolve=>setImmediate(resolve));
 const s=f.state();assert.equal(s.posts,0);assert.deepEqual(s.gets,[['p','one']]);assert.equal(s.active.id,'one');assert.equal(s.active.status,'UNKNOWN_OUTCOME');assert.equal(s.timers.size,0);assert.deepEqual(JSON.parse(s.storage.get('eeas-discovery-operation:p')),{id:'one',sessionId:'work',basisRevision:1,retainedInput:discoveryInput('Original message',[],[])});assert.deepEqual(s.errors,[]);
});

test('recovery rejects another session or project and never interrupts an existing operation',async()=>{
 const f=fixture(unknown,false);f.api.restoreDiscoveryOperation('p','work',unknown);assert.equal(f.state().gets.length,0);f.loseMarker();
 f.api.restoreDiscoveryOperation('elsewhere','work',unknown);f.api.restoreDiscoveryOperation('p','other',unknown);f.api.restoreDiscoveryOperation('p','work',{...unknown,session_id:'other'});
 assert.equal(f.state().gets.length,0);assert.equal(f.state().posts,0);assert.equal(f.state().active,undefined);assert.equal(f.state().storage.size,0);
});


test('a unique old-basis operation must not automatically occupy a newer session version',async()=>{
 const previous=initial.revision;initial.revision=2;
 try{const f=fixture(unknown,false);f.loseMarker();f.api.restoreDiscoveryOperation('p','work',unknown);await new Promise(resolve=>setImmediate(resolve));const s=f.state();assert.equal(s.active,undefined);assert.equal(s.gets.length,0);assert.equal(s.saved.length,0);assert.equal(s.posts,0);}finally{initial.revision=previous;}
});

test('late old-basis observation cannot replace the newer current version',()=>{
 const previous=initial.revision;initial.revision=2;
 try{const f=fixture(unknown,false);assert.equal(f.api.applyDiscoveryObservation('p','work','one',unknown),false);const s=f.state();assert.equal(s.active,undefined);assert.equal(s.saved.length,0);assert.equal(s.errors.length,0);}finally{initial.revision=previous;}
});

test('explicit historical inspection reads old key but cannot adopt its completed result',async()=>{
 const previous=initial.revision;initial.revision=2;
 try{const f=fixture({...unknown,status:'COMPLETED',result:{...initial,revision:1}},false);f.loseMarker();f.api.restoreDiscoveryOperation('p','work',unknown,true);await new Promise(resolve=>setImmediate(resolve));const s=f.state();assert.equal(s.posts,0);assert.deepEqual(s.gets,[['p','one']]);assert.deepEqual(s.saved,[]);assert.equal(s.active,undefined);}finally{initial.revision=previous;}
});

test('local save failure prevents dispatch and retains the original pending bytes',async()=>{
 const f=fixture();const before=[...f.state().storage];f.failSave();assert.equal(await f.api.advanceDiscovery(initial,'Original message'),undefined);const s=f.state();assert.equal(s.posts,0);assert.equal(s.gets.length,0);assert.deepEqual([...s.storage],before);assert.equal(s.scopedError.sessionId,'work');assert.deepEqual(s.errors,[]);
});

test('late same-key receipt with another session identity is rejected',()=>{
 const f=fixture();assert.equal(f.api.applyDiscoveryObservation('p','work','one',{...unknown,session_id:'elsewhere'}),false);assert.equal(f.state().active.status,'PROCESSING');assert.equal(f.state().saved.length,0);
});

test('known completed exact-key GET remains successful when local marker cleanup fails',async()=>{
 const f=fixture({...unknown,status:'COMPLETED',result:initial},false);f.failCleanup();
 const result=await f.api.inspectDiscoveryOperation('p','work','one');assert.equal(result.status,'COMPLETED');const s=f.state();assert.equal(s.active,undefined);assert.deepEqual(s.saved,[initial]);assert.equal(s.posts,0);assert.equal(s.gets.length,1);assert.ok(s.storage.has('eeas-discovery-operation:p'));assert.equal(s.timers.size,0);assert.deepEqual(s.errors,[]);assert.ok(s.scopedError);
});
test('a successful POST is adopted without another GET when local cleanup fails',async()=>{
 const f=fixture({...unknown,status:'COMPLETED',result:initial},true,{postResult:initial});f.failCleanup();assert.deepEqual(await f.api.advanceDiscovery(initial,'Original message'),initial);const s=f.state();assert.equal(s.active,undefined);assert.deepEqual(s.saved,[initial]);assert.equal(s.posts,1);assert.equal(s.gets.length,1);assert.equal(s.timers.size,0);assert.ok(s.storage.has('eeas-discovery-operation:p'));assert.deepEqual(s.errors,[]);
});
test('a completed readback after a lost POST is adopted despite local cleanup failure',async()=>{
 const f=fixture({...unknown,status:'COMPLETED',result:initial});f.failCleanup();assert.deepEqual(await f.api.advanceDiscovery(initial,'Original message'),initial);const s=f.state();assert.equal(s.active,undefined);assert.deepEqual(s.saved,[initial]);assert.equal(s.posts,1);assert.equal(s.gets.length,2);assert.equal(s.timers.size,0);assert.deepEqual(s.errors,[]);
});
test('old-basis cleanup failure releases only the old observer without adopting old results',()=>{
 const previous=initial.revision;initial.revision=2;
 try{const f=fixture(unknown,false);f.failCleanup();assert.equal(f.api.applyDiscoveryObservation('p','work','one',unknown),false);const s=f.state();assert.equal(s.active,undefined);assert.deepEqual(s.saved,[]);assert.equal(s.posts,0);assert.ok(s.storage.has('eeas-discovery-operation:p'));assert.equal(s.scopedError.sessionId,'work');}finally{initial.revision=previous;}
});

test('known completed result survives local display/index refresh failure without extra model or GET',async()=>{
 const f=fixture({...unknown,status:'COMPLETED',result:initial},true,{postResult:initial,failRemember:true});assert.deepEqual(await f.api.advanceDiscovery(initial,'Original message'),initial);const s=f.state();assert.equal(s.active,undefined);assert.deepEqual(s.saved,[initial]);assert.equal(s.posts,1);assert.equal(s.gets.length,1);assert.equal(s.timers.size,0);assert.ok(s.scopedError);assert.deepEqual(s.errors,[]);
});


test('explicit discovery dispatch follows its exact discussion and operation; checks do not start a new reading intent',async()=>{
 const f=fixture();await f.api.advanceDiscovery(initial,'Original message');const s=f.state();assert.equal(s.reading.length,1);const [id,key,current,target]=s.reading[0];assert.equal(id,JSON.stringify(['discovery','p','work']));assert.equal(key,'one');assert.equal(current(),true);assert.equal(target(),undefined);await f.api.inspectDiscoveryOperation('p','work','one');assert.equal(f.state().reading.length,1);f.navigate();assert.equal(current(),false);
});
test('local persistence rejection never starts following or sends a request',async()=>{
 const f=fixture();f.failSave();await f.api.advanceDiscovery(initial,'Original message');assert.equal(f.state().posts,0);assert.equal(f.state().reading.length,0);
});
test('a different visible discovery discussion cannot acquire the old send reading target',()=>{
 const f=fixture();f.api.followDiscoveryReading({...initial,id:'another'},'old-op',()=>true);assert.equal(f.state().reading.length,0);
});

test('changing the future wait preference alone cannot replay the original unknown discovery input',async()=>{
 const f=fixture();await f.api.advanceDiscovery(initial,'Original message');assert.equal(f.state().posts,1);assert.equal(f.state().active.status,'UNKNOWN_OUTCOME');f.changeWait(600);await f.api.advanceDiscovery(initial,'Original message');assert.equal(f.state().posts,1);assert.ok(f.state().storage.has('eeas-discovery-operation:p'));
});
