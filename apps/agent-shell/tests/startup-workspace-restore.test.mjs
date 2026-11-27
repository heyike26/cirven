import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
import {NavigationOwner} from '../src/registry.ts';

const source=await readFile(new URL('../src/App.tsx',import.meta.url),'utf8');
const tree=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const app=tree.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='App');
const callbacks=['selectProject','selectWorkspace'].map(name=>app.body.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text===name).getText(tree)).join('\n');
const js=ts.transpileModule(callbacks,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
const old={id:'old',project_id:'p',revision:1,binding_sha256:'a'.repeat(64)};
const bound={id:'bound',project_id:'p',revision:2,binding_sha256:'b'.repeat(64)};
const saved={id:'current',project_id:'p',revision:1,payload:{workspace_ref:{id:bound.id,revision:bound.revision,binding_sha256:bound.binding_sha256},materials:[]}};
function scene(options={}){
 const map=new Map([['eeas-shell-workspace:p',old.id],['eeas-discovery-session:p',saved.id],['eeas-agent-tool-mode','discovery']]);
 const localStorage={getItem:key=>map.get(key)??null,setItem:(key,v)=>map.set(key,v),removeItem:key=>map.delete(key)};
 const state={project:undefined,workspace:undefined,session:undefined,prompt:'',errors:[],reads:[],events:[]};
 const navigation=new NavigationOwner();let composerInteracted=false;
 const noop=()=>{};
 const bindings={localStorage,navigation,batch:fn=>fn(),project:()=>state.project,selectedDiscussion:()=>undefined,prompt:()=>state.prompt,toolMode:()=>map.get('eeas-agent-tool-mode'),windowMode:()=> 'workbench',activeSession:()=>state.session,
  saveComposerDraft:noop,clearThread:noop,draftPersistence:{pause:noop},projectObjects:{activate:noop},restoreOriginalSession:async()=>{},loadSourceContext:noop,restoreDirectHistory:async()=>{},restoreSelectedDirectFile:async()=>{},refreshNative:async()=>{},stopObserver:noop,clearProjectFeed:noop,restoreNativeDraft:noop,readNativeText:()=>'',readNativeDraft:()=>[],readComposerDraft:()=>({text:'',feedback_refs:[]}),saveNativeText:noop,restoreComposerDraft:noop,
  setProject:v=>state.project=v,setWorkspace:v=>state.workspace=v,setActiveSession:v=>state.session=v,setError:v=>state.errors.push(v),authoredErrorCopy:e=>String(e),authoredCopy:v=>v,
  activateSession:v=>{state.session=v;state.prompt='original saved draft';state.events.push(['activate',v.id]);},
  api:{workspaces:async()=>options.list??[old,bound]},discoveryApi:{get:async(pid,id,signal)=>{state.reads.push([pid,id,signal]);return options.sessionRead?options.sessionRead():structuredClone(saved);}},
  refreshObjects:async()=>state.events.push(['catalog']),observeOperation:noop,observeDiscovery:(...args)=>state.events.push(['observe',...args]),readDiscoveryPending:()=>options.pending,
  selectOriginalSession:noop,setNativeConfig:noop,
 };
 for(const name of ['setDraftReady','setDraftAttachments','setSelectedDiscussion','setSupervisorReturn','setHistoryActiveId','setActiveOperation','setDirectResults','setDirectFile','setDirectWrite','setNativeFileFeedbacks','setDirectFeedbackRefs','setFeedbackRecords','setNativeOpen','setInspector','setSelection','setMaterials','setWorkspaces','setUnboundRecoveryText','setSidebarOpen','setDiscoveryError'])bindings[name]=noop;
 const values=new Function(...Object.keys(bindings),`let discussionDraftLease, nativeDraftOwner, feedbackRefreshGeneration=0;let composerInteracted=false;${js};return {selectProject,selectWorkspace,interact:()=>composerInteracted=true};`)(...Object.values(bindings));
 return{...values,state,map,navigation};
}
test('cold startup repairs an existing stale project directory using the selected requirements session exact binding',async()=>{
 const s=scene({pending:{sessionId:saved.id,id:'original-unknown',basisRevision:1}});await s.selectProject({id:'p'},{restore:true});
 assert.equal(s.state.workspace?.id,bound.id);assert.equal(s.state.session?.id,saved.id);assert.equal(s.state.prompt,'original saved draft');assert.equal(s.map.get('eeas-shell-workspace:p'),bound.id);
 assert.equal(s.state.reads.length,1);assert.ok(s.state.events.some(v=>v[0]==='observe'&&v[3]==='original-unknown'));
});
test('changed or missing exact directory never falls back to a different project directory',async()=>{
 for(const list of [[old],[old,{...bound,binding_sha256:'c'.repeat(64)}]]){const s=scene({list});await s.selectProject({id:'p'},{restore:true});assert.equal(s.state.workspace,undefined);assert.equal(s.state.session?.id,saved.id);assert.equal(s.state.prompt,'original saved draft');assert.ok(s.state.errors.length);assert.equal(s.map.get('eeas-shell-workspace:p'),old.id);}
});
test('a failed session read retains the pointer and refuses an unrelated directory',async()=>{
 const s=scene({sessionRead:async()=>{throw Error('temporary unavailable');}});await s.selectProject({id:'p'},{restore:true});assert.equal(s.state.workspace,undefined);assert.equal(s.map.get('eeas-discovery-session:p'),saved.id);assert.ok(s.state.errors.length);
});
test('explicit project selection and a pending modern thread do not activate a legacy requirements session',async()=>{
 for(const modern of [false,true]){const s=scene();if(modern)s.map.set('eeas-current-thread-v1','saved modern selection');await s.selectProject({id:'p'},modern?{restore:true}:undefined);assert.equal(s.state.session,undefined);assert.equal(s.state.reads.length,0);assert.equal(s.state.workspace.id,old.id);}
});
test('delayed startup session load cannot replace explicit navigation or text entered meanwhile',async()=>{
 for(const edit of [false,true]){let release;const gate=new Promise(r=>release=r);const s=scene({sessionRead:()=>gate});const pending=s.selectProject({id:'p'},{restore:true});await new Promise(r=>setImmediate(r));if(edit)s.interact();else s.navigation.begin();release(saved);await pending;assert.equal(s.state.workspace,undefined);assert.equal(s.state.session,undefined);assert.equal(s.map.get('eeas-shell-workspace:p'),old.id);}
});
