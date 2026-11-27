'use strict';
// Source authoring only. No App, transport, business validator invocation or archive build.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..'),ts=require(path.join(root,'apps/agent-shell/node_modules/typescript'));
const inputs=['apps/agent-shell/src/original-work-test-cases.ts','apps/agent-shell/src/work-case-editor.ts','apps/agent-shell/src/original-work-requirements.ts','apps/agent-shell/src/original-session-files.ts'];
const texts=inputs.map(p=>fs.readFileSync(path.join(root,p),'utf8'));
const sources=texts.map((text,i)=>ts.createSourceFile(inputs[i],text,ts.ScriptTarget.Latest,true,ts.ScriptKind.TS));
const name=n=>n.name?.text??(ts.isVariableStatement(n)?n.declarationList.declarations[0].name.text:null);
function pick(source,names){return names.map(key=>{const node=source.statements.find(n=>name(n)===key);if(!node)throw Error('Missing pure validator: '+key);return node.getText(source).replace(/^export\s+/,'');}).join('\n');}
const klass=sources[0].statements.find(n=>n.name?.text==='OriginalWorkTestCasesApi'),method=klass.members.find(n=>n.name?.text==='inspect');
if(!method||method.body.statements[0].declarationList?.declarations.map(n=>n.name.text).join(',')!=='scope,request,v')throw Error('Case reader changed; audit generator');
const validation=method.body.statements.slice(1).map(n=>n.getText(sources[0])).join('\n');
const base=pick(sources[0],['same','ref','ids','requirementStatuses','check','flags','source','commandFacts','caseIdentity','workCaseSourceFromReport','workCaseMappingKey','receipt']);
const selected=pick(sources[1],['sha','object','check','ids','reference','source','choice','scope','request','draft','basicReceipt','workCaseDraft','workCaseDraftMatches','workCaseCanSave','workCaseUnchanged']);
const scopeSource=pick(sources[3],['sha','positive','ref','originalFileScope']);
const requirementScope=pick(sources[2],['workRequirementScope']);
const readerClass=pick(sources[0],['OriginalWorkTestCasesApi']);
const synthetic=`const {createHash}=require('node:crypto');
const canonical=v=>JSON.stringify(v,(_k,x)=>x!==null&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,x[k]])):x);
const hash=v=>createHash('sha256').update(canonical(v)).digest('hex');
${base}
const originalFileScope=(()=>{${scopeSource};return originalFileScope;})();
${requirementScope}
const api=Object.freeze({request(){throw Error('WORK_CASE_TRANSPORT_REQUIRED');}});
${readerClass}
const validateWorkCaseSource=source;
async function validateView(v,session,root,request){const {offset,case_source,selected_requirement_link_operation_id,mapping_key,...scope}=request;const options={selectedLink:selected_requirement_link_operation_id};${validation}}
const selection=(()=>{const same=(a,b)=>canonical(a)===canonical(b),copy=v=>structuredClone(v);
${selected}
return {validSource:source,validChoice:choice,validRequest:request,validDraft:draft,draftFor:workCaseDraft,matches:workCaseDraftMatches,canSave:workCaseCanSave,unchanged:workCaseUnchanged,basicReceipt};})();
async function verifyReceipt(v,r,sum,owner){if(!selection.validRequest(r,owner)||hash(r)!==sum||Buffer.byteLength(JSON.stringify(v))>32768)throw Error('WORK_CASE_RECEIPT_CHANGED');await receipt(v,{project_id:owner.project_id},r,r.root_sha256);selection.basicReceipt(v,r,sum,owner);return structuredClone(v);}
module.exports={canonical,hash,same,validateView,verifyReceipt,validateReceipt:receipt,caseSourceFromReport:workCaseSourceFromReport,mappingKey:workCaseMappingKey,workRequirementScope,OriginalWorkTestCasesApi,...selection};`;
const coreNames=['PREFIX','sha','object','check','ids','reference','source','choice','scope','request','draft','basicReceipt','validLabel','attempt','disposition','confirmed','parse','ownerOf','workCaseLocalKey','scoped','workCaseDraft','workCaseDraftMatches','workCaseCanSave','workCasePending','dirty','flushWorkCaseDrafts','WorkCaseJournal','WorkCaseActions','workCaseUnchanged'];
const coreSource=`const {webcrypto:crypto,randomUUID:operationId}=require('node:crypto');
const wire=require('./work-case-wire.cjs');
const {canonical,hash,workRequirementScope,OriginalWorkTestCasesApi,validateReceipt:validateWorkCaseReceipt,mappingKey:workCaseMappingKey}=wire;
const api=Object.freeze({request(){throw Error('WORK_CASE_TRANSPORT_REQUIRED');}});
function validateWorkCaseSource(v,generation){if(!wire.validSource(v,generation))throw Error('ORIGINAL_WORK_CASE_MAPPING_CHANGED');}
${pick(sources[1],coreNames)}
module.exports={WorkCaseJournal,WorkCaseActions,workCaseLocalKey,workCaseDraft,workCaseDraftMatches,workCaseCanSave,workCasePending,workCaseUnchanged,flushWorkCaseDrafts,validateLocal:parse};`;
function emit(source,targetName){
 const result=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS},reportDiagnostics:true});
 if((result.diagnostics||[]).some(d=>d.category===ts.DiagnosticCategory.Error))throw Error('WORK_CASE_WIRE_TRANSFORM_FAILED');
 const output="'use strict';\n// Generated from renderer authority; edit that source and run the source generator.\n"+inputs.map((p,i)=>'// SHA256 '+p+' '+crypto.createHash('sha256').update(texts[i]).digest('hex')+'\n').join('')+result.outputText;
 const target=path.join(root,'plugins/vscode-companion',targetName);
 if(process.argv.includes('--check')){if(fs.readFileSync(target,'utf8')!==output)throw Error('WORK_CASE_WIRE_SOURCE_CHANGED: '+targetName);}else fs.writeFileSync(target,output);
}
emit(synthetic,'work-case-wire.cjs');emit(coreSource,'work-case-core.cjs');
console.log(JSON.stringify({kind:'SOURCE_TRANSFORM_SHARED_VALIDATORS_AND_DURABLE_JOURNAL',check_only:process.argv.includes('--check'),outputs:2,app_constructed:false,business_dispatches:0}));
