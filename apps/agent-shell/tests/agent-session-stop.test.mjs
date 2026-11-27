import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {ApiClient} from '../src/api.ts';
import * as apiModule from '../src/api.ts';
import * as sessionsModule from '../src/agent-sessions.ts';
import * as authored from '../src/authored-ui-copy.ts';
import * as nativeModule from '../src/native-collaboration.ts';
import {translateUI} from '../src/ui-language.ts';
import * as languageModule from '../src/ui-language.ts';
import * as nativeTools from '../src/native-tools.ts';

const app=resolve(dirname(fileURLToPath(import.meta.url)),'..'),req=createRequire(resolve(app,'package.json'));
const ts=req('typescript'),babel=req('@babel/core'),solidPreset=(await import(pathToFileURL(resolve(app,'node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js')).href)).default;
const solid=await import(pathToFileURL(resolve(app,'node_modules/solid-js/dist/solid.js')).href);
const source=readFileSync(resolve(app,'node_modules/solid-js/universal/dist/universal.js'),'utf8').replace("from 'solid-js'",`from '${pathToFileURL(resolve(app,'node_modules/solid-js/dist/solid.js')).href}'`);
const {createRenderer}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const ref={id:'own_session',revision:1,sha256:'a'.repeat(64)};
const permission={kind:'PERMISSION',request_ref:{id:'own_permission',generation:2,sha256:'b'.repeat(64)},native_request:{permission:'edit',patterns:['own-local-fixture.txt']},allowed_choices:['once','reject'],reply_is_execution_authorization:true};
const current=(patch={})=>sessionsModule.assertAgentSession({project_id:'own_project',session_ref:ref,generation:2,owner_status:'READY',connected_owner:true,configuration_current:false,observation_is_current:false,native_status:'busy',workspace_kind:'APP_INTERNAL_READONLY',active_operation_id:'own_running_operation',messages:[],todo:[],children:[],pending_questions:[],pending_permissions:[permission],last_event_seq:4,automatic_post_replay:false,...patch});
const receipt=(id,status='RETURNED')=>({id,status,request_sha256:'c'.repeat(64),automatic_replay:false,session_ref:ref});
const tick=()=>new Promise(done=>setImmediate(done));
async function until(check){for(let i=0;i<80;i++){if(check())return;await tick();}assert.ok(check(),'component settled in bounded turns');}

// Execute the actual AgentSessionPanel and actual AgentSessionsApi/ApiClient.
// Universal DOM is the renderer boundary; only HTTP transport and the unrelated
// DynamicWorkflow child are fixtures. No installed App API, model or owner runs.
function harness(t,options={}){
 const host={createElement:type=>({type,props:{},children:[],parent:null}),createTextNode:value=>({type:'#text',value,children:[],parent:null}),isTextNode:node=>node.type==='#text',replaceText:(node,value)=>{node.value=value;},insertNode(parent,node,anchor){if(node.parent){const children=node.parent.children;children.splice(children.indexOf(node),1);}node.parent=parent;const at=anchor?parent.children.indexOf(anchor):-1;parent.children.splice(at<0?parent.children.length:at,0,node);},removeNode(parent,node){const i=parent.children.indexOf(node);if(i>=0)parent.children.splice(i,1);node.parent=null;},setProperty:(node,key,value)=>{node.props[key]=value;},getParentNode:node=>node.parent,getFirstChild:node=>node.children[0],getNextSibling:node=>node.parent?.children[node.parent.children.indexOf(node)+1]};
 const renderer=createRenderer(host),calls=[],selected=[],uses=[];
 let responseSession=current(options.session),lastPost;
 const http=new ApiClient(async(url,init)=>{
  if(url==='/api/bootstrap')return Response.json({csrf_token:'OWN_UI_TRANSPORT_FIXTURE_NOT_AUTHORITY'});
  const call={url,method:init.method??'GET',body:init.body?JSON.parse(init.body):undefined};calls.push(call);
  if(call.method==='POST'){
   assert.match(url,/\/interrupt$/,'Only an explicit stop may reach the POST transport');lastPost=call;
   if(options.interrupt)return options.interrupt(call);
   return Response.json(receipt(call.body.operation_id));
  }
  if(url.endsWith('/agent-sessions'))return Response.json({sessions:[responseSession]});
  if(url.includes('/operations/'))return Response.json(receipt(lastPost.body.operation_id,'UNKNOWN_OUTCOME'));
  assert.equal(url,'/api/projects/own_project/agent-sessions/own_session');return options.get?options.get(call,responseSession):Response.json(responseSession);
 });
 const client=new sessionsModule.AgentSessionsApi(http),bindings={'solid-js':solid,'test-renderer':renderer,'./agent-sessions':{...sessionsModule,agentSessionsApi:client},'./authored-ui-copy':authored,'./ui-language':{...languageModule,t:(message,params)=>translateUI(message,'zh-CN',params)},'./native-collaboration':nativeModule,'./api':{...apiModule,api:http},'./native-tools':nativeTools,'./DynamicWorkflowPanel':{__esModule:true,default:()=>null}};
 const cache=new Map();
 function load(file){
  if(cache.has(file))return cache.get(file).exports;
  const module={exports:{}};cache.set(file,module);const raw=readFileSync(file,'utf8');
  const jsx=ts.transpileModule(raw,{fileName:file,compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;
  const universal=file.endsWith('.tsx')?babel.transformSync(jsx,{filename:file+'.jsx',presets:[[solidPreset,{generate:'universal',moduleName:'test-renderer'}]],configFile:false,babelrc:false}).code:jsx;
  const code=ts.transpileModule(universal,{fileName:file+'.js',compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText;
  new Function('require','module','exports',code)(name=>{
   if(bindings[name])return bindings[name];if(name.endsWith('.css'))return {};
   assert.ok(name.startsWith('.'),'explicit component dependency '+name);
   const base=resolve(dirname(file),name),candidate=[base,base+'.ts',base+'.tsx',base+'.json'].find(p=>{try{return readFileSync(p).length>=0;}catch{return false;}});
   assert.ok(candidate,'actual component dependency '+name);return candidate.endsWith('.json')?JSON.parse(readFileSync(candidate,'utf8')):load(candidate);
  },module,module.exports);return module.exports;
 }
 const file=options.sourceFile??resolve(app,'src/AgentSessionPanel.tsx'),component=load(file);
 const [value,setValue]=solid.createSignal(responseSession),[active,setActive]=solid.createSignal(true),root=host.createElement('root');
 const dispose=renderer.render(()=>component.default({get active(){return active();},projectId:'own_project',get selected(){return value();},onSelect:next=>{selected.push(next);setValue(next);},onUse:(...args)=>uses.push(args)}),root);t.after(dispose);
 const all=(node=root)=>[node,...node.children.flatMap(all)],text=(node=root)=>node.type==='#text'?String(node.value):node.children.map(text).join('');
 const button=label=>{const node=all().find(node=>node.type==='button'&&text(node)===label);assert.ok(node,'button '+label);return node;};
 return {calls,selected,uses,client,text,button,nodes:all,setActive,setCurrent:next=>{responseSession=next;setValue(next);},async settled(){await until(()=>!all().some(node=>node.type==='p'&&node.props.class==='as-capability-pending'));},click(label){const node=button(label);assert.equal(!!node.props.disabled,false,'enabled normal button '+label);node.props.onClick?.({currentTarget:node});},ready:()=>value()};
}

test('stop admits only the existing READY connected generation while new execution remains strict',()=>{
 for(const patch of [{},{configuration_current:false,observation_is_current:true},{configuration_current:true,observation_is_current:false}]){const value=current(patch);assert.equal(sessionsModule.originalSessionCanStop(value),true);assert.equal(sessionsModule.originalSessionReady(value),false);}
 assert.equal(sessionsModule.originalSessionCanStop(undefined),false);
 for(const patch of [{connected_owner:false},{generation:0},{owner_status:'DISCONNECTED'},{owner_status:'EXPIRED'},{owner_status:'OBSERVATION_UNAVAILABLE'}])assert.equal(sessionsModule.originalSessionCanStop(current(patch)),false);
 assert.equal(sessionsModule.originalSessionReady(current({configuration_current:true,observation_is_current:true})),true);
});

test('actual panel stops the exact stale-config owner once, with no control preflight, while continue and permission stay disabled',async t=>{
 let release;const wait=new Promise(done=>release=done),view=harness(t,{interrupt:async call=>{await wait;return Response.json(receipt(call.body.operation_id));}});await view.settled();
 assert.equal(view.button('中断原执行').props.disabled,false);assert.equal(view.button('在当前输入框继续').props.disabled,true);assert.equal(view.nodes().some(node=>node.type==='button'&&view.text(node)==='仅允许这一次'),false,'approval belongs to the exact pending controls in chat');assert.equal(view.button('返回对话').props.disabled,undefined);
 view.calls.length=0;view.click('中断原执行');await until(()=>view.calls.some(call=>call.method==='POST'));
 assert.equal(view.button('中断原执行').props.disabled,true,'pending control blocks another normal click');
 const post=view.calls[0];assert.equal(post.method,'POST');assert.equal(post.url,'/api/projects/own_project/agent-sessions/own_session/interrupt');assert.deepEqual(post.body.session_ref,ref);assert.equal(post.body.generation,2);assert.equal(typeof post.body.operation_id,'string');assert.equal(Object.keys(post.body).length,3);
 release();await view.settled();assert.equal(view.calls.filter(call=>call.method==='POST').length,1);assert.equal(view.calls.length,2);assert.equal(view.calls[1].method,'GET');assert.equal(view.uses.length,0);assert.equal(view.selected.length,1);assert.equal(view.button('在当前输入框继续').props.disabled,true);assert.equal(view.nodes().some(node=>node.type==='button'&&view.text(node)==='仅允许这一次'),false,'approval belongs to the exact pending controls in chat');assert.equal(view.button('返回对话').props.disabled,undefined);
});

test('unknown stop inspects only its original operation and never reposts on refresh or reactivation',async t=>{
 const view=harness(t,{interrupt:()=>{throw Error('connection ended after possible stop');}});await view.settled();view.calls.length=0;view.click('中断原执行');await view.settled();
 const post=view.calls.find(call=>call.method==='POST');assert.ok(post);assert.deepEqual(post.body.session_ref,ref);assert.equal(post.body.generation,2);
 assert.equal(view.calls.filter(call=>call.method==='POST').length,1);assert.equal(view.calls.at(-1).url,'/api/projects/own_project/agent-sessions/own_session/operations/'+post.body.operation_id);assert.match(view.text(),/操作结果待核对/);assert.match(view.text(),/不自动重发/);
 view.setActive(false);await tick();view.setActive(true);await view.settled();view.click('核对状态');await view.settled();await tick();assert.equal(view.calls.filter(call=>call.method==='POST').length,1);assert.equal(view.uses.length,0);
});

test('actual panel disables stop after disconnect or invalid generation without widening continuation or permission',async t=>{
 const view=harness(t);await view.settled();
 for(const patch of [{owner_status:'DISCONNECTED',connected_owner:false},{generation:0},{owner_status:'EXPIRED'}]){view.setCurrent(current(patch));await tick();assert.equal(view.button('中断原执行').props.disabled,true);assert.equal(view.button('在当前输入框继续').props.disabled,true);assert.equal(view.nodes().some(node=>node.type==='button'&&view.text(node)==='仅允许这一次'),false,'approval belongs to the exact pending controls in chat');assert.equal(view.button('返回对话').props.disabled,undefined);}
 assert.equal(view.calls.filter(call=>call.method==='POST').length,0);
 view.setCurrent(current({configuration_current:true,observation_is_current:true}));await tick();assert.equal(view.button('中断原执行').props.disabled,false);assert.equal(view.button('在当前输入框继续').props.disabled,false);assert.equal(view.nodes().some(node=>node.type==='button'&&view.text(node)==='仅允许这一次'),false,'readiness does not duplicate chat approval in management');
});

if(process.env.EEAS_STOP_PANEL_BASELINE)test('saved real before-panel reproduces the stale-config stop blocker',async t=>{const view=harness(t,{sourceFile:process.env.EEAS_STOP_PANEL_BASELINE});await view.settled();assert.equal(view.button('中断原执行').props.disabled,true);assert.equal(view.calls.filter(call=>call.method==='POST').length,0);});


test('management selects an exact ready owner for inspection, and only explicit continue changes the discussion',async t=>{
 const view=harness(t,{compact:true,session:{configuration_current:true,observation_is_current:true}});await view.settled();view.calls.length=0;
 view.click('own_session会话已就绪');await view.settled();assert.equal(view.uses.length,0);assert.equal(view.selected.length,1);view.click('在当前输入框继续');await view.settled();assert.equal(view.uses.length,1);assert.deepEqual(view.uses[0][0].session_ref,ref);assert.equal(view.uses[0][1],'plan');
 assert.deepEqual(view.calls.map(c=>[c.method,c.url]),[['GET','/api/projects/own_project/agent-sessions/own_session']]);
});
test('closing management before its GET returns keeps the current discussion and does not reconnect or continue',async t=>{
 let release;const gate=new Promise(done=>release=done),view=harness(t,{compact:true,session:{configuration_current:true,observation_is_current:true},get:()=>gate});await view.settled();view.calls.length=0;
 view.click('own_session会话已就绪');await tick();view.setActive(false);await tick();release(Response.json(current({configuration_current:true,observation_is_current:true})));await view.settled();
 assert.equal(view.uses.length,0);assert.equal(view.selected.length,0);assert.equal(view.calls.filter(c=>c.method==='POST').length,0);
});
test('failed management selection preserves its current owner and exposes a readable error without reconnecting',async t=>{
 const view=harness(t,{compact:true,session:{configuration_current:true,observation_is_current:true},get:()=>Response.json({detail:'session temporarily unavailable'},{status:503})});await view.settled();view.calls.length=0;const basis=view.ready();
 view.click('own_session会话已就绪');await view.settled();assert.equal(view.ready(),basis);assert.equal(view.uses.length,0);assert.equal(view.selected.length,0);assert.equal(view.calls.filter(c=>c.method==='POST').length,0);assert.match(view.text(),/session temporarily unavailable/);
});
test('an unavailable session stays in management controls and cannot silently reconnect or continue',async t=>{
 const view=harness(t,{compact:true,session:{configuration_current:false,observation_is_current:false}});await view.settled();view.calls.length=0;
 view.click('own_session需要核对状态');await view.settled();assert.equal(view.selected.length,1);assert.equal(view.uses.length,0);assert.equal(view.calls.filter(c=>c.method==='POST').length,0);assert.equal(view.button('在当前输入框继续').props.disabled,true);
});
test('owner management renders task text and returns pending permissions to chat without raw payloads or duplicate approval',async t=>{
 const view=harness(t,{compact:true,session:{configuration_current:true,observation_is_current:true,todo:[{content:'检查所选文件排版',status:'pending',internal:'INTERNAL_TODO_ONLY'}],pending_permissions:[{...permission,native_request:{permission:'edit',internal:'INTERNAL_REQUEST_ONLY'}}],qualification:{internal:'INTERNAL_QUALIFICATION_ONLY'}}});await view.settled();
 assert.match(view.text(),/检查所选文件排版/);assert.doesNotMatch(view.text(),/INTERNAL_|APP_INTERNAL_READONLY/);assert.equal(view.nodes().filter(n=>n.type==='pre').length,0);assert.equal(view.nodes().some(node=>node.type==='button'&&view.text(node)==='仅允许这一次'),false,'approval belongs to the exact pending controls in chat');assert.equal(view.button('返回对话').props.disabled,undefined);assert.equal(view.nodes().some(node=>node.type==='button'&&view.text(node)==='拒绝此请求'),false,'management must return to the same chat controls');
});
