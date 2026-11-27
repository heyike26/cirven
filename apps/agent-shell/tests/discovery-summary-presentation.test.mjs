import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,writeFile,mkdir,mkdtemp} from 'node:fs/promises';
import {resolve,dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {tmpdir} from 'node:os';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {build} from 'esbuild';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';

const app=resolve(dirname(fileURLToPath(import.meta.url)),'..'),root=resolve(app,'../..');
const sha=value=>createHash('sha256').update(value).digest('hex');

test('actual App draft and historical-answer boundaries show safe readable Markdown without changing their source',async()=>{
 const sourcePath=process.env.EEAS_DISCOVERY_APP_SOURCE??resolve(app,'src/App.tsx');
 const source=await readFile(sourcePath,'utf8'),tree=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
 assert.deepEqual(tree.parseDiagnostics,[]);
 function element(className){const found=[];function visit(node){if(ts.isJsxElement(node)&&node.openingElement.attributes.properties.some(prop=>ts.isJsxAttribute(prop)&&prop.name.text==='class'&&ts.isStringLiteral(prop.initializer)&&prop.initializer.text===className))found.push(node);ts.forEachChild(node,visit);}visit(tree);assert.equal(found.length,1);return found[0].getText(tree);}
 const article=element('as-history-message as-agent-understanding'),dialog=element('as-history-preview');
 const owner=tree.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='App');
 const previewEffect=owner.body.statements.find(node=>ts.isExpressionStatement(node)&&node.getText(tree).startsWith('createEffect(')&&node.getText(tree).includes('historyDialog?.showModal()'));
 assert.ok(previewEffect,'Actual App preview lifecycle is part of the fixture');
 const base=process.env.TMPDIR??tmpdir();await mkdir(base,{recursive:true});
 const out=process.env.EEAS_DISCOVERY_SUMMARY_TEST_OUT??await mkdtemp(join(base,'discovery-summary-'));await mkdir(out,{recursive:true});
 const actual=process.env.EEAS_DISCOVERY_SUMMARY_TEXT?await readFile(process.env.EEAS_DISCOVERY_SUMMARY_TEXT,'utf8'):'## 当前要求\n\n- 保留来源与人工草稿\n- 同一任务继续反馈\n\n### 验收\n按实际结果检查。';
 const entry=`import {render} from 'solid-js/web';import {createSignal,createEffect,Show} from 'solid-js';
import AssistantReply from './src/AssistantReply.tsx';
import './src/conversation-navigation.css';import './src/styles.css';import './src/native-collaboration.css';import './src/agent-studio.css';import './src/brand-and-spacing.css';import './src/chat-workbench-layout.css';
function Fixture(){const initial=${JSON.stringify(actual)},[text,setText]=createSignal(initial),[historyPreview,setHistoryPreview]=createSignal(),[draft,setDraft]=createSignal('未发送的人工草稿 ## 原话');let historyDialog;const calls=[],identity={id:'display-session',project_id:'display-project',revision:2,sha256:'a'.repeat(64)};const activeSession=()=>({...identity,payload:{status:'DRAFT',candidate:{summary:text()}}}),uiLocale=()=> 'zh-CN',uiText=value=>value,envelope=(...args)=>args,inspect=(...args)=>calls.push(args);
${previewEffect.getText(tree)}
window.summaryFixture={setText,open:()=>setHistoryPreview({title:'同一原要求的历史版本',input:'## 用户原话保持字面量',answer:text()}),state:()=>({text:text(),preview:historyPreview()?.answer,draft:draft(),identity,calls})};
return <div class="as-app-frame"><div class="as-shell" data-chat-layout="adaptive" data-layout="workbench" data-window-mode="workbench" style={{display:'flex'}}><div class="as-agent-content"><main class="as-conversation"><div class="as-persistent-transcript">${article}</div></main><footer><input aria-label="Unsent draft" value={draft()} onInput={e=>setDraft(e.currentTarget.value)}/><button type="button" onClick={()=>window.summaryFixture.open()}>Read historical version</button></footer></div></div>${dialog}</div>;}
render(()=> <Fixture/>,document.getElementById('root'));`;
 const entryJS=ts.transpileModule(entry,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;
 const compiledEntry=babel.transformSync(entryJS,{filename:'actual-App-summary-boundaries.jsx',presets:[[solidPreset,{generate:'dom',hydratable:false}]],configFile:false,babelrc:false}).code;
 const result=await build({stdin:{contents:compiledEntry,resolveDir:app,sourcefile:'actual-App-summary-boundaries.mjs',loader:'js'},outfile:join(out,'fixture.js'),bundle:true,platform:'browser',format:'iife',target:'es2022',conditions:['browser'],metafile:true,loader:{'.ttf':'file','.woff2':'file'},plugins:[{name:'real-solid-components',setup(bundle){bundle.onLoad({filter:/\.tsx$/},async args=>{const raw=await readFile(args.path,'utf8'),js=ts.transpileModule(raw,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;return{contents:babel.transformSync(js,{filename:args.path,presets:[[solidPreset,{generate:'dom',hydratable:false}]],configFile:false,babelrc:false}).code,loader:'js',resolveDir:dirname(args.path)};});}}],jsx:'preserve'});
 assert.ok(Object.keys(result.metafile.inputs).some(path=>path.endsWith('src/summary-content.ts')));
 await writeFile(join(out,'index.html'),'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><link rel="stylesheet" href="fixture.css"><style>.as-agent-content{width:100%}footer{flex:none;padding:12px 20px}footer input{min-height:36px}.as-shell{min-height:0}</style></head><body><div id="root"></div><script src="fixture.js"></script></body></html>');
 await writeFile(join(out,'actual-summary.txt'),actual);
 await writeFile(join(out,'BUILD_INPUTS.r1.json'),JSON.stringify({App:{path:sourcePath,sha256:sha(source)},article_sha256:sha(article),dialog_sha256:sha(dialog),actual_summary_sha256:sha(actual),metafile:result.metafile,scope:'ACTUAL_APP_JSX_BOUNDARIES_AND_REAL_COMPONENT_CSS_NOT_FULL_APP_STARTUP'},null,2)+'\n');
 const output=await new Promise((yes,no)=>{const child=spawn(process.env.EEAS_TEST_PYTHON??resolve(root,'.venv/bin/python'),['-B',resolve(app,'tests/discovery-summary-browser.py'),'--out',out],{cwd:root,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},stdio:['ignore','pipe','pipe']});let stdout='',stderr='';child.stdout.on('data',value=>stdout+=value);child.stderr.on('data',value=>stderr+=value);child.on('error',no);child.on('exit',code=>code===0?yes(stdout):no(new Error(`Actual summary browser exit ${code}\n${stdout}\n${stderr}`)));});
 const receipt=JSON.parse(output.trim().split('\n').at(-1));assert.equal(receipt.status,'PASS');assert.equal(receipt.cases.length,2);assert.equal(receipt.original_summary_sha256,sha(actual));assert.equal(await readFile(sourcePath,'utf8'),source);
 console.log('Actual summary evidence: '+out);
});
