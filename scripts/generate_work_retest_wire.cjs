'use strict';
// Author shared source only; no App, transport, validator invocation or package.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..'),ts=require(path.join(root,'apps/agent-shell/node_modules/typescript'));
const inputs=['apps/agent-shell/src/original-work-retests.ts','apps/agent-shell/src/work-retest-editor.ts','apps/agent-shell/src/original-work-maintenance.ts'];
const texts=inputs.map(p=>fs.readFileSync(path.join(root,p),'utf8')),sources=texts.map((v,i)=>ts.createSourceFile(inputs[i],v,ts.ScriptTarget.Latest,true,ts.ScriptKind.TS));
const name=n=>n.name?.text??(ts.isVariableStatement(n)?n.declarationList.declarations[0].name.text:null);
function pick(source,names){return names.map(key=>{const n=source.statements.find(n=>name(n)===key);if(!n)throw Error('Missing shared retest source: '+key);return n.getText(source).replace(/^export\s+/,'');}).join('\n');}
const readers=['same','text','ids','check','object','retestCommand','retestPrepareBody','retestRequest','readFlags','retestText','basis','validateRetestPreparation','validateRetestInspection','OriginalWorkRetestsApi'];
const wire=`const caseWire=require('./work-case-wire.cjs');const {canonical,hash,workRequirementScope,mappingKey:workCaseMappingKey}=caseWire;
const {validateMaintenanceContext}=require('./work-maintenance-wire.cjs');
const api=Object.freeze({request(){throw Error('WORK_RETEST_TRANSPORT_REQUIRED');}});
function validateWorkCaseSource(v,generation){if(!caseWire.validSource(v,generation))throw Error('WORK_RETEST_CHANGED');}
${pick(sources[0],readers)}
module.exports={canonical,hash,same,workRequirementScope,retestCommand,retestPrepareBody,retestRequest,retestText,validateRetestPreparation,validateRetestInspection,OriginalWorkRetestsApi};`;
const coreNames=['same','check','ownerOf','retestLocalKey','owned','labels','draft','attempt','validateRetestLocal','retestUnresolved','dirty','flushWorkRetestDrafts','RetestJournal','RetestActions'];
const core=`const {webcrypto:crypto}=require('node:crypto');const wire=require('./work-retest-wire.cjs');
const {canonical,hash,workRequirementScope,OriginalWorkRetestsApi,retestPrepareBody,retestRequest}=wire;
${pick(sources[1],coreNames)}
module.exports={RetestJournal,RetestActions,retestLocalKey,validateRetestLocal,retestUnresolved,flushWorkRetestDrafts};`;
function emit(text,name){const result=ts.transpileModule(text,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS},reportDiagnostics:true});if((result.diagnostics||[]).some(d=>d.category===ts.DiagnosticCategory.Error))throw Error('RETEST_SOURCE_TRANSFORM_FAILED');const output="'use strict';\n// Generated from desktop source authority; do not edit independently.\n"+inputs.map((p,i)=>'// SHA256 '+p+' '+crypto.createHash('sha256').update(texts[i]).digest('hex')+'\n').join('')+result.outputText,target=path.join(root,'plugins/vscode-companion',name);if(process.argv.includes('--check')){if(fs.readFileSync(target,'utf8')!==output)throw Error('RETEST_SOURCE_CHANGED: '+name);}else fs.writeFileSync(target,output);}
emit(wire,'work-retest-wire.cjs');emit(core,'work-retest-core.cjs');
console.log(JSON.stringify({kind:'RETEST_VALIDATOR_AND_DURABLE_CORE_SOURCE_TRANSFORM',check_only:process.argv.includes('--check'),outputs:2,app_constructed:false,business_dispatches:0}));
