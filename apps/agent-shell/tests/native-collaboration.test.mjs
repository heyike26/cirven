import test from 'node:test';
import assert from 'node:assert/strict';
import { NativeCollaborationController, NativeMessageClient, nativeCollaborationBridge, nativeInitialMode, nativeSnapshot, currentNativeReference, nativePreview, draftContextReference, nativeDraftInstruction, readNativeDraft, saveNativeDraft, readNativeText, saveNativeText, nativeRegion, nativeMessagePending } from '../src/native-collaboration.ts';
import {nativeDirectoryPicker,nativeFilePicker} from '../src/native-picker.ts';
import { FeedbackReworkApi } from '../src/feedback-rework-api.ts';
import { ApiClient } from '../src/api.ts';
import {nativeContextLabel} from '../src/native-collaboration.ts';
import {translateUI} from '../src/ui-language.ts';

const ref={id:'actual-native-record',revision:4,sha256:'a'.repeat(64)};
const snapshot=(revision=4)=>({operation_id:'context-operation',current:true,automatic_input_authorized:false,observation:{status:'OBSERVED',context_ref:{...ref,revision},window:{title:'原生文稿 — 人工最新修改'},coverage:{metadata:true,whole_page_seen:false,page_content_is_engineering_fact:false}},preview:{status:'AVAILABLE',data_url:'data:image/png;base64,YWJj',image_ref:{sha256:'b'.repeat(64)}}});
const storage=()=>{const items=new Map();return{getItem:key=>items.get(key)??null,setItem:(key,value)=>items.set(key,value),items};};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function bridge(overrides={}){return{version:1,getCollaborationState:async()=>({mode:'companion',capabilities:{current_context:true,native_input:false}}),setCollaborationMode:async mode=>({mode}),captureCurrentContext:async()=>snapshot(),onNativeContext:()=>()=>{},...overrides};}

test('unavailable context labels are authored and bilingual while exact diagnostic facts stay immutable',()=>{
 const value=Object.freeze({operation_id:'unavailable',current:false,observation:Object.freeze({status:'NEEDS_CLARIFICATION',reason:'WINDOW_NOT_OBSERVABLE',questions:Object.freeze([{code:'WINDOW_NOT_OBSERVABLE',text:'Original diagnostic remains unchanged.'}])})});
 const bytes=JSON.stringify(value);
 assert.equal(nativeContextLabel(value),'请打开要查看的页面');
 assert.equal(nativeContextLabel(value,message=>translateUI(message,'en')),'Open the page you want to view');
 for(const reason of ['FUTURE_PROVIDER_CODE','constructor','__proto__','toString','']){
  const unknown={...value,observation:{...value.observation,reason}};
  assert.equal(nativeContextLabel(unknown),'当前对象尚未取得');
  assert.equal(nativeContextLabel(unknown,message=>translateUI(message,'en')),'Current object not available yet');
 }
 assert.equal(JSON.stringify(value),bytes);
});
test('a real source title stays verbatim even when it matches a machine code or a translatable label',()=>{
 for(const title of ['WINDOW_NOT_OBSERVABLE','当前对象尚未取得','原生文稿 — 人工最新修改 😀']){
  const value={...snapshot(),observation:{...snapshot().observation,reason:'WINDOW_NOT_OBSERVABLE',window:{title}}};
  assert.equal(nativeContextLabel(value,()=>assert.fail('real window titles must never enter the authored translator')),title);
 }
});

test('companion detection requires the complete new bridge; a historical AppBar ABI never auto-binds or pretends to be native collaboration',()=>{
 const old={version:1,getWorkspaceSurface:()=>assert.fail('must not read old authority'),bindWorkspaceSurface:()=>assert.fail('must not reserve desktop'),updateWorkspaceSurface:()=>assert.fail('must not reshape desktop')};
 assert.equal(nativeCollaborationBridge({eeasDesktop:old}),undefined);assert.equal(nativeInitialMode({eeasDesktop:old}),'workbench');assert.equal(nativeInitialMode({eeasDesktop:bridge()}),'workbench');assert.equal(nativeInitialMode({eeasDesktop:bridge()},'?mode=workbench'),'workbench');assert.equal(nativeInitialMode({},'?mode=companion'),'companion');
});
test('version 2 preload preserves real folder/file chooser support and malformed shortcut failure becomes unavailable context rather than crashing or inventing proof',async()=>{
 const host={eeasDesktop:{...bridge(),version:2,chooseFolder:async()=> 'D:\\work',chooseFiles:async()=>['D:\\work\\真实文稿.docx']}};
 assert.equal(nativeInitialMode(host),'workbench');assert.equal(await nativeDirectoryPicker(host).openDirectoryPicker(),'D:\\work');assert.deepEqual((await nativeFilePicker(host).openFilePicker()).files,[{path:'D:\\work\\真实文稿.docx',name:'真实文稿.docx'}]);const failed=nativeSnapshot({status:'UNAVAILABLE',error:{message:'EXTERNAL_TARGET_UNKNOWN'}});assert.equal(failed.observation.reason,'EXTERNAL_TARGET_UNKNOWN');assert.equal(failed.current,false);assert.equal(currentNativeReference(failed),undefined);assert.equal(nativePreview(failed),undefined);
});
test('a context event frozen before focus survives a slower initial state read; all calls remain free of geometry, HWND and input dispatch',async()=>{
 let finishState,publish;const calls=[],states=[],contexts=[];
 const host=bridge({getCollaborationState:()=>new Promise(resolve=>finishState=resolve),onNativeContext:listener=>{publish=listener;return()=>calls.push('unsubscribe');},bindWorkspaceSurface:()=>assert.fail('AppBar forbidden'),updateWorkspaceSurface:()=>assert.fail('reflow forbidden')});
 const controller=new NativeCollaborationController(host,value=>states.push(value),value=>contexts.push(value));const pending=controller.start();publish(snapshot(5));finishState({mode:'companion',context:snapshot(4)});await pending;assert.equal(contexts.length,1);assert.equal(contexts[0].observation.context_ref.revision,5);assert.equal(states.length,1);controller.stop();assert.deepEqual(calls,['unsubscribe']);
});
test('a late capture and mode switch cannot overwrite newer native context or a newer user window mode',async()=>{
 let finishCapture,publish;const modeFinishes=[],states=[],contexts=[];const host=bridge({onNativeContext:listener=>{publish=listener;return()=>{};},captureCurrentContext:()=>new Promise(resolve=>finishCapture=resolve),setCollaborationMode:mode=>new Promise(resolve=>modeFinishes.push(()=>resolve({mode})))});
 const controller=new NativeCollaborationController(host,value=>states.push(value),value=>contexts.push(value));await controller.start();const capture=controller.capture({intent:'用户引用',need:{metadata:true,accessibility:true,visual:false,ocr:false}});publish(snapshot(8));finishCapture(snapshot(7));await capture;assert.deepEqual(contexts.map(value=>value.observation.context_ref.revision),[8]);const first=controller.mode('workbench'),second=controller.mode('companion');modeFinishes[1]();await second;modeFinishes[0]();await first;assert.deepEqual(states.map(value=>value.mode),['companion','companion']);controller.stop();publish(snapshot(9));assert.equal(contexts.length,1);
});
test('current context is a host fact: a missing/stale reference never borrows a file or reconstructs a new version; remote preview URLs are rejected',()=>{
 const value=snapshot();assert.deepEqual(currentNativeReference(value),ref);assert.equal(currentNativeReference({...value,current:false}),undefined);assert.equal(currentNativeReference({...value,observation:{status:'UNKNOWN',window:{title:'known title'}}}),undefined);assert.equal(currentNativeReference({...value,observation:{...value.observation,context_ref:{...ref,sha256:'wrong'}}}),undefined);
 assert.equal(nativePreview(value),'data:image/png;base64,YWJj');for(const url of ['https://localhost/image?token=secret','file:///D:/screen.png','data:image/svg+xml;base64,YWJj','data:image/png;base64,not valid'])assert.equal(nativePreview({...value,preview:{status:'AVAILABLE',data_url:url}}),undefined);
});
test('persistent native annotations preserve exact Chinese/emoji/newlines and native versions across restart and project switching without storing frame bytes',()=>{
 const local=storage(),comment='人工意见 😀\r\n  保留结论与改写范围 ';const attachment={context_ref:ref,label:'报告.pptx · 第3页',comment,regions:[{x:.2,y:.1,width:.3,height:.4}],preview_sha256:'b'.repeat(64)};
 saveNativeDraft(local,'p',[attachment]);saveNativeDraft(local,'q',[]);saveNativeText(local,undefined,comment);saveNativeText(local,'p','项目草稿');assert.equal(readNativeText(local),comment);assert.equal(readNativeText(local,'p'),'项目草稿');assert.deepEqual(readNativeDraft(local,'p'),[attachment]);assert.deepEqual(readNativeDraft(local,'q'),[]);assert.ok(![...local.items.values()].some(value=>value.includes('data:image')));assert.match(nativeDraftInstruction('原有文字',[attachment]),/原有文字/);assert.ok(nativeDraftInstruction('',[attachment]).includes(comment));assert.match(nativeDraftInstruction('',[attachment]),/非自动识别对象/);
});
test('restored drafts retain their original refs and refuse stale or mixed-version sending instead of relabeling them as current',()=>{
 const item={context_ref:ref,label:'exact',comment:'keep',regions:[]};assert.deepEqual(draftContextReference([item],snapshot()),ref);assert.throws(()=>draftContextReference([item],snapshot(5)),/版本已变化/);assert.throws(()=>draftContextReference([item],undefined),/版本已变化/);assert.throws(()=>draftContextReference([item,{...item,context_ref:{...ref,sha256:'c'.repeat(64)}}],snapshot()),/版本已变化/);assert.equal(item.context_ref.revision,4);
});
test('frame annotation coordinates are normalized to the exact displayed image and reject clicks, infinities and invalid layout',()=>{
 assert.deepEqual(nativeRegion({x:200,y:150},{x:110,y:110},{left:100,top:100,width:200,height:100}),{x:.05,y:.1,width:.45,height:.4});assert.deepEqual(nativeRegion({x:50,y:50},{x:400,y:300},{left:100,top:100,width:200,height:100}),{x:0,y:0,width:1,height:1});assert.equal(nativeRegion({x:100,y:100},{x:101,y:101},{left:100,top:100,width:200,height:100}),undefined);assert.equal(nativeRegion({x:NaN,y:0},{x:1,y:1},{left:0,top:0,width:0,height:0}),undefined);
});
test('native message dispatch preserves actual refs/raw text, uses one original-loop call, and only reads exact operation progress/cancellation',async()=>{
 const calls=[];const host=bridge({submitNativeMessage:async body=>{calls.push(['submit',body]);return{operation_id:body.operationId,status:'PROCESSING',events:[]};},inspectNativeMessage:async id=>{calls.push(['inspect',id]);return{operation_id:id,status:'RUNNING',events:[{type:'tool.executed',receipt:'actual'}]};},cancelNativeMessage:async id=>{calls.push(['cancel',id]);return{operation_id:id,status:'CANCELLED'};}});const client=new NativeMessageClient(host),body={operationId:'one',rawText:'请核对 😀\r\n',contextRef:ref};const requested=await client.send(body);assert.equal(nativeMessagePending(requested),true);assert.deepEqual(calls[0],['submit',body]);assert.deepEqual((await client.inspect('one')).events,[{type:'tool.executed',receipt:'actual'}]);assert.equal(nativeMessagePending(await client.cancel('one')),false);assert.deepEqual(calls.map(item=>item[0]),['submit','inspect','cancel']);assert.ok(!('send_full_image_to_model' in calls[0][1]));
});
test('IPC failure or unrelated receipt does not redispatch; restart recovers by inspection and NOT_CONFIGURED never becomes success',async()=>{
 let calls=0;const host=bridge({submitNativeMessage:async()=>{calls++;throw new Error('IPC disconnected after send');},inspectNativeMessage:async id=>({operation_id:id,status:'UNKNOWN_OUTCOME',reason:'restart during execution'})});const client=new NativeMessageClient(host);await assert.rejects(()=>client.send({operationId:'same',rawText:'keep this draft'}),/IPC disconnected/);assert.equal(calls,1);assert.equal((await client.inspect('same')).status,'UNKNOWN_OUTCOME');assert.equal(calls,1);
 const wrong=new NativeMessageClient(bridge({submitNativeMessage:async()=>({operation_id:'other',status:'COMPLETED'})}));await assert.rejects(()=>wrong.send({operationId:'requested',rawText:'no'}),/本次操作/);const unavailable=new NativeMessageClient(bridge({submitNativeMessage:async body=>({operation_id:body.operationId,status:'NOT_CONFIGURED'})}));assert.equal(nativeMessagePending(await unavailable.send({operationId:'no-provider',rawText:'ask'})),false);
});
test('file-bound feedback sends the actual project/source baseline, preserves draft text, and never claims model dispatch',async()=>{
 const calls=[];const source={kind:'local_file',project_id:'p',workspace_id:'w',binding_revision:2,binding_sha256:'c'.repeat(64),path:'draft.md',revision:3,sha256:'d'.repeat(64)};
 const http=new ApiClient(async(url,options)=>{calls.push({url,options});const body=url==='/api/bootstrap'?{csrf_token:'real-csrf'}:url.endsWith('/bindings')?{binding_ref:ref,payload:{file_source_ref:source,locator:{kind:'FILE'}},native_context_status:'ATTACHED_NOT_QUALIFIED'}:{feedback_ref:{...ref,id:'feedback'},payload:{comment:'人工反馈\r\n'},status:'DRAFT',native_requested:false};return new Response(JSON.stringify(body));});
 const feedback=new FeedbackReworkApi(http);const binding=await feedback.bind('p',source,{nativeContext:{context_ref:ref}},'binding-key');const result=await feedback.record('p',binding.binding_ref,'人工反馈\r\n','DRAFT','feedback-key');assert.equal(result.native_requested,false);assert.equal(result.status,'DRAFT');const bodies=calls.slice(1).map(item=>JSON.parse(item.options.body));assert.deepEqual(bodies[0].file_source_ref,source);assert.deepEqual(bodies[0].native_context_ref,{context_ref:ref});assert.equal(bodies[1].comment,'人工反馈\r\n');assert.equal(bodies[1].operation_id,'feedback-key');assert.ok(!calls.some(item=>item.url.includes('direct-actions')||item.url.includes('advance')));assert.throws(()=>feedback.bind('different',source),/当前项目/);
});
