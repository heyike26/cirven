import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
import {originalConversationMode,readConversationAgentMode,saveConversationAgentMode} from '../src/conversation-agent-mode.ts';

const storage=()=>{const values=new Map();return {values,getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};};
const source=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8');
const tree=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const app=tree.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='App');
const fn=name=>app.body.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text===name).getText(tree);

test('mode survives restart and project moves by stable discussion, without leaking between discussions',()=>{
 const s=storage();
 for(const mode of ['build','workflow','plan']){
  saveConversationAgentMode(s,'original:a',mode);
  assert.equal(readConversationAgentMode({...s},'original:a','plan'),mode);
  assert.equal(readConversationAgentMode(s,'original:b','plan'),'plan');
 }
 assert.throws(()=>saveConversationAgentMode(s,'','build'),/MODE_UNAVAILABLE/);
 assert.throws(()=>saveConversationAgentMode(s,'original:a','invented'),/MODE_UNAVAILABLE/);
 assert.equal(readConversationAgentMode(s,'original:a','build'),'plan');
});

test('legacy original session uses its last actual user mode, not an assistant or a mode from another session',()=>{
 const session={messages:[{info:{role:'user',agent:'build'}},{info:{role:'assistant',agent:'plan'}}]};
 assert.equal(originalConversationMode(session,'plan'),'build');
 assert.equal(originalConversationMode({messages:[]},'plan'),'plan');
 assert.equal(originalConversationMode(undefined,'build'),'build');
 assert.equal(originalConversationMode({messages:[],work_mode_message:{agent:'workflow'}},'plan'),'workflow');
 const s=storage();saveConversationAgentMode(s,'a','plan');
 assert.equal(readConversationAgentMode(s,'a',originalConversationMode(session,'plan')),'plan');
});

test('actual App mode action saves originals without metadata, and keeps the current mode on failure',()=>{
 const s=storage(),errors=[];let mode='plan',id='original:a';
 const bindings={localStorage:s,threadScope:()=>({conversation_ref:{id}}),saveConversationAgentMode,setOriginalAgent:value=>mode=value,setError:value=>errors.push(value),uiLocale:()=> 'zh-CN'};
 const action=new Function(...Object.keys(bindings),ts.transpileModule(fn('changeConversationAgentMode')+';return changeConversationAgentMode;',{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText)(...Object.values(bindings));
 assert.equal(action('build'),true);assert.equal(mode,'build');assert.equal(readConversationAgentMode(s,id,'plan'),'build');
 s.setItem=()=>{throw Error('disk full');};assert.equal(action('workflow'),false);assert.equal(mode,'build');assert.equal(errors.length,1);
 s.setItem=()=>{};assert.equal(action('plan'),false);assert.equal(mode,'build');
 id='original:b';s.getItem=()=>{throw Error('unreadable');};assert.equal(action('plan'),false);assert.equal(mode,'build');
});

test('corrupt preference fails before adopting a different mode or clearing drafts',()=>{
 const s=storage();s.values.set('eeas-conversation-agent-mode-v1:a','unknown');
 assert.throws(()=>readConversationAgentMode(s,'a','build'),/MODE_UNAVAILABLE/);
 assert.equal(s.values.get('eeas-conversation-agent-mode-v1:a'),'unknown');
 const install=fn('installThread');assert(install.indexOf('readConversationAgentMode')<install.indexOf('draftPersistence.pause()'));
 assert.match(install,/setOriginalAgent\(savedMode\)/);
});

test('the actual toolbar routes through the same durable action and restores the old selection if rejected',()=>{
 let handler;
 function visit(n){if(ts.isJsxOpeningElement(n)&&n.tagName.getText(tree)==='select'&&n.attributes.properties.some(a=>a.name?.getText(tree)==='aria-label'&&a.getText(tree).includes('原会话 Agent'))){handler=n.attributes.properties.find(a=>a.name?.getText(tree)==='onChange').initializer.expression;}ts.forEachChild(n,visit);}
 visit(tree);assert(handler);
 let mode='build';const seen=[];
 const run=new Function('changeConversationAgentMode','originalAgent',ts.transpileModule('return '+handler.getText(tree),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText)(value=>seen.push(value),()=>mode);
 const event={currentTarget:{value:'workflow'}};run(event);
 assert.deepEqual(seen,['workflow']);assert.equal(event.currentTarget.value,'build');
});
