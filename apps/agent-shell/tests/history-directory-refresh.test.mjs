import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
import {HistoryDirectoryRefresh} from '../src/history-directory-refresh.ts';
import {parseHistoryPage,mergeHistoryItems,labelHistoryItems} from '../src/conversation-history.ts';
import {projectConversationSidebar} from '../src/conversation-sidebar.ts';
import {supervisorSnapshot} from '../src/global-supervisor.ts';

const source=await readFile(new URL('../src/App.tsx',import.meta.url),'utf8');
const parsed=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const component=parsed.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='App');
const actual=name=>{const n=component.body.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text===name);assert.ok(n,name);return n.getText(parsed);};
const hooks=component.body.statements.filter(n=>ts.isExpressionStatement(n)&&ts.isCallExpression(n.expression)&&n.expression.expression.getText(parsed)==='onMount').map(n=>n.expression.arguments[0].getText(parsed));
const initial=hooks.find(n=>n.includes('historyDirectoryRefresh.dispose()'));
const events=hooks.find(n=>n.includes("window.addEventListener('focus',focused)"));
assert.ok(initial);assert.ok(events);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return{promise,resolve,reject};};
const row=(name='a',pid='project_a')=>({id:'original:'+name.repeat(64),kind:'original',project_id:pid,project_name:pid,title:'讨论 '+name,
 updated_at:'2026-10-27T08:00:00Z',read_only:true,source:{kind:'original',project_id:pid,session_ref:{id:'session_'+name,revision:1,sha256:'d'.repeat(64)},generation:1,provider_profile_ref:null,workspace_ref:null}});
const page=(rows=[],cursor=null)=>({version:1,execution_authorized:false,items:rows,next_cursor:cursor,
 coverage:{model_calls:0,complete:cursor===null,partial:false,has_more:cursor!==null,errors:[]}});
const task=(operation='original_op')=>({key:{kind:'original_session',project_id:'project_a',object_id:'session_a',operation_id:operation,generation:1},
 label:'原讨论',source_status:'RUNNING',observed_at:'2026-10-27T08:00:00Z',actual_error_code:null,execution_returned:null,validation_status:'NOT_RUN',
 wait_reason:null,coverage:'PARTIAL',source_ref:{id:'session_a',revision:1,sha256:'d'.repeat(64)},fresh_for_seconds:120,classification:'RUNNING',observation_status:'CURRENT',last_checked_at:null});
const snapshot=tasks=>({settings:{enabled:true,notifications_enabled:false,interval_seconds:30,revision:1,sha256:'d'.repeat(64)},
 observer:{status:'READY',last_attempt_at:null,last_success_at:null,actual_error_code:null,running:true},tasks,alerts:[],alerts_total:0,coverage:'ONLY_COLLECTED_EXISTING_TASKS'});

function harness(request=async()=>page()){
 const state={items:[],loaded:false,loading:false,loadingMore:false,cursor:null,error:'',partial:false,activityError:'',activityReadStatus:'INITIAL',
   prompt:'人工草稿😀\n未发送',selection:'current_chat',workbench:false,scrollTop:281,mode:'workbench',tab:'conversations',files:true,compact:false,sidebar:false,popover:false,fullscreenFiles:false};
 const original={prompt:state.prompt,selection:state.selection,workbench:state.workbench,scrollTop:state.scrollTop};
 const win=new EventTarget(),doc=new EventTarget();doc.hidden=false;
 const calls=[],cleanup=[];
 const set=name=>value=>{state[name]=typeof value==='function'?value(state[name]):value;};
 const context={HistoryDirectoryRefresh,parseHistoryPage,mergeHistoryItems,labelHistoryItems,supervisorSnapshot,document:doc,window:win,cleanup,
  api:{request:async(...args)=>{calls.push(args);assert.equal(args[1],'GET');assert.equal(args[2],undefined);return request(...args);}},
  localStorage:{getItem:()=>null},uiLocale:()=> 'zh',authoredText:value=>value,
  historyLoading:()=>state.loading,historyLoadingMore:()=>state.loadingMore,historyCursor:()=>state.cursor,
  setHistoryLoading:set('loading'),setHistoryLoadingMore:set('loadingMore'),setHistoryError:set('error'),setHistoryItems:set('items'),
  setHistoryCursor:set('cursor'),setHistoryPartial:set('partial'),setHistoryLoaded:set('loaded'),
  setActivitySnapshot:set('snapshot'),setActivityReadStatus:set('activityReadStatus'),activityError:()=>state.activityError,setActivityError:set('activityError'),
  windowMode:()=>state.mode,historyPopoverOpen:()=>state.popover,leftTab:()=>state.tab,panelVisibility:()=>({files:state.files}),
  compact:()=>state.compact,sidebarOpen:()=>state.sidebar,fullscreenFiles:()=>state.fullscreenFiles,onCleanup:fn=>cleanup.push(fn),
 };
 const text=ts.transpileModule([actual('refreshHistory'),actual('acceptTaskActivity'),actual('historyDirectoryVisible')].join('\n')+
   '\nconst initial=('+initial+'),events=('+events+');',{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
 const h=new Function(...Object.keys(context),`let studioDisposed=false,historyDirectoryRequest=0,historyDirectoryAbort,historySourceUnavailable=false;const historyCursors=new Set();
   const historyDirectoryRefresh=new HistoryDirectoryRefresh(()=>refreshHistory(false,true));\n${text}\n
   return{refreshHistory,acceptTaskActivity,initial,events,historyDirectoryRefresh,
   visibility(){historyDirectoryRefresh.setVisible(historyDirectoryVisible());},dispose(){studioDisposed=true;for(const fn of cleanup)fn();}};`)(...Object.values(context));
 h.visibility();h.events();
 return {...h,state,original,calls,win,doc,assertUnchanged(){for(const [key,value]of Object.entries(original))assert.equal(state[key],value,key);}};
}

test('a new validated task key refreshes once; phases, timestamps, insertion order and duplicate keys do not poll history',async()=>{
 const h=harness();h.initial();h.acceptTaskActivity(snapshot([task()]));await tick();assert.equal(h.calls.length,1);
 h.acceptTaskActivity(snapshot([task(),task('new_operation')]));
 h.acceptTaskActivity(snapshot([task('new_operation'),{...task(),source_status:'RETURNED',classification:'EXECUTION_RETURNED',execution_returned:true,observed_at:'2026-10-27T08:02:00Z'},task()]));
 await tick();assert.equal(h.calls.length,2);assert.equal(h.calls[1][0],'/api/conversations?limit=50');
 h.acceptTaskActivity({tasks:'invalid'});await tick();assert.equal(h.calls.length,2);assert.match(h.state.activityError,/未核对/);h.assertUnchanged();h.dispose();
});

test('a saved empty discussion from another entry appears on focus, including a project absent from the startup catalog',async()=>{
 let externalRows=[row()];const h=harness(async()=>page(externalRows));h.initial();await tick();
 externalRows=[row('b','external_project'),row()];h.win.dispatchEvent(new Event('focus'));h.win.dispatchEvent(new Event('focus'));await tick();
 assert.equal(h.calls.length,2);assert.equal(h.state.items.length,2);
 const groups=projectConversationSidebar(h.state.items,{projectCatalog:[{id:'project_a',name:'旧项目'}]});
 assert.equal(groups.projects.find(p=>p.projectId==='external_project').items[0].id,row('b','external_project').id);
 h.assertUnchanged();h.dispose();
});

test('pagination completes without abort, then one dirty follow-up retains earlier loaded pages and the existing cursor',async()=>{
 const more=deferred();let count=0;
 const h=harness(async path=>{count++;return count===1?page([row()],'page2'):path.includes('cursor=')?more.promise:page([row('b','external_project')]);});
 h.initial();await tick();const reading=h.refreshHistory(true);const pagingSignal=h.calls[1][3];
 h.win.dispatchEvent(new Event('focus'));h.win.dispatchEvent(new Event('focus'));h.acceptTaskActivity(snapshot([]));h.acceptTaskActivity(snapshot([task()]));await tick();
 assert.equal(h.calls.length,2);assert.equal(pagingSignal.aborted,false);
 more.resolve(page([row('c')],'page3'));await reading;await tick();
 assert.equal(h.calls.length,3);assert.equal(h.state.cursor,'page3');assert.deepEqual(new Set(h.state.items.map(x=>x.id)),new Set([row().id,row('b').id,row('c').id]));
 h.assertUnchanged();h.dispose();
});

test('hidden hints wait, reappearance merges focus/visibility and task hints, and a hidden sidebar waits until opened',async()=>{
 const h=harness();h.initial();await tick();h.doc.hidden=true;h.doc.dispatchEvent(new Event('visibilitychange'));
 h.win.dispatchEvent(new Event('focus'));h.acceptTaskActivity(snapshot([]));h.acceptTaskActivity(snapshot([task()]));await tick();assert.equal(h.calls.length,1);
 h.doc.hidden=false;h.doc.dispatchEvent(new Event('visibilitychange'));h.win.dispatchEvent(new Event('focus'));await tick();assert.equal(h.calls.length,2);
 h.state.compact=true;h.visibility();h.win.dispatchEvent(new Event('focus'));await tick();assert.equal(h.calls.length,2);
 h.state.sidebar=true;h.visibility();await tick();assert.equal(h.calls.length,3);
 h.state.mode='companion';h.state.popover=false;h.visibility();h.win.dispatchEvent(new Event('focus'));await tick();assert.equal(h.calls.length,3);
 h.state.popover=true;h.visibility();await tick();assert.equal(h.calls.length,4);h.assertUnchanged();h.dispose();
});

test('changes during an automatic read coalesce into one follow-up, while rejection alone does not retry',async()=>{
 const pending=deferred();let calls=0;
 const refresh=new HistoryDirectoryRefresh(async()=>{calls++;if(calls===1)return pending.promise;if(calls===3)throw Error('unavailable');});
 refresh.setVisible(true);refresh.invalidate();await tick();assert.equal(calls,1);
 for(let i=0;i<20;i++)refresh.invalidate();await tick();assert.equal(calls,1);
 pending.resolve();await tick();assert.equal(calls,2);await tick();assert.equal(calls,2);
 refresh.invalidate();await tick();assert.equal(calls,3);await tick();assert.equal(calls,3);refresh.dispose();
});

test('cleanup cancels queued refreshes/listeners and the actual reader rejects already in-flight late publication',async()=>{
 const pending=deferred();const h=harness(()=>pending.promise);h.initial();const signal=h.calls[0][3];
 h.win.dispatchEvent(new Event('focus'));h.dispose();assert.equal(signal.aborted,true);
 h.win.dispatchEvent(new Event('focus'));h.doc.dispatchEvent(new Event('visibilitychange'));pending.resolve(page([row()]));await tick();
 assert.equal(h.calls.length,1);assert.deepEqual(h.state.items,[]);h.assertUnchanged();
 const pending2=deferred();const late=harness(()=>pending2.promise);late.initial();await tick();late.dispose();pending2.reject(Error('late'));await tick();assert.equal(late.state.error,'');
});

test('the actual reader keeps newer explicit refresh ownership when an older response arrives last',async()=>{
 const old=deferred();let count=0;const h=harness(()=>++count===1?old.promise:Promise.resolve(page([row('b')])));
 h.initial();const signal=h.calls[0][3];await h.refreshHistory();old.resolve(page([row('a')]));await tick();
 assert.equal(signal.aborted,true);assert.deepEqual(h.state.items.map(x=>x.id),[row('b').id]);h.assertUnchanged();h.dispose();
});
