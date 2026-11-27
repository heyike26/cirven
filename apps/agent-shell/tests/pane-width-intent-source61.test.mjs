import {baseline as readBaseline,evidenceDirectory} from './historical-renderer-evidence.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,writeFileSync} from 'node:fs';
import {resolve,dirname,extname} from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const app=resolve(dirname(fileURLToPath(import.meta.url)),'..'),req=createRequire(resolve(app,'package.json'));
const ts=req('typescript'),babel=req('@babel/core'),esbuild=req('esbuild');
const preset=(await import(resolve(app,'node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js'))).default;
function load(file){file=resolve(file);if(!extname(file))file+='.ts';const module={exports:{}};new Function('require','module','exports',ts.transpileModule(readFileSync(file,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText)(name=>load(resolve(dirname(file),name)),module,module.exports);return module.exports;}
const {projectPaneWidths,savedPaneWidth,fitPaneWidths,READING_AGENT_WIDTH_KEY}=load(resolve(app,'src/panes.ts'));
test('projection preserves explicit intent across narrow fit and derives a fresh chat default without a key',()=>{
 const intent={files:320,agent:820};assert.deepEqual(projectPaneWidths(intent,768),{files:220,agent:360});assert.deepEqual(projectPaneWidths(intent,1920),intent);assert.deepEqual(intent,{files:320,agent:820});
 const automatic={files:236};assert.equal(projectPaneWidths(automatic,768).agent,360);assert.equal(projectPaneWidths(automatic,1920).agent,800);
 for(const bad of [null,'',' ','NaN','Infinity','0','-20'])assert.equal(savedPaneWidth(bad,'agent'),undefined);
 assert.equal(savedPaneWidth('820','agent'),820);assert.equal(savedPaneWidth('10000','files'),440);
 assert.deepEqual(projectPaneWidths({files:300,agent:750},1440),fitPaneWidths(300,750,1440));
});
function fragments(source){
 const tree=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX),nodes=[];function walk(n){nodes.push(n);ts.forEachChild(n,walk);}walk(tree);
 const variable=name=>{const node=nodes.find(n=>ts.isVariableDeclaration(n)&&ts.isIdentifier(n.name)&&n.name.text===name);assert.ok(node,name);return 'const '+node.getText(tree)+';';};
 const effect=needle=>{const n=nodes.find(n=>ts.isCallExpression(n)&&n.expression.getText(tree)==='createEffect'&&n.getText(tree).includes(needle));assert.ok(n,needle);return n.getText(tree)+';';};
 const start=source.indexOf(source.includes('const [paneIntent,')?'const [paneIntent,':'const initialPanes='),end=source.indexOf('const [filesHidden,',start);assert.ok(start>=0&&end>start);
 const separators=nodes.filter(n=>ts.isJsxSelfClosingElement(n)&&n.tagName.getText(tree)==='PaneSeparator').map(n=>n.getText(tree));assert.equal(separators.length,2);
 const area=nodes.find(n=>ts.isJsxSelfClosingElement(n)&&n.tagName.getText(tree)==='textarea'&&n.getText(tree).includes('描述要完成的工作'));assert.ok(area);
 return {setup:source.slice(start,end),resized:variable('resized'),restore:effect('workspaceStateRelease=owner.subscribe'),persist:effect('const values={files_width:'),separators,textarea:area.getText(tree)};
}
test('real App divider handler rejects hidden-mode edits and writes only the explicitly changed side',()=>{
 const f=fragments(readFileSync(resolve(app,'src/App.tsx'),'utf8')),store=new Map([['eeas-file-width','300'],['eeas-agent-width-v2','780']]),writes=[];
 const solid=req('solid-js/dist/solid.cjs');let full=false,mode='workbench',single=false,reading=false;
 const code=ts.transpileModule(f.setup+'return {change:changePaneWidth,resize:resizePanes,intent:paneIntent,widths:()=>({files:fileWidth(),agent:agentWidth()})};',{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText;
 solid.createRoot(dispose=>{try{
  const target=new Function('createSignal','createEffect','untrack','projectPaneWidths','savedPaneWidth','localStorage','window','fullscreen','windowMode','singlePane','readingWorkspace','READING_AGENT_WIDTH_KEY',code)(solid.createSignal,solid.createEffect,solid.untrack,projectPaneWidths,savedPaneWidth,{getItem:key=>store.get(key)??null,setItem:(key,value)=>{writes.push([key,value]);store.set(key,value);}},{innerWidth:1920},()=>full,()=>mode,()=>single,()=>reading,READING_AGENT_WIDTH_KEY);
  for(const blocked of ['fullscreen','companion','single']){full=blocked==='fullscreen';mode=blocked==='companion'?'companion':'workbench';single=blocked==='single';target.change('files',420);target.change('agent',900);}
  assert.deepEqual(target.intent(),{files:300,agent:780});assert.equal(writes.length,0);
  full=false;mode='workbench';single=false;target.change('files',340);assert.deepEqual(target.widths(),{files:340,agent:780});assert.deepEqual(writes,[['eeas-file-width','340']]);
  target.change('agent',840);assert.deepEqual(target.intent(),{files:340,agent:840});assert.deepEqual(writes.at(-1),['eeas-agent-width-v2','840']);
  reading=true;target.resize();assert.equal(target.widths().agent,420);target.change('agent',500);assert.equal(target.widths().agent,500);assert.equal(target.intent().agent,840);assert.equal(store.get('eeas-agent-width-v2'),'840');assert.equal(store.get(READING_AGENT_WIDTH_KEY),'500');reading=false;target.resize();assert.equal(target.widths().agent,840);reading=true;target.resize();assert.equal(target.widths().agent,500);
 }finally{dispose();}});
});
test('compile real App width callbacks, workspace restoration and real pointer separators without a width stub',async()=>{
 const out=evidenceDirectory('pane-width-intent-source61'),baseline=readBaseline('pane','EEAS_PANE_BASELINE');
 const plugin={name:'source61-real-solid',setup(build){build.onLoad({filter:/\.tsx$/},async({path})=>({contents:babel.transformSync(ts.transpileModule(readFileSync(path,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText,{filename:path+'.jsx',presets:[[preset,{generate:'dom',hydratable:false}]],configFile:false,babelrc:false}).code,loader:'js',resolveDir:dirname(path)}));}};
 try{for(const variant of ['before','after']){
 const src=variant==='before'?resolve(baseline,'apps/agent-shell/src'):resolve(app,'src'),f=fragments(readFileSync(resolve(src,'App.tsx'),'utf8'));
 const program=`import {render} from 'solid-js/web';import {createEffect,createSignal,onCleanup,untrack,Show} from 'solid-js';
import {fitPaneWidths,${variant==='after'?'projectPaneWidths,savedPaneWidth,READING_AGENT_WIDTH_KEY,type PaneWidthIntent,':''}} from '${resolve(src,'panes.ts')}';
import {defaultConversationWidth} from '${resolve(app,'src/chat-workbench-layout.ts')}';
import PaneSeparator from '${resolve(app,'src/PaneSeparator.tsx')}';import NativePageConversation from '${resolve(app,'src/NativePageConversation.tsx')}';
import {t as uiText,startUILanguage} from '${resolve(app,'src/ui-language.ts')}';
function Fixture(){onCleanup(startUILanguage());
 const [fullscreen,setFullscreen]=createSignal(false),[windowMode,setWindowMode]=createSignal('workbench'),[singlePane,setSinglePane]=createSignal(innerWidth<=1100);
 function readingWorkspace(){return false;}
 ${f.setup}
 const [filesHidden,setFilesHidden]=createSignal(false),[workspace,setWorkspace]=createSignal<any>(),[reviewTab,setReviewTab]=createSignal('current');
 let workspaceStateRelease,workspaceStateOwner;const owners=new Map();const writes=[];
 function objectDraft(key){let owner=owners.get(key);if(owner)return owner;let resolveReady;owner={status:'loading',view:{},ready:new Promise(r=>resolveReady=r),listeners:new Set(),subscribe(fn){this.listeners.add(fn);return()=>this.listeners.delete(fn);},set(channel,key,value){this.view[key]=value;writes.push({workspace:keyIdentity(this.key),key,value:structuredClone(value)});},key,resolve(value){this.view=value;this.status='ready';for(const fn of this.listeners)fn();resolveReady();}};owners.set(key,owner);return owner;}
 function workspaceStateKey(w){return w.id;}function keyIdentity(key){return key;}function setDraftRecords(){}function refreshDraftRecords(){}function inspector(){return undefined;}function interactionBlocking(){return false;}function restoreDraftObject(){}const navigation={capture:()=>()=>true};
 ${f.restore}
 ${f.persist}
 ${f.resized}window.addEventListener('resize',resized);onCleanup(()=>window.removeEventListener('resize',resized));
 const [mobileView,setMobileView]=createSignal('agent'),[prompt,setPrompt]=createSignal('QA61未发送草稿：拖动与往返保持'),[focus,setFocus]=createSignal(false);let shell;const composerView=()=>({focused:focus(),folded:false}),composerPresentation={focus:setFocus};function panelVisibility(){return {agent:true};}function submitPrompt(){throw Error('No send allowed');}
 const root=<div class="as-app-frame" data-native-titlebar="true"><div class="qa-titlebar">Cirven</div><div ref={shell} class="as-shell" data-layout="workbench" data-chat-layout="adaptive" data-window-mode={windowMode()} data-fullscreen={String(fullscreen())} data-fullscreen-files="false" data-fullscreen-agent="false" data-compact={String(singlePane()&&innerWidth<=920)} data-mobile-view={mobileView()} data-files-hidden={String(filesHidden())} data-agent-hidden="false" data-workbench-hidden="false" style={{'--as-file-width':fileWidth()+'px','--as-agent-width':agentWidth()+'px'}}>
 <aside class="as-sidebar"><section class="as-file-pane"><strong>当前项目文件</strong><p>源码与文档保留原目录。</p></section></aside>${f.separators[0]}
 <section class="as-workbench"><header class="as-topbar"><strong>当前工作</strong></header><div class="as-workspace-body">同一任务的产物与原文件</div></section>${f.separators[1]}
 <aside class="as-agent"><div class="as-agent-content"><header class="as-agent-header"><strong>当前对话</strong></header><main class="as-conversation"><p>同一份讨论正文，宽窗应该恢复充足阅读空间。</p><p>文件栏和对话栏的宽度是用户意图；最小窗口只改变当前显示。</p></main><form class="as-composer" onSubmit={e=>e.preventDefault()}><NativePageConversation scopeKey="qa61" attachments={[]} input={${f.textarea}} toolbar={<div class="as-composer-toolbar"><button type="button">需求梳理</button><button type="button">选择模型</button><button type="button">发送</button></div>}/></form></div></aside></div></div>;
 window.fixture={state:()=>({files:fileWidth(),agent:agentWidth(),intent:${variant==='after'?'paneIntent()':'null'},single:singlePane(),mode:windowMode(),fullscreen:fullscreen(),fileKey:localStorage.getItem('eeas-file-width'),agentKey:localStorage.getItem('eeas-agent-width-v2'),writes:structuredClone(writes)}),workspace:id=>setWorkspace({id,project_id:'qa61'}),resolveWorkspace:(id,value)=>owners.get(id).resolve({panes:value}),mode:setWindowMode,fullscreen:setFullscreen};return root;}
render(()=> <Fixture/>,document.getElementById('root'));`;
 const entry=resolve(out,variant+'.tsx');writeFileSync(entry,program);await esbuild.build({entryPoints:[entry],outfile:resolve(out,variant+'.js'),bundle:true,platform:'browser',format:'iife',conditions:['browser'],nodePaths:[resolve(app,'node_modules')],plugins:[plugin],loader:{'.css':'empty'},define:{'process.env.NODE_ENV':'"production"'},logLevel:'silent'});
 }}finally{esbuild.stop();}
});
