import test from 'node:test';
import {batch} from 'solid-js';
import {readComposerDraft} from '../src/composer-draft.ts';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
import {startupProject,userProjects} from '../src/startup-project.ts';
import {conversationIdFor,QuickInbox,sameQuickScope} from '../src/quick-inbox.ts';
import {NavigationOwner} from '../src/registry.ts';
import {readDiscoveryPending} from '../src/discovery-pending.ts';
import {readNativeHistory,readNativeText,readNativeDraft} from '../src/native-collaboration.ts';

const source=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8'),tree=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);let startup;
function scan(node){if(ts.isCallExpression(node)&&node.expression.getText(tree)==='action'&&node.arguments[0]?.getText(tree)==="'连接本地服务'")startup=node.arguments[1].getText(tree);ts.forEachChild(node,scan);}scan(tree);assert.ok(startup,'actual App onMount startup callback required');
const cid='f9a76f4b-b75c-43bc-b7bb-9e9773ffeac6',ordinary={id:'user-project',name:'工程资料',description:''},internal={id:'runtime-owner',name:'EEAS conversation',description:'eeas.original-agent-session.internal-project.v1'},nativeInternal={id:'desktop-owner',name:'本机工作上下文',description:'EEAS_INTERNAL_DESKTOP_RUNTIME_V1'};
test('all project selectors share the filtered catalog without deleting storage projects or guessing names',()=>{
 const renamed={...ordinary,id:'ordinary-with-internal-name',name:internal.name},input=[internal,ordinary,nativeInternal,renamed];
 assert.deepEqual(userProjects(input),[ordinary,renamed]);assert.equal(input.length,4);
 assert.equal(userProjects(input)[0],ordinary);
 let projection;
 function find(node){if(ts.isVariableDeclaration(node)&&node.name.getText(tree)==='projects')projection=node.initializer?.getText(tree);ts.forEachChild(node,find);}find(tree);
 assert.equal(projection,'createMemo(()=>userProjects(allProjects()))');
});
function harness({projects=[],saved=null,discovery=[],savedSession,projectsError,existingValues}={}){
 const calls=[],history=[{input:'原问题一',operation_id:'original_a'},{input:'原问题二',operation_id:'original_b'}],records=history.map((item,index)=>({id:'quick_'+index,revision:2,sha256:(index?'b':'a').repeat(64),scope:{conversationId:cid},delivery:'SEND',comment:item.input,regions:[],operationId:item.operation_id,message:{operation_id:item.operation_id,conversation_id:cid,status:'RETURNED',text:'原回答'+index}}));
 const values=new Map(existingValues??[['eeas-conversation:'+JSON.stringify(['','']),cid],['eeas-native-message-history:'+cid,JSON.stringify(history)],['eeas-quick-inbox-v1',JSON.stringify({schema:1,records})],['eeas-native-text:unbound','原未发送草稿😀'],['eeas-native-draft:unbound',JSON.stringify([{context_ref:{id:'original-frame',revision:4,sha256:'e'.repeat(64)},label:'原冻结页面',comment:'待发送的批注',regions:[]}])]]);
 if(saved!==null)values.set('eeas-shell-project',saved);if(savedSession){values.set('eeas-discovery-session:'+ordinary.id,savedSession);values.set('eeas-agent-tool-mode','discovery');}
 const writes=[],localStorage={getItem:key=>values.get(key)??null,setItem:(key,value)=>{writes.push([key,value]);values.set(key,value);},removeItem:key=>{writes.push(['remove',key]);values.delete(key);}};
 let current,provider,active,actualSelect;const state={projects:[],providers:[],ready:false,nativeConfig:null};
 const bindings={startupProject,localStorage,project:()=>current,api:{projects:async()=>{calls.push(['projects']);if(projectsError)throw projectsError;return projects;},providers:async()=>{calls.push(['providers']);return [];},request:async path=>{calls.push(['native-config',path]);return {status:'READY'};}},setConnectionReady:value=>state.connected=value,setProjects:list=>state.projects=list,
  selectProject:async(value,options)=>{calls.push(['selectProject',value.id]);state.selectionOptions=options;return actualSelect(value,options);},restoreOriginalSession:pid=>{calls.push(['restoreOriginalSession',pid]);},discoverySessions:()=>discovery,
  activateSession:value=>{active=value;calls.push(['activateSession',value.id]);},inspector:()=>undefined,inspect:()=>calls.push(['inspect-discovery']),envelope:()=>({}),observeDiscovery:()=>calls.push(['observe-discovery']),setProviders:list=>state.providers=list,setProvidersReady:value=>state.ready=value,setProviderId:value=>provider=value,setError:value=>calls.push(['error',value]),authoredErrorCopy:error=>String(error),setNativeConfig:value=>state.nativeConfig=value,ApiError:class extends Error{}};
 // The startup callback delegates restoration to the actual current
 // selectProject implementation. Unrelated presentation/catalogs stay boundaries.
 Object.assign(bindings,{batch,readComposerDraft,navigation:new NavigationOwner(),readDiscoveryPending,selectedDiscussion:()=>undefined,prompt:()=>readNativeText(localStorage,undefined),saveComposerDraft(){},clearThread(){},draftPersistence:{pause(){}},projectObjects:{activate(){}},setProject:value=>current=value,activeSession:()=>active,setActiveSession:value=>active=value,windowMode:()=> 'workbench',toolMode:()=>localStorage.getItem('eeas-agent-tool-mode')??'original',discoveryApi:{get:async(pid,id)=>{calls.push(['getRequirements',pid,id]);const value=discovery.find(value=>value.project_id===pid&&value.id===id);assert.ok(value);return value;}},api:{...bindings.api,workspaces:async()=>[]},refreshObjects:async()=>{},setDiscoveryError:()=>assert.fail('no pending operation in this startup scene')});
 for(const name of ['selectOriginalSession','setDraftReady','setDraftAttachments','setSelectedDiscussion','setSupervisorReturn','setHistoryActiveId','stopObserver','setActiveOperation','loadSourceContext','setDirectResults','setDirectFile','setDirectWrite','setNativeFileFeedbacks','setDirectFeedbackRefs','restoreDirectHistory','restoreSelectedDirectFile','setFeedbackRecords','setNativeOpen','refreshNative','setWorkspace','setInspector','setSelection','setMaterials','clearProjectFeed','setWorkspaces','setUnboundRecoveryText','restoreComposerDraft','restoreNativeDraft'])if(!(name in bindings))bindings[name]=()=>{};
 const owner=tree.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='App'),select=owner.body.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='selectProject');
 actualSelect=new Function(...Object.keys(bindings),'let discussionDraftLease,feedbackRefreshGeneration=0,composerInteracted=false;'+ts.transpileModule(select.getText(tree),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText+';return selectProject;')(...Object.values(bindings));
 const run=new Function(...Object.keys(bindings),ts.transpileModule('return ('+startup+');',{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText)(...Object.values(bindings));
 return {run,calls,writes,values,localStorage,state,get project(){return current;},snapshot:()=>[...values],cid:()=>conversationIdFor(localStorage,{projectId:current?.id})};
}
function preservedUnbound(h,before){assert.equal(h.project,undefined);assert.equal(h.cid(),cid);assert.equal(readNativeHistory(h.localStorage,cid).length,2);assert.equal(readNativeText(h.localStorage,undefined),'原未发送草稿😀');assert.equal(readNativeDraft(h.localStorage,undefined)[0]?.context_ref.id,'original-frame');const inbox=new QuickInbox(h.localStorage,{},()=>{});assert.equal(inbox.records.filter(record=>record.delivery==='SEND'&&sameQuickScope(record.scope,{conversationId:cid})).length,2);assert.deepEqual(h.snapshot(),before);assert.deepEqual(h.writes,[]);assert.equal(h.calls.filter(row=>row[0]==='restoreOriginalSession').length,1);assert.equal(h.calls.some(row=>row[0]==='selectProject'),false);assert.equal(h.state.ready,true);assert.ok(h.calls.some(row=>row[0]==='native-config'&&row[1]==='/api/agent-shell/native'));}

test('actual startup keeps unbound history/draft when backend projects exist but no saved user project, regardless of list order',async()=>{
 for(const projects of [[internal,ordinary],[ordinary,internal],[nativeInternal,internal]]){const h=harness({projects}),before=h.snapshot();await h.run();preservedUnbound(h,before);}
});
test('actual startup with empty projects, empty saved ID or stale saved ID never falls back, clears storage or opens a folder',async()=>{
 for(const options of [{projects:[]},{projects:[ordinary],saved:''},{projects:[ordinary,internal],saved:'deleted-project'},{projects:[],saved:ordinary.id}]){const h=harness(options),before=h.snapshot();await h.run();preservedUnbound(h,before);}
});
test('already-contaminated exact internal saved IDs remain untouched while original unbound conversation and both answers return to scope',async()=>{
 for(const item of [internal,nativeInternal]){const h=harness({projects:[ordinary,item],saved:item.id}),before=h.snapshot();await h.run();preservedUnbound(h,before);assert.equal(h.localStorage.getItem('eeas-shell-project'),item.id);}
});
test('valid ordinary saved project still restores exact ID and its saved requirements through the actual App branch',async()=>{
 const session={id:'requirements',project_id:ordinary.id,revision:1,sha256:'a'.repeat(64),payload:{workspace_ref:null}},h=harness({projects:[internal,ordinary,nativeInternal],saved:ordinary.id,discovery:[session],savedSession:session.id});await h.run();assert.equal(h.project,ordinary);assert.deepEqual(h.calls.filter(row=>row[0]==='selectProject'),[['selectProject',ordinary.id]]);assert.deepEqual(h.calls.filter(row=>row[0]==='restoreOriginalSession'),[['restoreOriginalSession',ordinary.id]]);assert.deepEqual(h.state.selectionOptions,{restore:true});assert.deepEqual(h.calls.filter(row=>row[0]==='getRequirements'),[['getRequirements',ordinary.id,session.id]]);assert.deepEqual(h.calls.filter(row=>row[0]==='activateSession'),[['activateSession',session.id]]);assert.equal(h.localStorage.getItem('eeas-conversation:'+JSON.stringify(['',''])),cid);assert.equal(readNativeHistory(h.localStorage,cid).length,2);assert.ok(h.calls.some(row=>row[0]==='native-config'&&row[1].endsWith('?project_id='+ordinary.id)));
});
test('display names never classify internal projects; unknown ordinary legacy saved keys preserve previous saved semantics',async()=>{
 for(const item of [{...ordinary,name:'EEAS conversation'},{...ordinary,name:'本机工作上下文'}, {id:'old-user-project',name:'旧项目'}]){const h=harness({projects:[internal,item],saved:item.id});await h.run();assert.equal(h.project,item);assert.equal(h.calls.filter(row=>row[0]==='selectProject').length,1);assert.equal(h.localStorage.getItem('eeas-shell-project'),item.id);}
});
test('startup list-read failure cannot select a project or rewrite original scope/history before existing outer action handles it',async()=>{
 const h=harness({projects:[internal],saved:internal.id,projectsError:new Error('offline fixture')}),before=h.snapshot();await assert.rejects(h.run(),/offline fixture/);assert.equal(h.project,undefined);assert.deepEqual(h.snapshot(),before);assert.deepEqual(h.writes,[]);assert.equal(h.calls.length,1);
});

test('two cold startup callbacks with the same persisted state keep the global conversation when a backend owner project appears between runs',async()=>{
 const first=harness({projects:[]}),before=first.snapshot();await first.run();preservedUnbound(first,before);const second=harness({projects:[internal],existingValues:first.snapshot()}),restarted=second.snapshot();await second.run();preservedUnbound(second,restarted);assert.equal(second.cid(),first.cid());assert.equal(second.localStorage.getItem('eeas-shell-project'),null);
});
