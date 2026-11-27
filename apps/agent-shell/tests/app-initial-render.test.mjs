import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';
import * as solid from 'solid-js';
import * as web from 'solid-js/web';
import * as uiLanguage from '../src/ui-language.ts';
const app=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const source=await readFile(resolve(app,'src/App.tsx'),'utf8');
const productBrand=JSON.parse(await readFile(resolve(app,'../../packages/branding/product.json'),'utf8'));
const brandIcon='data:image/svg+xml;base64,'+Buffer.from(await readFile(resolve(app,'../../packages/branding/icon.svg'),'utf8')).toString('base64');
const fixtureLanguage={...uiLanguage,t:(message,params)=>uiLanguage.translateUI(message,'zh-CN',params),uiLocale:()=> 'zh-CN'};
const brandModules={'./brand':{default:productBrand},'../../../packages/branding/icon.svg':{default:brandIcon},'./ui-language':fixtureLanguage,'./assets/cirven-signature.svg':{default:'data:image/svg+xml;base64,'+Buffer.from(await readFile(resolve(app,'src/assets/cirven-signature.svg'),'utf8')).toString('base64')}};
// Compile the complete production component, including its own signals, JSX
// getters, conditionals and the real project-review component. Native/large
// preview children are rendering boundaries, not invented business records.
function compileComponent(source,name,modules,extraExports=[]){
 const js=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;
 const output=babel.transformSync(js,{filename:name+'.jsx',presets:[[solidPreset,{generate:'ssr',hydratable:false}]],configFile:false,babelrc:false}).code;
 const parsed=ts.createSourceFile(name+'.mjs',output,ts.ScriptTarget.Latest,true,ts.ScriptKind.JS),bindings={};let code=output;
 for(const node of [...parsed.statements].reverse())if(ts.isImportDeclaration(node)){
  const imported=modules[node.moduleSpecifier.text];assert.ok(imported,'resolved '+node.moduleSpecifier.text);
  const clause=node.importClause;if(clause?.name)bindings[clause.name.text]=imported.default;
  if(clause?.namedBindings&&ts.isNamedImports(clause.namedBindings))for(const item of clause.namedBindings.elements)bindings[item.name.text]=imported[item.propertyName?.text??item.name.text];
  code=code.slice(0,node.getStart(parsed))+code.slice(node.end);
 }
 code=code.replace('export default function '+name,'function '+name).replace(/export function /g,'function ');
 return new Function(...Object.keys(bindings),'__EEAS_UI_SOURCE_SHA__',code+'\nreturn Object.assign('+name+', {'+extraExports.join(',')+'});')(...Object.values(bindings),'a'.repeat(64));
}
const Review=compileComponent(await readFile(resolve(app,'src/WorkbenchReview.tsx'),'utf8'),'WorkbenchReview',{'solid-js':solid,'solid-js/web':web,'./ui-language':fixtureLanguage});
const empty=()=>null,modules={'solid-js':solid,'solid-js/web':web,'@opencode-ai/ui/button':{Button:props=>props.children},'@opencode-ai/ui/icon':{Icon:empty},...brandModules};
const parsed=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
for(const node of parsed.statements)if(ts.isImportDeclaration(node)){
 const name=node.moduleSpecifier.text;if(!name.startsWith('./')||modules[name])continue;
 try{modules[name]=await import(pathToFileURL(resolve(app,'src',name.slice(2).replace(/\.ts$/,'')+'.ts')).href);}
 catch(error){if(!['ERR_MODULE_NOT_FOUND','ERR_UNKNOWN_FILE_EXTENSION'].includes(error.code))throw error;modules[name]={default:empty};}
}
modules['./BrandLogo']={BrandLoader:empty};
modules['./TaskStatus']={default:props=>props.value};
modules['./renderers']={GenericRenderer:empty,registerBuiltins:()=>{}};
modules['./StudioPopover']={default:props=>props.trigger};
const Composer=compileComponent(await readFile(resolve(app,'src/NativePageConversation.tsx'),'utf8'),'NativePageConversation',{'solid-js':solid,'solid-js/web':web,'./StudioPopover':modules['./StudioPopover'],'./ui-language':fixtureLanguage,'./native-composer.css':{}},['ReferenceIcon']);
modules['./NativePageConversation']={default:Composer,ReferenceIcon:Composer.ReferenceIcon};
modules['./CompanionFileContext']={default:compileComponent(await readFile(resolve(app,'src/CompanionFileContext.tsx'),'utf8'),'CompanionFileContext',{'solid-js':solid,'solid-js/web':web,'./StudioPopover':modules['./StudioPopover'],'./ui-language':fixtureLanguage,'./native-composer.css':{},'./NativePageConversation':modules['./NativePageConversation'],'./companion-files':await import('../src/companion-files.ts'),'./pinned-source-observer':await import('../src/pinned-source-observer.ts')})};
modules['./WorkbenchReview']={default:Review};
modules['./ui-language']=fixtureLanguage;
modules['./DesktopTitlebar']={default:compileComponent(await readFile(resolve(app,'src/DesktopTitlebar.tsx'),'utf8'),'DesktopTitlebar',{'solid-js':solid,'solid-js/web':web,...brandModules})};
modules['./ObjectDraft']={ObjectDraftBoundary:props=>props.children};
function environment(){const values=new Map(),events=new EventTarget();return {localStorage:{getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)},window:{innerWidth:1440,location:{search:''},matchMedia:()=>({matches:false}),addEventListener:events.addEventListener.bind(events),removeEventListener:events.removeEventListener.bind(events)}};}
// Solid SSR disposes its root on a zero-delay timer; retain the scoped
// globals through that cleanup instead of swallowing late exceptions.
async function render(source,host){const previous={localStorage:globalThis.localStorage,window:globalThis.window,eeasDesktop:globalThis.eeasDesktop};Object.assign(globalThis,environment());if(host)globalThis.eeasDesktop=host;else delete globalThis.eeasDesktop;try{return web.renderToString(()=>compileComponent(source,'App',modules)());}finally{await new Promise(resolve=>setTimeout(resolve,0));for(const[key,value]of Object.entries(previous))if(value===undefined)delete globalThis[key];else globalThis[key]=value;}}

test('actual compiled App renders its first shell with no project, original session, model, or feed',async()=>{
 const html=await render(source);assert.match(html,/class="as-shell"/);assert.match(html,/项目审阅工作台/);assert.match(html,/当前工作/);assert.match(html,/还没有任务|可先在原页面提问/);assert.match(html,/描述要完成的工作/);assert.doesNotMatch(html,/原 Agent 等待回应/);assert.ok(html.includes(brandModules['./assets/cirven-signature.svg'].default));assert.ok(html.includes(brandIcon));assert.doesNotMatch(html,/>EEAS</);
});

test('the captured unguarded original-session branch reproduces the real first-render failure',async()=>{
 const before=source.replace("originalSession()&&originalSession()!.project_id===project()?.id?","originalSession()?.project_id===project()?.id?");assert.notEqual(before,source);await assert.rejects(()=>render(before),/pending_questions/);
});


test('actual compiled App also renders the native default workbench with an empty project/session scope',async()=>{
 const unused=()=>{throw Error('SSR must not call a native operation');};
 const html=await render(source,{version:2,getCollaborationState:unused,captureCurrentContext:unused,setCollaborationMode:unused,onNativeContext:unused});
 // Native startup follows the confirmed full-window workbench default.
 assert.match(html,/class="as-shell"/);assert.match(html,/data-window-mode="workbench"/);assert.match(html,/描述要完成的工作/);assert.doesNotMatch(html,/原 Agent 等待回应/);assert.equal((html.match(/aria-label="显示或收起工作台"/g)??[]).length,1);assert.doesNotMatch(html,/aria-label="打开项目工作台"|aria-label="显示工作台"|aria-label="隐藏 Agent"/);assert.ok(html.includes(productBrand.display_name));assert.doesNotMatch(html,/>EEAS</);
});
