import {authoredCopy,authoredErrorCopy} from '../src/authored-ui-copy.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
import {batch} from 'solid-js';
import {NativeCollaborationController,confirmedCollaborationFullscreen} from '../src/native-collaboration.ts';
import {visiblePanels} from '../src/presentation.ts';
const source=await readFile(new URL('../src/App.tsx',import.meta.url),'utf8'),parsed=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX),body=parsed.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='App').body;
const layoutModule={exports:{}};
new Function('require','module','exports',ts.transpileModule(await readFile(new URL('../src/layout-model.ts',import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText)(name=>{assert.equal(name,'./presentation');return {visiblePanels};},layoutModule,layoutModule.exports);
const {workspacePanels,mobileWorkspace}=layoutModule.exports;
function actual(name){const node=body.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text===name);assert.ok(node,name);return node.getText(parsed);}
function execute(bindings,text){return new Function(...Object.keys(bindings),ts.transpileModule(text,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText)(...Object.values(bindings));}
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return{promise,resolve,reject};};
function scene({mode='workbench',fullscreen=false,modeResult,fullResult}={}){
 const events=[],errors=[];let current=mode,full=fullscreen,hidden=true,mobile='workbench';
 const bridge={setCollaborationMode:async requested=>{events.push(['mode',requested]);return modeResult?modeResult(requested):{mode:requested,fullscreen:false};},setCollaborationFullscreen:async enabled=>{events.push(['fullscreen',enabled]);return fullResult?fullResult(enabled):{mode:current,fullscreen:enabled};}};
 const controller=new NativeCollaborationController(bridge,value=>{current=value.mode;},()=>{});
 const commands=execute({authoredCopy,authoredErrorCopy,nativeBridge:bridge,nativeController:controller,windowMode:()=>current,fullscreen:()=>full,confirmedCollaborationFullscreen,syncFullscreenPanels:value=>{full=value;events.push(['full-view',value]);},setWindowMode:value=>current=value,setNativeMenu:value=>events.push(['menu',value]),setWorkbenchHidden:value=>{hidden=value;events.push(['workbench-hidden',value]);},setMobileView:value=>{mobile=value;events.push(['mobile',value]);},setAgentHidden:()=>assert.fail('mode changes must not overwrite workspace Agent preference'),setError:value=>errors.push(value),message:error=>error.message,batch},'let fullscreenRequest=0,fullscreenPending,modeRequest=0;'+actual('leaveFullscreen')+'\n'+actual('changeNativeMode')+'\nreturn {changeNativeMode,leaveFullscreen};');
 return {...commands,events,errors,get mode(){return current;},get fullscreen(){return full;},get hidden(){return hidden;},get mobile(){return mobile;}};
}

test('actual mode transition stops after refused fullscreen exit and leaves mode/layout untouched',async()=>{
 const s=scene({fullscreen:true,fullResult:async()=>{throw Error('host refused fullscreen exit');}});assert.equal(await s.changeNativeMode('companion'),false);assert.deepEqual(s.events,[['fullscreen',false]]);assert.equal(s.mode,'workbench');assert.equal(s.fullscreen,true);assert.equal(s.hidden,true);assert.match(s.errors[0],/refused/);
});

test('actual transition waits for fullscreen and host receipts without mutating saved Agent preference',async()=>{
 const gate=deferred(),s=scene({fullscreen:true,modeResult:()=>gate.promise});const pending=s.changeNativeMode('companion');await new Promise(done=>setImmediate(done));assert.deepEqual(s.events,[['fullscreen',false],['full-view',false],['mode','companion']]);assert.equal(s.mobile,'workbench');assert.equal(s.hidden,true);gate.resolve({mode:'companion',fullscreen:false});assert.equal(await pending,true);assert.equal(s.mode,'companion');assert.equal(s.mobile,'agent');assert.equal(s.hidden,true);
 const reopening=scene({mode:'companion'});assert.equal(await reopening.changeNativeMode('workbench'),true);assert.equal(reopening.hidden,false,'explicit opening restores the workbench only');assert.equal(reopening.mobile,'workbench');
});

test('wrong mode receipt and superseded requests never report a successful UI transition',async()=>{
 const wrong=scene({mode:'companion',modeResult:async()=>({mode:'companion'})});assert.equal(await wrong.changeNativeMode('workbench'),false);assert.equal(wrong.hidden,true);assert.equal(wrong.events.some(([name])=>name==='mobile'),false);assert.match(wrong.errors[0],/未确认/);
 const a=deferred(),b=deferred();let i=0;const racing=scene({modeResult:()=>++i===1?a.promise:b.promise});const first=racing.changeNativeMode('companion'),latest=racing.changeNativeMode('workbench');b.resolve({mode:'workbench'});assert.equal(await latest,true);a.resolve({mode:'companion'});assert.equal(await first,false);assert.equal(racing.mode,'workbench');assert.equal(racing.mobile,'workbench');assert.equal(racing.events.filter(([name])=>name==='mobile').length,1);
});

test('actual Web collapsed presentation keeps one usable Agent without changing hidden preferences',()=>{
 const prefs={filesHidden:true,agentHidden:true,workbenchHidden:true,fullscreen:false,fullscreenAgent:false,fullscreenFiles:false},before=structuredClone(prefs);const project=execute({authoredCopy,authoredErrorCopy,nativeBridge:undefined,workspacePanels,mobileWorkspace,layoutState:()=>({...prefs,singlePane:false,mobileView:'agent',companion:false}),...Object.fromEntries(Object.entries(prefs).map(([name,value])=>[name,()=>value]))},actual('panelVisibility')+'\nreturn panelVisibility;');assert.deepEqual(project(),{files:false,agent:true,workbench:false});assert.deepEqual(prefs,before);
});

test('actual whole-workbench collapse preserves preferences and stops on failed fullscreen or mode confirmation',async()=>{
 for(const native of [false,true]){const changes=[];const collapse=execute({nativeController:native?{}:undefined,changeNativeMode:async()=>false,fullscreen:()=>true,leaveFullscreen:async()=>false,setWorkbenchHidden:value=>changes.push(value),setMobileView:value=>changes.push(value),setAgentHidden:()=>assert.fail('never reset layout preferences'),workspaceStateOwner:{flush:()=>changes.push('flush')}},actual('collapseWorkbench')+'\nreturn collapseWorkbench;');await collapse();assert.deepEqual(changes,[]);}
});

test('actual project-review navigation waits for mode confirmation and cannot clear an object after rejection or later navigation',async()=>{
 for(const accepted of [false,true]){const gate=deferred(),changes=[];let current=true;const open=execute({navigation:{capture:()=>()=>current,begin:()=>changes.push('owner')},windowMode:()=> 'companion',changeNativeMode:()=>gate.promise,setInspector:value=>changes.push(['inspector',value]),interactions:{current:()=>[]},setSkillDrawer:value=>changes.push(['tools',value]),setWorkbenchHidden:value=>changes.push(['workbench',value]),setMobileView:value=>changes.push(['mobile',value]),workspaceStateOwner:undefined},actual('showProjectReview')+'\nreturn showProjectReview;');const pending=open();assert.deepEqual(changes,[]);if(accepted)current=false;gate.resolve(accepted);await pending;assert.deepEqual(changes,[]);}
});

test('actual titlebar pin is independent of task busy state, single flight and reports refusal without changing the mode',async()=>{
 const gate=deferred(),events=[],errors=[];let pending=false,pinned=false;
 const toggle=execute({companionPinBusy:()=>pending,setCompanionPinBusy:value=>pending=value,windowMode:()=> 'companion',
  collaborationState:()=>({pinned_preference:pinned,pinned}),nativeController:{pin:async value=>{events.push(value);await gate.promise;pinned=value;}},
  busy:()=> '执行中的原任务',setError:value=>errors.push(value),authoredErrorCopy},actual('toggleCompanionPin')+'\nreturn toggleCompanionPin;');
 const first=toggle();await toggle();assert.equal(pending,true);assert.deepEqual(events,[true]);gate.resolve();await first;
 assert.equal(pinned,true);assert.equal(pending,false);assert.deepEqual(errors,[]);
 const fail=execute({companionPinBusy:()=>false,setCompanionPinBusy:value=>events.push(['busy',value]),windowMode:()=> 'companion',
  collaborationState:()=>({pinned:false}),nativeController:{pin:async()=>{throw Error('原宿主拒绝，草稿不变');}},setError:value=>errors.push(value),authoredErrorCopy},actual('toggleCompanionPin')+'\nreturn toggleCompanionPin;');
 await fail();assert.match(String(errors[0]),/原宿主拒绝/);assert.deepEqual(events.at(-1),['busy',false]);
});
