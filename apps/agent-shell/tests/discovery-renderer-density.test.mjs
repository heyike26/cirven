import test from 'node:test';
import {ApiError} from '../src/api.ts';
import {Marked} from 'marked';
import DOMPurify from 'dompurify';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
import {resolve,dirname,extname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';
import * as solid from '../node_modules/solid-js/dist/solid.js';

// Client UNIT: execute the real DiscoveryRenderer, draft signals, stable item
// identities, translations, source notes and delivery permission helper. Rich-summary HTML, the
// universal host, network/desktop picker and canvas/Office children are explicit
// presentation/IO boundaries. This does not qualify API writes, CSS or Windows.
const app=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const source=readFileSync(resolve(app,'node_modules/solid-js/universal/dist/universal.js'),'utf8')
 .replace("from 'solid-js'",`from '${pathToFileURL(resolve(app,'node_modules/solid-js/dist/solid.js')).href}'`);
const {createRenderer}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const tick=()=>new Promise(done=>setImmediate(done)),hash='a'.repeat(64);
function candidate(){return {title:'用户定义的工程 😀',phase:'DEFINE',summary:'用户原话与模型理解，保持原样。',
 claims:[{id:'requirement',text:'人工确认的要求',category:'REQUIREMENT',origin:'USER_STATEMENT',source_anchors:[],acceptance:['可检查的验收条件']}],
 questions:Array.from({length:6},(_,index)=>({id:'q'+index,text:'需要用户裁决的问题 '+index,reason:'这项会影响原工作边界 '+index,category:'DECISION',blocking:true,affects_claim_ids:['requirement'],requested_materials:['补充的资料 '+index],source_anchors:[]})),
 competitors:[],graphs:[],next_actions:[{id:'next',method:'模型原方法',action:'明确下一步',reason:'模型原始理由不改写',expected_artifact:'原预期产物'}]};}
function session(value=candidate()){return {id:'discovery_one',project_id:'project_one',revision:4,current_revision:4,sha256:hash,source_current:true,
 payload:{entry_mode:'PROBLEM',workspace_ref:{id:'workspace_one',revision:2,binding_sha256:hash},materials:[],source_receipt:{documents:[]},candidate:value,history:[],status:'DRAFT',accepted_claim_ids:['requirement'],accepted_graph_ids:[],resolutions:[]},
 answers:[{id:'saved_answer',question_id:'q0',text:'已保存但未接受的原回答',session_revision:4,accepted:false}],
 readiness:{requirements_confirmed:false,can_plan:false,pending_count:value.questions.length,blocking_count:value.questions.filter(q=>q.blocking).length,review_status:'PENDING',semantic_verification:'NOT_PERFORMED',execution_authorized:false}};}
function harness(t,initial=session(),options={}){
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
 function load(file){file=resolve(file);if(file.endsWith('.css'))return {};if(!extname(file))file+=existsSync(file+'.tsx')?'.tsx':'.ts';if(modules.has(file))return modules.get(file).exports;if(file.endsWith('.json'))return JSON.parse(readFileSync(file,'utf8'));
  const module={exports:{}};modules.set(file,module);let code=readFileSync(file,'utf8');
  if(file.endsWith('.tsx')){code=ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;code=babel.transformSync(code,{filename:file+'.jsx',presets:[[solidPreset,{generate:'universal',moduleName:'test-renderer'}]],configFile:false,babelrc:false}).code;}
  code=ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText;
  const require=name=>{
   if(name==='marked')return {Marked};if(name==='dompurify')return DOMPurify;
   if(name==='solid-js')return solid;if(name==='test-renderer')return renderer;
   if(name==='@opencode-ai/ui/button')return {Button:props=>primitive('button',props)};
   if(/^\.\/api(?:\.ts)?$/.test(name))return {api,ApiError,operationId:()=>{throw Error('No implicit mutation in component UNIT');}};
   if(name==='./workbench-api')return {workbenchApi:{profiles:async()=>{io.push('profiles');if(options.profilesError)throw Error('资料读取失败，保留原文');return [];}}};
   if(name==='./SummaryContent')return {__esModule:true,default:props=>primitive('div',{'data-summary-html-boundary':true,get children(){return props.text;}})};
   if(name==='./SemanticGraph')return {__esModule:true,default:props=>primitive('div',{'data-canvas-boundary':props.graph.id,get children(){return props.graph.title;}})};
   if(['./GraphEditor','./WordPreview','./TextFeedbackEditor'].includes(name))return {__esModule:true,default:()=>null};
   if(name==='./PlanningDeliveryResult')return {__esModule:true,default:props=>primitive('div',{'data-delivery-boundary':true,get children(){return props.value.status;}})};
   if(name.startsWith('.'))return load(resolve(dirname(file),name));throw Error('Unconfigured UNIT dependency '+name);
  };
  try{new Function('require','module','exports',code)(require,module,module.exports);}catch(error){error.message=file+': '+error.message;throw error;}return module.exports;
 }
 const language=load(resolve(app,'src/ui-language.ts')),stop=language.startUILanguage(),[current,setCurrent]=solid.createSignal(initial);
 const artifact={artifactId:initial.id,type:'discovery',scope:{projectId:initial.project_id,workspaceId:'workspace_one'},revision:initial.revision,contentHash:initial.sha256,get data(){return current();}};
 const context={dispatch:async(id,target,input)=>{calls.push({id,target,input});if(options.dispatch)return options.dispatch(id,target,input);return {operation_id:'stage_once',status:'STAGE_REPORT'};}};
 const Component=load(resolve(app,'src/DiscoveryRenderer.tsx')).default,root=host.createElement('root'),dispose=renderer.render(()=>Component({artifact,context}),root);
 t.after(()=>{dispose();stop();for(const[key,value]of Object.entries(saved))if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];});
 const all=(node=root)=>[node,...node.children.flatMap(child=>all(child))];
 const text=(node=root,visible=false)=>node.type==='#text'?String(node.value):node.children.filter(child=>!visible||node.type!=='details'||node.props.open||child.type==='summary').map(child=>text(child,visible)).join(' ');
 const view={all:()=>all(),text:()=>text(),visible:()=>text(root,true),find:(type,filter=()=>true)=>all().find(node=>node.type===type&&filter(node)),findIn:(parent,type,filter=()=>true)=>all(parent).find(node=>node.type===type&&filter(node)),
  button:label=>all().find(node=>node.type==='button'&&text(node).trim()===language.t(label)),textOf:node=>text(node),calls,io,artifact,setCurrent,language,async locale(value){await language.setUILanguagePreference(value);await tick();}};
 return view;
}
function click(node){assert.ok(node,'action exists');assert.notEqual(node.props.disabled,true,'action remains usable');node.props.onClick?.({preventDefault(){}});}
function enter(node,value){assert.ok(node);node.props.value=value;node.props.onInput?.({currentTarget:{value}});}
function choose(node,value){assert.ok(node);node.props.value=value;node.props.onChange?.({currentTarget:{value}});}
function open(node,value=true){assert.ok(node);node.props.open=value;node.props.onToggle?.({currentTarget:{open:value}});}

test('six questions and unaccepted answers stay inline while repeated prose and empty sections leave the default reading path',async t=>{
 const initial=session(),before=JSON.stringify(initial),view=harness(t,initial);await tick();
 assert.equal(view.all().filter(node=>node.props?.class==='as-question-card').length,6);
 for(const question of initial.payload.candidate.questions){assert.ok(view.visible().includes(question.text));assert.ok(view.visible().includes(question.reason));assert.ok(view.find('textarea',node=>node.props['aria-label']===view.language.t('回答 {value0}',{value0:question.text})));}
 assert.match(view.visible(),/已保存 · 待接受/);assert.match(view.visible(),/6\s+项待确认/);assert.match(view.visible(),/人工确认的要求/);
 assert.doesNotMatch(view.visible(),/工作位置可以回访|包括直接竞品|图与条目共享来源|可以逐项回答|未完成确认也可生成草稿/);
 assert.equal(view.find('nav',node=>node.props.class==='as-phase-track'),undefined);
 assert.equal(view.find('section',node=>node.props.class==='as-discovery-diagrams'),undefined);
 assert.equal(view.find('select',node=>node.props.value==='DEFINE'),undefined,'phase picker belongs to explicit editing only');
 assert.ok(view.button('保存或导入竞品资料'));assert.ok(view.button('生成草稿报告与待确认文件'));assert.ok(view.button('确认选中的要求与图'));
 assert.equal(view.calls.length,0);assert.equal(JSON.stringify(initial),before);
});

test('the primary next action stays one click; its original rationale and optional report settings are single-level disclosures',async t=>{
 const early=session();early.payload.candidate.claims[0]={...early.payload.candidate.claims[0],category:'NEED',acceptance:[]};early.payload.accepted_claim_ids=[];const view=harness(t,early);await tick();const next=view.find('details',node=>node.props.class==='as-next-action-details'),options=view.find('details',node=>node.props.class==='as-discovery-report-options');
 assert.doesNotMatch(view.visible(),/模型原始理由不改写|原预期产物|采用已审阅的写法经验|模板相对路径/);
 assert.ok(view.visible().includes('明确下一步'));click(view.button('沿此方向继续'));await tick();
 assert.deepEqual(view.calls.map(item=>[item.id,item.input]),[['discovery.advance',{message:'明确下一步'}]]);
 open(next);assert.match(view.visible(),/模型原始理由不改写/);assert.match(view.visible(),/原预期产物/);open(options);
 assert.match(view.visible(),/模板相对路径/);assert.match(view.visible(),/采用已审阅的写法经验/);
 assert.equal(view.all().some(node=>node.type==='details'&&node.parent===options),false,'report choices have no nested disclosure');
 assert.equal(view.calls.length,1);
});

test('saving an answer does not confirm; explicit acceptance keeps the exact answer and original discovery target',async t=>{
 const view=harness(t);await tick();const input=view.find('textarea',node=>node.props['aria-label']==='回答 需要用户裁决的问题 0'),question=input.parent;
 enter(input,'用户未发送草稿 😀');click(view.findIn(question,'button',node=>view.textOf(node).trim()===view.language.t('保存回答')));await tick();
 assert.deepEqual(view.calls.map(item=>item.id),['discovery.answer']);assert.deepEqual(view.calls[0].input,{questionId:'q0',text:'用户未发送草稿 😀'});assert.equal(view.calls[0].target,view.artifact);
 const choices=view.findIn(question,'select');choose(choices,'saved_answer');enter(view.findIn(question,'input'),'用户明确核对过此依据');
 click(view.button('确认选中的要求与图'));await tick();
 assert.deepEqual(view.calls[1].input,{claims:['requirement'],graphs:[],resolutions:[{question_id:'q0',mode:'ANSWER_ACCEPTED',answer_id:'saved_answer',reason:'用户明确核对过此依据'}]});
 assert.equal(view.calls[1].id,'discovery.confirm');assert.equal(view.calls[1].target,view.artifact);assert.equal(view.artifact.revision,4);
});

test('unanswered stage report remains directly usable; folder restrictions stay visible outside collapsed settings',async t=>{
 const view=harness(t);await tick();const report=view.button('生成草稿报告与待确认文件');click(report);await tick();
 assert.deepEqual(view.calls.map(item=>item.id),['discovery.stage']);assert.deepEqual(view.calls[0].input,{template:undefined,experience:undefined,graphs:[]});
 const options=view.find('details',node=>node.props.class==='as-discovery-report-options');open(options);
 const save=view.findIn(options,'input',node=>node.props.type==='checkbox');save.props.onChange({currentTarget:{checked:true}});await tick();open(options,false);
 assert.match(view.visible(),/选择已获写入范围的目录/);assert.equal(report.props.disabled,true);assert.equal(view.calls.length,1);
 open(options);const directory=view.find('input',node=>node.props['aria-label']==='阶段交付相对目录');enter(directory,'delivery/review');await tick();open(options,false);
 assert.equal(report.props.disabled,false);click(report);await tick();assert.equal(view.calls.at(-1).input.directory,'delivery/review');assert.equal(view.calls.at(-1).input.workspace.id,'workspace_one');assert.equal(view.calls.length,2);
});

test('language switches preserve the same answer draft and model content; stale source still blocks confirmation and resource errors remain visible',async t=>{
 const initial=session(),view=harness(t,initial,{profilesError:true,destinationsError:true});await tick();await tick();
 const input=view.find('textarea',node=>node.props['aria-label']==='回答 需要用户裁决的问题 0');enter(input,'尚未发送 IME 中文 😀');const options=view.find('details',node=>node.props.class==='as-discovery-report-options');
 assert.equal(options.props.open,undefined);assert.match(view.visible(),/模板经验读取失败/);assert.match(view.visible(),/实际目录读取失败/);assert.equal(view.button('生成草稿报告与待确认文件').props.disabled,false,'a private stage report does not need the unavailable optional folder');
 await view.locale('en');assert.equal(view.find('textarea',node=>node.props['aria-label']==='Answer 需要用户裁决的问题 0'),input);assert.equal(input.props.value,'尚未发送 IME 中文 😀');assert.match(view.visible(),/用户原话与模型理解/);assert.ok(view.button('生成草稿报告与待确认文件'));assert.equal(view.calls.length,0);
 view.setCurrent({...initial,source_current:false});await tick();assert.equal(view.button('确认选中的要求与图').props.disabled,true);assert.ok(view.visible().includes(view.language.t('来源需要复核')));
 await view.locale('zh-CN');assert.equal(input.props.value,'尚未发送 IME 中文 😀');assert.equal(view.calls.length,0);
});

test('an initial unconfirmed statement has no empty question/graph blocks and can produce a stage report directly',async t=>{
 const value=candidate();value.questions=[];value.graphs=[];value.next_actions=[];value.claims[0]={...value.claims[0],category:'NEED',acceptance:[]};
 const initial=session(value);initial.answers=[];initial.payload.accepted_claim_ids=[];const view=harness(t,initial);await tick();
 assert.equal(view.find('section',node=>node.props.class==='as-plan-questions'),undefined);assert.equal(view.find('section',node=>node.props.class==='as-discovery-diagrams'),undefined);assert.doesNotMatch(view.visible(),/0\s+项待确认|工作位置可以回访/);
 assert.equal(view.button('确认选中的要求与图').props.disabled,true);click(view.button('生成草稿报告与待确认文件'));await tick();assert.deepEqual(view.calls.map(item=>item.id),['discovery.stage']);
});

test('selecting a real template reference survives collapsing and language changes and is sent only with explicit generation',async t=>{
 const view=harness(t);await tick();const options=view.find('details',node=>node.props.class==='as-discovery-report-options');open(options);
 const path=view.findIn(options,'input',node=>node.props.placeholder==='templates/report.dotx');enter(path,'templates/用户任务书.docx');click(view.button('读取实际模板'));await tick();
 assert.equal(view.calls.length,0);assert.deepEqual(view.io.filter(item=>Array.isArray(item)),[['file','project_one','workspace_one','templates/用户任务书.docx']]);open(options,false);
 assert.match(view.visible(),/用户任务书\.docx/);await view.locale('en');assert.equal(view.find('input',node=>node.props.placeholder==='templates/report.dotx'),path);assert.equal(path.props.value,'templates/用户任务书.docx');assert.equal(view.calls.length,0);
 click(view.button('生成草稿报告与待确认文件'));await tick();assert.equal(view.calls.length,1);assert.equal(view.calls[0].id,'discovery.stage');assert.equal(view.calls[0].input.template.project_id,'project_one');assert.equal(view.calls[0].input.template.path,'templates/用户任务书.docx');assert.equal(view.calls[0].input.template.sha256,hash);
});

test('actual populated graph and comparison content stays present, with the same graph acceptance and original provenance access',async t=>{
 const value=candidate();value.graphs=[{id:'flow_one',title:'真实图标题原文',kind:'FLOW',nodes:[],edges:[]}];value.competitors=[{id:'manual',name:'人工现行办法',alternative_type:'MANUAL',summary:'模型原始比较内容',observations:[{dimension:'可回访',text:'尚未核实的原观察',status:'UNKNOWN',source_anchors:[]}]}];
 const view=harness(t,session(value));await tick();assert.match(view.visible(),/真实图标题原文|人工现行办法/);assert.match(view.visible(),/尚未核实的原观察/);assert.match(view.visible(),/待核实/);
 const graph=view.find('section',node=>node.props.class==='as-discovery-diagrams'),check=view.findIn(graph,'input',node=>node.props.type==='checkbox');check.props.onChange({currentTarget:{checked:true}});await tick();
 click(view.button('确认选中的要求与图'));await tick();assert.deepEqual(view.calls.at(-1).input.graphs,['flow_one']);
 const history=view.find('details',node=>node.props.class==='as-discovery-history');open(history);assert.match(view.visible(),/结构检查不等于工程内容已验证/);assert.match(view.visible(),/保存回答不会自动接受答案/);
});

if(process.env.EEAS_DISCOVERY_CANDIDATE_FIXTURE)test('actual Pro-returned six-question candidate remains readable without rewriting content or treating validation failure as acceptance',async t=>{
 const raw=JSON.parse(readFileSync(process.env.EEAS_DISCOVERY_CANDIDATE_FIXTURE,'utf8')),entry=raw.result?.candidate_json??raw.candidate_json??raw;
 const model=typeof entry==='string'?JSON.parse(entry):entry;assert.equal(model.questions.length,6,'bind the actual original B candidate');
 // Raw model JSON omits legal default arrays. API model_dump materializes them;
 // mirror only those declared defaults without repairing any fact/graph evidence.
 const value={...model,claims:model.claims.map(item=>({acceptance:[],source_anchors:[],...item})),questions:model.questions.map(item=>({requested_materials:[],source_anchors:[],affects_claim_ids:[],...item})),competitors:(model.competitors??[]).map(item=>({observations:[],...item,observations:(item.observations??[]).map(row=>({source_anchors:[],...row}))})),graphs:model.graphs??[],next_actions:model.next_actions??[]};const initial=session(value);initial.payload.accepted_claim_ids=[];initial.answers=[];const before=JSON.stringify(initial),view=harness(t,initial);await tick();
 for(const question of value.questions){assert.ok(view.visible().includes(question.text));assert.ok(view.visible().includes(question.reason));}
 assert.equal(view.all().filter(node=>node.props?.class==='as-question-card').length,6);assert.ok(view.button('生成草稿报告与待确认文件'));assert.equal(view.button('确认选中的要求与图').props.disabled,true);assert.equal(JSON.stringify(initial),before);assert.equal(view.calls.length,0);
});


test('plan wait budget is one inline choice, defaults to five minutes and freezes only the explicit click',async t=>{
 let release;const waiting=new Promise(done=>{release=done;}),initial=session();initial.payload.status='CONFIRMED';initial.readiness.can_plan=true;
 const view=harness(t,initial,{dispatch:async()=>waiting});await tick();
 const choice=view.find('select',node=>node.props.class==='as-plan-wait-budget');assert.ok(choice);assert.equal(choice.props.value,300);
 assert.deepEqual(choice.children.filter(node=>node.type==='option').map(node=>node.props.value),[120,300,600]);assert.equal(choice.props.disabled,false);
 choose(choice,'600');assert.equal(choice.props.value,600);await view.locale('en');assert.equal(choice.props.value,600);assert.equal(choice.props['aria-label'],'Wait budget for this plan');
 click(view.button('生成任务计划'));await tick();assert.equal(view.calls.length,1);assert.equal(view.calls[0].id,'discovery.plan');assert.equal(view.calls[0].target,view.artifact);assert.equal(view.calls[0].input.timeoutSeconds,600);assert.equal(choice.props.disabled,true);
 choice.props.onChange({currentTarget:{value:'120'}});assert.equal(view.calls[0].input.timeoutSeconds,600,'later local field changes cannot mutate the submitted packet');
 release({status:'PROCESSING'});await tick();assert.equal(view.calls.length,1);
});

test('wait choice never grants readiness or starts a plan while questions remain unconfirmed',async t=>{
 const view=harness(t);await tick();assert.equal(view.find('select',node=>node.props.class==='as-plan-wait-budget'),undefined);assert.equal(view.button('生成任务计划'),undefined);assert.equal(view.calls.length,0);
});


test('group selection selects only requirements with observable acceptance, preserves other choices and never confirms by itself',async t=>{
 const value=candidate();value.claims.push({id:'second',text:'另一个有验收要求',category:'REQUIREMENT',origin:'MODEL_SUGGESTION',source_anchors:[],acceptance:['检查结果']},{id:'empty',text:'尚无验收',category:'REQUIREMENT',origin:'SOURCE_FACT',source_anchors:[],acceptance:['  ']},{id:'context',text:'显式选择保留的背景',category:'CONSTRAINT',origin:'USER_STATEMENT',source_anchors:[],acceptance:[]});
 const initial=session(value);initial.payload.accepted_claim_ids=['context'];const before=JSON.stringify(initial),view=harness(t,initial);await tick();
 const group=()=>view.find('input',node=>node.props['aria-label']===view.language.t('选择全部有验收条件的要求'));
 assert.ok(group(),'current requirement group offers a native checkbox');assert.equal(group().props.checked,false);
 group().props.onChange({currentTarget:{checked:true}});await tick();assert.equal(view.calls.length,0,'selection is not acceptance or model dispatch');
 assert.equal(group().props.checked,true);assert.equal(group().indeterminate,false);
 const row=view.find('article',node=>node.props['data-claim-id']==='second'),one=view.findIn(row,'input',node=>node.props.type==='checkbox');one.props.onChange({currentTarget:{checked:false}});await tick();assert.equal(group().indeterminate,true);assert.equal(group().props['aria-checked'],'mixed');
 group().props.onChange({currentTarget:{checked:true}});await tick();click(view.button('确认选中的要求与图'));await tick();
 assert.deepEqual(view.calls.map(call=>[call.id,call.input.claims,call.input.graphs]),[['discovery.confirm',['context','requirement','second'],[]]]);assert.equal(JSON.stringify(initial),before);
});

test('group unselection preserves unrelated explicit choices and no bulk control is shown for a confirmed version',async t=>{
 const value=candidate();value.claims.push({...value.claims[0],id:'second'});const initial=session(value);initial.payload.accepted_claim_ids=['requirement','second','explicit_other'];const view=harness(t,initial);await tick();
 const group=view.find('input',node=>node.props['aria-label']===view.language.t('选择全部有验收条件的要求'));assert.ok(group);group.props.onChange({currentTarget:{checked:false}});await tick();assert.equal(view.calls.length,0);assert.equal(view.button('确认选中的要求与图').props.disabled,true);
 initial.payload.status='CONFIRMED';view.setCurrent({...initial});await tick();assert.equal(view.find('input',node=>node.props['aria-label']===view.language.t('选择全部有验收条件的要求')),undefined);
});


test('Source27 formed requirements lead the review; original model guidance stays available on demand with its exact dispatch',async t=>{
 const initial=session(),before=JSON.stringify(initial),view=harness(t,initial);await tick();
 assert.doesNotMatch(view.visible(),/明确下一步/,'formed requirements do not start with a stack of recommendations');
 assert.match(view.visible(),/当前问题与要求/);const advice=view.find('details',node=>node.props.class==='as-discovery-guidance');assert.ok(advice);
 assert.equal(view.calls.length,0);open(advice);assert.match(view.visible(),/明确下一步/);click(view.button('沿此方向继续'));await tick();
 assert.equal(view.calls.length,1);assert.equal(view.calls[0].id,'discovery.advance');assert.deepEqual(view.calls[0].input,{message:'明确下一步'});assert.equal(view.calls[0].target,view.artifact);assert.equal(JSON.stringify(initial),before);
});

test('Source27 confirmation prioritizes one real plan action and retains authored title and phase in readable records',async t=>{
 const initial=session();initial.payload.status='CONFIRMED';initial.payload.candidate.title='原始讨论（待审查）';initial.readiness.can_plan=true;const before=JSON.stringify(initial),view=harness(t,initial);await tick();
 assert.match(view.visible(),/已确认的要求与图/);assert.doesNotMatch(view.visible(),/原始讨论（待审查）|明确问题|明确下一步/);
 const primary=view.find('div',node=>node.props.class==='as-discovery-followup');assert.ok(primary);assert.ok(view.findIn(primary,'button',node=>view.textOf(node).trim()===view.language.t('生成任务计划')));
 assert.equal(view.all().filter(node=>node.type==='button'&&view.textOf(node).trim()===view.language.t('生成任务计划')).length,1);assert.equal(view.button('确认选中的要求与图'),undefined);
 assert.equal(view.calls.length,0);const history=view.find('details',node=>node.props.class==='as-discovery-history');open(history);assert.match(view.visible(),/原始讨论（待审查）/);assert.match(view.visible(),/明确问题/);assert.equal(view.findIn(history,'pre'),undefined);
 click(view.button('生成任务计划'));await tick();assert.deepEqual(view.calls.map(c=>[c.id,c.input]),[['discovery.plan',{experience:undefined,timeoutSeconds:300}]]);assert.equal(JSON.stringify(initial),before);
});

test('Source27 an existing plan has one current-context entry, without generation; stale sources never grant planning',async t=>{
 const initial=session();initial.payload.status='CONFIRMED';initial.readiness.can_plan=true;initial.payload.proposal_ref={id:'original_plan',revision:2,sha256:'b'.repeat(64)};
 const view=harness(t,initial);await tick();assert.equal(view.button('生成任务计划'),undefined);const entries=view.all().filter(n=>n.type==='button'&&view.textOf(n).trim()===view.language.t('查看已有任务计划'));assert.equal(entries.length,1);
 click(entries[0]);await tick();assert.equal(view.calls.length,1);assert.equal(view.calls[0].id,'discovery.open-plan');assert.equal(view.calls[0].target,view.artifact);
 view.setCurrent({...initial,source_current:false,payload:{...initial.payload,proposal_ref:null}});await tick();assert.equal(view.button('生成任务计划').props.disabled,true);assert.match(view.visible(),/来源需要复核/);assert.equal(view.calls.length,1);
});

test('Source27 history presents user text and feedback comments without internal payloads, preserving real user JSON and coverage limits',async t=>{
 const initial=session();initial.payload.source_receipt={documents:[{document_id:'material1',filename:'真实资料 😀.md',revision:2,source:'local-file',raw_sha256:hash},{document_id:'statement1',filename:'用户陈述-internal-uuid.txt',revision:1,source:'discovery-user-statements:internal-uuid'}],coverage:[{document_id:'material1',text_coverage:'SELECTED_CHUNKS',original_modal_coverage:'UNASSESSED',omitted_chunk_indexes:[1,2],warnings:[]}]};
 initial.payload.history=[{kind:'USER_MESSAGE',text:'{"user_config":true}',created_at:'2026-10-23T01:00:00Z'},{kind:'USER_FEEDBACK',text:JSON.stringify({comment:'这句反馈原文应可读',target:{internal_only_marker:'hidden-wire-identifier'},selection:{node_ids:['secret-node']}}),created_at:'2026-10-23T01:01:00Z'},{kind:'FUTURE_INTERNAL_EVENT',text:'unexpected-internal-wire'},{kind:'USER_FEEDBACK',text:'broken_internal_feedback_wire'}];
 const before=JSON.stringify(initial),view=harness(t,initial);await tick();const history=view.find('details',node=>node.props.class==='as-discovery-history');open(history);
 assert.match(view.visible(),/真实资料 😀\.md/);assert.match(view.visible(),/本次讨论的陈述与补充/);assert.match(view.visible(),/原文件版式与图像覆盖尚未核实/);assert.match(view.visible(),/2/);assert.match(view.visible(),/这句反馈原文应可读/);assert.match(view.visible(),/"user_config":true/);
 assert.doesNotMatch(view.visible(),/hidden-wire-identifier|secret-node|unexpected-internal-wire|broken_internal_feedback_wire|用户陈述-internal-uuid/);assert.equal(view.findIn(history,'pre'),undefined);assert.equal(view.calls.length,0);assert.equal(JSON.stringify(initial),before);
 await view.locale('en');assert.match(view.visible(),/Original file layout and image coverage have not been verified/);assert.match(view.visible(),/真实资料 😀\.md/);assert.equal(view.calls.length,0);
});
