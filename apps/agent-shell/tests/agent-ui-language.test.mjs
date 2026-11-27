import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
import {resolve,dirname,extname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';
import * as solid from '../node_modules/solid-js/dist/solid.js';

// UNIT: actual authored components, their real clients and the real locale facade
// run with the same Solid signal runtime. Only the DOM host, storage and external
// service callbacks are isolated. This does not qualify browser/Windows layout.
const app=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const universalSource=readFileSync(resolve(app,'node_modules/solid-js/universal/dist/universal.js'),'utf8')
  .replace("from 'solid-js'",`from '${pathToFileURL(resolve(app,'node_modules/solid-js/dist/solid.js')).href}'`);
const {createRenderer}=await import('data:text/javascript;base64,'+Buffer.from(universalSource).toString('base64'));
const tick=()=>new Promise(done=>setImmediate(done));
const sha='a'.repeat(64),stamp='2026-10-15T08:00:00+00:00';
function memory(){const map=new Map();return {getItem:key=>map.get(key)??null,setItem:(key,value)=>map.set(key,String(value)),removeItem:key=>map.delete(key),map};}
function harness(t){
  const storage=memory(),saved={};
  for(const key of ['localStorage','window','navigator','eeasDesktop'])saved[key]=Object.getOwnPropertyDescriptor(globalThis,key);
  Object.defineProperty(globalThis,'localStorage',{configurable:true,value:storage});
  Object.defineProperty(globalThis,'window',{configurable:true,value:{addEventListener(){},removeEventListener(){}}});
  Object.defineProperty(globalThis,'navigator',{configurable:true,value:{language:'zh-CN'}});
  delete globalThis.eeasDesktop;
  const host={createElement:type=>({type,props:{},children:[],parent:null}),createTextNode:value=>({type:'#text',value,children:[],parent:null}),
    isTextNode:node=>node.type==='#text',replaceText:(node,value)=>{node.value=value;},
    insertNode(parent,node,anchor){if(node.parent){const old=node.parent.children;old.splice(old.indexOf(node),1);}node.parent=parent;const index=anchor?parent.children.indexOf(anchor):-1;parent.children.splice(index<0?parent.children.length:index,0,node);},
    removeNode(parent,node){const index=parent.children.indexOf(node);if(index>=0)parent.children.splice(index,1);node.parent=null;},
    setProperty:(node,name,value)=>{node.props[name]=value;},getParentNode:node=>node.parent,getFirstChild:node=>node.children[0],getNextSibling:node=>node.parent?.children[node.parent.children.indexOf(node)+1]};
  const renderer=createRenderer(host),modules=new Map();
  // The component's real StudioPopover wrapper runs too. Kobalte is a DOM-only
  // library: this universal-host unit boundary supplies just its controlled
  // open/children contract. Position, dismissal and focus require browser tests.
  const popoverContext=solid.createContext();
  const Popover=props=>solid.createComponent(popoverContext.Provider,{value:props,get children(){return props.children;}});
  for(const name of ['Anchor','Portal','Title'])Popover[name]=props=>props.children;
  Popover.Content=props=>{const state=solid.useContext(popoverContext);return solid.createComponent(solid.Show,{get when(){return state.open;},get children(){return props.children;}});};
  function load(file){
    file=resolve(file);if(!extname(file)){const found=['.ts','.tsx','.json'].map(ext=>file+ext).find(existsSync);assert.ok(found,'Actual local module '+file);file=found;}
    if(modules.has(file))return modules.get(file).exports;
    if(file.endsWith('.json'))return JSON.parse(readFileSync(file,'utf8'));
    const module={exports:{}};modules.set(file,module);
    let source=readFileSync(file,'utf8');
    if(file.endsWith('.tsx')){
      source=ts.transpileModule(source,{fileName:file,compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;
      source=babel.transformSync(source,{filename:file+'.jsx',presets:[[solidPreset,{generate:'universal',moduleName:'test-renderer'}]],configFile:false,babelrc:false}).code;
    }
    const code=ts.transpileModule(source,{fileName:file.endsWith('.tsx')?file+'.js':file,compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText;
    const require=name=>{
      if(name==='solid-js')return solid;if(name==='test-renderer')return renderer;
      if(name==='@kobalte/core/popover')return {Popover};
      if(name.endsWith('.css'))return {};
      if(name==='@opencode-ai/ui/icon')return {Icon:()=>null};
      if(name.startsWith('.'))return load(resolve(dirname(file),name));
      throw new Error('Unconfigured test dependency '+name);
    };
    new Function('require','module','exports',code)(require,module,module.exports);return module.exports;
  }
  const language=load(resolve(app,'src/ui-language.ts')),stop=language.startUILanguage(),disposes=[];
  t.after(()=>{for(const dispose of disposes.reverse())dispose();stop();for(const[key,value]of Object.entries(saved)){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}});
  function mount(name,props){
    const Panel=load(resolve(app,'src',name+'.tsx')).default,container=host.createElement('root');
    disposes.push(renderer.render(()=>Panel(props),container));
    const nodes=()=>{const values=[];const walk=node=>{values.push(node);node.children.forEach(walk);};walk(container);return values;};
    return {nodes,text:()=>nodes().filter(node=>node.type==='#text').map(node=>String(node.value)).join(' '),
      find:(type,filter=()=>true)=>nodes().find(node=>node.type===type&&filter(node)),
      field:label=>nodes().find(node=>['input','textarea','select'].includes(node.type)&&node.props['aria-label']===label)};
  }
  return {mount,language,storage,load,async locale(value){await language.setUILanguagePreference(value);await tick();}};
}
function input(node,value){assert.ok(node);node.props.onInput({currentTarget:{value}});}
function click(node){assert.ok(node);node.props.onClick?.({preventDefault(){}});}

test('exact authored catalog preserves placeholders and never translates arbitrary document strings',async t=>{
  const f=harness(t),catalog=JSON.parse(readFileSync(resolve(app,'../../packages/i18n/renderer-agent.json'),'utf8'));
  assert.ok(Object.keys(catalog.messages).length>1000);
  for(const[key,row]of Object.entries(catalog.messages)){
    assert.ok(row['zh-CN'].trim(),key);assert.ok(row.en.trim(),key);assert.doesNotMatch(row.en,/[\u3400-\u9fff]/u,key);
    const markers=value=>[...value.matchAll(/\{([A-Za-z][A-Za-z0-9_]*)\}/g)].map(item=>item[1]).sort();
    assert.deepEqual(markers(row.en),markers(key),key);assert.deepEqual(markers(row['zh-CN']),markers(key),key);
  }
  await f.locale('en');
  const body='正文里的保存设置、引用与用户模板不翻译。';
  assert.equal(f.language.t(body),body);
  assert.equal(catalog.messages['原 OpenCode 会话控制']['zh-CN'],'会话控制');
  assert.equal(catalog.messages['原 OpenCode 会话控制'].en,'Session controls');
  assert.equal(f.language.t('思考档位 {value0}',{value0:'high / 中文模型'}),'Reasoning level high / 中文模型');
  assert.equal(f.language.t('CURRENT'),'CURRENT');
});

test('actual API form switches labels twice without recreating fields, changing credentials or sending a request',async t=>{
  const f=harness(t),calls=[];
  const view=f.mount('ProviderConnectionForm',{storage:f.storage,onSave:async body=>{calls.push(structuredClone(body));throw new Error('fixture transport unavailable');}});
  await tick();const id=view.field('连接标识'),url=view.field('API 地址'),model=view.field('模型标识'),key=view.field('本次 API 密钥');
  input(id,'team_model');input(url,'https://gateway.example/v1');input(model,'original-model-v1');input(key,'fixture-only-private-value');
  const advanced=view.find('details');advanced.props.onToggle({currentTarget:{open:true}});
  await f.locale('en');assert.equal(view.field('Connection ID'),id);assert.equal(view.field('API URL'),url);
  assert.equal(url.props.value,'https://gateway.example/v1');assert.equal(model.props.value,'original-model-v1');assert.equal(key.props.value,'fixture-only-private-value');
  assert.equal(advanced.props.open,true);assert.equal(calls.length,0);assert.match(view.text(),/Save connection/);
  await f.locale('zh-CN');assert.equal(view.field('本次 API 密钥'),key);assert.equal(key.props.value,'fixture-only-private-value');
  view.find('form').props.onSubmit({preventDefault(){}});await tick();await tick();
  assert.equal(calls.length,1);assert.equal(calls[0].model_id,'original-model-v1');assert.equal(calls[0].api_key,'fixture-only-private-value');
  assert.equal(calls[0].provider_url,'https://gateway.example/v1');assert.equal(key.props.value,'fixture-only-private-value');
  assert.equal([...f.storage.map.values()].some(value=>value.includes('fixture-only-private-value')),false,'locale/history storage must not persist credentials');
});

test('actual native annotation preserves comment and original source across locale and current-page changes',async t=>{
  const f=harness(t),calls=[],captures=[],basis={operation_id:'context_one',current:true,observation:{status:'OBSERVED',context_ref:{id:'context',revision:1,sha256:sha},window:{title:'保存设置 — 用户原生文稿'},coverage:{metadata:true}}};
  const [snapshot,setSnapshot]=solid.createSignal(basis);
  const view=f.mount('NativeContextPanel',{get snapshot(){return snapshot();},available:true,pending:false,onCapture:value=>captures.push(value),onFeedback:async(...args)=>calls.push(args)});
  click(view.find('button',node=>node.props['aria-label']==='展开当前对象与批注'));
  const textarea=view.field('当前对象批注');input(textarea,'保留原句：The gain is 17%，只改这里。');
  await f.locale('en');assert.equal(view.field(f.language.t('当前对象批注')),textarea);assert.equal(textarea.props.value,'保留原句：The gain is 17%，只改这里。');
  assert.match(view.text(),/保存设置 — 用户原生文稿/);assert.equal(calls.length,0);assert.equal(captures.length,0);
  setSnapshot({...basis,observation:{...basis.observation,context_ref:{...basis.observation.context_ref,revision:2,sha256:'b'.repeat(64)}}});await tick();
  const send=view.find('button',node=>node.children.some(child=>child.type==='#text'&&String(child.value).includes(f.language.t('直接发送反馈'))));
  assert.ok(send?.props.disabled,'old exact basis must still prohibit direct send');
  const draft=view.find('button',node=>node.children.some(child=>child.type==='#text'&&String(child.value).includes(f.language.t('加入草稿'))));
  click(draft);await tick();assert.equal(calls.length,1);assert.equal(calls[0][0],'保留原句：The gain is 17%，只改这里。');assert.equal(calls[0][2],'DRAFT');assert.equal(calls[0][3],basis);
  assert.equal(captures.length,0);await f.locale('zh-CN');assert.equal(view.field('当前对象批注'),textarea);
});

function supervisorSnapshot(){return {settings:{enabled:true,notifications_enabled:false,interval_seconds:30,revision:1,sha256:sha},observer:{status:'READY',last_attempt_at:stamp,last_success_at:stamp,actual_error_code:null,running:true},tasks:[{key:{kind:'original_session',project_id:'project_one',object_id:'session_one',operation_id:'operation_one',generation:7},label:'保存设置',source_status:'RUNNING',observed_at:stamp,actual_error_code:null,execution_returned:null,validation_status:'NOT_RUN',wait_reason:null,coverage:'PARTIAL',fresh_for_seconds:120,classification:'RUNNING',observation_status:'CURRENT',last_checked_at:stamp}],alerts:[],alerts_total:0,coverage:'ONLY_COLLECTED_EXISTING_TASKS'};}
test('actual unavailable-context panel translates safe summaries while retaining unknown codes and saved comment in diagnostics',async t=>{
  const f=harness(t),original={operation_id:'unavailable-context',current:false,observation:{status:'NEEDS_CLARIFICATION',reason:'WINDOW_NOT_OBSERVABLE',questions:[{code:'WINDOW_NOT_OBSERVABLE',text:'Original observation question'}]}};
  const [snapshot,setSnapshot]=solid.createSignal(original),before=JSON.stringify(original);
  const view=f.mount('NativeContextPanel',{get snapshot(){return snapshot();},available:false,pending:false,onCapture:()=>assert.fail('presentation must not capture'),onFeedback:async()=>assert.fail('presentation must not send')});
  assert.equal(view.find('strong'),undefined,'closed on-demand content is not mounted');
  click(view.find('button',node=>node.props['aria-label']==='展开当前对象与批注'));
  const headingText=()=>{const text=node=>node.type==='#text'?String(node.value):node.children.map(text).join('');return text(view.find('strong'));};assert.equal(headingText(),'请打开要查看的页面');
  const comment=view.field('当前对象批注');input(comment,'人工草稿，保持不变 😀');
  await f.locale('en');assert.equal(headingText(),'Open the page you want to view');assert.equal(view.field(f.language.t('当前对象批注')),comment);
  assert.equal(comment.props.value,'人工草稿，保持不变 😀');assert.match(view.text(),/WINDOW_NOT_OBSERVABLE/);assert.equal(JSON.stringify(original),before);
  setSnapshot({...original,observation:{...original.observation,reason:'FUTURE_CONTEXT_CODE'}});await tick();
  assert.equal(headingText(),'Current object not available yet');assert.match(view.text(),/FUTURE_CONTEXT_CODE/);
  await f.locale('zh-CN');assert.equal(headingText(),'当前对象尚未取得');assert.equal(comment.props.value,'人工草稿，保持不变 😀');
  setSnapshot({...original,observation:{...original.observation,window:{title:'WINDOW_NOT_OBSERVABLE — 用户原始标题'}}});await tick();
  assert.equal(headingText(),'WINDOW_NOT_OBSERVABLE — 用户原始标题');
});

test('actual supervisor translates fixed status while preserving task names, edited interval and exact open callback',async t=>{
  const f=harness(t),source=supervisorSnapshot(),calls=[],opened=[];
  const {GlobalSupervisorClient}=f.load(resolve(app,'src/global-supervisor.ts'));
  const client=new GlobalSupervisorClient({request:async(path,method='GET',body)=>{calls.push({path,method,body});if(method==='GET')return source;throw new Error('fixture response unavailable');}});
  const view=f.mount('GlobalSupervisorPanel',{active:true,managed:true,snapshot:source,client,onOpenTask:value=>opened.push(value)});
  await tick();const interval=view.field('检查间隔（秒）');input(interval,'47');
  await f.locale('en');assert.equal(view.field(f.language.t('检查间隔（秒）')),interval);assert.equal(interval.props.value,'47');assert.match(view.text(),/Running/);
  assert.match(view.text(),/保存设置/,'user task label must remain verbatim even when it equals a catalog msgid');assert.equal(calls.length,0,'managed locale change must not poll or write');
  click(view.find('button',node=>node.props.class==='as-supervisor-task'));await tick();assert.deepEqual(opened,[source.tasks[0]]);
  view.find('form').props.onSubmit({preventDefault(){}});await tick();await tick();
  assert.equal(calls.filter(value=>value.method==='POST').length,1);assert.equal(calls.find(value=>value.method==='POST').body.interval_seconds,47);assert.equal(interval.props.value,'47');
  await f.locale('zh-CN');assert.equal(view.field('检查间隔（秒）'),interval);assert.match(view.text(),/设置结果待核对/);assert.equal(calls.filter(value=>value.method==='POST').length,1);
});

test('actual task-first history and true creation dates translate without changing labels, DOM or acknowledgement state',async t=>{
  const f=harness(t),base=supervisorSnapshot(),returned={...base.tasks[0],label:'运费边界修复',classification:'EXECUTION_RETURNED',display_context:{project_label:'Windows 工程验证',task_created_at:'2026-10-27T05:25:00+08:00'}};
  const other={...returned,key:{...returned.key,operation_id:'other_operation'},display_context:{project_label:'Windows 工程验证',task_created_at:'2026-10-27T02:25:00+08:00'}};
  const alert={id:'phase',revision:1,sha256:sha,alert_ref:{id:'phase',revision:1,sha256:sha},kind:'TASK_STATE_CHANGED',key:returned.key,label:returned.label,category:'RUNNING',source_status:'RUNNING',actual_error_code:null,created_at:stamp,terminal:false,acknowledged:false,acknowledged_at:null};
  const calls=[],{GlobalSupervisorClient}=f.load(resolve(app,'src/global-supervisor.ts'));
  const view=f.mount('GlobalSupervisorPanel',{active:true,managed:true,snapshot:{...base,tasks:[returned,other],alerts:[alert],alerts_total:1},client:new GlobalSupervisorClient({request:async(...args)=>{calls.push(args);throw new Error('must not request');}}),onOpenTask(){}});
  await tick();const row=view.find('button',node=>node.props.class==='as-supervisor-task'),history=view.find('details',node=>node.props.class==='as-supervisor-history');
  assert.match(view.text(),/任务创建于/);assert.equal(history.props.open,undefined);
  await f.locale('en');assert.match(view.text(),/Returned \/ ended/);assert.match(view.text(),/Task created/);assert.match(view.text(),/Loaded update history/);
  assert.equal(view.find('button',node=>node.props.class==='as-supervisor-task'),row);assert.equal(view.find('details',node=>node.props.class==='as-supervisor-history'),history);
  assert.match(view.text(),/运费边界修复/);assert.match(view.text(),/Windows 工程验证/);assert.equal(calls.length,0);
  await f.locale('zh-CN');assert.match(view.text(),/已载入的变化记录/);assert.equal(calls.length,0);
});

test('optional translator changes guidance and capability presentation without altering original raw helpers',async t=>{
  const f=harness(t),cap=f.load(resolve(app,'src/native-capabilities.ts')),supervisor=f.load(resolve(app,'src/global-supervisor.ts'));
  const task=supervisorSnapshot().tasks[0];assert.equal(supervisor.supervisorTaskStatus(task),'运行中');
  await f.locale('en');assert.equal(supervisor.supervisorTaskStatus(task,f.language.t),'Running');assert.equal(task.source_status,'RUNNING');
  const item={enabled:true,integrity:'CURRENT',loaded_in_original_session:false,invoked:false,qualified:false};
  const original=cap.capabilityStatus(item);assert.match(original,/声明已启用/);
  assert.match(cap.capabilityStatus(item,f.language.t),/Declared enabled/);assert.equal(cap.capabilityStatus(item),original);
});

// Dialog, file input, focus and draft persistence now run in the real browser:
// tests/test_native_capabilities_panel_browser.py (actual Solid/Kobalte, external host fixture).
test('actual capability client preserves Skill source bytes and the saved receipt across locale changes',async t=>{
  const f=harness(t),writes=[],cap=f.load(resolve(app,'src/native-capabilities.ts'));
  const content='---\r\nname: draft-review\r\ndescription: 用户的原始写法\r\n---\r\nDo not translate this source. 保存设置。';
  const client=new cap.AgentCapabilityClient({importAgentSkill:async body=>{writes.push(structuredClone(body));return {config_ref:{id:body.id,revision:1,sha256:sha},saved:true,loaded_in_original_session:false,qualified:false};}});
  const receipt=await client.importSkill('draft-review',content,false,undefined,'fixture_import');
  assert.equal(writes.length,1);assert.equal(writes[0].content,content);assert.equal(receipt.config_ref.revision,1);
  await f.locale('en');assert.equal(f.language.t('配置已保存'),'Configuration saved');assert.equal(writes[0].content,content);
  await f.locale('zh-CN');assert.equal(f.language.t('配置已保存'),'配置已保存');assert.equal(writes.length,1);assert.equal(receipt.qualified,false);
});

test('actual App parameterized notice and focus callback stay reactive without translating data or relying on translated labels',async t=>{
  const f=harness(t),source=readFileSync(resolve(app,'src/App.tsx'),'utf8'),parsed=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
  let copy,focus;function visit(node){if(ts.isVariableDeclaration(node)&&node.name.getText(parsed)==='uiCopyText')copy=node;if(ts.isFunctionDeclaration(node)&&node.name?.text==='focusComposer')focus=node;ts.forEachChild(node,visit);}visit(parsed);assert.ok(copy&&focus);
  const code=ts.transpileModule('const '+copy.getText(parsed)+';\n'+focus.getText(parsed)+'\nreturn {uiCopyText,focusComposer};',{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
  const calls=[],textarea={focus:options=>calls.push(['focus',options])},shell={querySelector:selector=>{calls.push(['query',selector]);return selector==='.as-composer textarea'?textarea:undefined;}};
  const actual=new Function('uiText','authoredText','closeComposerMenu','composerPresentation','requestAnimationFrame','shell',code)(f.language.t,f.load(resolve(app,'src/authored-ui-copy.ts')).authoredText,()=>calls.push(['close']),{compose:()=>calls.push(['compose'])},callback=>callback(),shell);
  const notice={message_key:'已保存任务书版本 {revision}',params:{revision:17}},rendered=[];
  const dispose=solid.createRoot(dispose=>{solid.createRenderEffect(()=>rendered.push(actual.uiCopyText(notice)));return dispose;});t.after(dispose);
  assert.equal(rendered.at(-1),'已保存任务书版本 17');await f.locale('en');assert.equal(rendered.at(-1),'Task brief revision 17 saved');
  actual.focusComposer();assert.deepEqual(calls,[['close'],['compose'],['query','.as-composer textarea'],['focus',{preventScroll:true}]]);
  await f.locale('zh-CN');assert.equal(rendered.at(-1),'已保存任务书版本 17');assert.equal(notice.params.revision,17);
});

test('actual dynamic header validation translates both ways while preserving raw errors and making no save call',async t=>{
  const f=harness(t),calls=[],view=f.mount('ProviderConnectionForm',{storage:f.storage,onSave:async body=>calls.push(body)});
  await tick();input(view.field('连接标识'),'header_validation');input(view.field('API 地址'),'https://gateway.example/v1');input(view.field('模型标识'),'exact-model');input(view.field('本次 API 密钥'),'fixture-private');
  click(view.find('button',node=>node.children.some(child=>child.type==='#text'&&child.value==='添加请求头')));
  input(view.field('请求头名称 1'),'Host');input(view.field('请求头值 1'),'fixture-value');await f.locale('en');
  view.find('form').props.onSubmit({preventDefault(){}});await tick();
  assert.match(view.text(),/Header host is managed by the connection service/);assert.equal(calls.length,0);
  await f.locale('zh-CN');assert.match(view.text(),/请求头 host 由连接服务管理，不能自定义/);assert.equal(calls.length,0);
  const connection=f.load(resolve(app,'src/provider-connection.ts')),draft=connection.providerConnectionDraft();
  Object.assign(draft,{id:'raw_check',url:'https://gateway.example/v1',model:'exact-model',auth:'NONE',headers:[{id:'h',name:'Host',mode:'SECRET',value:'fixture'}]});
  assert.throws(()=>connection.providerConnectionBody(draft),{message:'请求头 host 由连接服务管理，不能自定义'});
});

test('unknown provider receipt carries structured authored context without rewriting diagnostics or resending',async t=>{
  const f=harness(t),connection=f.load(resolve(app,'src/provider-connection.ts')),copy=f.load(resolve(app,'src/authored-ui-copy.ts')),calls=[];
  const client=new connection.ProviderConnectionClient({save:async()=>assert.fail('read recovery cannot save'),inspect:async id=>{calls.push(id);throw new Error('fixture unavailable');}},f.storage);
  const operation={operationId:'original_save',profileId:'saved_profile',expectedRevision:0,providerUrl:'https://gateway.example/v1',modelId:'exact-model',authMode:'NONE',supportsImages:false};
  const view=await client.inspect(operation);assert.equal(view.status,'INSPECTION_UNAVAILABLE');assert.deepEqual(calls,['original_save']);
  const raw=view.message;assert.match(raw,/原保存结果尚未核对；fixture unavailable/);await f.locale('en');
  assert.equal(copy.authoredText(view.message_copy,f.language.t),'The original save result is unconfirmed; fixture unavailable. No save was resent.');
  await f.locale('zh-CN');assert.equal(copy.authoredText(view.message_copy,f.language.t),raw);assert.equal(view.message,raw);assert.deepEqual(calls,['original_save']);
});

test('actual Quick Inbox reception failure translates in place without acknowledging, sending or polling again',async t=>{
  const f=harness(t),calls=[],{QuickInbox}=f.load(resolve(app,'src/quick-inbox.ts'));
  const bridge={getQuickDrafts:async()=>{calls.push('GET');throw new Error('fixture unavailable');},ackQuickDraft:async()=>assert.fail('failed reception cannot acknowledge'),submitQuickDraft:async()=>assert.fail('locale cannot send')};
  const inbox=new QuickInbox(f.storage,bridge,()=>{});await inbox.refresh();const raw=inbox.error;
  const view=f.mount('QuickInboxPanel',{records:[],scope:{},bridge,get error(){return inbox.error_copy??inbox.error;},onRefresh:()=>assert.fail('locale cannot refresh'),onSent:()=>assert.fail('locale cannot dispatch')});
  assert.match(view.text(),/快捷反馈尚未完成本机接收/);await f.locale('en');assert.match(view.text(),/Quick feedback has not been fully received locally/);assert.match(view.text(),/fixture unavailable/);
  await f.locale('zh-CN');assert.match(view.text(),/快捷反馈尚未完成本机接收/);assert.equal(inbox.error,raw);assert.deepEqual(calls,['GET']);
});
