import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,writeFile,mkdtemp,rm} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';
import {renderToString} from 'solid-js/web';
import {QuickInbox,quickRef,quickTextNotes,quickHasIntent,quickIntentSummary,validQuickRecord} from '../src/quick-inbox.ts';
const source=await readFile(new URL('../src/QuickInboxPanel.tsx',import.meta.url),'utf8');
const base={id:'frozen-feedback',revision:3,sha256:'a'.repeat(64),scope:{projectId:'p',workspaceId:'w',sessionId:'s',conversationId:'conversation'},delivery:'DRAFT',comment:'',regions:[],contextRef:{id:'frozen-page',revision:7,sha256:'b'.repeat(64)}};
const text=(value='请改这里',tag='kind')=>({[tag]:'text',x:.3,y:.7,text:value});
const memory=()=>{const values=new Map();return{getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};};
const signal=value=>{const read=()=>value;read.set=next=>value=next;return read;};

test('bounded kind/type text marks are actual intent; pure shapes, bad coordinates, malformed Unicode and oversized contracts are not',()=>{
 for(const tag of ['kind','type']){const record={...base,marks:[text('😀'.repeat(2000),tag)]};assert.equal(quickTextNotes(record).length,1);assert.equal(quickHasIntent(record),true);assert.ok(quickIntentSummary(record).includes('😀'));}
 for(const marks of [[{kind:'rectangle',x:0,y:0,width:.5,height:.5}],[text('   ')],[text('\ud800')],[text('字'.repeat(2001))],[{...text(),x:1.1}],[{...text(),y:NaN}],[{...text(),type:'rectangle'}],[{...text(),data_url:'data:image/png;base64,secret'}],Array.from({length:33},()=>text()),Array.from({length:9},()=>text('字'.repeat(2000)))]){const record={...base,marks};assert.deepEqual(quickTextNotes(record),[]);assert.equal(quickHasIntent(record),false);}
 assert.equal(quickTextNotes({...base,marks:Array.from({length:8},()=>text('字'.repeat(2000)))}).length,8);assert.equal(quickHasIntent({...base,comment:'用户实际指令',marks:[{kind:'rectangle'}]}),true);assert.equal(quickHasIntent(base),false);assert.equal(validQuickRecord({...base,snapshot:{preview:{data_url:'data:image/png;base64,secret'}}}),false,'existing screenshot guard is unchanged');
});

test('actual Solid Inbox shows safe text excerpts, keeps scope isolation and enables only explicit text intent',async t=>{
 const directory=await mkdtemp(resolve(process.env.TMPDIR,'quick-text-panel-'));t.after(()=>rm(directory,{recursive:true,force:true}));
 const js=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText.replace(/from ['"]\.\/authored-ui-copy['"]/g,`from '${new URL('../src/authored-ui-copy.ts',import.meta.url).href}'`).replace(/from ['"]\.\/ui-language['"]/g,`from '${new URL('./fixture-ui-language.mjs',import.meta.url).href}'`).replace(/from ['"]\.\/quick-inbox['"]/g,`from '${new URL('../src/quick-inbox.ts',import.meta.url).href}'`);
 const compiled=babel.transformSync(js,{filename:'actual-quick-inbox.jsx',presets:[[solidPreset,{generate:'ssr',hydratable:false}]],configFile:false,babelrc:false}).code.replace(/from (["'])solid-js(\/web)?\1/g,(_all,quote,suffix='')=>`from ${quote}${import.meta.resolve('solid-js'+suffix)}${quote}`),file=resolve(directory,'panel.mjs');await writeFile(file,compiled);const {default:Panel}=await import(pathToFileURL(file).href);
 const props={scope:base.scope,bridge:{submitQuickDraft:()=>assert.fail('render cannot send')},onRefresh:()=>{},onSent:()=>assert.fail('render cannot create a receipt')};
 const html=renderToString(()=>Panel({...props,records:[{...base,marks:[text('请删除 <script>alert(1)</script>'),text('第二条图上要求','type')]}]}));assert.match(html,/图上文字/);assert.match(html,/&lt;script>/);assert.doesNotMatch(html.match(/<small>图上文字<\/small>(.*?)<\/p>/s)[1],/<script>/);assert.match(html,/第二条图上要求/);assert.doesNotMatch(html,/<button[^>]*disabled[^>]*>发送这条反馈/);
 const shapes=renderToString(()=>Panel({...props,records:[{...base,marks:[{kind:'rectangle',x:0,y:0,width:.5,height:.5}]}]}));assert.match(shapes,/<button[^>]*disabled[^>]*>发送这条反馈/);assert.match(shapes,/继续补充/);
 const foreign=renderToString(()=>Panel({...props,records:[{...base,scope:{...base.scope,projectId:'other'},marks:[text('不得进入当前话题')]}]}));assert.doesNotMatch(foreign,/不得进入当前话题/);
});

test('actual Inbox action callback refuses shape-only sends, submits the original exact text-mark ref once and never touches a composer',async()=>{
 const parsed=ts.createSourceFile('QuickInboxPanel.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX),component=parsed.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='QuickInboxPanel'),node=component.body.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='act');assert.ok(node);
 const pending=signal(''),error=signal(''),calls=[],sent=[],open=[],composer={text:'原有完整消息😀\r\n',refs:[{id:'other-basis'}]},before=structuredClone(composer);let resolve;const reply=new Promise(done=>resolve=done);
 const bindings={pending,setPending:pending.set,setError:error.set,quickHasIntent,quickRef,props:{bridge:{submitQuickDraft:body=>{calls.push(body);return reply;},openQuickDraft:async ref=>open.push(ref)},onSent:record=>sent.push(record),onRefresh:()=>{}}};
 const act=new Function(...Object.keys(bindings),ts.transpileModule(node.getText(parsed)+'\nreturn act;',{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText)(...Object.values(bindings));
 await act({...base,marks:[{kind:'rectangle'}]},true);assert.deepEqual(calls,[]);assert.match(error(),/图上添加文字/);await act(base,false);assert.deepEqual(open,[quickRef(base)]);
 const record={...base,marks:[text('直接修改圈中定义')],scope:structuredClone(base.scope)},sending=act(record,true);await act(record,true);assert.deepEqual(calls,[{ref:quickRef(record)}]);assert.deepEqual(composer,before);assert.equal(sent.length,0);resolve({operation_id:'host-original-op',status:'RETURNED',text:'实际回执'});await sending;assert.equal(sent[0].delivery,'SEND');assert.deepEqual(sent[0].marks,record.marks);assert.deepEqual(sent[0].scope,record.scope);assert.deepEqual(composer,before);
});

test('text-mark Inbox persists exact data before ack and restores it without a model request',async()=>{
 const storage=memory(),record={...base,marks:[text('图中已说明实际修改意图')]},acks=[];const bridge={getQuickDrafts:async()=>({records:[record]}),ackQuickDraft:async ref=>{const stored=JSON.parse(storage.getItem('eeas-quick-inbox-v1'));assert.deepEqual(stored.records[0].marks,record.marks);acks.push(ref);},submitQuickDraft:()=>assert.fail('refresh/restart must not send')};const inbox=new QuickInbox(storage,bridge,()=>{});await inbox.flush();inbox.stop();const restart=new QuickInbox(storage,{},()=>{});assert.deepEqual(restart.records[0],record);assert.equal(quickHasIntent(restart.records[0]),true);assert.deepEqual(acks,[quickRef(record)]);
});
