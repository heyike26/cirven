'use strict';
// Source transformation only; no App, validator, port, dialog or archive dispatch.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..'),ts=require(path.join(root,'apps/agent-shell/node_modules/typescript'));
const inputs=['apps/agent-shell/src/original-delivery-exports.ts','apps/agent-shell/src/native-export-journal.ts'],texts=inputs.map(p=>fs.readFileSync(path.join(root,p),'utf8'));
const sources=texts.map((v,i)=>ts.createSourceFile(inputs[i],v,ts.ScriptTarget.Latest,true,ts.ScriptKind.TS));
const pick=i=>sources[i].statements.filter(n=>ts.isVariableStatement(n)||ts.isFunctionDeclaration(n)||ts.isClassDeclaration(n)).map(n=>n.getText(sources[i]).replace(/^export\s+/,'')).join('\n');
const wire=`const {canonical,hash,workRequirementScope}=require('./work-case-wire.cjs');\n${pick(0)}\nmodule.exports={canonical,hash,workRequirementScope,same,id,sha,count,text,validNativeExportScope,validateNativeExportRequest,nativeExportQuery,validateNativeExportOutcome,validateNativeExportHistory,nativeExportTerminal,NativeDeliveryExportsApi};`;
const core=`const {canonical,hash,workRequirementScope}=require('./work-case-wire.cjs');const {NativeDeliveryExportsApi,validateNativeExportRequest,validateNativeExportOutcome,nativeExportTerminal}=require('./native-export-wire.cjs');\n${pick(1)}\nmodule.exports={nativeExportOwner,nativeExportLocalKey,validateNativeExportLocal,flushNativeExportJournals,NativeExportJournal,nativeExportJournal,NativeExportActions};`;
function emit(code,filename){const result=ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS},reportDiagnostics:true});if((result.diagnostics||[]).some(d=>d.category===ts.DiagnosticCategory.Error))throw Error('NATIVE_EXPORT_SOURCE_TRANSFORM_FAILED');
 const output="'use strict';\n// Generated from desktop source authority; do not edit independently.\n"+inputs.map((p,i)=>'// SHA256 '+p+' '+crypto.createHash('sha256').update(texts[i]).digest('hex')+'\n').join('')+result.outputText,target=path.join(root,'plugins/vscode-companion',filename);
 if(process.argv.includes('--check')){if(fs.readFileSync(target,'utf8')!==output)throw Error('NATIVE_EXPORT_SOURCE_CHANGED');}else fs.writeFileSync(target,output);
}
emit(wire,'native-export-wire.cjs');emit(core,'native-export-core.cjs');
console.log(JSON.stringify({kind:'NATIVE_EXPORT_VALIDATOR_JOURNAL_SOURCE_TRANSFORM_ONLY',outputs:2,check_only:process.argv.includes('--check'),business_dispatches:0}));
