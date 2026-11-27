import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash,webcrypto} from 'node:crypto';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';
import * as solid from 'solid-js';
import * as web from 'solid-js/web';
import {sameQuickScope,quickRef,quickIntentSummary} from '../src/quick-inbox.ts';
import {nativeMessagePending,saveNativeHistory,readNativeHistory} from '../src/native-collaboration.ts';
import {showOriginalLiveMessage} from '../src/original-live-projection.ts';
import {originalReadableParts,originalCompletedToolOnly} from '../src/agent-sessions.ts';
import {originalToolHasDisplayedResult} from '../src/original-command-display.ts';
import {authoredText} from '../src/authored-ui-copy.ts';
import {stableItems} from '../src/stable-items.ts';

const source=await readFile(new URL('../src/App.tsx',import.meta.url),'utf8');
const parsed=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
assert.deepEqual(parsed.parseDiagnostics,[]);
const app=parsed.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='App');
function actual(name){const node=app.body.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text===name);assert.ok(node,'actual App function '+name);return node.getText(parsed);}
const restore=app.body.statements.find(node=>ts.isExpressionStatement(node)&&ts.isCallExpression(node.expression)&&node.expression.expression.getText(parsed)==='createEffect'&&node.getText(parsed).includes('restoreNativeHistory')).expression.arguments[0].getText(parsed);
const tick=()=>new Promise(done=>setImmediate(done));
const deferred=()=>{let resolve;const promise=new Promise(done=>resolve=done);return{promise,resolve};};
const scope=(suffix='a')=>({projectId:'p-'+suffix,workspaceId:'w-'+suffix,sessionId:'s-'+suffix,conversationId:'c-'+suffix});
const record=(suffix='a',extra={})=>({id:'signed-'+suffix,revision:1,sha256:'a'.repeat(64),scope:scope(suffix),delivery:'SEND',comment:'原问题 '+suffix,regions:[],operationId:'quick_'+suffix,message:{operation_id:'quick_'+suffix,conversation_id:'c-'+suffix,status:'RETURNED',text:'## 原回复 '+suffix+'\n\n完整回答。'},...extra});

function harness(initial=[record()]){
 const state={scope:scope(),records:initial,messages:[],pending:undefined,gets:[],saved:[],opened:[],links:[],flights:[],clock:0};
 const data=new Map(),storage={getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value)};state.storage=storage;
 const functions=['nativeRecord','nativeReplyText','openAssistantLink','savedQuickReply','viewSavedQuickReply','importQuickRecord','importSavedQuickReplies'].map(actual).join('\n');
 const bindings={historyReloadToken:()=>state.clock,sameQuickScope,quickRef,quickIntentSummary,nativeMessagePending,crypto:webcrypto,TextEncoder,
  conversationId:()=>state.scope.conversationId,quickScope:()=>state.scope,quickRecords:()=>state.records,nativeMessages:()=>state.messages,
  setNativeMessages:next=>state.messages=typeof next==='function'?next(state.messages):next,setNativePending:value=>state.pending=value,nativePending:()=>state.pending,
  persistNativeHistory:()=>{saveNativeHistory(storage,state.messages.map(row=>({input:row.input,operation_id:row.message.operation_id})),state.scope.conversationId);state.saved.push(state.messages.map(row=>row.message.operation_id));},
  nativeBridge:{openNativeReplyLink:async body=>state.links.push(body)},quickBridge:{openQuickDraft:async ref=>{state.opened.push(ref);const saved=state.records.find(row=>row.id===ref.id&&row.revision===ref.revision&&row.sha256===ref.sha256);return state.openResult??{draft:{readonly:true,id:ref.id,operationId:saved?.operationId},result:{operation_id:saved?.operationId}};}},
  action:async(_label,run)=>run(),untrack:fn=>fn(),
  localStorage:storage,readReviewDraft:()=>null,readNativeHistory,
  restoreNativeHistory:(_client,entries)=>{const flight=deferred();state.gets.push(entries);state.flights.push(flight);return flight.promise;},
  setReviewContinue:()=>{},setReviewRetryContext:()=>{},setReviewUnknown:()=>{},setPageReview:()=>{},clearTimeout:()=>{},
  observeNativeMessage:(id,text)=>{state.pending=id;state.observed.set(id,text);},forgetNativeMessage:id=>{state.observed.delete(id);if(state.pending===id)state.pending=undefined;},
 };
 state.observed=new Map();
 const code=ts.transpileModule(functions+`\nconst restore=(${restore});\nreturn {restore,nativeReplyText,savedQuickReply,viewSavedQuickReply,importSavedQuickReplies,openAssistantLink,dispose(){nativeDisposed=true;}};`,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
 const api=new Function(...Object.keys(bindings),'let nativeDisposed=false,historyGeneration=0,nativeTimer,nativeClient={};const nativeObserved=new Map();\n'+code)(...Object.values(bindings));
 return{state,...api};
}

test('only real returned assistant text uses reply presentation, while local inspection failures stay plain',()=>{
 const h=harness();
 for(const status of ['RETURNED','SUCCEEDED','COMPLETED'])assert.equal(h.nativeReplyText({status,text:'**actual**'}),'**actual**');
 for(const status of ['NO_RECORD','NOT_FOUND','INSPECTION_UNAVAILABLE','UNKNOWN_OUTCOME','NOT_DISPATCHED','NOT_CONFIGURED','RUNNING','FAILED'])assert.equal(h.nativeReplyText({status,text:'status **literal**'}),undefined);
 for(const value of [{status:'RETURNED',text:' '},{status:'RETURNED',summary:'summary **literal**'},{status:'FAILED',error:{message:'error **literal**'}}])assert.equal(h.nativeReplyText(value),undefined);
});

test('Quick view opens only the latest exact signed SEND for the visible operation and current discussion',async()=>{
 const good=record(),newer={...good,revision:3,sha256:'c'.repeat(64)};
 const h=harness([{...good,delivery:'DRAFT',revision:99},{...good,scope:scope('foreign'),revision:100},{...good,operationId:'other',revision:101},{...good,message:{...good.message,operation_id:'other'},revision:102},good,newer]);
 assert.equal(h.savedQuickReply('quick_a'),newer);await h.viewSavedQuickReply(newer);assert.deepEqual(h.state.opened,[quickRef(newer)]);
 await assert.rejects(h.viewSavedQuickReply(good),/原记录已变化/);h.state.scope=scope('other');assert.equal(h.savedQuickReply('quick_a'),undefined);await assert.rejects(h.viewSavedQuickReply(newer),/原记录已变化/);assert.equal(h.state.opened.length,1);
});

test('Quick view reports a busy, stale or mismatched host response instead of claiming the saved reply opened',async()=>{
 const h=harness();for(const result of [{status:'BUSY'},{status:'STALE'},{status:'UNAVAILABLE'},{status:'CLOSING'},{draft:{readonly:false}},{draft:{readonly:true,id:'foreign',operationId:'quick_a'},result:{operation_id:'quick_a'}}]){h.state.openResult=result;await assert.rejects(h.viewSavedQuickReply(h.state.records[0]),/浮窗暂不可用/);}
 assert.equal(h.state.opened.length,6);assert.equal(h.state.messages.length,0);assert.equal(h.state.records[0].message.text,record().message.text);
});

test('history reset and a late missing-service observation preserve the durable returned reply without resending',async()=>{
 const h=harness();h.importSavedQuickReplies();assert.equal(h.state.messages[0].message.text,record().message.text);
 h.restore();assert.equal(h.state.messages.length,1,'the reset immediately reprojects the saved SEND');
 h.state.flights[0].resolve([{input:'indexed',message:{operation_id:'quick_a',conversation_id:'c-a',status:'NO_RECORD',text:'本机尚未查到服务记录'}}]);await tick();
 assert.equal(h.state.messages.length,1);assert.equal(h.state.messages[0].message.status,'RETURNED');assert.equal(h.state.messages[0].message.text,record().message.text);assert.equal(h.state.pending,undefined);assert.deepEqual(h.state.opened,[]);assert.deepEqual(h.state.links,[]);
});

test('history captures the existing Main index before the Quick projection persists and merges both original operations',async()=>{
 const h=harness();saveNativeHistory(h.state.storage,[{input:'Main original question',operation_id:'main_old_a'}],'c-a');
 h.restore();assert.deepEqual(h.state.gets[0].map(row=>row.operation_id),['main_old_a']);
 h.state.flights[0].resolve([{input:'Main original question',message:{operation_id:'main_old_a',conversation_id:'c-a',status:'RETURNED',text:'Main original answer'}}]);await tick();
 assert.deepEqual(new Set(h.state.messages.map(row=>row.message.operation_id)),new Set(['main_old_a','quick_a']));
 assert.deepEqual(new Set(readNativeHistory(h.state.storage,'c-a').map(row=>row.operation_id)),new Set(['main_old_a','quick_a']));
 assert.equal(h.state.messages.find(row=>row.message.operation_id==='main_old_a').message.text,'Main original answer');
});

test('late old-discussion history cannot clear or mix the new discussion saved reply',async()=>{
 const h=harness([record(),record('b')]);h.restore();h.state.scope=scope('b');h.restore();
 assert.deepEqual(h.state.messages.map(row=>row.message.operation_id),['quick_b']);
 h.state.flights[0].resolve([{input:'old',message:{operation_id:'quick_a',conversation_id:'c-a',status:'RETURNED',text:'late old'}}]);await tick();
 assert.deepEqual(h.state.messages.map(row=>row.message.operation_id),['quick_b']);
 h.state.flights[1].resolve([{input:'new',message:{operation_id:'quick_b',conversation_id:'c-b',status:'INSPECTION_UNAVAILABLE'}}]);await tick();
 assert.deepEqual(h.state.messages.map(row=>row.message.operation_id),['quick_b']);assert.equal(h.state.messages[0].message.text,record('b').message.text);
});

test('a late inbox arrival and pending service observation settle to saved terminal prose without starting a poll',async()=>{
 const h=harness([]);h.restore();h.state.records=[record()];h.importSavedQuickReplies();
 h.state.flights[0].resolve([{input:'pending',message:{operation_id:'quick_a',conversation_id:'c-a',status:'RUNNING'}}]);await tick();
 assert.equal(h.state.messages[0].message.status,'RETURNED');assert.equal(h.state.pending,undefined);assert.equal(h.state.observed.size,0);assert.equal(h.state.gets.length,1);
});

test('native prose links bind the exact operation and canonical text digest, then reject a navigation during hashing',async()=>{
 const h=harness();h.importSavedQuickReplies();const value=h.state.messages[0].message;
 await h.openAssistantLink(value,'https://example.com/reference');assert.deepEqual(h.state.links,[{operationId:'quick_a',url:'https://example.com/reference',text_sha256:createHash('sha256').update(JSON.stringify(value.text)).digest('hex')}]);
 const pending=h.openAssistantLink(value,'https://example.com/obsolete');h.state.scope=scope('b');await assert.rejects(pending,/当前工作已切换/);assert.equal(h.state.links.length,1);
});

function jsx(predicate){let found;function visit(node){if(!found&&(ts.isJsxElement(node)||ts.isJsxSelfClosingElement(node))&&predicate(node))found=node;ts.forEachChild(node,visit);}visit(app.body);assert.ok(found,'actual reply JSX boundary');return found.getText(parsed);}
function renderFragment(fragment,context,prefix=''){
 const js=ts.transpileModule(prefix+'\nconst content=()=>('+fragment+');',{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;
 let code=babel.transformSync(js,{filename:'actual-App-reply.jsx',presets:[[solidPreset,{generate:'ssr',hydratable:false}]],configFile:false,babelrc:false}).code;
 const tree=ts.createSourceFile('reply.mjs',code,ts.ScriptTarget.Latest,true,ts.ScriptKind.JS),bindings={createSignal:solid.createSignal,onCleanup:solid.onCleanup,...context,Show:solid.Show,For:solid.For};
 for(const node of [...tree.statements].reverse())if(ts.isImportDeclaration(node)){const imported=node.moduleSpecifier.text==='solid-js/web'?web:solid;for(const item of node.importClause.namedBindings.elements)bindings[item.name.text]=imported[item.propertyName?.text??item.name.text];code=code.slice(0,node.getStart(tree))+code.slice(node.end);}
 return web.renderToString(new Function(...Object.keys(bindings),code+'\nreturn content;')(...Object.values(bindings)));
}

function componentFunctions(text){const tree=ts.createSourceFile('actual.tsx',text,99,true,4);return tree.statements.filter(ts.isFunctionDeclaration).map(node=>node.getText(tree).replace(/^export default /,'').replace(/^export /,'')).join('\n');}
const livePrefix=componentFunctions(await readFile(new URL('../src/OriginalLiveMessage.tsx',import.meta.url),'utf8'));
const nativePrefix=componentFunctions(await readFile(new URL('../src/NativeMessageFeedback.tsx',import.meta.url),'utf8'));
const liveBindings={originalReadableParts,originalCompletedToolOnly,originalToolHasDisplayedResult,showOriginalLiveMessage,ContextUsage:()=>null,originalSession:()=>({project_id:'p',session_ref:{id:'owner'}}),threadScope:()=>undefined,uiLocale:()=> 'zh-CN',displayedCommands:()=>new Set()};
test('actual three App assistant boundaries use the reply component while original user text and native fallback remain plain',()=>{
 const seen=[],AssistantReply=props=>{seen.push(props.text);return 'SAFE_ASSISTANT_BODY';},base={...liveBindings,AssistantReply,stableItems,uiText:text=>text};
 const original=jsx(node=>node.getText(parsed).startsWith('<For each={originalMessageRows().filter('));
 const result=renderFragment(original,{...base,originalMessageRows:()=>[{info:{role:'user'},parts:[{type:'text',text:'user **literal**'}]},{info:{role:'assistant'},parts:[{type:'text',text:'## original assistant'}]}]},livePrefix);
 assert.deepEqual(seen,['## original assistant']);assert.match(result,/user \*\*literal\*\*/);assert.match(result,/SAFE_ASSISTANT_BODY/);
 seen.length=0;const supervised=jsx(node=>node.getText(parsed).startsWith('<Show when={detail().answer_text?.trim()}'));
 renderFragment(supervised,{...base,detail:()=>({answer_text:'## task assistant'})});assert.deepEqual(seen,['## task assistant']);seen.length=0;
 const h=harness(),native=jsx(node=>ts.isJsxSelfClosingElement(node)&&node.tagName.getText(parsed)==='NativeMessageFeedback');
 const nativeBindings={authoredText,TaskStatus:props=>props.value,nativeLocalNotice:()=>undefined,busy:()=>'',nativeBridge:undefined,project:()=>undefined,conversationId:()=> 'native-test'};
 renderFragment(native,{...base,...nativeBindings,nativeReplyText:h.nativeReplyText,record:{message:{status:'RETURNED',text:'## native assistant'}},openAssistantLink:()=>{}},nativePrefix);assert.deepEqual(seen,['## native assistant']);seen.length=0;
 const fallback=renderFragment(native,{...base,...nativeBindings,nativeReplyText:h.nativeReplyText,record:{message:{status:'FAILED',error:{message:'error **literal**'}}},openAssistantLink:()=>{}},nativePrefix);assert.deepEqual(seen,[]);assert.match(fallback,/error \*\*literal\*\*/);
});

// Actual original-session JSX, including the non-text fallback that previously
// referenced an out-of-scope variable. No source repair inside the renderer.
test('non-text original records stay intact while live presentation shows tool status and safe attachment labels',()=>{
 const fragment=jsx(node=>node.getText(parsed).startsWith('<For each={originalMessageRows().filter('));
 for(const part of [{type:'tool',tool:'read',state:{status:'completed',input:{filePath:'own-fixture.txt'},output:'exact tool detail'}},{type:'reasoning',text:'retained non-text detail'},{type:'file',filename:'原件😀.txt',url:'own-fixture-only'}]){
  const message={info:{id:'original-fixture-message',role:'assistant'},parts:[part]},before=JSON.stringify(message);
  const html=renderFragment(fragment,{...liveBindings,AssistantReply:()=>{throw Error('Non-text must not become assistant prose');},stableItems,uiText:text=>text,originalMessageRows:()=>[message]},livePrefix);
  if(part.type==='tool')assert.match(html,/工具执行记录.*已完成/);if(part.type==='file')assert.ok(html.includes(part.filename));if(part.type==='reasoning')assert.ok(!html.includes(part.text));assert.equal(JSON.stringify(message),before);assert.ok(!html.includes('exact tool detail'),'full original tool details belong to the saved task view');
 }
});
