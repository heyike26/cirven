import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, writeFile, mkdtemp, rm } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawn } from 'node:child_process';
import {tmpdir} from 'node:os';
import readline from 'node:readline';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';
import { renderToString } from 'solid-js/web';
import { ApiClient, ApiError } from '../src/api.ts';
import { PinnedSourceObserver, pinnedObservationLabel } from '../src/pinned-source-observer.ts';
import { ComposerPresentation } from '../src/composer-presentation.ts';
import { readNativeText, saveNativeText, readNativeDraft, saveNativeDraft } from '../src/native-collaboration.ts';
import { saveCompanionFileDraft, readCompanionFileDraft } from '../src/companion-files.ts';

const here=dirname(fileURLToPath(import.meta.url)),app=resolve(here,'..'),root=resolve(app,'../..');
const ref={kind:'local_file',project_id:'p',workspace_id:'w',binding_revision:1,binding_sha256:'a'.repeat(64),path:'source.json',revision:3,sha256:'b'.repeat(64)},next={...ref,revision:4,sha256:'c'.repeat(64)};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function storage(){const values=new Map();return{getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key),values};}

test('folding for history and quick-question expansion preserve original text, twenty frozen annotations and exact pinned file/write choice',()=>{
 const local=storage(),text='保留这份长草稿😀\n'.repeat(200),attachments=Array.from({length:20},(_,i)=>({context_ref:{id:'actual-'+i,revision:1,sha256:'d'.repeat(64)},label:'真实冻结引用'+i,comment:'人工批注'+i,regions:[]}));saveNativeText(local,'p',text);saveNativeDraft(local,'p',attachments);saveCompanionFileDraft(local,'p',{source_ref:ref,write_requested:true});const before=[...local.values],views=[],controller=new ComposerPresentation(value=>views.push(value));
 controller.focus(true);controller.fold();assert.deepEqual(controller.snapshot(),{folded:true,focused:false});controller.focus(false);assert.equal(controller.snapshot().folded,true);controller.compose();controller.focus(true);assert.deepEqual(controller.snapshot(),{folded:false,focused:true});controller.focus(false);assert.deepEqual([...local.values],before);assert.equal(readNativeText(local,'p'),text);assert.deepEqual(readNativeDraft(local,'p'),attachments);assert.deepEqual(readCompanionFileDraft(local,'p'),{source_ref:ref,write_requested:true});assert.ok(views.some(value=>value.folded));
});

test('actual current-source observations mark pinned v3 vs current v4 without adopting or changing authority; failed reads never reuse cached current',async()=>{
 const values=[],calls=[],responses=[{current:true,source_ref:ref},{current:true,source_ref:next},new Error('API unavailable'),new ApiError(404,'NOT_FOUND','missing')],source=structuredClone(ref),observer=new PinnedSourceObserver(value=>values.push(value),{file:async(pid,wid,path,signal)=>{calls.push({pid,wid,path,signal});const value=responses.shift();if(value instanceof Error)throw value;return value;}});
 observer.update(source);await tick();assert.equal(values.at(-1).status,'CURRENT');assert.match(pinnedObservationLabel(source,values.at(-1)),/读取时原件一致/);await observer.refresh();assert.equal(values.at(-1).status,'STALE');assert.deepEqual(values.at(-1).current_ref,next);assert.match(pinnedObservationLabel(source,values.at(-1)),/固定引用 v3.*v4/);assert.deepEqual(source,ref);await observer.refresh();assert.equal(values.at(-1).status,'NOT_RECHECKED');assert.ok(!values.at(-1).current_ref);assert.match(pinnedObservationLabel(source,values.at(-1)),/未核对/);await observer.refresh();assert.equal(values.at(-1).status,'MISSING');assert.equal(calls.length,4);assert.ok(calls.every(value=>value.pid==='p'&&value.wid==='w'&&value.path==='source.json'));observer.stop();
});

test('old-project, substituted path, binding changes and disposed responses cannot mislabel a pin as current',async()=>{
 let release;const values=[],old=new Promise(resolve=>release=resolve),other={...ref,project_id:'q'},observer=new PinnedSourceObserver(value=>values.push(value),{file:async(pid)=>pid==='p'?old:{current:true,source_ref:other}});observer.update(ref);observer.update(other);await tick();release({current:true,source_ref:next});await tick();assert.equal(values.at(-1).pinned_ref.project_id,'q');assert.equal(values.at(-1).status,'CURRENT');observer.stop();
 const wrong=[],invalid=new PinnedSourceObserver(value=>wrong.push(value),{file:async()=>({current:true,source_ref:{...next,path:'different.json'}})});invalid.update(ref);await tick();assert.equal(wrong.at(-1).status,'NOT_RECHECKED');invalid.stop();const changed=[],binding=new PinnedSourceObserver(value=>changed.push(value),{file:async()=>({current:true,source_ref:{...next,binding_sha256:'e'.repeat(64)}})});binding.update(ref);await tick();assert.equal(changed.at(-1).status,'BINDING_CHANGED');binding.stop();
});

test('actual Solid expanded file-menu keeps stale wording and pinned identity without granting writes',async t=>{
 const directory=await mkdtemp(resolve(process.env.TMPDIR??tmpdir(),'source13-context-'));t.after(()=>rm(directory,{recursive:true,force:true}));
 // This boundary exposes menu content for SSR inspection only. The production
 // component and its reference icon remain real; closed/open behavior is covered
 // by native-composer's actual reactive component tests, then real browser QA.
 const paths={'solid-js':resolve(app,'node_modules/solid-js/dist/server.js'),'solid-js/web':resolve(app,'node_modules/solid-js/web/dist/server.js'),'./companion-files':resolve(app,'src/companion-files.ts'),'./pinned-source-observer':resolve(app,'src/pinned-source-observer.ts'),'./ui-language':resolve(app,'tests/fixture-ui-language.mjs'),'./StudioPopover':resolve(directory,'popover-boundary.mjs'),'./NativePageConversation':resolve(directory,'native-composer.mjs')};
 await writeFile(paths['./StudioPopover'],'export default props=>[props.trigger,props.children];\n');
 async function compile(filename,out){const source=await readFile(resolve(app,'src',filename),'utf8'),js=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText.replace(/import ['"].*?\.css['"];?/g,'');let compiled=babel.transformSync(js,{filename:filename+'.jsx',presets:[[solidPreset,{generate:'ssr',hydratable:false}]],configFile:false,babelrc:false}).code;compiled=compiled.replace(/from (['"])(.*?)\1/g,(all,quote,name)=>paths[name]?`from '${pathToFileURL(paths[name]).href}'`:all);await writeFile(out,compiled);}
 await compile('NativePageConversation.tsx',paths['./NativePageConversation']);const file=resolve(directory,'file-context.mjs');await compile('CompanionFileContext.tsx',file);
 const {default:Component}=await import(pathToFileURL(file).href),selected={file:{source_ref:ref,current:true,encoding:'utf-8',byte_length:3,content:'old'},workspace:{allowed_write_paths:['source.json']}};
 const before=JSON.stringify(selected);const html=renderToString(()=>Component({selected,observation:{pinned_ref:ref,status:'STALE',current_ref:next},required:false,busy:false,title:'当前页协作',onChoose:()=>{},onOpen:()=>{},onReadCurrent:()=>{},onRemove:()=>{}}));
 assert.match(html,/固定引用 v3.*原件已变.*v4/);assert.ok(!html.includes('原版本仍有效'));assert.ok(!/<details class="as-native-file-diagnostics"[^>]*\bopen/.test(html));assert.match(html,/重新读取当前版本/);assert.match(html,/所选文件版本 3/);assert.match(html,/修改文件需另行明确授权/);assert.equal(JSON.stringify(selected),before);assert.equal(selected.file.source_ref,ref);assert.match(html,/aria-expanded="false"/);
});

test('real app GET observes external human change without modifying the immutable pin, feedback or project files',async t=>{
 const child=spawn(resolve(root,'.venv/bin/python'),[resolve(here,'rework-http-fixture.py')],{cwd:root,stdio:['pipe','pipe','pipe']}),waiting=[];let stderr='';child.stderr.on('data',v=>stderr=(stderr+v).slice(-4000));const lines=readline.createInterface({input:child.stdout});lines.on('line',line=>{const next=waiting.shift();if(next)try{next.resolve(JSON.parse(line));}catch(error){next.reject(error);}});child.on('error',error=>waiting.splice(0).forEach(v=>v.reject(error)));child.on('exit',code=>waiting.splice(0).forEach(v=>v.reject(new Error(`fixture exit ${code}: ${stderr}`))));t.after(async()=>{child.stdin.end();await new Promise(resolve=>child.exitCode!==null?resolve():child.once('exit',resolve));lines.close();});const raw=body=>new Promise((resolve,reject)=>{waiting.push({resolve,reject});child.stdin.write(JSON.stringify(body)+'\n');});const setup=await raw({fixture:'setup'});assert.equal(setup.status,200);const scene=setup.body,calls=[],api=new ApiClient(async(url,options)=>{calls.push({url,method:options.method??'GET'});const result=await raw({method:options.method??'GET',path:url,headers:options.headers??{},body:options.body?JSON.parse(options.body):undefined});return new Response(JSON.stringify(result.body),{status:result.status});}),values=[],observer=new PinnedSourceObserver(value=>values.push(value),api),pin=structuredClone(scene.source);observer.update(pin);for(let i=0;i<100&&values.at(-1)?.status!=='CURRENT';i++)await new Promise(resolve=>setTimeout(resolve,10));assert.equal(values.at(-1).status,'CURRENT');const human=scene.original.replace('原人工备注','外部人工备注必须保留');await raw({fixture:'tamper',text:human});await observer.refresh();assert.equal(values.at(-1).status,'STALE');assert.ok(values.at(-1).current_ref.revision>pin.revision);assert.deepEqual(pin,scene.source);assert.ok(calls.every(value=>value.method==='GET'));const actual=(await raw({fixture:'bytes'})).body;assert.equal(actual.actual,human);assert.equal(actual.independent,scene.independent);observer.stop();
});
