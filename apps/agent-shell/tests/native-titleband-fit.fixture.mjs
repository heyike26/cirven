import {readFileSync,writeFileSync} from 'node:fs';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
const app=resolve(dirname(fileURLToPath(import.meta.url)),'..'),req=createRequire(resolve(app,'package.json'));
import {evidenceDirectory} from './historical-renderer-evidence.mjs';
const out=evidenceDirectory('native-titleband');
const ts=req('typescript'),babel=req('@babel/core'),esbuild=req('esbuild');
const preset=(await import(resolve(app,'node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js'))).default;
const pin=p=>{const b=readFileSync(p);return {path:p,bytes:b.length,sha256:createHash('sha256').update(b).digest('hex')};};
const used=new Map();
const plugin={name:'actual-titleband',setup(b){b.onLoad({filter:/\.tsx$/},({path})=>{used.set(path,pin(path));return {contents:babel.transformSync(ts.transpileModule(readFileSync(path,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText,{filename:path+'.jsx',presets:[[preset,{generate:'dom',hydratable:false}]],configFile:false,babelrc:false}).code,loader:'js',resolveDir:dirname(path)};});b.onLoad({filter:/\.css$/},({path})=>{used.set(path,pin(path));return {contents:'',loader:'js'};});}};
const entry=resolve(out,'entry.tsx');
writeFileSync(entry,`import{render}from'solid-js/web';import{createSignal}from'solid-js';import Titlebar from '${app}/src/DesktopTitlebar';import WorkspaceToolbar from '${app}/src/WorkspaceToolbar';import Center from '${app}/src/SupervisorCommandCenter';import{startUILanguage}from'${app}/src/ui-language';startUILanguage();const[w,setW]=createSignal(window.qaInitial.workspaces[0]),[snapshot,setSnapshot]=createSignal(window.qaInitial.snapshot);window.qa={selected:[],open:0,publish:value=>setSnapshot(structuredClone(value))};render(()=><div class="as-app-frame"><Titlebar mode="workbench" actions={<WorkspaceToolbar workspace={w()} workspaces={window.qaInitial.workspaces} onSelect={item=>{qa.selected.push(item.id);setW(item)}} onOpen={()=>qa.open++}/>} center={<Center snapshot={snapshot()} onOpenTask={()=>{}} onOpenAll={()=>{}}/>}/><textarea id="original-draft">Original unsent draft 原创未发送草稿</textarea></div>,document.getElementById('root'));`);
try{const result=await esbuild.build({entryPoints:[entry],outfile:resolve(out,'fixture.js'),bundle:true,platform:'browser',format:'iife',conditions:['browser'],nodePaths:[resolve(app,'node_modules')],plugins:[plugin],loader:{'.svg':'dataurl'},define:{'process.env.NODE_ENV':'"production"'},metafile:true,logLevel:'silent'});for(const name of Object.keys(result.metafile.inputs)){const p=resolve(name);if(!used.has(p))used.set(p,pin(p));}}finally{esbuild.stop();}
writeFileSync(resolve(out,'INPUTS.json'),JSON.stringify({boundary:'Actual titlebar/workspace/supervisor components, original synthetic data; production compiled CSS served separately; no App/API/model/Windows claim.',inputs:[...used.values()],artifact:pin(resolve(out,'fixture.js'))},null,2)+'\n');
