import {baseline as readBaseline,evidenceDirectory} from './historical-renderer-evidence.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,writeFileSync} from 'node:fs';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
const app=resolve(dirname(fileURLToPath(import.meta.url)),'..'),req=createRequire(resolve(app,'package.json'));
const ts=req('typescript'),babel=req('@babel/core'),esbuild=req('esbuild');
const preset=(await import(resolve(app,'node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js'))).default;
const pin=path=>{const raw=readFileSync(path);return{path,bytes:raw.length,sha256:createHash('sha256').update(raw).digest('hex')};};
test('compile actual manual review/controller/ObjectDraft and original Button into isolated browser fixtures',async()=>{
 const out=evidenceDirectory('manual-review-browser-source61'),baseline=readBaseline('manual','EEAS_MANUAL_BASELINE');
 try{for(const variant of ['before','after']){
  const used=new Map(),manual=resolve(app,'src/ManualChangesReview.tsx'),before=resolve(baseline,'apps/agent-shell/src/ManualChangesReview.tsx');
  const plugin={name:'source61-actual-manual-dom',setup(build){build.onLoad({filter:/\.tsx$/},async({path})=>{const actual=variant==='before'&&path===manual?before:path;used.set(actual,pin(actual));return{contents:babel.transformSync(ts.transpileModule(readFileSync(actual,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText,{filename:path+'.jsx',presets:[[preset,{generate:'dom',hydratable:false}]],configFile:false,babelrc:false}).code,loader:'js',resolveDir:dirname(path)};});}};
  const program=`import {render} from 'solid-js/web';import {createSignal,createMemo,onCleanup,Show} from 'solid-js';
import {ObjectDraftBoundary,draftSignal,DraftStatus,useDraftSession} from '${resolve(app,'src/ObjectDraft.tsx')}';
import {createManualChanges,ManualChangesSummary,ManualChangesNode} from '${manual}';
import {ApiError} from '${resolve(app,'src/api.ts')}';import {workbenchApi} from '${resolve(app,'src/workbench-api.ts')}';
import {startUILanguage} from '${resolve(app,'src/ui-language.ts')}';import {stableItems} from '${resolve(app,'src/stable-items.ts')}';
const data=window.qaManualData,proposal=data.proposal,artifact={type:'proposal',artifactId:proposal.id,revision:proposal.revision,contentHash:proposal.sha256,schemaVersion:1,scope:{projectId:proposal.project_id},data:proposal};
function Review(){onCleanup(startUILanguage());const owner=useDraftSession(),[candidate,setCandidate]=draftSignal('candidate',structuredClone(proposal.payload.candidate)),[reason,setReason]=draftSignal('changeReason',''),[selected,setSelected]=createSignal('C');
 const renderedNodes=stableItems(()=>candidate().nodes,value=>value.id),node=createMemo(()=>renderedNodes().find(value=>value.id===selected()));
 const context={open(){throw Error('No navigation/business save authorized in component fixture');},dispatch(id,target,request){if(id!=='proposal.manual.capture')throw Error('Unexpected business action '+id);return workbenchApi.captureManual(proposal.project_id,proposal.id,request);}};
 const controller=createManualChanges({artifact,context,proposal:()=>proposal,candidate,setCandidate,reason,onInspectNode:setSelected,onReturnToExport:()=>{}});
 window.fixture={state:()=>{owner.version();return{status:owner.session.status,error:owner.session.error,fields:structuredClone(owner.session.fields),view:structuredClone(owner.session.view),capture:controller.capture()?structuredClone(controller.capture()):null};},finish:()=>owner.session.finish(),offerGate:gate=>controller.receiveExportError(new ApiError(409,'MANUAL_REVIEW_REQUIRED','Original controlled fixture',{detail:{manual_changes:gate}}),controller.lease())};
 return <><DraftStatus/><main id="reader" onScroll={e=>owner.session.set('view','scroll',{x:e.currentTarget.scrollLeft,y:e.currentTarget.scrollTop})}><div style={{height:'84px'}}>原创同一捕获的人工改文核对；没有模型或项目执行。</div><ManualChangesSummary controller={controller}/><Show when={node()} keyed>{value=><ManualChangesNode controller={controller} nodeId={value.id}/>}</Show><div style={{height:'650px'}}>阅读末端；这是隔离组件夹具。</div></main></>;}
render(()=> <ObjectDraftBoundary artifact={artifact}><Review/></ObjectDraftBoundary>,document.getElementById('root'));`;
  const entry=resolve(out,variant+'.tsx');writeFileSync(entry,program);const result=await esbuild.build({entryPoints:[entry],outfile:resolve(out,variant+'.js'),bundle:true,platform:'browser',format:'iife',conditions:['browser'],nodePaths:[resolve(app,'node_modules')],plugins:[plugin],loader:{'.css':'empty'},define:{'process.env.NODE_ENV':'"production"'},logLevel:'silent',metafile:true});
  for(const name of Object.keys(result.metafile.inputs)){const p=resolve(process.cwd(),name);if(!used.has(p))used.set(p,pin(p));}
  writeFileSync(resolve(out,variant+'-inputs.json'),JSON.stringify({variant,manual_source:pin(variant==='before'?before:manual),entry:pin(entry),bundle:pin(resolve(out,variant+'.js')),inputs:[...used.values()],boundaries:'Actual component/controller/ObjectDraft/ApiClient/Kobalte Button; intercepted original HTTP fixture. No App, Vite, model or native qualification.'},null,2));
 }}finally{esbuild.stop();}
});
