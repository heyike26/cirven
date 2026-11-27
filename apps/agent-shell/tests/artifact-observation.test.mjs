import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
import {resolve,dirname,extname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import ts from 'typescript';
import babel from '@babel/core';
import * as apiModule from '../src/api.ts';
import {Marked} from 'marked';
import DOMPurify from 'dompurify';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';
import * as solid from '../node_modules/solid-js/dist/solid.js';
import {observeArtifactResult,confirmedArtifactResult,panelKey,InteractionState} from '../src/interaction-state.ts';

// Client integration UNIT: real cached hosts, Dynamic, DiscoveryRenderer,
// ObjectDraftSession and action-result projection. Only API/draft transport,
// native primitives and canvas/Office presentation are boundaries. This does
// not claim browser focus/layout, real API persistence or Windows qualification.
const app=resolve(dirname(fileURLToPath(import.meta.url)),'..'),hash='a'.repeat(64),tick=()=>new Promise(done=>setImmediate(done));
const client=pathToFileURL(resolve(app,'node_modules/solid-js/dist/solid.js')).href;
const universal=readFileSync(resolve(app,'node_modules/solid-js/universal/dist/universal.js'),'utf8').replaceAll("from 'solid-js'",`from '${client}'`);
const {createRenderer}=await import('data:text/javascript;base64,'+Buffer.from(universal).toString('base64'));
const web=readFileSync(resolve(app,'node_modules/solid-js/web/dist/web.js'),'utf8').replaceAll("from 'solid-js'",`from '${client}'`);
const {Dynamic}=await import('data:text/javascript;base64,'+Buffer.from(web).toString('base64'));
function session(){return {id:'discovery',project_id:'project',revision:2,current_revision:2,sha256:hash,source_current:true,answers:[],
 payload:{entry_mode:'REQUIREMENTS',workspace_ref:{id:'workspace',revision:1,binding_sha256:hash},materials:[],source_receipt:{documents:[]},history:[],status:'DRAFT',accepted_claim_ids:['r'],accepted_graph_ids:[],resolutions:[],candidate:{title:'原任务',phase:'SPECIFY',summary:'不可漂移的原理解',claims:[{id:'r',category:'REQUIREMENT',origin:'USER_STATEMENT',text:'原要求',acceptance:['可验证条件'],source_anchors:[]}],questions:[{id:'q',text:'原问题',reason:'原理由',blocking:true,affects_claim_ids:['r'],requested_materials:[],source_anchors:[]}],competitors:[],next_actions:[],graphs:[{id:'g',kind:'FLOW',title:'原图',nodes:[],edges:[]}] }},
 readiness:{requirements_confirmed:false,can_plan:false,pending_count:1,blocking_count:1,semantic_verification:'NOT_PERFORMED',review_status:'PENDING',execution_authorized:false}};}
function envelope(data=session()){return {artifactId:data.id,type:'discovery',schemaVersion:1,revision:data.revision,contentHash:data.sha256,scope:{projectId:data.project_id,workspaceId:'workspace',sessionId:data.id},availability:'IMPLEMENTED',data};}
const answer=(id='answer',text='已保存回答')=>({id,question_id:'q',session_revision:2,text,accepted:false});
function confirmed(old){return {...old,revision:3,current_revision:3,sha256:'b'.repeat(64),answers:old.answers.map(item=>({...item,accepted:true})),payload:{...old.payload,revision:3,status:'CONFIRMED',resolutions:[{question_id:'q',mode:'ANSWER_ACCEPTED',answer_id:'answer',reason:'人工接受'}],confirmation:{basis_revision:2,basis_sha256:hash,execution_authorized:false}},readiness:{...old.readiness,requirements_confirmed:true,can_plan:true,pending_count:0,blocking_count:0}};}

test('same-version observations preserve the immutable payload and do not let late reads remove known answers',()=>{
 const original=envelope(),payload=original.data.payload,next=observeArtifactResult(original,{...original.data,answers:[answer()]});
 assert.equal(next.data.payload,payload);assert.equal(next.revision,2);assert.equal(next.contentHash,hash);assert.deepEqual(next.data.answers,[answer()]);
 assert.equal(observeArtifactResult(next,{...original.data,answers:[]}),undefined);assert.equal(original.data.answers.length,0);
 for(const change of [{project_id:'other'},{id:'other'},{revision:3},{sha256:'c'.repeat(64)},{payload:{...payload,status:'CONFIRMED'}},{readiness:{...original.data.readiness,execution_authorized:true}}])assert.equal(observeArtifactResult(original,{...original.data,answers:[answer()],...change}),undefined);
});
test('a new confirmation needs its exact original basis and stays a separate version',()=>{
 const original=envelope(),next=confirmed(original.data),value=confirmedArtifactResult(original,next);
 assert.equal(value.data,next);assert.equal(value.revision,3);assert.notEqual(panelKey(original),panelKey(value));assert.equal(original.data.payload.status,'DRAFT');
 for(const candidate of [{...next,project_id:'other'},{...next,payload:{...next.payload,confirmation:{...next.payload.confirmation,basis_sha256:'c'.repeat(64)}}},{...next,payload:{...next.payload,candidate:{...next.payload.candidate,summary:'换了原要求'}}}])assert.equal(confirmedArtifactResult(original,candidate),undefined);
});
test('confirmed same-version observations expose changed sources without changing the frozen requirements or permitting planning',()=>{
 const data=confirmed({...session(),answers:[answer()]}),original=envelope(data),payload=data.payload;
 const stale={...data,source_current:false,readiness:{...data.readiness,requirements_confirmed:false,can_plan:false,review_status:'NEEDS_REVIEW'}};
 const next=observeArtifactResult(original,stale);
 assert.ok(next);assert.equal(next.data.payload,payload);assert.equal(next.data.payload.status,'CONFIRMED');assert.equal(next.data.source_current,false);assert.equal(next.data.readiness.can_plan,false);assert.deepEqual(next.data.answers,data.answers);assert.equal(panelKey(next),panelKey(original));
 assert.equal(observeArtifactResult(original,{...stale,readiness:{...stale.readiness,can_plan:true}}),undefined);
 assert.equal(observeArtifactResult(original,{...stale,readiness:{...stale.readiness,requirements_confirmed:true}}),undefined);
 const current=observeArtifactResult(next,data);assert.ok(current);assert.equal(current.data.source_current,true);assert.equal(current.data.readiness.can_plan,true);assert.equal(current.data.payload,payload);
});
test('a committed confirmation remains a separate confirmed version when its sources need review by receipt time',()=>{
 const original=envelope(),committed=confirmed(original.data),stale={...committed,source_current:false,readiness:{...committed.readiness,requirements_confirmed:false,can_plan:false,review_status:'NEEDS_REVIEW'}};
 const next=confirmedArtifactResult(original,stale);
 assert.ok(next);assert.equal(next.data,stale);assert.equal(next.data.payload.status,'CONFIRMED');assert.equal(next.data.source_current,false);assert.equal(next.data.readiness.can_plan,false);assert.equal(next.revision,3);assert.notEqual(panelKey(next),panelKey(original));assert.equal(original.data.payload.status,'DRAFT');
 assert.equal(confirmedArtifactResult(original,{...stale,readiness:{...stale.readiness,can_plan:true}}),undefined);
 assert.equal(confirmedArtifactResult(original,{...stale,payload:{...stale.payload,confirmation:{...stale.payload.confirmation,basis_sha256:'c'.repeat(64)}}}),undefined);
});
test('observing a hidden panel never reopens, changes geometry or crosses the current project owner',()=>{
 let changed=0;const state=new InteractionState(()=>changed++),original=envelope();state.owner(original.scope);const key=state.request(original,'FULL_WORKSPACE','review',true);state.resize(key,630);state.move(key,{x:48,y:32});state.collapse(key);const request=state.records.get(key).request;
 state.observe(original,{...original.data,answers:[answer()]});const record=state.records.get(key);assert.equal(record.visible,false);assert.equal(record.request,request);assert.equal(record.request.status,'DISMISSED');assert.equal(record.height,630);assert.deepEqual(record.position,{x:48,y:32});assert.equal(record.artifact.data.answers.length,1);
 state.owner({projectId:'other'});const before=changed;state.observe(original,{...original.data,answers:[answer('late')]});assert.equal(changed,before);assert.equal(record.artifact.data.answers.length,1);
});

function harness(t,mode='interaction',options={}){
 const saved={},storage=new Map();for(const key of ['window','document','navigator','localStorage','eeasDesktop','requestAnimationFrame','cancelAnimationFrame'])saved[key]=Object.getOwnPropertyDescriptor(globalThis,key);
 const globals={window:{addEventListener(){},removeEventListener(){},matchMedia:()=>({matches:false})},document:{documentElement:{lang:'zh-CN'},activeElement:null},navigator:{language:'zh-CN'},localStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,String(value))},requestAnimationFrame:fn=>{fn();return 1;},cancelAnimationFrame(){}};
 for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,value});delete globalThis.eeasDesktop;
 const host={createElement:type=>({type,props:{},children:[],parent:null,scrollTop:0,scrollLeft:0}),createTextNode:value=>({type:'#text',value,children:[],parent:null}),isTextNode:node=>node.type==='#text',replaceText:(node,value)=>{node.value=value;},insertNode(parent,node,anchor){if(node.parent){const a=node.parent.children;a.splice(a.indexOf(node),1);}node.parent=parent;const at=anchor?parent.children.indexOf(anchor):-1;parent.children.splice(at<0?parent.children.length:at,0,node);},removeNode(parent,node){const i=parent.children.indexOf(node);if(i>=0)parent.children.splice(i,1);node.parent=null;},setProperty:(node,key,value)=>{node.props[key]=value;},getParentNode:node=>node.parent,getFirstChild:node=>node.children[0],getNextSibling:node=>node.parent?.children[node.parent.children.indexOf(node)+1]};
 const renderer=createRenderer(host),modules=new Map(),calls=[],opens=[],draftWrites=[],mounts={graph:0},[version,setVersion]=solid.createSignal(0),[project,setProject]=solid.createSignal('project'),[active,setActive]=solid.createSignal(envelope());
 let current=session();const workspace={id:'workspace',project_id:'project',revision:1,binding_sha256:hash,root:'D:\\Research',allowed_write_paths:[]};
 const api={projectPath:pid=>'/api/projects/'+pid,workspaces:async()=>[workspace],request:async(path,method,body)=>{
  if(path.endsWith('/workbench-state/load'))return {status:'EMPTY',key:body.key,state_revision:0,fields:{},view:{},dirty:false};
  if(path.endsWith('/workbench-state/save')){draftWrites.push(body);return {status:'SAVED',key:body.key,state_revision:body.expected_revision+1,operation_id:body.operation_id,fields:body.fields,view:body.view,dirty:body.dirty};}
  throw Error('Unexpected UNIT IO '+path);
 }};
 function primitive(type,props){const node=host.createElement(type);renderer.spread(node,props);return node;}
 function load(file){file=resolve(file);if(file.endsWith('.css'))return {};if(!extname(file))file+=existsSync(file+'.tsx')?'.tsx':'.ts';if(modules.has(file))return modules.get(file).exports;if(file.endsWith('.json'))return JSON.parse(readFileSync(file,'utf8'));
  const module={exports:{}};modules.set(file,module);const legacy=process.env.EEAS_OBSERVATION_LEGACY_BASE&&['InspectorDocuments.tsx','InteractionPanels.tsx','interaction-state.ts'].find(name=>file.endsWith('/'+name));let code=readFileSync(legacy?resolve(process.env.EEAS_OBSERVATION_LEGACY_BASE,legacy):file,'utf8');if(file.endsWith('.tsx')){code=ts.transpileModule(code,{fileName:file,compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;code=babel.transformSync(code,{filename:file+'.jsx',presets:[[solidPreset,{generate:'universal',moduleName:'test-renderer'}]],configFile:false,babelrc:false}).code;}code=ts.transpileModule(code,{fileName:file.endsWith('.tsx')?file+'.js':file,compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText;
  const require=name=>{
   if(name==='marked')return {Marked};if(name==='dompurify')return {__esModule:true,default:DOMPurify};if(name==='solid-js')return solid;if(name==='solid-js/web')return {Dynamic};if(name==='test-renderer')return renderer;if(name==='@opencode-ai/ui/button')return {Button:props=>primitive('button',props)};if(name==='@opencode-ai/ui/icon')return {Icon:()=>null};
   if(/^\.\/api(?:\.ts)?$/.test(name))return {...apiModule,api,operationId:()=>crypto.randomUUID()};
   if(name==='./workbench-api')return {workbenchApi:{profiles:async()=>[]}};
   if(name==='./SemanticGraph')return {__esModule:true,default:props=>{mounts.graph++;const[zoom,setZoom]=solid.createSignal(1);return primitive('div',{'data-canvas-boundary':props.graph.id,get children(){return [primitive('span',{get children(){return 'viewport '+zoom();}}),primitive('button',{onClick:()=>setZoom(2),children:'unit graph zoom'})];}});}};
   if(['./GraphEditor','./WordPreview','./TextFeedbackEditor','./PlanningDeliveryResult'].includes(name))return {__esModule:true,default:()=>null};
   if(name==='./renderers')return {GenericRenderer:()=>null};
   if(name.startsWith('.'))return load(resolve(dirname(file),name));throw Error('Unconfigured UNIT dependency '+name);
  };new Function('require','module','exports',code)(require,module,module.exports);return module.exports;
 }
 const language=load(resolve(app,'src/ui-language.ts')),stop=language.startUILanguage(),State=load(resolve(app,'src/interaction-state.ts')).InteractionState,state=new State(()=>setVersion(v=>v+1));state.owner(active().scope);state.request(active(),'FULL_WORKSPACE','review',true);
 const context={open(value){opens.push(value);if(project()!==value.scope.projectId)return;if(mode==='interaction')state.request(value,'FULL_WORKSPACE','explicit confirmation result',true);else setActive(value);},async dispatch(id,target,input){calls.push({id,target,input});if(options.dispatch)return options.dispatch(id,target,input);if(id==='discovery.answer'){current={...current,answers:[...current.answers,answer('answer',input.text)]};return current;}if(id==='discovery.confirm'){current=confirmed(current);return current;}throw Error('Unexpected business action '+id);}};
 const registry={renderer:()=>({component:load(resolve(app,'src/DiscoveryRenderer.tsx')).default,descriptor:{label:'需求工作'}})},Component=load(resolve(app,'src',mode==='interaction'?'InteractionPanels.tsx':'InspectorDocuments.tsx')).default,root=host.createElement('root');
 const dispose=renderer.render(()=>mode==='interaction'?Component({state,get version(){return version();},registry,context,fullReady:true}):Component({get active(){return active();},get projectId(){return project();},registry,context,onBack:()=>setActive(undefined)}),root);
 t.after(()=>{dispose();stop();for(const[key,value]of Object.entries(saved))if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];});
 const all=(node=root)=>[node,...node.children.flatMap(child=>all(child))],text=(node=root)=>node.type==='#text'?String(node.value):node.children.map(text).join(' ');
 const visible=node=>{for(let p=node;p;p=p.parent)if(p.props?.hidden||p.props?.['aria-hidden']===true||p.props?.['data-visible']==='false')return false;return true;};
 return {state,context,opens,calls,mounts,draftWrites,setActive,setProject,language,all:()=>all(),text:()=>text(),find:(type,filter=()=>true)=>all().find(node=>node.type===type&&visible(node)&&filter(node)),allOf:(type,filter=()=>true)=>all().filter(node=>node.type===type&&filter(node)),textOf:text,
  button:label=>all().find(node=>node.type==='button'&&visible(node)&&text(node).trim()===language.t(label)),async locale(value){await language.setUILanguagePreference(value);await tick();}};
}
function click(node){assert.ok(node);assert.notEqual(node.props.disabled,true);node.props.onClick?.({preventDefault(){}});}
function enter(node,value){assert.ok(node);node.props.value=value;node.props.onInput?.({currentTarget:{value}});}

for(const mode of ['interaction','inspector'])test(`${mode}: actual saved answer refreshes the same-version pane without replacing its input, graph or scroll`,async t=>{
 const view=harness(t,mode);await tick();await tick();const input=view.find('textarea',node=>node.props['aria-label']==='回答 原问题'),frame=view.find('div',node=>node.props.class?.includes(mode==='interaction'?'as-interaction-content':'as-cached-document')),graph=view.find('div',node=>node.props['data-canvas-boundary']==='g');
 enter(input,'真实保存一次，继续保留输入 😀');globalThis.document.activeElement=input;frame.scrollTop=733;frame.props.onScroll({currentTarget:{scrollTop:733,scrollLeft:0}});click(view.button('unit graph zoom'));click(view.button('保存回答'));await tick();await tick();
 assert.equal(view.calls.length,1);assert.equal(view.calls[0].id,'discovery.answer');assert.ok(view.find('div',node=>node.props.class==='as-saved-answer'));assert.match(view.text(),/已保存 · 待接受/);assert.match(view.text(),/viewport 2/);
 assert.equal(view.find('textarea',node=>node.props['aria-label']==='回答 原问题'),input);assert.equal(view.find('div',node=>node.props['data-canvas-boundary']==='g'),graph);assert.equal(view.mounts.graph,1);assert.equal(frame.scrollTop,733);assert.equal(document.activeElement,input);assert.equal(input.props.value,'真实保存一次，继续保留输入 😀');assert.equal(view.opens.length,0);
 await view.locale('en');assert.equal(view.find('textarea',node=>node.props['aria-label']==='Answer 原问题'),input);assert.ok(view.text().includes(view.language.t('已保存 · 待接受')));assert.equal(view.opens.length,0);
});

test('Inspector receives same-key refreshed props; payload changes or a new version never mutate its old exact-version draft',async t=>{
 const view=harness(t,'inspector');await tick();const input=view.find('textarea',node=>node.props['aria-label']==='回答 原问题');enter(input,'未发送输入');
 const fresh={...session(),answers:[answer()]};view.setActive(envelope(fresh));await tick();assert.ok(view.find('div',node=>node.props.class==='as-saved-answer'));assert.equal(view.find('textarea',node=>node.props['aria-label']==='回答 原问题'),input);
 view.setActive(envelope({...fresh,payload:{...fresh.payload,candidate:{...fresh.payload.candidate,summary:'同hash伪造新正文'}}}));await tick();assert.doesNotMatch(view.text(),/同hash伪造新正文/);
 view.setActive(envelope(confirmed(fresh)));await tick();assert.equal(view.allOf('div',node=>node.props.class==='as-inspector as-cached-document').length,2);assert.equal(input.props.value,'未发送输入');assert.equal(input.parent.parent!==null,true);
});

test('a successful originating confirmation opens the accurate new version once and keeps the historical draft DOM',async t=>{
 const view=harness(t);await tick();await tick();const input=view.find('textarea',node=>node.props['aria-label']==='回答 原问题');enter(input,'保存的回答');click(view.button('保存回答'));await tick();const graph=view.find('div',node=>node.props['data-canvas-boundary']==='g');
 const confirm=view.button('确认选中的要求与图');click(confirm);await tick();await tick();assert.deepEqual(view.calls.map(c=>c.id),['discovery.answer','discovery.confirm']);assert.equal(view.opens.length,1);assert.equal(view.opens[0].revision,3);assert.equal(view.opens[0].data.payload.status,'CONFIRMED');assert.equal(view.button('要求与图已确认'),undefined,'confirmed controls are no longer offered');assert.equal(view.button('确认选中的要求与图'),undefined);assert.ok(view.find('h2',node=>view.textOf(node)==='已确认的要求与图'));assert.equal(view.state.records.size,2);
 assert.ok(view.allOf('textarea').includes(input));assert.ok(view.allOf('div').includes(graph));assert.equal(input.props.value,'保存的回答');assert.equal(view.calls.some(c=>c.id==='discovery.plan'),false);
 confirm.props.onClick();await tick();assert.equal(view.calls.length,2,'even a stale programmatic event cannot resend the old confirmation CAS');
});

test('typing during confirmation preserves the old draft and defers the exact confirmed version without another mutation',async t=>{
 let complete;const original=session(),view=harness(t,'interaction',{dispatch:async(id)=>{assert.equal(id,'discovery.confirm');return new Promise(resolve=>complete=resolve);}});await tick();await tick();const input=view.find('textarea',node=>node.props['aria-label']==='回答 原问题');
 click(view.button('确认选中的要求与图'));await tick();enter(input,'确认等待中又写下的新意见 😀');complete(confirmed(original));await tick();await tick();assert.equal(view.opens.length,0);assert.match(view.text(),/新版本已保存，当前草稿保留/);assert.equal(input.props.value,'确认等待中又写下的新意见 😀');assert.equal(view.find('div',node=>node.props.class==='as-artifact-action-content').props.inert,true);
 const inspect=view.button('查看详情');click(inspect);await tick();assert.equal(view.opens.length,1);assert.equal(view.opens[0].revision,3);assert.equal(view.calls.length,1);assert.ok(view.allOf('textarea').includes(input));assert.equal(view.calls.some(c=>c.id==='discovery.plan'),false);
 view.state.owner({projectId:'other'});await tick();inspect.props.onClick();assert.equal(view.opens.length,1,'a late click from a previously owned pane cannot navigate another project');
});

test('a late confirmation after another project becomes active cannot open or update that project',async t=>{
 let complete;const original=session(),view=harness(t,'interaction',{dispatch:async()=>new Promise(resolve=>complete=resolve)});await tick();await tick();click(view.button('确认选中的要求与图'));await tick();view.state.owner({projectId:'other'});view.setProject('other');complete(confirmed(original));await tick();await tick();assert.equal(view.opens.length,0);assert.equal(view.state.current().length,0);assert.equal(view.calls.length,1);
});
