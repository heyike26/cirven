/** UNIT: actual compiled Solid components and callbacks, in-memory DOM host.
 * No OAuth, model calls or Windows qualification. Real API save/recovery remains
 * covered by provider-connection-form.test.mjs. CSS geometry is checked separately.
 */
import assert from 'node:assert/strict';
import test from 'node:test';
import {readFile} from 'node:fs/promises';
import {resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';
import * as solid from '../node_modules/solid-js/dist/solid.js';
import * as language from './fixture-ui-language.mjs';
import * as authored from '../src/authored-ui-copy.ts';
import * as connection from '../src/provider-connection.ts';
import * as subscription from '../src/subscription-account.ts';
import * as catalog from '../src/model-catalog.ts';
import brand from '../src/brand.ts';
const app=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const universal=(await readFile(resolve(app,'node_modules/solid-js/universal/dist/universal.js'),'utf8')).replace("from 'solid-js'",`from '${pathToFileURL(resolve(app,'node_modules/solid-js/dist/solid.js')).href}'`);
const {createRenderer}=await import('data:text/javascript;base64,'+Buffer.from(universal).toString('base64'));
const storage=()=>{const m=new Map();return{getItem:k=>m.get(k)??null,setItem:(k,v)=>m.set(k,v)};};
function harness(t,component,props){
 const host={createElement:type=>({type,props:{},children:[],parent:null}),createTextNode:value=>({type:'#text',value,children:[],parent:null}),isTextNode:n=>n.type==='#text',replaceText:(n,v)=>n.value=v,
 insertNode(p,n,a){if(n.parent){const old=n.parent.children;old.splice(old.indexOf(n),1);}n.parent=p;const i=a?p.children.indexOf(a):-1;p.children.splice(i<0?p.children.length:i,0,n);},removeNode(p,n){const i=p.children.indexOf(n);if(i>=0)p.children.splice(i,1);n.parent=null;},setProperty:(n,k,v)=>n.props[k]=v,getParentNode:n=>n.parent,getFirstChild:n=>n.children[0],getNextSibling:n=>n.parent?.children[n.parent.children.indexOf(n)+1]};
 const renderer=createRenderer(host),cache=new Map();
 function compile(name){if(cache.has(name))return cache.get(name);const source=sources.get(name),js=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;
 let code=babel.transformSync(js,{filename:name+'.jsx',presets:[[solidPreset,{generate:'universal',moduleName:'fixture-renderer'}]],configFile:false,babelrc:false}).code;
 const parsed=ts.createSourceFile(name+'.mjs',code,ts.ScriptTarget.Latest,true,ts.ScriptKind.JS),bindings={};
 for(const node of [...parsed.statements].reverse())if(ts.isImportDeclaration(node)){
  const spec=node.moduleSpecifier.text;
  const dep=spec==='solid-js'?solid:spec==='fixture-renderer'?renderer:spec==='./ui-language'?language:spec==='./authored-ui-copy'?authored:spec==='./provider-connection'?connection:spec==='./subscription-account'?subscription:spec==='./model-catalog'?catalog:spec==='./brand'?{default:brand}:spec.endsWith('icon.svg')?{default:'fixture-original-brand.svg'}:spec.endsWith('.css')?{}:compile(spec.slice(2));
  if(node.importClause?.name)bindings[node.importClause.name.text]=dep.default;
  if(node.importClause?.namedBindings&&ts.isNamedImports(node.importClause.namedBindings))for(const b of node.importClause.namedBindings.elements)bindings[b.name.text]=dep[b.propertyName?.text??b.name.text];
  code=code.slice(0,node.getStart(parsed))+code.slice(node.end);
 }
 const defaults={BrandLogo:undefined,ChatGPTSubscriptionPanel:'ChatGPTSubscriptionPanel',ProviderConnectionForm:'ProviderConnectionForm',ProviderRenderer:'ProviderRenderer'};
 code=code.replace(/export default function /g,'function ').replace(/export function /g,'function ');
 const exports=name==='BrandLogo'?'Mark,Splash,Logo,BrandLoader':name==='ProviderConnectionForm'?'ProviderConnectionReceipt':'';
 const result=new Function(...Object.keys(bindings),code+`\nreturn {${defaults[name]?'default:'+defaults[name]+(exports?',':''):''}${exports}};`)(...Object.values(bindings));cache.set(name,result);return result;
 }
 const C=compile(component).default,container=host.createElement('root');t.after(renderer.render(()=>C(props),container));
 const nodes=()=>{const out=[];const walk=n=>{out.push(n);for(const c of n.children)walk(c);};walk(container);return out;};
 return {nodes,find:(type,label)=>nodes().find(n=>n.type===type&&(!label||n.props['aria-label']===label)),text:()=>nodes().filter(n=>n.type==='#text').map(n=>n.value).join(' ')};
}
const sources=new Map(await Promise.all(['BrandLogo','ChatGPTSubscriptionPanel','ProviderConnectionForm','ProviderRenderer'].map(async name=>[name,await readFile(resolve(app,'src/'+name+'.tsx'),'utf8')])));
const profile=(extra={})=>({profile_ref:{id:'saved-connection',revision:8,sha256:'a'.repeat(64)},model_id:'exact-model-a',provider_url:'https://chatgpt.com/backend-api/codex',auth_kind:'OAUTH_SUBSCRIPTION',credential_present:false,supports_images:true,model_access:'NOT_VALIDATED',...extra});
const account=p=>({...subscription.signedOutSubscription(),status:'AUTHENTICATED',profile:p,model_access:'VERIFIED',models:[{id:'exact-model-a',name:'A'},{id:'exact-model-b',name:'B'}]});
const flush=()=>new Promise(r=>setImmediate(r));
const panelClient=(state,create)=>({refresh:async()=>state,restoreProfiles:async()=>[],canCancel:()=>false,createProfile:create});

test('exact saved subscription profile uses its original revision without another save POST',async t=>{
 const p=profile(),seen=[],a=account(p);let saves=0;
 const view=harness(t,'ChatGPTSubscriptionPanel',{initialAccount:a,client:panelClient(a,async()=>{saves++;throw Error('must not create another revision');}),onProfile:async x=>seen.push(x)});
 await flush();view.find('form').props.onSubmit({preventDefault(){}});await flush();
 assert.equal(saves,0);assert.deepEqual(seen,[p]);assert.match(view.text(),/此模型已有成功响应/);assert.equal(view.find('select','选择模型').props.value,'exact-model-a');
});
test('changed subscription model follows the original save once and cannot inherit verification',async t=>{
 const p=profile(),a=account(p),seen=[],requests=[];
 const view=harness(t,'ChatGPTSubscriptionPanel',{initialAccount:a,client:panelClient(a,async(id,model)=>{requests.push({id,model});return profile({model_id:model,profile_ref:{id,revision:9,sha256:'b'.repeat(64)}});}),onProfile:x=>seen.push(x)});
 await flush();view.find('select','选择模型').props.onChange({currentTarget:{value:'exact-model-b'}});assert.doesNotMatch(view.text(),/此模型已有成功响应/);
 view.find('form').props.onSubmit({preventDefault(){}});await flush();assert.deepEqual(requests,[{id:'saved-connection',model:'exact-model-b'}]);assert.equal(seen[0].profile_ref.revision,9);assert.doesNotMatch(view.text(),/此模型已有成功响应/);
});
test('same model under a different explicit connection id saves instead of borrowing the old ref',async t=>{
 const p=profile(),a=account(p),requests=[];
 const view=harness(t,'ChatGPTSubscriptionPanel',{initialAccount:a,client:panelClient(a,async(id,model)=>{requests.push({id,model});return profile({profile_ref:{id,revision:1,sha256:'c'.repeat(64)}});})});
 await flush();view.find('input','订阅连接标识').props.onInput({currentTarget:{value:'different-connection'}});assert.doesNotMatch(view.text(),/此模型已有成功响应/);
 view.find('form').props.onSubmit({preventDefault(){}});await flush();assert.deepEqual(requests,[{id:'different-connection',model:'exact-model-a'}]);
});
test('selected API basis and unsaved address survive a new catalog revision; new selection is explicit',async t=>{
 const old=profile({auth_kind:'NONE',supports_images:false,provider_url:'http://127.0.0.1:1/v1'}),next={...old,profile_ref:{...old.profile_ref,revision:9,sha256:'b'.repeat(64)}};
 const [artifact,setArtifact]=solid.createSignal({artifactId:'providers-api-test',type:'providers',data:[old]});
 const view=harness(t,'ProviderRenderer',{get artifact(){return artifact();},context:{dispatch:async()=>{throw Error('no save on selection');}}});await flush();
 const key=p=>`${p.profile_ref.id}:${p.profile_ref.revision}:${p.profile_ref.sha256}`;
 view.find('select','选择已有连接').props.onChange({currentTarget:{value:key(old)}});view.find('input','API 地址').props.onInput({currentTarget:{value:'http://127.0.0.1:1/v1/UNSAVED'}});
 setArtifact({...artifact(),data:[next]});assert.equal(view.find('select','选择已有连接').props.value,key(old));assert.equal(view.find('input','API 地址').props.value,'http://127.0.0.1:1/v1/UNSAVED');assert.ok(view.nodes().some(n=>n.type==='option'&&n.props.value===key(old)&&n.props.selected));
 view.find('select','选择已有连接').props.onChange({currentTarget:{value:key(next)}});assert.equal(view.find('input','API 地址').props.value,next.provider_url);
 view.find('select','选择已有连接').props.onChange({currentTarget:{value:''}});assert.equal(view.find('input','连接标识').props.value,'');
});
