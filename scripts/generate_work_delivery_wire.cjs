'use strict';
// Source transformation only: no validator, journal, transport, App or package invocation.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..'),ts=require(path.join(root,'apps/agent-shell/node_modules/typescript'));
const inputs=['apps/agent-shell/src/original-work-deliveries.ts','apps/agent-shell/src/work-delivery-editor.ts','apps/agent-shell/src/original-work-roots.ts','apps/agent-shell/src/original-session-files.ts','apps/agent-shell/src/api.ts'];
const texts=inputs.map(p=>fs.readFileSync(path.join(root,p),'utf8')),sources=texts.map((v,i)=>ts.createSourceFile(inputs[i],v,ts.ScriptTarget.Latest,true,ts.ScriptKind.TS));
const runtime=n=>ts.isVariableStatement(n)||ts.isFunctionDeclaration(n)||ts.isClassDeclaration(n);
const name=n=>n.name?.text??(ts.isVariableStatement(n)?n.declarationList.declarations[0].name.text:null);
function pick(i,names){return names.map(key=>{const n=sources[i].statements.find(n=>name(n)===key);if(!n)throw Error('Missing delivery authority: '+key);return n.getText(sources[i]).replace(/^export\s+/,'');}).join('\n');}
const all=i=>sources[i].statements.filter(runtime).map(n=>name(n));
const base=`const {webcrypto:crypto}=require('node:crypto');const {canonical,hash,workRequirementScope}=require('./work-case-wire.cjs');
const api=Object.freeze({request(){throw Error('WORK_DELIVERY_TRANSPORT_REQUIRED');},bytes(){throw Error('WORK_DELIVERY_TRANSPORT_REQUIRED');}});`;
const rootSource=`const roots=(()=>{const originalFileScope=(()=>{${pick(3,['sha','positive','ref','originalFileScope'])};return originalFileScope;})();
${pick(2,all(2))}
return {workRootScope,validateWorkRoots,OriginalWorkRootsApi};})();`;
const wire=`${base}\n${pick(0,all(0))}\n${rootSource}
module.exports={canonical,hash,same,workRequirementScope,validScope,sha,id,count,text,deliveryArchivePath,validateDeliveryTree,validateDeliveryPrepare,validateDeliveryDraftQuery,validateDeliveryRequest,validateDeliveryReview,validateDeliveryOutcome,validateDeliveries,validateDeliveryRecovery,deliveryDownloadPath,OriginalWorkDeliveriesApi,...roots};`;
const core=`const {webcrypto:crypto}=require('node:crypto');const wire=require('./work-delivery-wire.cjs');
const {canonical,hash,workRequirementScope,OriginalWorkDeliveriesApi,validateDeliveryPrepare,validateDeliveryDraftQuery,validateDeliveryRequest,validateDeliveryReview,validateDeliveryOutcome,validateDeliveryRecovery}=wire;
${pick(4,['ApiError'])}
${pick(1,all(1))}
module.exports={ApiError,DeliveryJournal,DeliveryActions,emptyDeliveryDraft,deliveryUnresolved,deliveryLocalKey,validateDeliveryLocal,deliveryJournal,flushWorkDeliveryDrafts};`;
function emit(source,filename){const result=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS},reportDiagnostics:true});if((result.diagnostics||[]).some(d=>d.category===ts.DiagnosticCategory.Error))throw Error('DELIVERY_SOURCE_TRANSFORM_FAILED');
 const output="'use strict';\n// Generated from desktop source authority; do not edit independently.\n"+inputs.map((p,i)=>'// SHA256 '+p+' '+crypto.createHash('sha256').update(texts[i]).digest('hex')+'\n').join('')+result.outputText,target=path.join(root,'plugins/vscode-companion',filename);
 if(process.argv.includes('--check')){if(fs.readFileSync(target,'utf8')!==output)throw Error('DELIVERY_SOURCE_CHANGED: '+filename);}else fs.writeFileSync(target,output);
}
emit(wire,'work-delivery-wire.cjs');emit(core,'work-delivery-core.cjs');
console.log(JSON.stringify({kind:'DELIVERY_ROOTS_AND_DURABLE_CORE_SOURCE_TRANSFORM',check_only:process.argv.includes('--check'),outputs:2,app_constructed:false,business_dispatches:0}));
