import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
import {discoveryInput,sameDiscoveryInput,readDiscoveryPending,saveDiscoveryPending,clearDiscoveryPending,discoverySendGate} from '../src/discovery-pending.ts';
const key='eeas-discovery-operation:p',doc={document_id:'d',revision:1,sha256:'a'.repeat(64)},ref={id:'f',revision:1,sha256:'b'.repeat(64)};
function store(raw){const data=new Map(raw?[[key,raw]]:[]);return {data,getItem:k=>data.get(k)??null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)};}
test('legacy pending is compatible, snapshots survive a restart, and clearing owns the exact key',()=>{
 const s=store('{"id":"one","sessionId":"work"}');assert.deepEqual(readDiscoveryPending(s,'p'),{id:'one',sessionId:'work'});
 const v={id:'one',sessionId:'work',basisRevision:1,retainedInput:discoveryInput('未提交 😀\n第二行',[doc],[ref])};saveDiscoveryPending(s,'p',v);assert.deepEqual(readDiscoveryPending(store(s.getItem(key)),'p'),v);clearDiscoveryPending(s,'p','another');assert.ok(s.getItem(key));clearDiscoveryPending(s,'p','one');assert.equal(s.getItem(key),null);
});
test('corrupt or excessive local records are preserved and cannot be silently replaced',()=>{
 for(const raw of ['broken','null',JSON.stringify({id:'one',sessionId:'work',basisRevision:0}),JSON.stringify({id:'one',sessionId:'work',retainedInput:{text:'x',materials:['x'],feedback:'no'}}),'x'.repeat(262145)]){
  const s=store(raw);assert.throws(()=>readDiscoveryPending(s,'p'));assert.throws(()=>saveDiscoveryPending(s,'p',{id:'two',sessionId:'work'}));assert.equal(s.getItem(key),raw);
 }
});
test('cleanup verifies removal, preserves a newer key, and never deletes malformed bytes',()=>{
 const original=JSON.stringify({id:'one',sessionId:'work'}),s=store(original);s.removeItem=()=>{};assert.throws(()=>clearDiscoveryPending(s,'p','one'));assert.equal(s.getItem(key),original);
 const newer=JSON.stringify({id:'two',sessionId:'work'});s.removeItem=k=>s.setItem(k,newer);clearDiscoveryPending(s,'p','one');assert.equal(s.getItem(key),newer);clearDiscoveryPending(s,'p','one');assert.equal(s.getItem(key),newer);
 const broken=store('bad bytes');assert.throws(()=>clearDiscoveryPending(broken,'p','one'));assert.equal(broken.getItem(key),'bad bytes');
});
test('unknown retained input blocks duplicates, not explicit new text or changed reference versions',()=>{
 const input=discoveryInput('第一行\r\n第二行',[doc],[ref]),op={kind:'discovery',status:'UNKNOWN_OUTCOME',projectId:'p',sessionId:'work',retainedInput:input};
 const gate=v=>discoverySendGate(op,'p','work',v);assert.equal(gate(discoveryInput(' 第一行\n第二行 ',[doc],[ref])),'blocked');
 assert.equal(gate(discoveryInput('第一行\n第二行\n新问题',[doc],[ref])),'new-message');assert.equal(gate(discoveryInput(input.text,[{...doc,revision:2}],[ref])),'new-message');assert.equal(gate(discoveryInput(input.text,[doc],[])),'new-message');
 assert.equal(discoverySendGate(op,'other','work',input),'normal');assert.equal(discoverySendGate({...op,historicalInspection:true},'p','work',input),'normal');assert.equal(discoverySendGate({...op,retainedInput:undefined},'p','work',input),'blocked');
 assert.equal(discoverySendGate({...op,discoveryPurpose:'PLAN'},'p','work',input),'normal');
 assert.equal(sameDiscoveryInput(discoveryInput('a',[doc,{...doc,document_id:'e'}],[]),discoveryInput('a',[{...doc,document_id:'e'},doc],[])),true);
});

test('actual App button, submit callback and Ctrl+Enter share the unknown-input gate; IME cannot send',async()=>{
 const source=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8'),ast=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX),nodes=[];
 function walk(n){nodes.push(n);ts.forEachChild(n,walk);}walk(ast);
 const fn=name=>nodes.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text===name).getText(ast);
 const button=nodes.find(n=>ts.isJsxOpeningElement(n)&&n.tagName.getText(ast)==='Button'&&n.attributes.getText(ast).includes('as-composer-send'));
 const input=nodes.find(n=>ts.isJsxSelfClosingElement(n)&&n.tagName.getText(ast)==='textarea'&&n.getText(ast).includes('描述要完成的工作'));
 const attr=(node,name)=>node.attributes.properties.find(p=>p.name?.getText(ast)===name).initializer.expression.getText(ast);
 let text='原稿 😀\n第二行',refs=[doc],actions=0,mode='discovery',feedback=[],selected=null,nativeSends=0,originalSends=0;const original=text,op={kind:'discovery',status:'UNKNOWN_OUTCOME',projectId:'p',sessionId:'work',retainedInput:discoveryInput(text,refs,[])};
 const bindings={discoveryInput,discoverySendGate,activeOperation:()=>op,toolMode:()=>mode,pageReview:()=>null,nativeDraftAttachments:()=>[],ordinaryGuidanceParent:()=>null,nativePending:()=>null,ordinaryNewQuestion:()=>false,project:()=>({id:'p'}),activeSession:()=>({id:'work',revision:1}),prompt:()=>text,materials:()=>refs.map(document_ref=>({document_ref})),draftAttachments:()=>feedback,threadWriteAllowed:()=>true,threadScope:()=>null,selectedDiscussion:()=>selected,submitFeedbackPassage:async()=>false,invalidFeedback:()=>({}),draftPersistence:{hasPending:()=>false},entryMode:()=> 'PROBLEM',action:async()=>{actions++;},threadSendingBlocked:()=>false,nativeFeedbackAvailable:()=>false,currentNativeFeedbackAction:()=>null,ordinaryControlSending:()=>false,reviewUnknown:()=>false,reviewContinue:()=>false,needsReviewProcess:()=>false,busy:()=>false};
 Object.assign(bindings,{hasComposerFile:()=>false,reviewCatalog:()=>null,reviewHintSelection:()=>null,linkedFile:()=>null,sendNativeMessage:async()=>{nativeSends++;},submitOriginalPrompt:async()=>{originalSends++;}});
 const code=ts.transpileModule(fn('discoverySendState')+'\n'+fn('submitPrompt')+`;return {submitPrompt,disabled:()=>(${attr(button,'disabled')}),key:(${attr(input,'onKeyDown')})};`,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
 const api=new Function(...Object.keys(bindings),code)(...Object.values(bindings));
 assert.equal(api.disabled(),true);await api.submitPrompt();let prevented=0;api.key({key:'Enter',ctrlKey:true,isComposing:false,preventDefault(){prevented++;}});await new Promise(setImmediate);assert.equal(actions,0);assert.equal(prevented,1);assert.equal(text,original);
 text+='\n这是新的问题';assert.equal(api.disabled(),false);api.key({key:'Enter',ctrlKey:true,isComposing:true,preventDefault(){throw Error('IME consumed');}});await new Promise(setImmediate);assert.equal(actions,0);
 api.key({key:'Enter',ctrlKey:true,isComposing:false,preventDefault(){}});await new Promise(setImmediate);assert.equal(actions,1);
 text=original;refs=[{...doc,revision:2}];assert.equal(api.disabled(),false);await api.submitPrompt();assert.equal(actions,2);
 text=original;refs=[doc];feedback=[{feedback_ref:ref}];op.retainedInput=discoveryInput(text,refs,[ref]);mode='direct';assert.equal(api.disabled(),true,'direct mode with retained structured feedback still uses discovery');await api.submitPrompt();assert.equal(actions,2);
 selected={key:{kind:'native'}};mode='discovery';assert.equal(api.disabled(),false);await api.submitPrompt();assert.equal(nativeSends,1);assert.equal(actions,2);
 selected=null;mode='original';assert.equal(api.disabled(),false);await api.submitPrompt();assert.equal(originalSends,1);assert.equal(actions,2);
});
