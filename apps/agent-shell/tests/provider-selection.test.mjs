import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';
import * as solid from '../node_modules/solid-js/dist/solid.js';
import {sortModelProfiles,modelProfileConnectionHint} from '../src/model-catalog.ts';
import {translateUI} from '../src/ui-language.ts';

// Actual App listbox JSX, original click closures, provider signals and the real
// saved-ID initialization block. Only the DOM, provider-directory response and
// storage are UNIT boundaries. This does not qualify native browser focus/layout.
const app=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const source=readFileSync(resolve(app,'src/App.tsx'),'utf8');
const parsed=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const lists=[],signals=new Map(),initializations=[];
function visit(node){
 if(ts.isJsxElement(node)&&node.openingElement.attributes.properties.some(property=>ts.isJsxAttribute(property)&&property.name.getText(parsed)==='role'&&ts.isStringLiteral(property.initializer)&&property.initializer.text==='listbox')&&node.openingElement.attributes.properties.some(property=>ts.isJsxAttribute(property)&&property.name.getText(parsed)==='class'&&ts.isStringLiteral(property.initializer)&&property.initializer.text==='as-composer-model-list'))lists.push(node);
 if(ts.isVariableDeclaration(node)&&ts.isArrayBindingPattern(node.name)&&['providers','providersReady','providerId'].includes(node.name.elements[0]?.name?.getText(parsed)))signals.set(node.name.elements[0].name.getText(parsed),node.getText(parsed));
 if(ts.isTryStatement(node)&&node.tryBlock.statements.some(statement=>ts.isVariableStatement(statement)&&statement.declarationList.declarations.some(declaration=>declaration.name.getText(parsed)==='savedProvider')))initializations.push(node.tryBlock.statements.map(statement=>statement.getText(parsed)).join('\n'));
 ts.forEachChild(node,visit);
}
visit(parsed);assert.equal(lists.length,1,'pin the one actual App provider listbox');assert.equal(initializations.length,1,'pin actual saved-provider initialization');assert.equal(signals.size,3);
const fixture=`import {For,Show,createSignal} from 'solid-js';
import {Icon} from 'test-icon';
import {sortModelProfiles,modelProfileConnectionHint} from 'test-catalog';
import {t as uiText} from 'test-language';
export default function Fixture(props){
 ${[...signals.values()].map(declaration=>'const '+declaration+';').join('\n')}
 const localStorage=props.storage,api=props.api,closeComposerMenu=props.onClose,railKey=props.onKey;
 props.expose({providers,setProviders,providersReady,providerId,setProviderId,initialize:async()=>{${initializations[0]}}});
 return (${lists[0].getText(parsed)});
}`;
const js=ts.transpileModule(fixture,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;
const compiled=babel.transformSync(js,{filename:'actual-App-provider-listbox.jsx',presets:[[solidPreset,{generate:'universal',moduleName:'test-renderer'}]],configFile:false,babelrc:false}).code;
const code=ts.transpileModule(compiled,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText;
const universal=readFileSync(resolve(app,'node_modules/solid-js/universal/dist/universal.js'),'utf8').replace("from 'solid-js'",`from '${pathToFileURL(resolve(app,'node_modules/solid-js/dist/solid.js')).href}'`);
const {createRenderer}=await import('data:text/javascript;base64,'+Buffer.from(universal).toString('base64'));
const tick=()=>new Promise(done=>setImmediate(done));
const profile=(id,revision=1)=>({profile_ref:{id,revision,sha256:'a'.repeat(64)},model_id:'model-'+id});
function storage(value){const values=new Map(value===undefined?[]:[['eeas-provider-id',value]]),writes=[];return {values,writes,getItem:key=>values.get(key)??null,setItem:(key,value)=>{writes.push([key,value]);values.set(key,value);}};}
function mount(t,{local=storage(),read=async()=>[]}={}){
 const callbacks=[],requests=[],api={providers:async()=>{requests.push('providers');return read();}};
 const host={createElement:type=>({type,props:{},children:[],parent:null}),createTextNode:value=>({type:'#text',value,children:[],parent:null}),isTextNode:node=>node.type==='#text',replaceText:(node,value)=>{node.value=value;},
  insertNode(parent,node,anchor){if(node.parent){const old=node.parent.children;old.splice(old.indexOf(node),1);}node.parent=parent;const at=anchor?parent.children.indexOf(anchor):-1;parent.children.splice(at<0?parent.children.length:at,0,node);},removeNode(parent,node){const at=parent.children.indexOf(node);if(at>=0)parent.children.splice(at,1);node.parent=null;},setProperty:(node,name,value)=>{node.props[name]=value;},getParentNode:node=>node.parent,getFirstChild:node=>node.children[0],getNextSibling:node=>node.parent?.children[node.parent.children.indexOf(node)+1]};
 const renderer=createRenderer(host),module={exports:{}};
 const require=name=>{if(name==='solid-js')return solid;if(name==='test-renderer')return renderer;if(name==='test-icon')return {Icon:()=>null};if(name==='test-catalog')return {sortModelProfiles,modelProfileConnectionHint};if(name==='test-language')return {t:(key,params)=>translateUI(key,'zh-CN',params)};throw Error('unexpected dependency '+name);};
 new Function('require','module','exports',code)(require,module,module.exports);
 const container=host.createElement('root');let owner;
 const dispose=renderer.render(()=>module.exports.default({storage:local,api,onClose:value=>callbacks.push(['close',value]),onKey:()=>callbacks.push(['key']),expose:value=>{owner=value;}}),container);t.after(dispose);
 const nodes=()=>{const values=[];const walk=node=>{values.push(node);node.children.forEach(walk);};walk(container);return values;};
 const options=()=>nodes().filter(node=>node.type==='button'&&node.props.role==='option');
 const selected=()=>options().filter(node=>node.props['aria-selected']===true).map(node=>node.props.title);
 return {owner,local,callbacks,requests,options,selected,choice:id=>options().find(node=>node.props.title==='model-'+id),clear:()=>options().find(node=>!node.props.title),text:()=>nodes().filter(node=>node.type==='#text').map(node=>node.value).join(' ')};
}
function click(button){assert.ok(button,'actual option exists');button.props.onClick();}

test('actual App listbox restores the stored ID only after the provider directory arrives',async t=>{
 let resolveRead;const pending=new Promise(resolve=>{resolveRead=resolve;}),local=storage('qa-r3-enterprise'),view=mount(t,{local,read:()=>pending});
 const loading=view.owner.initialize();await tick();assert.equal(view.owner.providerId(),'');assert.deepEqual(view.selected(),[]);assert.equal(view.options().length,0);
 resolveRead([profile('other'),profile('qa-r3-enterprise')]);await loading;await tick();
 assert.equal(view.owner.providerId(),'qa-r3-enterprise');assert.deepEqual(view.selected(),['model-qa-r3-enterprise']);assert.equal(view.owner.providersReady(),true);assert.deepEqual(local.writes,[]);assert.deepEqual(view.callbacks,[]);assert.deepEqual(view.requests,['providers']);
});

test('actual reactive listbox keeps identity when refreshed profiles replace every object',async t=>{
 const original=[profile('a'),profile('b')],view=mount(t,{local:storage('b'),read:async()=>original});await view.owner.initialize();await tick();assert.deepEqual(view.selected(),['model-b']);
 const refreshed=original.map(item=>profile(item.profile_ref.id,2));assert.notEqual(refreshed[1],original[1]);view.owner.setProviders(refreshed);await tick();assert.deepEqual(view.selected(),['model-b']);assert.equal(view.owner.providerId(),'b');
 view.owner.setProviders([...refreshed].reverse().map(item=>({...item})));await tick();assert.deepEqual(view.selected(),['model-b']);assert.deepEqual(view.callbacks,[]);assert.deepEqual(view.local.writes,[]);assert.deepEqual(view.requests,['providers']);
});

test('missing identities and an explicitly cleared choice never silently select another model',async t=>{
 const local=storage('removed'),view=mount(t,{local,read:async()=>[profile('available')]});await view.owner.initialize();await tick();assert.equal(view.owner.providerId(),'');assert.deepEqual(view.selected(),[]);assert.equal(view.clear(),undefined);assert.deepEqual(local.writes,[]);
 view.owner.setProviderId('removed');await tick();assert.deepEqual(view.selected(),[]);assert.ok(view.clear());view.owner.setProviders([profile('available'),profile('removed')]);await tick();assert.deepEqual(view.selected(),['model-removed']);
 click(view.clear());await tick();assert.equal(view.owner.providerId(),'');assert.deepEqual(view.selected(),[]);assert.equal(local.getItem('eeas-provider-id'),'');assert.deepEqual(local.writes,[['eeas-provider-id','']]);assert.deepEqual(view.callbacks,[['close',true]]);
 const reopened=mount(t,{local,read:async()=>[profile('available'),profile('removed')]});await reopened.owner.initialize();await tick();assert.equal(reopened.owner.providerId(),'');assert.deepEqual(reopened.selected(),[]);assert.deepEqual(reopened.callbacks,[]);
});

test('one actual option click persists only the chosen ID and closes the menu without any model request',async t=>{
 const view=mount(t,{local:storage('a'),read:async()=>[profile('a'),profile('b')]});await view.owner.initialize();await tick();assert.deepEqual(view.selected(),['model-a']);
 click(view.choice('b'));await tick();assert.equal(view.owner.providerId(),'b');assert.deepEqual(view.selected(),['model-b']);assert.deepEqual(view.local.writes,[['eeas-provider-id','b']]);assert.deepEqual(view.callbacks,[['close',true]]);assert.deepEqual(view.requests,['providers']);assert.equal(view.owner.providers().length,2);
});
