import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';
import * as solid from '../node_modules/solid-js/dist/solid.js';
import {ordinaryControlScope,ordinaryControlSummaries,dispatchOrdinaryGuidance,controlNotice} from '../src/native-message-guidance.ts';
import {sameReview} from '../src/native-review.ts';
import {nativeMessagePending} from '../src/native-collaboration.ts';
const ref=id=>({id,revision:1,sha256:'a'.repeat(64)});
const message=(extra={})=>({operation_id:'parent',status:'PROCESSING',conversation_id:'discussion',native_message_ref:ref('parent'),native_message_phase:'RUNNING',control_capabilities:{can_guide:true,can_status:true,can_cancel:true},...extra});
const storage=()=>{const data=new Map();return {data,getItem:k=>data.get(k)??null,setItem:(k,v)=>data.set(k,String(v))};};
const turn=()=>new Promise(setImmediate);
test('ordinary control uses only exact server native scope; legacy review and malformed projections stay separate',()=>{
 assert.deepEqual(ordinaryControlScope(message()),{ref:ref('parent'),can_guide:true,can_status:true,can_cancel:true});
 for(const value of [message({review:{review_ref:ref('review')}}),message({native_message_ref:{...ref('parent'),revision:2}}),message({native_message_ref:{...ref('parent'),extra:true}}),message({control_capabilities:{can_guide:true}})])assert.equal(ordinaryControlScope(value),null);
 const rows=Array.from({length:20},(_,i)=>({operation_id:String(i),raw_text:'原文'+i,status:'ACCEPTED'}));assert.equal(ordinaryControlSummaries(message({native_message_controls:rows})).length,16);assert.equal(controlNotice({status:'RETURNED',role:'LOCAL_STATUS',local_status:{active_operation_id:'initial'}}),'任务仍在运行');assert.equal(controlNotice({status:'ACCEPTED'}),'补充已送达');
});
test('actual dispatch saves before transport; same receipt recovery only inspects and preserves newer concurrent indexes',async()=>{
 const local=storage(),calls=[],parent=message(),send=async input=>{calls.push(input);if(calls.length===1){assert.equal(JSON.parse(local.getItem('eeas-native-review-control-index-v1'))['guidance:discussion:parent'].body.operation_id,'first');return {operation_id:'first',status:'UNKNOWN_OUTCOME'};}const index=JSON.parse(local.getItem('eeas-native-review-control-index-v1'));index.other={retained:'parallel'};local.setItem('eeas-native-review-control-index-v1',JSON.stringify(index));return {operation_id:'first',status:'ACCEPTED'};};
 await dispatchOrdinaryGuidance(local,parent,'discussion','原补充😀\n','first',send);const recovered=await dispatchOrdinaryGuidance(local,parent,'discussion','后续尚未发送文字','second',send);
 assert.equal(calls.length,2);assert.equal(calls[1].inspect,true);assert.equal(calls[1].body.operation_id,'first');assert.equal(recovered.submittedText,'原补充😀\n');assert.equal(calls[0].body.expected_review_ref,undefined);assert.deepEqual(calls[0].body.expected_native_message_ref,ref('parent'));assert.ok(JSON.parse(local.getItem('eeas-native-review-control-index-v1')).other);
});
test('storage failure and changed frozen parent refuse transport; historical control recovery never grants a new turn',async()=>{
 let calls=0;const send=async input=>{calls++;return {operation_id:input.body.operation_id,status:'UNKNOWN_OUTCOME'};};
 await assert.rejects(dispatchOrdinaryGuidance({getItem:()=>null,setItem:()=>{throw Error('disk full');}},message(),'discussion','保留','first',send));assert.equal(calls,0);
 const local=storage();await dispatchOrdinaryGuidance(local,message(),'discussion','保留','first',send);
 await assert.rejects(dispatchOrdinaryGuidance(local,message({native_message_ref:{...ref('parent'),sha256:'b'.repeat(64)}}),'discussion','new','second',send));assert.equal(calls,1);
 const inactive=message({status:'RETURNED',control_capabilities:{can_guide:false,can_status:false,can_cancel:false}});await dispatchOrdinaryGuidance(local,inactive,'discussion','','ignored',send);assert.equal(calls,2);
 await assert.rejects(dispatchOrdinaryGuidance(storage(),inactive,'discussion','new','new',send));assert.equal(calls,2);
});
const source=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8'),tree=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX),app=tree.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='App');
function actual(name){const node=app.body.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text===name);assert.ok(node,name);return node.getText(tree);}
function appHarness(options={}){
 const state={messages:[{input:'原始问题',message:message()}],text:'补充新的约束',selected:undefined,notices:[],errors:[],controls:[],submits:[],pending:'parent',sending:undefined,cid:'discussion',separate:false};const local=storage();
 const bindings={nativeFeedbackAvailable:()=>false,nativeFeedbackAction:()=>undefined,organizationVersion:()=>0,organizationClient:{pending:undefined},threadAccess:()=> 'ready',connectionAffectsCurrent:()=>false,creationAffectsCurrent:()=>false,selectedDiscussion:()=>undefined,threadScope:()=>undefined,contextChoice:()=>undefined,originalWorkInputChoice:()=>undefined,uiLocale:()=> 'zh-CN',ordinaryControlScope,ordinaryControlSummaries,dispatchOrdinaryGuidance,controlNotice,sameReview,nativeMessagePending,
 nativeMessages:()=>state.messages,conversationId:()=>state.cid,nativePending:()=>state.pending,reviewContinue:()=>state.selected,ordinaryNewQuestion:()=>state.separate,
 nativeDraftAttachments:()=>[],pageReview:()=>null,quickScope:()=>({providerProfileRef:ref('provider')}),reviewProviderForOperation:()=>options.mismatch?ref('other'):ref('provider'),
 ordinaryControlSending:()=>state.sending,setOrdinaryControlSending:v=>state.sending=v,prompt:()=>state.text,setPrompt:v=>state.text=v,setNotice:v=>state.notices.push(v),setError:v=>state.errors.push(v),
 localStorage:local,operationId:()=> 'guide-'+(state.controls.length+1),nativeBridge:{submitNativeMessage:async v=>state.submits.push(v),inspectNativeMessage(){},controlNativeReview:async v=>{state.controls.push(v);return options.control?options.control(v,state):{operation_id:v.body.operation_id,status:'ACCEPTED'};}},
 nativeClient:{inspect:async()=>state.messages[0].message},nativeRecord:(input,value)=>{state.messages=[{input:state.messages[0].input,message:value}];},persistNativeHistory(){},observeNativeMessage:id=>state.pending=id,
 toolMode:()=> 'direct',authoredErrorCopy:e=>e.message,dispatchNativeMessage:()=>{throw Error('Unexpected new task');},nativeDisposed:false};
 const names=['submitFeedbackPassage','currentNativeFeedbackAction','selectedThreadId','threadOrganizationPending','threadSendingBlocked','threadWriteAllowed','ordinaryGuidanceParent','ordinaryGuidanceAvailable','sendOrdinaryGuidance','controlPageReview','sendNativeMessage','submitPrompt'];const code=ts.transpileModule(names.map(actual).join('\n')+'\nreturn {submitPrompt,sendNativeMessage,ordinaryGuidanceParent,ordinaryGuidanceAvailable};',{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
 const api=new Function(...Object.keys(bindings),code)(...Object.values(bindings));return {state,local,...api};
}
test('actual Main submit routes processing ordinary task through one control on the same parent, without a new task or capture',async()=>{
 const h=appHarness();await h.submitPrompt();assert.equal(h.state.controls.length,1);assert.equal(h.state.controls[0].parentOperationId,'parent');assert.equal(h.state.controls[0].body.raw_text,'补充新的约束');assert.deepEqual(h.state.controls[0].body.expected_native_message_ref,ref('parent'));assert.equal(h.state.submits.length,0);assert.equal(h.state.messages[0].input,'原始问题');assert.equal(h.state.pending,'parent');assert.equal(h.state.text,'');assert.equal(h.state.notices.at(-1),'补充已送达');
});
test('actual Main progress answer leaves its original task running and does not label local status as assistant output',async()=>{
 const h=appHarness({control:async input=>({operation_id:input.body.operation_id,status:'RETURNED',role:'LOCAL_STATUS',local_status:{active_operation_id:'initial'}})});h.state.text='现在进度如何？';await h.submitPrompt();assert.equal(h.state.pending,'parent');assert.equal(h.state.messages[0].message.status,'PROCESSING');assert.equal(h.state.messages[0].message.text,undefined);assert.equal(h.state.notices.at(-1),'任务仍在运行');assert.equal(h.state.submits.length,0);
});
test('native observed and associated receipts unblock only the exact local recovery slot; a cohort return is not an individual answer',async()=>{
 for(const status of ['OBSERVED','NEXT_TURN_ASSOCIATED','COHORT_RETURNED']){
  const local=storage(),scope=message(),send=async request=>({operation_id:request.body.operation_id,status});await dispatchOrdinaryGuidance(local,scope,'discussion','原文','original',send);assert.deepEqual(JSON.parse(local.getItem('eeas-native-review-control-index-v1')),{});
  const h=appHarness({control:async input=>({operation_id:input.body.operation_id,status})});await h.submitPrompt();assert.equal(h.state.text,'');assert.equal(h.state.pending,'parent');assert.equal(h.state.messages[0].message.text,undefined);assert.equal(h.state.notices.at(-1),status==='COHORT_RETURNED'?'任务已返回':'补充已送达');
 }
 assert.equal(controlNotice({status:'UNKNOWN_OUTCOME'}),'核查原操作');
});
test('actual Main typing while send waits is retained, duplicate submit suppressed, and provider drift is not a new-task fallback',async()=>{
 let done;const wait=new Promise(resolve=>done=resolve);const h=appHarness({control:async()=>wait});const sending=h.submitPrompt();await turn();h.state.text='发送期间新输入';await h.submitPrompt();assert.equal(h.state.controls.length,1);done({operation_id:'guide-1',status:'ACCEPTED'});await sending;assert.equal(h.state.text,'发送期间新输入');
 const wrong=appHarness({mismatch:true});await wrong.submitPrompt();assert.equal(wrong.state.controls.length,0);assert.equal(wrong.state.submits.length,0);assert.equal(wrong.state.text,'补充新的约束');
});
test('actual Main multiple pending tasks requires an exact selected parent',async()=>{
 const h=appHarness();h.state.messages.push({input:'其他任务',message:message({operation_id:'other',native_message_ref:ref('other')})});await h.submitPrompt();assert.equal(h.state.controls.length,0);assert.equal(h.state.submits.length,0);h.state.selected='parent';await h.submitPrompt();assert.equal(h.state.controls[0].parentOperationId,'parent');
});
test('actual Main new-question action preserves existing follow-up, attachment and in-flight control before changing task context',()=>{
 for(const sample of [{text:'尚未发送',attachments:[],sending:undefined},{text:'',attachments:[{id:'annotation'}],sending:undefined},{text:'',attachments:[],sending:'parent'},{text:'',attachments:[],sending:undefined}]){
  const calls=[],bindings={ordinaryControlSending:()=>sample.sending,prompt:()=>sample.text,nativeDraftAttachments:()=>sample.attachments,setError:v=>calls.push(['error',v]),setOrdinaryNewQuestion:v=>calls.push(['separate',v]),setReviewUnknown:v=>calls.push(['unknown',v]),setReviewRetryContext(){},conversationId:()=> 'discussion',localStorage:{removeItem:k=>calls.push(['remove',k])},authoredErrorCopy:e=>e.message,setReviewContinue:v=>calls.push(['selected',v]),setPrompt:v=>calls.push(['text',v]),setNativeDraftAttachments:v=>calls.push(['attachments',v]),setCurrentNativeContext:v=>calls.push(['context',v]),pageReview:()=>null};
  const code=ts.transpileModule('let reviewEditEpoch=0;'+actual('newPageQuestion')+';return newPageQuestion;',{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText,newQuestion=new Function(...Object.keys(bindings),code)(...Object.values(bindings));newQuestion();
  if(sample.text||sample.attachments.length||sample.sending){assert.equal(calls.some(([kind])=>['separate','remove','selected','text','attachments','context'].includes(kind)),false);if(!sample.sending)assert.equal(calls[0][1],'请先发送或清空当前补充');}
  else{assert.deepEqual(calls.find(([kind])=>kind==='separate'),['separate',true]);assert.deepEqual(calls.find(([kind])=>kind==='context'),['context',undefined]);}
 }
});

// Render the actual production textarea and send button; host elements and the
// design-system Button are isolated. Native focus/layout are tested in browser.
const universal=readFileSync(new URL('../node_modules/solid-js/universal/dist/universal.js',import.meta.url),'utf8').replace("from 'solid-js'",`from '${new URL('../node_modules/solid-js/dist/solid.js',import.meta.url).href}'`);
const {createRenderer}=await import('data:text/javascript;base64,'+Buffer.from(universal).toString('base64'));
function composerNodes(){let input,button;function visit(n){if(ts.isJsxElement(n)||ts.isJsxSelfClosingElement(n)){const opening=ts.isJsxElement(n)?n.openingElement:n,tag=opening.tagName.getText(tree),text=opening.getText(tree);if(tag==='textarea'&&text.includes('描述要完成的工作'))input=n.getText(tree);if(tag==='Button'&&text.includes('as-composer-send'))button=n.getText(tree);}ts.forEachChild(n,visit);}visit(app);assert.ok(input&&button);return {input,button};}
test('actual Main composer renderer keeps one editable textarea and stable nodes while control capability changes; IME does not submit',async()=>{
 const host={createElement:type=>({type,props:{},children:[],parent:null}),createTextNode:value=>({type:'#text',value,children:[],parent:null}),isTextNode:n=>n.type==='#text',replaceText:(n,v)=>n.value=v,insertNode(p,n,a){n.parent=p;p.children.splice(a?p.children.indexOf(a):p.children.length,0,n);},removeNode(p,n){p.children.splice(p.children.indexOf(n),1);n.parent=null;},setProperty:(n,k,v)=>n.props[k]=v,getParentNode:n=>n.parent,getFirstChild:n=>n.children[0],getNextSibling:n=>n.parent?.children[n.parent.children.indexOf(n)+1]};
 const renderer=createRenderer(host),[text,setText]=solid.createSignal('已有补充'),[cap,setCap]=solid.createSignal(true),calls=[];
 const nodes=composerNodes(),jsx=`function Composer(){return <main>${nodes.input}${nodes.button}</main>}`;
 const js=ts.transpileModule(jsx,{compilerOptions:{jsx:ts.JsxEmit.Preserve,target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
 const compiled=babel.transformSync(js,{configFile:false,babelrc:false,presets:[[solidPreset,{generate:'universal',moduleName:'actual-renderer'}]]}).code.replace(/import\s*\{([^}]+)\}\s*from\s*["']actual-renderer["'];?/g,(_match,list)=>'const {'+list.replace(/\s+as\s+/g,':')+'}=bindings.renderer;');
 const bindings={toolMode:()=> 'direct',linkedFile:()=>undefined,draftAttachments:()=>[],threadSendingBlocked:()=>false,nativeFeedbackAvailable:()=>false,currentNativeFeedbackAction:()=>undefined,renderer,uiText:k=>k,windowMode:()=> 'workbench',composerView:()=>({focused:true}),composerPresentation:{focus(){}},prompt:text,setPrompt:setText,submitPrompt:()=>calls.push('submit'),nativeDraftAttachments:()=>[],entryMode:()=> 'PROBLEM',materials:()=>[],nativePending:()=> 'parent',pageReview:()=>null,ordinaryControlSending:()=>undefined,ordinaryNewQuestion:()=>false,ordinaryGuidanceAvailable:cap,reviewUnknown:()=>undefined,reviewContinue:()=>undefined,nativeMessages:()=>[],needsReviewProcess:()=>false,reviewCatalog:()=>undefined,reviewHintSelection:()=>undefined,busy:()=>'',activeOperation:()=>undefined,Button:props=>{const node=renderer.createElement('button');renderer.spread(node,props);return node;}};
 const Component=new Function('bindings',`const {${Object.keys(bindings).filter(k=>k!=='renderer').join(',')}}=bindings;${ts.transpileModule(actual('discoverySendState'),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText};${compiled};return Composer;`)(bindings);
 const mount={type:'root',children:[]};const dispose=renderer.render(()=>Component(),mount);const all=()=>{const out=[];function walk(n){out.push(n);for(const c of n.children??[])walk(c);}walk(mount);return out;};const input=all().find(n=>n.type==='textarea'),button=all().find(n=>n.type==='button');
 assert.ok(input&&button);assert.equal(button.props.disabled,false);input.props.onKeyDown({key:'Enter',ctrlKey:true,isComposing:true,preventDefault(){throw Error('IME consumed');}});assert.equal(calls.length,0);
 input.props.onKeyDown({key:'Enter',ctrlKey:true,isComposing:false,preventDefault(){}});assert.equal(calls.length,1);setCap(false);await turn();assert.equal(button.props.disabled,true);setText('新输入保留');await turn();assert.equal(all().find(n=>n.type==='textarea'),input);assert.equal(all().filter(n=>n.type==='textarea').length,1);assert.equal(input.props.value,'新输入保留');dispose();
});
