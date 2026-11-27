import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {evidenceDirectory} from './historical-renderer-evidence.mjs';
const root=process.env.EEAS_REPO,out=evidenceDirectory('planning-source-editor');
if(!root)throw Error('Explicit repository required');
const app=path.join(root,'apps/agent-shell'),req=createRequire(path.join(app,'package.json'));
const ts=req('typescript'),babel=req('@babel/core'),esbuild=req('esbuild'),solid=createRequire(req.resolve('vite-plugin-solid'))('babel-preset-solid');
const entry=`import{createSignal}from'solid-js';import{render}from'solid-js/web';import PlanningSourceEditor from'${app}/src/PlanningSourceEditor.tsx';import{SourceEvidenceContext}from'${app}/src/SourceNotes.tsx';import{startUILanguage,setUILanguagePreference}from'${app}/src/ui-language.ts';
startUILanguage();const[receipt,setReceipt]=createSignal(window.sourceFixture),[anchors,setAnchors]=createSignal([]),[current,setCurrent]=createSignal(true);const changes=[];
const scope={get receipt(){return receipt()},get sourceCurrent(){return current()}};
const dispose=render(()=><SourceEvidenceContext.Provider value={scope}><PlanningSourceEditor anchors={anchors()} receipt={receipt()} onChange={value=>{changes.push(structuredClone(value));setAnchors(value)}}/></SourceEvidenceContext.Provider>,document.getElementById('fixture'));
window.sourceQA={receipt,setReceipt,anchors,changes,setCurrent,locale:setUILanguagePreference,dispose};`;
fs.mkdirSync(out,{recursive:true});const file=path.join(out,'source-editor.tsx');fs.writeFileSync(file,entry);
try{await esbuild.build({entryPoints:[file],outfile:path.join(out,'source-editor.js'),bundle:true,platform:'browser',format:'iife',nodePaths:[app+'/node_modules'],conditions:['browser'],define:{'process.env.NODE_ENV':'"production"'},plugins:[{name:'real-source-editor',setup(build){build.onLoad({filter:/\.tsx$/},({path:file})=>({contents:babel.transformSync(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText,{filename:file+'.jsx',presets:[[solid,{generate:'dom',hydratable:false}]],configFile:false,babelrc:false}).code,loader:'js',resolveDir:path.dirname(file)}))}}],logLevel:'silent'});}finally{esbuild.stop();}
