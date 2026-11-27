import {authoredCopy,authoredErrorCopy,authoredText} from '../src/authored-ui-copy.ts';
import test from 'node:test';
import {spawn} from 'node:child_process';
import readline from 'node:readline';
import {ApiClient} from '../src/api.ts';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';
import * as solid from 'solid-js';
import * as web from 'solid-js/web';
import {sameSupervisedTask} from '../src/task-activity.ts';
import {supervisorSnapshot} from '../src/global-supervisor.ts';
import {HistoryDirectoryRefresh} from '../src/history-directory-refresh.ts';
import {NavigationOwner} from '../src/registry.ts';
import {sameNativeReference,saveNativeHistory,readNativeHistory,readNativeText} from '../src/native-collaboration.ts';
import {DiscussionDraftLease} from '../src/discussion-drafts.ts';
import {ComposerDraftPersistence} from '../src/composer-draft.ts';
import {conversationIdFor} from '../src/quick-inbox.ts';
import {fileConversationBasis,sameDiscussionScope,associationScope} from '../src/discussion-file-context.ts';
import {directFileSupported,exactFileSource} from '../src/companion-files.ts';
import {nativeReportArtifact,openNativeReportArtifact} from '../src/native-artifacts.ts';
import {nativeReference} from '../src/native-collaboration.ts';
import {loadSupervisedTaskNavigation} from '../src/supervised-task-navigation.ts';
import {readComposerDraft} from '../src/composer-draft.ts';
const app=resolve(dirname(fileURLToPath(import.meta.url)),'..'),source=await readFile(resolve(app,'src/App.tsx'),'utf8');
const parsed=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX),component=parsed.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='App');
const actualFlushers={};
for(const declaration of parsed.statements.filter(ts.isImportDeclaration)){const wanted=declaration.importClause?.namedBindings?.elements?.filter(item=>item.name.text.startsWith('flush'))??[];if(wanted.length){const name=declaration.moduleSpecifier.text,loaded=await import(new URL(name.endsWith('.ts')?name:name+'.ts',new URL('../src/App.tsx',import.meta.url)).href);for(const item of wanted)actualFlushers[item.name.text]=loaded[item.propertyName?.text??item.name.text];}}
const actual=name=>{const node=component.body.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text===name);assert.ok(node,'actual App callback '+name);return node.getText(parsed);};
const hooks=component.body.statements.filter(node=>ts.isExpressionStatement(node)&&ts.isCallExpression(node.expression)&&node.expression.expression.getText(parsed)==='onMount').map(node=>node.expression.arguments[0]);
const watch=hooks.find(node=>node.getText(parsed).includes('nativeBridge?.onTaskActivity'));
assert.ok(watch,'actual supervisor mount callback');
let closeCallback,snapshotCallback,openAllCallback;
function visit(node){if(ts.isJsxElement(node)||ts.isJsxSelfClosingElement(node)){const attrs=ts.isJsxElement(node)?node.openingElement.attributes:node.attributes;if(attrs.properties.some(attr=>ts.isJsxAttribute(attr)&&attr.name.getText(parsed)==='aria-label'&&(attr.initializer?.text??(ts.isJsxExpression(attr.initializer)&&ts.isCallExpression(attr.initializer.expression)?attr.initializer.expression.arguments[0]?.text:undefined))==='收起任务动态'))closeCallback=attrs.properties.find(attr=>ts.isJsxAttribute(attr)&&attr.name.getText(parsed)==='onClick').initializer.expression.getText(parsed);}if(ts.isJsxSelfClosingElement(node)&&node.tagName.getText(parsed)==='SupervisorCommandCenter')openAllCallback=node.attributes.properties.find(attr=>ts.isJsxAttribute(attr)&&attr.name.getText(parsed)==='onOpenAll')?.initializer.expression.getText(parsed);if(ts.isJsxSelfClosingElement(node)&&node.tagName.getText(parsed)==='GlobalSupervisorPanel')snapshotCallback=node.attributes.properties.find(attr=>ts.isJsxAttribute(attr)&&attr.name.getText(parsed)==='onSnapshot')?.initializer.expression.getText(parsed);ts.forEachChild(node,visit);}visit(component);assert.ok(closeCallback);assert.ok(snapshotCallback);assert.ok(openAllCallback);
const hash='a'.repeat(64),ref={id:'a'.repeat(32),revision:1,sha256:hash};
const key=(extra={})=>({kind:'original_session',project_id:'project_a',object_id:ref.id,operation_id:'operation_a',generation:3,...extra});
const task=(extra={})=>({key:key(),label:'真实独立任务',source_status:'RETURNED',observed_at:'2026-10-15T08:00:00+00:00',actual_error_code:null,execution_returned:true,validation_status:'NOT_RUN',wait_reason:null,coverage:'PARTIAL',source_ref:ref,fresh_for_seconds:120,classification:'EXECUTION_RETURNED',observation_status:'CURRENT',last_checked_at:'2026-10-15T08:00:00+00:00',...extra});
const snapshot=(tasks=[task()])=>({settings:{enabled:true,notifications_enabled:false,interval_seconds:30,revision:1,sha256:hash},observer:{status:'READY',last_attempt_at:null,last_success_at:null,actual_error_code:null,running:true,unavailable_codes:[]},tasks,alerts:[],alerts_total:0,coverage:'ONLY_COLLECTED_EXISTING_TASKS'});
const detail=(taskValue=task())=>({key:taskValue.key,task:taskValue,input_text:'原任务问题',answer_text:'原任务回答',artifacts:[],session_ref:ref,automatic_retry:false,execution_authorized:false,navigation:{version:1,kind:'original',status:'AVAILABLE',reason_code:null,task_key:taskValue.key,source_ref:taskValue.source_ref,project_id:taskValue.key.project_id,operation_id:taskValue.key.operation_id,automatic_retry:false,execution_authorized:false,session_ref:ref,generation:taskValue.key.generation,current_generation:taskValue.key.generation,request_sha256:hash,provider_profile_ref:null,workspace_ref:null}});
const session=(pid='project_a')=>({project_id:pid,session_ref:ref,generation:3,owner_status:'READY',native_status:'idle',connected_owner:true,configuration_current:true,observation_is_current:true,workspace_kind:'APP_INTERNAL_READONLY',last_event_seq:0,automatic_post_replay:false,provider_profile_ref:null,workspace_ref:null,messages:[],todo:[],children:[],pending_questions:[],pending_permissions:[]});
const operation=()=>({id:'operation_a',kind:'MESSAGE',status:'RETURNED',session_ref:ref,request_sha256:hash,operation_ref:{id:'operation_a',request_sha256:hash},request:{operation_id:'operation_a',session_ref:ref,generation:3,raw_text:'原任务问题'},automatic_replay:false});
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return{promise,resolve,reject};};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function harness(options={}){
 const state={open:false,detail:undefined,snapshot:undefined,error:'',loading:false,menu:true,project:options.project??'project_a',prompt:'用户原有草稿😀\r\n继续补充',messages:[],conversation:'topic_a',busy:'',globalError:'',notice:''},originalDraft=state.prompt;
 const calls=[],opened=[],cleanup=[],timers=new Map(),listeners={},navigation=new NavigationOwner();let timer=0;
 const set=name=>value=>{state[name]=typeof value==='function'?value(state[name]):value;};
 const data=new Map(),localStorage=options.storage??{getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key)};
 const draftPersistence=new ComposerDraftPersistence(localStorage),lease=draftPersistence.begin(state.project,undefined,{text:state.prompt,feedback_refs:[]});draftPersistence.initialize(lease);draftPersistence.hydrated(lease);
 const api=options.apiClient??{workspaces:async()=>[],projectPath:pid=>'/api/projects/'+encodeURIComponent(pid),projects:async()=>[{id:'project_a'},{id:'project_b'}],request:async(...args)=>{calls.push(args);if(args[0].includes('/operations/'))return operation();if(args[0].includes('/agent-sessions/'))return {...session(),...options.sessionGet?await options.sessionGet('project_a',ref):{}};return options.request?options.request(...args):detail();},...options.api};
 const bridge=options.web?undefined:{getTaskActivity:async()=>({snapshot:snapshot(),readStatus:'READY'}),onTaskActivity:listener=>{listeners.snapshot=listener;return()=>delete listeners.snapshot;},onSupervisedTask:listener=>{listeners.open=listener;return()=>delete listeners.open;},openDocument:async(...args)=>{opened.push(['document',...args]);return{status:'OPEN_REQUESTED'};},...options.bridge};
 const context={...actualFlushers,loadSupervisedTaskNavigation,DiscussionDraftLease,conversationIdFor,fileConversationBasis,associationScope,sameDiscussionScope,exactFileSource,directFileSupported,nativeReference,nativeReportArtifact,openNativeReportArtifact,batch:fn=>fn(),originalWorkInputChoice:()=>undefined,selectedDiscussion:()=>state.selectedDiscussion,draftReady:()=>state.draftReady!==false,draftAttachments:()=>[],nativeDraftAttachments:()=>[],activeSession:()=>state.activeSession,workspace:()=>state.workspace,threadScope:()=>state.threadScope,draftPersistence,projectObjects:{activate(){}},deliveryRecovery:{resetView(){}},quickScope:()=>({projectId:state.project,workspaceId:'workspace_b',conversationId:state.conversation}),directFile:()=>state.directFile,nativeFeedbackLease:()=>JSON.stringify([state.project,state.conversation]),showAgentChat(){},uiLocale:()=> 'zh-CN',authoredCopy,authoredErrorCopy,authoredText,sameSupervisedTask,supervisorSnapshot,historyDirectoryRefresh:new HistoryDirectoryRefresh(async()=>{}),setActivityReadStatus:()=>{},sameNativeReference,nativeBridge:bridge,api,navigation,cleanup,activityError:()=>state.error,project:()=>({id:state.project}),activityOpen:()=>state.open,activityDetail:()=>state.detail,
  setNativeMenu:set('menu'),setActivityOpen:set('open'),setActivityDetail:set('detail'),setActivitySnapshot:set('snapshot'),setActivityError:set('error'),setActivityLoading:set('loading'),
  setBusy:set('busy'),busy:()=>state.busy,setError:set('globalError'),setNotice:set('notice'),message:value=>value instanceof Error?value.message:String(value),
  agentSessionsApi:{get:async(...args)=>options.sessionGet?options.sessionGet(...args):({project_id:args[0],session_ref:args[1],generation:3})},
  selectProject:async selected=>{navigation.begin();state.project=selected.id;},useOriginalSession:(...args)=>opened.push(['original',...args]),showOriginalSessionManager:()=>opened.push(['manager']),
  discoveryApi:{get:async(...args)=>options.discoveryGet?options.discoveryGet(...args):({project_id:args[0],id:args[1]})},openDiscovery:async value=>opened.push(['discovery',value]),openProjectArtifact:(...args)=>opened.push(['artifact',...args]),
  globalSupervisor:{refresh:async()=>options.refresh?options.refresh():snapshot()},onCleanup:fn=>cleanup.push(fn),setTimeout:(fn,ms)=>{const id=++timer;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id),
  conversationId:()=>state.conversation,setNativeMessages:set('messages'),nativeMessages:()=>state.messages,
  localStorage,saveNativeHistory,readNativeText,readComposerDraft,windowMode:()=> 'workbench',prompt:()=>state.prompt,setUnboundRecoveryText:()=>{},
  adoptDirectSource:async value=>{opened.push(['source',value]);state.directFile={file:{source_ref:value,current:true,encoding:'binary',byte_length:0,content:''}};},
 };
 context.setProject=value=>{state.project=value?.id;};context.selectOriginalSession=value=>{state.original=value;if(value)opened.push(['original',value]);};
 for(const name of ['DraftReady','SelectedDiscussion','ActiveOperation','ActiveSession','FeedbackRecords','DraftAttachments','Materials','Selection','DirectFile','DirectWrite','DirectFileRequired','DirectResults','NativeFileFeedbacks','DirectFeedbackRefs','Inspector','NativeOpen','SkillDrawer','SessionManagementOpen','ToolMode','HistoryReloadToken','Prompt','NativeDraftAttachments','ThreadAccess','DraftOnlyTitle','ThreadScope','ThreadMessages','ThreadCursor','ThreadLoading','ThreadError','ThreadResultSelection','ReadingDeliveryReady','DeliveryDirectories','Feed','Workspaces','Workspace','SupervisorReturn','SupervisorOpen','OpenedFeedbackFile','SuspendedFeedbackGeneration'])if(!context['set'+name])context['set'+name]=set(name[0].toLowerCase()+name.slice(1));
 for(const name of ['stopObserver','loadSourceContext','refreshNative','refreshObjects','recoverDraft'])if(!context[name])context[name]=()=>{};
 state.historyReloadToken=0;if(options.flushWorkbenchDrafts)context.flushWorkbenchDrafts=options.flushWorkbenchDrafts;
 const functionNodes=component.body.statements.filter(node=>ts.isFunctionDeclaration(node)&&(/TaskActivity|ActivityTask|SupervisedTask/.test(node.name?.text??'')||['saveComposerDraft','currentDiscussionDraft','installDiscussionTarget','clearThread','clearProjectFeed','openFeedbackDocument','ownsOpenedFeedbackFile','action','nativeRecord','persistNativeHistory','nativeArtifactSource','openNativeArtifact'].includes(node.name?.text)));
 const code=ts.transpileModule(functionNodes.map(node=>node.getText(parsed)).join('\n')+(options.realProjectFlow?'\n'+actual('selectProject')+'\n'+actual('selectWorkspace'):'')+'\nconst mount=('+watch.getText(parsed)+');\nconst close=('+closeCallback+');\nconst openAll=('+openAllCallback+');\nconst notifySnapshot=('+snapshotCallback+');',{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
 const controller=new Function(...Object.keys(context),'let activityRequest=0,supervisorDiscussionEpoch=0,studioDisposed=false,feedbackRefreshGeneration=0,discussionDraftLease,nativeDraftOwner,threadReadAbort,threadReadGeneration=0,threadResultGeneration=0,readingDirectoryTurn=0,openingFeedbackFile,directFileGeneration=0;\n'+code+'\nreturn{showTaskActivity,openActivityTask,enterSupervisedTask,acceptTaskActivity,nativeRecord,persistNativeHistory,openNativeArtifact,mount,close,openAll,notifySnapshot,dispose(){studioDisposed=true;for(const release of cleanup)release();}};')(...Object.values(context),cleanup);
 return {...controller,state,localStorage,calls,opened,timers,listeners,navigation,originalDraft,navigate(pid){navigation.begin();state.project=pid;},cleanup};
}

test('task identity ignores insertion order while keeping every project/operation/generation boundary',()=>{
 const reordered={generation:3,operation_id:'operation_a',object_id:ref.id,project_id:'project_a',kind:'original_session'};
 assert.equal(sameSupervisedTask(reordered,key()),true);
 for(const extra of [{generation:4},{project_id:'project_b'},{operation_id:'other'},{object_id:'other'},{kind:'native_message'}])assert.equal(sameSupervisedTask({...reordered,...extra},key()),false);
 assert.equal(sameSupervisedTask(null,key()),false);assert.equal(sameSupervisedTask({kind:'execution',object_id:'one',generation:0},key({kind:'execution',object_id:'one',generation:0,project_id:null,operation_id:null})),true);
});

test('actual task detail GET accepts reordered exact key and rejects foreign scope without writes',async()=>{
 const h=harness({request:async()=>detail(task({key:{generation:3,operation_id:'operation_a',kind:'original_session',object_id:ref.id,project_id:'project_a'}}))});
 await h.openActivityTask(task());assert.equal(h.state.detail.answer_text,'原任务回答');assert.equal(h.calls[0][1],'GET');assert.equal(h.calls[0][2],undefined);
 const query=new URLSearchParams(h.calls[0][0].split('?')[1]);for(const [field,value]of Object.entries(key()))assert.equal(query.get(field),String(value));
 const bad=harness({request:async()=>detail(task({key:key({generation:4})}))});await bad.openActivityTask(task());assert.equal(bad.state.detail,undefined);assert.match(authoredText(bad.state.error),/未重发/);
});

test('closing or explicitly reopening list invalidates late task detail and never changes composer',async()=>{
 for(const close of ['close','showTaskActivity']){
  const gate=deferred(),h=harness({request:()=>gate.promise});const pending=h.openActivityTask(task());assert.equal(h.state.loading,true);h[close]();h.showTaskActivity();gate.resolve(detail());await pending;
  assert.equal(h.state.detail,undefined,close+' must keep the explicitly chosen list');assert.equal(h.state.prompt,h.originalDraft);assert.equal(h.state.loading,false);
 }
});

test('actual native list event never fetches detail; known exact key alone opens its detail and unknown generation stays list',async()=>{
 const h=harness();h.mount();await tick();h.listeners.open(null);await tick();assert.equal(h.calls.length,0);assert.equal(h.state.open,true);assert.equal(h.state.detail,undefined);
 h.listeners.open({...key(),generation:4});await tick();assert.equal(h.calls.length,0);assert.match(authoredText(h.state.error),/引用已变化/);
 h.listeners.open({generation:3,kind:'original_session',project_id:'project_a',operation_id:'operation_a',object_id:ref.id});await tick();await tick();assert.equal(h.calls.length,1);assert.equal(h.state.detail.key.operation_id,'operation_a');h.dispose();
});

test('native old refresh cannot reopen detail after list/null intent and disposed callbacks stay silent',async()=>{
 const gate=deferred(),h=harness({refresh:()=>gate.promise});h.mount();await tick();h.listeners.open(key());h.listeners.open(null);gate.resolve(snapshot());await tick();await tick();assert.equal(h.calls.length,0);assert.equal(h.state.detail,undefined);h.dispose();
 const failure=deferred(),stopped=harness({refresh:()=>failure.promise});stopped.mount();await tick();stopped.listeners.open(key());stopped.dispose();const old=stopped.state.error;failure.reject(Error('late read error'));await tick();assert.equal(stopped.state.error,old);
});

test('native event subscription owns no second renderer poll; web owns one readonly refresh timer and cleans it',async()=>{
 const native=harness();native.mount();await tick();assert.equal(native.timers.size,0,'TaskActivityHost owns native polling');native.dispose();assert.deepEqual(Object.keys(native.listeners),[]);
 const web=harness({web:true});web.mount();await tick();assert.equal(web.timers.size,1);const [timerId,timer]=[...web.timers.entries()][0];assert.equal(timer.ms,30000);web.timers.delete(timerId);await timer.fn();await tick();assert.equal(web.calls.length,0);web.dispose();assert.equal(web.timers.size,0);
});

test('enter original task respects late user navigation, exact generation and callback failure preserves task details',async()=>{
 const gate=deferred(),h=harness({sessionGet:()=>gate.promise});h.state.open=true;h.state.detail=detail();const pending=h.enterSupervisedTask(h.state.detail);h.navigate('project_b');gate.resolve({project_id:'project_a',session_ref:ref,generation:3});await pending;assert.equal(h.opened.length,0,'late origin read cannot select a session into another current project');assert.equal(h.state.prompt,h.originalDraft);
 const wrong=harness({sessionGet:async()=>({project_id:'project_a',session_ref:ref,generation:4})});wrong.state.open=true;wrong.state.detail=detail();await wrong.enterSupervisedTask(wrong.state.detail);assert.equal(wrong.opened.length,0);assert.equal(wrong.state.open,true);assert.ok(wrong.state.detail);assert.ok(authoredText(wrong.state.error));
 const broken=harness({sessionGet:async()=>{throw Error('read unavailable');}});broken.state.open=true;broken.state.detail=detail();await broken.enterSupervisedTask(broken.state.detail);assert.ok(broken.state.detail);assert.equal(broken.state.open,true);assert.equal(broken.state.prompt,broken.originalDraft);assert.match(authoredText(broken.state.error),/read unavailable/);
});

test('artifact action uses its actual source project/reference and native histories remain per conversation',async()=>{
 const values=new Map(),storage={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)},h=harness({storage,project:'project_b'});
 const source={kind:'local_file',project_id:'project_b',workspace_id:'workspace_b',binding_revision:1,binding_sha256:hash,path:'交付/工程报告.pptx',revision:7,sha256:'b'.repeat(64)};
 await h.openNativeArtifact({filename:'display-only.pptx',file_source_ref:source});assert.deepEqual(h.opened.find(item=>item[0]==='document'),['document','project_b',source,{scope:{projectId:'project_b',workspaceId:'workspace_b',conversationId:'topic_a'}}]);
 h.nativeRecord('topic A',{operation_id:'op_a',status:'RETURNED',conversation_id:'topic_a'});h.persistNativeHistory();h.state.conversation='topic_b';h.state.messages=[];
 h.nativeRecord('late A',{operation_id:'late_a',status:'RETURNED',conversation_id:'topic_a'});assert.deepEqual(h.state.messages,[]);h.nativeRecord('topic B',{operation_id:'op_b',status:'RETURNED',conversation_id:'topic_b'});h.persistNativeHistory();
 assert.deepEqual(readNativeHistory(storage,'topic_a').map(item=>item.operation_id),['op_a']);assert.deepEqual(readNativeHistory(storage,'topic_b').map(item=>item.operation_id),['op_b']);assert.equal(h.state.prompt,h.originalDraft);
});

// Full production App compile/first paint; only large/native child presentation
// boundaries are omitted, as in the existing first-render acceptance test.
function compileComponent(input,name,modules){const js=ts.transpileModule(input,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;let code=babel.transformSync(js,{filename:name+'.jsx',presets:[[solidPreset,{generate:'ssr',hydratable:false}]],configFile:false,babelrc:false}).code;const file=ts.createSourceFile(name+'.mjs',code,ts.ScriptTarget.Latest,true,ts.ScriptKind.JS),bindings={};for(const node of [...file.statements].reverse())if(ts.isImportDeclaration(node)){const imported=modules[node.moduleSpecifier.text];assert.ok(imported,'resolved '+node.moduleSpecifier.text);if(node.importClause?.name)bindings[node.importClause.name.text]=imported.default;if(node.importClause?.namedBindings&&ts.isNamedImports(node.importClause.namedBindings))for(const item of node.importClause.namedBindings.elements)bindings[item.name.text]=imported[item.propertyName?.text??item.name.text];code=code.slice(0,node.getStart(file))+code.slice(node.end);}code=code.replace('export default function '+name,'function '+name).replace(/export function /g,'function ');return new Function(...Object.keys(bindings),'__EEAS_UI_SOURCE_SHA__',code+'\nreturn '+name+';')(...Object.values(bindings),'a'.repeat(64));}
test('actual initial App exposes one closed supervisor entry and keeps task details closed without project or model',async()=>{
 const empty=()=>null,modules={'solid-js':solid,'solid-js/web':web,'@opencode-ai/ui/button':{Button:props=>props.children},'@opencode-ai/ui/icon':{Icon:empty}};
 for(const node of parsed.statements)if(ts.isImportDeclaration(node)){const id=node.moduleSpecifier.text;if(!id.startsWith('./'))continue;try{modules[id]=await import(pathToFileURL(resolve(app,'src',id.slice(2).replace(/\.ts$/,'')+'.ts')).href);}catch(error){if(!['ERR_MODULE_NOT_FOUND','ERR_UNKNOWN_FILE_EXTENSION'].includes(error.code))throw error;modules[id]={default:empty};}}
 modules['../../../packages/branding/icon.svg']={default:pathToFileURL(resolve(app,'../../packages/branding/icon.svg')).href};
 modules['./ui-language']=await import('./fixture-ui-language.mjs');
  modules['./supervisor-command-center.css']={};
 modules['./renderers']={GenericRenderer:empty,registerBuiltins:()=>{}};modules['./StudioPopover']={default:props=>props.trigger};modules['./ObjectDraft']={ObjectDraftBoundary:props=>props.children};
  modules['./SupervisorCommandCenter']={default:compileComponent(await readFile(resolve(app,'src/SupervisorCommandCenter.tsx'),'utf8'),'SupervisorCommandCenter',modules)};

 const before={window:globalThis.window,document:globalThis.document,localStorage:globalThis.localStorage,eeasDesktop:globalThis.eeasDesktop},values=new Map();Object.assign(globalThis,{document:{hidden:false},window:{innerWidth:1440,location:{search:''},matchMedia:()=>({matches:false}),addEventListener(){},removeEventListener(){}},localStorage:{getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)}});delete globalThis.eeasDesktop;
 try{const html=web.renderToString(()=>compileComponent(source,'App',modules)());assert.match(html,/class="as-shell"/);assert.equal((html.match(/class="as-supervisor-command-trigger"/g)??[]).length,1);assert.match(html,/<button[^>]*class="as-supervisor-command-trigger"[^>]*aria-haspopup="dialog"[^>]*aria-expanded="false"/);assert.doesNotMatch(html,/aria-label="全局任务动态"/);assert.match(html,/<div class="as-global-activity" hidden/);assert.doesNotMatch(html,/原任务回答/);}finally{await new Promise(done=>setTimeout(done,0));for(const[name,value]of Object.entries(before))if(value===undefined)delete globalThis[name];else globalThis[name]=value;}
});


const fixtureScript=String.raw`
import json,sys,tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from apps.api.main import create_app
from apps.api.config import Settings
from packages.domain.tasks import now
with tempfile.TemporaryDirectory(prefix='task-activity-real-api-') as folder:
 app=create_app(Settings(data_dir=Path(folder),port=8787,worker_enabled=False))
 with TestClient(app,base_url='http://127.0.0.1:8787') as client:
  token=client.get('/api/bootstrap',headers={'X-EEAS-Bootstrap':'1'}).json()['csrf_token']
  client.headers.update({'Origin':'http://127.0.0.1:8787','X-EEAS-CSRF':token})
  store=app.state.store
  pid=store.create_project('真实任务详情夹具','原创SQLite记录，不启动模型')['id']
  with store.connect() as connection:
   for ident,status in [('first','SUCCEEDED'),('second','FAILED')]:
    connection.execute('INSERT INTO agent_executions (id,project_id,operation_id,request_hash,runtime,workspace,execution_status,verification_status,session_id,model_profile,sdk_version,request,result,created_at,ended_at,cancel_requested) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
     ('execution_'+ident,pid,'operation_'+ident,'a'*64,'opencode-http','owned-fixture',status,'NOT_RUN',None,'configured-fixture','1.19.0','{}',json.dumps({'candidate_json':{'summary':'SQLite原回答 '+ident}}),now(),now(),0))
  checked=client.post('/api/supervisor/check')
  for line in sys.stdin:
   try:
    cmd=json.loads(line)
    if cmd.get('fixture')=='setup':
     response={'status':checked.status_code,'body':checked.json()}
    else:
     r=client.request(cmd.get('method','GET'),cmd['path'],json=cmd.get('body'),headers=cmd.get('headers') or {})
     response={'status':r.status_code,'body':r.json()}
    print(json.dumps(response,ensure_ascii=False),flush=True)
   except Exception as error:print(json.dumps({'fixture_error':type(error).__name__}),flush=True)
`;
function realFixture(t){
 const root=resolve(app,'../..'),child=spawn(resolve(root,'.venv/bin/python'),['-c',fixtureScript],{cwd:root,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},stdio:['pipe','pipe','pipe']}),waiting=[],calls=[];let stderr='';
 const lines=readline.createInterface({input:child.stdout});lines.on('line',line=>{const item=waiting.shift();if(item)try{item.resolve(JSON.parse(line));}catch(error){item.reject(error);}});child.stderr.on('data',value=>stderr=(stderr+value).slice(-5000));child.on('error',error=>{for(const item of waiting.splice(0))item.reject(error);});child.on('exit',code=>{for(const item of waiting.splice(0))item.reject(Error('real API exited '+code+': '+stderr));});
 t.after(async()=>{child.stdin.end();await new Promise(done=>child.exitCode!==null?done():child.once('exit',done));lines.close();});
 const raw=command=>new Promise((resolve,reject)=>{waiting.push({resolve,reject});child.stdin.write(JSON.stringify(command)+'\n');});
 const client=new ApiClient(async(path,options)=>{calls.push({path,method:options?.method??'GET'});const result=await raw({path,method:options?.method??'GET',headers:options?.headers,body:options?.body?JSON.parse(options.body):undefined});return new Response(JSON.stringify(result.body),{status:result.status});});
 return{raw,client,calls};
}
test('actual App task callback reads exact independent task details from real create_app and SQLite without mutation/retry',async t=>{
 const fixture=realFixture(t),prepared=await fixture.raw({fixture:'setup'});assert.equal(prepared.status,200,JSON.stringify(prepared.body));const observed=supervisorSnapshot(prepared.body);assert.equal(observed.tasks.length,2);const first=observed.tasks.find(task=>task.key.object_id==='execution_first'),second=observed.tasks.find(task=>task.key.object_id==='execution_second');
 assert.equal(first.classification,'EXECUTION_RETURNED');assert.equal(first.validation_status,'NOT_RUN');assert.equal(second.classification,'EXECUTION_ERROR');
 const h=harness({apiClient:fixture.client});await h.openActivityTask(first);assert.equal(h.state.detail.answer_text,'SQLite原回答 first');assert.ok(sameSupervisedTask(h.state.detail.key,first.key));assert.equal(h.state.detail.automatic_retry,false);
 await h.openActivityTask(second);assert.equal(h.state.detail.answer_text,'SQLite原回答 second');assert.equal(h.state.detail.task.classification,'EXECUTION_ERROR');
 await h.openActivityTask({...first,key:{...first.key,generation:first.key.generation+1}});assert.equal(h.state.detail,undefined);assert.match(authoredText(h.state.error),/未重发/);
 assert.ok(fixture.calls.every(call=>call.method==='GET'));assert.equal(fixture.calls.filter(call=>call.path.startsWith('/api/supervisor/task-detail?')).length,3);assert.equal(h.state.prompt,h.originalDraft);
});


test('actual task installation adopts its exact project and original session after draft barriers',async()=>{
 const h=harness({realProjectFlow:true,project:'project_b'});h.state.open=true;h.state.detail=detail();await h.enterSupervisedTask(h.state.detail);assert.equal(h.state.project,'project_a',authoredText(h.state.error));assert.deepEqual(readComposerDraft(h.localStorage,'project_b'),{text:h.originalDraft,feedback_refs:[]});assert.equal(h.opened.filter(value=>value[0]==='original').length,1,'the actual installDiscussionTarget adopts the original session after exact reads');assert.equal(h.state.open,false);
});


test('task installation still rejects a later independent navigation while saved drafts are pending and close during enter keeps draft',async()=>{
 const gate=deferred(),h=harness({realProjectFlow:true,flushWorkbenchDrafts:()=>gate.promise,project:'project_b'});h.state.open=true;h.state.detail=detail();const entering=h.enterSupervisedTask(h.state.detail);await tick();h.navigate('project_b');gate.resolve();await entering;assert.equal(h.opened.length,0);assert.equal(h.state.project,'project_b');assert.equal(h.state.prompt,h.originalDraft);
 const read=deferred(),closed=harness({sessionGet:()=>read.promise});closed.state.open=true;closed.state.detail=detail();const pending=closed.enterSupervisedTask(closed.state.detail);closed.close();read.resolve({project_id:'project_a',session_ref:ref,generation:3});await pending;assert.equal(closed.opened.length,0);assert.equal(closed.state.prompt,closed.originalDraft);
});

test('task event format failure preserves known snapshot and does not erase an unrelated task-detail error',async()=>{
 const h=harness();h.mount();await tick();const old=h.state.snapshot;h.listeners.snapshot({invalid:'source'});assert.equal(h.state.snapshot,old);assert.match(authoredText(h.state.error),/任务动态暂未/);h.listeners.snapshot(snapshot());assert.equal(h.state.error,'');h.state.error='原任务引用已变化';h.listeners.snapshot(snapshot());assert.equal(h.state.error,'原任务引用已变化');h.dispose();
});

test('actual settings/ack snapshot callback keeps known task outcome when the optional native projection refresh fails',async()=>{
 const h=harness({bridge:{getTaskActivity:async()=>{throw Error('native read unavailable');}}});h.notifySnapshot(snapshot());await tick();await tick();assert.equal(h.state.snapshot.tasks[0].classification,'EXECUTION_RETURNED');assert.equal(h.state.prompt,h.originalDraft);
});

test('actual supervisor All tasks callback opens the list and invalidates an older detail read without changing the composer',async()=>{
 const gate=deferred(),h=harness({request:()=>gate.promise});const pending=h.openActivityTask(task());assert.equal(h.state.loading,true);h.openAll();assert.equal(h.state.open,true);assert.equal(h.state.loading,false);assert.equal(h.state.detail,undefined);gate.resolve(detail());await pending;assert.equal(h.state.detail,undefined);assert.equal(h.state.prompt,h.originalDraft);assert.equal(h.calls.length,1);assert.equal(h.opened.length,0);
});
