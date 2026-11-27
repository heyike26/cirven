import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
import {resolve,dirname,extname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';
import * as solid from '../node_modules/solid-js/dist/solid.js';
import {nativeReportArtifact,nativeRevisionChanges,openNativeReportArtifact} from '../src/native-artifacts.ts';
import {NativeMessageClient,restoreNativeHistory,nativeReference} from '../src/native-collaboration.ts';
import {associationScope,sameDiscussionScope,sameFileLink,DiscussionFileContext} from '../src/discussion-file-context.ts';
import {directFileSupported,exactFileSource} from '../src/companion-files.ts';
import {authoredErrorCopy} from '../src/authored-ui-copy.ts';

const app=resolve(dirname(fileURLToPath(import.meta.url)),'..'),sha='a'.repeat(64),pid='project_one';
const ref=(id)=>({id,revision:1,sha256:sha}),link={...ref('source_link'),project_id:pid},scope={projectId:pid,workspaceId:'workspace_one',conversationId:'conversation_one'};
const change=(index=0)=>({part:'word/document.xml',paragraph_path:`/document[1]/body[1]/p[${index+1}]`,char_start:0,char_end:2,quote:'原文',quote_sha256:sha,replacement:'用户修改建议 😀 '+index,reason:'保留用户提供的依据'});
const artifact=()=>({kind:'docx_revision',id:'b'.repeat(64),project_id:pid,revision:1,sha256:'c'.repeat(64),filename:'revision-bbbbbbbbbbbb.docx',
 download_url:`/api/projects/${pid}/docx-revisions/artifacts/${'b'.repeat(64)}/download`,
 owner:{operation_id:'native_question',generation:'host_generation',conversation_id:'conversation_one'},source_link_ref:link,snapshot_ref:{...ref('frozen_snapshot'),project_id:pid},
 source_sha256:sha,proposal_ref:ref('proposal'),review:{changes:[change()],mode:'WORD_TRACKED_INSERTIONS_AND_DELETIONS'},source_files_modified:false,changes_applied:false,later_source_edits_included:false,execution_effects:'CREATE_NEW_DERIVED_DOCX'});
const message=(file=artifact())=>({operation_id:'native_question',status:'RETURNED',text:'原模型回复保留 😀',project_id:pid,conversation_id:'conversation_one',file_link_ref:link,artifact_status:'NEW_ARTIFACT',artifacts:[file]});
function freeze(value){if(value&&typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value);}return value;}
const tick=()=>new Promise(done=>setImmediate(done));

test('exact artifact target keeps native route/parent refs and rejects cross-project, operation, conversation, source and unsafe URL',()=>{
 const item=freeze(artifact()),record=freeze(message(item)),bytes=JSON.stringify(record);
 assert.equal(nativeReportArtifact(item,record).revision,item);
 const bad=[{...item,project_id:'other'}, {...item,download_url:'https://example.invalid/file.docx'},
  {...item,download_url:item.download_url+'?x=1'}, {...item,download_url:item.download_url.replace('/download','/../download')},
  {...item,owner:{...item.owner,operation_id:'another'}}, {...item,owner:{...item.owner,conversation_id:'another'}},
  {...item,source_link_ref:{...link,revision:2}}, {...item,snapshot_ref:{...item.snapshot_ref,project_id:'other'}},
  {...item,source_files_modified:true}, {...item,changes_applied:true}, {...item,later_source_edits_included:true}];
 for(const value of bad)assert.equal(nativeReportArtifact(value,record),undefined);
 assert.equal(JSON.stringify(record),bytes);
 const ordinary={id:'d'.repeat(64),project_id:pid,sha256:sha,download_url:`/api/projects/${pid}/report-artifacts/${'d'.repeat(64)}/download`};
 assert.equal(nativeReportArtifact(ordinary).artifactId,ordinary.id);
 assert.equal(nativeReportArtifact({...ordinary,download_url:ordinary.download_url.replace(pid,'other')}),undefined);
});

test('one click opens through original typed report IPC, preserving original scope and no model dispatch or replay after lost acknowledgement',async()=>{
 const item=artifact(),record=message(item),calls=[],bridge={openReportArtifact:async(...args)=>{calls.push(args);throw Error('transport lost after accepted');}};
 await assert.rejects(()=>openNativeReportArtifact(bridge,item,scope,{message:record}),/transport lost/);
 assert.equal(calls.length,1);
 assert.deepEqual(calls[0],[pid,item.id,item.sha256,item.download_url,{scope}]);
 await assert.rejects(()=>openNativeReportArtifact(bridge,item,{...scope,conversationId:'other'},{message:record}),/当前工作已切换/);
 await assert.rejects(()=>openNativeReportArtifact(bridge,item,scope,{message:record,current:()=>false}),/当前工作已切换/);
 assert.equal(calls.length,1);
 await assert.rejects(()=>openNativeReportArtifact({openReportArtifact:async()=>({status:'UNKNOWN'})},item,scope,{message:record}),/系统未确认/);
});

test('host split changes through256 remain valid; oversized/malformed details are not invented or silently truncated',()=>{
 const item=artifact();item.review.changes=Array.from({length:256},(_,index)=>change(index));
 assert.equal(nativeRevisionChanges(item),item.review.changes);
 assert.equal(nativeRevisionChanges({...item,review:{...item.review,changes:[...item.review.changes,change(256)]}}),undefined);
 assert.equal(nativeRevisionChanges({...item,review:{...item.review,changes:[{...change(),char_end:-1}]}}),undefined);
 assert.equal(nativeRevisionChanges({...item,review:{...item.review,changes:[{...change(),replacement:'x'.repeat(8001)}]}}),undefined);
});

test('history restores exact real-message artifacts/errors by GET only, and never republishes or invokes a model',async()=>{
 const original=freeze(message()),calls=[];
 const client=new NativeMessageClient({inspectNativeMessage:async id=>{calls.push(['GET',id]);return original;},submitNativeMessage:()=>assert.fail('no replay')});
 const restored=await restoreNativeHistory(client,[{operation_id:original.operation_id,input:'原提问'}]);
 assert.deepEqual(calls,[['GET',original.operation_id]]);
 assert.equal(restored[0].message.artifacts[0],original.artifacts[0]);
 assert.equal(nativeReportArtifact(restored[0].message.artifacts[0],restored[0].message).revision.source_link_ref,link);
});

// Actual production App function bodies, not a test reimplementation. UI state,
// navigation and Electron IPC are UNIT boundaries; this does not qualify shell.
function appOpenHarness(bridge,options={}){
 const source=ts.createSourceFile('App.tsx',readFileSync(resolve(app,'src/App.tsx'),'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX),names=new Set(['action','nativeArtifactSource','nativeArtifactAvailable','openNativeArtifact','openFeedbackDocument','ownsOpenedFeedbackFile']);
 const selected=[];function visit(node){if(ts.isFunctionDeclaration(node)&&names.has(node.name?.text))selected.push(node.getText(source));ts.forEachChild(node,visit);}visit(source);
 assert.equal(selected.length,names.size);
 const compiled=ts.transpileModule(selected.join('\n'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.None}}).outputText;
 const state={busy:'',error:'',notice:'',scope:{...scope},project:{id:pid},current:true,adopted:[],refresh:0};
 const dependencies={nativeBridge:bridge,nativeReportArtifact,openNativeReportArtifact,associationScope,sameDiscussionScope,sameFileLink,directFileSupported,exactFileSource,nativeReference,quickScope:()=>state.scope,
  nativeFeedbackLease:()=>JSON.stringify(state.scope),directFile:()=>state.selected,setOpenedFeedbackFile:value=>state.opened=value,setSuspendedFeedbackGeneration:value=>state.suspended=value,
  navigation:{capture:()=>()=>state.current},project:()=>state.project,busy:()=>state.busy,setBusy:value=>state.busy=value,setError:value=>state.error=value,setNotice:value=>state.notice=value,authoredErrorCopy,
  adoptDirectSource:async value=>{state.adopted.push(value);state.selected={file:{source_ref:value,current:true,encoding:'utf-8',byte_length:0,content:''}};},discussionFiles:{refresh:async()=>{state.refresh++;}},...options};
 const result=new Function(...Object.keys(dependencies),'let openingFeedbackFile;const directFileGeneration=1;'+compiled+'\nreturn {nativeArtifactAvailable,openNativeArtifact};')(...Object.values(dependencies));
 return {...result,state};
}
test('actual App report branch opens same-conversation revision and refreshes Main-owned new link; legacy local file branch remains intact',async()=>{
 const calls=[],view=appOpenHarness({openReportArtifact:async(...args)=>{calls.push(['report',...args]);return {status:'OPEN_REQUESTED'};},openDocument:async(...args)=>{calls.push(['file',...args]);return {status:'OPEN_REQUESTED'};}}),item=artifact();
 assert.equal(view.nativeArtifactAvailable(item,message(item)),true);
 await view.openNativeArtifact(item,message(item));assert.equal(calls.length,1);assert.equal(calls[0][0],'report');assert.equal(view.state.refresh,1);assert.equal(view.state.adopted.length,0);
 const source={kind:'local_file',project_id:pid,workspace_id:'workspace_one',binding_revision:1,binding_sha256:sha,path:'result.txt',revision:2,sha256:sha};
 assert.equal(view.nativeArtifactAvailable({filename:'result.txt',file_source_ref:source}),true);
 await view.openNativeArtifact({filename:'result.txt',file_source_ref:source});assert.equal(calls.length,2);assert.equal(calls[1][0],'file');assert.equal(view.state.adopted[0],source);assert.equal(view.state.error,'');
});
test('actual App late open acknowledgement cannot refresh another topic or post success into its draft',async()=>{
 let complete;const calls=[],view=appOpenHarness({openReportArtifact:async(...args)=>{calls.push(args);return await new Promise(resolve=>complete=resolve);}}),item=artifact();
 const pending=view.openNativeArtifact(item,message(item));await tick();assert.equal(calls.length,1);
 view.state.scope={...scope,conversationId:'other'};view.state.current=false;complete({status:'OPEN_REQUESTED'});await pending;
 assert.equal(view.state.refresh,0);assert.equal(view.state.notice,'');assert.equal(view.state.busy,'');
});
test('actual App and discussion controller adopt the returned working-copy link in the same conversation while preserving the artifact parent',async()=>{
 const item=freeze(artifact()),next={...ref('new_working_copy'),project_id:pid},states=[];let current={scope,selected:{scope,fileLinkRef:link,filename:'original.docx'},legacy_candidates:[]};
 const bridge={getQuickFileContext:async()=>current,openReportArtifact:async()=>{current={scope,selected:{scope,fileLinkRef:next,filename:item.filename},legacy_candidates:[]};return {status:'OPEN_REQUESTED'};}};
 const discussion=new DiscussionFileContext(bridge,state=>states.push(state),error=>assert.equal(error,''));await discussion.setScope(scope);
 const view=appOpenHarness(bridge,{discussionFiles:discussion});await view.openNativeArtifact(item,message(item));
 assert.equal(states.at(-1).selected.fileLinkRef,next);assert.deepEqual(states.at(-1).scope,scope);assert.equal(item.source_link_ref,link);discussion.stop();
});

const universalSource=readFileSync(resolve(app,'node_modules/solid-js/universal/dist/universal.js'),'utf8').replace("from 'solid-js'",`from '${pathToFileURL(resolve(app,'node_modules/solid-js/dist/solid.js')).href}'`);
const {createRenderer}=await import('data:text/javascript;base64,'+Buffer.from(universalSource).toString('base64'));
function componentHarness(t){
 const previous={},values=new Map(),storage={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,String(value))};
 for(const key of ['window','navigator','localStorage','eeasDesktop'])previous[key]=Object.getOwnPropertyDescriptor(globalThis,key);
 Object.defineProperty(globalThis,'window',{configurable:true,value:{addEventListener(){},removeEventListener(){}}});
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{language:'zh-CN'}});Object.defineProperty(globalThis,'localStorage',{configurable:true,value:storage});delete globalThis.eeasDesktop;
 const host={createElement:type=>({type,props:{},children:[],parent:null}),createTextNode:value=>({type:'#text',value,children:[],parent:null}),isTextNode:node=>node.type==='#text',replaceText:(node,value)=>{node.value=value;},
  insertNode(parent,node,anchor){if(node.parent){const old=node.parent.children;old.splice(old.indexOf(node),1);}node.parent=parent;const at=anchor?parent.children.indexOf(anchor):-1;parent.children.splice(at<0?parent.children.length:at,0,node);},
  removeNode(parent,node){const index=parent.children.indexOf(node);if(index>=0)parent.children.splice(index,1);node.parent=null;},setProperty:(node,name,value)=>{node.props[name]=value;},getParentNode:node=>node.parent,getFirstChild:node=>node.children[0],getNextSibling:node=>node.parent?.children[node.parent.children.indexOf(node)+1]};
 const renderer=createRenderer(host),modules=new Map(),disposes=[];
 function load(file){file=resolve(file);if(file.endsWith('.css'))return {};if(!extname(file))file+=existsSync(file+'.tsx')?'.tsx':'.ts';if(modules.has(file))return modules.get(file).exports;if(file.endsWith('.json'))return JSON.parse(readFileSync(file,'utf8'));
  const module={exports:{}};modules.set(file,module);let code=readFileSync(file,'utf8');
  if(file.endsWith('.tsx')){code=ts.transpileModule(code,{fileName:file,compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;code=babel.transformSync(code,{filename:file+'.jsx',presets:[[solidPreset,{generate:'universal',moduleName:'test-renderer'}]],configFile:false,babelrc:false}).code;}
  code=ts.transpileModule(code,{fileName:file.endsWith('.tsx')?file+'.js':file,compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText;
  const require=name=>name==='solid-js'?solid:name==='test-renderer'?renderer:name.startsWith('.')?load(resolve(dirname(file),name)):(()=>{throw Error('Unexpected test import '+name);})();
  try{new Function('require','module','exports',code)(require,module,module.exports);}catch(error){error.message+=' [actual component loader: '+file+']';throw error;}return module.exports;
 }
 const language=load(resolve(app,'src/ui-language.ts')),release=language.startUILanguage();
 t.after(()=>{disposes.reverse().forEach(fn=>fn());release();for(const[key,value]of Object.entries(previous))if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];});
 function mount(props){const Component=load(resolve(app,'src/NativeMessageArtifacts.tsx')).default,container=host.createElement('root');disposes.push(renderer.render(()=>Component(props),container));
  const all=()=>{const list=[];function walk(node){list.push(node);node.children.forEach(walk);}walk(container);return list;},text=node=>node.type==='#text'?String(node.value):node.children.map(text).join(' ');
  return {all,text:()=>text(container),find:(type,filter=()=>true)=>all().find(node=>node.type===type&&filter(node))};}
 return {mount,async locale(value){await language.setUILanguagePreference(value);await tick();}};
}
function click(button){assert.ok(button);assert.notEqual(button.props.disabled,true);button.props.onClick({preventDefault(){}});}
function toggle(details,open=true){assert.ok(details);details.props.onToggle({currentTarget:{open}});}

test('actual revision component reveals bounded real changes only on request, preserves user text and open state across languages, and dispatches once',async t=>{
 const h=componentHarness(t),item=freeze(artifact()),record=freeze(message(item)),before=JSON.stringify(record),calls=[];
 const view=h.mount({message:record,canOpen:()=>true,onOpen:value=>calls.push(value)});await tick();
 const button=view.find('button',node=>node.props.class.includes('as-native-artifact')),details=view.find('details',node=>node.props.class==='as-native-revision-review');
 assert.match(view.text(),/新修订稿/);assert.equal(view.all().some(node=>node.type==='del'),false);assert.equal(view.all().some(node=>node.type==='pre'),false);assert.deepEqual(calls,[]);
 click(button);assert.equal(calls[0],item);toggle(details);await tick();assert.match(view.text(),/用户修改建议 😀 0/);assert.match(view.text(),/原文/);
 await h.locale('en');assert.equal(view.find('button',node=>node.props.class.includes('as-native-artifact')),button);assert.equal(view.find('details',node=>node.props.class==='as-native-revision-review'),details);
 assert.match(view.text(),/New revision · original preserved/);assert.match(view.text(),/用户修改建议 😀 0/);assert.equal(view.all().some(node=>node.type==='pre'),false);
 await h.locale('zh-CN');assert.match(view.text(),/新修订稿/);assert.equal(JSON.stringify(record),before);assert.equal(calls.length,1);
});
test('actual component keeps 256 host-split edits reachable without eagerly filling the reply with them',async t=>{
 const h=componentHarness(t),item=artifact();item.review.changes=Array.from({length:256},(_,index)=>change(index));
 const view=h.mount({message:message(item),canOpen:()=>true,onOpen:()=>assert.fail('review is not open action')});await tick();
 assert.equal(view.all().filter(node=>node.type==='li').length,0);
 toggle(view.find('details',node=>node.props.class==='as-native-revision-review'));await tick();assert.equal(view.all().filter(node=>node.type==='li').length,20);assert.match(view.text(),/其余修订（236）/);
 toggle(view.find('details',node=>node.props.class==='as-native-revision-remaining'));await tick();assert.equal(view.all().filter(node=>node.type==='li').length,256);assert.match(view.text(),/用户修改建议 😀 255/);
});
test('RETURNED artifact failure remains visible and GET-inspectable, clarification stays distinct, and no missing file becomes an enabled card',async t=>{
 const h=componentHarness(t),calls=[],[current,setCurrent]=solid.createSignal({...message(),artifacts:[],artifact_status:'UNAVAILABLE',artifact_error:{message:'回复已收到，修订文件未完成生成；原文件和提案保留，请核查原操作，勿自动重发。'}});
 const view=h.mount({get message(){return current();},canOpen:()=>false,onOpen:()=>assert.fail('no artifact'),onInspect:()=>calls.push(['GET','native_question'])});await tick();
 assert.ok(view.find('p',node=>node.props.role==='alert'));assert.match(view.text(),/修订文件未完成生成/);click(view.find('button'));assert.deepEqual(calls,[['GET','native_question']]);
 await h.locale('en');assert.match(view.text(),/reply was received/);assert.equal(current().status,'RETURNED');assert.match(current().artifact_error.message,/回复已收到/);
 setCurrent({...current(),artifact_error:undefined,artifact_status:'NEEDS_CLARIFICATION'});await tick();assert.match(view.text(),/needs clarification/);assert.equal(view.all().some(node=>node.props?.class?.includes('as-native-artifact ')),false);
 setCurrent({...message(),artifacts:[{kind:'docx_revision',filename:'not-ready.docx'}]});await tick();assert.equal(view.find('button',node=>node.props.class?.includes('as-native-artifact')).props.disabled,true);
 assert.equal(calls.length,1);
});

test('actual canceled-before-publication and unknown-publication states preserve the returned answer and never offer a regenerate action',async t=>{
 const h=componentHarness(t),calls=[],[current,setCurrent]=solid.createSignal({...message(),artifacts:[],artifact_status:'PREPARING'});
 const view=h.mount({get message(){return current();},canOpen:()=>false,onOpen:()=>assert.fail('no artifact'),onInspect:()=>calls.push(['GET','native_question'])});await tick();
 assert.match(view.text(),/正在生成修订稿/);
 setCurrent({...current(),artifact_status:'CANCELLED_NOT_PUBLISHED'});await tick();assert.match(view.text(),/已停止生成修订文件/);assert.equal(current().status,'RETURNED');assert.equal(current().text,'原模型回复保留 😀');assert.equal(view.find('button'),undefined);assert.deepEqual(calls,[]);
 await h.locale('en');assert.match(view.text(),/Revision file generation stopped/);
 setCurrent({...current(),artifact_status:'PUBLICATION_UNKNOWN'});await tick();assert.match(view.text(),/publication outcome is unconfirmed/);assert.doesNotMatch(view.text(),/No file has been created|generation stopped/);click(view.find('button'));assert.deepEqual(calls,[['GET','native_question']]);
 setCurrent({...current(),artifact_error:{message:'修订文件的生成结果尚未确认；请核查原操作，勿重新生成。'}});await tick();assert.match(view.text(),/publication outcome is unconfirmed/);assert.equal(view.all().filter(node=>node.type==='button').length,1);
 assert.equal(current().status,'RETURNED');assert.equal(current().artifacts.length,0);assert.equal(calls.length,1);
});
