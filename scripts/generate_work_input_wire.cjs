'use strict';
// Shared pure input selection source only: no App, draft, RPC or dispatch.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..'),input='apps/agent-shell/src/original-work-input.ts',text=fs.readFileSync(path.join(root,input),'utf8'),ts=require(path.join(root,'apps/agent-shell/node_modules/typescript'));
const source=ts.createSourceFile(input,text,ts.ScriptTarget.Latest,true,ts.ScriptKind.TS),body=source.statements.filter(n=>ts.isVariableStatement(n)||ts.isFunctionDeclaration(n)).map(n=>n.getText(source).replace(/^export\s+/,''));
const code=`const {webcrypto:crypto}=require('node:crypto');const {canonical,hash}=require('./work-case-wire.cjs');\n${body.join('\n')}\nmodule.exports={originalWorkRef,validateOriginalWorkChoice,originalWorkChoiceMatches,sameOriginalWorkChoice,originalWorkChoiceForRef,makeOriginalWorkChoice,originalWorkStatusText,verifyOriginalWorkInputReceipt};`;
const compiled=ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS},reportDiagnostics:true});if((compiled.diagnostics||[]).some(d=>d.category===ts.DiagnosticCategory.Error))throw Error('WORK_INPUT_SOURCE_TRANSFORM_FAILED');
const output="'use strict';\n// Generated from desktop pure source authority; do not edit independently.\n// SHA256 "+input+' '+crypto.createHash('sha256').update(text).digest('hex')+'\n'+compiled.outputText,target=path.join(root,'plugins/vscode-companion/work-input-wire.cjs');
if(process.argv.includes('--check')){if(fs.readFileSync(target,'utf8')!==output)throw Error('WORK_INPUT_SOURCE_CHANGED');}else fs.writeFileSync(target,output);
console.log(JSON.stringify({kind:'PURE_ORIGINAL_WORK_INPUT_SOURCE_TRANSFORM',check_only:process.argv.includes('--check'),outputs:1,business_dispatches:0}));
