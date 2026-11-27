'use strict';
// Source transform only. Does not construct an App, store or transport.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..'),ts=require(path.join(root,'apps/agent-shell/node_modules/typescript'));
const inputs=['apps/agent-shell/src/original-work-requirement-sources.ts','apps/agent-shell/src/work-source-editor.ts'],texts=inputs.map(p=>fs.readFileSync(path.join(root,p),'utf8')),sources=texts.map((v,i)=>ts.createSourceFile(inputs[i],v,ts.ScriptTarget.Latest,true,ts.ScriptKind.TS));
const name=n=>n.name?.text??(ts.isVariableStatement(n)?n.declarationList.declarations[0].name.text:null);
function pick(source,names){return names.map(key=>{const n=source.statements.find(n=>name(n)===key);if(!n)throw Error('Missing shared source declaration: '+key);return n.getText(source).replace(/^export\s+/,'');}).join('\n');}
const wire=`const {canonical,hash,workRequirementScope}=require('./work-case-wire.cjs');
const api=Object.freeze({request(){throw Error('WORK_SOURCE_TRANSPORT_REQUIRED');}});
${pick(sources[0],['sourceSame','sourceSha','id','check','exact','ids','ref','scope','sourceQuery','sourceDecision','flags','selection','validateSourceReceipt','sourceAuthorityRef','validateSourceReview','OriginalWorkRequirementSourcesApi'])}
module.exports={canonical,hash,workRequirementScope,sourceSame,sourceSha,sourceQuery,sourceDecision,validateSourceReceipt,sourceAuthorityRef,validateSourceReview,OriginalWorkRequirementSourcesApi};`;
const core=`const {webcrypto:crypto}=require('node:crypto');const wire=require('./work-source-wire.cjs');
const {canonical,hash,workRequirementScope,OriginalWorkRequirementSourcesApi,sourceQuery,sourceDecision,validateSourceReceipt,sourceSame:same,sourceSha:sha}=wire;
${pick(sources[1],['copy','check','exact','ownerOf','sourceLocalKey','owned','draft','attempt','validateSourceLocal','dirty','flushWorkSourceDrafts','SourceJournal','SourceActions'])}
module.exports={SourceJournal,SourceActions,sourceLocalKey,validateSourceLocal,flushWorkSourceDrafts};`;
function emit(text,name){const result=ts.transpileModule(text,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS},reportDiagnostics:true});if((result.diagnostics||[]).some(d=>d.category===ts.DiagnosticCategory.Error))throw Error('WORK_SOURCE_TRANSFORM_FAILED');const output="'use strict';\n// Generated from desktop authority; do not edit independently.\n"+inputs.map((p,i)=>'// SHA256 '+p+' '+crypto.createHash('sha256').update(texts[i]).digest('hex')+'\n').join('')+result.outputText,target=path.join(root,'plugins/vscode-companion',name);if(process.argv.includes('--check')){if(fs.readFileSync(target,'utf8')!==output)throw Error('WORK_SOURCE_CHANGED: '+name);}else fs.writeFileSync(target,output);}
emit(wire,'work-source-wire.cjs');emit(core,'work-source-core.cjs');
console.log(JSON.stringify({kind:'SOURCE_VALIDATOR_AND_DURABLE_CORE_SOURCE_TRANSFORM',outputs:2,check_only:process.argv.includes('--check'),app_constructed:false,business_dispatches:0}));
