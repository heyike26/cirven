'use strict';
// Source authoring only: erase types from selected pure renderer validators.
// Does not construct an App, dispatch an API or execute the selected functions.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..'),ts=require(path.join(root,'apps/agent-shell/node_modules/typescript'));
const readerPath='apps/agent-shell/src/original-work-requirements.ts',editorPath='apps/agent-shell/src/work-requirement-editor.ts';
const readerText=fs.readFileSync(path.join(root,readerPath),'utf8'),editorText=fs.readFileSync(path.join(root,editorPath),'utf8');
const parse=(text,name)=>ts.createSourceFile(name,text,ts.ScriptTarget.Latest,true,ts.ScriptKind.TS);
const reader=parse(readerText,readerPath),editor=parse(editorText,editorPath);
function name(node){return node.name?.text??(ts.isVariableStatement(node)?node.declarationList.declarations[0].name.text:null);}
function pick(source,text,names){return names.map(key=>{const node=source.statements.find(n=>name(n)===key);if(!node)throw Error('Missing pure validator: '+key);return node.getText(source).replace(/^export\s+/,'');}).join('\n');}
const klass=reader.statements.find(n=>n.name?.text==='OriginalWorkRequirementsApi'),method=klass.members.find(n=>n.name?.text==='inspect');
if(!method||method.body.statements[1].declarationList?.declarations[0].name.text!=='value')throw Error('Reader shape changed; audit generator before updating');
const validation=method.body.statements.slice(2).map(n=>n.getText(reader)).join('\n');
const base=pick(reader,readerText,['same','check','sha','id','key','count','text','reference','keys','subset','sources','validRequirementSourceAuthority','parseRequirementLink']);
const selected=pick(editor,editorText,['sha','id','count','object','exact','check','reference','ids','choice','scope','request','draft','verifyReceipt','workRequirementDraft','workRequirementDraftMatches','workRequirementCanSave','workRequirementUnchanged']);
const synthetic=`const {createHash,webcrypto:crypto}=require('node:crypto');
const canonical=v=>JSON.stringify(v,(_k,x)=>x!==null&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,x[k]])):x);
const hash=v=>createHash('sha256').update(canonical(v)).digest('hex');
${base}
async function validateView(value,session,root,request){
 const {include_catalog,catalog_offset,selected_requirement_ref,detail_kind,detail_id,selected_source_authority,...scope}=request;
 const options={selected:selected_requirement_ref,sourceAuthority:selected_source_authority,detail:detail_kind===null?undefined:{kind:detail_kind,id:detail_id}};
 ${validation}
}
const selection=(()=>{const same=(a,b)=>canonical(a)===canonical(b),copy=v=>structuredClone(v);
${selected}
return {draftFor:workRequirementDraft,matches:workRequirementDraftMatches,canSave:workRequirementCanSave,unchanged:workRequirementUnchanged,validChoice:choice,validRequest:request,validDraft:draft,verifyReceipt};})();
module.exports={canonical,hash,same,validateView,parseRequirementLink,...selection};`;
const result=ts.transpileModule(synthetic,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,removeComments:false},reportDiagnostics:true});
const errors=(result.diagnostics||[]).filter(d=>d.category===ts.DiagnosticCategory.Error);if(errors.length)throw Error(ts.formatDiagnostics(errors,{getCurrentDirectory:()=>root,getCanonicalFileName:x=>x,getNewLine:()=> '\n'}));
const output="'use strict';\n// Generated pure validators; edit the renderer authority, then run this source generator.\n// SHA256 "+readerPath+' '+crypto.createHash('sha256').update(readerText).digest('hex')+'\n// SHA256 '+editorPath+' '+crypto.createHash('sha256').update(editorText).digest('hex')+'\n'+result.outputText;
const target=path.join(root,'plugins/vscode-companion/work-requirement-wire.cjs');
if(process.argv.includes('--check')){if(fs.readFileSync(target,'utf8')!==output)throw Error('WORK_REQUIREMENT_WIRE_SOURCE_CHANGED');}
else fs.writeFileSync(target,output);
console.log(JSON.stringify({kind:'PURE_VALIDATOR_SOURCE_TRANSFORM',check_only:process.argv.includes('--check'),app_constructed:false,business_dispatches:0}));
