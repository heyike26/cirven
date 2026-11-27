import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {dirname,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {DeliveryHistoryRecovery,deliveryFeedItem,loadedDeliveryTargets,selectedDeliverySession,upsertDeliveryFeed} from '../src/delivery-recovery.ts';
import {deliveryComplete} from '../src/planning-delivery.ts';
import {sameNativeReference} from '../src/native-collaboration.ts';
const app=resolve(dirname(fileURLToPath(import.meta.url)),'..'),require=createRequire(resolve(app,'package.json')),ts=require('typescript');
const solid=await import(pathToFileURL(resolve(app,'node_modules/solid-js/dist/solid.js')).href),H='a'.repeat(64),basis={id:'guide-session',revision:2,sha256:H};
const source=readFileSync(process.env.CIRVEN_DELIVERY_APP_SOURCE??resolve(app,'src/App.tsx'),'utf8'),tree=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
let refresh;function visit(node){if(ts.isFunctionDeclaration(node)&&node.name?.text==='refreshHistory')refresh=node.getText(tree);ts.forEachChild(node,visit);}visit(tree);assert.ok(refresh);
const start=source.indexOf('  const [readingDeliveryReady,setReadingDeliveryReady]=createSignal'),end=source.indexOf('  onCleanup(()=>deliveryRecovery.stop());',start)+'  onCleanup(()=>deliveryRecovery.stop());'.length;
const recoveryBlock=source.slice(start,end),record=source.split('\n').find(line=>line.trimStart().startsWith('function recordDeliveryFeed('));assert.ok(record);
const code=ts.transpileModule(`${record}\n${recoveryBlock}\n${refresh}\nreturn {refreshHistory,clearProjectFeed,inspectDeliveryFeedback,stop:()=>deliveryRecovery.stop()};`,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText;
const settle=()=>new Promise(done=>setTimeout(done,0));
function environment(t,Recovery=DeliveryHistoryRecovery){
 const [project,setProject]=solid.createSignal({id:'p'}),[session,setSession]=solid.createSignal({project_id:'p',...basis}),[feed,setFeed]=solid.createSignal([]),[error,setError]=solid.createSignal('');let entries=[],reads=0,directoryGate,operationGate,operationError,operationBasis=basis,dispose,navigationCurrent=true;
 const receipt=key=>({operation_id:key,status:'STAGE_REPORT',session_ref:basis,pending_count:1,blocking_count:0,report:{artifact:{id:H,filename:'guide.docx',sha256:H}},diagrams:[],artifacts:[],project_files:[],file_checks:[],basis_current:true,current_review_status:'CURRENT',execution_authorized:false});
 const api={request:async()=>({items:[],nextCursor:null,partial:false,sourceUnavailable:false})},planningDeliveryApi={sessionOperations:async()=>{reads++;if(directoryGate)await directoryGate;return {entries:entries.map(operation_id=>({operation_id,session_ref:basis}))};},operation:async(pid,key)=>{if(operationGate)await operationGate;if(operationError)throw operationError;return {...receipt(key),session_ref:operationBasis};}};
 const noop=()=>{},bindings={conversationReading:{update(){}},...solid,DeliveryHistoryRecovery:Recovery,deliveryFeedItem,deliveryComplete,sameNativeReference,loadedDeliveryTargets,selectedDeliverySession,upsertDeliveryFeed,project,activeSession:session,toolMode:()=> 'discovery',discoverySessions:()=>[session()],proposals:()=>[],inspector:()=>undefined,localStorage:{getItem:()=>null},feed,setFeed,setError,setReports:noop,uiLocale:()=> 'zh-CN',navigation:{capture:()=>()=>navigationCurrent},planningDeliveryApi,api,parseHistoryPage:v=>v,labelHistoryItems:v=>v,mergeHistoryItems:(a,b)=>[...a,...b],historyDirectoryRefresh:{setBusy:noop},historyLoading:()=>false,historyLoadingMore:()=>false,historyCursor:()=>null,historyCursors:new Set(),setHistoryLoading:noop,setHistoryLoadingMore:noop,setHistoryError:noop,setHistoryItems:noop,setHistoryCursor:noop,setHistoryPartial:noop,setHistoryLoaded:noop,studioDisposed:false,historyDirectoryRequest:0,historyDirectoryAbort:undefined,historySourceUnavailable:false};
 let instance;solid.createRoot(stop=>{dispose=stop;instance=new Function(...Object.keys(bindings),code)(...Object.values(bindings));});
 t.after(()=>dispose());return {instance,feed,error,setSession,setProject,setEntries:value=>entries=value,reads:()=>reads,hold:value=>directoryGate=value,invalidate:()=>navigationCurrent=false,holdOperation:value=>operationGate=value,failOperation:value=>operationError=value,setOperationBasis:value=>operationBasis=value};
}

test('actual App history refresh rechecks an unchanged selected discussion, and a cleared/reselected view restores the same original key',async t=>{
 const s=environment(t);await settle();assert.equal(s.feed().length,0);s.setEntries(['api-stage']);await s.instance.refreshHistory();await settle();assert.equal(s.feed().length,1);assert.equal(s.feed()[0].id,'delivery:p:api-stage');s.instance.clearProjectFeed();s.setSession({...basis,project_id:'p'});await settle();assert.equal(s.feed().length,1);assert.equal(s.feed()[0].artifact.data.operation_id,'api-stage');assert.equal(s.error(),'');assert.equal(s.reads(),3);
});

test('actual App scope effect ignores late previous-session directory and does not put it into another conversation',async t=>{
 const s=environment(t);await settle();let release;s.hold(new Promise(done=>release=done));s.setEntries(['old-stage']);const pending=s.instance.refreshHistory();await settle();s.setEntries([]);s.setSession({...basis,id:'other-session',project_id:'p'});release();await pending;await settle();assert.equal(s.feed().length,0);assert.equal(s.error(),'');
});


test('actual App error publication rejects late same-session old-revision failures and invalid navigation leases',async t=>{
 const pending=[];class Recovery{constructor(){}restore(targets){return new Promise((resolve,reject)=>pending.push({targets,resolve,reject}));}resetView(){}stop(){for(const item of pending)item.resolve();}}
 const s=environment(t,Recovery);await settle();assert.equal(pending.length,1);s.setSession({...basis,project_id:'p',revision:3,sha256:'f'.repeat(64)});await settle();assert.equal(pending.length,2);pending[0].reject(new Error('old directory failure'));await settle();assert.equal(s.error(),'');s.invalidate();pending[1].reject(new Error('abandoned navigation failure'));await settle();assert.equal(s.error(),'');
});


test('actual App manual inspection binds the selected view fullref, permits historical cards and suppresses late result/error across same-session revision changes',async t=>{
 for(const fail of [false,true]){
  const s=environment(t);await settle();const historical={...basis,revision:1,sha256:'e'.repeat(64)},item={deliveryTarget:{projectId:'p',kind:'STAGE',basis:historical},deliveryInspection:{operationId:'historical-stage',diagnostic:'saved original key'}};s.setOperationBasis(historical);
  await s.instance.inspectDeliveryFeedback(item);assert.equal(s.feed().length,1);assert.deepEqual(s.feed()[0].artifact.data.session_ref,historical);s.instance.clearProjectFeed();
  let release,releaseDirectory;s.holdOperation(new Promise(done=>release=done));const pending=s.instance.inspectDeliveryFeedback(item);await settle();s.hold(new Promise(done=>releaseDirectory=done));s.setSession({...basis,project_id:'p',revision:3,sha256:'f'.repeat(64)});await settle();if(fail)s.failOperation(new Error('old manual GET failure'));release();await assert.rejects(pending);assert.equal(s.feed().length,0,'late manual success/error cannot publish while new directory is still pending');assert.equal(s.error(),'');releaseDirectory();await settle();
 }
});
