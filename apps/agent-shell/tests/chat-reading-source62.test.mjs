import {baseline as readBaseline,evidenceDirectory} from './historical-renderer-evidence.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,writeFileSync} from 'node:fs';
import {resolve,dirname} from 'node:path';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const app=resolve(dirname(fileURLToPath(import.meta.url)),'..'),root=resolve(app,'../..'),req=createRequire(resolve(app,'package.json')),ts=req('typescript'),babel=req('@babel/core'),esbuild=req('esbuild');
const preset=(await import(resolve(app,'node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js'))).default;
const pin=path=>{const raw=readFileSync(path);return{path,bytes:raw.length,sha256:createHash('sha256').update(raw).digest('hex')};};
function fragments(){const source=readFileSync(resolve(app,'src/App.tsx'),'utf8'),tree=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX),nodes=[];function walk(n){nodes.push(n);ts.forEachChild(n,walk);}walk(tree);
 const fun=name=>{const n=nodes.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text===name);assert.ok(n,name);return n.getText(tree);};
 const area=nodes.find(n=>ts.isJsxSelfClosingElement(n)&&n.tagName.getText(tree)==='textarea'&&n.getText(tree).includes('描述要完成的工作'));assert.ok(area);
 const toggle=nodes.find(n=>ts.isJsxElement(n)&&n.getText(tree).startsWith('<button ')&&n.getText(tree).includes('显示或收起工作台'));assert.ok(toggle);
 const back=nodes.find(n=>ts.isJsxElement(n)&&n.getText(tree).startsWith('<button ')&&n.getText(tree).includes("presentWorkspace('chat')")&&n.getText(tree).includes('返回对话'));assert.ok(back);
 const flush=nodes.find(n=>ts.isCallExpression(n)&&n.expression.getText(tree)==='onMount'&&n.getText(tree).includes('onDraftFlushRequested?.'));assert.ok(flush);
 return {functions:['layoutState','presentWorkspace','panelVisibility','saveComposerDraft'].map(fun).join('\n'),area:area.getText(tree),toggle:toggle.getText(tree),back:back.getText(tree),flush:flush.getText(tree)+';'};}
test('compile actual context/composer, current App presentation/IME/save/flush callbacks and fixed Icon for browser comparison',async()=>{
 const out=evidenceDirectory('chat-reading-source62'),baseline=readBaseline('chat','EEAS_CHAT_BASELINE');const f=fragments();
 try{for(const variant of ['before','after']){const used=new Map(),old=new Set(['NativeContextPanel.tsx','NativePageConversation.tsx']);
 const plugin={name:'source62-real-components',setup(build){build.onLoad({filter:/\.tsx$/},async({path})=>{const actual=variant==='before'&&dirname(path)===resolve(app,'src')&&old.has(path.split('/').at(-1))?resolve(baseline,'apps/agent-shell/src',path.split('/').at(-1)):path;used.set(actual,pin(actual));return{contents:babel.transformSync(ts.transpileModule(readFileSync(actual,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText,{filename:path+'.jsx',presets:[[preset,{generate:'dom',hydratable:false}]],configFile:false,babelrc:false}).code,loader:'js',resolveDir:dirname(path)};});}};
 const program=`import {render} from 'solid-js/web';import {batch,createEffect,createSignal,For,onCleanup,onMount,Show} from 'solid-js';
import NativeContextPanel from '${resolve(app,'src/NativeContextPanel.tsx')}';import NativePageConversation from '${resolve(app,'src/NativePageConversation.tsx')}';
import {Icon} from '${resolve(root,'staging-native-ui/upstream/packages/ui/src/components/icon.tsx')}';
import {preserveLayoutReading,workspaceView,WORKSPACE_VIEW_KEY} from '${resolve(app,'src/chat-workbench-layout.ts')}';import {mobileWorkspace,workspacePanels,type WorkspaceLayoutState} from '${resolve(app,'src/layout-model.ts')}';
import {startUILanguage,t as uiText,setUILanguagePreference} from '${resolve(app,'src/ui-language.ts')}';import {ComposerPresentation} from '${resolve(app,'src/composer-presentation.ts')}';
import {ComposerDraftPersistence,readComposerDraft} from '${resolve(app,'src/composer-draft.ts')}';import {readNativeText,saveNativeText,readNativeDraft,saveNativeDraft} from '${resolve(app,'src/native-collaboration.ts')}';
function Fixture(){onCleanup(startUILanguage());let shell,flushCallback;let submits=0,captures=0,feedbackCalls=0,flushes=0,failFlush=false,failFeedback=false;
 const [windowMode,setWindowMode]=createSignal('workbench'),[fullscreen]=createSignal(false),[fullscreenAgent]=createSignal(false),[fullscreenFiles]=createSignal(false),[singlePane,setSinglePane]=createSignal(innerWidth<=1100),[mobileView,setMobileView]=createSignal('agent'),[filesHidden]=createSignal(true),[agentHidden,setAgentHidden]=createSignal(false),[workbenchHidden,setWorkbenchHidden]=createSignal(true);
 const [prompt,setPrompt]=createSignal('QA62未发送草稿：保持当前项目与引用'),[composerView,setComposerView]=createSignal({focused:false,folded:false}),composerPresentation=new ComposerPresentation(setComposerView);
 const [status,setStatus]=createSignal('IDLE'),[busy,setBusy]=createSignal(false),[warning,setWarning]=createSignal(''),[snapshot,setSnapshot]=createSignal<any>();
 const [available,setAvailable]=createSignal(true),[unavailableReason,setUnavailableReason]=createSignal('');
 const [refs,setRefs]=createSignal([{id:'one',label:'跨目录中文文件名'.repeat(12)+'.md'},{id:'two',label:'UNBREAKABLE_REFERENCE_'.repeat(14)+'.docx'}]);
 const nativeBridge={onDraftFlushRequested(fn){flushCallback=fn;return()=>flushCallback=undefined;}},project=()=>({id:'qa62'}),activeSession=()=>undefined,draftReady=()=>true,draftAttachments=()=>refs().map((item,index)=>({feedback_ref:{id:item.id,revision:1,sha256:String(index+1).repeat(64)}})),nativeDraftAttachments=()=>refs().map((item,index)=>({context_ref:{id:item.id,revision:1,sha256:String(index+1).repeat(64)},label:item.label,comment:'已保存引用',regions:[]}));
 const draftPersistence=new ComposerDraftPersistence(localStorage),lease=draftPersistence.begin('qa62');draftPersistence.initialize(lease);
 const selectedDiscussion=()=>undefined,supervisorClient=undefined,conversationReading={flush(){}};
 const flushCurrentChatDraft=saveComposerDraft;
 const idleFlush=()=>{},flushConfigurationDrafts=idleFlush,flushWorkRequirementDrafts=idleFlush,flushWorkCaseDrafts=idleFlush,flushWorkRetestDrafts=idleFlush,flushWorkDeliveryDrafts=idleFlush,flushNativeExportJournals=idleFlush,flushWorkMaintenanceDrafts=idleFlush,flushExperienceDrafts=idleFlush,flushResourceAllowanceDrafts=idleFlush,flushWorkSourceDrafts=idleFlush,flushNativeConflictDrafts=idleFlush,flushNativePreviewDrafts=idleFlush,flushNativeFeedbackDrafts=idleFlush,flushOriginalControlDrafts=idleFlush;

 const quickInbox={start(){},async flush(){flushes++;if(failFlush)throw Error('Original controlled flush rejection');}},flushWorkbenchDrafts=async()=>{},flushDynamicWorkflowDrafts=async()=>{};
 function setError(value){throw Error(String(value));}function submitPrompt(){submits++;}function closeComposerMenu(){}function setNativeMenu(){}
 ${f.functions}
 ${f.flush}
 createEffect(()=>{prompt();refs();saveComposerDraft();});
 const resize=()=>setSinglePane(innerWidth<=1100);window.addEventListener('resize',resize);onCleanup(()=>window.removeEventListener('resize',resize));
 const root=<div class="as-app-frame" data-native-titlebar="true"><div class="qa-titlebar">Cirven</div><div ref={shell} class="as-shell" data-layout="workbench" data-chat-layout="adaptive" data-window-mode={windowMode()} data-fullscreen="false" data-compact={String(singlePane()&&innerWidth<=920)} data-mobile-view={mobileView()} data-files-hidden={String(!panelVisibility().files)} data-agent-hidden={String(!panelVisibility().agent)} data-workbench-hidden={String(!panelVisibility().workbench)} style={{'--as-file-width':'236px','--as-agent-width':'800px'}}>
 <aside class="as-sidebar"/><div class="as-pane-separator"/><section class="as-workbench"><header class="as-topbar">${f.back}</header><main class="as-workspace-body">低频工作台的原对象</main></section><div class="as-pane-separator"/>
 <aside class="as-agent"><nav class="as-activity-rail as-agent-activity"><button aria-label="Agent 对话"><Icon name="prompt"/></button></nav><div class="as-agent-content"><header class="as-agent-header"><strong>当前项目 · 原讨论</strong><span/>${f.toggle}</header>
 <NativeContextPanel snapshot={snapshot()} available={available()} reason={unavailableReason()} pending={busy()} onCapture={()=>captures++} onFeedback={async()=>{feedbackCalls++;if(failFeedback)throw Error("Original controlled feedback rejection");}}/>
 <main class="as-conversation"><Show when={status()!=='IDLE'}><p class="as-inline-warning" role="status">{status()==='UNKNOWN'?'原操作结果尚未确认':status()==='FAILED'?'原操作已明确失败':'原任务正在运行'}</p></Show><For each={Array.from({length:24},(_,index)=>index)}>{index=><article class="as-history-message" data-qa-message={index}><small>{index%2?'Cirven':'你'}</small><p>第{index+1}段原创讨论。当前资料、确认依据与待处理工作保持在同一讨论中。{('这段文字用于检查真实换行与阅读位置。').repeat(3)}</p></article>}</For></main>
 <form class="as-composer" onSubmit={e=>{e.preventDefault();submitPrompt();}}><NativePageConversation scopeKey="qa62" busy={busy()} mode={<div class="as-native-review-composer"><button type="button">当前页检查</button><Show when={status()==='RUNNING'||status()==='UNKNOWN'}><span>{status()==='UNKNOWN'?'结果尚未确认，请核查原操作':'补充当前任务'}</span><button type="button">新问题</button></Show></div>} attachments={refs().map((item,index)=>({...item,description:'引用内容保留原依据。'.repeat(24),warning:index===0?warning():'',onRemove:()=>setRefs(items=>items.filter(old=>old.id!==item.id))}))} input={${f.area}} toolbar={<div class="as-composer-toolbar"><button type="button">需求梳理</button><span style={{flex:1}}/><button type="button" class="as-composer-model-trigger">选择模型</button><button type="button" class="as-composer-icon" aria-label="消息选项"><Icon name="settings-gear"/></button><button type="submit" class="as-composer-send" data-component="button" aria-label="发送消息"><Icon name="arrow-up"/></button></div>}/></form>
 </div></aside></div></div>;
 window.fixture={state:()=>({text:prompt(),refs:refs(),draft:readComposerDraft(localStorage,'qa62'),mode:windowMode(),mobile:mobileView(),panels:panelVisibility(),submits,captures,feedbackCalls,flushes,originalOperation:'qa62-original-operation-unchanged'}),setStatus:setStatus,setBusy,setWarning,setSnapshot,setAvailable,setUnavailableReason,rejectFeedback:value=>failFeedback=value,mode:setWindowMode,locale:setUILanguagePreference,flush:()=>flushCallback(),rejectFlush:value=>failFlush=value};return root;}
render(()=> <Fixture/>,document.getElementById('root'));`;
 const entry=resolve(out,variant+'.tsx');writeFileSync(entry,program);const built=await esbuild.build({entryPoints:[entry],outfile:resolve(out,variant+'.js'),bundle:true,platform:'browser',format:'iife',conditions:['browser'],nodePaths:[resolve(app,'node_modules')],plugins:[plugin],alias:{'@opencode-ai/ui/icon':resolve(root,'staging-native-ui/upstream/packages/ui/src/components/icon.tsx')},loader:{'.css':'empty'},define:{'process.env.NODE_ENV':'"production"'},logLevel:'silent',metafile:true});
 for(const name of Object.keys(built.metafile.inputs)){const path=resolve(process.cwd(),name);if(!used.has(path))used.set(path,pin(path));}
 writeFileSync(resolve(out,variant+'-inputs.json'),JSON.stringify({variant,App:pin(resolve(app,'src/App.tsx')),entry:pin(entry),bundle:pin(resolve(out,variant+'.js')),inputs:[...used.values()]},null,2));
 }}finally{esbuild.stop();}
});
