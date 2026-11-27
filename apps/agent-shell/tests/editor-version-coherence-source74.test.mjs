import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';
import {NavigationOwner} from '../src/registry.ts';
import {exactFileSource,sameSelectedFile} from '../src/companion-files.ts';
import {artifactStateKey} from '../src/workbench-state.ts';
import {authoredCopy,authoredErrorCopy,authoredText} from '../src/authored-ui-copy.ts';

// Actual App callbacks + actual FileRenderer TSX. API IO and the host renderer
// are controlled boundaries; this does not qualify backend writes or Windows.
const app=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const parse=file=>{const source=readFileSync(resolve(app,'src',file),'utf8');return ts.createSourceFile(file,source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);};
const parsed=parse('App.tsx'),body=parsed.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='App').body;
const functions=['sameEditorFile','reloadEditor','observeEditorApply','synchronizeSource'].map(name=>body.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text===name).getText(parsed));
let save;function scan(n){if(ts.isCallExpression(n)&&n.expression.getText(parsed)==='registry.registerAction'&&n.arguments[0].properties.some(p=>p.name?.getText(parsed)==='actionId'&&p.initializer?.text==='workspace.file.save'))save=n.arguments[1].getText(parsed);ts.forEachChild(n,scan);}scan(body);
const code=ts.transpileModule(functions.join('\n')+'\nconst save=('+save+');',{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
const source=(revision=1,extra={})=>({kind:'local_file',project_id:'p',workspace_id:'w',binding_revision:1,binding_sha256:'b'.repeat(64),path:'task.md',revision,sha256:String(revision).repeat(64),...extra});
const file=(revision=1,extra={})=>({source_ref:source(revision),content:'text-v'+revision,encoding:'utf-8',byte_length:7,current:true,live_sha256:String(revision).repeat(64),...extra});
const artifact=f=>({type:'local-file',artifactId:f.source_ref.path,schemaVersion:1,revision:f.source_ref.revision,contentHash:f.source_ref.sha256,scope:{projectId:'p',workspaceId:'w'},data:f});
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
const tick=()=>new Promise(done=>setImmediate(done));
function controller(options={}){
 let current=artifact(file()),pid='p',wid='w',discussionId='one',visible=true;const opened=[],calls=[],notices=[],errors=[],navigation=new NavigationOwner();
 const session={fields:{},dirty:false,status:'ready'},editor={isConnected:true,dataset:{editorEpoch:'0',editorComposing:'false'}},document={activeElement:null,body:{}};
 class HTMLElement{matches(){return false;}}
 const api={file:async(...args)=>{calls.push(['GET',...args]);return options.read?options.read(...args):file(2);},edit:async(...args)=>{calls.push(['POST',...args]);return{source_ref:source(2),outcome:'APPLIED'};}};
 let draftText=options.prompt??'',syncs=[],mats=[],activeId='discussion';const syncGate=options.sync;
 const context={api,navigation,quickScope:()=>({projectId:pid,conversationId:discussionId}),associationScope:v=>v,sameDiscussionScope:(a,b)=>JSON.stringify(a)===JSON.stringify(b),project:()=>({id:pid}),workspace:()=>({id:wid,revision:1,binding_sha256:'b'.repeat(64)}),panelVisibility:()=>({workbench:visible}),inspector:()=>current,setInspector:value=>{current=value;opened.push(value);},workspaceStateOwner:{set(){}},objectDraft:()=>session,artifactStateKey,exactFileSource,sameSelectedFile,setNotice:v=>notices.push(v),setError:v=>errors.push(v),authoredCopy,authoredErrorCopy,document,HTMLElement,queueMicrotask,shell:{querySelector:()=>editor},directFile:()=>undefined,persistSelectedDirectFile(){},setDirectFile(){},prompt:()=>draftText,activeSession:()=>({id:activeId}),materials:()=>mats,sourceSyncs:()=>syncs,invalidFeedback:()=>({}),feedbackRecords:()=>[],syncSelectedSource:async()=>{calls.push(['SYNC']);if(syncGate)await syncGate();return {current:source(2),previous:source(),material:{file_source_ref:source(2),document_ref:{document_id:'doc',revision:2}}};},setMaterials:fn=>{mats=fn(mats);},setSourceSyncs:fn=>{syncs=fn(syncs);},setInvalidFeedback(){},localStorage:{setItem(){}},setPrompt:fn=>{draftText=fn(draftText);},fullscreen:()=>false,setAgentHidden(){},setMobileView(){}};
 const actions=new Function(...Object.keys(context),code+'\nreturn{reloadEditor,observeEditorApply,save};')(...Object.values(context));
 return {...actions,session,editor,navigation,opened,calls,notices,errors,document,get current(){return current;},get prompt(){return draftText;},get materials(){return mats;},editPrompt(){draftText='new question';},closeInspector(){current=undefined;},hide(){visible=false;},switchFile(){current=artifact(file(1,{source_ref:source(1,{path:'other.md'})}));navigation.begin();},switchProject(){pid='other';navigation.begin();},switchBinding(){wid='other';},switchDiscussion(){discussionId='two';},api};
}
const applied=(extra={})=>({kind:'APPLY',status:'APPLIED',payload:{project_id:'p',engine_receipt:{payload:{outcomes:[{status:'APPLIED',source_ref:source(2),path:'task.md',workspace_id:'w'}]}}},...extra});

test('App editor reload is GET only; same immutable version keeps pane, new version advances without source/prompt import',async()=>{
 const same=controller({read:async()=>file()});const before=same.current;assert.equal((await same.reloadEditor(before,{canPresent:()=>true})).presented,false);assert.equal(same.current,before);
 const next=controller();assert.equal((await next.reloadEditor(next.current,{canPresent:()=>true})).presented,true);assert.equal(next.current.revision,2);assert.deepEqual(next.calls.map(x=>x[0]),['GET']);
 const ack=await next.save(artifact(file()),'saved body');assert.equal(ack.source_ref.revision,2);assert.deepEqual(next.calls.map(x=>x[0]),['GET','POST']);await ack.syncContext(()=>true);assert.deepEqual(next.calls.map(x=>x[0]),['GET','POST','SYNC']);assert.equal(next.materials.length,1);assert.equal(next.prompt,'');
});

test('in-flight reload cannot replace later input, hidden panel, switched file/project/binding or a different acknowledged version',async()=>{
 for(const kind of ['input','hidden','file','project','binding','discussion','newer']){
  const d=deferred(),view=controller({read:()=>d.promise});let valid=true;const pending=view.reloadEditor(view.current,{canPresent:()=>valid,expected:source(2)});
  if(kind==='input')valid=false;if(kind==='hidden')view.hide();if(kind==='file')view.switchFile();if(kind==='project')view.switchProject();if(kind==='binding')view.switchBinding();if(kind==='discussion')view.switchDiscussion();d.resolve(file(kind==='newer'?3:2));
  assert.equal((await pending).presented,false,kind);assert.equal(view.opened.length,0,kind);
 }
});

test('apply observer captures clean exact-version draft before dispatch, advances once and ignores conflicts',async()=>{
 const view=controller(),observe=view.observeEditorApply(source());observe(applied());await tick();assert.equal(view.opened.length,1);assert.equal(view.current.revision,2);assert.deepEqual(view.calls.map(x=>x[0]),['GET']);
 const conflict=controller();conflict.observeEditorApply(source())(applied({status:'CONFLICT'}));await tick();assert.equal(conflict.calls.length,0);assert.equal(conflict.notices.length,0);
});

test('apply keeps dirty/late edit-undo/hidden/source-changed panes and exact version notice; read failure is not a failed apply',async()=>{
 for(const kind of ['dirty','later','undo','hidden','binding','readfail']){
  const d=deferred(),view=controller({read:()=>d.promise});if(kind==='dirty')view.session.dirty=true;if(kind==='hidden')view.hide();const observe=view.observeEditorApply(source());
  if(kind==='later'){view.session.fields={text:'new words'};view.session.dirty=true;}if(kind==='undo')view.session.fields={};if(kind==='binding')view.switchBinding();observe(applied());await tick();
  if(kind==='readfail')d.reject(Error('GET unavailable'));else d.resolve(file(2));await tick();assert.equal(view.opened.length,0,kind);assert.ok(view.notices.length,kind);
  if(kind==='readfail')assert.equal(view.errors.length,1);
 }
});

const solid=await import(pathToFileURL(resolve(app,'node_modules/solid-js/dist/solid.js')).href);
const universal=readFileSync(resolve(app,'node_modules/solid-js/universal/dist/universal.js'),'utf8').replace("from 'solid-js'",`from '${pathToFileURL(resolve(app,'node_modules/solid-js/dist/solid.js')).href}'`);
const {createRenderer}=await import('data:text/javascript;base64,'+Buffer.from(universal).toString('base64'));
function component(t,dispatch,finish=async()=>{}){
 const host={createElement:type=>({type,props:{},children:[],parent:null}),createTextNode:value=>({type:'#text',value,children:[],parent:null}),isTextNode:n=>n.type==='#text',replaceText:(n,v)=>{n.value=v;},insertNode(p,n,a){n.parent=p;const i=a?p.children.indexOf(a):-1;p.children.splice(i<0?p.children.length:i,0,n);},removeNode(p,n){p.children.splice(p.children.indexOf(n),1);n.parent=null;},setProperty:(n,k,v)=>{n.props[k]=v;},getParentNode:n=>n.parent,getFirstChild:n=>n.children[0],getNextSibling:n=>n.parent?.children[n.parent.children.indexOf(n)+1]};
 const renderer=createRenderer(host),primitive=(type,props)=>{const n=host.createElement(type);renderer.spread(n,props);return n;},parsed=parse('renderers.tsx'),fn=parsed.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='FileRenderer').getText(parsed).replace('export ','');
 let js=ts.transpileModule(fn,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;
 js=babel.transformSync(js,{filename:'FileRenderer.jsx',presets:[[solidPreset,{generate:'universal',moduleName:'test-renderer'}]],configFile:false,babelrc:false}).code;
 js=ts.transpileModule(js,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText;
 let clean=0;const module={exports:{}},bindings={...solid,Button:p=>primitive('button',p),Icon:p=>primitive('svg',p),draftSignal:(_n,v)=>solid.createSignal(v),DraftStatus:()=>null,useDraftSession:()=>({session:{finish,markClean:()=>{clean++;}}}),uiText:s=>s,authoredText,authoredErrorCopy,NativeDocument:()=>null,BitmapFile:()=>null,SavedTextFeedbacks:()=>null};
 const Component=new Function('require','exports',...Object.keys(bindings),js+'\nreturn FileRenderer;')(name=>{assert.equal(name,'test-renderer');return {...solid,...renderer};},module.exports,...Object.values(bindings));
 const root=host.createElement('root'),calls=[],dispose=renderer.render(()=>Component({artifact:artifact(file()),context:{dispatch:(...args)=>{calls.push(args);return dispatch(...args);}}}),root);t.after(dispose);
 const all=(n=root)=>[n,...n.children.flatMap(c=>all(c))],text=(n=root)=>n.type==='#text'?String(n.value):n.children.map(text).join('');
 return {all,text,calls,get clean(){return clean;},field:()=>all().find(n=>n.type==='textarea'),button:label=>all().find(n=>n.type==='button'&&(n.props['aria-label']===label||text(n)===label)),input(value){const n=this.field();n.props.value=value;n.props.onInput({currentTarget:{value}});}};
}

test('actual FileRenderer save ACK survives following GET failure; preserved text and reload retry never repeat POST',async t=>{
 let fail=true;const view=component(t,async(id,_target,input)=>{if(id==='workspace.file.save')return{source_ref:source(2)};assert.equal(id,'workspace.file.reload');if(fail)throw Error('GET unavailable');return{file:file(2),presented:true};});
 view.input('saved user body');await view.button('保存文件').props.onClick();assert.equal(view.field().props.value,'saved user body');assert.equal(view.button('保存文件').props.disabled,true);assert.match(view.text(),/已保存 · 文件版本 2/);assert.match(view.text(),/文件已保存，但新版本尚未读到/);assert.match(view.text(),/GET unavailable/);assert.equal(view.clean,0);
 fail=false;await view.button('读取当前版本').props.onClick();assert.deepEqual(view.calls.map(x=>x[0]),['workspace.file.save','workspace.file.reload','workspace.file.reload']);
});

test('actual FileRenderer later edit/IME blocks ACK auto-read and keeps exact field; explicit read drains original draft before GET',async t=>{
 const ack=deferred();let finished=0;const view=component(t,async id=>id==='workspace.file.save'?ack.promise:{file:file(2),presented:true},async()=>{finished++;});view.input('submitted');const field=view.field(),pending=view.button('保存文件').props.onClick();view.input('later unsaved');ack.resolve({source_ref:source(2)});await pending;assert.equal(view.field(),field);assert.equal(field.props.value,'later unsaved');assert.deepEqual(view.calls.map(x=>x[0]),['workspace.file.save']);assert.equal(view.clean,0);
 await view.button('读取当前版本').props.onClick();assert.equal(finished,2);assert.equal(view.calls.at(-1)[0],'workspace.file.reload');
});

test('actual FileRenderer composition and local draft failure prevent reload without discarding input',async t=>{
 let block=true;const view=component(t,async()=>{throw Error('must not GET');},async()=>{if(block)throw Error('original draft unknown');});view.input('unsaved draft');const field=view.field();await view.button('读取当前版本').props.onClick();assert.match(view.text(),/original draft unknown/);assert.equal(view.calls.length,0);block=false;field.props.onCompositionStart();await view.button('读取当前版本').props.onClick();assert.equal(view.calls.length,0);assert.equal(field.props.value,'unsaved draft');field.props.onCompositionEnd();
});


test('post-save context preserves existing import behavior but cannot publish after a changed discussion draft',async()=>{
 const gate=deferred(),view=controller({sync:()=>gate.promise}),ack=await view.save(view.current,'saved');const pending=ack.syncContext(()=>true);view.editPrompt();gate.resolve();await pending;assert.equal(view.prompt,'new question');assert.equal(view.materials.length,0);assert.deepEqual(view.calls.map(x=>x[0]),['POST','SYNC']);
 const noInput=controller(),original=await noInput.save(noInput.current,'saved');await original.syncContext(()=>false);assert.deepEqual(noInput.calls.map(x=>x[0]),['POST']);
});

test('apply ACK with compositionstart before any input retains exact old editor; GET-in-flight composition also fences replacement',async()=>{
 for(const stage of ['before-ack','during-get']){const d=deferred(),view=controller({read:()=>d.promise}),old=view.current,observe=view.observeEditorApply(source());if(stage==='before-ack'){view.editor.dataset.editorEpoch='1';view.editor.dataset.editorComposing='true';}observe(applied());if(stage==='during-get'){await tick();view.editor.dataset.editorEpoch='1';view.editor.dataset.editorComposing='true';}d.resolve(file(2));await tick();assert.equal(view.current,old);assert.equal(view.opened.length,0);assert.equal(view.session.dirty,false);assert.deepEqual(view.session.fields,{});}
});

test('actual panel accepts original APPLIED receipt before the independent read failure; no second apply or UNKNOWN replacement',async()=>{
 const p=parse('NativeReworkPanel.tsx');let body;const walk=n=>{if(ts.isFunctionDeclaration(n)&&n.name?.text==='apply')body=n.getText(p);ts.forEachChild(n,walk);};walk(p);assert.ok(body);
 const view=controller({read:async()=>{throw Error('after-ACK GET failure');}}),record=applied(),accepted=[];let applyCalls=0;const context={plan:()=>({id:'plan'}),applyApproved:()=>true,unresolved:()=>false,alreadyApplied:()=>false,allowRebase:()=>false,client:()=>({apply:async()=>{applyCalls++;return record;}}),localStorage:{},alive:true,accept:v=>accepted.push(v),props:{onBeforeApply:()=>view.observeEditorApply(source()),onApplied:v=>assert.equal(v,record)}};const apply=new Function(...Object.keys(context),ts.transpileModule(body,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText+'\nreturn apply;')(...Object.values(context));await apply();await tick();assert.equal(accepted[0].status,'APPLIED');assert.equal(applyCalls,1);assert.equal(view.errors.length,1);assert.equal(view.opened.length,0);
});


test('saved v2 ACK remains exact when a later external v3 is observed; old basis cannot save again',async t=>{
 const view=component(t,async id=>id==='workspace.file.save'?{source_ref:source(2)}:{file:file(3),presented:false});view.input('submitted v2');await view.button('保存文件').props.onClick();assert.match(view.text(),/已保存 · 文件版本 2/);assert.match(view.text(),/实际更新版本 v3/);assert.equal(view.button('保存文件').props.disabled,true);assert.deepEqual(view.calls.map(x=>x[0]),['workspace.file.save','workspace.file.reload']);
});

test('an old apply follow-up rejection cannot publish into another discussion or hidden/different file',async()=>{
 for(const kind of ['discussion','hidden','file']){const d=deferred(),view=controller({read:()=>d.promise}),observe=view.observeEditorApply(source());observe(applied());await tick();if(kind==='discussion')view.switchDiscussion();if(kind==='hidden')view.hide();if(kind==='file')view.switchFile();d.reject(Error('old A read failed'));await tick();assert.equal(view.errors.length,0,kind);assert.equal(view.opened.length,0,kind);assert.deepEqual(view.calls.map(x=>x[0]),['GET']);}
});


test('post-save context failure remains visible after the editor advanced and never changes the acknowledged write',async()=>{
 const view=controller({sync:async()=>{throw Error('SOURCE_IMPORT_FAILED');}}),ack=await view.save(view.current,'saved');assert.equal(ack.source_ref.revision,2);await view.reloadEditor(view.current,{expected:ack.source_ref,canPresent:()=>true});assert.equal(view.current.revision,2);await assert.rejects(()=>ack.syncContext(()=>true),/SOURCE_IMPORT_FAILED/);assert.equal(view.errors.length,1);assert.equal(view.errors[0].message_key,'{saved}{version}{revision} · {context}: {detail}');assert.equal(view.errors[0].params.revision,2);assert.equal(view.errors[0].params.saved.message_key,'已保存 · ');assert.equal(view.errors[0].params.version.message_key,'文件版本 ');assert.equal(view.errors[0].params.context.message_key,'同步原件');assert.deepEqual(view.errors[0].params.detail,authoredErrorCopy(Error('SOURCE_IMPORT_FAILED')));assert.deepEqual(view.calls.map(x=>x[0]),['POST','GET','SYNC']);
});


test('returning to workbench during the acknowledged save GET keeps ACK but cannot start late source sync or reopen the file',async()=>{
 const d=deferred(),view=controller({read:()=>d.promise}),target=view.current,ack=await view.save(target,'saved body');assert.equal(ack.source_ref.revision,2);const read=view.reloadEditor(target,{canPresent:()=>true,expected:ack.source_ref});view.closeInspector();d.resolve(file(2));assert.equal((await read).presented,false);await ack.syncContext(()=>true);assert.equal(view.current,undefined);assert.equal(view.opened.length,0);assert.equal(view.materials.length,0);assert.equal(view.prompt,'');assert.deepEqual(view.calls.map(x=>x[0]),['POST','GET']);assert.equal(ack.source_ref.sha256,source(2).sha256);
});


test('acknowledged save synchronizes the actual material without writing system metadata into empty or existing user input',async()=>{for(const text of ['', 'Keep this exact user question 😀\nDo not rewrite it.']){const view=controller({prompt:text}),ack=await view.save(view.current,'saved file');await ack.syncContext(()=>true);assert.equal(view.prompt,text);assert.equal(view.materials.length,1);assert.deepEqual(view.materials[0].document_ref,{document_id:'doc',revision:2});assert.deepEqual(view.materials[0].file_source_ref,source(2));assert.deepEqual(view.calls.map(x=>x[0]),['POST','SYNC']);}});
