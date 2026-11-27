import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdtemp, rm } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import test from 'node:test';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';
import { renderToString } from 'solid-js/web';
import { NativeAppendClient, NativeAppendRequests, NATIVE_APPEND_HISTORY_KEY, NATIVE_APPEND_SCOPE, appendActionRequest, appendChatRequest, appendActions, appendPrepare, appendResultLabel, appendSelection, appendTakeoverNotice, assertAppendState, readAppendHistory } from '../src/native-append.ts';

const app=resolve(dirname(fileURLToPath(import.meta.url)),'..'),root=resolve(app,'../..');
const ref=(id,letter='a')=>({id,revision:1,sha256:letter.repeat(64)}),context=ref('actual-context'),provider=ref('provider','b'),preview=ref('preview','c'),approval=ref('approval','d'),qualification=ref('qualification','e');
const sha=value=>createHash('sha256').update(value).digest('hex');
const raw='请在当前记事本末尾追加：原创中文🙂',quote='原创中文🙂';
const input=key=>appendPrepare(context,raw,appendSelection(raw,raw.indexOf(quote),raw.length),provider,key);
function storage(){const values=new Map();return{getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),values};}
function receipt(operationId,state='PREPARED',extra={}){return{adapter_id:'classic_notepad_append',state,scope:NATIVE_APPEND_SCOPE,automatic_retry:false,foreground_required:true,no_selection_overwrite:true,no_save_action:true,native_input_global_enabled:false,input_epoch:7,expected_intent_epoch:8,task_operation_id:operationId,preview_ref:preview,qualification_ref:qualification,payload_text:quote,payload_sha256:sha(quote),target_label:'原创测试记事本',provider_state:'PROFILE_SELECTED_UNVALIDATED',permission_state:'NONE',activation_state:'HANDOFF_REQUIRED',task_completed:false,...extra};}
// This stand-in checks the Main bridge boundary and dispatch journal only. It does
// not execute or qualify Notepad, model calls, Win32 handoff, SDK input or cancellation.
function protocol(local=storage()){
 const calls=[],states=new Map();
 const bridge={getNativeAppend:async(contextRef,operationId)=>{calls.push({method:'get',contextRef,operationId});return operationId?states.get(operationId)??{status:'NO_RECORD',error:{code:'NOT_FOUND'}}:{adapter_id:'classic_notepad_append',state:'NOT_ADAPTED',reason:'PROTOCOL_SEAM_ONLY',automatic_retry:false,expected_intent_epoch:8};},prepareNativeAppend:async body=>{assert.ok(readAppendHistory(local).some(op=>op.operationId===body.operation_id&&op.actions.includes('PREPARE')));calls.push({method:'prepare',body});const result=receipt(body.operation_id);states.set(body.operation_id,result);return result;},approveNativeAppend:async body=>{assert.ok(readAppendHistory(local).find(op=>op.operationId===body.operationId).actions.includes('APPROVE'));calls.push({method:'approve',body});const result=receipt(body.operationId,'RUNNING',{approval_ref:approval,permission_state:'CONSUMED'});states.set(body.operationId,result);return result;},startNativeAppend:async body=>{assert.ok(readAppendHistory(local).find(op=>op.operationId===body.operationId).actions.includes('START'));calls.push({method:'start',body});const result=receipt(body.operationId,'RUNNING',{approval_ref:approval});states.set(body.operationId,result);return result;},revokeNativeAppend:async body=>{assert.ok(readAppendHistory(local).find(op=>op.operationId===body.operationId).kind==='REVOKE');calls.push({method:'revoke',body});return{status:'REVOKED',mutation_authority:false,cancellation:{confirmed:false,status:'UNKNOWN_OUTCOME'}};}};
 return{local,calls,states,bridge,client:new NativeAppendClient(bridge,local)};
}

test('user selection converts UTF-16 offsets to exact Python codepoints without cutting surrogate pairs or widening text',()=>{
 const text='先🙂后追加中文😀终',start=text.indexOf('中文'),end=text.indexOf('终'),selection=appendSelection(text,start,end);assert.deepEqual(selection,{start:5,end:8,quote:'中文😀'});
 for(const [start,end] of [[2,3],[1,2],[-1,3],[0,0],[0,99],[true,3]])assert.throws(()=>appendSelection(text,start,end));
 assert.equal(appendSelection('😀'.repeat(128),0,256).quote.length,256);assert.throws(()=>appendSelection('😀'.repeat(129),0,258),/256/);assert.equal(appendSelection('中'.repeat(256),0,256).quote.length,256);assert.throws(()=>appendSelection('中'.repeat(257),0,257),/256/);
 for(const text of ['追加\n文本','追加\t文本','追加\u007f文本','追加\ud800'])assert.throws(()=>appendSelection(text,0,text.length));
 assert.throws(()=>appendPrepare(context,raw,{start:0,end:1,quote},provider,'op'),/选文/);assert.throws(()=>appendPrepare(context,'a'.repeat(16001),{start:0,end:1,quote:'a'},provider,'op'),/16000/);
});

test('actual Pydantic append schema accepts the generated codepoint body and rejects renderer approval flags, boolean epochs and added target fields',()=>{
 const body=input('actual-schema-check'),script=`import json,sys\nfrom apps.api.native_append import AppendPrepareInput,AppendApproveInput,AppendStartInput\nfrom pydantic import ValidationError\nx=json.load(sys.stdin)\nactual=AppendPrepareInput.model_validate(x['prepare']).model_dump()\nassert actual['raw_text'][actual['payload_source']['span']['start']:actual['payload_source']['span']['end']]==actual['payload_source']['span']['quote']\nrejected=0\nfor model,value in [(AppendPrepareInput,{**x['prepare'],'window_id':100}),(AppendApproveInput,x['renderer_approve']),(AppendApproveInput,x['boolean_epoch']),(AppendStartInput,{**x['server_start'],'approved':True})]:\n try: model.model_validate(value)\n except ValidationError: rejected+=1\nassert rejected==4\nprint(json.dumps({'prepare':actual,'rejected':rejected},ensure_ascii=False))`;
 const result=spawnSync(resolve(root,'.venv/bin/python'),['-c',script],{cwd:root,input:JSON.stringify({prepare:body,renderer_approve:{operationId:body.operation_id,previewRef:preview,approved:true},boolean_epoch:{operation_id:body.operation_id,preview_ref:preview,expected_intent_epoch:true,expected_input_epoch:7,main_gesture_ref:ref('trusted-main','f')},server_start:{operation_id:body.operation_id,preview_ref:preview,approval_ref:approval}}),encoding:'utf8'});assert.equal(result.status,0,result.stderr);const actual=JSON.parse(result.stdout);assert.deepEqual(actual.prepare,body);assert.equal(actual.rejected,4);
});

test('prewrite failure prevents dispatch; saved metadata excludes raw text, quote and target primitives',async()=>{
 const local=storage(),p=protocol(local),body=input('prewrite');local.setItem=()=>{throw new Error('storage blocked');};const client=new NativeAppendClient(p.bridge,local);await assert.rejects(client.prepare(body),/storage blocked/);assert.equal(p.calls.length,0);
 const q=protocol();await q.client.prepare(input('metadata'));const serialized=q.local.getItem(NATIVE_APPEND_HISTORY_KEY);assert.ok(!serialized.includes(raw)&&!serialized.includes(quote));for(const field of ['raw_text','payload_text','quote','window_id','pid','approved','gesture'])assert.ok(!serialized.includes('"'+field+'"'));assert.equal(readAppendHistory(q.local)[0].payloadSha256,sha(quote));
});

test('one live explicit approval calls only Main with op/preview; approval already running never creates a second start',async()=>{
 const p=protocol(),prepared=await p.client.prepare(input('approve-once'));assert.equal(appendActions(prepared,context).approve,true);const running=await p.client.approve(prepared,context);assert.equal(running.snapshot.state,'RUNNING');assert.deepEqual(p.calls.find(call=>call.method==='approve').body,{operationId:'approve-once',previewRef:preview});assert.deepEqual(appendActions(running,context),{approve:false,start:false});await p.client.approve(prepared,context);assert.equal(p.calls.filter(call=>call.method==='approve').length,1);assert.equal(p.calls.filter(call=>call.method==='start').length,0);assert.equal(p.calls.at(-1).method,'get');
});

test('failed handoff never reuses approval or starts old input; explicit new preview has a different operation and observed baseline',async()=>{
 const p=protocol();p.bridge.approveNativeAppend=async body=>{p.calls.push({method:'approve',body});const result=receipt(body.operationId,'HANDOFF_REQUIRED',{approval_ref:approval});p.states.set(body.operationId,result);return result;};const prepared=await p.client.prepare(input('handoff')),handoff=await p.client.approve(prepared,context);assert.equal(appendActions(handoff,context).start,false);await p.client.start(handoff);await p.client.start(handoff);assert.equal(p.calls.filter(call=>call.method==='start').length,0);await p.client.prepare(input('handoff-new-explicit-preview'));assert.equal(p.calls.filter(call=>call.method==='prepare').length,2);assert.equal(p.calls.filter(call=>call.method==='approve').length,1);
 for(const state of ['UNKNOWN','UNKNOWN_OUTCOME','PARTIAL','BASE_CHANGED','NOT_CONFIGURED','HISTORICAL_READONLY','REVOKED','APPROVAL_CANCELLED'])assert.deepEqual(appendActions({...handoff,snapshot:{...handoff.snapshot,state}},context),{approve:false,start:false});assert.deepEqual(appendActions(handoff,ref('other-context')),{approve:false,start:false});assert.deepEqual(appendActions({...handoff,origin:'RECOVERED'},context),{approve:false,start:false});
});

test('lost approval acknowledgement and 404 are inspect-only, preserved across restart and never evidence that an input did not occur',async()=>{
 const p=protocol(),prepared=await p.client.prepare(input('lost-ack'));p.bridge.approveNativeAppend=async body=>{p.calls.push({method:'approve',body});p.states.delete(body.operationId);throw new Error('connection lost after request');};const uncertain=await p.client.approve(prepared,context);assert.equal(uncertain.review,'NO_RECORD');assert.match(uncertain.message,/不证明没有执行/);assert.equal(uncertain.origin,'RECOVERED');await p.client.approve(prepared,context);const restarted=new NativeAppendClient(p.bridge,p.local),history=await restarted.restore();assert.equal(history.length,1);assert.equal(history[0].review,'NO_RECORD');assert.deepEqual(appendActions(history[0],context),{approve:false,start:false});assert.equal(p.calls.filter(call=>call.method==='approve').length,1);assert.equal(p.calls.filter(call=>call.method==='prepare').length,1);assert.equal(p.calls.filter(call=>call.method==='start').length,0);assert.ok(readAppendHistory(p.local)[0].actions.includes('APPROVE'));
});

test('a recovered PREPARED/APPROVED record never authorizes replay; substituted operation, payload or reference cannot become live',async()=>{
 const p=protocol(),prepared=await p.client.prepare(input('history'));p.states.set('history',receipt('history','APPROVED',{approval_ref:approval}));const restored=await p.client.restore();assert.equal(restored[0].origin,'RECOVERED');await p.client.start(restored[0]);assert.equal(p.calls.filter(call=>call.method==='start').length,0);
 p.states.set('history',receipt('another-operation'));assert.equal((await p.client.inspect(prepared.operation)).review,'INSPECTION_UNAVAILABLE');p.states.set('history',receipt('history','PREPARED',{payload_text:'其它文字',payload_sha256:sha(quote)}));assert.equal((await p.client.inspect(prepared.operation)).review,'INSPECTION_UNAVAILABLE');assert.throws(()=>assertAppendState(receipt('history','PREPARED',{preview_ref:ref('swapped','f')}),prepared.operation),/预览引用/);assert.throws(()=>assertAppendState(receipt('history','PREPARED',{no_save_action:false}),prepared.operation),/范围/);
});

test('concurrent read completion cannot erase a persisted approval attempt; cancelled native dialog offers no implicit new approval',async()=>{
 const p=protocol(),prepared=await p.client.prepare(input('race'));let release;const original=p.bridge.getNativeAppend;p.bridge.getNativeAppend=async(...args)=>{await new Promise(resolve=>release=resolve);return original(...args);};const read=p.client.inspect(prepared.operation);p.bridge.approveNativeAppend=async body=>{p.calls.push({method:'approve',body});const result=receipt(body.operationId,'APPROVAL_CANCELLED',{approval_ref:null});p.states.set(body.operationId,result);return result;};const cancelled=await p.client.approve(prepared,context);release();await read;assert.ok(readAppendHistory(p.local)[0].actions.includes('APPROVE'));assert.equal(cancelled.snapshot.state,'APPROVAL_CANCELLED');assert.deepEqual(appendActions(cancelled,context),{approve:false,start:false});assert.equal(p.calls.filter(call=>call.method==='approve').length,1);
});

test('user takeover rereads the actual intent epoch, persists before revoke, and does not claim unverified cancellation',async()=>{
 const p=protocol();const result=await p.client.revoke('takeover-once');assert.equal(result.cancellation.confirmed,false);assert.deepEqual(p.calls.find(call=>call.method==='revoke').body,{operationId:'takeover-once',expectedIntentEpoch:8});await assert.rejects(p.client.revoke('takeover-once'),/已尝试/);assert.equal(p.calls.filter(call=>call.method==='revoke').length,1);
 p.bridge.getNativeAppend=async()=>({adapter_id:'classic_notepad_append',state:'NOT_ADAPTED',automatic_retry:false,expected_intent_epoch:true});await assert.rejects(p.client.revoke('bad-epoch'),/意图版本/);assert.equal(p.calls.filter(call=>call.method==='revoke').length,1);
});

test('takeover notice keeps an unknown foreground handoff separate from confirmed original SDK cancellation',()=>{
 const receipt={status:'REVOKED',mutation_authority:false,cancellation:{confirmed:true,status:'CONFIRMED'},readonly_intent_cancellation_status:'CONFIRMED',foreground_handoff_stop:{status:'OWN_HELPER_STOP_REQUESTED',owned_helpers:1,foreground_effect:'UNKNOWN'}},before=structuredClone(receipt),notice=appendTakeoverNotice(receipt);assert.match(notice,/已请求停止自有助手；焦点交接是否发生仍需核查，未自动继续追加/);assert.match(notice,/原 SDK 任务取消已确认/);assert.ok(!notice.includes('自有执行的取消已确认'));assert.deepEqual(receipt,before);
 const unresolved=appendTakeoverNotice({...receipt,readonly_intent_cancellation_status:'UNKNOWN_OUTCOME'});assert.match(unresolved,/原解释任务的取消结果仍需核查/);const none=appendTakeoverNotice({...receipt,foreground_handoff_stop:{status:'NO_OWN_HELPER_PENDING',owned_helpers:0,foreground_effect:'NOT_OBSERVED'},cancellation:{confirmed:false,status:'UNKNOWN_OUTCOME'}});assert.match(none,/原 SDK 任务是否停止仍需核查/);assert.ok(!none.includes('焦点交接是否发生'));
});

test('model/transport completion alone never becomes an append or disk-save claim',()=>{
 assert.match(appendResultLabel(receipt('goal','FINISHED',{task_completed:true})),/尚未核实/);const state=receipt('goal','FINISHED',{task_completed:true,task:{task_outcome:{task_outcome:{verification:{status:'VERIFIED',reason:'protocol location check only',evidence_refs:[ref('proof','f')]}}}}});assert.match(appendResultLabel(state),/缓冲区.*核实.*未保存/);assert.match(appendResultLabel({...state,task_completed:false}),/尚未核实/);
});

test('unadapted or missing-provider responses preserve actual reasons and extra host status; arbitrary Agent reply sources are unavailable',async()=>{
 const p=protocol();p.bridge.prepareNativeAppend=async body=>{p.calls.push({method:'prepare',body});return{adapter_id:'classic_notepad_append',state:'NOT_ADAPTED',automatic_retry:false,reason:'ACTUAL_NOTEPAD_QUALIFICATION_NOT_CONFIGURED',host_broker_status:'NOT_ADAPTED'};};const unadapted=await p.client.prepare(input('unadapted'));assert.equal(unadapted.snapshot.reason,'ACTUAL_NOTEPAD_QUALIFICATION_NOT_CONFIGURED');assert.equal(unadapted.snapshot.host_broker_status,'NOT_ADAPTED');assert.deepEqual(appendActions(unadapted,context),{approve:false,start:false});await assert.rejects(p.client.approve(unadapted,context),/只有本轮/);await assert.rejects(p.client.prepare({...input('reply-unavailable'),payload_source:{kind:'ORIGINAL_REPLY',reply_ref:ref('original-reply','f'),span:input('unused').payload_source.span}}),/可信选文/);assert.equal(p.calls.filter(call=>call.method==='prepare').length,1);assert.equal(p.calls.filter(call=>call.method==='approve').length,0);
 const missing={...unadapted,snapshot:receipt('unadapted','PREPARED',{provider_state:'NOT_CONFIGURED'})};assert.deepEqual(appendActions(missing,context),{approve:false,start:false});
});

test('explicit chat request keeps original text and exact codepoint span; model-proposed authority or altered quotes are rejected',()=>{
 const body=appendChatRequest({operationId:'chat-source',rawText:raw,contextRef:context,providerProfileRef:provider},input('unused').payload_source.span);
 assert.deepEqual(body,input('chat-source'));assert.deepEqual(appendActionRequest(body),body);
 for(const changed of [{...body,approved:true},{...body,window_id:9},{...body,payload_source:{...body.payload_source,span:{...body.payload_source.span,quote:'模型改写'}}},{...body,payload_source:{...body.payload_source,kind:'ORIGINAL_REPLY',reply_ref:ref('reply')}}])assert.throws(()=>appendActionRequest(changed));
 assert.deepEqual(body.context_ref,context);assert.equal(body.raw_text,raw);
});

test('same in-flight chat request shares one preparation; same operation cannot change target, profile or selection',async()=>{
 const p=protocol(),base=p.bridge.prepareNativeAppend;let release;
 p.bridge.prepareNativeAppend=body=>new Promise(resolve=>{release=()=>resolve(base(body));});
 const requests=new NativeAppendRequests(p.client),body=input('concurrent-chat'),a=requests.prepare(body),b=requests.prepare(structuredClone(body));
 assert.equal(a,b);await assert.rejects(requests.prepare({...body,provider_profile_ref:ref('other-profile','f')}),/已变化/);
 await assert.rejects(requests.prepare({...body,context_ref:ref('other-context')}),/已变化/);
 while(!release)await new Promise(resolve=>setImmediate(resolve));release();await a;
 assert.equal(p.calls.filter(call=>call.method==='prepare').length,1);await requests.prepare(body);assert.equal(p.calls.filter(call=>call.method==='prepare').length,1);
 assert.equal(p.calls.filter(call=>call.method==='approve').length,0);
});

test('diagnostic history reads only its explicit bounded tail and never prepares or approves recovered operations',async()=>{
 const p=protocol();for(let i=0;i<12;i++)await p.client.prepare(input('history-'+i));p.calls.length=0;
 const values=await p.client.restore(8);assert.deepEqual(values.map(row=>row.operation.operationId),Array.from({length:8},(_,i)=>'history-'+(i+4)));
 assert.equal(p.calls.filter(call=>call.method==='get').length,8);assert.ok(values.every(row=>row.origin==='RECOVERED'&&!appendActions(row,context).approve));
 assert.equal(p.calls.filter(call=>call.method==='prepare'||call.method==='approve').length,0);await assert.rejects(p.client.restore(0),/读取范围/);
});

test('actual Solid action is absent without an explicit request; recovered and unknown cards remain inspect-only',async t=>{
 const directory=await mkdtemp(resolve(process.env.EEAS_APPEND_TEST_OUTPUT??tmpdir(),'native-append-ui-'));t.after(()=>rm(directory,{recursive:true,force:true}));
 const source=(await readFile(resolve(app,'src/NativeAppendPanel.tsx'),'utf8')).replace("import './native-append.css';",''),transpiled=ts.transpileModule(source,{reportDiagnostics:true,compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}});assert.deepEqual(transpiled.diagnostics,[],'Actual TSX must parse without compiler recovery');
 const js=transpiled.outputText.replace(/from ['"]\.\/(native-append|native-append-copy|native-collaboration|ui-language|authored-ui-copy)['"]/g,(_,name)=>`from '${pathToFileURL(name==='ui-language'?resolve(app,'tests/fixture-ui-language.mjs'):resolve(app,'src',name+'.ts')).href}'`);
 const code=babel.transformSync(js,{filename:'append-panel.jsx',presets:[[solidPreset,{generate:'ssr',hydratable:false}]],configFile:false,babelrc:false}).code.replace(/from ["']solid-js\/web["']/g,`from '${pathToFileURL(resolve(app,'node_modules/solid-js/web/dist/server.js')).href}'`).replace(/from ["']solid-js["']/g,`from '${pathToFileURL(resolve(app,'node_modules/solid-js/dist/server.js')).href}'`),file=resolve(directory,'append-panel.mjs');
 await writeFile(file,code);const {default:Panel,NativeAppendResult}=await import(pathToFileURL(file).href),p=protocol();
 const hidden=renderToString(()=>Panel({active:true,rawText:raw,contextRef:context,providerProfileRef:provider,bridge:p.bridge,storage:p.local}));assert.ok(!hidden.includes('<section')&&!hidden.includes('<textarea')&&!hidden.includes('checkbox'));assert.equal(p.calls.length,0);
 const diagnostic=renderToString(()=>Panel({diagnostics:true,rawText:raw,bridge:p.bridge,storage:p.local}));assert.match(diagnostic,/as-append-history/);assert.ok(!diagnostic.includes('<textarea')&&!diagnostic.includes('checkbox'));assert.equal(p.calls.length,0);
 const view=await p.client.prepare(input('ssr-controls')),props={busy:false,contextRef:context,onApprove:()=>{},onInspect:()=>{},onDismiss:()=>{}};
 const live=renderToString(()=>NativeAppendResult({...props,view}));assert.match(live,/检查并允许|Check and allow/);assert.match(live,/将追加的文字|Text to append/);assert.ok(!live.includes('checkbox')&&!live.includes('<textarea'));
 for(const state of ['UNKNOWN_OUTCOME','PARTIAL','BASE_CHANGED','NOT_ADAPTED']){const html=renderToString(()=>NativeAppendResult({...props,view:{...view,snapshot:{...view.snapshot,state}}}));assert.ok(!html.includes('检查并允许')&&!html.includes('Check and allow'));assert.match(html,/核查原操作|Check operation/);}
 const history=renderToString(()=>NativeAppendResult({...props,view:{...view,origin:'RECOVERED'}}));assert.match(history,/历史回执|Recorded action/);assert.ok(!history.includes('检查并允许')&&!history.includes('Check and allow'));
});
