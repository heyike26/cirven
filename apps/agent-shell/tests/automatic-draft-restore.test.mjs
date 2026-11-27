import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
import {NavigationOwner} from '../src/registry.ts';
import {authoredCopy,authoredErrorCopy} from '../src/authored-ui-copy.ts';

const source=await readFile(new URL('../src/App.tsx',import.meta.url),'utf8');
const tree=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const app=tree.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='App');
const restore=app.body.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='restoreDraftObject');
const guard=app.body.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='automaticDraftCurrent');
const js=ts.transpileModule(guard.getText(tree)+'\n'+restore.getText(tree),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
const deferred=()=>{let resolve;const promise=new Promise(yes=>resolve=yes);return{promise,resolve};};
const key={project_id:'own-project',workspace_id:'own-workspace',kind:'proposal',object_id:'saved-proposal',revision:3,sha256:'a'.repeat(64),session_id:'historical-discussion'};
const proposal={id:key.object_id,revision:key.revision,sha256:key.sha256};
function scene(sessionId=key.session_id){
 const navigation=new NavigationOwner(),initial=navigation.begin(),gate=deferred(),events=[];let inspector;let session=sessionId;let workspace={id:key.workspace_id,project_id:key.project_id,revision:1,binding_sha256:'b'.repeat(64)};
 const bindings={navigation,project:()=>({id:key.project_id}),api:{projectPath:id=>'/api/projects/'+id,request:async(url,method,body,signal)=>{events.push(['read',url,signal]);return gate.promise;}},discoveryApi:{get:async()=>{events.push(['session-read']);return{id:key.session_id,project_id:key.project_id,payload:{workspace_ref:null}};}},activeSession:()=>session?{id:session}:undefined,workspace:()=>workspace,localStorage:{getItem:()=>null},threadScope:()=>undefined,saveComposerDraft:()=>events.push(['save-draft']),installDiscussionTarget:async value=>events.push(['install-context',value]),activateSession:session=>events.push(['activate',session]),inspect:value=>events.push(['open',value]),inspector:()=>inspector,interactionBlocking:()=>false,setInspector:value=>{inspector=value;events.push(['retain',value]);},envelope:(kind,id,data,revision,sha256)=>({type:kind,artifactId:id,data,revision,contentHash:sha256}),setError:value=>events.push(['error',value]),authoredCopy,authoredErrorCopy};
 const execute=new Function(...Object.keys(bindings),js+'\nreturn restoreDraftObject;')(...Object.values(bindings));
 return{execute,navigation,initial,gate,events,setInspector:value=>inspector=value,setSession:value=>session=value,setWorkspace:value=>workspace=value,get inspector(){return inspector;}};
}
test('automatic draft restore retains the exact artifact without aborting startup or opening a workbench or another discussion',async()=>{
 const s=scene(),pending=s.execute(key,false);assert.equal(s.initial.signal.aborted,false);assert.equal(s.events[0][2],undefined);
 s.gate.resolve(proposal);await pending;assert.equal(s.initial.current(),true);assert.deepEqual(s.events.map(event=>event[0]),['read','retain']);
 assert.equal(s.inspector.data,proposal);assert.equal(s.inspector.contentHash,key.sha256);assert.equal(s.inspector.scope.sessionId,key.session_id);
});
test('an automatic restore cannot overwrite a newer navigation or an already visible artifact',async()=>{
 for(const navigate of [false,true]){const s=scene(),pending=s.execute(key,false),newer={artifactId:'user-selected-now'};s.setInspector(newer);if(navigate)s.navigation.begin();s.gate.resolve(proposal);await pending;assert.equal(s.inspector,newer);assert.deepEqual(s.events.map(event=>event[0]),['read']);}
});
test('explicit restore still owns navigation and activates and opens the requested exact draft',async()=>{
 const s=scene('current-discussion'),pending=s.execute(key);assert.equal(s.initial.signal.aborted,true);assert.ok(s.events.find(v=>v[0]==='read')[2] instanceof AbortSignal);
 s.gate.resolve(proposal);await pending;assert.deepEqual(s.events.map(event=>event[0]),['save-draft','read','session-read','install-context','open']);assert.equal(s.events.at(-1)[1].contentHash,key.sha256);
});

test('automatic restoration rejects another session even within the same project and directory',async()=>{
 const s=scene('current-discussion');await s.execute(key,false);assert.deepEqual(s.events,[]);assert.equal(s.inspector,undefined);
});
test('session or directory changes while the original read is pending cannot install the old artifact',async()=>{
 for(const change of ['session','workspace']){const s=scene(),pending=s.execute(key,false);if(change==='session')s.setSession('new-session');else s.setWorkspace({id:'other',project_id:key.project_id,revision:1,binding_sha256:'b'.repeat(64)});s.gate.resolve(proposal);await pending;assert.equal(s.inspector,undefined);assert.deepEqual(s.events.map(v=>v[0]),['read']);}
});
test('a local file with a changed binding is never restored automatically',async()=>{
 const s=scene();await s.execute({...key,kind:'local-file',binding_revision:4,binding_sha256:'c'.repeat(64)},false);assert.deepEqual(s.events,[]);
});
