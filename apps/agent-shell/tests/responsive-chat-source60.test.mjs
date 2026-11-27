import {baseline as readBaseline,evidenceDirectory} from './historical-renderer-evidence.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,writeFileSync,existsSync} from 'node:fs';
import {resolve,dirname,extname} from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';

const app=resolve(dirname(fileURLToPath(import.meta.url)),'..'),req=createRequire(resolve(app,'package.json'));
const ts=req('typescript'),babel=req('@babel/core'),esbuild=req('esbuild');
const preset=(await import(resolve(app,'node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js'))).default;
function load(file){
 file=resolve(file);if(!extname(file))file+='.ts';const module={exports:{}};
 const code=ts.transpileModule(readFileSync(file,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText;
 new Function('require','module','exports',code)(name=>load(resolve(dirname(file),name)),module,module.exports);return module.exports;
}
const {workspacePanels,mobileWorkspace}=load(resolve(app,'src/layout-model.ts'));
const base={filesHidden:false,agentHidden:false,workbenchHidden:false,fullscreen:false,fullscreenAgent:false,fullscreenFiles:false,singlePane:false,mobileView:'agent',companion:false};
test('one mobile view controls actual panels independently of saved wide preference',()=>{
 for(const [agentHidden,workbenchHidden] of [[false,false],[false,true],[true,false]])for(const mobileView of ['agent','workbench']){
  const original={...base,agentHidden,workbenchHidden,mobileView,singlePane:true},before=structuredClone(original);
  assert.deepEqual(workspacePanels(original),{files:true,agent:mobileView==='agent',workbench:mobileView==='workbench'});
  assert.deepEqual(original,before,'projection cannot write back a preferred layout');
 }
});
test('desktop, companion and fullscreen preserve their established pane semantics',()=>{
 assert.deepEqual(workspacePanels({...base,agentHidden:true}),{files:true,agent:false,workbench:true});
 assert.deepEqual(workspacePanels({...base,workbenchHidden:true}),{files:true,agent:true,workbench:false});
 assert.equal(mobileWorkspace({...base,singlePane:true,companion:true}),false);
 assert.deepEqual(workspacePanels({...base,singlePane:true,companion:true,workbenchHidden:true}),{files:true,agent:true,workbench:false});
 assert.deepEqual(workspacePanels({...base,singlePane:true,fullscreen:true,fullscreenAgent:true}),{files:false,agent:true,workbench:true});
});

function fragments(source,variant){
 const tree=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX),nodes=[];
 function walk(node){nodes.push(node);ts.forEachChild(node,walk);}walk(tree);
 const fun=name=>{const node=nodes.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text===name);assert.ok(node,name);return node.getText(tree);};
 const variable=name=>{const node=nodes.find(n=>ts.isVariableDeclaration(n)&&ts.isIdentifier(n.name)&&n.name.text===name&&(name!=='changed'||n.initializer?.getText(tree).includes('setCompact(media.matches)')));assert.ok(node,name);return 'const '+node.getText(tree)+';';};
 const button=(predicate,label)=>{
  const node=nodes.find(n=>ts.isJsxElement(n)&&n.openingElement.tagName.getText(tree)==='button'&&predicate(n.getText(tree)));
  assert.ok(node,label);return node;
 };
 const visibilityOwner=(node,label)=>{
  const parent=node.parent;
  assert.ok(ts.isJsxElement(parent)&&parent.openingElement.tagName.getText(tree)==='Show',label);
  return parent.getText(tree);
 };
 const toggle=button(s=>s.includes('class="as-workbench-switch"')&&s.includes('显示或收起工作台'),'workbench toggle').getText(tree);
 const frozen=variant==='before';
 // The current header owns both the mobile return and the hidden-wide-chat restore.
 const back=frozen?button(s=>s.includes('aria-label={uiText("返回对话")}'),'historical return').getText(tree)
   :visibilityOwner(button(s=>s.includes('showAgentChat()')&&s.includes("presentWorkspace('chat')")&&s.includes('返回对话'),'current chat return'),'return visibility');
 const menu=frozen?button(s=>s.includes('显示或隐藏 Agent'),'historical pane menu').getText(tree):'';
 const hidden=visibilityOwner(button(s=>s.includes('class="as-hide-agent"'),'hide agent'),'hide visibility');
 const textarea=nodes.find(n=>ts.isJsxSelfClosingElement(n)&&n.getText(tree).startsWith('<textarea ')&&n.getText(tree).includes('描述要完成的工作'));
 assert.ok(textarea,'actual App textarea');
 const restore=nodes.find(n=>ts.isJsxElement(n)&&n.getText(tree).startsWith('<Show when={!fullscreen()')&&n.getText(tree).includes('class="as-panel-restore"'));
 if(frozen)assert.ok(restore,'historical restore controls');
 const effect=nodes.find(n=>ts.isCallExpression(n)&&n.expression.getText(tree)==='createEffect'&&n.getText(tree).includes('localStorage.setItem(WORKSPACE_VIEW_KEY,workbenchHidden()'));
 assert.ok(effect,'actual preferred-layout persistence effect');
 return {functions:['presentWorkspace','panelVisibility','revealAgent',...source.includes('function layoutState(')?['layoutState','toggleAgent']:[],...frozen?[]:['showAgentChat']].map(fun).join('\n'),resized:variable('resized'),changed:variable('changed'),toggle,back,menu,hidden,textarea:textarea.getText(tree),restore:frozen?restore.getText(tree):'',effect:effect.getText(tree)+';'};
}
test('compile real App callbacks and controls with the real native composer for isolated browser events',async()=>{
 const out=evidenceDirectory('responsive-chat-source60'),baseline=readBaseline('layout','EEAS_LAYOUT_BASELINE');
 const plugin={name:'source60-real-solid',setup(build){build.onLoad({filter:/\.tsx$/},async({path})=>{
  const code=ts.transpileModule(readFileSync(path,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;
  return {contents:babel.transformSync(code,{filename:path+'.jsx',presets:[[preset,{generate:'dom',hydratable:false}]],configFile:false,babelrc:false}).code,loader:'js',resolveDir:dirname(path)};
 });}};
 try{for(const variant of ['before','after']){
  const file=variant==='before'?resolve(baseline,'apps/agent-shell/src/App.tsx'):resolve(app,'src/App.tsx'),f=fragments(readFileSync(file,'utf8'),variant);
  const standaloneContext=variant==='before'?'<section class="as-native-context"><div class="as-native-context-line"><strong>先切换到要查看的页面</strong></div></section>':'';
  const composerContext=variant==='after'?'context={<div class="as-native-file-context"><button type="button" class="as-native-file-chip">当前页协作</button></div>}':'';
  const program=`import {render} from 'solid-js/web';import {batch,createEffect,createSignal,onCleanup,Show} from 'solid-js';
import NativePageConversation from '${resolve(app,'src/NativePageConversation.tsx')}';
import {visiblePanels} from '${resolve(app,'src/presentation.ts')}';
import {mobileWorkspace,workspacePanels,type WorkspaceLayoutState} from '${resolve(app,'src/layout-model.ts')}';
import {preserveLayoutReading,workspaceView,WORKSPACE_VIEW_KEY} from '${resolve(app,'src/chat-workbench-layout.ts')}';
import {t as uiText,uiLocale,setUILanguagePreference,startUILanguage} from '${resolve(app,'src/ui-language.ts')}';
function Icon(){return null;}function Fixture(){
 onCleanup(startUILanguage());
 const initialView=workspaceView(localStorage.getItem(WORKSPACE_VIEW_KEY));
 const [filesHidden,setFilesHidden]=createSignal(true),[agentHidden,setAgentHidden]=createSignal(initialView==='workbench'),[workbenchHidden,setWorkbenchHidden]=createSignal(initialView==='chat');
 const [mobileView,setMobileView]=createSignal(initialView==='workbench'?'workbench':'agent'),[singlePane,setSinglePane]=createSignal(window.innerWidth<=1100),[compact,setCompact]=createSignal(window.matchMedia('(max-width:920px)').matches);
 const [fullscreen,setFullscreen]=createSignal(false),[fullscreenAgent,setFullscreenAgent]=createSignal(false),[fullscreenFiles,setFullscreenFiles]=createSignal(false);
 const [sidebarOpen,setSidebarOpen]=createSignal(false),[prompt,setPrompt]=createSignal('QA60未发送草稿：保留项目与引用'),[focus,setFocus]=createSignal(false);
 const windowMode=()=>'workbench',nativeBridge={},composerView=()=>({focused:focus(),folded:false}),composerPresentation={focus:setFocus};let shell:HTMLDivElement|undefined,submits=0;
 function submitPrompt(){submits++;}function resizePanes(){}function setViewMenu(){}
 const agentTools={current:()=>[],collapse:()=>{}},setSkillDrawer=()=>{};
 ${f.functions}\n${f.effect}
 const media=window.matchMedia('(max-width:920px)');${f.changed}${f.resized}media.addEventListener('change',changed);window.addEventListener('resize',resized);
 const root=<div class="as-app-frame" data-native-titlebar="true"><div class="qa-titlebar">Cirven</div><div ref={shell} class="as-shell" data-layout="workbench" data-chat-layout="adaptive" data-window-mode="workbench" data-fullscreen="false" data-compact={String(compact())} data-mobile-view={mobileView()} data-files-hidden={String(!panelVisibility().files)} data-agent-hidden={String(!panelVisibility().agent)} data-workbench-hidden={String(!panelVisibility().workbench)} style={{'--as-file-width':'40px','--as-agent-width':'600px'}}>
 <aside class="as-sidebar"/><div class="as-pane-separator" data-kind="files"/><section class="as-workbench"><header class="as-topbar">${f.back}${f.menu}</header><div class="as-workspace-body">当前B工作台 · 原对象与原草稿</div></section><div class="as-pane-separator" data-kind="agent"/>
 <aside class="as-agent"><div class="as-agent-content"><header class="as-agent-header"><strong>B 当前工作</strong><span aria-hidden="true"/>${f.toggle}${f.hidden}</header>
 ${standaloneContext}
 <main class="as-conversation"><article class="as-history-message"><p>第一段已保存的讨论，回看时不应被输入框挤成细缝。</p></article><article class="as-history-message"><p>第二段保留原工作上下文，工作台按需打开。</p></article><article class="as-history-message"><p>最后一段真实原创夹具内容，滚动锚点保留。</p></article></main>
 <form class="as-composer" onSubmit={event=>{event.preventDefault();submitPrompt();}}><NativePageConversation scopeKey="qa60:B" ${composerContext} mode={<div class="as-native-review-composer"><button type="button">当前页检查</button></div>} attachments={[{id:'one',label:'中文长文件名'.repeat(18)+'.md',description:'原引用内容'.repeat(30)},{id:'two',label:'UNBREAKABLE_REFERENCE_'.repeat(20)+'.docx'}]} input={${f.textarea}} toolbar={<div class="as-composer-toolbar"><button type="button">需求梳理</button><button type="button" class="as-composer-model-trigger"><span>选择模型</span></button><button type="button" class="as-composer-icon" aria-label="消息选项">···</button><button class="as-composer-send" data-component="button" aria-label="发送消息">↑</button></div>}/></form>
 </div></aside>${f.restore}</div></div>;
 (window as any).fixture={preferred:()=>localStorage.getItem(WORKSPACE_VIEW_KEY),state:()=>({mobile:mobileView(),single:singlePane(),compact:compact(),panels:panelVisibility(),submits}),draft:()=>prompt(),locale:setUILanguagePreference};return root;}
render(()=> <Fixture/>,document.getElementById('root')!);`;
  const entry=resolve(out,variant+'.tsx');writeFileSync(entry,program);
  await esbuild.build({entryPoints:[entry],outfile:resolve(out,variant+'.js'),bundle:true,platform:'browser',format:'iife',conditions:['browser'],nodePaths:[resolve(app,'node_modules')],plugins:[plugin],loader:{'.css':'empty'},define:{'process.env.NODE_ENV':'"production"'},logLevel:'silent'});
  assert.ok(existsSync(resolve(out,variant+'.js')));
 }}finally{esbuild.stop();}
});
