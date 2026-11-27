import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,writeFile,mkdir,mkdtemp} from 'node:fs/promises';
import {resolve,dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {tmpdir} from 'node:os';
import {spawn} from 'node:child_process';
import {build} from 'esbuild';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';

const app=resolve(dirname(fileURLToPath(import.meta.url)),'..'),root=resolve(app,'../..');

test('actual AssistantReply DOM preserves complete prose and stable reading state, with inert links and local keyboard scrolling',async()=>{
 const base=process.env.TMPDIR??tmpdir();await mkdir(base,{recursive:true});
 const out=process.env.EEAS_ASSISTANT_REPLY_TEST_OUT??await mkdtemp(join(base,'assistant-reply-'));
 await mkdir(out,{recursive:true});
 const result=await build({
  stdin:{contents:`import {createSignal,createComponent,For} from 'solid-js';
import {render} from 'solid-js/web';
import AssistantReply from './src/AssistantReply.tsx';
import {stableItems} from './src/stable-items.ts';
import {startUILanguage,setUILanguagePreference,uiLocale} from './src/ui-language.ts';
const releaseLanguage=startUILanguage(),[rows,setRows]=createSignal([{id:'original-operation',parts:[{id:'original-part',type:'text',text:''}],receipt:0}]),[links,setLinks]=createSignal(true),opens=[],projection=stableItems(rows,row=>row.id);
render(()=>createComponent(For,{get each(){return projection();},children:row=>{const parts=stableItems(()=>row.parts.map((part,index)=>({part,index})),entry=>JSON.stringify([entry.part.id,entry.part.type,entry.index]));return createComponent(For,{get each(){return parts();},children:entry=>createComponent(AssistantReply,{get text(){return entry.part.text;},get onOpenLink(){return links()?href=>{opens.push(href);}:undefined;}})});}}),document.querySelector('#reply-owner'));
window.replyFixture={setText:text=>setRows(values=>values.map(row=>({...row,parts:row.parts.map(part=>({...part,text}))}))),receipt:()=>setRows(values=>values.map(row=>({...row,parts:row.parts.map(part=>({...part})),receipt:row.receipt+1}))),setLinks,opens,setLocale:async locale=>{await setUILanguagePreference(locale);return uiLocale();},dispose:releaseLanguage};`,resolveDir:app,sourcefile:'assistant-reply-fixture.mjs',loader:'js'},
  outfile:join(out,'fixture.js'),bundle:true,platform:'browser',format:'iife',target:'es2022',conditions:['browser'],metafile:true,
  plugins:[{name:'actual-solid-component',setup(bundle){bundle.onLoad({filter:/\.tsx$/},async args=>{
   const source=await readFile(args.path,'utf8'),js=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;
   const transformed=babel.transformSync(js,{filename:args.path,presets:[[solidPreset,{generate:'dom',hydratable:false}]],configFile:false,babelrc:false});
   return {contents:transformed.code,loader:'js',resolveDir:dirname(args.path)};
  });}}],
 });
 assert.ok(Object.keys(result.metafile.inputs).some(path=>path.endsWith('src/AssistantReply.tsx')));
 assert.ok(Object.keys(result.metafile.inputs).some(path=>path.endsWith('src/summary-content.ts')));
 const legacy=(await Promise.all(['native-collaboration.css','brand-and-spacing.css','task-activity-shell.css'].map(name=>readFile(resolve(app,'src',name),'utf8')))).join('\n');
 await writeFile(join(out,'legacy.css'),legacy);
 await writeFile(join(out,'index.html'),`<!doctype html><html lang="en"><head><meta charset="utf-8"><link rel="stylesheet" href="fixture.css"><link rel="stylesheet" href="legacy.css"><style>
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;font-family:Arial,"Noto Sans CJK SC",sans-serif;color:#343b42;background:#fff}.as-app-frame{height:100%;display:flex;flex-direction:column;min-width:0}.reading{flex:1;min-width:0;min-height:0;overflow:auto;padding:16px}.as-native-message{min-width:0;max-width:100%;margin:0;padding:0;border:0}#reply-owner{min-width:0;max-width:100%}footer{flex:none;padding:12px;border-top:1px solid #ddd}footer button{font:14px/1.7 Arial;padding:6px 12px}
</style></head><body><div class="as-app-frame"><main class="reading"><article class="as-native-message"><div class="as-native-agent-message" id="reply-owner"></div></article></main><footer><button id="last-action">Continue</button></footer></div><script src="fixture.js"></script></body></html>`);
 await writeFile(join(out,'BUILD_INPUTS.json'),JSON.stringify(result.metafile,null,2)+'\n');
 if(process.env.EEAS_ASSISTANT_REPLY_SAVED_RESULT)await writeFile(join(out,'saved-reply.json'),await readFile(process.env.EEAS_ASSISTANT_REPLY_SAVED_RESULT));
 const script=resolve(app,'tests/assistant-reply-browser.py');
 const stdout=await new Promise((yes,no)=>{
  const python=process.env.EEAS_TEST_PYTHON??resolve(root,'.venv/bin/python'),child=spawn(python,['-B',script,'--out',out],{cwd:root,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1',TMPDIR:out,TEMP:out,TMP:out},stdio:['ignore','pipe','pipe']});let output='',errors='';
  child.stdout.on('data',data=>output+=data);child.stderr.on('data',data=>errors+=data);child.on('error',no);child.on('exit',code=>code===0?yes(output):no(new Error(`AssistantReply browser exit ${code}\n${output}\n${errors}`)));
 });
 const receipt=JSON.parse(stdout.trim().split('\n').at(-1));assert.equal(receipt.status,'PASS');assert.equal(receipt.model_calls,0);assert.equal(receipt.scenes.length,3);
 assert.ok(receipt.scenes.every(scene=>scene.body_overflow===false&&scene.complete_tail&&scene.keyboard_regions));
 assert.equal(receipt.stable_receipt_dom,true);assert.equal(receipt.safe_links,true);
 console.log('AssistantReply evidence: '+out);
});
