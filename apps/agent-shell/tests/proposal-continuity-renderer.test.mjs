import {ApiError} from '../src/api.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
import {resolve,dirname,extname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
const dependencyRoot=process.env.EEAS_FRONTEND_APP??resolve(dirname(fileURLToPath(import.meta.url)),'..'),req=createRequire(resolve(dependencyRoot,'package.json'));
const ts=req('typescript');
const babel=req('@babel/core');
const solidPreset=(await import(pathToFileURL(resolve(dependencyRoot,'node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js')).href)).default;
const solid=await import(pathToFileURL(resolve(dependencyRoot,'node_modules/solid-js/dist/solid.js')).href);

// Client UNIT: execute the real ProposalRenderer, draft signals, stable item
// identities, translations, source notes and delivery permission helper. The
// universal host, network/desktop picker, summary and canvas/Office children are explicit
// presentation/IO boundaries. This does not qualify API writes, CSS or Windows.
const app=process.env.EEAS_FRONTEND_APP??resolve(dirname(fileURLToPath(import.meta.url)),'..');
const proposed=process.env.EEAS_PROPOSAL_SOURCE??resolve(app,'src');
const source=readFileSync(resolve(app,'node_modules/solid-js/universal/dist/universal.js'),'utf8')
 .replace("from 'solid-js'",`from '${pathToFileURL(resolve(app,'node_modules/solid-js/dist/solid.js')).href}'`);
const {createRenderer}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const tick=()=>new Promise(done=>setImmediate(done)),hash='a'.repeat(64);
import {proposalFixture,editedFixture,observedAnswer,confirmedFixture} from './proposal-continuity-fixture.mjs';
function harness(t,initial=proposalFixture(),options={}){
 const saved={},values=new Map();for(const key of ['window','navigator','localStorage','eeasDesktop'])saved[key]=Object.getOwnPropertyDescriptor(globalThis,key);
 Object.defineProperty(globalThis,'window',{configurable:true,value:{addEventListener(){},removeEventListener(){},matchMedia:()=>({matches:false})}});
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{language:'zh-CN'}});
 Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,String(value))}});delete globalThis.eeasDesktop;
 const host={createElement:type=>({type,props:{},children:[],parent:null}),createTextNode:value=>({type:'#text',value,children:[],parent:null}),isTextNode:node=>node.type==='#text',replaceText:(node,value)=>{node.value=value;},
  insertNode(parent,node,anchor){if(node.parent){const children=node.parent.children;children.splice(children.indexOf(node),1);}node.parent=parent;const at=anchor?parent.children.indexOf(anchor):-1;parent.children.splice(at<0?parent.children.length:at,0,node);},
  removeNode(parent,node){const index=parent.children.indexOf(node);if(index>=0)parent.children.splice(index,1);node.parent=null;},setProperty:(node,key,value)=>{node.props[key]=value;},getParentNode:node=>node.parent,getFirstChild:node=>node.children[0],getNextSibling:node=>node.parent?.children[node.parent.children.indexOf(node)+1]};
 const renderer=createRenderer(host),modules=new Map(),calls=[],io=[];
 const workspace={id:'workspace_one',project_id:'project_one',revision:2,binding_sha256:hash,root:'D:\\Research',allowed_write_paths:['delivery/']};
 const api={projectPath:pid=>'/api/projects/'+pid,workspaces:async()=>{io.push('workspaces');if(options.destinationsError)throw Error('工作目录暂不可读');return [workspace];},
  file:async(pid,wid,path)=>{io.push(['file',pid,wid,path]);return {current:true,source_ref:{kind:'local_file',project_id:pid,workspace_id:wid,binding_revision:2,binding_sha256:hash,path,revision:1,sha256:hash}};}};
 function primitive(type,props){const node=host.createElement(type);renderer.spread(node,props);return node;}
 function load(file){file=resolve(file);if(file.startsWith(resolve(app,'src')+'/')){const alternative=resolve(proposed,file.slice(resolve(app,'src').length+1));if(existsSync(alternative))file=alternative;}if(file.endsWith('packages/i18n/renderer.json')&&process.env.EEAS_PROPOSAL_I18N)file=process.env.EEAS_PROPOSAL_I18N;if(file.startsWith(proposed+'/')&&!existsSync(file)&&!existsSync(file+'.ts')&&!existsSync(file+'.tsx'))file=resolve(app,'src',file.slice(proposed.length+1));if(!extname(file))file+=existsSync(file+'.tsx')?'.tsx':'.ts';if(modules.has(file))return modules.get(file).exports;if(file.endsWith('.json'))return JSON.parse(readFileSync(file,'utf8'));
  const module={exports:{}};modules.set(file,module);let code=readFileSync(file,'utf8');
  if(file.endsWith('.tsx')){code=ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;code=babel.transformSync(code,{filename:file+'.jsx',presets:[[solidPreset,{generate:'universal',moduleName:'test-renderer'}]],configFile:false,babelrc:false}).code;}
  code=ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText;
  const require=name=>{
   if(name.endsWith('.css'))return {};if(name==='solid-js')return solid;if(name==='test-renderer')return renderer;
   if(name==='@opencode-ai/ui/button')return {Button:props=>primitive('button',props)};
   if(/^\.\/api(?:\.ts)?$/.test(name))return {api,ApiError,operationId:()=>{throw Error('No implicit mutation in component UNIT');}};
   if(name==='./workbench-api')return {workbenchApi:{profiles:async()=>{io.push('profiles');if(options.profilesError)throw Error('资料读取失败，保留原文');return [];}}};
   if(name==='./SummaryContent')return {__esModule:true,default:props=>primitive('p',{get children(){return props.text;}})};
   if(name==='./TaskGraph')return {__esModule:true,default:props=>primitive('div',{'data-graph-boundary':true,get 'data-resolved-count'(){return props.questions.filter(value=>value.resolved).length;}})};
   if(name==='./SemanticGraph')return {__esModule:true,default:props=>primitive('div',{'data-canvas-boundary':props.graph.id,get children(){return props.graph.title;}})};
   if(['./GraphEditor','./WordPreview','./TextFeedbackEditor'].includes(name))return {__esModule:true,default:()=>null};
   if(name==='./PlanningDeliveryResult')return {__esModule:true,default:props=>primitive('div',{'data-delivery-boundary':true,get children(){return props.value.status;}})};
   if(name.startsWith('.'))return load(resolve(dirname(file),name));throw Error('Unconfigured UNIT dependency '+name);
  };
  new Function('require','module','exports',code)(require,module,module.exports);return module.exports;
 }
 const language=load(resolve(app,'src/ui-language.ts')),stop=language.startUILanguage(),[current,setCurrent]=solid.createSignal(initial);
 const artifact={...initial,get data(){return current().data;}};
 const context={dispatch:async(id,target,input)=>{calls.push({id,target,input});if(options.dispatch)return options.dispatch(id,target,input);return {operation_id:'stage_once',status:'STAGE_REPORT'};}};
 const Component=load(resolve(app,'src/ProposalRenderer.tsx')).default,root=host.createElement('root'),dispose=renderer.render(()=>Component({artifact,context}),root);
 t.after(()=>{dispose();stop();for(const[key,value]of Object.entries(saved))if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];});
 const all=(node=root)=>[node,...node.children.flatMap(child=>all(child))];
 const text=(node=root,visible=false)=>visible&&node.props?.hidden?'':node.type==='#text'?String(node.value):node.children.filter(child=>!visible||node.type!=='details'||node.props.open||child.type==='summary').map(child=>text(child,visible)).join(' ');
 const view={all:()=>all(),text:()=>text(),visible:()=>text(root,true),find:(type,filter=()=>true)=>all().find(node=>node.type===type&&filter(node)),findIn:(parent,type,filter=()=>true)=>all(parent).find(node=>node.type===type&&filter(node)),
  button:label=>all().find(node=>node.type==='button'&&text(node).trim()===language.t(label)),textOf:node=>text(node),calls,io,artifact,setCurrent,language,async locale(value){await language.setUILanguagePreference(value);await tick();}};
 return view;
}
function click(node){assert.ok(node,'action exists');assert.notEqual(node.props.disabled,true,'action remains usable');node.props.onClick?.({preventDefault(){}});}
function enter(node,value){assert.ok(node);node.props.value=value;node.props.onInput?.({currentTarget:{value}});}
function choose(node,value){assert.ok(node);node.props.value=value;node.props.onChange?.({currentTarget:{value}});}
function open(node,value=true){assert.ok(node);node.props.open=value;node.props.onToggle?.({currentTarget:{open:value}});}


test('confirmed plan accepts a newly saved answer inline with the exact old basis and only one explicit new decision',async t=>{
 let view;view=harness(t,proposalFixture(),{dispatch:async(id,target,input)=>{if(id==='proposal.answer')view.setCurrent(observedAnswer(view.artifact,input.questionId,input.text));return {};}});await tick();
 const card=()=>view.find('article',node=>node.props['data-question-id']==='Q12'),field=view.findIn(card(),'textarea'),oldField=field;
 assert.equal(view.button('此版本计划已确认').props.disabled,true);enter(field,'负责人新增的完整答复');click(view.findIn(card(),'button',node=>view.textOf(node).trim()==='保存答复'));await tick();
 assert.equal(view.calls.length,1);assert.equal(view.calls[0].id,'proposal.answer');assert.equal(view.artifact.revision,3);assert.equal(view.findIn(card(),'textarea'),oldField);assert.equal(oldField.props.value,'负责人新增的完整答复');
 const actual=view.artifact.data.answers.at(-1),select=view.findIn(card(),'select');choose(select,actual.answer_id);await tick();assert.equal(view.button('此版本计划已确认').props.disabled,true);
 enter(view.findIn(card(),'input'),'负责人明确采纳新答复');await tick();click(view.button('更新计划确认'));await tick();
 assert.equal(view.calls.length,2);assert.equal(view.calls[1].id,'proposal.confirm');assert.deepEqual(view.calls[1].input,[{question_id:'Q12',mode:'ANSWER_ACCEPTED',answer_id:actual.answer_id,reason:'负责人明确采纳新答复'}]);assert.equal(view.calls[1].target.revision,3);assert.equal(view.calls[1].target.contentHash,'a'.repeat(64));
 assert.equal(view.artifact.data.payload.confirmation.resolutions.length,11);assert.equal(view.artifact.data.answers.at(-1).accepted,false);assert.equal(view.find('div',node=>node.props['data-graph-boundary']).props['data-resolved-count'],11);
});

test('opening an old decision does not enable no-op confirmation; reason edits and same-text new answer records are distinct',async t=>{
 const initial=observedAnswer(proposalFixture(),'Q01',proposalFixture().data.answers[0].text),view=harness(t,initial);await tick();click(view.find('button',node=>/已采用\s*11\s*项/.test(view.textOf(node))));await tick();
 const card=view.find('article',node=>node.props['data-question-id']==='Q01');click(view.findIn(card,'button',node=>view.textOf(node).trim()==='补充或调整答复'));await tick();assert.equal(view.button('此版本计划已确认').props.disabled,true);
 const select=view.findIn(card,'select'),reason=view.findIn(card,'input');assert.equal(select.props.value,'answer_1');assert.equal(reason.props.value,'负责人明确采纳此版本依据');
 enter(reason,'核对后补充的接受理由');await tick();assert.equal(view.button('更新计划确认').props.disabled,false);click(view.button('更新计划确认'));await tick();assert.deepEqual(view.calls[0].input,[{question_id:'Q01',mode:'ANSWER_ACCEPTED',answer_id:'answer_1',reason:'核对后补充的接受理由'}]);
 enter(reason,'负责人明确采纳此版本依据');await tick();assert.equal(view.button('此版本计划已确认').props.disabled,true);choose(select,initial.data.answers.at(-1).answer_id);await tick();assert.equal(view.button('更新计划确认').props.disabled,false);
});

test('edited draft shows carried decisions as awaiting confirmation, preserves exact provenance and leaves the graph unconfirmed',async t=>{
 const initial=editedFixture(),before=JSON.stringify(initial),view=harness(t,initial);await tick();assert.match(view.visible(),/待确认/);assert.match(view.visible(),/沿用决定 9 项/);assert.doesNotMatch(view.visible(),/已采用 9 项/);
 assert.equal(view.find('div',node=>node.props['data-graph-boundary']).props['data-resolved-count'],0);assert.equal(view.button('确认此版本计划').props.disabled,false);assert.equal(view.button('生成完整交付文件').props.disabled,true);
 const toggle=view.find('button',node=>view.textOf(node).includes('沿用决定 9 项'));click(toggle);await tick();const card=view.find('article',node=>node.props['data-question-id']==='Q01');assert.equal(card.props['data-retained'],'true');assert.equal(card.props['data-adopted'],'false');assert.match(view.textOf(card),/来自已确认版本 3/);
 click(view.button('确认此版本计划'));await tick();assert.deepEqual(view.calls[0].input,[]);assert.equal(JSON.stringify(initial),before);
});

test('answer observation and language changes preserve the original form nodes and unsubmitted local decisions',async t=>{
 const initial=proposalFixture(),view=harness(t,initial);await tick();const card=view.find('article',node=>node.props['data-question-id']==='Q12'),field=view.findIn(card,'textarea'),select=view.findIn(card,'select'),reason=view.findIn(card,'input');enter(field,'尚未提交的新输入🙂');enter(reason,'未提交理由');
 view.setCurrent(observedAnswer(initial));await tick();assert.equal(view.findIn(card,'textarea'),field);assert.equal(view.findIn(card,'select'),select);assert.equal(field.props.value,'尚未提交的新输入🙂');assert.equal(reason.props.value,'未提交理由');
 await view.locale('en');assert.equal(view.findIn(card,'textarea'),field);assert.equal(field.props.value,'尚未提交的新输入🙂');assert.match(view.visible(),/This plan version is confirmed/);assert.equal(view.calls.length,0);
});

// Optional exact saved HTTP JSON from the sibling zero-model ASGI qualification.
// This replays complete server payloads; it does not call an App API.
for(const [kind,file] of [['confirmed',process.env.EEAS_PLAN_ACTUAL_CONFIRMED],['draft',process.env.EEAS_PLAN_ACTUAL_DRAFT]])if(file)test(`actual saved ASGI ${kind} payload renders exact continuity without inventing adoption`,async t=>{
 const data=JSON.parse(readFileSync(file,'utf8')),artifact={artifactId:data.id,type:'proposal',schemaVersion:1,scope:{projectId:data.project_id,workspaceId:data.payload.workspace_ref?.id},revision:data.revision,contentHash:data.sha256,availability:'IMPLEMENTED',data},view=harness(t,artifact);await tick();
 if(kind==='confirmed'){assert.equal(view.button('此版本计划已确认').props.disabled,true);assert.equal(view.find('div',node=>node.props['data-graph-boundary']).props['data-resolved-count'],data.payload.confirmation.resolutions.length);}
 else{assert.equal(data.payload.confirmation,null);assert.equal(view.find('div',node=>node.props['data-graph-boundary']).props['data-resolved-count'],0);const count=data.payload.decision_continuity.retained_decisions.length;assert.match(view.visible(),new RegExp('沿用决定 '+count+' 项'));assert.equal(view.button('确认此版本计划').props.disabled,false);}
 for(const id of data.payload.decision_continuity.structural_impact.affected_node_ids)assert.match(view.text(),new RegExp(id));assert.equal(view.calls.length,0);
});

test('earlier-scope answers remain readable but only a freshly saved current-scope record is selectable',async t=>{
 const initial=editedFixture();initial.data.payload.decision_continuity.question_scope_revisions={Q02:4,Q03:4};const view=harness(t,initial);await tick();const card=view.find('article',node=>node.props['data-question-id']==='Q02'),select=view.findIn(card,'select'),old=initial.data.answers.find(row=>row.question_id==='Q02');assert.match(view.textOf(card),/原范围答复 · 仅供参考/);assert.ok(view.textOf(card).includes(old.text));assert.equal(view.findIn(select,'option',node=>node.props.value===old.answer_id),undefined);
 view.setCurrent(observedAnswer(initial,'Q02','当前范围的新答复'));await tick();assert.equal(view.findIn(card,'select'),select);assert.ok(view.findIn(select,'option',node=>node.props.value===view.artifact.data.answers.at(-1).answer_id));assert.equal(view.calls.length,0);
});
