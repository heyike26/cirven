import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
import {createRoot,createSignal,createMemo,createComputed,batch} from '../node_modules/solid-js/dist/solid.js';
import {originalWorkChoiceMatches} from '../src/original-work-input.ts';
import {sameNativeReference} from '../src/native-collaboration.ts';
const source=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8');
const tree=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
let expression;
function visit(node){if(ts.isVariableDeclaration(node)&&node.name.getText(tree)==='quickScopeValue')expression=node.initializer.getText(tree);ts.forEachChild(node,visit);}visit(tree);assert.ok(expression);
const build=ts.transpileModule('return '+expression+';',{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
const make=new Function('createMemo','threadScope','originalSession','providers','providerId','project','workspace','toolMode','activeSession','conversationId','originalAgent','sameNativeReference','originalWorkInputChoice','contextChoice','originalWorkChoiceMatches',build);
const ref=(id)=>({id,revision:1,sha256:'a'.repeat(64)});
test('actual App scope memo ignores 100 original observation/provider polls but reacts to organization and execution generation',()=>{
 createRoot(dispose=>{try{
  const original={project_id:'private_execution',session_ref:ref('original'),generation:1};
  const thread={conversation_ref:{id:'native:'+'b'.repeat(64),revision:1},project_id:null,sources:[{original_source:{kind:'original',...original}}]};
  const [owner,setOwner]=createSignal(original),[scope,setScope]=createSignal(thread),[profiles,setProfiles]=createSignal([{profile_ref:ref('model')}]),[project,setProject]=createSignal(undefined),[space,setSpace]=createSignal(undefined);
  const value=make(createMemo,scope,owner,profiles,()=> 'model',project,space,()=> 'original',()=>undefined,()=> 'chat_id',()=> 'build',sameNativeReference,()=>undefined,()=>undefined,originalWorkChoiceMatches);
  let emissions=0;createComputed(()=>{value();emissions++;});
  for(let i=0;i<100;i++){batch(()=>{setOwner({...original,last_event_seq:i,messages:[{text:String(i)}],native_status:'busy'});setProfiles([{profile_ref:ref('model')}]);});}
  assert.equal(emissions,1);let observed=JSON.parse(value());assert.equal(observed.projectId,undefined);assert.equal(observed.sessionId,undefined);assert.deepEqual(observed.originalSource,original);
  setOwner({...original,generation:2});assert.equal(emissions,2);assert.equal(JSON.parse(value()).originalSource.generation,2);
  batch(()=>{setProject({id:'organization_b'});setSpace({project_id:'organization_a',id:'old_workspace'});setScope({...thread,project_id:'organization_b',conversation_ref:{...thread.conversation_ref,revision:2}});});
  assert.equal(emissions,3);observed=JSON.parse(value());assert.equal(observed.projectId,'organization_b');assert.equal(observed.workspaceId,undefined);assert.equal(observed.originalSource.project_id,'private_execution');assert.equal(observed.conversationRef.revision,2);
 }finally{dispose();}});
});
