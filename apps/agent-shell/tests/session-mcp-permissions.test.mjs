import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import * as apiModule from '../src/api.ts';
import * as nativeModule from '../src/native-collaboration.ts';
import * as languageModule from '../src/ui-language.ts';
import {ApiClient} from '../src/api.ts';
import * as sessions from '../src/agent-sessions.ts';
import * as authored from '../src/authored-ui-copy.ts';
import * as tools from '../src/native-tools.ts';
import {sameNativeReference} from '../src/native-collaboration.ts';
import {translateUI} from '../src/ui-language.ts';

const app=resolve(dirname(fileURLToPath(import.meta.url)),'..'),req=createRequire(resolve(app,'package.json'));
const ts=req('typescript'),babel=req('@babel/core'),solidPreset=(await import(pathToFileURL(resolve(app,'node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js')).href)).default;
const solid=await import(pathToFileURL(resolve(app,'node_modules/solid-js/dist/solid.js')).href);
const universal=readFileSync(resolve(app,'node_modules/solid-js/universal/dist/universal.js'),'utf8').replace("from 'solid-js'",`from '${pathToFileURL(resolve(app,'node_modules/solid-js/dist/solid.js')).href}'`);
const {createRenderer}=await import('data:text/javascript;base64,'+Buffer.from(universal).toString('base64'));
const localRef={id:'chosen-local',revision:2,sha256:'a'.repeat(64)},remoteRef={id:'chosen-remote',revision:3,sha256:'b'.repeat(64)},binding='c'.repeat(64);
const item=ref=>({server_id:ref.id,config_ref:{...ref},probe_ref:{id:'d'.repeat(32),sha256:'d'.repeat(64)},tool_id:ref.id+'_echo',name:'echo',input_schema_sha256:'e'.repeat(64)});
const selection=(...refs)=>({capability_binding_sha256:binding,items:refs.map(item)});
const config=(ref,type)=>({id:ref.id,config_ref:{...ref},type,enabled:true,integrity:'CURRENT'});
const configs=[config(localRef,'local'),config(remoteRef,'remote')];
const chosen=(...refs)=>tools.withMCPConnectionHints(selection(...refs),configs);
const tick=()=>new Promise(done=>setImmediate(done));
async function until(check){for(let i=0;i<80;i++){if(check())return;await tick();}assert.ok(check(),'bounded component completion');}
function actualSession(pid){return {project_id:pid,session_ref:{id:'own_session',revision:1,sha256:'f'.repeat(64)},owner_status:'READY',generation:1,connected_owner:true,configuration_current:true,observation_is_current:true,native_status:'idle',workspace_kind:'APP_INTERNAL_READONLY',messages:[],todo:[],children:[],pending_questions:[],pending_permissions:[],last_event_seq:0,automatic_post_replay:false};}
function harness(t,initial,options={}){
 const host={createElement:type=>({type,props:{},children:[],parent:null}),createTextNode:value=>({type:'#text',value,children:[],parent:null}),isTextNode:n=>n.type==='#text',replaceText:(n,v)=>{n.value=v;},insertNode(parent,node,anchor){if(node.parent){const a=node.parent.children;a.splice(a.indexOf(node),1);}node.parent=parent;const at=anchor?parent.children.indexOf(anchor):-1;parent.children.splice(at<0?parent.children.length:at,0,node);},removeNode(parent,node){const i=parent.children.indexOf(node);if(i>=0)parent.children.splice(i,1);node.parent=null;},setProperty:(node,key,value)=>{node.props[key]=value;},getParentNode:n=>n.parent,getFirstChild:n=>n.children[0],getNextSibling:n=>n.parent?.children[n.parent.children.indexOf(n)+1]};
 const renderer=createRenderer(host),calls=[],uses=[],selected=[],consumed=[];
 const http=new ApiClient(async(url,init)=>{if(url==='/api/bootstrap')return Response.json({csrf_token:'ORIGINAL_LOCAL_TRANSPORT_FIXTURE'});const call={url,method:init.method??'GET',body:init.body?JSON.parse(init.body):undefined};calls.push(call);if(url==='/api/agent-sessions/configuration')return Response.json({capability_binding_sha256:binding,configuration_refs:{skills:{},mcp:{[localRef.id]:localRef,[remoteRef.id]:remoteRef}},permission_granted:false,loaded_in_original_session:false});if(url==='/api/agent-sessions/open'){if(options.open)return options.open(call);return Response.json(actualSession(call.body.project_id));}assert.ok(url.endsWith('/agent-sessions'));return Response.json({sessions:[]});});
 const client=new sessions.AgentSessionsApi(http),bindings={'solid-js':solid,'test-renderer':renderer,'./agent-sessions':{...sessions,agentSessionsApi:client},'./authored-ui-copy':authored,'./api':{...apiModule,api:http},'./ui-language':{...languageModule,t:(key,params)=>translateUI(key,options.locale??'zh-CN',params)},'./native-collaboration':nativeModule,'./native-tools':tools,'./DynamicWorkflowPanel':{__esModule:true,default:()=>null}};
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
 const file=resolve(app,'src/AgentSessionPanel.tsx'),component=load(file);
 const [choice,setChoice]=solid.createSignal(initial),[project,setProject]=solid.createSignal('project-a'),root=host.createElement('root');
 const dispose=renderer.render(()=>component.default({active:true,get projectId(){return project();},provider:{id:'configured-model',revision:1,sha256:'9'.repeat(64)},get toolSelection(){return choice();},onToolSelectionUsed:()=>consumed.push(true),onSelect:value=>selected.push(value),onUse:(...args)=>uses.push(args)}),root);t.after(dispose);
 const all=(node=root)=>[node,...node.children.flatMap(all)],text=(node=root)=>node.type==='#text'?String(node.value):node.children.map(text).join('');
 const label=kind=>translateUI(kind==='local'?'允许启动所选的本地 MCP 进程':'允许连接所选的远端 MCP',options.locale??'zh-CN');
 const checkbox=kind=>{const parent=all().find(node=>node.type==='label'&&text(node)===label(kind));return parent?.children.find(node=>node.type==='input');};
 const button=title=>{const node=all().find(node=>node.type==='button'&&text(node)===translateUI(title,options.locale??'zh-CN'));assert.ok(node,'button '+title);return node;};
 const field=()=>all().find(node=>node.type==='textarea'&&node.props['aria-label']===translateUI('原会话 MCP 工具名单',options.locale??'zh-CN'));
 return {calls,uses,selected,consumed,checkbox,button,field,setChoice,setProject,text,check(kind,value=true){const node=checkbox(kind);assert.ok(node,kind+' visible');assert.equal(!!node.props.disabled,false);node.props.onChange({currentTarget:{checked:value}});},write(value){const node=field();node.props.onInput({currentTarget:{value}});},click(){const node=button('创建并连接');assert.equal(!!node.props.disabled,false);node.props.onClick();},async settled(){await until(()=>!all().some(node=>node.type==='p'&&node.props.class==='as-capability-pending'));},posts:()=>calls.filter(call=>call.method==='POST')};
}

test('connection hints require exact selected configuration refs and never infer types from names',()=>{
 const selected=chosen(localRef);assert.deepEqual(selected.connection_hints,[{server_id:localRef.id,config_ref:localRef,type:'local'}]);
 assert.deepEqual(tools.mcpPermissionVisibility(selected,[localRef.id+'_echo']),{local:true,remote:false});
 for(const metadata of [[],[{...configs[0],config_ref:{...localRef,revision:1}}],[{...configs[0],enabled:false}],[{...configs[0],integrity:'CHANGED'}],[configs[0],configs[0]]]){const stale=tools.withMCPConnectionHints(selected,metadata);assert.equal(stale.connection_hints,undefined);assert.deepEqual(tools.mcpPermissionVisibility(stale,[localRef.id+'_echo']),{local:true,remote:true});}
 assert.deepEqual(tools.mcpPermissionVisibility({...selected,connection_hints:[{...selected.connection_hints[0],config_ref:remoteRef}]},[localRef.id+'_echo']),{local:true,remote:true});
 assert.deepEqual(tools.mcpPermissionVisibility(selected,[]),{local:false,remote:false});
 assert.deepEqual(tools.mcpPermissionVisibility(undefined,['legacy-exact-tool']),{local:true,remote:true});
 assert.deepEqual(tools.mcpPermissionVisibility(chosen(localRef,remoteRef),[localRef.id+'_echo',remoteRef.id+'_echo']),{local:true,remote:true});
 configs[0].config_ref.revision=7;assert.equal(selected.connection_hints[0].config_ref.revision,2);configs[0].config_ref.revision=2;
});

test('the actual capability choose callback attaches only exact already-loaded metadata without another read',()=>{
 const file=resolve(app,'src/NativeCapabilitiesPanel.tsx'),tree=ts.createSourceFile(file,readFileSync(file,'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX),component=tree.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='NativeCapabilitiesPanel'),choose=component.body.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='choose');
 const s=selection(localRef),catalog={schema:'eeas.native-tools.v1',capability_binding_sha256:binding,configuration_refs:{skills:{},mcp:{[localRef.id]:localRef}},owner:{scope:null,status:'NOT_SELECTED'},builtin_tools:[],mcp_servers:[{id:localRef.id,config_ref:localRef,enabled:true,availability:'AVAILABLE',probe_ref:s.items[0].probe_ref,tools:[{id:s.items[0].tool_id,name:'echo',input_schema_sha256:s.items[0].input_schema_sha256,selectable:true}],source:'PRIVATE_MCP_PROBE_NOT_ORIGINAL_OWNER'}],read_only:true,model_calls:0,effects_applied:false};
 const values=[],deps={catalog:()=>catalog,toolsCurrent:()=>true,selectedStale:()=>false,selected:()=>[s.items[0].tool_id],state:()=>({mcp:configs}),chooseMCPTools:tools.chooseMCPTools,withMCPConnectionHints:tools.withMCPConnectionHints,props:{onChooseTools:value=>values.push(value)},setNotice(){},setError(){assert.fail('unexpected choice error');},authoredErrorCopy:authored.authoredErrorCopy};
 const js=ts.transpileModule('let skipReturnFocus=false;'+choose.getText(tree)+';return choose;',{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
 new Function(...Object.keys(deps),js)(...Object.values(deps))();assert.equal(values.length,1);assert.deepEqual(values[0].connection_hints,[{server_id:localRef.id,config_ref:localRef,type:'local'}]);
});

test('empty selection renders no MCP grants and actual creation carries no effects despite other globally configured servers',async t=>{
 const view=harness(t,chosen());await view.settled();assert.equal(view.checkbox('local'),undefined);assert.equal(view.checkbox('remote'),undefined);view.click();await view.settled();assert.equal(view.posts().length,1);assert.deepEqual(view.posts()[0].body.allowed_effects,[]);assert.deepEqual(view.posts()[0].body.mcp_tool_selection,[]);
});

test('local-only and remote-only actual panels require their explicit checkbox and never post UI hints',async t=>{
 for(const [ref,kind,effect,other] of [[localRef,'local','PROCESS_START','remote'],[remoteRef,'remote','REMOTE_MCP_CONNECT','local']]){const view=harness(t,chosen(ref),{locale:kind==='remote'?'en':'zh-CN'});await view.settled();assert.equal(view.checkbox(kind).props.checked,false);assert.equal(view.checkbox(other),undefined);view.check(kind);view.click();await view.settled();const [post]=view.posts();assert.deepEqual(post.body.allowed_effects,[effect]);assert.deepEqual(post.body.mcp_tool_allowlist,[ref.id+'_echo']);assert.deepEqual(post.body.mcp_tool_selection,selection(ref).items);assert.equal(Object.hasOwn(post.body,'connection_hints'),false);assert.equal(Object.hasOwn(post.body.mcp_tool_selection[0],'type'),false);assert.equal(view.uses.length,1);}
});

test('changing selected scope or manually editing the list clears old consent while preserving the same field',async t=>{
 const view=harness(t,chosen(localRef));await view.settled();const field=view.field();view.check('local');view.setChoice(chosen(remoteRef));await tick();assert.equal(view.checkbox('local'),undefined);assert.equal(view.checkbox('remote').props.checked,false);assert.equal(view.field(),field);view.check('remote');view.write('legacy_exact_tool');await tick();assert.equal(view.field(),field);assert.equal(field.props.value,'legacy_exact_tool');assert.equal(view.checkbox('local').props.checked,false);assert.equal(view.checkbox('remote').props.checked,false);view.check('local');view.write('');await tick();assert.equal(view.checkbox('local'),undefined);assert.equal(view.checkbox('remote'),undefined);view.click();await view.settled();assert.deepEqual(view.posts()[0].body.allowed_effects,[]);assert.deepEqual(view.posts()[0].body.mcp_tool_allowlist,[]);assert.equal(view.posts()[0].body.mcp_tool_selection,null);
});

test('changing project clears an existing grant and an older pending open never consumes a newer selection',async t=>{
 let release;const gate=new Promise(done=>release=done),view=harness(t,chosen(localRef),{open:async call=>{await gate;return Response.json(actualSession(call.body.project_id));}});await view.settled();view.check('local');view.setProject('project-b');await view.settled();assert.equal(view.checkbox('local').props.checked,false);view.check('local');view.click();await until(()=>view.posts().length===1);const first=view.posts()[0];assert.equal(first.body.project_id,'project-b');assert.deepEqual(first.body.allowed_effects,['PROCESS_START']);const field=view.field();view.setChoice(chosen(remoteRef));await tick();release();await view.settled();assert.equal(view.posts().length,1);assert.equal(view.uses.length,0);assert.equal(view.selected.length,0);assert.equal(view.consumed.length,0);assert.equal(view.field(),field);assert.equal(view.checkbox('remote').props.checked,false);
});
