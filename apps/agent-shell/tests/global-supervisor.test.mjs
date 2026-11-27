import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import readline from 'node:readline';
import {readFile} from 'node:fs/promises';
import {resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';
import * as solid from '../node_modules/solid-js/dist/solid.js';
import {ApiClient} from '../src/api.ts';
import * as supervisor from '../src/global-supervisor.ts';
import {translateUI} from '../src/ui-language.ts';
const {GlobalSupervisorClient,GlobalSupervisorPanelModel,supervisorSnapshot,supervisorSettingsInput,supervisorCounts,supervisorTaskStatus}=supervisor;
const app=resolve(dirname(fileURLToPath(import.meta.url)),'..'),root=resolve(app,'../..');
const stamp='2026-10-15T08:00:00+00:00',sha='a'.repeat(64);
const reference=id=>({id,revision:1,sha256:sha});
const task=(extra={})=>({key:{kind:'original_session',project_id:'project_one',object_id:'original_one',operation_id:'message_one',generation:7},
  label:'独立原会话任务',source_status:'RUNNING',observed_at:stamp,actual_error_code:null,execution_returned:null,validation_status:'NOT_RUN',wait_reason:null,
  coverage:'PARTIAL',source_ref:reference('exact_task_source'),fresh_for_seconds:120,classification:'RUNNING',observation_status:'CURRENT',last_checked_at:stamp,...extra});
const alert=(extra={})=>({...reference('alert_one'),alert_ref:reference('alert_one'),kind:'TASK_STATE_CHANGED',key:task().key,label:'独立任务需补充',category:'WAITING_USER',
  source_status:'WAITING_USER',actual_error_code:null,created_at:stamp,terminal:false,acknowledged:false,acknowledged_at:null,...extra});
const snapshot=(extra={})=>({settings:{enabled:true,notifications_enabled:true,interval_seconds:30,revision:1,sha256:sha},
  observer:{status:'READY',last_attempt_at:stamp,last_success_at:stamp,actual_error_code:null,running:true},tasks:[task()],alerts:[alert()],alerts_total:1,coverage:'ONLY_COLLECTED_EXISTING_TASKS',...extra});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}

test('safe projections keep exact independent session refs, separate return from validation and reject unsafe metadata',()=>{
  const value=supervisorSnapshot({...snapshot(),private_token:'never-copy',tasks:[{...task(),transcript:'never-copy'}]});
  assert.equal(JSON.stringify(value).includes('never-copy'),false);assert.deepEqual(value.tasks[0].key,task().key);
  assert.deepEqual(supervisorCounts(value),{running:1,attention:0,unacknowledged:1});
  assert.equal(supervisorSnapshot(snapshot({alerts_has_more:true,alerts_total:300})).alerts_has_more,true);
  assert.equal(supervisorTaskStatus(task({classification:'EXECUTION_RETURNED',source_status:'RETURNED',validation_status:'NOT_RUN'})),'执行返回');
  assert.doesNotMatch(supervisorTaskStatus(task({classification:'EXECUTION_RETURNED'})),/验收通过|验证通过/);
  const partial=supervisorSnapshot(snapshot({observer:{...snapshot().observer,status:'PARTIAL',unavailable_codes:['ONE_SOURCE_CORRUPT']},
    tasks:[task(),task({key:{...task().key,object_id:'stale_task'},observation_status:'STALE'})]}));
  assert.deepEqual(supervisorCounts(partial),{running:1,attention:1,unacknowledged:1});
  for(const changes of [{label:'password=do-not-render'}, {key:{...task().key,object_id:'../other'}},{observed_at:'2026-10-15'},
    {source_ref:{...reference('source'),sha256:'bad'}},{classification:'SUCCESS_MEANS_ACCEPTED'}]){
    assert.throws(()=>supervisorSnapshot(snapshot({tasks:[task(changes)]})));
  }
  for(const interval of ['0','4','3601','12.5','NaN',''])assert.throws(()=>supervisorSettingsInput({enabled:true,notifications_enabled:false,interval_seconds:interval},1));
  assert.deepEqual(supervisorSettingsInput({enabled:false,notifications_enabled:true,interval_seconds:'3600'},2),{enabled:false,notifications_enabled:true,interval_seconds:3600,expected_revision:2});
});

// Actual production FastAPI routes + GlobalSupervisor SQLite. Only the source
// collector uses original task facts. Transport fault injection loses a real
// response after the real transaction, never substitutes storage or API logic.
const fixtureScript=String.raw`
import asyncio,json,sys,tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime,timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
from apps.api.supervisor import register_supervisor_routes
with tempfile.TemporaryDirectory(prefix='supervisor-ui-real-api-') as folder:
 @asynccontextmanager
 async def lifetime(app):
  yield
  await app.state.global_supervisor.close()
 app=FastAPI(lifespan=lifetime)
 app.state.agent_sessions=SimpleNamespace(data_dir=Path(folder),active={})
 service=register_supervisor_routes(app,Path(folder))
 @app.get('/api/bootstrap')
 def bootstrap():return {'csrf_token':'original-local-supervisor-fixture'}
 async def source():
  return [{'key':{'kind':'original_session','project_id':'original_project','object_id':'original_session','operation_id':'original_operation','generation':1},
   'label':'原创独立会话任务','source_status':'WAITING_USER','wait_reason':'USER_INPUT','observed_at':datetime.now(timezone.utc).isoformat(),
   'source_ref':{'id':'original_source','revision':3,'sha256':'a'*64},'coverage':'SOURCE_SNAPSHOT','fresh_for_seconds':120}]
 service.collect=source
 with TestClient(app) as client:
  for line in sys.stdin:
   try:
    cmd=json.loads(line)
    response=client.request(cmd.get('method','GET'),cmd['path'],json=cmd.get('body'),headers=cmd.get('headers') or {})
    print(json.dumps({'status':response.status_code,'body':response.json()},ensure_ascii=False),flush=True)
   except Exception as error:print(json.dumps({'fixture_error':type(error).__name__}),flush=True)
`;
function actualApi(t){
  const env={...process.env,PYTHONDONTWRITEBYTECODE:'1'},child=spawn(resolve(root,'.venv/bin/python'),['-u','-c',fixtureScript],{cwd:root,env,stdio:['pipe','pipe','pipe']}),pending=[],calls=[];
  let stderr='',dropPath='';child.stderr.on('data',value=>stderr=(stderr+value).slice(-4000));
  const lines=readline.createInterface({input:child.stdout});lines.on('line',line=>{const item=pending.shift();if(item)try{item.resolve(JSON.parse(line));}catch(error){item.reject(error);}});
  child.on('error',error=>pending.splice(0).forEach(item=>item.reject(error)));child.on('exit',code=>pending.splice(0).forEach(item=>item.reject(new Error(`Original supervisor fixture exited ${code}: ${stderr}`))));
  t.after(async()=>{child.stdin.end();await new Promise(resolve=>child.exitCode!==null?resolve():child.once('exit',resolve));lines.close();});
  const raw=body=>new Promise((resolve,reject)=>{pending.push({resolve,reject});child.stdin.write(JSON.stringify(body)+'\n');});
  const http=new ApiClient(async(path,options)=>{
    const method=options?.method??'GET',body=options?.body?JSON.parse(options.body):undefined;calls.push({path,method,body,headers:options?.headers});
    const value=await raw({path,method,body,headers:options?.headers});assert.ok(!value.fixture_error,JSON.stringify(value));
    if(path===dropPath&&method==='POST'){dropPath='';throw new Error('Original acknowledgement lost after transaction');}
    return new Response(JSON.stringify(value.body),{status:value.status});
  });
  return {client:new GlobalSupervisorClient(http),raw,calls,drop(path){dropPath=path;}};
}

test('actual supervisor API/SQLite reads settings, zero-model local check, CAS retention, lost settings and exact alert ack recover only with GET',async t=>{
  const f=actualApi(t);let state=await f.client.refresh();assert.equal(state.settings.interval_seconds,30);
  const checked=await f.client.check();assert.equal(checked.outcome,'ACKNOWLEDGED');state=checked.snapshot;
  assert.equal(state.tasks[0].key.object_id,'original_session');assert.equal(state.tasks[0].classification,'WAITING_USER');assert.ok(state.alerts.length);
  const body={enabled:false,notifications_enabled:false,interval_seconds:17,expected_revision:state.settings.revision};
  f.drop('/api/supervisor/settings');const changed=await f.client.settings(body);
  assert.equal(changed.outcome,'OBSERVED_MATCH');assert.equal(changed.snapshot.settings.interval_seconds,17);assert.equal(changed.snapshot.settings.notifications_enabled,false);
  assert.equal(f.calls.filter(c=>c.path==='/api/supervisor/settings'&&c.method==='POST').length,1);
  const conflict=await f.client.settings({...body,interval_seconds:19});assert.equal(conflict.outcome,'REJECTED');assert.equal(conflict.snapshot.settings.interval_seconds,17);
  const exact=changed.snapshot.alerts.find(a=>!a.acknowledged);f.drop('/api/supervisor/alerts/ack');
  const ack=await f.client.acknowledge(exact.alert_ref);assert.equal(ack.outcome,'OBSERVED_MATCH');assert.equal(ack.snapshot.alerts.find(a=>a.id===exact.id).acknowledged,true);
  assert.equal(ack.snapshot.tasks[0].classification,'WAITING_USER','ack cannot change the actual task');
  assert.equal(f.calls.filter(c=>c.path.endsWith('/alerts/ack')&&c.method==='POST').length,1);
  assert.ok(f.calls.filter(c=>c.method==='POST').every(c=>c.headers['x-eeas-csrf']==='original-local-supervisor-fixture'));
  assert.ok(f.calls.every(c=>!c.path.includes('chat/completions')&&!c.path.includes('native-messages')&&!c.path.includes('/runs')));
});

test('unconfirmed POST is fenced, read recovery never replays; settings rejection/unknown preserve typed input',async()=>{
  const calls=[],remote=snapshot();let fail=true;
  const client=new GlobalSupervisorClient({request:async(path,method='GET',body)=>{
    calls.push({path,method,body});if(method==='GET')return remote;
    if(fail)throw new Error('transport lost');return remote.settings;
  }});
  let view;const model=new GlobalSupervisorPanelModel(client,value=>view=value,()=>assert.fail('no navigation'));
  model.setActive(true);await model.refresh();model.edit({enabled:false,interval_seconds:'37'});await model.save();
  assert.equal(view.unknown,true);assert.equal(view.draft.interval_seconds,'37');assert.equal(view.draft.enabled,false);
  await model.save();await model.refresh();await model.save();assert.equal(calls.filter(c=>c.method==='POST').length,1);
  assert.ok(calls.filter(c=>c.method==='POST').every(c=>c.path==='/api/supervisor/settings'));
  Object.assign(remote.settings,{enabled:false,interval_seconds:37,revision:2});await model.refresh();assert.equal(view.unknown,false);
  assert.equal(view.draft.interval_seconds,'37');assert.equal(view.dirty,false);assert.equal(view.basis.revision,2);
  model.accept(snapshot());assert.equal(view.snapshot.settings.revision,2,'late managed read cannot downgrade confirmed settings');model.dispose();
});

test('late reads after hide/dispose cannot overwrite or call callbacks, while reopen can read again',async()=>{
  const gate=deferred();let reads=0;const values=[],callbacks=[];
  const client=new GlobalSupervisorClient({request:async()=>++reads===1?gate.promise:snapshot({tasks:[]})});
  const model=new GlobalSupervisorPanelModel(client,value=>values.push(value),task=>callbacks.push(task));
  model.setActive(true);const pending=model.refresh();model.setActive(false);gate.resolve(snapshot());await pending;
  assert.equal(model.state.snapshot,undefined);assert.equal(model.state.busy,'');await model.open(task());assert.equal(callbacks.length,0);
  model.setActive(true);await model.refresh();assert.equal(model.state.snapshot.tasks.length,0);
  const before=values.length;model.dispose();model.accept(snapshot());await model.open(task());assert.equal(values.length,before);assert.equal(callbacks.length,0);
});

// Compile the actual Solid component to Solid's official universal renderer.
// Only its DOM host is in memory: real signals/effects/For/Show, compiled event
// handlers and production PanelModel/Client execute. No browser/Windows claim.
const universalSource=(await readFile(resolve(app,'node_modules/solid-js/universal/dist/universal.js'),'utf8'))
  .replace("from 'solid-js'",`from '${pathToFileURL(resolve(app,'node_modules/solid-js/dist/solid.js')).href}'`);
const {createRenderer}=await import('data:text/javascript;base64,'+Buffer.from(universalSource).toString('base64'));
function componentHarness(t,initial,client){
  const host={createElement:type=>({type,props:{},children:[],parent:null}),createTextNode:value=>({type:'#text',value,children:[],parent:null}),
    isTextNode:node=>node.type==='#text',replaceText:(node,value)=>{node.value=value;},
    insertNode(parent,node,anchor){if(node.parent){const old=node.parent.children;old.splice(old.indexOf(node),1);}node.parent=parent;
      const index=anchor?parent.children.indexOf(anchor):-1;parent.children.splice(index<0?parent.children.length:index,0,node);},
    removeNode(parent,node){const index=parent.children.indexOf(node);if(index>=0)parent.children.splice(index,1);node.parent=null;},
    setProperty:(node,name,value)=>{node.props[name]=value;},getParentNode:node=>node.parent,getFirstChild:node=>node.children[0],
    getNextSibling:node=>node.parent?.children[node.parent.children.indexOf(node)+1]};
  const renderer=createRenderer(host),source=readFileSyncSource(),js=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;
  const output=babel.transformSync(js,{filename:'GlobalSupervisorPanel.jsx',presets:[[solidPreset,{generate:'universal',moduleName:'test-renderer'}]],configFile:false,babelrc:false}).code;
  const parsed=ts.createSourceFile('panel.mjs',output,ts.ScriptTarget.Latest,true,ts.ScriptKind.JS);let code=output;const bindings={};
  for(const node of [...parsed.statements].reverse())if(ts.isImportDeclaration(node)){
    const imported=node.moduleSpecifier.text==='solid-js'?solid:node.moduleSpecifier.text==='test-renderer'?renderer:node.moduleSpecifier.text==='./ui-language'?{t:message=>translateUI(message,'zh-CN'),uiLocale:()=> 'zh-CN'}:node.moduleSpecifier.text.endsWith('.css')?{}:supervisor;
    if(node.importClause?.name)bindings[node.importClause.name.text]=imported.default;
    if(node.importClause?.namedBindings&&ts.isNamedImports(node.importClause.namedBindings))for(const item of node.importClause.namedBindings.elements)bindings[item.name.text]=imported[item.propertyName?.text??item.name.text];
    code=code.slice(0,node.getStart(parsed))+code.slice(node.end);
  }
  code=code.replace('export default function GlobalSupervisorPanel','function GlobalSupervisorPanel');
  const Panel=new Function(...Object.keys(bindings),code+'\nreturn GlobalSupervisorPanel;')(...Object.values(bindings));
  const [active,setActive]=solid.createSignal(true),[current,setCurrent]=solid.createSignal(initial),opened=[],published=[];
  const props={get active(){return active();},get snapshot(){return current();},managed:true,client,onOpenTask:task=>opened.push(task),onSnapshot:value=>published.push(value)};
  const container=host.createElement('root'),dispose=renderer.render(()=>Panel(props),container);t.after(dispose);
  const nodes=()=>{const result=[];const walk=node=>{result.push(node);for(const child of node.children)walk(child);};walk(container);return result;};
  return {nodes,setActive,setCurrent,opened,published,text:()=>nodes().filter(n=>n.type==='#text').map(n=>n.value).join(' '),
    find:(type,filter=()=>true)=>nodes().find(n=>n.type===type&&filter(n))};
}
const panelSource=await readFile(resolve(app,'src/GlobalSupervisorPanel.tsx'),'utf8');
function readFileSyncSource(){return panelSource;}

test('actual compiled component invokes exact independent-task callback and active=false disables retained handlers without changing project',async t=>{
  const calls=[],client=new GlobalSupervisorClient({request:async(...args)=>{calls.push(args);return snapshot();}});
  const f=componentHarness(t,snapshot(),client);await tick();
  const button=f.find('button',n=>n.props.class==='as-supervisor-task');assert.ok(button);button.props.onClick();await tick();
  assert.deepEqual(f.opened,[task()]);assert.equal(calls.length,0,'managed view must not create a second polling loop');
  assert.match(f.text(),/运行/);assert.ok(f.find('details'));assert.equal(f.find('details').props.open,undefined);
  f.setActive(false);button.props.onClick();await tick();assert.equal(f.opened.length,1);assert.equal(f.find('section'),undefined);
});

test('actual component keeps disabled/empty/partial/error states honest and settings callback preserves failed input',async t=>{
  const current=snapshot({tasks:[],alerts:[],alerts_total:300,alerts_has_more:true,settings:{...snapshot().settings,enabled:false},observer:{...snapshot().observer,status:'DISABLED'}}),calls=[];
  const client=new GlobalSupervisorClient({request:async(path,method='GET',body)=>{calls.push({path,method,body});if(method==='GET')return current;throw new Error('lost response');}});
  const f=componentHarness(t,current,client);await tick();assert.match(f.text(),/监督已关闭/);assert.match(f.text(),/还没有可观察的任务/);
  assert.match(f.text(),/更早记录仍保留/);
  const interval=f.find('input',n=>n.props['aria-label']==='检查间隔（秒）');interval.props.onInput({currentTarget:{value:'47'}});
  const checks=f.nodes().filter(n=>n.type==='input'&&n.props.type==='checkbox');checks[1].props.onChange({currentTarget:{checked:false}});
  f.find('form').props.onSubmit({preventDefault(){}});await tick();await tick();
  assert.equal(interval.props.value,'47');assert.match(f.text(),/设置结果待核对/);assert.equal(calls.filter(c=>c.method==='POST').length,1);
  assert.equal(checks[1].props.checked,false);assert.equal(calls.find(c=>c.method==='POST').body.notifications_enabled,false);
  f.find('form').props.onSubmit({preventDefault(){}});await tick();assert.equal(calls.filter(c=>c.method==='POST').length,1);
  f.setCurrent(snapshot({observer:{...snapshot().observer,status:'PARTIAL',unavailable_codes:['ONE_SOURCE_UNAVAILABLE']},tasks:[task({classification:'EXECUTION_RETURNED',source_status:'RETURNED',validation_status:'NOT_RUN'})]}));
  await tick();assert.match(f.text(),/部分任务状态待核查/);assert.match(f.text(),/执行返回/);assert.doesNotMatch(f.text(),/验收通过/);
  f.setCurrent({...current,observer:{...current.observer,status:'UNSUPPORTED_NOT_A_FACT'}});await tick();
  assert.match(f.text(),/SUPERVISOR_RESPONSE_INVALID/);assert.match(f.text(),/执行返回/,'malformed managed response must preserve last known tasks instead of crashing');
});

test('activity projection groups complete keys, keeps important history and validates public display context',()=>{
  const sameTitle=task({classification:'EXECUTION_RETURNED',label:'运费边界修复',display_context:{project_label:'Windows 工程验证',task_created_at:'2026-10-27T05:25:00+08:00'}});
  const other=task({...sameTitle,key:{...sameTitle.key,project_id:'other_project'},display_context:{project_label:'Windows 工程验证',task_created_at:'2026-10-27T02:25:00+08:00'}});
  const parsed=supervisorSnapshot(snapshot({tasks:[sameTitle,other]}));
  assert.deepEqual(parsed.tasks[0].display_context,sameTitle.display_context);
  assert.deepEqual(supervisorSnapshot(snapshot({tasks:[task({display_context:{project_label:'Windows 工程验证',workspace_label:null,conversation_created_at:null,task_created_at:null}})]})).tasks[0].display_context,{project_label:'Windows 工程验证'});
  assert.notEqual(supervisor.supervisorTaskIdentity(sameTitle.key),supervisor.supervisorTaskIdentity(other.key));
  assert.equal(supervisor.supervisorTaskIdentity({...sameTitle.key,project_id:null,operation_id:undefined}),
    supervisor.supervisorTaskIdentity({generation:7,kind:'original_session',object_id:'original_one',operation_id:null}));
  const events=Array.from({length:400},(_,i)=>alert({id:'phase_'+i,alert_ref:reference('phase_'+i),key:i%2?sameTitle.key:other.key,category:'RUNNING'}));
  events.push(alert({id:'error',alert_ref:reference('error'),category:'EXECUTION_ERROR'}),
    alert({id:'stale',alert_ref:reference('stale'),category:'EXECUTION_RETURNED',observation_status:'STALE'}),
    alert({id:'failed',alert_ref:reference('failed'),category:'EXECUTION_RETURNED',validation_status:'FAILED'}),
    alert({id:'unknown',alert_ref:reference('unknown'),category:'RUNNING'}));
  const projected=supervisor.supervisorActivity(events,r=>r.id==='unknown');
  assert.equal(projected.history.length,2,'same label in different projects is two complete identities');
  assert.equal(projected.history.reduce((n,g)=>n+g.alerts.length,0),404,'history does not discard event records');
  assert.equal(projected.attention.length,1);assert.equal(projected.attention[0].alerts.length,4,'returned tasks cannot erase prior errors/stale/failed/ack-unknown');
  assert.equal(supervisor.supervisorGroup(task({classification:'EXECUTION_RETURNED',validation_status:'FAILED'})),'attention');
  assert.equal(supervisor.supervisorGroup(task({classification:'EXECUTION_RETURNED',actual_error_code:'ORIGINAL_FAILURE'})),'attention');
  const context=supervisor.supervisorTaskContext(parsed.tasks[0],parsed.tasks);
  assert.deepEqual(context,{labels:['Windows 工程验证'],created_kind:'task',created_at:'2026-10-27T05:25:00+08:00'});
  assert.deepEqual(supervisor.supervisorTaskContext(task(),[task()]),{labels:[]});
  assert.deepEqual(supervisor.supervisorTaskContext({...sameTitle,display_context:{workspace_label:'当前工程'}},[sameTitle,other]),{labels:['当前工程']});
  for(const display_context of [{project_label:'password=private'}, {task_created_at:'2026-10-27'}, {workspace_label:'bad\npath'}])
    assert.throws(()=>supervisorSnapshot(snapshot({tasks:[task({display_context})]})));
});

test('actual component presents results before grouped history and retains same-key DOM and exact unknown recovery',async t=>{
  const returned=task({classification:'EXECUTION_RETURNED',label:'运费边界修复',display_context:{project_label:'Windows 工程验证',task_created_at:'2026-10-27T05:25:00+08:00'}});
  const second=task({...returned,key:{...returned.key,operation_id:'other_operation'},display_context:{project_label:'Windows 工程验证',task_created_at:'2026-10-27T02:25:00+08:00'}});
  const events=Array.from({length:1000},(_,i)=>alert({id:'phase_'+i,alert_ref:reference('phase_'+i),category:'RUNNING',key:i%2?returned.key:second.key}));
  let current=snapshot({tasks:[returned,second],alerts:events,alerts_total:1005,alerts_has_more:true}),calls=[];
  const client=new GlobalSupervisorClient({request:async(path,method='GET')=>{calls.push({path,method});if(method==='GET')return current;throw new Error('lost ack');}});
  await client.acknowledge(events[0].alert_ref);assert.ok(client.alertUnresolved(events[0].alert_ref));
  const f=componentHarness(t,current,client);await tick();
  const first=f.find('button',n=>n.props.class==='as-supervisor-task');assert.ok(first);
  const history=f.find('details',n=>n.props.class==='as-supervisor-history');assert.ok(history);assert.equal(history.props.open,undefined);
  assert.ok(f.nodes().indexOf(first)<f.nodes().indexOf(history));
  assert.equal(f.nodes().filter(n=>n.type==='details'&&n.props.class==='as-supervisor-history-group').length,2);
  assert.match(f.text(),/任务创建于/);assert.doesNotMatch(f.text(),/确认后可继续|06:55:15/);
  assert.match(f.text(),/确认结果待核对/);assert.equal(calls.filter(c=>c.method==='POST').length,1);
  history.props.open=true;
  f.find('details',n=>n.props.class==='as-supervisor-history-group').props.onToggle({currentTarget:{open:true}});await tick();
  const visibleEvents=f.nodes().filter(n=>n.type==='article'&&n.props.class==='as-supervisor-event');assert.equal(visibleEvents.length,20);
  assert.equal(visibleEvents.flatMap(n=>n.children).filter(n=>n.type==='button').length,0,'history is read-only: no per-phase acknowledgement or duplicate warning action');
  current=structuredClone(current);current.observer.last_success_at='2026-10-27T08:00:00Z';
  f.setCurrent(current);await tick();
  assert.equal(f.find('button',n=>n.props.class==='as-supervisor-task'),first,'exact task keys retain actual button DOM');
  assert.equal(f.find('details',n=>n.props.class==='as-supervisor-history'),history);assert.equal(history.props.open,true);
  first.props.onClick();await tick();assert.deepEqual(f.opened,[returned]);
  assert.equal(calls.filter(c=>c.method==='POST').length,1,'render/expand/refresh never auto-acknowledges');
});

test('actual component keeps one exact task button across running to returned and opens the updated source',async t=>{
  const running=task(),returned={...running,classification:'EXECUTION_RETURNED',source_status:'RETURNED',execution_returned:true};
  const calls=[],client=new GlobalSupervisorClient({request:async(...args)=>{calls.push(args);return snapshot();}});
  const f=componentHarness(t,snapshot({tasks:[running],alerts:[],alerts_total:0}),client);await tick();
  const button=f.find('button',n=>n.props.class==='as-supervisor-task');assert.ok(button);assert.match(f.text(),/运行中/);
  f.setCurrent(snapshot({tasks:[returned],alerts:[],alerts_total:0}));await tick();
  assert.equal(f.find('button',n=>n.props.class==='as-supervisor-task'),button,'status groups must share one keyed task list');
  assert.match(f.text(),/已返回 \/ 已结束/);button.props.onClick();await tick();assert.deepEqual(f.opened,[returned]);assert.equal(calls.length,0);
});
