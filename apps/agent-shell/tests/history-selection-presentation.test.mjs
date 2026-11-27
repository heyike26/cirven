import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
import {NavigationOwner} from '../src/registry.ts';
import {DiscussionDraftLease,DiscussionDraftMigrationConflict,discussionDraftKey,migrateConversationDraft,seedConversationDraft,readDraftCopies,chooseDraft} from '../src/discussion-drafts.ts';
import {threadDraftSources,threadOriginalOwner,verifyThreadOriginalOwner} from '../src/conversation-thread.ts';
import {sameReview} from '../src/native-review.ts';
import {authoredCopy,authoredErrorCopy} from '../src/authored-ui-copy.ts';

const source=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8');
const tree=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const app=tree.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='App');
const actualFlushers={};
for(const declaration of tree.statements.filter(ts.isImportDeclaration)){
 const wanted=declaration.importClause?.namedBindings?.elements?.filter(item=>item.name.text.startsWith('flush'))??[];
 if(wanted.length){const name=declaration.moduleSpecifier.text;const loaded=await import(new URL(name.endsWith('.ts')?name:name+'.ts',new URL('../src/App.tsx',import.meta.url)).href);for(const item of wanted)actualFlushers[item.name.text]=loaded[item.propertyName?.text??item.name.text];}
}
const callbacks=['currentDiscussionDraft','openHistoryConversation','installThread','openLegacyHistoryConversation','enterSupervisedTask','adoptCreatedConversation','savedThreadRow','selectDraftChoice',...(app.body.statements.some(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='finishHistorySelection')?['finishHistorySelection']:[])].map(name=>{
 const node=app.body.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text===name);assert.ok(node,name);return node.getText(tree);
}).join('\n');
const js=ts.transpileModule(callbacks,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
const before='native:'+'a'.repeat(64),destination='native:'+'b'.repeat(64);
const originalRef={id:'saved_original',revision:1,sha256:'c'.repeat(64)};
const ws={id:'d'.repeat(64),revision:1,binding_sha256:'e'.repeat(64)};
const original={project_id:'destination_project',session_ref:originalRef,generation:1,connected_owner:false,workspace_ref:ws,provider_profile_ref:null,active_operation_id:null,model_calls_reserved:6,native_turns_started:0};
const draftKey=id=>({kind:'conversation',projectId:null,id});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}

function scene(options={}){
 const native=options.native===true;
 const row={id:destination,title:'普通已存讨论',kind:native?'native':'original',source:native?
  {kind:'native',project_id:'destination_project',request_project_id:'destination_project',workspace_ref:null,conversation_id:'native_chat',last_operation_id:'saved_send',request_sha256:'f'.repeat(64)}:
  {kind:'original',project_id:'destination_project',workspace_ref:ws,session_ref:originalRef,generation:1,provider_profile_ref:null}};
 const scope={conversation_ref:{id:destination,revision:1},project_id:'destination_project',metadata_source:{kind:'conversation'},
  sources:[{id:destination,identity:native?{kind:'native',project_id:'destination_project',conversation_id:'native_chat'}:{kind:'original',project_id:'destination_project',session_id:originalRef.id},original_source:native?null:row.source}],
  next_message:{conversation_id:'next_chat'}};
 const page={messages:[{id:'stored_question',text:'原问题'}, {id:'stored_answer',text:'原答案和工具记录'}],nextCursor:null};
 const state={prompt:'当前尚未发送的草稿😀',attachments:[],selection:{key:draftKey(before),conversationId:'before'},scope:undefined,
  project:{id:'before_project'},workspace:undefined,activityOpen:true,activityLoading:false,activityDetail:{key:'old_task'},
  supervisorOpen:true,supervisorReturn:{key:'old_return'},errors:[],saved:[],recovery:[],calls:[],presentations:[],messages:[]};
 const data=new Map(),storage={getItem:key=>data.get(key)??null,setItem:(key,value)=>{if(options.pointerFailure&&key==='eeas-current-thread-v1')throw Error('storage quota');data.set(key,value);},removeItem:key=>data.delete(key)};
 new DiscussionDraftLease(storage,draftKey(before)).save({text:state.prompt,attachments:[]});
 new DiscussionDraftLease(storage,draftKey(destination)).save({text:'目的会话的维护草稿',attachments:[]});
 if(options.migrationConflict)new DiscussionDraftLease(storage,{kind:'original',projectId:'destination_project',id:originalRef.id}).save({text:'另一个来源的人工草稿',attachments:[]});
 if(options.thirdDraft){
  const key={kind:'native',projectId:'destination_project',id:'another_saved_native'};
  scope.sources.push({id:'native:'+'f'.repeat(64),identity:{kind:'native',project_id:key.projectId,conversation_id:key.id},original_source:null});
  new DiscussionDraftLease(storage,key).save({text:'第三份独立输入',attachments:[]});
 }
 const navigation=new NavigationOwner();
 const setter=name=>value=>{state[name]=typeof value==='function'?value(state[name]):value;};
 const bindings={...actualFlushers,threadOriginalOwner,verifyThreadOriginalOwner,originalWorkInputChoice:()=>undefined,conversationReadingState:()=>undefined,enterConversationReading:()=>{},NavigationOwner,navigation,DiscussionDraftLease,DiscussionDraftMigrationConflict,discussionDraftKey,migrateConversationDraft,seedConversationDraft,threadDraftSources,sameReview,authoredCopy,authoredErrorCopy,
  historyItems:()=>options.missingHistory?[]:[row],creationClient:{pending:options.adoption?{request:{operation_id:'creation',project_id:'destination_project'},receipt:{operation_id:'creation',conversation_ref:scope.conversation_ref}}:options.seedConflict?{receipt:{operation_id:'creation'}}:undefined,acknowledge:async()=>{if(options.acknowledge)return options.acknowledge();}},
  threadClient:{scope:async()=>scope,select:async()=>{state.calls.push('selection metadata');return scope;},history:async()=>options.historyRead?options.historyRead():page},
  nativeBridge:options.nativeBridge,supervisorClient:{getSnapshot:async()=>{}},uiLocale:()=> 'zh',refreshHistory:async()=>state.calls.push('history read'),
  project:()=>state.project,projects:()=>[state.project],allProjects:()=>[state.project],userProjects:values=>values,
  api:{projects:async()=>[{id:'destination_project'}],workspaces:async()=>options.workspaceMismatch?[]:[ws]},
  agentSessionsApi:{get:async()=>{state.calls.push('saved owner GET');return original;}},workspace:()=>state.workspace,
  saveComposerDraft:()=>{if(options.saveFailure)throw Error('draft save failed');new DiscussionDraftLease(storage,state.selection.key).save({text:state.prompt,attachments:state.attachments});state.saved.push({key:state.selection.key,text:state.prompt});},
  flushWorkbenchDrafts:async()=>{if(options.flushFailure)throw Error('draft flush failed');if(options.flush)return options.flush();},
  selectedDiscussion:()=>state.selection,activeSession:()=>undefined,
  conversationId:()=> 'before',originalSession:()=>state.original,prompt:()=>state.prompt,nativeDraftAttachments:()=>state.attachments,
  localStorage:storage,readConversationAgentMode:(_storage,_id,fallback)=>fallback,originalConversationMode:()=> 'build',
  recoverDraft:(...args)=>state.recovery.push(args),draftRecovery:()=>state.draftRecovery,setDraftRecovery:setter('draftRecovery'),
  draftRecoveryBusy:()=>state.draftRecoveryBusy,setDraftRecoveryBusy:setter('draftRecoveryBusy'),
  setDraftRecoveryError:setter('draftRecoveryError'),setDraftVersion:()=>{},chooseDraft,
  draftPersistence:{pause:()=>{}},stopObserver:()=>{},batch:callback=>callback(),projectObjects:{activate:()=>{}},
  selectOriginalSession:value=>state.original=value,clearProjectFeed:()=>{},nativeMessagePending:()=>false,nativeDiscussionInput:()=> 'Exact saved question',nativeReplyText:()=> 'Exact saved preview result',
  showAgentChat:()=>state.presentations.push('chat'),presentWorkspace:kind=>state.presentations.push(kind),compact:()=>false,
  loadSourceContext:()=>{},refreshNative:async()=>{},refreshObjects:async()=>{},restoreFileFeedbackHistory:(pid,current)=>{state.fileFeedbackRestores=(state.fileFeedbackRestores??[]).concat({pid,current:current()});},threadScope:()=>state.scope,setSidebarOpen:()=>{},
  historyDiscoverySource:()=>false,busy:()=>options.busy?'working':false,focusComposer:()=>state.calls.push('composer focus'),setCreationNotice:value=>state.creationNotice=value,activityOpen:()=>state.activityOpen,
  loadSupervisedTaskNavigation:async()=>options.taskTarget??{kind:'original',readOnly:false},
  installDiscussionTarget:async(_target,current)=>{assert.equal(current(),true,'task overlay remains open throughout installation');if(options.install)await options.install();if(!current())return;state.installed=true;},
 };
 for(const name of ['DraftReady','SelectedDiscussion','ActiveOperation','Projects','Project','Workspaces','Workspace','ActiveSession','FeedbackRecords','DraftAttachments','Materials','Selection','DirectFile','DirectWrite','DirectFileRequired','DirectResults','NativeFileFeedbacks','DirectFeedbackRefs','Inspector','NativeOpen','SkillDrawer','SessionManagementOpen','NativeMessages','ThreadAccess','ThreadScope','ThreadMessages','ThreadCursor','ThreadLoading','ThreadError','ToolMode','OriginalAgent','HistoryActiveId','HistorySelectedItem','HistoryReloadToken','Prompt','NativeDraftAttachments','Error','Notice','HistoryPopoverOpen','HistoryError','SupervisorReturn','ActivityLoading','ActivityOpen','ActivityDetail','SupervisorOpen','ActivityError','SupervisorSelection','HistoryPreview']){
  const key={SelectedDiscussion:'selection',ThreadScope:'scope',ThreadMessages:'messages',NativeDraftAttachments:'attachments',Error:'error'}[name]??name[0].toLowerCase()+name.slice(1);
  bindings['set'+name]=setter(key);
 }
 const run=new Function(...Object.keys(bindings),`let studioDisposed=false,activityRequest=0,composerInteracted=false,nativeDraftOwner,discussionDraftLease,threadReadAbort,threadReadGeneration=0,threadResultGeneration=0;const setThreadResultSelection=()=>{};\n${js}\nreturn {openHistoryConversation,installThread,openLegacyHistoryConversation,enterSupervisedTask,selectDraftChoice,dispose(){studioDisposed=true;navigation.dispose();}};`)(...Object.values(bindings));
 return{...run,state,row,scope,page,data,storage,navigation,current:()=>true};
}

test('actual shared history install reveals the selected original discussion after draft barriers, retaining both drafts and stored tools',async()=>{
 const barrier=deferred(),h=scene({flush:()=>barrier.promise}),pending=h.openHistoryConversation(h.row);await tick();
 assert.equal(h.state.activityOpen,true);assert.equal(h.state.supervisorOpen,true);assert.equal(h.state.selection.key.id,before);
 h.state.prompt='等待期间新增的人工草稿';barrier.resolve();await pending;
 assert.equal(h.state.scope.conversation_ref.id,destination);assert.equal(h.state.selection.key.id,destination);
 assert.deepEqual(h.state.messages,h.page.messages);assert.equal(h.state.prompt,'目的会话的维护草稿');
 assert.equal(new DiscussionDraftLease(h.storage,draftKey(before)).read().text,'等待期间新增的人工草稿');
 assert.equal(new DiscussionDraftLease(h.storage,draftKey(destination)).read().text,'目的会话的维护草稿');
 assert.equal(h.state.activityOpen,false);assert.equal(h.state.supervisorOpen,false);assert.equal(h.state.activityDetail,undefined);assert.equal(h.state.supervisorReturn,undefined);
 assert.ok(h.state.presentations.includes('chat'));assert.deepEqual(h.state.calls,['selection metadata','saved owner GET']);
 assert.equal(h.state.original.model_calls_reserved,6);assert.equal(h.state.original.connected_owner,false);
});

test('canonical native history uses the same success boundary, without title-specific behavior',async()=>{
 const h=scene({native:true});await h.openHistoryConversation(h.row);
 assert.equal(h.state.activityOpen,false);assert.equal(h.state.supervisorOpen,false);assert.equal(h.state.prompt,'目的会话的维护草稿');assert.equal(h.state.toolMode,'direct');
 assert.deepEqual(h.state.fileFeedbackRestores,[{pid:'destination_project',current:true}]);
});

test('failed source validation or draft barriers retain the task surface, selected input and original recovery evidence',async()=>{
 for(const options of [{workspaceMismatch:true},{saveFailure:true},{flushFailure:true},{pointerFailure:true}]){
  const h=scene(options),targetRaw=h.storage.getItem(discussionDraftKey(draftKey(destination)));await h.openHistoryConversation(h.row);
  assert.equal(h.state.activityOpen,true);assert.equal(h.state.supervisorOpen,true);assert.deepEqual(h.state.activityDetail,{key:'old_task'});
  assert.equal(h.state.selection.key.id,before);assert.equal(h.state.prompt,'当前尚未发送的草稿😀');assert.equal(h.state.presentations.length,0);
  assert.equal(h.storage.getItem(discussionDraftKey(draftKey(destination))),targetRaw);assert.match(h.state.error,/无法打开/);
 }
});

test('history navigation presents both stored drafts and resumes the selected conversation after choice',async()=>{
 for(const choice of ['local','saved']){
  const h=scene({migrationConflict:true}),key=draftKey(destination);
  assert.equal(await h.openHistoryConversation(h.row),'DEFERRED');
  assert.equal(h.state.selection.key.id,before);
  assert.equal(h.state.error,undefined);
  const dialog=h.state.draftRecovery;
  assert.equal(dialog.local.text,'另一个来源的人工草稿');
  assert.equal(dialog.saved.text,'目的会话的维护草稿');
  await h.selectDraftChoice(choice);
  assert.equal(h.state.draftRecovery,undefined);
  assert.equal(h.state.selection.key.id,destination);
  assert.equal(h.state.prompt,dialog[choice].text);
  const pairs=readDraftCopies(h.storage,new DiscussionDraftLease(h.storage,key));
  assert.deepEqual(pairs.map(pair=>[pair.local.text,pair.saved.text]),[[dialog.local.text,dialog.saved.text]]);
  assert.equal(h.state.original.connected_owner,false);
 }
});

test('migrating a draft cannot take over after navigation changes or bind a revised conversation',async()=>{
 for(const changed of ['navigation','revision']){
  const h=scene({migrationConflict:true});
  await h.openHistoryConversation(h.row);
  const beforeChoice=[...h.data];
  if(changed==='navigation')h.navigation.begin();
  else h.scope.conversation_ref={...h.scope.conversation_ref,revision:2};
  await h.selectDraftChoice('local');
  assert.deepEqual([...h.data],beforeChoice);
  assert.equal(h.state.selection.key.id,before);
 }
});

test('a second conflict during the same history selection opens its own recovery and finishes navigation',async()=>{
 const h=scene({migrationConflict:true,thirdDraft:true});
 await h.openHistoryConversation(h.row);
 const first=h.state.draftRecovery;
 await h.selectDraftChoice('saved');
 assert.notEqual(h.state.draftRecovery,first);
 assert.equal(h.state.draftRecovery.local.text,'第三份独立输入');
 await h.selectDraftChoice('local');
 assert.equal(h.state.draftRecovery,undefined);
 assert.equal(h.state.selection.key.id,destination);
 assert.equal(h.state.prompt,'第三份独立输入');
 assert.equal(readDraftCopies(h.storage,new DiscussionDraftLease(h.storage,draftKey(destination))).length,2);
});

test('a missing or changed selected history reference reports failure without changing discussion or drafts',async()=>{
 for(const missing of [true,false]){
  const h=scene({missingHistory:missing}),drafts=[...h.data],selection=h.state.selection;
  const clicked=missing?h.row:{...h.row,source:{...h.row.source,generation:2}};
  assert.equal(await h.openHistoryConversation(clicked),'FAILED');
  assert.match(h.state.error,/无法打开/);assert.deepEqual(h.state.calls,['history read']);
  assert.equal(h.state.selection,selection);assert.deepEqual([...h.data],drafts);
  assert.equal(h.state.prompt,'当前尚未发送的草稿😀');assert.equal(h.state.activityOpen,true);assert.equal(h.state.supervisorOpen,true);
  assert.deepEqual(h.state.activityDetail,{key:'old_task'});assert.equal(h.state.presentations.length,0);
 }
});

test('late history completion and disposed navigation cannot dismiss a newer task or replace its input',async()=>{
 for(const dispose of [false,true]){
  const read=deferred(),h=scene({historyRead:()=>read.promise}),pending=h.openHistoryConversation(h.row);await tick();
  if(dispose)h.dispose();else h.navigation.begin();
  h.state.activityDetail={key:'new_task'};h.state.prompt='另一次导航的新输入';read.resolve(h.page);await pending;
  assert.equal(h.state.activityOpen,true);assert.deepEqual(h.state.activityDetail,{key:'new_task'});assert.equal(h.state.prompt,'另一次导航的新输入');assert.equal(h.state.presentations.length,0);
 }
});

test('creation draft recovery conflict returns false before presentation and keeps the task surface and both exact inputs',async()=>{
 const h=scene({seedConflict:true}),installed=await h.installThread(h.row,h.scope,h.page,h.current,undefined,[],{operationId:'creation',seed:()=>({text:'待迁移的新会话草稿',attachments:[]})});
 assert.equal(installed,false);assert.equal(h.state.recovery.length,1);assert.equal(h.state.activityOpen,true);assert.equal(h.state.supervisorOpen,true);
 assert.equal(h.state.prompt,'当前尚未发送的草稿😀');assert.equal(new DiscussionDraftLease(h.storage,draftKey(destination)).read().text,'目的会话的维护草稿');assert.equal(h.state.presentations.length,0);
});

test('legacy selection already reveals its discussion only after successful installation',async()=>{
 const wait=deferred(),h=scene({install:()=>wait.promise}),pending=h.openLegacyHistoryConversation(h.row);await tick();
 assert.equal(h.state.activityOpen,true);assert.equal(h.state.supervisorOpen,true);wait.resolve();await pending;
 assert.equal(h.state.activityOpen,false);assert.equal(h.state.supervisorOpen,false);assert.equal(h.state.installed,true);
});

test('task-entry ownership stays valid during installation and closes only afterwards; readonly task details remain open',async()=>{
 const wait=deferred(),h=scene({install:()=>wait.promise}),pending=h.enterSupervisedTask({key:'saved',task:{source_ref:{id:'saved'}}});await tick();
 assert.equal(h.state.activityOpen,true);wait.resolve();await pending;assert.equal(h.state.installed,true);assert.equal(h.state.activityOpen,false);
 const historical=scene({taskTarget:{kind:'original',readOnly:true}});await historical.enterSupervisedTask({key:'saved',task:{source_ref:{id:'saved'}}});
 assert.equal(historical.state.activityOpen,true);assert.equal(historical.state.installed,undefined);assert.match(historical.state.activityError.message_key,/历史记录/);
});

const managerSource=readFileSync(new URL('../src/ConversationManager.tsx',import.meta.url),'utf8');
const managerTree=ts.createSourceFile('ConversationManager.tsx',managerSource,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const manager=managerTree.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='ConversationManager');
const managerOpen=manager.body.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='open').getText(managerTree);
const managerHook=name=>manager.body.statements.find(n=>ts.isExpressionStatement(n)&&ts.isCallExpression(n.expression)&&n.expression.expression.getText(managerTree)===name).expression.arguments[0].getText(managerTree);
function managerScene(result){
 const state={open:true,closed:0,calls:0,error:false,openError:false,busy:false,setters:[]};
 const props={get open(){return state.open;},busy:false,refreshVersion:0,onOpen:async()=>{state.calls++;return result();},onOpenChange:open=>{state.open=open;if(!open)state.closed++;}};
 const bindings={props,busy:()=>state.busy,setError:value=>state.error=value,setOpenError:value=>{state.setters.push('openError');state.openError=value;},setBusy:value=>state.busy=value,
  tab:()=> 'active',setItems:()=>{},setCursor:()=>{},setPartial:()=>{},load:async()=>{}};
 const script=`let epoch=0,controller,previousTab='active',openingRequest=0,opening=false;${managerOpen};const lifecycle=${managerHook('createEffect')};const cleanup=${managerHook('onCleanup')};return{open,lifecycle,cleanup};`;
 const actual=ts.transpileModule(script,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
 const run=new Function(...Object.keys(bindings),actual)(...Object.values(bindings));
 return{...run,state,closeAndReopen(){state.open=false;run.lifecycle();state.open=true;run.lifecycle();}};
}

test('actual manager keeps false, undefined and rejected selection open; only an explicit visible success closes it',async()=>{
 for(const result of [async()=>false,async()=>undefined,async()=>{throw Error('draft conflict');}]){
  const h=managerScene(result);await h.open({id:destination});assert.equal(h.state.open,true);assert.equal(h.state.closed,0);assert.equal(h.state.calls,1);assert.equal(h.state.openError,true);
 }
 const accepted=managerScene(async()=> 'OPENED');await accepted.open({id:destination});assert.equal(accepted.state.closed,1);assert.equal(accepted.state.open,false);
});

test('a manager closed/reopened or disposed during selection is not dismissed by the old true reply',async()=>{
 for(const disposed of [false,true]){
  const wait=deferred(),h=managerScene(()=>wait.promise),pending=h.open({id:destination});await tick();
  if(disposed)h.cleanup();else h.closeAndReopen();const prior=h.state.setters.length;wait.resolve('OPENED');await pending;assert.equal(h.state.setters.length,prior);assert.equal(h.state.closed,0);assert.equal(h.state.open,true);
 }
});

test('manager busy and duplicate pending clicks cannot start another navigation',async()=>{
 const wait=deferred(),h=managerScene(()=>wait.promise);h.state.busy=true;await h.open({id:destination});assert.equal(h.state.calls,0);
 h.state.busy=false;const pending=h.open({id:destination});await tick();const duplicate=h.open({id:destination});await tick();const calls=h.state.calls;
 wait.resolve('DEFERRED');await Promise.all([pending,duplicate]);assert.equal(calls,1);assert.equal(h.state.open,true);
});

test('busy or superseded App navigation is explicitly deferred, not a visible manager failure',async()=>{
 const busy=scene({busy:true});assert.equal(await busy.openHistoryConversation(busy.row),'DEFERRED');assert.equal(busy.state.calls.length,0);
 const wait=deferred(),late=scene({historyRead:()=>wait.promise}),pending=late.openHistoryConversation(late.row);await tick();late.navigation.begin();wait.resolve(late.page);
 assert.equal(await pending,'DEFERRED');assert.equal(late.state.error,undefined);
 const manager=managerScene(async()=> 'DEFERRED');await manager.open({id:destination});assert.equal(manager.state.open,true);assert.equal(manager.state.openError,false);
});

test('a successfully adopted chat stays opened when only its journal cleanup fails, while late cleanup cannot steal focus',async()=>{
 const failed=scene({native:true,adoption:true,acknowledge:async()=>{throw Error('cleanup failed');}});
 assert.equal(await failed.openHistoryConversation(failed.row),'OPENED');assert.equal(failed.state.activityOpen,false);assert.match(failed.state.creationNotice,/已打开/);
 for(const rejected of [false,true]){
  const wait=deferred(),late=scene({native:true,adoption:true,acknowledge:()=>wait.promise}),pending=late.openHistoryConversation(late.row);await tick();
  assert.equal(late.state.scope.conversation_ref.id,destination);late.navigation.begin();late.state.activityOpen=true;late.state.activityDetail={key:'new_task'};late.state.prompt='新导航的输入';
  late.storage.setItem('eeas-current-free-draft-v1','new-free-draft-owner');late.state.creationNotice='新导航的提示';
  if(rejected)wait.reject(Error('late cleanup failure'));else wait.resolve();
  assert.equal(await pending,'DEFERRED');assert.equal(late.state.activityOpen,true);assert.equal(late.state.prompt,'新导航的输入');assert.ok(!late.state.calls.includes('composer focus'));
  assert.equal(late.storage.getItem('eeas-current-free-draft-v1'),'new-free-draft-owner');assert.equal(late.state.creationNotice,'新导航的提示');
 }
});

test('legacy supervisor and readonly preview report success while retaining their actual visible destination',async()=>{
 for(const coordinator of [true,false]){
  const request='f'.repeat(64),message={operation_id:'saved_send',project_id:'destination_project',request_project_id:coordinator?'destination_project':'before_project',conversation_id:'native_chat',request_sha256:request};
  const bridge={inspectNativeMessage:async()=>message,getNativeConversation:async()=>coordinator?
   {kind:'SUPERVISOR_DISCUSSION',conversation_id:'native_chat',selection:{operation_id:'saved_send',request_sha256:request,question:'Exact saved question',text:'Exact saved answer',status:'RETURNED'}}:
   {kind:'NATIVE',project_id:'destination_project',conversation_id:'native_chat',messages:[message]}};
  const h=scene({native:true,nativeBridge:bridge});h.row.source.request_project_id=message.request_project_id;
  assert.equal(await h.openLegacyHistoryConversation(h.row),'OPENED');assert.equal(h.state.activityOpen,false);
  if(coordinator){assert.equal(h.state.supervisorOpen,true);assert.equal(h.state.supervisorSelection.answer_text,'Exact saved answer');}
  else{assert.equal(h.state.supervisorOpen,false);assert.equal(h.state.historyPreview.answer,'Exact saved preview result');}
  assert.equal(h.state.prompt,'当前尚未发送的草稿😀');
 }
});
