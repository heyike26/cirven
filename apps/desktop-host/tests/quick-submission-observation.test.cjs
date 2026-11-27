'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),vm=require('node:vm'),http=require('node:http');
const {createRequire}=require('node:module');
const {QuickJournal}=require('../quick-journal.cjs');
const {QuickHost}=require('../quick-host.cjs');
const {httpSubmissionObservation,submissionObservation}=require('../quick-submission-observation.cjs');
// Actual production Main request function; only its lexical host credentials
// are synthetic and its remote boundary is an original local HTTP fixture.
function mainRequest(origin,fetchOverride=fetch){
  const file=path.resolve(__dirname,'../main.cjs'),source=fs.readFileSync(file,'utf8');
  const begin=source.indexOf('async function request(endpoint, body, signal) {'),end=source.indexOf('\nasync function startIdeCompanion()',begin);
  assert.ok(begin>0&&end>begin);
  const context=vm.createContext({origin,cookie:'qa-cookie',csrf:'qa-csrf',desktopToken:'qa-desktop',uiLanguage:{resolved_locale:'en'},
    fetch:fetchOverride,AbortSignal,require:createRequire(file),tr:()=> 'not ready'});
  vm.runInContext(source.slice(begin,end)+'\nglobalThis.actualRequest=request;',context,{filename:file});
  return context.actualRequest;
}
async function scene(t,post){
  const calls=[];let readStatus='404';
  const server=http.createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req)chunks.push(chunk);
    const body=chunks.length?JSON.parse(Buffer.concat(chunks)):undefined;calls.push({method:req.method,path:req.url,body});
    res.setHeader('Content-Type','application/json');
    if(req.method==='POST')return post(res,body);
    const operation=req.url.split('/').at(-1);
    if(readStatus==='404'){res.statusCode=404;return res.end(JSON.stringify({error:{code:'NOT_FOUND',message:'private/path/do-not-copy'}}));}
    res.end(JSON.stringify({operation_id:operation,status:'RETURNED',text:'actual fixture final'}));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const dataDir=fs.mkdtempSync(path.join(os.tmpdir(),'quick-submission-'));
  const request=mainRequest('http://127.0.0.1:'+server.address().port),journal=new QuickJournal({dataDir,hostGeneration:'test_host'});
  const host=new QuickHost({journal,request,getCompanion:()=>null});host.setScope({projectId:'qa_project',conversationId:'qa_conversation'});
  t.after(async()=>{host.close();await new Promise(resolve=>server.close(resolve));fs.rmSync(dataDir,{recursive:true,force:true});});
  const item=host.save({draftId:'qa_draft',expectedRevision:0,comment:'这是独立的原创问题',delivery:'DRAFT',marks:[],regions:[]});
  const send=()=>host.submitDraft({ref:{id:item.id,revision:item.revision,sha256:item.sha256}});
  return {host,journal,dataDir,calls,send,request,complete:()=>{readStatus='RETURNED';},missing:()=>{readStatus='404';}};
}

test('actual HTTP409 first diagnostic survives GET404, restart, immutable intent, then real terminal recovery without POST replay',async t=>{
  const f=await scene(t,(res)=>{res.statusCode=409;res.end(JSON.stringify({error:{code:'REVISION_CONFLICT',message:'Selected Windows file is busy or its namespace changed',detail:'NEVER_STORE_SECRET_OR_PATH'}}));});
  const sent=await f.send();assert.equal(sent.status,'UNKNOWN_OUTCOME');
  assert.equal(sent.first_submission_observation.http_status,409);assert.equal(sent.first_submission_observation.api_code,'REVISION_CONFLICT');
  assert.equal(sent.first_submission_observation.reason_code,'LINKED_DOCUMENT_UNAVAILABLE');
  const first=JSON.stringify(sent.first_submission_observation),command=f.journal.command(sent.operation_id),signature=command.signature;
  const inspected=await f.host.inspect(sent.operation_id);assert.equal(inspected.status,'UNKNOWN_OUTCOME');assert.equal(JSON.stringify(inspected.first_submission_observation),first);
  assert.match(inspected.error.message,/不能据此认定未执行/);
  const restarted=new QuickJournal({dataDir:f.dataDir,hostGeneration:'another_host'});
  assert.equal(JSON.stringify(restarted.command(sent.operation_id).first_submission_observation),first);
  assert.equal(restarted.command(sent.operation_id).signature,signature);assert.deepEqual(restarted.command(sent.operation_id).input,command.input);
  assert.doesNotMatch(fs.readFileSync(restarted.file,'utf8'),/NEVER_STORE_SECRET_OR_PATH|private\/path|Selected Windows file/);
  assert.equal(f.host.retainSubmitted({draftId:'qa_draft',operationId:sent.operation_id}).status,'RETAINED');
  f.complete();const final=await f.host.inspect(sent.operation_id);assert.equal(final.status,'RETURNED');assert.equal(final.text,'actual fixture final');
  assert.equal(JSON.stringify(final.first_submission_observation),first);
  f.journal.observe(sent.operation_id,{operation_id:sent.operation_id,status:'RETURNED',text:'next',first_submission_observation:{kind:'HTTP_RESPONSE',http_status:500,reason_code:'HTTP_RESPONSE_UNCONFIRMED'}});
  assert.equal(JSON.stringify(f.journal.command(sent.operation_id).first_submission_observation),first);
  assert.equal(f.calls.filter(x=>x.method==='POST').length,1);assert.equal(f.calls.filter(x=>x.method==='GET').length,2);
});

test('non-JSON HTTP500 preserves actual HTTP status but never stores HTML, paths or fabricated API code',async t=>{
  const f=await scene(t,res=>{res.statusCode=500;res.end('<html>PRIVATE_TOKEN D:\\credentials\\file</html>');});
  const result=await f.send();assert.equal(result.status,'UNKNOWN_OUTCOME');assert.equal(result.first_submission_observation.http_status,500);
  assert.equal(result.first_submission_observation.api_code,undefined);assert.equal(result.first_submission_observation.reason_code,'HTTP_RESPONSE_UNCONFIRMED');
  assert.doesNotMatch(JSON.stringify(f.host.list())+fs.readFileSync(f.journal.file,'utf8'),/PRIVATE_TOKEN|credentials|<html>/);
});

test('actual HTTP body is not a reason whitelist; successful Main request semantics remain unchanged',async t=>{
  const f=await scene(t,res=>{res.statusCode=422;res.end(JSON.stringify({error:{code:'SECRET_STRING_THAT_LOOKS_VALID',message:'Bearer PRIVATE D:\\personal\\x'},detail:'PRIVATE'}));});
  const result=await f.send();assert.equal(result.status,'UNKNOWN_OUTCOME');assert.equal(result.first_submission_observation.http_status,422);
  assert.equal(result.first_submission_observation.api_code,undefined);assert.equal(result.first_submission_observation.reason_code,'HTTP_RESPONSE_UNCONFIRMED');
  assert.doesNotMatch(fs.readFileSync(f.journal.file,'utf8'),/Bearer|SECRET_STRING|personal/);
  f.complete();const body=await f.request('/api/desktop-host/native-messages/'+result.operation_id);assert.equal(body.status,'RETURNED');
});

test('transport failure has no invented HTTP evidence, no raw error leak, and diagnostic survives bounded long result projection',async t=>{
  const f=await scene(t,res=>res.end('{}'));
  const request=mainRequest('http://fixture.invalid',async()=>{throw Error('PRIVATE_TOKEN D:/private/unread');});
  f.host.request=request;const result=await f.send();assert.equal(result.first_submission_observation.kind,'TRANSPORT_UNCONFIRMED');
  assert.equal(result.first_submission_observation.http_status,undefined);assert.equal(f.calls.length,0);
  f.host.recordResult(result.operation_id,{operation_id:result.operation_id,status:'RETURNED',text:'x'.repeat(400000)});
  const restored=new QuickJournal({dataDir:f.dataDir,hostGeneration:'reader'});
  assert.equal(restored.command(result.operation_id).message.first_submission_observation.kind,'TRANSPORT_UNCONFIRMED');
  assert.equal(restored.command(result.operation_id).message.local_projection.complete,false);
  assert.doesNotMatch(fs.readFileSync(f.journal.file,'utf8'),/PRIVATE_TOKEN|private\/unread/);
});

test('first diagnostic persistence failure is explicit and prevents discarding the only visible diagnostic',async t=>{
  const f=await scene(t,res=>{res.statusCode=409;res.end('{}');});
  const commit=f.journal.commit.bind(f.journal);let commits=0;
  f.journal.commit=next=>{if(++commits===2)throw Error('ENOSPC');return commit(next);};
  const result=await f.send();assert.equal(result.status,'UNKNOWN_OUTCOME');assert.equal(result.local_persistence_status,'FAILED');
  assert.throws(()=>f.host.retainSubmitted({draftId:'qa_draft',operationId:result.operation_id}),/not been durably/);
  assert.equal(f.journal.command(result.operation_id).status,'DISPATCHING');
  assert.equal(f.calls.filter(x=>x.method==='POST').length,1);
});

test('diagnostic normalization rejects malformed status and arbitrary metadata instead of treating it as public content',()=>{
  assert.equal(httpSubmissionObservation(200,{}),null);
  const value=submissionObservation({kind:'HTTP_RESPONSE',http_status:403,reason_code:'HTTP_RESPONSE_UNCONFIRMED',api_code:'FORBIDDEN',raw:'SECRET'});
  assert.deepEqual(value,{kind:'HTTP_RESPONSE',reason_code:'HTTP_RESPONSE_UNCONFIRMED',http_status:403,api_code:'FORBIDDEN'});
  assert.equal(submissionObservation({kind:'HTTP_RESPONSE',http_status:403,reason_code:'SECRET'}),null);
});


test('GET404 cannot demote an already received terminal answer or overwrite its original diagnostic',async t=>{
  const f=await scene(t,res=>{res.statusCode=409;res.end('{}');});
  const initial=await f.send();f.complete();const final=await f.host.inspect(initial.operation_id);
  f.missing();const checked=await f.host.inspect(initial.operation_id);
  assert.equal(checked.status,'RETURNED');assert.equal(checked.text,final.text);
  assert.deepEqual(checked.first_submission_observation,initial.first_submission_observation);
  assert.equal(f.calls.filter(item=>item.method==='POST').length,1);
});

test('malformed added diagnostic cannot serialize arbitrary body strings into journal or visible projection',async t=>{
  const f=await scene(t,(res,body)=>res.end(JSON.stringify({operation_id:body.operation_id,status:'RETURNED',text:'known answer',
    first_submission_observation:{kind:'HTTP_RESPONSE',http_status:500,reason_code:'PRIVATE_TOKEN_D:/secret'}})));
  const initial=await f.send();assert.equal(initial.first_submission_observation,undefined);
  const result=f.host.recordResult(initial.operation_id,{operation_id:initial.operation_id,status:'RETURNED',text:'known answer',
    first_submission_observation:{kind:'HTTP_RESPONSE',http_status:500,reason_code:'PRIVATE_TOKEN_D:/secret'}});
  assert.equal(result.status,'RETURNED');assert.equal(result.first_submission_observation,undefined);
  assert.doesNotMatch(fs.readFileSync(f.journal.file,'utf8'),/PRIVATE_TOKEN_D:/);
});


test('a null JSON error body still records its actual HTTP status without manufacturing a code or message',async t=>{
  const f=await scene(t,res=>{res.statusCode=503;res.end('null');});
  const result=await f.send();assert.equal(result.status,'UNKNOWN_OUTCOME');
  assert.equal(result.first_submission_observation.http_status,503);assert.equal(result.first_submission_observation.api_code,undefined);
});
