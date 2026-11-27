import {authoredCopy,authoredErrorCopy} from '../src/authored-ui-copy.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';
import { nativeCollaborationBridge } from '../src/native-collaboration.ts';

const appSource=await readFile(new URL('../src/App.tsx',import.meta.url),'utf8');
const contextSource=await readFile(new URL('../src/NativeContextPanel.tsx',import.meta.url),'utf8');
function component(source,name){const parsed=ts.createSourceFile(name+'.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);assert.deepEqual(parsed.parseDiagnostics,[]);return{parsed,body:parsed.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text===name).body};}
const app=component(appSource,'App'),panel=component(contextSource,'NativeContextPanel');
function actualFunction(owner,name){const node=owner.body.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text===name);assert.ok(node,'actual production function '+name);return node.getText(owner.parsed);}
function actualHook(owner,name,contains){const statement=owner.body.statements.find(node=>ts.isExpressionStatement(node)&&ts.isCallExpression(node.expression)&&node.expression.expression.getText(owner.parsed)===name&&node.expression.arguments[0].getText(owner.parsed).includes(contains));assert.ok(statement,'actual production hook '+contains);return statement.expression.arguments[0].getText(owner.parsed);}
function execute(bindings,source){bindings={authoredCopy,authoredErrorCopy,...bindings};return new Function(...Object.keys(bindings),ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText)(...Object.values(bindings));}
const signal=value=>{const get=()=>value;get.set=next=>value=typeof next==='function'?next(value):next;return get;};
function frames(){const callbacks=new Map();let sequence=0;return{request:fn=>{callbacks.set(++sequence,fn);return sequence;},cancel:id=>callbacks.delete(id),flush(){for(const[id,fn]of[...callbacks]){callbacks.delete(id);fn();}},get size(){return callbacks.size;}};}
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return{promise,resolve,reject};};

test('actual composer switches popovers, cancels obsolete focus, restores keyboard focus and leaves the draft untouched',()=>{
 const menu=signal(undefined),keyboard=signal(false),clock=frames(),focus=[],draft={text:'保留我的草稿\r\n😀',refs:Array.from({length:20},(_,i)=>({id:'frozen-'+i,revision:3}))},before=structuredClone(draft);
 const controller=execute({composerMenu:menu,setComposerMenu:menu.set,setComposerMenuKeyboard:keyboard.set,composerMenuFrame:undefined,requestAnimationFrame:clock.request,cancelAnimationFrame:clock.cancel,modelMenuButton:{focus:()=>focus.push('model-trigger')},optionsMenuButton:{focus:()=>focus.push('options-trigger')},document:{querySelector:selector=>({querySelector:()=>({focus:()=>focus.push(selector)})})}},[actualFunction(app,'closeComposerMenu'),actualFunction(app,'toggleComposerMenu'),'return {closeComposerMenu,toggleComposerMenu};'].join('\n'));
 controller.toggleComposerMenu('model');assert.equal(menu(),'model');controller.toggleComposerMenu('options',true);assert.equal(menu(),'options');assert.equal(keyboard(),true);assert.equal(clock.size,1);clock.flush();assert.deepEqual(focus,['#as-composer-action-options']);controller.closeComposerMenu(true);assert.equal(menu(),undefined);assert.equal(focus.at(-1),'options-trigger');controller.toggleComposerMenu('model');controller.closeComposerMenu();clock.flush();assert.deepEqual(focus,['#as-composer-action-options','options-trigger']);assert.deepEqual(draft,before);
});

test('actual anchored popover returns keyboard focus to its trigger but respects the outside click target',async()=>{
 const source=await readFile(new URL('../src/StudioPopover.tsx',import.meta.url),'utf8'),parsed=ts.createSourceFile('StudioPopover.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX),handlers={};
 function visit(node){if(ts.isJsxAttribute(node)&&['onCloseAutoFocus','onPointerDownOutside','onFocusOutside','onOpenChange','onEscapeKeyDown'].includes(node.name.getText(parsed)))handlers[node.name.getText(parsed)]=node.initializer.expression.getText(parsed);ts.forEachChild(node,visit);}visit(parsed);
 const focus=[],opens=[],controller=execute({anchor:{querySelector:()=>({focus:options=>focus.push(options)})},props:{onOpenChange:value=>opens.push(value)}},`let outside=false;return {close:(${handlers.onCloseAutoFocus}),outside:(${handlers.onPointerDownOutside}),focusOutside:(${handlers.onFocusOutside}),change:(${handlers.onOpenChange}),escape:(${handlers.onEscapeKeyDown})};`);
 const key=new Event('close',{cancelable:true});controller.close(key);assert.equal(key.defaultPrevented,true);assert.deepEqual(focus,[{preventScroll:true}]);controller.outside();controller.close(new Event('close',{cancelable:true}));assert.equal(focus.length,1);controller.change(true);controller.close(new Event('close',{cancelable:true}));assert.equal(focus.length,2);controller.focusOutside();controller.close(new Event('close',{cancelable:true}));assert.equal(focus.length,2);assert.deepEqual(opens,[true]);const escape=new Event('keydown',{cancelable:true});let propagationStopped=false;escape.stopPropagation=()=>{propagationStopped=true;};controller.escape(escape);assert.equal(escape.defaultPrevented,true);assert.equal(propagationStopped,true);assert.deepEqual(opens,[true,false]);
});

test('actual orb setting uses the host receipt, prevents overlapping writes and exposes a recoverable human error',async()=>{
 const enabled=signal(true),busy=signal(false),error=signal(''),calls=[],first=deferred();
 const bridge={setAssistantOrbEnabled:value=>{calls.push(value);return calls.length===1?first.promise:Promise.reject(Error('IPC unavailable'));}};
 const change=execute({nativeBridge:bridge,orbSettingBusy:busy,setOrbSettingBusy:busy.set,setOrbEnabled:enabled.set,setOrbSettingError:error.set,studioDisposed:false},actualFunction(app,'changeOrbSetting')+'\nreturn changeOrbSetting;');
 const pending=change(false);assert.deepEqual(calls,[false]);assert.equal(enabled(),true,'display waits for the actual host receipt');assert.equal(busy(),true);await change(true);assert.equal(calls.length,1);first.resolve({enabled:false,visible:false});await pending;assert.equal(enabled(),false);assert.equal(busy(),false);await change(true);assert.equal(enabled(),false);assert.equal(busy(),false);assert.match(error(),/暂时无法更改悬浮球设置/);assert.doesNotMatch(error(),/IPC/);
});

test('actual orb preference initialization reads once and never writes or sends a model request',async()=>{
 const enabled=signal(true),errors=[],calls=[];execute({nativeBridge:{getAssistantOrbState:async()=>{calls.push('GET');return{enabled:false,visible:false};},setAssistantOrbEnabled:()=>{throw Error('mount must not write');}},studioDisposed:false,setOrbEnabled:enabled.set,setOrbSettingError:value=>errors.push(value)},`const mount=(${actualHook(app,'onMount','getAssistantOrbState')});mount();`);await new Promise(done=>setImmediate(done));assert.deepEqual(calls,['GET']);assert.equal(enabled(),false);assert.deepEqual(errors,[]);
});

test('native annotation keeps the original frame, delegates initial focus to the real popover and focuses an already open comment',()=>{
 const expanded=signal(false),basis=signal({id:'original-frame',revision:2}),marking=signal(false),focus=[],cleanup=[];let listener,unsubscribed=0;
 const bridge={version:2,getCollaborationState:async()=>({}),captureCurrentContext:async()=>({}),setCollaborationMode:async()=>({}),onNativeContext:()=>()=>{},onAnnotationRequested:fn=>{listener=fn;return()=>{unsubscribed++;listener=undefined;};}};
 const old=basis(),snapshot={id:'new-frame',revision:3},comment='保留原批注',commentInput={focus:options=>focus.push(options)};
 let initialFocus;
 function visit(node){if(ts.isJsxElement(node)&&node.openingElement.tagName.getText(panel.parsed)==='StudioPopover'){const attr=node.openingElement.attributes.properties.find(item=>item.name?.getText(panel.parsed)==='initialFocus');initialFocus=attr.initializer.expression.getText(panel.parsed);}ts.forEachChild(node,visit);}visit(panel.body);assert.ok(initialFocus);
 const controller=execute({nativeCollaborationBridge:()=>nativeCollaborationBridge({eeasDesktop:bridge}),globalThis:{},props:{snapshot},expanded,setExpanded:expanded.set,basis,setBasis:basis.set,image:()=> 'data:image/png;base64,frame',setMarking:marking.set,commentInput,onCleanup:fn=>cleanup.push(fn)},'let annotationIntent=false;'+actualFunction(panel,'revealAnnotation')+`\nconst mount=(${actualHook(panel,'onMount','onAnnotationRequested')});mount();return {focusTarget:(${initialFocus})};`);
 assert.equal(controller.focusTarget(),undefined);assert.equal(typeof listener,'function');listener();assert.equal(expanded(),true);assert.equal(marking(),true);assert.equal(basis(),old,'unfinished annotation retains its exact old frame');assert.deepEqual(focus,[],'first focus belongs to the primitive lifecycle');assert.equal(controller.focusTarget(),commentInput);assert.equal(comment,'保留原批注');listener();assert.deepEqual(focus,[{preventScroll:true}]);cleanup.forEach(fn=>fn());assert.equal(unsubscribed,1);assert.equal(listener,undefined);
});

test('late native preference completion cannot update a disposed settings view',async()=>{
 const outcome=deferred(),enabled=[],busy=[],errors=[];
 const controller=execute({nativeBridge:{setAssistantOrbEnabled:()=>outcome.promise},orbSettingBusy:()=>false,setOrbSettingBusy:value=>busy.push(value),setOrbEnabled:value=>enabled.push(value),setOrbSettingError:value=>errors.push(value),studioDisposed:false},actualFunction(app,'changeOrbSetting')+'\nreturn {changeOrbSetting,dispose(){studioDisposed=true;}};');
 const pending=controller.changeOrbSetting(false);controller.dispose();outcome.resolve({enabled:false,visible:false});await pending;assert.deepEqual(enabled,[]);assert.deepEqual(busy,[true]);assert.deepEqual(errors,['']);
});
