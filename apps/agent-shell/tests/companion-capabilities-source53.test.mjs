import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';
import * as solid from '../node_modules/solid-js/dist/solid.js';

function parse(path,name){const tree=ts.createSourceFile(path,readFileSync(new URL(path,import.meta.url),'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);return {tree,component:tree.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text===name)};}
const app=parse('../src/App.tsx','App'),popover=parse('../src/StudioPopover.tsx','StudioPopover'),panel=parse('../src/NativeCapabilitiesPanel.tsx','NativeCapabilitiesPanel');
const openManager=app.component.body.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='openCapabilityManager').getText(app.tree);
let entry,callerClose;
function visitApp(node){if(ts.isJsxElement(node)&&node.openingElement.tagName.getText(app.tree)==='button'&&node.openingElement.getText(app.tree).includes('openCapabilityManager()'))entry=node.getText(app.tree);if(ts.isJsxElement(node)&&node.openingElement.tagName.getText(app.tree)==='StudioPopover'&&node.openingElement.getText(app.tree).includes('协作与设置'))callerClose=node.openingElement.attributes.properties.find(prop=>prop.name?.getText(app.tree)==='onCloseAutoFocus').initializer.expression.getText(app.tree);ts.forEachChild(node,visitApp);}visitApp(app.component);assert.ok(entry&&callerClose);
function jsxHandler(owner,tag,name){let handler;function visit(node){if(ts.isJsxElement(node)&&node.openingElement.tagName.getText(owner.tree)===tag)handler=node.openingElement.attributes.properties.find(prop=>prop.name?.getText(owner.tree)===name)?.initializer.expression.getText(owner.tree);ts.forEachChild(node,visit);}visit(owner.component);assert.ok(handler,tag+name);return handler;}
const primitiveClose=jsxHandler(popover,'Popover.Content','onCloseAutoFocus'),dialogClose=jsxHandler(panel,'Dialog.Content','onCloseAutoFocus');
const captureOpener=panel.component.body.statements.find(node=>ts.isExpressionStatement(node)&&ts.isCallExpression(node.expression)&&node.expression.expression.getText(panel.tree)==='createEffect'&&node.expression.arguments[0].getText(panel.tree).includes('opener=')).expression.arguments[0].getText(panel.tree);
function execute(bindings,source){return new Function(...Object.keys(bindings),ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText)(...Object.values(bindings));}
const universal=readFileSync(new URL('../node_modules/solid-js/universal/dist/universal.js',import.meta.url),'utf8').replace("from 'solid-js'",`from '${new URL('../node_modules/solid-js/dist/solid.js',import.meta.url).href}'`);
const {createRenderer}=await import('data:text/javascript;base64,'+Buffer.from(universal).toString('base64'));
function harness(mode='companion'){
 const state={open:false,menu:true,drawer:false,tab:'mcp',draft:{text:'未发送的消息 😀\r\n',refs:[{id:'frozen-frame',revision:4}]},calls:[],focus:[]};
 const document={activeElement:undefined};class Element{constructor(type){this.type=type;this.isConnected=true;this.children=[];this.props={};this.parent=null;}focus(options){document.activeElement=this;state.focus.push([this.type,options]);}}
 const trigger=new Element('stable-menu-trigger'),modal=new Element('dialog-control'),outside=new Element('other-software-control'),shell={querySelector:selector=>selector==='.as-agent-menu-trigger'?trigger:undefined};
 const control=execute({document,HTMLElement:Element,props:{get open(){return state.open;},initialTab:undefined,review:undefined},untrack:solid.untrack,tab:()=> 'tools',queries:{},query:()=>'',setTab(){},setQuery(){},setDetail(){},run:async()=>state.calls.push('read-directory'),load(){},retainDraft:()=>state.calls.push('retain-dialog-draft')},`let opener,skipReturnFocus=false,wasOpen=false,editEpoch=0;return {observe:(${captureOpener}),close:(${dialogClose})};`);
 const bindings={shell,windowMode:()=>mode,uiText:key=>key,setCapabilitiesTab:value=>state.tab=value,setCapabilitiesOpen:value=>{state.open=value;control.observe();},setNativeMenu:value=>state.menu=value,setSkillDrawer:value=>state.drawer=value};
 const host={createElement:type=>new Element(type),createTextNode:value=>({type:'#text',value,children:[],parent:null}),isTextNode:node=>node.type==='#text',replaceText:(node,value)=>node.value=value,insertNode(parent,node,anchor){node.parent=parent;parent.children.splice(anchor?parent.children.indexOf(anchor):parent.children.length,0,node);},removeNode(parent,node){parent.children.splice(parent.children.indexOf(node),1);node.parent=null;node.isConnected=false;},setProperty:(node,key,value)=>node.props[key]=value,getParentNode:node=>node.parent,getFirstChild:node=>node.children[0],getNextSibling:node=>node.parent?.children[node.parent.children.indexOf(node)+1]};
 const renderer=Object.assign(createRenderer(host),{Show:solid.Show}),jsx=openManager+`\nfunction Entry(){return ${entry};}`;
 const js=ts.transpileModule(jsx,{compilerOptions:{jsx:ts.JsxEmit.Preserve,target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
 const compiled=babel.transformSync(js,{configFile:false,babelrc:false,presets:[[solidPreset,{generate:'universal',moduleName:'actual-renderer'}]]}).code.replace(/import\s*\{([^}]+)\}\s*from\s*["']actual-renderer["'];?/g,(_match,list)=>'const {'+list.replace(/\s+as\s+/g,':')+'}=bindings.renderer;');
 const Icon=()=>renderer.createElement('i'),Entry=new Function('bindings',`const {${Object.keys(bindings).join(',')},renderer,Icon}=bindings;${compiled};return Entry;`)({...bindings,renderer,Icon}),mount=new Element('root'),dispose=renderer.render(()=>Entry(),mount);
 const all=()=>{const nodes=[];function walk(node){nodes.push(node);for(const child of node.children??[])walk(child);}walk(mount);return nodes;},button=()=>all().find(node=>node.type==='button');
 const appClose=execute({capabilitiesOpen:()=>state.open,skillDrawer:()=>state.drawer,focusRestoredAgentTool:()=>false,shell},'return ('+callerClose+');');
 const primitive=execute({props:{onCloseAutoFocus:appClose},anchor:{querySelector:()=>trigger}},`let outside=false;return {close:(${primitiveClose}),setOutside:()=>outside=true};`);
 return {state,button,trigger,modal,outside,document,dispose,primitive,closeDialog(){state.open=false;control.observe();control.close(new Event('close',{cancelable:true}));}};
}

test('shared actual global-menu action opens the same capability dialog in full window and companion without changing a draft',()=>{
 for(const mode of ['companion','workbench']){const h=harness(mode);try{const before=structuredClone(h.state.draft);const button=h.button();assert.ok(button);assert.equal(button.props['aria-haspopup'],'dialog');button.focus();button.props.onClick();assert.equal(h.state.open,true);assert.equal(h.state.menu,false);assert.equal(h.state.drawer,false);assert.equal(h.state.tab,undefined);assert.deepEqual(h.state.calls,['read-directory']);assert.deepEqual(h.state.draft,before);assert.equal(h.document.activeElement,h.trigger);}finally{h.dispose();}}
});
test('modal owns focus after the actual popover-close callback; closing the existing Dialog returns to the connected stable trigger',()=>{
 const h=harness();try{const before=structuredClone(h.state.draft);h.button().focus();h.button().props.onClick();h.button().isConnected=false;h.modal.focus();const count=h.state.focus.length,event=new Event('close',{cancelable:true});h.primitive.close(event);assert.equal(event.defaultPrevented,true);assert.equal(h.state.focus.length,count);assert.equal(h.document.activeElement,h.modal);h.closeDialog();assert.equal(h.document.activeElement,h.trigger);assert.deepEqual(h.state.focus.at(-1),['stable-menu-trigger',{preventScroll:true}]);assert.deepEqual(h.state.draft,before);}finally{h.dispose();}
});
test('ordinary menu dismissal retains previous trigger focus and outside-click behavior when no modal is opening',()=>{
 const h=harness();try{h.primitive.close(new Event('close',{cancelable:true}));assert.equal(h.document.activeElement,h.trigger);h.outside.focus();const count=h.state.focus.length;h.primitive.setOutside();h.primitive.close(new Event('close',{cancelable:true}));assert.equal(h.state.focus.length,count);assert.equal(h.document.activeElement,h.outside);assert.equal(h.state.open,false);}finally{h.dispose();}
});
test('pointer-style and keyboard-style activation preserve the same connected return target and cannot create a second modal or reload its directory',()=>{
 for(const focusMenu of [false,true]){const h=harness();try{if(focusMenu)h.button().focus();h.button().props.onClick();h.button().props.onClick();assert.equal(h.state.open,true);assert.deepEqual(h.state.calls,['read-directory']);h.closeDialog();assert.equal(h.document.activeElement,h.trigger);}finally{h.dispose();}}
});
