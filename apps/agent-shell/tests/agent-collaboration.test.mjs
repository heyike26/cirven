import * as language from './fixture-ui-language.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';
import * as solid from '../node_modules/solid-js/dist/solid.js';
import cytoscape from 'cytoscape';
import * as apiModule from '../src/api.ts';
import * as native from '../src/native-collaboration.ts';
const app=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const universalSource=(await readFile(resolve(app,'node_modules/solid-js/universal/dist/universal.js'),'utf8')).replace("from 'solid-js'",`from '${pathToFileURL(resolve(app,'node_modules/solid-js/dist/solid.js')).href}'`);
const {createRenderer}=await import('data:text/javascript;base64,'+Buffer.from(universalSource).toString('base64'));
const source=await readFile(resolve(app,'src/AgentCollaborationGraph.tsx'),'utf8');
function compile(renderer){
 const js=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;
 let code=babel.transformSync(js,{filename:'AgentCollaborationGraph.jsx',presets:[[solidPreset,{generate:'universal',moduleName:'test-renderer'}]],configFile:false,babelrc:false}).code;
 const parsed=ts.createSourceFile('component.mjs',code,ts.ScriptTarget.Latest,true,ts.ScriptKind.JS),bindings={};
 for(const node of [...parsed.statements].reverse())if(ts.isImportDeclaration(node)){
  const id=node.moduleSpecifier.text,imported=id==='solid-js'?solid:id==='test-renderer'?renderer:id==='cytoscape'?{default:cytoscape}:id==='./api'?apiModule:id==='./ui-language'?language:native;
  if(node.importClause?.name)bindings[node.importClause.name.text]=imported.default;
  if(node.importClause?.namedBindings&&ts.isNamedImports(node.importClause.namedBindings))for(const item of node.importClause.namedBindings.elements)bindings[item.name.text]=imported[item.propertyName?.text??item.name.text];
  code=code.slice(0,node.getStart(parsed))+code.slice(node.end);
 }
 code=code.replaceAll('export default function ','function ').replaceAll('export function ','function ');
 return new Function(...Object.keys(bindings),code+'\nreturn {AgentCollaborationGraph,assertCollaboration,updateCollaborationGraph};')(...Object.values(bindings));
}
const session=(extra={})=>({project_id:'project',session_ref:{id:'a'.repeat(32),revision:1,sha256:'a'.repeat(64)},generation:3,original_session_id:'ses_root',observation_at:'one',last_event_seq:1,children:[{id:'ses_child'}],...extra});
const snapshot=(s=session(),extra={})=>({project_id:s.project_id,session_ref:s.session_ref,generation:s.generation,root_native_session_id:s.original_session_id,source_ref:{id:s.session_ref.id,revision:1,sha256:'b'.repeat(64)},observation_at:s.observation_at,observation_is_current:true,read_only:true,model_calls:0,
 nodes:[{id:'ses_root',kind:'ROOT',title:'主会话',observed_status:'idle',models:[],pending_request_refs:[]},{id:'ses_child',kind:'CHILD',title:'实际子会话',observed_status:'UNKNOWN',models:[{provider_id:'provider',model_id:'selected',evidence_level:'TASK_METADATA_SELECTION',evidence_ref:{}}],pending_request_refs:[]}],
 edges:[{id:'lineage',kind:'LINEAGE',source:'ses_root',target:'ses_child',evidence_ref:{}},{id:'attempt_one',kind:'DELEGATION',source:'ses_root',target:'ses_child',call_id:'call_one',observed_tool_status:'completed',description:'实际检查',evidence_ref:{}}],coverage:{root_messages_observed:1,direct_children_observed:1,child_messages_observed:false,subtree_complete:false,gaps:[]},...extra});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function harness(t,client,initial=session()){
  const host={createElement:type=>({type,props:{},children:[],parent:null}),createTextNode:value=>({type:'#text',value,children:[],parent:null}),
    isTextNode:node=>node.type==='#text',replaceText:(node,value)=>{node.value=value;},
    insertNode(parent,node,anchor){if(node.parent){const old=node.parent.children;old.splice(old.indexOf(node),1);}node.parent=parent;
      const index=anchor?parent.children.indexOf(anchor):-1;parent.children.splice(index<0?parent.children.length:index,0,node);},
    removeNode(parent,node){const index=parent.children.indexOf(node);if(index>=0)parent.children.splice(index,1);node.parent=null;},
    setProperty:(node,name,value)=>{node.props[name]=value;},getParentNode:node=>node.parent,getFirstChild:node=>node.children[0],
    getNextSibling:node=>node.parent?.children[node.parent.children.indexOf(node)+1]};
 const renderer=createRenderer(host),compiled=compile(renderer),cores=[];
 const [current,setCurrent]=solid.createSignal(initial),[active,setActive]=solid.createSignal(true);
 const container=host.createElement('root'),dispose=renderer.render(()=>compiled.AgentCollaborationGraph({get session(){return current();},get active(){return active();},client,graphFactory:options=>{const core=cytoscape({...options,container:undefined,headless:true,styleEnabled:true});cores.push(core);return core;}}),container);t.after(dispose);
 const nodes=()=>{const result=[];const walk=node=>{result.push(node);for(const child of node.children)walk(child);};walk(container);return result;};
 return {compiled,cores,setCurrent,setActive,nodes,text:()=>nodes().filter(node=>node.type==='#text').map(node=>node.value).join(' '),find:(type,filter=()=>true)=>nodes().find(node=>node.type===type&&filter(node))};
}
test('actual snapshot guard rejects foreign project, generation, root, edges and complete-coverage claims',t=>{
 const h=harness(t,{request:()=>assert.fail('collapsed must not request')});
 assert.deepEqual(h.compiled.assertCollaboration(snapshot(),session()),snapshot());
 for(const extra of [{project_id:'foreign'},{generation:4},{root_native_session_id:'foreign'},{coverage:{child_messages_observed:true,subtree_complete:true}},{edges:[{id:'false',kind:'HANDOFF',source:'ses_root',target:'ses_child'}]}])assert.throws(()=>h.compiled.assertCollaboration(snapshot(session(),extra),session()));
});
test('real Cytoscape update preserves viewport, dragged position and selected node across status ticks',t=>{
 const h=harness(t,{request:()=>assert.fail('no request')}),core=cytoscape({headless:true,elements:[],layout:{name:'preset'}});t.after(()=>core.destroy());
 h.compiled.updateCollaborationGraph(core,snapshot());core.getElementById('ses_child').position({x:731,y:237});core.getElementById('ses_child').select();core.viewport({zoom:1.6,pan:{x:32,y:71}});
 h.compiled.updateCollaborationGraph(core,snapshot(session(),{edges:[...snapshot().edges,{...snapshot().edges[1],id:'attempt_two',call_id:'call_two'}]}));
 assert.deepEqual(core.getElementById('ses_child').position(),{x:731,y:237});assert.deepEqual(core.pan(),{x:32,y:71});assert.equal(core.zoom(),1.6);assert.ok(core.getElementById('ses_child').selected());assert.equal(core.edges().length,3);
});
test('actual Solid expand, node callback, collapse and observation tick stay read-only and do not refit',async t=>{
 const calls=[],h=harness(t,{request:async(...args)=>{calls.push(args);return snapshot();}});await tick();assert.equal(calls.length,0);
 h.find('details').props.onToggle({currentTarget:{open:true}});await tick();await tick();assert.equal(calls.length,1);assert.match(h.text(),/未判定完成/);
 h.find('button',node=>node.props['aria-pressed']===false&&node.children.some(child=>child.value==='实际子会话')).props.onClick();await tick();assert.match(h.text(),/未读取子消息/);assert.match(h.text(),/call_one/);
 const core=h.cores[0];core.viewport({zoom:1.4,pan:{x:90,y:34}});core.getElementById('ses_child').position({x:600,y:100});
 h.setCurrent(session({observation_at:'two',last_event_seq:2}));await tick();await tick();assert.deepEqual(core.pan(),{x:90,y:34});assert.equal(core.zoom(),1.4);assert.deepEqual(core.getElementById('ses_child').position(),{x:600,y:100});
 h.find('details').props.onToggle({currentTarget:{open:false}});const count=calls.length;h.setCurrent(session({observation_at:'three',last_event_seq:3}));await tick();assert.equal(calls.length,count);
 assert.ok(calls.every(call=>call[1]==='GET'&&call[2]===undefined&&call[0].includes('generation=3')&&call[0].includes('session_sha256=')));
});
test('late old-scope GET cannot replace new generation and failures leave honest error',async t=>{
 let release;const gate=new Promise(resolve=>release=resolve),calls=[];const h=harness(t,{request:async(...args)=>{calls.push(args);return calls.length===1?gate:snapshot(session({generation:4}));}});await tick();h.find('details').props.onToggle({currentTarget:{open:true}});await tick();h.setCurrent(session({generation:4}));await tick();release(snapshot(session(),{nodes:[{...snapshot().nodes[0],title:'OLD_SCOPE_MUST_NOT_SHOW'}]}));await tick();await tick();assert.doesNotMatch(h.text(),/OLD_SCOPE_MUST_NOT_SHOW/);assert.match(h.text(),/实际子会话/);
 h.setActive(false);const before=calls.length;h.find('button').props.onClick();await tick();assert.equal(calls.length,before);
});
