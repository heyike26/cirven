import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';

const source=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8');
const tree=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const app=tree.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name.text==='App');
const actual=['selectedThreadId','threadOrganizationPending','threadSendingBlocked','threadWriteAllowed','syncOrganizedThread','organized'].map(name=>app.body.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name.text===name).getText(tree)).join('\n');
const cid='native:'+'a'.repeat(64),other='original:'+'b'.repeat(64);
const scope=(project_id='a',revision=1)=>({conversation_ref:{id:cid,revision},project_id,archived:false,next_message:{project_id,conversation_id:project_id==='a'?'old_route':'new_route'}});
const receipt=(action='move')=>({action,conversation_id:cid,project_id:'b',organization:{revision:2,archived:false}});
function harness(options={}){
 const state={scope:scope(),project:{id:'a'},access:'ready',text:'Unsent 😀\n{"USER":true}',attachments:['exact screenshot'],historyList:[{id:cid,organization:{revision:1,archived:false}}],compact:false,sidebarOpen:true,selection:{key:{kind:'conversation',id:cid},conversationId:'old_route'},history:{id:cid,title:'Original title',projectId:'a'},calls:[],errors:[],values:new Map(),owned:true,...options.state};
 const setter=key=>value=>state[key]=typeof value==='function'?value(state[key]):value;
 const update=()=>{},get=key=>()=>state[key];
 const bindings={creationAffectsCurrent:()=>!!state.creationPending,connectionAffectsCurrent:()=>!!state.connectionPending,setCreationNotice:value=>state.errors.push(value),setConnectionNotice:value=>state.errors.push(value),AbortController,setTimeout,clearTimeout,Promise,studioDisposed:false,threadReadGeneration:0,threadReadAbort:undefined,threadResultGeneration:0,
  organizationVersion:()=>0,draftOnlyTitle:()=>state.draftOnlyTitle??'',threadScope:get('scope'),setThreadScope:setter('scope'),threadAccess:get('access'),setThreadAccess:setter('access'),selectedDiscussion:get('selection'),setSelectedDiscussion:setter('selection'),organizationClient:{get pending(){return state.pending;}},
  navigation:{capture:()=>()=>state.owned},project:get('project'),setProject:setter('project'),projects:()=>[{id:'a',name:'Project A'},{id:'b',name:'Project B'}],workspaces:()=>[{id:'old_workspace'}],setWorkspaces:setter('workspaces'),setWorkspace:setter('workspace'),
  threadClient:{scope:async(id,signal)=>{state.calls.push(['scope',id]);return options.read?options.read(state,signal):scope('b',2);}},
  api:{projects:async()=>{state.calls.push(['projects']);return[{id:'a',name:'Project A'},{id:'b',name:'Project B'}];},workspaces:async id=>{state.calls.push(['workspaces',id]);return[{id:'workspace_'+id}];}},
  userProjects:value=>value,saveComposerDraft:()=>{state.calls.push(['save']);if(options.saveError)throw Error('quota');},localStorage:{getItem:key=>state.values.get(key)??null,setItem:(key,value)=>{if(options.storageError)throw Error('quota');state.values.set(key,value);},removeItem:key=>state.values.delete(key)},
  preserveLayoutReading:()=>()=>state.calls.push(['reading-restored']),shell:{},batch:fn=>fn(),setDirectFile:update,setDirectWrite:update,setDirectFileRequired:update,setSelection:update,setMaterials:update,
  setThreadResultSelection:update,historySelectedItem:get('history'),setHistorySelectedItem:setter('history'),uiLocale:()=> 'en',setError:value=>state.errors.push(value),
  setHistoryItems:setter('historyList'),compact:get('compact'),setSidebarOpen:setter('sidebarOpen'),setOrganizationNotice:setter('notice'),organizationNotice:get('notice'),setOrganizationVersion:update,setOrganizationRefresh:update,refreshHistory:async()=>{state.calls.push(['history']);if(options.listError)throw Error('list unavailable');},
  requestAnimationFrame:fn=>fn(),conversationManagerOpen:()=>false,organizationTarget:()=>undefined,document:{activeElement:null,body:{}},focusComposer:update,
 };
 const functions=new Function(...Object.keys(bindings),ts.transpileModule(actual+'\nreturn {syncOrganizedThread,threadWriteAllowed,organized};',{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText)(...Object.values(bindings));
 return{state,...functions};
}
test('actual Main applies only logical project/scope/next route and preserves the canonical key, text and screenshot',async()=>{
 const h=harness(),basis=structuredClone(h.state.selection.key),text=h.state.text,attachments=h.state.attachments;
 await h.syncOrganizedThread(receipt());assert.equal(h.state.project.id,'b');assert.equal(h.state.scope.conversation_ref.revision,2);assert.equal(h.state.selection.conversationId,'new_route');assert.deepEqual(h.state.selection.key,basis);assert.equal(h.state.text,text);assert.equal(h.state.attachments,attachments);assert.equal(h.state.access,'ready');assert.equal(h.state.history.projectId,'b');assert.equal(h.state.workspace,undefined);assert.ok(!h.state.calls.some(row=>/connect|send|load|POST/i.test(row[0])));
});
test('confirmed organization remains confirmed when either list or scope reads fail, without clearing draft or submitting again',async()=>{
 const h=harness({listError:true,read:async()=>{throw Error('scope unavailable');}}),text=h.state.text;
 await h.organized(receipt(),{title:'Original title'});assert.equal(h.state.notice.receipt.action,'move');assert.equal(h.state.access,'unavailable');assert.equal(h.state.text,text);assert.equal(h.state.project.id,'a');assert.equal(h.threadWriteAllowed(),false);assert.ok(!h.state.calls.some(row=>row[0]==='POST'));
});
test('late scope refresh cannot retarget a newly selected chat or change its draft and focus',async()=>{
 let finish;const h=harness({read:()=>new Promise(resolve=>finish=resolve)}),waiting=h.syncOrganizedThread(receipt());await new Promise(setImmediate);
 h.state.owned=false;h.state.scope={...scope('a'),conversation_ref:{id:other,revision:1}};h.state.text='New chat text';h.state.project={id:'a'};h.state.access='ready';finish(scope('b',2));await waiting;assert.equal(h.state.scope.conversation_ref.id,other);assert.equal(h.state.project.id,'a');assert.equal(h.state.text,'New chat text');assert.equal(h.state.access,'ready');assert.equal(h.state.calls.filter(row=>row[0]==='projects').length,0);
});
test('delete keeps current text and screenshot, persists an explicit tombstone pointer and gates further sends',async()=>{
 const h=harness(),before=h.state.attachments;await h.syncOrganizedThread(receipt('delete'));assert.equal(h.state.access,'deleted');assert.equal(h.state.attachments,before);assert.ok(h.state.text);assert.deepEqual(JSON.parse(h.state.values.get('eeas-current-thread-v1')),{version:1,id:cid,deleted:true,title:'Original title'});assert.equal(h.threadWriteAllowed(),false);assert.equal(h.state.calls.length,0);
});
test('a failed tombstone write does not un-delete the chat, hide the retained draft or grant a send',async()=>{
 const h=harness({storageError:true});await h.syncOrganizedThread(receipt('delete'));assert.equal(h.state.access,'deleted');assert.ok(h.state.text);assert.equal(h.threadWriteAllowed(),false);assert.match(h.state.errors[0],/deleted/);
});
test('an older scope or failed draft save cannot reopen sending after a known organization change',async()=>{
 for(const options of [{read:async()=>scope('b',1)},{saveError:true}]){const h=harness(options);await h.syncOrganizedThread(receipt());assert.equal(h.state.access,'unavailable');assert.equal(h.state.project.id,'a');assert.ok(h.state.text);assert.equal(h.threadWriteAllowed(),false);}
});


test('confirmed current-chat organization closes only the compact drawer and updates rows without hiding a newer revision',async()=>{
 const h=harness({state:{compact:true}});await h.organized(receipt('archive'));assert.equal(h.state.sidebarOpen,false);assert.equal(h.state.historyList.length,0);assert.ok(h.state.text);
 const newer=harness({state:{historyList:[{id:cid,organization:{revision:3,archived:false}}]}});await newer.organized(receipt('archive'));assert.equal(newer.state.historyList.length,1);assert.equal(newer.state.sidebarOpen,true);
});


test('a committed delete recovered after restart marks the same draft-only identity without requiring a readable execution scope',async()=>{
 const h=harness({state:{scope:undefined,access:'unavailable',pending:{conversationId:cid}}});await h.organized(receipt('delete'));assert.equal(h.state.access,'deleted');assert.ok(h.state.text);assert.equal(h.threadWriteAllowed(),false);assert.equal(JSON.parse(h.state.values.get('eeas-current-thread-v1')).id,cid);assert.equal(h.state.calls.filter(row=>row[0]==='scope').length,0);
});


test('the actual Main route memo does not allocate a fake execution address for an unverified canonical draft',()=>{
 const initializer=app.body.statements.filter(ts.isVariableStatement).flatMap(node=>[...node.declarationList.declarations]).find(node=>node.name.getText(tree)==='conversationId').initializer.getText(tree);
 const state={scope:undefined,selection:{key:{kind:'conversation',projectId:null,id:cid},conversationId:''},allocations:0};
 const bindings={createMemo:fn=>fn,threadScope:()=>state.scope,selectedDiscussion:()=>state.selection,project:()=>undefined,activeSession:()=>undefined,toolMode:()=> 'direct',originalSession:()=>undefined,localStorage:{},conversationIdFor:()=>{state.allocations++;return 'allocated_legacy';},fileConversationBasis:value=>value};
 const route=new Function(...Object.keys(bindings),ts.transpileModule('const value='+initializer+';return value;',{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText)(...Object.values(bindings));
 assert.equal(route(),undefined);assert.equal(state.allocations,0);state.scope=scope();assert.equal(route(),'old_route');assert.equal(state.allocations,0);state.scope=undefined;state.selection=undefined;assert.equal(route(),'allocated_legacy');assert.equal(state.allocations,1);
});


test('current connection and creation uncertainty also keep organization-related drafts gated',()=>{
 for(const state of [{connectionPending:true},{creationPending:true}]){const h=harness({state}),text=h.state.text;assert.equal(h.threadWriteAllowed(),false);assert.equal(h.state.text,text);assert.equal(h.state.calls.length,0);assert.equal(h.state.errors.length,1);}
});
