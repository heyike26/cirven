import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {resolve,dirname} from 'node:path';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';
import * as solid from '../node_modules/solid-js/dist/solid.js';
const app=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const source=async file=>readFile(resolve(app,'src',file),'utf8');
const moduleURL=code=>'data:text/javascript;base64,'+Buffer.from(code).toString('base64');
const compile=code=>ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;
const copyURL=moduleURL(compile(await source('supervisor-command-center-copy.ts'))),copy=await import(copyURL);
const model=await import(moduleURL(compile(await source('supervisor-command-center.ts')).replace("'./supervisor-command-center-copy.ts'",JSON.stringify(copyURL))));
const now=Date.parse('2026-10-19T08:00:00Z'),stamp=new Date(now).toISOString(),sha='a'.repeat(64);
const task=(id,extra={})=>({key:{kind:'original_session',project_id:'project_one',object_id:id,operation_id:'message_'+id,generation:7},
  label:'任务 '+id,source_status:'RUNNING',observed_at:stamp,actual_error_code:null,execution_returned:null,validation_status:'NOT_RUN',wait_reason:null,
  coverage:'PARTIAL',source_ref:{id:'ref_'+id,revision:1,sha256:sha},fresh_for_seconds:120,classification:'RUNNING',observation_status:'CURRENT',last_checked_at:stamp,...extra});
const snapshot=(tasks=[],extra={})=>({settings:{enabled:true,notifications_enabled:false,interval_seconds:30,revision:1,sha256:sha},
  observer:{status:'READY',last_attempt_at:stamp,last_success_at:stamp,actual_error_code:null,running:true},tasks,alerts:[],alerts_total:0,coverage:'ONLY_COLLECTED_EXISTING_TASKS',...extra});
const project=(value,locale='zh-CN',read='READY',clock=now)=>model.projectSupervisorCenter(value,locale,read,clock);

test('projection prioritizes actual failure, retains exact task keys and separates execution return from validation',()=>{
  const values=[task('running'),task('returned',{classification:'EXECUTION_RETURNED',source_status:'RETURNED'}),task('waiting',{classification:'WAITING_USER',wait_reason:'USER_INPUT'}),task('failed',{classification:'EXECUTION_ERROR',source_status:'FAILED'})];
  const value=project(snapshot(values));assert.deepEqual(value.tasks.map(item=>item.task.key.object_id),['failed','waiting','running','returned']);
  assert.deepEqual(value.counts,{running:1,attention:2,recent:1});assert.deepEqual(value.tasks[0].task.key,values[3].key);
  assert.doesNotMatch(value.tasks[3].status,/验收通过|验证通过/);
  const reordered={operation_id:'message_failed',generation:7,object_id:'failed',kind:'original_session',project_id:'project_one'};
  assert.equal(model.supervisorTaskIdentity(reordered),model.supervisorTaskIdentity(values[3].key));
  assert.notEqual(model.supervisorTaskIdentity({...reordered,generation:8}),model.supervisorTaskIdentity(values[3].key));
});

test('age, unavailable observations, disabled settings and duplicate identities cannot imply current running success',()=>{
  const old=task('old',{observed_at:new Date(now-121000).toISOString()});
  const value=project(snapshot([old]));assert.equal(value.tasks[0].tone,'attention');assert.match(value.tasks[0].status,/过期/);assert.equal(value.counts.running,0);
  const disabled=project(snapshot([task('running')],{settings:{...snapshot().settings,enabled:false}}));assert.equal(disabled.cached,true);
  assert.match(model.answerSupervisorStatusQuery('任务进度',disabled,'zh-CN').text,/上次观察/);
  const outage=project(snapshot([task('one')]),'en','OBSERVATION_UNAVAILABLE');assert.equal(outage.cached,true);assert.equal(outage.items[0].label,'Task status is unavailable');
  const duplicate=project(snapshot([task('same'),task('same',{classification:'EXECUTION_ERROR'})]));assert.equal(duplicate.tasks.length,0);assert.match(duplicate.items[0].label,/待核对/);
  assert.equal(project(undefined).items[0].label,'等待首次观察');
});

test('finite local questions return actual facts without treating ordinary instructions as status or cancelled as returned',()=>{
  const value=project(snapshot([task('run'),task('returned',{classification:'EXECUTION_RETURNED'}),task('cancelled',{classification:'CANCELLED'})]));
  const returned=model.answerSupervisorStatusQuery('哪些任务已返回？',value,'zh-CN');assert.deepEqual(returned.tasks.map(item=>item.key.object_id),['returned']);
  assert.equal(returned.kind,'LOCAL_STATUS');assert.equal(returned.observedAt,stamp);
  const running=model.answerSupervisorStatusQuery('show running tasks',value,'en');assert.equal(running.tasks.length,1);
  for(const query of ['停止全部任务','按这个文件的 status 字段修改代码','解释一下为什么任务慢','send my progress to someone',''])assert.equal(model.answerSupervisorStatusQuery(query,value,'en'),undefined);
});

test('ticker preserves identity through reorder, pauses for every input/visibility reason, then admits urgent real state',()=>{
  const ticker=new model.SupervisorTicker(),one=project(snapshot([task('one'),task('two')])).items;
  ticker.update(one);const first=ticker.current.id;ticker.update([...one].reverse());assert.equal(ticker.current.id,first);
  for(const reason of ['hover','focus','composition','draft','expanded','hidden','manual']){ticker.pause(reason,true);ticker.advance();assert.equal(ticker.current.id,first);ticker.pause(reason,false);}
  ticker.pause('draft',true);const urgent=project(snapshot([task('one'),task('two'),task('failure',{classification:'EXECUTION_ERROR'})])).items;
  ticker.update(urgent);assert.equal(ticker.current.id,first);ticker.pause('draft',false);assert.equal(ticker.current.task.key.object_id,'failure');
  ticker.advance();assert.notEqual(ticker.current.task.key.object_id,'failure');
});

test('bilingual messages retain dynamic task names and do not add competing product brands',()=>{
  assert.equal(copy.centerText('en','summary',{running:2,attention:1,recent:3}),'2 running · 1 need attention · 3 recently ended');
  assert.match(project(snapshot([task('original',{label:'跨目录 αβ 项目/report.docx'})]),'en').tasks[0].label,/跨目录 αβ 项目/);
  assert.equal(copy.centerText('zh-CN','open',{name:'a{b}.md'}),'查看任务：a{b}.md');
});

test('closed summary keeps quiet terminal history out of the headline without changing its facts',()=>{
  const source=snapshot([task('cancelled',{classification:'CANCELLED',label:'旧验收已取消 — 不属于当前讨论'}),task('returned',{classification:'EXECUTION_RETURNED',label:'保留原结果'})]);
  const original=JSON.stringify(source),value=project(source),summary=model.summarizeSupervisorCenter(value,'zh-CN');
  assert.deepEqual(summary,{label:'暂无进行中的任务',status:'',tone:'quiet'});
  assert.deepEqual(value.counts,{running:0,attention:0,recent:2});
  assert.deepEqual(value.tasks.map(item=>item.task.label),['旧验收已取消 — 不属于当前讨论','保留原结果']);
  assert.equal(JSON.stringify(source),original);
});

test('closed summary aggregates all scopes and never hides permissions, failures, uncertainty or cancellation in progress',()=>{
  const source=snapshot([task('run'),task('cancelling',{classification:'CANCEL_REQUESTED'}),
    task('permission',{classification:'WAITING_USER',wait_reason:'PERMISSION',key:{kind:'native',project_id:'other_project',object_id:'permission',operation_id:'other_permission',generation:3}}),
    task('failed',{classification:'EXECUTION_ERROR'}),task('unknown',{classification:'NEEDS_CHECK'}),
    task('validation',{classification:'EXECUTION_RETURNED',validation_status:'FAILED'}),task('ended',{classification:'CANCELLED'})]);
  const value=project(source),before=JSON.stringify(value),summary=model.summarizeSupervisorCenter(value,'zh-CN');
  assert.equal(summary.label,'2 项运行中 · 4 项待处理');assert.equal(summary.tone,'error');
  assert.equal(model.summarizeSupervisorCenter(value,'en').label,'2 Running · 4 Needs attention');
  assert.equal(value.tasks.find(item=>item.task.key.object_id==='permission').status,'等待授权');
  assert.equal(JSON.stringify(value),before);assert.equal(value.tasks.length,7);
});

test('closed summary retains observer limits, cached counts and stale-task attention instead of claiming idle',()=>{
  const scenarios=[
    [snapshot([],{settings:{...snapshot().settings,enabled:false}}),'READY','监督已关闭',true,'quiet'],
    [snapshot([],{observer:{...snapshot().observer,status:'CLOSED'}}),'READY','监督已停止',true,'quiet'],
    [snapshot([]),'OBSERVATION_UNAVAILABLE','任务状态暂不可读',true,'attention'],
    [snapshot([task('run')],{observer:{...snapshot().observer,status:'PARTIAL'}}),'READY','部分状态待核对',false,'attention'],
    [undefined,'INITIAL','等待首次观察',true,'quiet'],
  ];
  for(const [source,read,label,cached,tone] of scenarios){
    const value=project(source,'zh-CN',read),summary=model.summarizeSupervisorCenter(value,'zh-CN');
    assert.equal(summary.label,label);assert.equal(value.cached,cached);assert.equal(summary.tone,tone);
    if(cached)assert.match(summary.status,/上次观察/);
    if(source?.tasks.length)assert.match(summary.status,/1 项运行中/);
    assert.doesNotMatch(summary.label,/暂无进行/);
  }
  const stale=model.summarizeSupervisorCenter(project(snapshot([task('stale',{observed_at:new Date(now-121000).toISOString()})])),'zh-CN');
  assert.equal(stale.label,'1 项待处理');assert.equal(stale.tone,'attention');
});

const universalSource=(await readFile(resolve(app,'node_modules/solid-js/universal/dist/universal.js'),'utf8')).replace("from 'solid-js'",`from '${pathToFileURL(resolve(app,'node_modules/solid-js/dist/solid.js')).href}'`);
const {createRenderer}=await import(moduleURL(universalSource));
const componentSource=await source('SupervisorCommandCenter.tsx');
function harness(t,extra={}){
  const host={createElement:type=>({type,props:{},children:[],parent:null}),createTextNode:value=>({type:'#text',value,children:[],parent:null}),
    isTextNode:node=>node.type==='#text',replaceText:(node,value)=>{node.value=value;},
    insertNode(parent,node,anchor){if(node.parent){const old=node.parent.children;old.splice(old.indexOf(node),1);}node.parent=parent;const index=anchor?parent.children.indexOf(anchor):-1;parent.children.splice(index<0?parent.children.length:index,0,node);},
    removeNode(parent,node){const index=parent.children.indexOf(node);if(index>=0)parent.children.splice(index,1);node.parent=null;},
    setProperty:(node,name,value)=>{node.props[name]=value;},getParentNode:node=>node.parent,getFirstChild:node=>node.children[0],getNextSibling:node=>node.parent?.children[node.parent.children.indexOf(node)+1]};
  const renderer=createRenderer(host),output=babel.transformSync(compile(componentSource),{filename:'SupervisorCommandCenter.jsx',presets:[[solidPreset,{generate:'universal',moduleName:'test-renderer'}]],configFile:false,babelrc:false}).code;
  const parsed=ts.createSourceFile('component.mjs',output,ts.ScriptTarget.Latest,true,ts.ScriptKind.JS);let code=output;const bindings={};
  const popover=props=>solid.createMemo(()=>[props.trigger,props.open?props.children:undefined]);
  for(const node of [...parsed.statements].reverse())if(ts.isImportDeclaration(node)){
    const imported=node.moduleSpecifier.text==='solid-js'?solid:node.moduleSpecifier.text==='test-renderer'?renderer:node.moduleSpecifier.text==='./ui-language'?{uiLocale:()=> 'zh-CN'}:node.moduleSpecifier.text==='./StudioPopover'?{default:popover}:node.moduleSpecifier.text==='./supervisor-command-center'?model:node.moduleSpecifier.text==='./supervisor-command-center-copy'?copy:{};
    if(node.importClause?.name)bindings[node.importClause.name.text]=imported.default;
    if(node.importClause?.namedBindings&&ts.isNamedImports(node.importClause.namedBindings))for(const item of node.importClause.namedBindings.elements)bindings[item.name.text]=imported[item.propertyName?.text??item.name.text];
    code=code.slice(0,node.getStart(parsed))+code.slice(node.end);
  }
  code=code.replace('export default function SupervisorCommandCenter','function SupervisorCommandCenter');
  const timers=[],doc={visibilityState:'visible',addEventListener(){},removeEventListener(){}};
  const Component=new Function(...Object.keys(bindings),'document','setInterval','clearInterval',code+'\nreturn SupervisorCommandCenter;')(...Object.values(bindings),doc,callback=>{timers.push(callback);return timers.length;},()=>{});
  const [state,setState]=solid.createSignal(snapshot([task('live',{observed_at:new Date().toISOString()})]));
  const [draft,setDraft]=solid.createSignal(''),[messages,setMessages]=solid.createSignal([]),[visible,setVisible]=solid.createSignal(true),[selectedOperation,setSelected]=solid.createSignal();const opened=[],locals=[];
  const props={get snapshot(){return state();},get draft(){return draft();},get messages(){return messages();},get visible(){return visible();},onDraftChange:setDraft,
    get selectedOperation(){return selectedOperation();},onDismissSelected:()=>setSelected(undefined),onOpenTask:task=>opened.push(task),onOpenAll:()=>{},onLocalAnswer:(text,answer)=>locals.push({text,answer})};
  Object.defineProperties(props,Object.getOwnPropertyDescriptors(extra));
  const container=host.createElement('root'),dispose=renderer.render(()=>Component(props),container);t.after(dispose);
  const nodes=()=>{const result=[];const walk=node=>{result.push(node);for(const child of node.children)walk(child);};walk(container);return result;};
  const find=(type,filter=()=>true)=>nodes().find(node=>node.type===type&&filter(node));
  return {nodes,find,setState,setDraft,setMessages,setVisible,setSelected,opened,locals,timers,draft,open(){find('button',node=>node.props.class==='as-supervisor-command-trigger').props.onClick();},
    text:()=>nodes().filter(node=>node.type==='#text').map(node=>node.value).join(' ')};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('completed history has no empty status while real history notices retain the original draft and input',async t=>{
  const messages=[{id:'original_question',role:'user',text:'原问题'},{id:'original_answer',role:'assistant',text:'原回答',status:'RETURNED'}];
  const [history,setHistory]=solid.createSignal({messages,hasMore:false,loading:false,windowed:false,changed:false});
  const f=harness(t,{get history(){return history();},onReadEarlier:async()=>{},onResetHistory:()=>{}});f.open();await tick();f.setDraft('尚未发送的原稿');
  const input=f.find('textarea'),hint=()=>f.find('p',node=>node.props.class==='as-supervisor-command-history-hint');
  assert.equal(hint(),undefined,'completed history must not reserve an empty status region');
  for(const update of [{changed:true},{error:'UNAVAILABLE'},{windowed:true}]){
    setHistory({...history(),changed:false,error:undefined,windowed:false,...update});await tick();
    assert.ok(hint());assert.ok(hint().children.some(node=>node.type==='#text'&&node.value.trim()));
    assert.equal(f.find('textarea'),input);assert.equal(f.draft(),'尚未发送的原稿');
  }
  setHistory({...history(),changed:false,error:undefined,windowed:false});await tick();assert.equal(hint(),undefined);
});
test('a verified not-dispatched capacity rejection keeps the input and gives a clear next send without an inspect control',async t=>{
 let inspections=0;const f=harness(t,{onAsk:async()=>({operation_id:'not_claimed',status:'REJECTED',error:'SUPERVISOR_STORAGE_FULL'}),onInspect:async()=>{inspections++;return {operation_id:'not_claimed',status:'UNKNOWN'};}});f.open();await tick();f.setDraft('保留未发送的问题');f.find('form').props.onSubmit({preventDefault(){}});await tick();
 assert.equal(f.draft(),'保留未发送的问题');assert.match(f.text(),/尚未发送，记录空间不足/);assert.equal(f.find('button',node=>node.props.class==='as-supervisor-command-input-inspect'),undefined);assert.equal(inspections,0);
});

test('actual compiled component answers local progress without model calls and retains the existing all-tasks route',async t=>{
  let calls=0,allTasks=0;const f=harness(t,{onOpenAll:()=>{allTasks++;},onAsk:async()=>{calls++;return {operation_id:'op_one',status:'RETURNED',text:'actual reply'};}});f.open();await tick();f.setDraft('任务进度');
  f.find('form').props.onSubmit({preventDefault(){}});await tick();assert.equal(calls,0);assert.equal(f.locals.length,1);assert.match(f.text(),/任务状态/);
  assert.equal(f.find('button',node=>String(node.props['aria-label']).startsWith('查看任务：')),undefined);
  f.find('footer').children.find(node=>node.type==='button').props.onClick();await tick();assert.equal(allTasks,1);assert.equal(f.opened.length,0);assert.equal(f.find('form'),undefined);assert.equal(calls,0);
});

test('persisted visible local question and answer replace only the duplicate text, preserving the task entry and input',async t=>{
  const f=harness(t);f.open();await tick();f.setDraft('任务进度');f.find('form').props.onSubmit({preventDefault(){}});await tick();
  const answer=f.locals[0].answer.text,input=f.find('textarea'),taskButton=f.find('footer').children.find(node=>node.type==='button');
  assert.equal(f.text().split(answer).length-1,1);
  f.setMessages([{id:'local_user',role:'user',text:'任务进度'},{id:'local_reply',role:'assistant',text:answer,status:'RETURNED'}]);await tick();
  assert.equal(f.text().split(answer).length-1,1);assert.equal(f.find('textarea'),input);
  assert.equal(f.find('footer').children.find(node=>node.type==='button'),taskButton);
  f.setSelected({operation_id:'older_model',request_sha256:sha,question:'旧讨论问题',answer_text:'旧讨论答复',status:'RETURNED'});await tick();
  assert.equal(f.text().split(answer).length-1,1);assert.match(f.text(),/旧讨论问题/);
});

test('same text in a different model operation cannot hide a local answer, and history outside the visible window cannot hide it',async t=>{
  const f=harness(t);f.open();await tick();f.setDraft('任务进度');f.find('form').props.onSubmit({preventDefault(){}});await tick();
  const answer=f.locals[0].answer.text;
  f.setMessages([{id:'model_user',role:'user',text:'任务进度',operation_id:'separate_model'},{id:'model_reply',role:'assistant',text:answer,status:'RETURNED',operation_id:'separate_model'}]);await tick();
  assert.equal(f.text().split(answer).length-1,2);
  f.setMessages([{id:'local_user',role:'user',text:'任务进度'},{id:'local_reply',role:'assistant',text:answer,status:'RETURNED'},...Array.from({length:12},(_,index)=>({id:'later_'+index,role:index%2?'assistant':'user',text:'后来讨论 '+index}))]);await tick();
  assert.equal(f.text().split(answer).length-1,1);
});

test('failed local persistence keeps the original question and the readable local answer without a model send',async t=>{
  let modelCalls=0;const f=harness(t,{onLocalAnswer:async()=>{throw Error('disk unavailable');},onAsk:async()=>{modelCalls++;return {status:'RETURNED'};}});
  f.open();await tick();f.setDraft('任务进度');f.find('form').props.onSubmit({preventDefault(){}});await tick();
  assert.equal(modelCalls,0);assert.equal(f.draft(),'任务进度');assert.match(f.text(),/1 项运行中/);assert.match(f.text(),/暂时无法保存总管草稿/);
});

test('actual component IME prevents send and late answers preserve newer user drafts',async t=>{
  let resolveReply,calls=0;const waiting=new Promise(resolve=>resolveReply=resolve);
  const f=harness(t,{onAsk:async()=>{calls++;return waiting;}});f.open();await tick();f.setDraft('请解释这里');const input=f.find('textarea');
  input.props.onCompositionStart();input.props.onKeyDown({key:'Enter',ctrlKey:true,isComposing:true,preventDefault(){}});await tick();assert.equal(calls,0);
  input.props.onCompositionEnd();input.props.onKeyDown({key:'Enter',ctrlKey:true,isComposing:false,preventDefault(){}});await tick();assert.equal(calls,1);
  f.setDraft('我随后补充的文字');resolveReply({status:'RETURNED',operation_id:'original_op',text:'原问题的答复'});await tick();assert.equal(f.draft(),'我随后补充的文字');assert.match(f.text(),/原问题的答复/);
});

test('unknown model operation cannot resend; inspect is read-only and bound to the original operation',async t=>{
  const sent=[],inspected=[];const f=harness(t,{onAsk:async value=>{sent.push(value);return {status:'UNKNOWN',operation_id:'original_unknown'};},
    onInspect:async id=>{inspected.push(id);return {status:'RETURNED',operation_id:id,text:'原操作已读取'};}});f.open();await tick();f.setDraft('请检查这个设计');
  const submit=()=>f.find('form').props.onSubmit({preventDefault(){}});submit();await tick();submit();await tick();assert.equal(sent.length,1);
  assert.equal(sent[0].text,'请检查这个设计');assert.equal(sent[0].taskKeys[0].operation_id,'message_live');assert.ok(sent[0].taskKeys.length<=8);
  f.find('button',node=>node.children.some(child=>child.type==='#text'&&child.value==='核查原操作')).props.onClick();await tick();assert.deepEqual(inspected,['original_unknown']);assert.equal(sent.length,1);assert.match(f.text(),/原操作已读取/);
});

test('message refresh retains the input and task entry while an exact terminal history reply releases unknown fencing',async t=>{
  let sends=0;const f=harness(t,{onAsk:async()=>{sends++;return {status:'UNKNOWN',operation_id:'op_persisted'};}});f.open();await tick();
  const input=f.find('textarea'),original=f.find('footer').children.find(node=>node.type==='button');
  f.setState(snapshot([task('live',{label:'更新任务标题',observed_at:new Date().toISOString()})]));await tick();assert.equal(f.find('footer').children.find(node=>node.type==='button'),original);assert.equal(f.find('textarea'),input);assert.doesNotMatch(f.text(),/更新任务标题/);
  f.setDraft('需要模型分析');f.find('form').props.onSubmit({preventDefault(){}});await tick();assert.equal(sends,1);
  f.setMessages([{id:'answer',role:'assistant',text:'实际保存的答复',operation_id:'other_op',status:'RETURNED'}]);await tick();f.find('form').props.onSubmit({preventDefault(){}});await tick();assert.equal(sends,1);
  f.setMessages([{id:'answer',role:'assistant',text:'实际保存的答复',operation_id:'op_persisted',status:'RETURNED'}]);await tick();assert.equal(f.find('button',node=>node.props.type==='submit').props.disabled,false);
});

test('restored unknown history fences new model sends while local progress remains available',async t=>{
  let calls=0;const f=harness(t,{onAsk:async()=>{calls++;return {operation_id:'new',status:'RETURNED'};}});
  f.setMessages([{id:'persisted',role:'assistant',text:'结果待核对',operation_id:'persisted_op',status:'UNKNOWN'}]);f.open();await tick();
  f.setDraft('另一个推理问题');f.find('form').props.onSubmit({preventDefault(){}});await tick();assert.equal(calls,0);
  f.setDraft('任务进度');assert.equal(f.find('button',node=>node.props.type==='submit').props.disabled,false);f.find('form').props.onSubmit({preventDefault(){}});await tick();
  assert.equal(calls,0);assert.equal(f.locals.length,1);assert.match(f.text(),/结果待核对/);
});

test('exact restored unknown blocks replayed text but permits a different user question and retains old operation history',async t=>{
  let calls=0;const f=harness(t,{onAsk:async()=>{calls++;return {operation_id:'new_explicit',status:'RETURNED',text:'新问题的答复'};}});
  f.setMessages([{id:'original_user',role:'user',text:'原来的问题',operation_id:'original_unknown'},
    {id:'original_answer',role:'assistant',text:'原结果待核对',operation_id:'original_unknown',status:'UNKNOWN'}]);
  f.open();await tick();f.setDraft('原来的问题');f.find('form').props.onSubmit({preventDefault(){}});await tick();assert.equal(calls,0);
  f.setDraft('不同的新问题');f.find('form').props.onSubmit({preventDefault(){}});await tick();assert.equal(calls,1);
  assert.match(f.text(),/原结果待核对/);assert.match(f.text(),/新问题的答复/);
});

test('controlled admission fences Map-only unknown before both submit paths and exposes exact original inspection',async t=>{
  const sent=[],inspected=[];const f=harness(t,{askAdmission:input=>input==='原来的问题'?{allowed:false,code:'SUPERVISOR_UNKNOWN_INPUT_REPLAY_BLOCKED',operation_id:'memory_only'}:{allowed:true},
    onAsk:async value=>{sent.push(value);return {operation_id:'new_explicit',status:'RETURNED',text:'不同问题答复'};},onInspect:async id=>{inspected.push(id);return {operation_id:id,status:'UNKNOWN'};}});
  f.open();await tick();f.setDraft('原来的问题');const input=f.find('textarea');assert.equal(f.find('button',node=>node.props.type==='submit').props.disabled,true);
  f.find('form').props.onSubmit({preventDefault(){}});input.props.onKeyDown({key:'Enter',ctrlKey:true,isComposing:false,preventDefault(){}});await tick();assert.equal(sent.length,0);assert.equal(f.draft(),'原来的问题');
  f.find('button',node=>node.props.class==='as-supervisor-command-input-inspect').props.onClick();await tick();assert.deepEqual(inspected,['memory_only']);
  f.setDraft('不同的问题');f.find('form').props.onSubmit({preventDefault(){}});await tick();assert.equal(sent.length,1);assert.equal(sent[0].text,'不同的问题');
});

test('local status bypasses controlled model admission and selected navigation never mutates the draft or asks',async t=>{
  let asked=0,inspected=0,admissions=0;const f=harness(t,{askAdmission:input=>{assert.notEqual(input,'任务进度');admissions++;return {allowed:false,code:'SUPERVISOR_OPERATION_PENDING',operation_id:'model_pending'};},onAsk:async()=>{asked++;return {status:'RETURNED'};},onInspect:async()=>{inspected++;return {status:'RETURNED'};}});
  f.open();await tick();f.setDraft('任务进度');await tick();const before=admissions;f.find('form').props.onSubmit({preventDefault(){}});await tick();assert.equal(asked,0);assert.equal(inspected,0);assert.equal(f.locals.length,1);
  f.setDraft('保留当前未发送的草稿');f.setMessages(Array.from({length:24},(_,i)=>({id:'recent_'+i,role:i%2?'assistant':'user',text:'近期讨论 '+i,status:i%2?'RETURNED':undefined})));
  f.setSelected({operation_id:'older_than_window',request_sha256:sha,question:'二十四条之前的原始问题',answer_text:'原始答复',status:'RETURNED'});await tick();assert.match(f.text(),/二十四条之前的原始问题/);assert.match(f.text(),/原始答复/);assert.doesNotMatch(f.text(),/近期讨论 23/);assert.equal(f.draft(),'保留当前未发送的草稿');assert.equal(asked,0);assert.equal(inspected,0);
  f.find('button',node=>node.props.class==='as-supervisor-command-selected-dismiss').props.onClick();await tick();assert.match(f.text(),/近期讨论 23/);assert.doesNotMatch(f.text(),/二十四条之前的原始问题/);assert.equal(f.draft(),'保留当前未发送的草稿');assert.ok(admissions>=before);
});

test('selected operation updates only its exact answer, deduplicates recent history and keeps its original question',async t=>{
  const f=harness(t);f.open();await tick();f.setSelected({operation_id:'old_op',request_sha256:sha,question:'必须保持的原问题',answer_text:'最初答复',status:'UNKNOWN'});
  f.setMessages([{id:'old_user',role:'user',operation_id:'old_op',text:'不能覆盖原问题的历史文本'},{id:'other_reply',role:'assistant',operation_id:'other_op',text:'无关答复',status:'RETURNED'},{id:'old_reply',role:'assistant',operation_id:'old_op',text:'核查后的真实答复',status:'RETURNED'}]);await tick();
  assert.equal(f.text().split('必须保持的原问题').length-1,1);assert.equal(f.text().split('核查后的真实答复').length-1,1);assert.doesNotMatch(f.text(),/不能覆盖原问题的历史文本|无关答复/);
});

test('explicit inspect remains available during a model request and a late processing receipt cannot downgrade the exact terminal',async t=>{
  let resolveModel,inspected=0;const waiting=new Promise(resolve=>resolveModel=resolve);
  const [allowed,setAllowed]=solid.createSignal(true);const g=harness(t,{askAdmission:()=>allowed()?{allowed:true}:{allowed:false,code:'SUPERVISOR_OPERATION_PENDING',operation_id:'inflight'},onAsk:async()=>{setAllowed(false);return waiting;},onInspect:async id=>{inspected++;return {operation_id:id,status:'RETURNED',text:'已核实的终态答复'};}});
  g.open();await tick();g.setDraft('请分析依赖关系');g.find('form').props.onSubmit({preventDefault(){}});await tick();const inspect=g.find('button',node=>node.props.class==='as-supervisor-command-input-inspect');assert.ok(inspect);assert.equal(inspect.props.disabled,false);inspect.props.onClick();await tick();assert.equal(inspected,1);assert.match(g.text(),/已核实的终态答复/);
  resolveModel({operation_id:'inflight',status:'PROCESSING'});await tick();assert.match(g.text(),/已核实的终态答复/);assert.equal(g.draft(),'');
});

test('new replies remain visible while an older selected operation is displayed',async t=>{
  const f=harness(t,{onAsk:async()=>({operation_id:'new_op',status:'RETURNED',text:'新提问的真实答复'})});f.open();await tick();
  f.setSelected({operation_id:'old_op',request_sha256:sha,question:'原始问题',answer_text:'原始答复',status:'RETURNED'});
  f.setMessages([{id:'new_user',role:'user',operation_id:'new_op',text:'继续这个讨论'},{id:'new_answer',role:'assistant',operation_id:'new_op',text:'新提问的真实答复',status:'RETURNED'}]);f.setDraft('继续这个讨论');f.find('form').props.onSubmit({preventDefault(){}});await tick();
  assert.match(f.text(),/原始问题/);assert.match(f.text(),/新提问的真实答复/);assert.equal(f.draft(),'');
  assert.equal(copy.centerText('en','originalQuestion'),'Original question');assert.equal(copy.centerText('en','recentDiscussion'),'Recent discussion');
});

test('selected follow-up references change only with explicit selection and dismissal, leaving raw text and local status unchanged',async t=>{
  const asked=[];const f=harness(t,{onAsk:async value=>{asked.push(value);return {operation_id:'new_'+asked.length,status:'RETURNED',text:'本次答复'};}});f.open();await tick();
  const first={operation_id:'old_first',request_sha256:'a'.repeat(64),question:'第一条原问题',answer_text:'第一条原答复',status:'RETURNED'},second={operation_id:'old_second',request_sha256:'b'.repeat(64),question:'第二条原问题',answer_text:'第二条原答复',status:'RETURNED'};
  const question='继续解释这部分，不要改写我的问题';
  f.setSelected(first);f.setDraft(question);f.find('form').props.onSubmit({preventDefault(){}});await tick();
  assert.equal(asked[0].text,question);assert.deepEqual(asked[0].followupRef,{operation_id:first.operation_id,request_sha256:first.request_sha256});
  f.setSelected(second);f.setDraft(question);f.find('form').props.onSubmit({preventDefault(){}});await tick();
  assert.equal(asked[1].text,question);assert.deepEqual(asked[1].followupRef,{operation_id:second.operation_id,request_sha256:second.request_sha256});assert.deepEqual(asked[0].followupRef,{operation_id:first.operation_id,request_sha256:first.request_sha256});
  f.find('button',node=>node.props.class==='as-supervisor-command-selected-dismiss').props.onClick();f.setDraft(question);f.find('form').props.onSubmit({preventDefault(){}});await tick();
  assert.equal(asked[2].text,question);assert.equal(Object.hasOwn(asked[2],'followupRef'),false);
  f.setSelected(first);f.setDraft('任务进度');f.find('form').props.onSubmit({preventDefault(){}});await tick();assert.equal(asked.length,3);assert.equal(f.locals.length,1);assert.equal(f.locals[0].text,'任务进度');
});


test('actual closed trigger stays aggregate through historical title updates, keeps drafts and creates no rotation wakeups',async t=>{
  let calls=0;const f=harness(t,{onAsk:async()=>{calls++;return {status:'RETURNED'};}}),currentStamp=new Date().toISOString();
  const values=[task('old',{classification:'CANCELLED',label:'旧项目 — 已取消的任务',observed_at:currentStamp})];
  f.setState(snapshot(values));await tick();
  const trigger=f.find('button',node=>node.props.class==='as-supervisor-command-trigger');
  const accessible=()=>trigger.props['aria-label'];
  assert.match(accessible(),/总管 · 暂无进行中的任务/);assert.doesNotMatch(accessible(),/旧项目|已取消的任务/);
  assert.equal(f.timers.length,0);assert.equal(f.find('button',node=>node.props.class==='as-supervisor-command-rotate'),undefined);
  f.setDraft('必须保留的未发送总管问题');await tick();assert.doesNotMatch(accessible(),/必须保留/);
  f.setState(snapshot([{...values[0],label:'不同的很长历史任务名字'.repeat(8)}]));await tick();
  assert.match(accessible(),/暂无进行中的任务/);assert.doesNotMatch(accessible(),/历史任务/);
  f.open();await tick();assert.equal(f.find('textarea').props.value,'必须保留的未发送总管问题');
  f.find('button',node=>node.props['aria-label']==='收起总管').props.onClick();await tick();assert.equal(f.draft(),'必须保留的未发送总管问题');
  assert.equal(f.timers.length,0);assert.equal(calls,0);
});

test('question panel omits task rows and observation time while one existing route retains task management',async t=>{
  let asks=0,allTasks=0;const f=harness(t,{onOpenAll:()=>{allTasks++;},onAsk:async()=>{asks++;return {status:'RETURNED'};}}),currentStamp=new Date().toISOString();
  const source=Array.from({length:12},(_,i)=>task('entry_'+i,{classification:i===0?'WAITING_USER':i===1?'EXECUTION_ERROR':'CANCELLED',wait_reason:i===0?'PERMISSION':null,label:'原始任务 '+i+' / 不截断归属',observed_at:currentStamp}));
  const before=JSON.stringify(source);f.setState(snapshot(source));f.setDraft('保留总管未发送的问题');await tick();assert.match(f.find('button',n=>n.props.class==='as-supervisor-command-trigger').props['aria-label'],/2 项待处理/);
  f.open();await tick();const buttons=f.nodes().filter(node=>node.type==='button'&&String(node.props['aria-label']).startsWith('查看任务：'));
  assert.equal(buttons.length,0);assert.equal(f.find('div',node=>node.props.class==='as-supervisor-command-list'),undefined);assert.doesNotMatch(f.text(),/原始任务|上次观察/);
  const footer=f.find('footer'),entry=footer.children.filter(node=>node.type==='button');assert.equal(entry.length,1);assert.equal(footer.children.some(node=>node.type==='span'),false);
  f.setMessages([{id:'user_exact',role:'user',text:'请解释“'+source[11].label+'”'},{id:'answer_exact',role:'assistant',text:'这是用户明确讨论的内容，仍要显示',status:'RETURNED'}]);await tick();assert.match(f.text(),/原始任务 11/);assert.match(f.text(),/用户明确讨论的内容/);
  entry[0].props.onClick();await tick();assert.equal(allTasks,1);assert.equal(f.opened.length,0);assert.equal(f.draft(),'保留总管未发送的问题');assert.equal(JSON.stringify(source),before);assert.equal(source[0].wait_reason,'PERMISSION');assert.equal(asks,0);
});

test('question-only panel keeps earlier discussion paging and current draft independent of task observations',async t=>{
  let pages=0,resets=0;const [history,setHistory]=solid.createSignal({messages:[],hasMore:true,loading:false,windowed:false,changed:false});
  const f=harness(t,{get history(){return history();},onReadEarlier:async()=>{pages++;setHistory({messages:[{id:'older_question',role:'user',text:'更早的原问题'},{id:'older_answer',role:'assistant',text:'更早的原回答',status:'RETURNED'}],hasMore:false,loading:false,windowed:true,changed:false});},onResetHistory:()=>{resets++;setHistory({messages:[],hasMore:true,loading:false,windowed:false,changed:false});}});
  f.open();f.setDraft('阅读历史时保留的草稿');await tick();const input=f.find('textarea');
  f.find('button',node=>node.children.some(child=>child.type==='#text'&&child.value===copy.centerText('zh-CN','historyEarlier'))).props.onClick();await tick();assert.equal(pages,1);assert.match(f.text(),/更早的原问题|更早的原回答/);assert.equal(f.find('textarea'),input);assert.equal(f.draft(),'阅读历史时保留的草稿');
  f.find('button',node=>node.children.some(child=>child.type==='#text'&&child.value===copy.centerText('zh-CN','recentDiscussion'))).props.onClick();await tick();assert.equal(resets,1);assert.equal(f.draft(),'阅读历史时保留的草稿');assert.equal(f.find('textarea'),input);
});

test('question-only panel retains a visible draft-save check and blocked send until recovery is resolved',async t=>{
  let checks=0,sends=0;const f=harness(t,{error:'草稿保存结果待核对',draftBlocked:true,draftRecovery:'CHECK_REQUIRED',onCheckDraftSave:()=>{checks++;},onAsk:async()=>{sends++;return {status:'RETURNED'};}});
  f.open();f.setDraft('必须保留的恢复草稿');await tick();assert.match(f.text(),/草稿保存结果待核对/);assert.equal(f.find('button',node=>node.props.type==='submit').props.disabled,true);
  f.find('button',node=>node.children.some(child=>child.type==='#text'&&child.value===copy.centerText('zh-CN','checkDraftSave'))).props.onClick();await tick();assert.equal(checks,1);assert.equal(sends,0);assert.equal(f.draft(),'必须保留的恢复草稿');
});

// batch78 AUTHORED_NOT_RUN. Limited universal UNIT nodes, not browser/native layout.
test('history search reads only after explicit submit and collapses without changing composer input',async t=>{
  const queries=[],resets=[];const f=harness(t,{onSearchEarlier:q=>queries.push(q),onResetSearch:()=>resets.push(true)});f.open();await tick();f.setDraft('保留我的新问题');const editor=f.find('textarea');
  f.find('input',n=>n.props.type==='search').props.onInput({currentTarget:{value:'旧问题'}});assert.deepEqual(queries,[]);
  const details=f.find('details',n=>n.props.class==='as-supervisor-search'),form=details.children.find(n=>n.type==='form');assert.ok(form);form.props.onSubmit({preventDefault(){}});assert.deepEqual(queries,['旧问题']);
  const before=resets.length;details.props.onToggle({currentTarget:{open:false}});assert.equal(resets.length,before+1);assert.equal(f.find('textarea'),editor);assert.equal(f.draft(),'保留我的新问题');
});
test('a saved history reply can select only its exact operation reference without sending or editing',async t=>{
  const selected=[],sent=[],id='archived',request='d'.repeat(64),messages=[{id:'archived_user',role:'user',operation_id:id,text:'旧问题'},{id:'archived_assistant',role:'assistant',operation_id:id,text:'旧回答',status:'RETURNED'}];
  const f=harness(t,{history:{messages,operations:[{operation_id:id,request_sha256:request}],hasMore:false,loading:false,windowed:false,changed:false},onSelectDiscussion:async ref=>{selected.push(ref);},onAsk:async v=>{sent.push(v);return {status:'RETURNED'};}});f.open();await tick();f.setDraft('新输入');const editor=f.find('textarea'),button=f.find('button',n=>n.children.some(c=>c.type==='#text'&&c.value==='继续这段对话'));assert.ok(button);assert.equal(button.props.disabled,false);button.props.onClick();await tick();assert.deepEqual(selected,[{operation_id:id,request_sha256:request}]);assert.deepEqual(sent,[]);assert.equal(f.draft(),'新输入');assert.equal(f.find('textarea'),editor);
});
test('a failed original discussion lookup reports an independent error and keeps the draft',async t=>{
  const messages=[{id:'old_user',role:'user',operation_id:'old',text:'旧问题'},{id:'old_assistant',role:'assistant',operation_id:'old',text:'旧回答',status:'RETURNED'}],f=harness(t,{history:{messages,operations:[{operation_id:'old',request_sha256:sha}],hasMore:false,loading:false,windowed:false,changed:false},onSelectDiscussion:async()=>{throw Error('missing original');}});f.open();await tick();f.setDraft('不丢失原输入');f.find('button',n=>n.children.some(c=>c.type==='#text'&&c.value==='继续这段对话')).props.onClick();await tick();assert.match(f.text(),/这段原对话暂未核对/);assert.equal(f.draft(),'不丢失原输入');
});
test('stale matching history shows searched coverage and disables direct continuation',async t=>{
  const found={query:'旧',results:[{id:'old',question:'旧问题',answer_text:'旧回答',text_truncated:false,reference:{operation_id:'old',request_sha256:sha}}],scanned_records:24,total_records:100,matched_records:1,hasMore:true,loading:false,changed:true,windowed:false,coverage:'SAVED_TEXT'};
  const f=harness(t,{search:found,onSearchEarlier:()=>{},onSelectDiscussion:async()=>{}});f.open();await tick();f.find('input',n=>n.props.type==='search').props.onInput({currentTarget:{value:'旧'}});await tick();assert.match(f.text(),/已查 24\/100 条/);const button=f.find('button',n=>n.children.some(c=>c.type==='#text'&&c.value==='继续这段对话'));assert.equal(button.props.disabled,true);assert.match(f.text(),/有新记录/);
});
test('a saved local status search hit stays readable without an invented model followup button',async t=>{
  const found={query:'状态',results:[{id:'local',question:'状态',answer_text:'没有活动执行',text_truncated:false}],scanned_records:1,total_records:1,matched_records:1,hasMore:false,loading:false,changed:false,windowed:false,coverage:'SAVED_TEXT'},f=harness(t,{search:found,onSearchEarlier:()=>{},onSelectDiscussion:async()=>{}});f.open();await tick();f.find('input',n=>n.props.type==='search').props.onInput({currentTarget:{value:'状态'}});await tick();assert.match(f.text(),/没有活动执行/);assert.equal(f.find('button',n=>n.children.some(c=>c.type==='#text'&&c.value==='继续这段对话')),undefined);
});
