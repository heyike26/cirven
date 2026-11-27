'use strict';
// UNIT: actual QuickHost/QuickJournal, opener and Main orchestration. HTTP,
// native document resolver results and Electron/OS boundaries are explicit
// fixtures; this file does not claim original CLI/model/Windows qualification.
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),vm=require('node:vm');
const {createRequire}=require('node:module');
const {QuickJournal,quickDigest}=require('../quick-journal.cjs');
const {QuickHost}=require('../quick-host.cjs');
const {openQuickArtifact}=require('../quick-artifact-open.cjs');
const copy=value=>JSON.parse(JSON.stringify(value));
const pid='original-project',conversation='original-conversation';
const link={project_id:pid,id:'a'.repeat(64),revision:1,sha256:'b'.repeat(64)};
const snapshot={project_id:pid,id:'c'.repeat(64),revision:1,sha256:'d'.repeat(64)};
function revision(operation='original-question'){
  const id='e'.repeat(64);
  return {kind:'docx_revision',project_id:pid,id,revision:1,sha256:'f'.repeat(64),filename:'revision.docx',
    download_url:`/api/projects/${pid}/docx-revisions/artifacts/${id}/download`,
    owner:{operation_id:operation,generation:'original-native-generation',conversation_id:conversation},
    source_link_ref:link,snapshot_ref:snapshot,source_sha256:'1'.repeat(64),
    source_files_modified:false,changes_applied:false,later_source_edits_included:false};
}
function message(operation,artifact){return {operation_id:operation,status:'RETURNED',project_id:pid,
  conversation_id:conversation,file_link_ref:link,file_observation:{link_ref:link,snapshot_ref:snapshot,current_sha256:'1'.repeat(64)},artifacts:[artifact]};}
async function fixture(t,artifact=revision()){
  const dataDir=fs.mkdtempSync(path.join(os.tmpdir(),'eeas-quick-artifact-'));
  const journal=new QuickJournal({dataDir,hostGeneration:'quick-host-generation'}),requests=[];
  const host=new QuickHost({journal,getCompanion:()=>({}),request:async(route,body)=>{
    requests.push({route,body:copy(body)});return message(body.operation_id,artifact);}});
  host.setScope({projectId:pid,workspaceId:'workspace',sessionId:'topic',conversationId:conversation});
  host.setFileContext({file_link_ref:link,filename:'Original.docx'});
  const capture=host.emptyCapture();
  const record=host.save({draftId:'draft',expectedRevision:0,capture,comment:'Only revise this saved document.',regions:[],marks:[],delivery:'EDITING'});
  await host.send({draftId:record.id,operationId:'original-question',capture,comment:record.comment,regions:[],marks:[]});
  t.after(()=>{host.close();fs.rmSync(dataDir,{recursive:true,force:true});});
  return {host,journal,dataDir,requests,artifact};
}

test('owned DOCX card uses the existing exact report resolver once and keeps Main hidden with frozen scope',async t=>{
  const value=await fixture(t),calls=[];
  const before=fs.readFileSync(value.journal.file);
  const output=await openQuickArtifact({artifact:value.artifact,quickHost:value.host,
    resolveAndOpenDocument:async(...args)=>{calls.push(copy(args));return {status:'OPEN_REQUESTED'};}});
  assert.equal(output.status,'OPEN_REQUESTED');assert.equal(calls.length,1);
  assert.deepEqual(calls[0],[ '/api/desktop-host/native-open/report',
    {project_id:pid,artifact_id:value.artifact.id,sha256:value.artifact.sha256,download_url:value.artifact.download_url},
    {keepHidden:true,associationScope:{projectId:pid,workspaceId:'workspace',sessionId:'topic',conversationId:conversation}}]);
  assert.deepEqual(fs.readFileSync(value.journal.file),before,'opening is not another command or draft mutation');
  assert.equal(value.requests.length,1,'only the original question was dispatched by the actual QuickHost');
});

test('unowned object and arbitrary report URLs are rejected before the resolver',async t=>{
  const value=await fixture(t);let opens=0;
  const resolveAndOpenDocument=async()=>{opens++;};
  await assert.rejects(openQuickArtifact({artifact:{...value.artifact,filename:'forged.docx'},quickHost:value.host,resolveAndOpenDocument}),{code:'QUICK_ARTIFACT_UNOWNED'});
  for(const download_url of ['https://remote.invalid/download','file:///D:/secret.docx',value.artifact.download_url+'?x=1',value.artifact.download_url.replace(pid,'foreign-project')]){
    const malformed={...value.artifact,download_url};
    const invalid=await fixture(t,malformed);
    await assert.rejects(openQuickArtifact({artifact:malformed,quickHost:invalid.host,resolveAndOpenDocument}),{code:'QUICK_ARTIFACT_BINDING_DIFFERS'});
  }
  assert.equal(opens,0);
});

test('owned server data must still match the original question, project, conversation and frozen file basis',async t=>{
  for(const change of [
    artifact=>({...artifact,project_id:'foreign-project'}),
    artifact=>({...artifact,owner:{...artifact.owner,operation_id:'foreign-question'}}),
    artifact=>({...artifact,owner:{...artifact.owner,conversation_id:'foreign-conversation'}}),
    artifact=>({...artifact,source_link_ref:{...link,revision:2}}),
    artifact=>({...artifact,snapshot_ref:{...snapshot,sha256:'0'.repeat(64)}}),
    artifact=>({...artifact,source_sha256:'0'.repeat(64)}),
    artifact=>({...artifact,source_files_modified:true}),
  ]){
    const artifact=change(revision()),value=await fixture(t,artifact);let calls=0;
    await assert.rejects(openQuickArtifact({artifact,quickHost:value.host,resolveAndOpenDocument:async()=>{calls++;}}),{code:'QUICK_ARTIFACT_BINDING_DIFFERS'});
    assert.equal(calls,0);
  }
});

test('project, topic and conversation changes cannot borrow current scope for an old artifact',async t=>{
  for(const scope of [
    {projectId:'foreign-project',conversationId:'different'},
    {projectId:pid,workspaceId:'workspace',sessionId:'other-topic',conversationId:conversation},
    {projectId:pid,workspaceId:'workspace',sessionId:'topic',conversationId:'different'},
  ]){
    const value=await fixture(t);value.host.setScope(scope);let calls=0;
    await assert.rejects(openQuickArtifact({artifact:value.artifact,quickHost:value.host,resolveAndOpenDocument:async()=>{calls++;}}),{code:'QUICK_ARTIFACT_SCOPE_CHANGED'});
    assert.equal(calls,0);
  }
});

test('an unconfirmed native open is surfaced once without retry, model dispatch or rewriting the saved record',async t=>{
  const value=await fixture(t);const before=fs.readFileSync(value.journal.file);let opens=0;
  await assert.rejects(openQuickArtifact({artifact:value.artifact,quickHost:value.host,
    resolveAndOpenDocument:async()=>{opens++;throw new Error('Explicit unconfirmed shell boundary fixture');}}),/unconfirmed shell/);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(opens,1);assert.equal(value.requests.length,1);assert.deepEqual(fs.readFileSync(value.journal.file),before);
});

test('existing local-file artifacts keep their same native resolver and frozen scope',async t=>{
  const source={kind:'local_file',project_id:pid,workspace_id:'workspace',binding_revision:1,binding_sha256:'2'.repeat(64),path:'Original.docx',revision:1,sha256:'3'.repeat(64)};
  const artifact={filename:'Original.docx',source_ref:source};const value=await fixture(t,artifact),calls=[];
  await openQuickArtifact({artifact,quickHost:value.host,resolveAndOpenDocument:async(...args)=>{calls.push(copy(args));}});
  assert.equal(calls.length,1);assert.equal(calls[0][0],'/api/desktop-host/native-open/file');assert.deepEqual(calls[0][1],{project_id:pid,file_source_ref:source});
  assert.equal(calls[0][2].keepHidden,true);assert.equal(calls[0][2].associationScope.conversationId,conversation);
});

// Reuse the existing orchestration harness verbatim, rather than copying Main
// behavior into a new test. Only the definitions preceding its first test run.
function mainFixture(options){
  const file=path.resolve(__dirname,'main-startup.test.cjs'),source=fs.readFileSync(file,'utf8');
  const end=source.indexOf("\ntest(");assert.ok(end>0);
  const context=vm.createContext({require:createRequire(file),__dirname:path.dirname(file),module:{exports:{}},
    Buffer,URL,URLSearchParams,AbortController,AbortSignal,console,process,setTimeout,clearTimeout,setImmediate});
  vm.runInContext(source.slice(0,end)+'\nmodule.exports={fixture};',context,{filename:file});
  return context.module.exports.fixture(options);
}
function resolved(id,hash){return {status:'RESOLVED',filename:'report.docx',native_path:path.resolve('/qa-native-working-copy',id+'.docx'),
  file_link_ref:{project_id:pid,id,revision:1,sha256:hash}};}
async function mainReady(t,{holdOpen=false}={}){
  let artifact;let release,enteredResolve;const entered=new Promise(resolve=>enteredResolve=resolve);
  const pending=new Promise(resolve=>release=resolve);
  const value=mainFixture({request:async({route,body,response})=>{
    if(route==='/api/desktop-host/native-messages'){
      artifact=revision(body.operation_id);return response(message(body.operation_id,artifact));
    }
    if(route==='/api/desktop-host/native-open/report'){
      if(body.artifact_id==='e'.repeat(64)&&holdOpen){enteredResolve();await pending;}
      return response(body.artifact_id==='e'.repeat(64)?resolved('4'.repeat(64),'5'.repeat(64)):resolved(link.id,link.sha256));
    }
  }});
  await value.boot();t.after(async()=>{release();await value.quitSafely();fs.rmSync(value.dataDir,{recursive:true,force:true});});
  await value.invoke('eeas:quick-scope',{projectId:pid,workspaceId:'workspace',sessionId:'topic',conversationId:conversation});
  await value.invoke('eeas:open-report-artifact',{projectId:pid,artifactId:'6'.repeat(64),sha256:'7'.repeat(64),downloadUrl:`/api/projects/${pid}/discovery/sessions/topic/reports/${'6'.repeat(64)}/download`});
  value.closeWindow();
  await value.quick().openAsk();await value.quickEvent({kind:'edit',comment:'Revise the linked document.',marks:[]});
  const sent=await value.quickEvent({kind:'command',command:'send'});assert.equal(sent.status,'COMPLETED');
  await value.quick().openAsk();
  return {value,artifact,entered,release};
}

test('actual Main Quick callback opens a DOCX revision via the same shell/association resolver without showing Main',async t=>{
  const {value,artifact}=await mainReady(t),before=value.events.length,count=value.windows.length;
  const output=await value.quickEvent({kind:'artifact',index:0});assert.equal(output.status,'OPENED');
  assert.equal(value.windows[0].visible,false);assert.equal(value.windows.length,count);
  assert.equal(value.events.slice(before).some(event=>['window.show','window.showInactive','window.focus','window.restore'].includes(event)),false);
  const requests=value.requests.filter(item=>item.url.endsWith('/api/desktop-host/native-open/report'));
  assert.equal(requests.length,2);assert.equal(JSON.parse(requests[1].init.body).download_url,artifact.download_url);
  assert.equal(value.fileContext(pid).fileLinkRef.id,'4'.repeat(64),'new copy becomes this same discussion explicit file context');
  assert.equal(value.requests.filter(item=>item.url.endsWith('/api/desktop-host/native-messages')).length,1);
});

test('actual Main resolver refuses a late Quick open after scope navigation, without shell opening or Main reveal',async t=>{
  const {value,entered,release}=await mainReady(t,{holdOpen:true});
  const openedBefore=value.events.filter(event=>Array.isArray(event)&&event[0]==='shell.openPath').length;
  const pending=value.quickEvent({kind:'artifact',index:0});await entered;
  await value.invoke('eeas:quick-scope',{projectId:'another-project',conversationId:'another-conversation'});
  release();const result=await pending;assert.equal(result.status,'ACTION_FAILED');
  assert.equal(value.events.filter(event=>Array.isArray(event)&&event[0]==='shell.openPath').length,openedBefore);
  assert.equal(value.windows[0].visible,false);
  assert.equal(value.requests.filter(item=>item.url.endsWith('/api/desktop-host/native-messages')).length,1);
});
