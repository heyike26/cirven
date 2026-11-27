import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
import {mcpPermissionVisibility} from '../src/native-tools.ts';

const source=await readFile(new URL('../src/AgentSessionPanel.tsx',import.meta.url),'utf8');
const tree=ts.createSourceFile('AgentSessionPanel.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const component=tree.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='AgentSessionPanel');
const open=component.body.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='open');
const js=ts.transpileModule(open.getText(tree),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return{promise,resolve,reject};};
const chosen=()=>({capability_binding_sha256:'a'.repeat(64),items:[{server_id:'local_echo',config_ref:{id:'local_echo',revision:2,sha256:'b'.repeat(64)},probe_ref:{id:'c'.repeat(32),sha256:'d'.repeat(64)},tool_id:'local_echo_echo',name:'echo',input_schema_sha256:'e'.repeat(64)}]});
function scene(selection){
  if(arguments.length===0)selection=chosen();
  const gate=deferred(),events=[];let current=selection;
  const props={active:true,provider:{id:'configured-model',revision:1,sha256:'f'.repeat(64)},projectId:'own-project',onSelect:value=>events.push(['select',value]),onUse:(value,agent)=>events.push(['use',value,agent]),onToolSelectionUsed:()=>events.push(['consumed'])};
  const names=()=>current?.items.map(item=>item.tool_id)??[],mcpScope=()=>JSON.stringify([props.projectId,current??null,names()]);
  const bindings={props,disposed:false,generation:0,mcpScope,permissionScope:mcpScope(),mcpPermissions:()=>mcpPermissionVisibility(current,names()),useWorkspace:()=>false,workflowEnabled:()=>false,workspaceWrite:()=>false,processStart:()=>false,remoteConnect:()=>false,commands:()=>'',tools:()=>names().join('\n'),literalArray:value=>value.split(/\r?\n/).filter(Boolean),selectedTools:()=>current,setSelectedTools:value=>current=value,workflowProfiles:()=>[],workflow:()=>undefined,agent:()=> 'plan',directory:async()=>events.push(['directory']),agentSessionsApi:{open:async options=>{events.push(['open',options]);return gate.promise;}}};
  const budgetNames=new Set(['ttlSeconds','maxCalls','maxOutputTokens']);
  const budgets=component.body.statements.filter(ts.isVariableStatement).flatMap(statement=>[...statement.declarationList.declarations]).filter(declaration=>ts.isArrayBindingPattern(declaration.name)&&budgetNames.has(declaration.name.elements[0]?.name?.getText(tree))).map(declaration=>'const '+declaration.getText(tree)+';').join('\n');
  assert.equal((budgets.match(/createSignal/g)??[]).length,3,'actual resource defaults stay part of the scenario');
  const createSignal=value=>[()=>value,next=>value=next];
  const execute=new Function(...Object.keys(bindings),'createSignal',ts.transpileModule(budgets,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText+js+'\nreturn open;')(...Object.values(bindings),createSignal);
  return{execute,gate,events,props,change:value=>current=value,get selected(){return current;}};
}

test('explicit creation with selected tools returns to that session without granting effects or sending the draft',async()=>{
  const selection=chosen(),s=scene(selection),pending=s.execute();
  assert.equal(s.events.length,1);assert.equal(s.events[0][0],'open');
  const options=s.events[0][1];assert.deepEqual(options.mcpToolSelection,selection);assert.deepEqual(options.mcpToolAllowlist,['local_echo_echo']);
  assert.equal(options.processStart,false);assert.equal(options.remoteMCPConnect,false);assert.equal(options.workspaceWrite,false);
  const actual={session_ref:{id:'actual-created-owner'}};s.gate.resolve(actual);await pending;
  assert.deepEqual(s.events.slice(1),[['use',actual,'plan'],['consumed'],['directory']]);assert.equal(s.selected,undefined);
});
test('ordinary manual creation retains the existing select-only behavior',async()=>{
  const s=scene(undefined),pending=s.execute(),actual={session_ref:{id:'manual-owner'}};
  s.gate.resolve(actual);await pending;assert.equal(s.events.some(event=>event[0]==='use'),false);assert.deepEqual(s.events[1],['select',actual]);
});
test('a newer tool choice during creation is retained and the older result does not switch the conversation',async()=>{
  const s=scene(),pending=s.execute(),latest={...chosen(),items:[]};s.change(latest);s.gate.resolve({session_ref:{id:'older-created-owner'}});await pending;
  assert.equal(s.selected,latest);assert.deepEqual(s.events.map(event=>event[0]),['open','directory']);
});
test('a refused creation keeps selected tools and does not consume or switch the current discussion',async()=>{
  const s=scene(),before=s.selected,pending=s.execute();s.gate.reject(Error('exact configuration changed'));
  await assert.rejects(pending,/configuration changed/);assert.equal(s.selected,before);assert.deepEqual(s.events.map(event=>event[0]),['open']);
});

test('creation returning after its management workspace closes preserves the tool choice and never changes the discussion',async()=>{
 const s=scene(),before=s.selected,pending=s.execute();s.props.active=false;s.gate.resolve({session_ref:{id:'created-while-closed'}});await pending;
 assert.equal(s.selected,before);assert.deepEqual(s.events.map(event=>event[0]),['open']);
});
