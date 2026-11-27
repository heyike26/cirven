import {ordinaryControlScope} from '../src/native-message-guidance.ts';
import {flushDynamicWorkflowDrafts} from '../src/dynamic-workflow.ts';
import {authoredCopy,authoredErrorCopy} from '../src/authored-ui-copy.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,mkdtemp,writeFile,rm} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {join} from 'node:path';
import ts from 'typescript';
import {sameQuickScope,quickIntentSummary} from '../src/quick-inbox.ts';
import {nativeMessagePending,saveNativeDraft,readNativeDraft,saveNativeText,readNativeText} from '../src/native-collaboration.ts';
import {ComposerDraftPersistence,readComposerDraft} from '../src/composer-draft.ts';
const source=await readFile(new URL('../src/App.tsx',import.meta.url),'utf8'),parsed=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX),body=parsed.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='App').body;
// Use the actual current journal flushers. Only the Workbench wait is held by
// the controlled close scenario; no pending journal is silently skipped.
const actualFlushers={};
for(const declaration of parsed.statements.filter(ts.isImportDeclaration)){
 const wanted=declaration.importClause?.namedBindings?.elements?.filter(item=>item.name.text.startsWith('flush'))??[];
 if(wanted.length){const loaded=await import(new URL(declaration.moduleSpecifier.text.endsWith('.ts')?declaration.moduleSpecifier.text:declaration.moduleSpecifier.text+'.ts',new URL('../src/App.tsx',import.meta.url)).href);for(const item of wanted)actualFlushers[item.name.text]=loaded[item.propertyName?.text??item.name.text];}
}
function actual(name){const fn=body.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text===name);assert.ok(fn,name);return fn.getText(parsed);}
function execute(bindings,text){bindings={authoredCopy,authoredErrorCopy,flushDynamicWorkflowDrafts,...bindings};return new Function(...Object.keys(bindings),ts.transpileModule(text,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText)(...Object.values(bindings));}
const memory=()=>{const data=new Map();return {getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value),data};};
const deferred=()=>{let resolve;const promise=new Promise(done=>resolve=done);return{promise,resolve};};

test('actual App import only observes matching-scope SEND, clears pending completion and never downgrades a final answer',()=>{
 let rows=[],pending;const counts={record:0,index:0,observe:0};const scope={projectId:'p',workspaceId:'w',conversationId:'c'},record={id:'q',revision:1,sha256:'a'.repeat(64),scope,delivery:'DRAFT',comment:'批注',regions:[],message:{operation_id:'op',status:'PROCESSING'}};
 const fn=execute({sameQuickScope,quickIntentSummary,quickScope:()=>scope,nativeMessages:()=>rows,nativePending:()=>pending,setNativePending:value=>pending=value,nativeTimer:undefined,clearTimeout,nativeMessagePending,forgetNativeMessage:id=>{if(pending===id)pending=undefined;},nativeRecord:(input,message)=>{counts.record++;rows=[{input,message}];},persistNativeHistory:()=>counts.index++,observeNativeMessage:op=>{counts.observe++;pending=op;}},actual('importQuickRecord')+'\nreturn importQuickRecord;');
 fn(record);fn({...record,delivery:'SEND',scope:{...scope,projectId:'foreign'}});assert.equal(counts.record,0);fn({...record,delivery:'SEND'});assert.equal(counts.record,1);assert.equal(pending,'op');fn({...record,delivery:'SEND',message:{operation_id:'op',status:'COMPLETED',text:'真实完成'}});assert.equal(pending,undefined);fn({...record,delivery:'SEND'});assert.equal(counts.record,2);assert.equal(rows[0].message.text,'真实完成');assert.equal(counts.observe,1);fn({...record,delivery:'SEND',message:{operation_id:'op',status:'RETURNED',text:'已收到真实回执'}});fn({...record,delivery:'SEND'});assert.equal(rows[0].message.status,'RETURNED');assert.equal(rows[0].message.text,'已收到真实回执');assert.equal(counts.observe,1);fn({...record,comment:'',marks:[{kind:'text',x:.2,y:.4,text:'图上实际返工要求'}],delivery:'SEND',message:{operation_id:'op-text',status:'RETURNED',text:'实际回执'}});assert.equal(rows[0].input,'图上文字：图上实际返工要求');
});

test('actual App inspect is GET-only and rejects a late result after the user changed conversation',async()=>{
 const gate=deferred(),opened=[],calls=[];let cid='a';const inspect=execute({historyGeneration:0,project:()=>({id:'p'}),nativeMessages:()=>[{message:{operation_id:'exact-original-op',conversation_id:'a',project_id:'p'}}],ordinaryControlScope,conversationId:()=>cid,action:async(_label,callback)=>callback(),nativeClient:{inspect:async id=>{calls.push(id);return gate.promise;}},nativeRecord:(...value)=>opened.push(value),persistNativeHistory:()=>assert.fail('late result cannot update current history'),nativeMessagePending,observeNativeMessage:()=>assert.fail('no late observer')},actual('inspectNativeOperation')+'\nreturn inspectNativeOperation;');const pending=inspect('exact-original-op','问题正文');cid='b';gate.resolve({operation_id:'exact-original-op',conversation_id:'a',status:'COMPLETED'});await pending;assert.deepEqual(calls,['exact-original-op']);assert.deepEqual(opened,[]);
});

test('actual normal-close callback persists and verifies both composer families, waits for Workbench, and never clears user text',async()=>{
 let flush;function visit(node){if(ts.isCallExpression(node)&&node.expression.getText(parsed).includes('onDraftFlushRequested'))flush=node.arguments[0].getText(parsed);ts.forEachChild(node,visit);}visit(body);assert.ok(flush);
 const storage=memory(),persistence=new ComposerDraftPersistence(storage),lease=persistence.begin('p','s');persistence.initialize(lease);const prompt='保持正在写的整段消息 😀';let completed=false;const gate=deferred(),events=[];
 const closeShell={inert:false},closePorts={...actualFlushers,shell:closeShell,supervisorClient:undefined,conversationReading:{flush(){}},selectedDiscussion:()=>undefined};
 const controller=execute({...closePorts,project:()=>({id:'p'}),activeSession:()=>({id:'s'}),draftReady:()=>true,draftAttachments:()=>[],draftPersistence:persistence,prompt:()=>prompt,localStorage:storage,saveNativeDraft,readNativeDraft,saveNativeText,readNativeText,readComposerDraft,windowMode:()=> 'companion',nativeDraftAttachments:()=>[],quickInbox:{flush:async()=>events.push('quick-persisted')},flushWorkbenchDrafts:async()=>{events.push('workbench-wait');await gate.promise;events.push('workbench-persisted');}},actual('saveComposerDraft')+'\n'+actual('flushCurrentChatDraft')+'\nreturn ('+flush+');');
 const closing=controller().then(()=>completed=true);await new Promise(done=>setImmediate(done));assert.equal(completed,false);assert.equal(closeShell.inert,true);assert.equal(readComposerDraft(storage,'p','s').text,prompt);assert.equal(readNativeText(storage,'p'),prompt);gate.resolve();await closing;assert.equal(completed,true);assert.equal(closeShell.inert,false);assert.deepEqual(events,['quick-persisted','workbench-wait','workbench-persisted']);assert.equal(prompt,'保持正在写的整段消息 😀');
 const failed=execute({...closePorts,project:()=>undefined,saveComposerDraft:()=>{},nativeDraftAttachments:()=>[],localStorage:{setItem(){throw Error('disk full');},getItem(){return null;}},saveNativeDraft,readNativeDraft,saveNativeText,readNativeText,windowMode:()=> 'companion',prompt:()=>prompt,quickInbox:{flush:()=>assert.fail('earlier save failed')},flushWorkbenchDrafts:()=>assert.fail('must retain Main')},actual('flushCurrentChatDraft')+'\nreturn ('+flush+');');await assert.rejects(failed(),/disk full/);assert.equal(closeShell.inert,false,'failure must restore the active surface');
});

test('stable production render projection retains identities across edits/restore while keeping source JSON and property spread intact',async t=>{
 const directory=await mkdtemp(join(process.env.TMPDIR,'stable-items-'));t.after(()=>rm(directory,{recursive:true,force:true}));const file=await readFile(new URL('../src/stable-items.ts',import.meta.url),'utf8'),clientURL=new URL('../node_modules/solid-js/dist/solid.js',import.meta.url).href;
 const js=ts.transpileModule(file,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText.replace(/from ["']solid-js["']/g,`from ${JSON.stringify(clientURL)}`);const path=join(directory,'actual-stable-items.mjs');await writeFile(path,js);const {stableItems}=await import(pathToFileURL(path).href),{createRoot,createSignal}=await import(clientURL);
 createRoot(dispose=>{const [rows,setRows]=createSignal([{id:'a',text:'初稿',nodes:[{id:'n'}]},{id:'b',text:'第二项'}]),projection=stableItems(rows,row=>row.id),first=projection()[0],second=projection()[1];setRows([{id:'a',text:'当前输入未丢',nodes:[{id:'n'}]},{id:'b',text:'新恢复的第二项'}]);assert.equal(projection()[0],first);assert.equal(projection()[1],second);assert.equal(first.text,'当前输入未丢');assert.equal(second.text,'新恢复的第二项');assert.deepEqual({...first},{id:'a',text:'当前输入未丢',nodes:[{id:'n'}]});assert.doesNotThrow(()=>structuredClone(rows()));dispose();});
});


test('actual local persistence warning preserves known operation status and incomplete projection remains inspectable',()=>{
 const notice=execute({},actual('nativeLocalNotice')+'\nreturn nativeLocalNotice;');
 const returned={operation_id:'returned-op',status:'RETURNED',text:'真实答案',local_persistence_status:'FAILED',local_projection:{complete:false}};
 assert.match(notice(returned),/回复已收到.*本机保存未成功.*勿重复发送/);assert.equal(returned.status,'RETURNED');assert.equal(returned.text,'真实答案');
 assert.match(notice({operation_id:'running-op',status:'PROCESSING',local_projection:{complete:false}}),/执行记录不完整/);
 assert.equal(notice({operation_id:'complete-op',status:'RETURNED',local_projection:{complete:true}}),undefined);
});
