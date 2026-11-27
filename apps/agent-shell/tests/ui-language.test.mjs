import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';
import * as solid from 'solid-js';
import * as web from 'solid-js/web';
import {UILanguageController,UI_LANGUAGE_STORAGE_KEY,assertUILanguageInfo,resolveUILocale,translateUI} from '../src/ui-language.ts';
import {authoredErrorCopy,authoredText} from '../src/authored-ui-copy.ts';
import {splitPickedPath} from '../src/native-picker.ts';
import {graphElements} from '../src/graph-data.ts';
import {deliveryFeedItem,unavailableDeliveryItem} from '../src/delivery-recovery.ts';
import {ApiError} from '../src/api.ts';
const app=resolve(dirname(fileURLToPath(import.meta.url)),'..');
test('file conflict advice is localized without changing the original error, receipt or user text',()=>{
 const raw='SOURCE_DRIFT: expected revision/SHA differs from current file; reread before editing';
 const detail={code:'SOURCE_DRIFT',expected_revision:2},failure=new ApiError(409,'REVISION_CONFLICT',raw,detail);
 const copy=authoredErrorCopy(failure);
 assert.equal(authoredText(copy,(key,params)=>translateUI(key,'zh-CN',params)),'文件已发生变化；请先核对最新内容，再继续操作。');
 assert.equal(authoredText(copy,(key,params)=>translateUI(key,'en',params)),'The file has changed. Review its latest contents before continuing.');
 assert.equal(failure.message,raw);assert.equal(failure.code,'REVISION_CONFLICT');assert.equal(failure.detail,detail);
 assert.equal(authoredErrorCopy(raw),raw,'User-authored strings are not protocol errors');
 const unrelated=new Error('Keep my original SOURCE_DRIFT: quotation');assert.equal(authoredErrorCopy(unrelated),unrelated.message);
});
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const memory=(initial)=>{const values=new Map(initial?[[UI_LANGUAGE_STORAGE_KEY,initial]]:[]),writes=[];return {values,writes,getItem:key=>values.get(key)??null,setItem:(key,value)=>{writes.push([key,value]);values.set(key,value);}};};
function host(initial={preference:'en',resolved_locale:'en'}){const calls=[];let listener;return {calls,emit:value=>listener?.(value),bridge:{getUILanguage:async()=>{calls.push(['get']);return initial;},setUILanguage:async preference=>{calls.push(['set',preference]);return {preference,resolved_locale:preference==='zh-CN'?'zh-CN':'en'};},onUILanguageChanged:fn=>{calls.push(['listen']);listener=fn;return()=>{calls.push(['unlisten']);listener=undefined;};}}};}

test('locale resolution is bounded to the shared preference contract and receipts are validated',()=>{
 for(const language of ['zh','zh-CN','zh-TW','zh-Hans','zh_CN'])assert.equal(resolveUILocale('system',language),'zh-CN');
 for(const language of ['en','fr-FR','ja',''])assert.equal(resolveUILocale('system',language),'en');
 assert.equal(resolveUILocale('en','zh-CN'),'en');assert.equal(resolveUILocale('zh-CN','en-US'),'zh-CN');
 for(const value of [null,{preference:'fr',resolved_locale:'en'},{preference:'zh-CN',resolved_locale:'en'},{preference:'system',resolved_locale:'zh'}])assert.throws(()=>assertUILanguageInfo(value),/INVALID/);
});

test('web preference survives a new controller without constructor writes or session/project effects',async()=>{
 const storage=memory(),changes=[],controller=new UILanguageController({storage,systemLocale:()=> 'zh-Hans',onChange:value=>changes.push(value)});
 assert.deepEqual(storage.writes,[]);await controller.start();assert.deepEqual(controller.info,{preference:'system',resolved_locale:'zh-CN'});
 await controller.set('en');assert.deepEqual(storage.writes,[[UI_LANGUAGE_STORAGE_KEY,'en']]);assert.equal(controller.info.resolved_locale,'en');controller.stop();
 const reopened=new UILanguageController({storage,systemLocale:()=> 'zh-CN'});await reopened.start();assert.equal(reopened.info.preference,'en');assert.equal(storage.writes.length,1);reopened.stop();
 assert.deepEqual(changes.map(value=>value.resolved_locale),['zh-CN','en']);
});

test('blocked web storage preserves the confirmed preference and does not pretend a save succeeded',async()=>{
 const controller=new UILanguageController({storage:{getItem:()=> 'zh-CN',setItem:()=>{throw Error('storage denied');}},systemLocale:()=> 'en'});
 await controller.start();await assert.rejects(controller.set('en'),/storage denied/);assert.deepEqual(controller.info,{preference:'zh-CN',resolved_locale:'zh-CN'});controller.stop();
 const invalid=memory('arbitrary-value'),fallback=new UILanguageController({storage:invalid,systemLocale:()=> 'en'});await fallback.start();assert.deepEqual(fallback.info,{preference:'system',resolved_locale:'en'});assert.equal(invalid.writes.length,0);fallback.stop();
});

test('native host is authoritative; local web preferences neither override it nor receive native writes',async()=>{
 const storage=memory('zh-CN'),native=host(),controller=new UILanguageController({native:true,bridge:native.bridge,storage,systemLocale:()=> 'zh-CN'});
 await controller.start();assert.equal(controller.info.preference,'en');await controller.set('zh-CN');assert.equal(controller.info.preference,'zh-CN');assert.deepEqual(storage.writes,[]);
 native.emit({preference:'en',resolved_locale:'en'});assert.equal(controller.info.preference,'en');native.emit({preference:'unknown',resolved_locale:'zh-CN'});assert.equal(controller.info.preference,'en');controller.stop();assert.equal(native.calls.filter(row=>row[0]==='unlisten').length,1);
});

test('a native event or explicit selection prevents a late initial read from replacing newer language',async()=>{
 for(const mode of ['event','selection']){
  const native=host(),gate=deferred();native.bridge.getUILanguage=()=>gate.promise;const controller=new UILanguageController({native:true,bridge:native.bridge,systemLocale:()=> 'en'}),starting=controller.start();
  if(mode==='event')native.emit({preference:'zh-CN',resolved_locale:'zh-CN'});else await controller.set('zh-CN');
  gate.resolve({preference:'en',resolved_locale:'en'});await starting;assert.equal(controller.info.preference,'zh-CN');controller.stop();
 }
});

test('overlapping preference receipts and host events cannot roll the display back to an older write',async()=>{
 const native=host(),first=deferred(),second=deferred();let count=0;native.bridge.setUILanguage=()=>++count===1?first.promise:second.promise;
 const controller=new UILanguageController({native:true,bridge:native.bridge});await controller.start();const a=controller.set('zh-CN'),b=controller.set('en');
 second.resolve({preference:'en',resolved_locale:'en'});await b;first.resolve({preference:'zh-CN',resolved_locale:'zh-CN'});await a;assert.equal(controller.info.preference,'en');
 const third=deferred();native.bridge.setUILanguage=()=>third.promise;const writing=controller.set('en');native.emit({preference:'zh-CN',resolved_locale:'zh-CN'});third.resolve({preference:'en',resolved_locale:'en'});await writing;assert.equal(controller.info.preference,'zh-CN');controller.stop();
});

test('an invalid host event cannot suppress a valid pending authoritative read',async()=>{
 const native=host(),read=deferred();native.bridge.getUILanguage=()=>read.promise;const controller=new UILanguageController({native:true,bridge:native.bridge,systemLocale:()=> 'zh-CN'}),starting=controller.start();
 native.emit({preference:'en',resolved_locale:'invalid'});read.resolve({preference:'en',resolved_locale:'en'});await starting;assert.deepEqual(controller.info,{preference:'en',resolved_locale:'en'});controller.stop();
});

test('disposal removes listeners and late read/write responses do not update the disposed owner',async()=>{
 const native=host(),read=deferred(),changes=[];native.bridge.getUILanguage=()=>read.promise;const controller=new UILanguageController({native:true,bridge:native.bridge,onChange:value=>changes.push(value)}),starting=controller.start();controller.stop();read.resolve({preference:'zh-CN',resolved_locale:'zh-CN'});await starting;assert.deepEqual(changes,[]);
 const write=deferred();native.bridge.getUILanguage=async()=>({preference:'en',resolved_locale:'en'});native.bridge.setUILanguage=()=>write.promise;await controller.start();const setting=controller.set('zh-CN');controller.stop();write.resolve({preference:'zh-CN',resolved_locale:'zh-CN'});await setting;assert.equal(controller.info.preference,'en');assert.equal(changes.length,1);
});

test('missing native methods and invalid native receipts do not fall back to false local persistence',async()=>{
 const storage=memory('zh-CN'),unsupported=new UILanguageController({native:true,storage});await assert.rejects(unsupported.start(),/NOT_SUPPORTED/);await assert.rejects(unsupported.set('en'),/NOT_SUPPORTED/);assert.deepEqual(storage.writes,[]);
 const native=host(),controller=new UILanguageController({native:true,storage,bridge:native.bridge});await controller.start();native.bridge.setUILanguage=async()=>({preference:'zh-CN',resolved_locale:'en'});await assert.rejects(controller.set('zh-CN'),/RECEIPT_INVALID/);assert.equal(controller.info.preference,'en');assert.deepEqual(storage.writes,[]);controller.stop();
});

test('catalogs have both locales and identical parameter names; interpolated user text is unchanged',async()=>{
 const catalogRows=new Map();
 for(const filename of ['renderer.json','renderer-agent.json']){
  const catalog=JSON.parse(await readFile(resolve(app,'../../packages/i18n',filename),'utf8'));assert.equal(catalog.schema_version,1);assert.ok(Object.keys(catalog.messages).length>0);
  for(const [key,row]of Object.entries(catalog.messages)){
   if(catalogRows.has(key))assert.deepEqual(row,catalogRows.get(key),'Conflicting shared UI copy: '+key);else catalogRows.set(key,row);
   assert.equal(typeof row['zh-CN'],'string',key);assert.equal(typeof row.en,'string',key);assert.ok(row.en.trim(),key);
   const params=text=>[...text.matchAll(/\{([A-Za-z][A-Za-z0-9_]*)\}/g)].map(match=>match[1]).sort();assert.deepEqual(params(row.en),params(row['zh-CN']),key);
  }
 }
 const raw='用户自己的要求😀.docx';assert.equal(translateUI('移除 {value0}','en',{value0:raw}),'Remove '+raw);assert.equal(translateUI('移除 {value0}','zh-CN',{value0:raw}),'移除 '+raw);assert.equal(translateUI('没有在产品目录中的用户原文😀','en'),'没有在产品目录中的用户原文😀');
});

test('actual dynamic validation errors keep original messages and translate structured copy without guessing user text',()=>{
 for(const [run,raw,english]of [
  [()=>splitPickedPath('D:\\论文.txt',['docx','dotx']),'请选择 DOCX / DOTX 文件','Choose a DOCX / DOTX file'],
  [()=>graphElements([{node_id:'original',role:'任务原文',dependency_node_ids:['用户节点😀']}],[]),'任务图缺少依赖节点 用户节点😀','Task graph is missing dependency node 用户节点😀']
 ]){let error;try{run();}catch(value){error=value;}assert.ok(error instanceof Error);assert.equal(error.message,raw);const copy=authoredErrorCopy(error);assert.equal(authoredText(copy,(key,params)=>translateUI(key,'en',params)),english);assert.equal(authoredText(copy,(key,params)=>translateUI(key,'zh-CN',params)),raw);assert.equal(error.message,raw);}
});

test('delivery presentation copy switches both ways while original receipts and historical summaries remain unchanged',()=>{
 const ref={id:'source',revision:4,sha256:'a'.repeat(64)},target={projectId:'p',kind:'STAGE',basis:{...ref,revision:5,sha256:'b'.repeat(64)}},result={operation_id:'original-operation',status:'STAGE_REPORT',session_ref:ref,pending_count:2,blocking_count:1,report:{artifact:{id:'word',sha256:'c'.repeat(64),filename:'用户原文件.docx'},pending:{download_url:'/pending'}},diagrams:[],artifacts:[],project_files:[],file_checks:[],basis_current:false,current_review_status:'SOURCE_OR_VERSION_CHANGED',execution_authorized:false,historical_receipt_preserved:true},before=structuredClone(result);
 const item=deliveryFeedItem(target,result),raw=item.body,en=authoredText(item.body_copy,(key,params)=>translateUI(key,'en',params));
 assert.match(en,/Based on requirements v4.*currently selected v5.*historical source retained/);assert.match(en,/2 items need confirmation/);assert.doesNotMatch(en,/SOURCE_OR_VERSION_CHANGED|original-operation/);const zh=authoredText(item.body_copy,(key,params)=>translateUI(key,'zh-CN',params));assert.match(zh,/依据需求 v4.*当前选中 v5.*历史来源保留.*2 项待确认/);assert.doesNotMatch(zh,/SOURCE_OR_VERSION_CHANGED|original-operation/);assert.match(raw,/SOURCE_OR_VERSION_CHANGED/);assert.equal(item.body,raw);assert.equal(item.artifact.data,result);assert.deepEqual(result,before);
 const missing=unavailableDeliveryItem(target,'unknown-operation',new ApiError(404,'NOT_FOUND','original source not present'));
 assert.match(authoredText(missing.body_copy,(key,params)=>translateUI(key,'en',params)),/No receipt was found; this does not establish whether files were generated\./);assert.match(missing.body,/没有记录不证明/);assert.equal(missing.status,'NO_RECORD');
});

function compileGraphEditor(source,locale){
 const js=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;
 let code=babel.transformSync(js,{filename:'GraphEditor.jsx',presets:[[solidPreset,{generate:'ssr',hydratable:false}]],configFile:false,babelrc:false}).code;
 const parsed=ts.createSourceFile('GraphEditor.mjs',code,ts.ScriptTarget.Latest,true,ts.ScriptKind.JS),bindings={},modules={'solid-js':solid,'solid-js/web':web,'@opencode-ai/ui/button':{Button:props=>props.children},'./ui-language':{t:(message,params)=>translateUI(message,locale,params)}};
 for(const node of [...parsed.statements].reverse())if(ts.isImportDeclaration(node)){const imported=modules[node.moduleSpecifier.text];assert.ok(imported,node.moduleSpecifier.text);const clause=node.importClause;if(clause?.name)bindings[clause.name.text]=imported.default;if(clause?.namedBindings&&ts.isNamedImports(clause.namedBindings))for(const item of clause.namedBindings.elements)bindings[item.name.text]=imported[item.propertyName?.text??item.name.text];code=code.slice(0,node.getStart(parsed))+code.slice(node.end);}
 code=code.replace('export default function GraphEditor','function GraphEditor');return new Function(...Object.keys(bindings),code+'\nreturn GraphEditor;')(...Object.values(bindings));
}

test('actual compiled graph editor localizes controls but retains Chinese document, node and source content exactly',async()=>{
 const source=await readFile(resolve(app,'src/GraphEditor.tsx'),'utf8'),graph={id:'g',title:'用户自己的中文图名😀',kind:'FLOW',nodes:[{id:'n1',label:'取消',description:'保存新版本',claim_ids:['c1'],source_anchors:[],origin:'USER_STATEMENT'}],edges:[]},before=structuredClone(graph),props={graph,claims:[{id:'c1',text:'用户正文，不要自动翻译'}],onChange:()=>assert.fail('rendering and language changes do not edit graph data')};
 const en=web.renderToString(()=>compileGraphEditor(source,'en')(props)),zh=web.renderToString(()=>compileGraphEditor(source,'zh-CN')(props));
 assert.match(en,/Edit graph nodes and relationships/);assert.match(en,/Delete node/);assert.match(zh,/编辑图的节点与关系/);assert.match(zh,/删除节点/);
 for(const html of [en,zh]){assert.ok(html.includes('用户自己的中文图名😀'));assert.match(html,/value="取消"/);assert.match(html,/<textarea value="保存新版本">/);assert.ok(html.includes('用户正文，不要自动翻译'));}
 assert.deepEqual(graph,before);
});
