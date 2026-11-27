import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import readline from 'node:readline';
import test from 'node:test';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';
import { renderToString } from 'solid-js/web';
import { ApiClient } from '../src/api.ts';
import { NativeFeedbackApi } from '../src/native-feedback-api.ts';
import { NativeReworkApi, ReworkOutcomeError, ReworkPreviewRecovery, assertReworkRecord, declaredMappedContracts, prepareOriginalPreview, readReworkHistory, reworkCanApply, reworkImpact, reworkPreviewQuestion } from '../src/native-rework.ts';
import { sameNativeReference } from '../src/native-collaboration.ts';

import {configurationHash as reworkHash} from '../src/conversation-configuration.ts';
import {normalizedReworkBody} from '../src/native-rework-index.ts';

// UNIT locks only; production never falls back when Web Locks are absent.
const unitReworkLocks=()=>{const tails=new Map();return {request:(key,_options,run)=>{const p=(tails.get(key)??Promise.resolve()).catch(()=>{}).then(run);tails.set(key,p);return p;}};};

const here=dirname(fileURLToPath(import.meta.url)),app=resolve(here,'..'),root=resolve(app,'../..');
function storage(){const data=new Map();return{getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value),data};}
function fixture(t){
  const child=spawn(resolve(root,'.venv/bin/python'),[resolve(here,'rework-http-fixture.py')],{cwd:root,stdio:['pipe','pipe','pipe']}),waiting=[],calls=[];let stderr='';
  child.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-8000);});const lines=readline.createInterface({input:child.stdout});lines.on('line',line=>{const next=waiting.shift();if(next)try{next.resolve(JSON.parse(line));}catch(error){next.reject(error);}});
  child.on('error',error=>waiting.splice(0).forEach(item=>item.reject(error)));child.on('exit',code=>waiting.splice(0).forEach(item=>item.reject(new Error(`Rework fixture exited ${code}: ${stderr}`))));
  t.after(async()=>{child.stdin.end();await new Promise(resolve=>child.exitCode!==null?resolve():child.once('exit',resolve));lines.close();});
  function raw(body){return new Promise((resolve,reject)=>{waiting.push({resolve,reject});child.stdin.write(JSON.stringify(body)+'\n');});}
  const api=new ApiClient(async(url,options)=>{const method=options?.method??'GET',body=options?.body?JSON.parse(options.body):undefined;calls.push({method,url,body});const reply=await raw({method,path:url,headers:options?.headers??{},body});return new Response(JSON.stringify(reply.body),{status:reply.status});});
  return{raw,calls,api,rework:new NativeReworkApi(api,unitReworkLocks()),feedback:new NativeFeedbackApi(api),local:storage()};
}
async function setup(f){const answer=await f.raw({fixture:'setup'});assert.equal(answer.status,200,JSON.stringify(answer.body));return answer.body;}
async function mapped(f,s,key='mapping'){
  const feedback=await f.feedback.record(s.source,'请修订任务 A，保留人工备注。','SUBMITTED','feedback-'+key),pending=await f.rework.map(f.local,feedback,s.proposal_ref,{locator:{kind:'JSON_POINTER',pointer:'/tasks/a/requirement'}},key+'-resolve');assert.equal(pending.status,'NEEDS_CLARIFICATION');
  const mapping=await f.rework.map(f.local,feedback,s.proposal_ref,{locator:pending.payload.locator,taskIds:['a'],confirmed:true},key+'-confirm');assert.equal(mapping.status,'MAPPED');assert.equal(await f.rework.binding(mapping),'A 原已确认要求');return{feedback,mapping};
}
async function preview(f,mapping,key='preview'){return f.rework.preview(f.local,mapping,{classification_kind:'CONSTRAINT',classification_origin:'USER_STATEMENT',rationale:'用户明确任务 A 使用此实际对象作为输入；其他任务缺少声明条件。',proposed_value:'A 本次用户提议要求',contracts:declaredMappedContracts(mapping,'VALUE_CHANGED','用户声明 A 的输入；未给 B 声明独立性。')},key);}

// Compile the actual Solid TSX for server rendering using installed dependencies.
// These are DOM/controls output checks, not Windows GUI qualification.
let viewPromise;
async function views(t){if(!viewPromise)viewPromise=(async()=>{
 const directory=await mkdtemp(resolve(app,'validation/r3-ui-test-')),file=resolve(directory,'panel.mjs'),source=await readFile(resolve(app,'src/NativeReworkPanel.tsx'),'utf8');
 for(const name of ['NativeConflictEditor','NativePreviewEditor','NativeFeedbackComposer']){const text=await readFile(resolve(app,'src',name+'.tsx'),'utf8'),js=ts.transpileModule(text,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.Preserve}}).outputText.replace(/from ['"]\.\/([^'"]+)['"]/g,(_,value)=>`from '${pathToFileURL(value==='ui-language'?resolve(app,'tests/fixture-ui-language.mjs'):resolve(app,'src',value+'.ts')).href}'`),code=babel.transformSync(js,{filename:name+'.jsx',presets:[[solidPreset,{generate:'ssr',hydratable:false}]],configFile:false,babelrc:false}).code;await writeFile(resolve(directory,name+'.mjs'),code);}
 const js=ts.transpileModule(source,{fileName:'NativeReworkPanel.tsx',compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.Preserve}}).outputText.replace(/from ['"]\.\/([^'"]+)['"]/g,(_,name)=>['NativeConflictEditor','NativePreviewEditor','NativeFeedbackComposer'].includes(name)?`from './${name}.mjs'`:`from '${pathToFileURL(name==='ui-language'?resolve(app,'tests/fixture-ui-language.mjs'):resolve(app,'src',name+'.ts')).href}'`);
 const code=babel.transformSync(js,{filename:'panel.jsx',presets:[[solidPreset,{generate:'ssr',hydratable:false}]],configFile:false,babelrc:false}).code;await writeFile(file,code);return {...await import(pathToFileURL(file).href),directory};})();const result=await viewPromise;t.after(()=>rm(result.directory,{recursive:true,force:true}));return result;}


test('actual API maps only after needed clarification, preserves undeclared task UNKNOWN, and applies the exact file with a clickable real after source',async t=>{
  const f=fixture(t),s=await setup(f),{feedback,mapping}=await mapped(f,s),plan=await preview(f,mapping);assert.equal(plan.status,'PROPOSED');assert.equal(plan.payload.engine_plan.payload.classification.origin,'USER_STATEMENT');assert.deepEqual(reworkImpact(plan).counts,{AFFECTED:1,UNKNOWN:1,RETAINED_BY_DECLARED_CONDITION:0});assert.deepEqual(plan.payload.write_scope,[{workspace_id:s.source.workspace_id,path:s.source.path}]);
  const result=await f.rework.apply(f.local,plan,false,'actual-apply');assert.equal(result.status,'APPLIED');const request=f.calls.find(call=>call.url.endsWith('/apply'));assert.equal(request.body.allow_rebase,false);assert.ok(!Object.hasOwn(request.body,'write_scope'));assert.deepEqual(request.body.expected_current_sources,[s.source]);
  const actual=(await f.raw({fixture:'bytes'})).body;assert.equal(actual.independent,s.independent);assert.equal(actual.actual,s.original.replace('A 原已确认要求','A 本次用户提议要求'));const after=result.payload.engine_receipt.payload.outcomes[0].source_ref;assert.ok(after);assert.notEqual(after.sha256,s.source.sha256);assert.deepEqual((await f.api.file(s.pid,after.workspace_id,after.path)).source_ref,after);assert.equal(result.payload.semantic_correctness_confirmed,false);
  assert.equal(readReworkHistory(f.local,s.pid,feedback.feedback_ref).length,4);const {NativeReworkResult,NativeReworkApplyControls}=await views(t),html=renderToString(()=>NativeReworkResult({record:result,onOpen:()=>{}}));assert.match(html,/打开实际版本 v/);assert.match(html,/任务与harness\.json/);assert.match(html,/影响未知/);
  const controls=renderToString(()=>NativeReworkApplyControls({plan,busy:false,unresolved:false,alreadyApplied:false,approved:false,allowRebase:false,onApproved:()=>{},onRebase:()=>{},onApply:()=>{}}));assert.match(controls,/应用限定变更/);assert.match(controls,/<button[^>]*disabled/);assert.ok(!controls.includes('checked'));
});

test('actual outside-object human edits produce a real 409 conflict with default rebase false; explicit scoped rebase keeps human bytes',async t=>{
  const f=fixture(t),s=await setup(f),{mapping}=await mapped(f,s),plan=await preview(f,mapping),human=s.original.replace('原人工备注','人工新增😀必须保留');await f.raw({fixture:'tamper',text:human});
  const conflict=await f.rework.apply(f.local,plan,false,'conflict');assert.equal(conflict.status,'CONFLICT');assert.equal((await f.raw({fixture:'bytes'})).body.actual,human);assert.equal(f.calls.filter(call=>call.url.endsWith('/apply')).length,1);assert.equal(f.calls.filter(call=>call.url.endsWith('/operations/conflict')).length,1);
  const applied=await f.rework.apply(f.local,plan,true,'explicit-rebase');assert.equal(applied.status,'APPLIED');const bytes=(await f.raw({fixture:'bytes'})).body;assert.equal(bytes.actual,human.replace('A 原已确认要求','A 本次用户提议要求'));assert.equal(bytes.independent,s.independent);assert.equal(f.calls.filter(call=>call.url.endsWith('/apply')).length,2);
  const {NativeReworkResult}=await views(t),html=renderToString(()=>NativeReworkResult({record:conflict}));assert.match(html,/文件冲突，保留人工内容/);assert.ok(!html.includes('打开实际版本'));
});

test('actual confirmed object mappings reuse automatically for new feedback without another task confirmation; binary feedback never gains apply controls',async t=>{
  const f=fixture(t),s=await setup(f),{mapping}=await mapped(f,s),feedback=await f.feedback.record(s.source,'再核对同一字段。','SUBMITTED','next-feedback'),reused=await f.rework.map(f.local,feedback,s.proposal_ref,{locator:mapping.payload.locator},'reuse');assert.equal(reused.status,'MAPPED');assert.equal(reused.payload.mapping_basis,'REUSED_CONFIRMED_OBJECT_MAPPING');assert.equal(reused.payload.repeated_mapping_confirmation_required,false);assert.deepEqual(reused.payload.reused_mapping_ref,mapping.record_ref);assert.equal(f.calls.find(call=>call.body?.operation_id==='reuse').body.user_confirmed,false);
  const binary=await f.feedback.record(s.binary_source,'图片批注不能伪称已经修改。','DRAFT','binary-feedback'),record=await f.rework.map(f.local,binary,s.proposal_ref,{},'binary-map');assert.equal(record.status,'NOT_ADAPTED');assert.equal(reworkCanApply(record),false);assert.throws(()=>f.rework.preview(f.local,record,{},'binary-plan'),/先核对/);
  const {NativeReworkResult,NativeReworkApplyControls}=await views(t),html=renderToString(()=>NativeReworkResult({record}));assert.match(html,/此格式尚未适配修改/);const controls=renderToString(()=>NativeReworkApplyControls({plan:record,busy:false,unresolved:false,alreadyApplied:false,approved:true,allowRebase:false,onApproved:()=>{},onRebase:()=>{},onApply:()=>{}}));assert.ok(!controls.includes('应用限定变更'));
});

test('actual restart restores persisted NativeMessages-style mapping/plan refs by GET only, deduplicates and preserves the original feedback versions',async t=>{
  const f=fixture(t),s=await setup(f),{mapping,feedback}=await mapped(f,s),plan=await preview(f,mapping);await f.raw({fixture:'restart'});const restored=[],recovery=new ReworkPreviewRecovery(f.rework,f.feedback,(record,previews)=>restored.push({record,previews}));const start=f.calls.length;
  await recovery.restore(s.pid,[mapping,plan,plan]);await recovery.restore(s.pid,[mapping,plan]);assert.equal(restored.length,1);assert.deepEqual(restored[0].record.feedback_ref,feedback.feedback_ref);assert.deepEqual(restored[0].previews.map(r=>r.record_ref),[mapping.record_ref,plan.record_ref]);assert.ok(f.calls.slice(start).every(call=>call.method==='GET'));assert.equal(f.calls.slice(start).filter(call=>call.url.includes('/plans/')).length,1);assert.equal(reworkCanApply(restored[0].previews[1]),true);recovery.stop();
  const history=readReworkHistory(f.local,s.pid,feedback.feedback_ref),before=f.calls.length;for(const op of history)await f.rework.inspect(op);assert.ok(f.calls.slice(before).every(call=>call.method==='GET'));
});

test('restoring a saved apply loads its exact manual preview even when the preview was stored in a separate draft journal',async t=>{
 const f=fixture(t),s=await setup(f),{feedback,mapping}=await mapped(f,s);
 const packet=prepareOriginalPreview(mapping,{
  operation_id:'manual-draft-preview',mapping_ref:mapping.record_ref,classification_kind:'CONSTRAINT',
  classification_origin:'USER_STATEMENT',rationale:'任务 A 使用该字段，按用户声明的值检查。',
  proposed_value_text:JSON.stringify('A 保存的手动预览'),
  contracts:declaredMappedContracts(mapping,'VALUE_CHANGED','用户确认 A 的输入字段'),
 });
 const plan=await f.rework.postOriginalPreview(packet);
 const applied=await f.rework.apply(f.local,plan,false,'apply-manual-preview');
 assert.equal(applied.status,'APPLIED');
 const history=readReworkHistory(f.local,s.pid,feedback.feedback_ref);
 assert.equal(history.some(op=>op.kind==='PLAN'),false);
 const operation=history.find(op=>op.kind==='APPLY');
 assert.ok(operation);
 await f.raw({fixture:'restart'});
 const source=ts.createSourceFile('NativeReworkPanel.tsx',await readFile(resolve(app,'src/NativeReworkPanel.tsx'),'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
 const panel=source.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='NativeReworkPanel');
 const inspect=panel.body.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='inspect');
 const compiled=ts.transpileModule(inspect.getText(source),{compilerOptions:{target:99,module:99}}).outputText;
 const records=[],selection=[];
 const context={
  client:()=>f.rework,controller:new AbortController(),alive:true,seedGeneration:0,
  setReviews:()=>{},props:{feedback},records:()=>records,plan:()=>selection.at(-1),
  sameNativeReference,accept:(row,select)=>{records.push(row);if(select&&row.kind==='PLAN')selection.push(row);},
 };
 const restore=new Function(...Object.keys(context),compiled+'\nreturn inspect;')(...Object.values(context));
 const start=f.calls.length;
 await restore(operation);
 assert.deepEqual(records.map(row=>row.record_ref),[applied.record_ref,plan.record_ref]);
 assert.deepEqual(selection.map(row=>row.record_ref),[plan.record_ref]);
 assert.deepEqual(f.calls.slice(start).map(({method,url})=>[method,url.split('/').slice(-2)]),[
  ['GET',['operations',operation.operationId]],['GET',['plans',plan.record_ref.id]],
 ]);
});

test('actual proposal revision change blocks apply before any write and renders the historical preview as stale',async t=>{
  const f=fixture(t),s=await setup(f),{mapping}=await mapped(f,s),plan=await preview(f,mapping),proposal=await f.api.proposal(s.pid,s.proposal_ref.id);await f.api.editProposal(proposal,{...proposal.payload.candidate,summary:'用户改变已确认任务依据，旧预览不能继续当前应用。'},'用户明确修改','changed-basis');
  const old=await f.rework.get(s.pid,'PLAN',plan.record_ref);assert.equal(old.current_basis_check.status,'STALE_REVIEW_REQUIRED');const before=f.calls.length;await assert.rejects(()=>f.rework.apply(f.local,plan,false,'must-not-apply'),/依据已变/);assert.equal(f.calls.slice(before).filter(call=>call.method==='POST').length,0);assert.equal((await f.raw({fixture:'bytes'})).body.actual,s.original);
  const {NativeReworkResult,NativeReworkApplyControls}=await views(t);assert.match(renderToString(()=>NativeReworkResult({record:old})),/原任务或需求已变化/);const controls=renderToString(()=>NativeReworkApplyControls({plan:old,busy:false,unresolved:false,alreadyApplied:false,approved:true,allowRebase:false,onApproved:()=>{},onRebase:()=>{},onApply:()=>{}}));assert.ok(!controls.includes('应用限定变更'));
});

test('actual read-only file binding can prepare an explicitly referenced original-Agent question but never grants R3 writes from an apply checkbox',async t=>{
  const f=fixture(t),s=await setup(f),workspace=await f.api.bindWorkspace(s.pid,s.selected_directory,false,'readonly-selected'),file=await f.api.file(s.pid,workspace.id,s.source.path),readonly={...s,source:file.source_ref},{feedback,mapping}=await mapped(f,readonly,'readonly'),plan=await preview(f,mapping,'readonly-preview');
  const human=s.original.replace('原人工备注','仍然保留的人工新备注');await f.raw({fixture:'tamper',text:human});const current=await f.rework.previewSource(feedback);assert.equal(current.workspace_id,workspace.id);assert.notEqual(current.sha256,feedback.payload.baseline_file_source_ref.sha256);const question=reworkPreviewQuestion(feedback,s.proposal_ref,mapping);assert.ok(question.includes(JSON.stringify(feedback.feedback_ref)));assert.ok(question.includes(JSON.stringify(mapping.record_ref)));assert.match(question,/不应用或写入/);assert.match(question,/影响保持未知/);assert.ok(question.length<16000);
  const before=f.calls.length;await assert.rejects(()=>f.rework.apply(f.local,plan,true,'never-authorized'),/没有限定文件的写入许可/);assert.ok(f.calls.slice(before).every(call=>call.method==='GET'));assert.ok(!readReworkHistory(f.local,s.pid,feedback.feedback_ref).some(value=>value.operationId==='never-authorized'));assert.equal((await f.raw({fixture:'bytes'})).body.actual,human);assert.equal((await f.raw({fixture:'bytes'})).body.independent,s.independent);
});

const ref={id:'actual',revision:1,sha256:'a'.repeat(64)},other={...ref,id:'other'},source={kind:'local_file',project_id:'p',workspace_id:'w',binding_revision:1,binding_sha256:'b'.repeat(64),path:'exact.json',revision:1,sha256:'c'.repeat(64)};
const feedback={feedback_ref:ref,payload:{project_id:'p',baseline_file_source_ref:source,comment:'user',mode:'DRAFT'},current_file_check:{status:'CURRENT'},direct_action_links:[]};
const record={record_ref:ref,mapping_ref:ref,kind:'MAPPING',status:'NEEDS_CLARIFICATION',payload:{id:ref.id,revision:1,kind:'MAPPING',status:'NEEDS_CLARIFICATION',project_id:'p',native_feedback_ref:ref,proposal_ref:other,baseline_file_source_ref:source},current_basis_check:{status:'CURRENT_TASK_REQUIREMENT_BASIS',semantic_correctness_confirmed:false}};

test('mutation indexes are persisted before dispatch; storage failure sends nothing and cached operation identities only inspect, never POST again',async()=>{
  const local=storage(),calls=[];let saved=record;const api=new ApiClient(async(url,options)=>{calls.push({url,method:options.method});if(url==='/api/bootstrap')return new Response(JSON.stringify({csrf_token:'token'}));if(options.method==='POST'){assert.equal(readReworkHistory(local,'p',ref)[0].operationId,'indexed');const body=JSON.parse(options.body);saved=structuredClone(record);saved.payload.request_sha256=await reworkHash({project_id:'p',operation_id:body.operation_id,kind:'MAPPING',input:normalizedReworkBody('MAPPING',body)});}return new Response(JSON.stringify(saved));}),client=new NativeReworkApi(api,unitReworkLocks());
  await client.map(local,feedback,other,{},'indexed');await client.map(local,feedback,other,{},'indexed');await assert.rejects(()=>client.map(local,feedback,other,{confirmed:true,taskIds:['changed']},'indexed'));assert.equal(calls.filter(call=>call.method==='POST').length,1);assert.equal(calls.filter(call=>call.url.endsWith('/operations/indexed')).length,1);
  const broken={getItem:()=>null,setItem:()=>{throw new Error('local storage full');}},before=calls.length;await assert.rejects(()=>client.map(broken,feedback,other,{},'never'),/NATIVE_REWORK_INDEX_UNAVAILABLE/);assert.equal(calls.length,before);
});
test('unknown, 404, 401 and incomplete POST results preserve the exact operation for read-only review without automatic replay',async()=>{
  for(const mode of ['network','404','401','incomplete']){let writes=0,gets=0;const local=storage(),api=new ApiClient(async(url,options)=>{if(url==='/api/bootstrap')return new Response(JSON.stringify({csrf_token:'t'}));if(options.method==='POST'){writes++;if(mode==='network')throw new Error('response lost');return new Response(JSON.stringify(mode==='incomplete'?{status:'INCOMPLETE_REVIEW_REQUIRED',operation_id:mode,request_sha256:readReworkHistory(local,'p',ref)[0].requestSha,automatic_retry:false}:{error:{code:'DECLINED',message:'unconfirmed'}}),{status:mode==='401'?401:mode==='404'?404:200});}gets++;return new Response(JSON.stringify(mode==='incomplete'?{status:'INCOMPLETE_REVIEW_REQUIRED',operation_id:mode,request_sha256:readReworkHistory(local,'p',ref)[0].requestSha,automatic_retry:false}:{error:{code:'NOT_FOUND',message:'not recorded'}}),{status:mode==='incomplete'?200:404});}),client=new NativeReworkApi(api,unitReworkLocks());
    await assert.rejects(()=>client.map(local,feedback,other,{},mode),error=>error instanceof ReworkOutcomeError&&error.operation.operationId===mode&&error.review.automatic_retry===false);assert.equal(writes,1);assert.equal(gets,1);assert.equal(readReworkHistory(local,'p',ref).length,1);const review=await client.inspect(readReworkHistory(local,'p',ref)[0]);assert.equal(review.status,mode==='incomplete'?'INCOMPLETE_REVIEW_REQUIRED':'NO_RECORD');assert.equal(writes,1);
  }
});
test('cross-project or substituted refs cannot restore or activate a preview; late previous-project reads cannot publish into a newer project',async()=>{
  assert.throws(()=>assertReworkRecord(record,'other'),/身份不一致/);assert.throws(()=>assertReworkRecord(record,'p',other),/身份不一致/);assert.throws(()=>assertReworkRecord({...record,mapping_ref:other},'p'),/引用不一致/);
  let release;const pending=new Promise(resolve=>release=resolve),published=[];const recovery=new ReworkPreviewRecovery({get:async()=>pending},{get:async()=>feedback},(f,records)=>published.push({f,records}));const old=recovery.restore('p',[record]);await recovery.restore('other',[]);release(record);await old;assert.equal(published.length,0);recovery.stop();
});
test('unresolved or partially applied operations hide apply controls, rather than claiming that a returned plan is safe to write',async t=>{
  const {NativeReworkApplyControls}=await views(t),minimal={kind:'PLAN',status:'PROPOSED',current_basis_check:{status:'CURRENT_TASK_REQUIREMENT_BASIS'}};for(const state of [{unresolved:true,alreadyApplied:false},{unresolved:false,alreadyApplied:true}]){const html=renderToString(()=>NativeReworkApplyControls({plan:minimal,busy:false,approved:true,allowRebase:false,...state,onApproved:()=>{},onRebase:()=>{},onApply:()=>{}}));assert.ok(!html.includes('应用限定变更'));}
});
