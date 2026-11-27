import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
import {NavigationOwner} from '../src/registry.ts';
import {authoredCopy,authoredErrorCopy} from '../src/authored-ui-copy.ts';

const source=await readFile(new URL('../src/App.tsx',import.meta.url),'utf8');
const tree=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const app=tree.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='App');
const callbacks=['openDiscovery','automaticDraftCurrent','restoreDraftObject'].map(name=>app.body.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text===name).getText(tree)).join('\n');
const js=ts.transpileModule(callbacks,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
const a={id:'directory-a',project_id:'p',revision:1,binding_sha256:'a'.repeat(64)};
const b={id:'directory-b',project_id:'p',revision:2,binding_sha256:'b'.repeat(64)};
const session=(id,w)=>({id,project_id:'p',revision:1,sha256:'c'.repeat(64),payload:{workspace_ref:{id:w.id,revision:w.revision,binding_sha256:w.binding_sha256}}});
const current=session('discussion-a',a),target=session('discussion-b',b);
const key={project_id:'p',session_id:target.id,kind:'proposal',object_id:'original-plan',revision:3,sha256:'d'.repeat(64)};
const plan={id:key.object_id,project_id:'p',revision:3,sha256:key.sha256};
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return{resolve,promise};}
function scene(options={}){
 const navigation=new NavigationOwner();navigation.begin();const state={session:current,workspace:a,text:'draft A',saved:[],opened:[],installed:[],errors:[],reads:[]};
 const bindings={navigation,project:()=>({id:'p'}),workspace:()=>state.workspace,activeSession:()=>state.session,threadScope:()=>undefined,localStorage:{getItem:()=>null,setItem:()=>{}},
  chooseTool:()=>{},saveComposerDraft:()=>{if(options.saveFailure)throw Error('draft storage failed');state.saved.push({session:state.session.id,text:state.text});},
  discoveryApi:{get:async(pid,id,signal)=>{state.reads.push(['session',pid,id,signal]);return options.sessionRead?options.sessionRead():target;}},
  api:{projectPath:pid=>'/api/projects/'+pid,request:async(url,method,body,signal)=>{state.reads.push(['artifact',url,signal]);return options.artifactRead?options.artifactRead():plan;}},
  activateSession:value=>{state.session=value;state.text='draft B';},
  installDiscussionTarget:async(value,owned,signal,presentation)=>{state.installed.push({value,presentation});assert.equal(presentation,'chat');assert.deepEqual(value.navigation,{project_id:'p',workspace_ref:target.payload.workspace_ref});if(options.installFailure)throw Error('original binding changed');if(!owned())return;bindings.saveComposerDraft();state.session=value.session;state.workspace=b;state.text='draft B';},
  inspect:value=>state.opened.push(value),envelope:(kind,id,data,revision,hash)=>({type:kind,artifactId:id,data,revision,contentHash:hash}),
  inspector:()=>undefined,interactionBlocking:()=>false,setInspector:()=>assert.fail('explicit navigation must present explicitly'),windowMode:()=> 'workbench',changeNativeMode:async()=>true,setSidebarOpen:()=>{},setError:e=>state.errors.push(e),authoredCopy,authoredErrorCopy};
 const handlers=new Function(...Object.keys(bindings),js+';return {openDiscovery,restoreDraftObject};')(...Object.values(bindings));
 return{...handlers,state,navigation};
}
test('workbench requirements opening installs the verified discussion and directory together and keeps the previous draft',async()=>{
 const s=scene();await s.openDiscovery(target);assert.equal(s.state.workspace.id,b.id);assert.equal(s.state.session.id,target.id);assert.equal(s.state.installed.length,1);assert.ok(s.state.saved.some(v=>v.session===current.id&&v.text==='draft A'));assert.equal(s.state.opened.at(-1).data,target);
});
test('explicit old artifact restore adopts its discussion directory before showing the exact immutable artifact',async()=>{
 const s=scene();await s.restoreDraftObject(key);assert.equal(s.state.workspace.id,b.id);assert.equal(s.state.session.id,target.id);assert.equal(s.state.installed.length,1);assert.equal(s.state.opened.at(-1).contentHash,key.sha256);assert.equal(s.state.opened.at(-1).scope.sessionId,target.id);
});
test('failed binding validation or original draft save never installs or opens the destination',async()=>{
 for(const entry of ['open','restore'])for(const options of [{installFailure:true},{saveFailure:true}]){const s=scene(options);if(entry==='open')await s.openDiscovery(target);else await s.restoreDraftObject(key);assert.equal(s.state.workspace,a);assert.equal(s.state.session,current);assert.equal(s.state.text,'draft A');assert.equal(s.state.opened.length,0);assert.ok(s.state.errors.length);}
});
test('delayed exact artifact or session reads cannot take back later user navigation',async()=>{
 for(const entry of ['open','restore']){const wait=deferred(),s=scene(entry==='open'?{sessionRead:()=>wait.promise}:{artifactRead:()=>wait.promise});const pending=entry==='open'?s.openDiscovery(target):s.restoreDraftObject(key);s.navigation.begin();wait.resolve(entry==='open'?target:plan);await pending;assert.equal(s.state.workspace,a);assert.equal(s.state.session,current);assert.equal(s.state.opened.length,0);assert.equal(s.state.installed.length,0);}
});
test('wrong artifact identity with the same revision and hash cannot change the conversation',async()=>{
 const s=scene({artifactRead:async()=>({...plan,id:'another-object'})});await s.restoreDraftObject(key);assert.equal(s.state.workspace,a);assert.equal(s.state.session,current);assert.equal(s.state.opened.length,0);assert.ok(s.state.errors.length);
});

test('restoring within the already selected session still repairs a different selected directory',async()=>{
 const s=scene();s.state.session=target;await s.restoreDraftObject(key);assert.equal(s.state.workspace.id,b.id);assert.equal(s.state.installed.length,1);
});
test('new text entered while a destination is loading is saved under the original discussion before switching',async()=>{
 const wait=deferred(),s=scene({sessionRead:()=>wait.promise});const pending=s.openDiscovery(target);s.state.text='latest manual draft A';wait.resolve(target);await pending;assert.ok(s.state.saved.some(v=>v.session===current.id&&v.text==='latest manual draft A'));assert.equal(s.state.text,'draft B');
});
test('a file record returned with the right hash but another exact path is rejected before context installation',async()=>{
 const fileKey={...key,kind:'local-file',workspace_id:b.id,binding_revision:b.revision,binding_sha256:b.binding_sha256,object_id:'original.txt'};
 const s=scene({artifactRead:async()=>({source_ref:{kind:'local_file',project_id:'p',workspace_id:b.id,binding_revision:b.revision,binding_sha256:b.binding_sha256,path:'different.txt',revision:key.revision,sha256:key.sha256}})});await s.restoreDraftObject(fileKey);assert.equal(s.state.session,current);assert.equal(s.state.installed.length,0);assert.equal(s.state.opened.length,0);assert.ok(s.state.errors.length);
});
test('a session read that finishes after user navigation cannot switch context after the artifact was already verified',async()=>{
 const wait=deferred(),s=scene({sessionRead:()=>wait.promise}),pending=s.restoreDraftObject(key);await new Promise(r=>setImmediate(r));s.navigation.begin();wait.resolve(target);await pending;assert.equal(s.state.session,current);assert.equal(s.state.installed.length,0);assert.equal(s.state.opened.length,0);
});
