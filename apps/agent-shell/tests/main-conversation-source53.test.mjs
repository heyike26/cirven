import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
import {companionRoute,exactFileSource} from '../src/companion-files.ts';
import {NativeMessageClient,NativeRequestIndexError,dispatchNativeMessage,nativeMessageInput,nativeMessagePending,readNativeHistory} from '../src/native-collaboration.ts';
import {ordinaryControlScope} from '../src/native-message-guidance.ts';
import {sameReview,consentedReviewInput} from '../src/native-review.ts';
import {saveConversationLabel} from '../src/conversation-history.ts';
import {discoveryInput,discoverySendGate} from '../src/discovery-pending.ts';

const source=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8');
const tree=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const app=tree.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='App');
const ref=id=>({id,revision:1,sha256:'a'.repeat(64)});
const fileSource={kind:'local_file',project_id:'p',workspace_id:'w',binding_revision:1,binding_sha256:'b'.repeat(64),path:'notes.md',revision:2,sha256:'c'.repeat(64)};
const file={source_ref:fileSource,current:true,encoding:'utf-8',content:'human text',byte_length:10,live_sha256:fileSource.sha256};
const snapshot={operation_id:'old-frame',current:true,observation:{status:'PARTIAL',context_ref:ref('old-frame')}};
const annotation={context_ref:ref('old-frame'),label:'批注',comment:'保留这处',regions:[]};
const tick=()=>new Promise(setImmediate);
function actual(name){const node=app.body.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text===name);assert.ok(node,name);return node.getText(tree);}
function harness(options={}){
 const state={mode:'workbench',tool:'direct',text:'当前页面这里是什么意思？ 😀\r\n ',project:undefined,selected:undefined,inspector:undefined,linked:undefined,write:false,attachments:[],feedback:[],records:[],calls:[],errors:[],notices:[],pending:undefined,separate:false,required:false,...options.state};
 const data=new Map(),localStorage={getItem:key=>data.get(key)??null,setItem:(key,value)=>{if(options.storageFailure)throw Error('fixture disk full');data.set(key,String(value));},removeItem:key=>data.delete(key)};
 const bridge={submitNativeMessage:async body=>{state.calls.push(['native',structuredClone(body)]);assert.equal(readNativeHistory(localStorage,'discussion').at(-1).operation_id,body.operationId);return options.send?options.send(body,state):{operation_id:body.operationId,status:'RETURNED',text:'fixture answer'};},inspectNativeMessage:async id=>({operation_id:id,status:'NO_RECORD'})};
 const bindings={companionRoute,exactFileSource,NativeRequestIndexError,dispatchNativeMessage,nativeMessageInput,nativeMessagePending,ordinaryControlScope,sameReview,discoveryInput,discoverySendGate,activeOperation:()=>undefined,
 organizationVersion:()=>0,organizationClient:{pending:undefined},threadAccess:()=> 'ready',connectionAffectsCurrent:()=>false,creationAffectsCurrent:()=>false,setConnectionNotice(){},setCreationNotice(){},uiLocale:()=> 'zh-CN',contextChoice:()=>undefined,originalWorkInputChoice:()=>undefined,selectedDiscussion:()=>undefined,threadScope:()=>undefined,submitFeedbackPassage:async()=>false,nativeFeedbackAvailable:()=>true,saveConversationLabel,refreshHistory:async()=>{},consentedReviewInput,reviewCatalog:()=>undefined,reviewHintSelection:()=>undefined,
 toolMode:()=>state.tool,windowMode:()=>state.mode,prompt:()=>state.text,setPrompt:value=>state.text=value,project:()=>state.project,inspector:()=>state.inspector,directFile:()=>state.selected,linkedFile:()=>state.linked,directWrite:()=>state.write,
 ordinaryGuidanceParent:()=>options.parent,ordinaryNewQuestion:()=>state.separate,nativePending:()=>state.pending,pageReview:()=>null,reviewUnknown:()=>undefined,reviewContinue:()=>undefined,
 nativeDraftAttachments:()=>state.attachments,setNativeDraftAttachments:value=>state.attachments=typeof value==='function'?value(state.attachments):value,draftAttachments:()=>state.feedback,invalidFeedback:()=>options.invalidFeedback??{},
 setError:value=>state.errors.push(value),setNotice:value=>state.notices.push(value),setDirectFileRequired:value=>state.required=value,
 submitDirect:async chosen=>state.calls.push(['file',chosen??state.inspector?.data]),submitOriginalPrompt:async()=>state.calls.push(['original']),sendOrdinaryGuidance:async(parent,text)=>state.calls.push(['guidance',parent,text]),
 companionFilesApi:{load:async source=>{state.calls.push(['load',source]);return {file:state.selected.file};}},recordSelectedFeedback:async()=>{state.calls.push(['feedback']);return {saved:true};},
 nativeBridge:options.noBridge?undefined:bridge,nativeClient:new NativeMessageClient(bridge),localStorage,operationId:()=> 'question-1',currentNativeContext:()=>snapshot,conversationId:()=> 'discussion',providers:()=>[{profile_ref:ref('provider')}],providerId:()=> 'provider',reviewRetryContext:()=>undefined,
 setOrdinaryNewQuestion:value=>state.separate=value,setNativePending:value=>state.pending=value,nativeRecord:(input,message)=>state.records.push({input,message}),persistNativeHistory(){},nativeObserved:new Map(),nativeDisposed:false,
 observeNativeMessage:(id,input)=>{state.pending=id;state.calls.push(['observe',id,input]);},setReviewContinue(){},setReviewUnknown(){},setReviewRetryContext(){},setPageReview(){},setReviewHintSelection(){},
 authoredErrorCopy:error=>error.message,message:error=>error.message,reviewProviderForOperation:()=>ref('provider'),quickScope:()=>({providerProfileRef:ref('provider')}),
 draftPersistence:{hasPending:()=>false},activeSession:()=>undefined,materials:()=>[],entryMode:()=> 'PROBLEM',action:async(label,run)=>{state.calls.push(['action',label]);if(label!=='建立需求工作')return run();}};
 const code=ts.transpileModule('let reviewEditEpoch=0;'+['selectedThreadId','threadOrganizationPending','threadSendingBlocked','threadWriteAllowed','hasComposerFile','discoverySendState','submitPrompt','submitCompanion','sendNativeMessage'].map(actual).join('\n')+'\nreturn {submitPrompt};',{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
 return {state,localStorage,...new Function(...Object.keys(bindings),code)(...Object.values(bindings))};
}

test('actual Main first ordinary question dispatches once with its conversation/provider and fresh-page intent, without creating a project or asking for a file',async()=>{
 const h=harness();const original=h.state.text;await h.submitPrompt();
 assert.deepEqual(h.state.calls.map(row=>row[0]),['native'],JSON.stringify(h.state.errors));const body=h.state.calls[0][1];
 assert.equal(body.rawText,original);assert.equal(body.conversationId,'discussion');assert.deepEqual(body.providerProfileRef,ref('provider'));
 for(const key of ['projectId','contextRef','fileSourceRef','fileLinkRef','write'])assert.equal(Object.hasOwn(body,key),false,key);
 assert.equal(h.state.project,undefined);assert.equal(h.state.required,false);assert.equal(h.state.text,'');assert.equal(h.state.records.at(-1).message.text,'fixture answer');
});
test('ordinary dispatch is identical in full-window, chat-first and companion presentations, including an existing project without a file',async()=>{
 for(const project of [undefined,{id:'p'}]){
  const bodies=[];for(const state of [{mode:'workbench',project},{mode:'workbench',project,inspector:{type:'proposal'}},{mode:'companion',project}]){const h=harness({state});await h.submitPrompt();bodies.push(h.state.calls.find(row=>row[0]==='native')[1]);}
  assert.deepEqual(bodies[0],bodies[1]);assert.deepEqual(bodies[1],bodies[2]);assert.equal(bodies[0].projectId,project?.id);
 }
});
test('explicit file selection and a saved discussion link retain precedence and exact source scope in either presentation',async()=>{
 for(const mode of ['workbench','companion']){
  const selected=harness({state:{mode,selected:{file},inspector:{type:'local-file',data:{...file,source_ref:{...fileSource,path:'other.md'}}}}});await selected.submitPrompt();assert.deepEqual(selected.state.calls,[['file',file]]);
  const link={...ref('linked'),project_id:'p'},linked=harness({state:{mode,linked:{fileLinkRef:link}}});await linked.submitPrompt();const body=linked.state.calls[0][1];assert.deepEqual(body.fileLinkRef,link);assert.equal(body.projectId,'p');assert.equal(body.contextRef,undefined);
  const inspected=harness({state:{mode,inspector:{type:'local-file',data:file},write:true}});await inspected.submitPrompt();assert.deepEqual(inspected.state.calls,[['file',file]]);
 }
});
test('an explicit write without a file keeps the draft and exposes the existing selection recovery, without a message or project mutation',async()=>{
 const h=harness({state:{write:true}}),text=h.state.text;await h.submitPrompt();assert.equal(h.state.required,true);assert.equal(h.state.text,text);assert.deepEqual(h.state.calls,[]);
 let condition;function visit(node){if(ts.isJsxElement(node)&&node.openingElement.tagName.getText(tree)==='Show'&&node.getText(tree).includes('<CompanionFileContext')){const when=node.openingElement.attributes.properties.find(prop=>prop.name?.getText(tree)==='when');if(when?.initializer?.expression?.getText(tree).includes('directFileRequired()'))condition=when.initializer.expression.getText(tree);}ts.forEachChild(node,visit);}visit(app);assert.ok(condition);
 const values={windowMode:()=> 'workbench',toolMode:()=> 'direct',directFile:()=>undefined,directFileRequired:()=>true,previousDirectFile:()=>undefined,linkedFile:()=>undefined,discussionFileState:()=>undefined,discussionFileError:()=>''};assert.equal(new Function(...Object.keys(values),'return !!('+condition+');')(...Object.values(values)),true);
});
test('the actual write checkbox uses the existing exact-file authorization even in a full window; inspected workspace files retain their original permission path',()=>{
 let handler;function visit(node){if(ts.isJsxSelfClosingElement(node)&&node.tagName.getText(tree)==='input'&&node.getText(tree).includes('checked={directWrite()}'))handler=node.attributes.properties.find(prop=>prop.name?.getText(tree)==='onChange').initializer.expression.getText(tree);ts.forEachChild(node,visit);}visit(app);assert.ok(handler);
 for(const selected of [undefined,{file}]){const calls=[],values={windowMode:()=> 'workbench',directFile:()=>selected,changeDirectWrite:value=>calls.push(['authorize',value]),setDirectWrite:value=>calls.push(['workspace',value])};const callback=new Function(...Object.keys(values),'return ('+handler+');')(...Object.values(values));callback({currentTarget:{checked:true}});assert.deepEqual(calls,[[selected?'authorize':'workspace',true]]);}
});
test('file staleness and conflicting frozen feedback remain local; binary reads use an exact source and unsupported writes only record feedback',async()=>{
 const stale=harness({state:{selected:{file:{...file,current:false}}}});await stale.submitPrompt();assert.equal(stale.state.calls.length,0);assert.match(stale.state.errors.at(-1),/已经变化/);
 const mixed=harness({state:{selected:{file},write:true,attachments:[annotation]}});await mixed.submitPrompt();assert.equal(mixed.state.calls.length,0);assert.equal(mixed.state.attachments.length,1);assert.match(mixed.state.errors.at(-1),/尚未建立精确定位/);
 const binary={...file,encoding:'base64',source_ref:{...fileSource,path:'report.docx'}};const read=harness({state:{selected:{file:binary}}});await read.submitPrompt();assert.deepEqual(read.state.calls.map(row=>row[0]),['load','native']);assert.deepEqual(read.state.calls[1][1].fileSourceRef,binary.source_ref);assert.equal(read.state.calls[1][1].contextRef,undefined);
 const write=harness({state:{selected:{file:binary},write:true}});await write.submitPrompt();assert.deepEqual(write.state.calls.map(row=>row[0]),['action','feedback']);assert.ok(write.state.text);
});
test('explicit requirement tools keep their workflow in a companion window; saved requirement feedback is never silently sent as a generic question',async()=>{
 for(const mode of ['workbench','companion']){
  const discovery=harness({state:{mode,tool:'discovery',selected:{file}}});await discovery.submitPrompt();assert.deepEqual(discovery.state.calls,[['action','建立需求工作']]);
  const feedback=harness({state:{mode,feedback:[{feedback_ref:{id:'feedback'}}],selected:{file}},invalidFeedback:{feedback:true}});await feedback.submitPrompt();assert.equal(feedback.state.calls.length,0);assert.match(feedback.state.errors.at(-1),/旧批注/);assert.ok(feedback.state.text);
 }
});
test('original sessions, ordinary running guidance and unsupported pending parents keep their previous routing priority',async()=>{
 const original=harness({parent:{operation_id:'parent'},state:{tool:'original',selected:{file},attachments:[annotation],pending:'parent'}});await original.submitPrompt();assert.deepEqual(original.state.calls,[['original']]);
 const parent={operation_id:'parent'},guidance=harness({parent,state:{selected:{file},pending:'parent'}});await guidance.submitPrompt();assert.deepEqual(guidance.state.calls,[['guidance',parent,guidance.state.text]]);
 const unsupported=harness({state:{pending:'parent'}});await unsupported.submitPrompt();assert.equal(unsupported.state.calls.length,0);assert.match(unsupported.state.errors.at(-1),/暂不能补充/);assert.ok(unsupported.state.text);
 const separate=harness({state:{pending:'parent',separate:true}});await separate.submitPrompt();assert.deepEqual(separate.state.calls.map(row=>row[0]),['native']);
});
test('explicit annotation preserves its frozen frame while ordinary questions never borrow an earlier page',async()=>{
 const h=harness({state:{attachments:[annotation]}}),text=h.state.text;await h.submitPrompt();const body=h.state.calls[0][1];assert.deepEqual(body.contextRef,annotation.context_ref);assert.equal(body.rawText,text);assert.deepEqual(body.inputAttachments,[{kind:'CURRENT_PAGE',context_ref:annotation.context_ref,label:annotation.label,regions:[],user_note:annotation.comment}]);assert.equal(h.state.attachments.length,0);
});
test('missing bridge, local persistence failure and capture/IPC failure keep the exact question and never fall back to a file task or retry',async()=>{
 for(const options of [{noBridge:true},{storageFailure:true},{send:async()=>{throw Error('fixture fresh capture unavailable');}},{send:async body=>({operation_id:body.operationId,status:'NOT_DISPATCHED',error:{message:'fixture no external page'}})}]){
  const h=harness(options),text=h.state.text;await h.submitPrompt();assert.equal(h.state.text,text);assert.equal(h.state.calls.filter(row=>row[0]==='native').length,options.send?1:0);assert.ok(h.state.errors.filter(Boolean).length);assert.ok(!h.state.calls.some(row=>row[0]==='file'||row[0]==='action'));if(options.send)assert.equal(h.state.calls[0][1].contextRef,undefined);
 }
});
test('a late ordinary response preserves a newer question and an in-flight duplicate cannot start a second task',async()=>{
 let finish;const wait=new Promise(resolve=>finish=resolve),h=harness({send:async()=>wait});const running=h.submitPrompt();await tick();h.state.text='尚未发送的新问题';await h.submitPrompt();assert.equal(h.state.calls.length,1);finish({operation_id:'question-1',status:'RETURNED',text:'fixture returned'});await running;assert.equal(h.state.text,'尚未发送的新问题');assert.equal(h.state.records.at(-1).message.text,'fixture returned');
});
