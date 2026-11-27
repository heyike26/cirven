import {authoredCopy,authoredErrorCopy} from '../src/authored-ui-copy.ts';
/** UNIT actual production callbacks + Solid signals/effects + draft helpers.
 * API/connector responses below are explicit boundary fixtures, not a browser,
 * filesystem binding, native desktop, database, or model qualification.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {resolve, dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import ts from 'typescript';
import {createRoot,createSignal,createEffect,batch} from 'solid-js/dist/solid.js';
import {NavigationOwner} from '../src/registry.ts';
import {ComposerDraftPersistence,readComposerDraft,draftKey} from '../src/composer-draft.ts';
import {readNativeText,saveNativeText,readNativeDraft,saveNativeDraft,nativeTextKey} from '../src/native-collaboration.ts';
import {FolderConnection} from '../src/project-folder.ts';

const app=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const source=await readFile(resolve(app,'src/App.tsx'),'utf8');
const parsed=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const component=parsed.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='App');
const names=['clearThread','clearProjectFeed','currentDiscussionDraft','action','saveComposerDraft','restoreComposerDraft','restoreNativeDraft','selectProject','selectWorkspace',
  'folderConnector','openFolderDialog','applyFolderConnection','bindFolder','recoverFolderConnection','appendUnboundDraft'];
const actual=name=>{const node=component.body.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text===name);assert.ok(node,'actual App callback '+name);return node.getText(parsed);};
const effects=component.body.statements.filter(node=>ts.isExpressionStatement(node)&&ts.isCallExpression(node.expression)&&
  node.expression.expression.getText(parsed)==='createEffect'&&
  (node.getText(parsed).includes('saveComposerDraft()')||node.getText(parsed).includes('nativeDraftOwner===')));
assert.equal(effects.length,2,'actual composer/native persistence effects');
const code=ts.transpileModule(names.map(actual).join('\n')+'\n'+effects.map(node=>node.getText(parsed)).join('\n')+
  '\nreturn {'+names.join(',')+',epoch:()=>folderEpoch};',
  {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return{promise,resolve,reject};};
const project=id=>({id,name:'工程 '+id,description:''});
const workspace=pid=>({id:(pid==='p_a'?'a':'b').repeat(64),project_id:pid,revision:1,root:'D:\\工作\\'+pid,
  binding_sha256:'c'.repeat(64),allowed_write_paths:[],native_location:{directory:'D:\\工作\\'+pid}});
function connected(pid='p_a',extra={}){return{operation_id:'connection_'+pid,project_operation_id:'connection_'+pid+'_project',
  binding_operation_id:'connection_'+pid+'_binding',stage:'CONNECTED',request:{target:'NEW',name:project(pid).name,path:workspace(pid).root,writable:false,current_project_id:null},
  project:project(pid),workspace:workspace(pid),current_binding_status:'AVAILABLE',local_persistence_status:'SAVED',...extra};}
function storage(){const values=new Map();return{values,fail:()=>false,drop:()=>false,getItem:key=>values.get(key)??null,
  setItem(key,value){if(this.fail(key,value))throw Error('UNIT storage write failed');if(!this.drop(key,value))values.set(key,value);},removeItem:key=>values.delete(key)};}

function harness(t,options={}){
 const local=options.storage??storage(),calls=[],navigation=new NavigationOwner();
 const initial={project:options.project,projects:options.project?[options.project]:[],workspace:undefined,workspaces:[],
  folderDialog:false,folderTarget:'NEW',folderProject:undefined,folderConnection:undefined,folderLoading:false,
  folderPath:'D:\\工作\\p_a',projectName:'工程 p_a',writeScope:false,prompt:options.text??'未绑定的正文 😀\r\n不要丢失 ',
  unboundRecoveryText:undefined,busy:'',error:'',notice:'',draftReady:false,draftAttachments:[],nativeDraftAttachments:[],
  activeSession:undefined,activeOperation:undefined,sidebarOpen:false,nativeOpen:false,nativeConfig:undefined,
  directResults:[],directFile:undefined,directWrite:false,nativeFileFeedbacks:[],directFeedbackRefs:{},
  feedbackRecords:[],discoverySessions:[],inspector:undefined,books:[],harnesses:[],reports:[],proposals:[],
  selection:[],materials:[],feed:[],windowMode:options.mode??'workbench'};
 Object.assign(initial,{selectedDiscussion:undefined,threadScope:undefined,threadAccess:'ready',draftOnlyTitle:'',threadMessages:[],threadCursor:null,threadLoading:false,threadError:'',threadResultSelection:undefined,supervisorReturn:undefined,historyActiveId:undefined,readingDeliveryReady:undefined,deliveryDirectories:{},toolMode:'direct'});
 const state={},context={authoredCopy,authoredErrorCopy,originalWorkInputChoice:()=>undefined,projectObjects:{activate(){}},deliveryRecovery:{resetView(){}}};
 for(const[name,value]of Object.entries(initial)){
  const[get,set]=createSignal(value);Object.defineProperty(state,name,{enumerable:true,get,set});
  context[name]=get;context['set'+name[0].toUpperCase()+name.slice(1)]=set;
 }
 const result=connected();
 const connector={current:()=>result,
  refresh:async()=>{calls.push(['refresh']);return options.refresh?options.refresh():result;},
  prepare:async(...args)=>{calls.push(['prepare',...args]);return options.prepare?options.prepare(...args):result;},
  run:async id=>{calls.push(['run',id]);return options.run?options.run(id):result;},
  inspect:async id=>{calls.push(['inspect',id]);return options.inspect?options.inspect(id):result;},
  ...options.connector};
 const api={workspaces:async(...args)=>{calls.push(['workspaces',...args]);return options.workspaces?options.workspaces(...args):[];},
  ...Object.fromEntries(['taskbooks','harnesses','reports','proposals'].map(name=>[name,async()=>[]]))};
 const draftPersistence=new ComposerDraftPersistence(local);
 if(options.project){const lease=draftPersistence.begin(options.project.id,undefined,{text:initial.prompt,feedback_refs:[]});draftPersistence.initialize(lease);draftPersistence.hydrated(lease);state.draftReady=true;}
 Object.assign(context,{localStorage:local,api,navigation,draftPersistence,FolderConnection,readComposerDraft,
  saveNativeText,readNativeText,readNativeDraft,saveNativeDraft,batch,createEffect,
  message:error=>error instanceof Error?error.message:String(error),
  refreshObjects:async(...args)=>{calls.push(['refreshObjects',...args]);return options.refreshObjects?.(...args);},
  refreshFeedback:async()=>{},stopObserver:()=>{},selectOriginalSession:()=>{},restoreOriginalSession:async()=>{},
  loadSourceContext:()=>{},restoreDirectHistory:async()=>{},restoreSelectedDirectFile:async()=>{},refreshNative:async()=>{},
  observeOperation:(...args)=>calls.push(['observeOperation',...args])});
 let dispose;
 const controller=createRoot(release=>{dispose=release;return new Function(...Object.keys(context),'__connector',
   'let folderEpoch=0,folderConnections=__connector,nativeDraftOwner="unbound",feedbackRefreshGeneration=0,discussionDraftLease,threadReadAbort,threadReadGeneration=0,threadResultGeneration=0,readingDirectoryTurn=0;\n'+code)
   (...Object.values(context),connector);});
 t.after(()=>{dispose();navigation.dispose();});
 return{state,local,calls,navigation,controller,connector,draftPersistence};
}

test('actual openFolderDialog refreshes current storage and ignores previous epoch completion or error',async t=>{
 const a=deferred(),b=deferred(),queue=[a,b],h=harness(t,{project:project('old'),refresh:()=>queue.shift().promise});
 h.controller.openFolderDialog();assert.equal(h.state.folderDialog,true);assert.equal(h.state.folderLoading,true);
 assert.equal(h.state.folderConnection,undefined);assert.equal(h.state.folderProject.id,'old');assert.equal(h.state.folderTarget,'CURRENT');
 h.state.folderDialog=false;h.controller.openFolderDialog();
 a.reject(Error('old refresh error'));await tick();assert.equal(h.state.error,'');assert.equal(h.state.folderLoading,true);
 const fresh=connected('p_b');b.resolve(fresh);await tick();assert.equal(h.state.folderConnection,fresh);assert.equal(h.state.folderLoading,false);
});

test('actual late refresh cannot restore a closed folder modal',async t=>{
 const pending=deferred(),h=harness(t,{refresh:()=>pending.promise});h.controller.openFolderDialog();h.state.folderDialog=false;
 pending.resolve(connected());await tick();assert.equal(h.state.folderDialog,false);assert.equal(h.state.folderConnection,undefined);
});

test('actual apply blocks unknown, unchecked and unavailable current bindings without changing project or draft',async t=>{
 for(const value of [connected('p_a',{current_binding_status:'UNAVAILABLE',current_binding_reason:'目录已替换'}),
  connected('p_a',{current_binding_status:'NOT_CHECKED'}),connected('p_a',{stage:'UNKNOWN'})]){
  const h=harness(t,{project:project('old')});h.state.folderDialog=true;
  const text=h.state.prompt;await h.controller.applyFolderConnection(value,h.navigation.capture(),0);
  assert.equal(h.state.project.id,'old');assert.equal(h.state.folderDialog,true);assert.equal(h.state.prompt,text);
  assert.equal(h.calls.some(row=>['workspaces','refreshObjects'].includes(row[0])),false);
 }
});

test('actual binding completion does not navigate after its original navigation owner became stale',async t=>{
 const pending=deferred(),h=harness(t,{run:()=>pending.promise});h.controller.openFolderDialog();await tick();
 const binding=h.controller.bindFolder();await tick();h.navigation.begin();h.state.project=project('user_selected');
 pending.resolve(connected());await binding;assert.equal(h.state.project.id,'user_selected');assert.equal(h.state.folderDialog,true);
});

test('actual old binding completion cannot replace a reopened dialog recovery record',async t=>{
 const pending=deferred();let refresh=0;const fresh=connected('p_b');
 const h=harness(t,{refresh:async()=>++refresh===1?connected():fresh,run:()=>pending.promise});
 h.controller.openFolderDialog();await tick();const binding=h.controller.bindFolder();await tick();
 h.state.folderDialog=false;h.controller.openFolderDialog();await tick();assert.equal(h.state.folderConnection,fresh);
 pending.resolve(connected());await binding;
 assert.equal(h.state.folderConnection,fresh,'old async operation replaced the new dialog record');assert.equal(h.state.folderDialog,true);
});

test('actual available connection respects a newer same-project navigation while project loading awaits',async t=>{
 const pending=deferred(),h=harness(t,{workspaces:()=>pending.promise});h.state.folderDialog=true;
 const opening=h.controller.applyFolderConnection(connected(),h.navigation.capture(),0);await tick();
 assert.equal(h.state.project.id,'p_a');h.navigation.begin();const selected={...workspace('p_a'),id:'d'.repeat(64)};h.state.workspace=selected;
 pending.resolve([workspace('p_a')]);await opening;
 assert.equal(h.state.workspace,selected,'older folder completion replaced a newer same-project workspace');
 assert.equal(h.state.folderDialog,true);
});

test('actual no-project workbench text survives project selection as pure text and remains stored unbound',async t=>{
 const h=harness(t);const text=h.state.prompt;await h.controller.selectProject(project('p_a'));
 assert.equal(h.state.project.id,'p_a');assert.equal(h.state.prompt,text);assert.equal(readNativeText(h.local),text);
 assert.equal(readComposerDraft(h.local,'p_a').text,text);assert.deepEqual(readComposerDraft(h.local,'p_a').feedback_refs,[]);
 assert.deepEqual(h.state.nativeDraftAttachments,[]);assert.equal(h.state.unboundRecoveryText,undefined);
});

test('actual existing target draft wins and original unbound text can be explicitly appended once',async t=>{
 const local=storage(),target='目标工程原有草稿 😀\r\n';local.setItem(draftKey('p_a'),JSON.stringify({text:target,feedback_refs:[]}));
 const h=harness(t,{storage:local});const unbound=h.state.prompt;await h.controller.selectProject(project('p_a'));
 assert.equal(h.state.prompt,target);assert.equal(h.state.unboundRecoveryText,unbound);assert.equal(readNativeText(local),unbound);
 await h.controller.appendUnboundDraft();assert.equal(h.state.prompt,target+'\n\n'+unbound);assert.equal(h.state.unboundRecoveryText,undefined);
 assert.equal(readComposerDraft(local,'p_a').text,h.state.prompt);await h.controller.appendUnboundDraft();assert.equal(h.state.prompt,target+'\n\n'+unbound);
});

test('actual companion carry preserves text without moving the unbound page attachments',async t=>{
 const h=harness(t,{mode:'companion'}),text=h.state.prompt;
 const attachment={context_ref:{id:'unbound_context',revision:1,sha256:'a'.repeat(64)},label:'原页面',comment:'未发送批注',regions:[]};
 h.state.nativeDraftAttachments=[attachment];assert.deepEqual(readNativeDraft(h.local),[attachment]);
 await h.controller.selectProject(project('p_a'));
 assert.equal(h.state.prompt,text);assert.equal(readNativeText(h.local,'p_a'),text);
 assert.deepEqual(h.state.nativeDraftAttachments,[]);assert.deepEqual(readNativeDraft(h.local,'p_a'),[]);
 assert.deepEqual(readNativeDraft(h.local),[attachment]);
});

test('actual companion keeps an existing target page draft and offers the unbound text separately',async t=>{
 const local=storage(),target='原项目已有文字';saveNativeText(local,'p_a',target);
 const attachment={context_ref:{id:'target_context',revision:1,sha256:'b'.repeat(64)},label:'目标原页面',comment:'目标批注',regions:[]};
 saveNativeDraft(local,'p_a',[attachment]);const h=harness(t,{mode:'companion',storage:local}),unbound=h.state.prompt;
 await h.controller.selectProject(project('p_a'));
 assert.equal(h.state.prompt,target);assert.equal(h.state.unboundRecoveryText,unbound);assert.deepEqual(h.state.nativeDraftAttachments,[attachment]);
 assert.equal(readNativeText(local,'p_a'),target);assert.equal(readNativeText(local),unbound);
 await h.controller.appendUnboundDraft();assert.equal(readNativeText(local,'p_a'),target+'\n\n'+unbound);
 assert.deepEqual(readNativeDraft(local,'p_a'),[attachment]);
});

test('actual target page annotations without text still count as an existing draft, not an empty target',async t=>{
 const local=storage(),attachment={context_ref:{id:'target_page',revision:1,sha256:'c'.repeat(64)},label:'目标页面',comment:'待补充文字的圈选',regions:[]};
 saveNativeDraft(local,'p_a',[attachment]);const h=harness(t,{mode:'companion',storage:local}),unbound=h.state.prompt;
 await h.controller.selectProject(project('p_a'));
 assert.equal(h.state.prompt,'','unbound text was silently combined with another target page draft');
 assert.equal(h.state.unboundRecoveryText,unbound);assert.deepEqual(h.state.nativeDraftAttachments,[attachment]);
 assert.equal(readNativeText(local,'p_a'),'');
});

test('actual unbound persistence failure blocks project navigation before changing its scope or modal',async t=>{
 const h=harness(t),text=h.state.prompt;h.state.folderDialog=true;h.local.fail=key=>key===nativeTextKey();
 await assert.rejects(h.controller.selectProject(project('p_a')),/storage write failed/);
 assert.equal(h.state.project,undefined);assert.equal(h.state.prompt,text);assert.equal(h.state.folderDialog,true);
 assert.equal(h.calls.some(row=>row[0]==='workspaces'),false);
});

test('actual manual append rolls back on save failure and retains both original drafts',async t=>{
 const h=harness(t,{project:project('p_a'),text:'目标已有正文'});h.state.unboundRecoveryText='未绑定的旧意见';
 h.local.fail=key=>key===draftKey('p_a');await h.controller.appendUnboundDraft();
 assert.equal(h.state.prompt,'目标已有正文');assert.equal(h.state.unboundRecoveryText,'未绑定的旧意见');assert.match(h.state.error,/storage write failed/);
});

test('actual inspect never resumes writes; explicitly opening the confirmed connection selects its original project',async t=>{
 const value=connected(),h=harness(t,{project:project('old'),inspect:async()=>value,run:async()=>value});
 h.state.folderDialog=true;h.state.folderConnection={...value,stage:'UNKNOWN'};
 await h.controller.recoverFolderConnection(false);assert.equal(h.state.project.id,'old');assert.equal(h.state.folderConnection,value);
 assert.equal(h.calls.some(row=>row[0]==='run'),false);assert.equal(h.state.folderDialog,true);
 await h.controller.recoverFolderConnection(true);assert.equal(h.state.project.id,'p_a');assert.equal(h.state.workspace.id,value.workspace.id);
 assert.equal(h.state.folderDialog,false);assert.match(h.state.notice,/已连接/);
});
