import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {dirname,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import readline from 'node:readline';
import test from 'node:test';
import {ApiClient} from '../src/api.ts';
import {NativeFeedbackApi,savedTextareaSelection} from '../src/native-feedback-api.ts';

const here=dirname(fileURLToPath(import.meta.url)),root=resolve(here,'../../..');

function fixture(t,{losePostReply=false}={}) {
  const child=spawn(resolve(root,'.venv/bin/python'),['-B',resolve(here,'rework-http-fixture.py')],{
    cwd:root,stdio:['pipe','pipe','pipe'],env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}
  });
  const pending=[],calls=[];
  let stderr='',lost=false;
  const lines=readline.createInterface({input:child.stdout});
  child.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-8000);});
  lines.on('line',line=>{
    const item=pending.shift();
    if(item)try{item.resolve(JSON.parse(line));}catch(error){item.reject(error);}
  });
  child.on('error',error=>pending.splice(0).forEach(item=>item.reject(error)));
  child.on('exit',code=>pending.splice(0).forEach(item=>item.reject(new Error(`Fixture exited ${code}: ${stderr}`))));
  t.after(async()=>{
    child.stdin.end();
    if(child.exitCode===null){
      const timeout=setTimeout(()=>child.kill('SIGTERM'),5000);
      try{await once(child,'exit');}finally{clearTimeout(timeout);}
    }
    lines.close();
  });
  function raw(command){return new Promise((resolve,reject)=>{
    pending.push({resolve,reject});child.stdin.write(JSON.stringify(command)+'\n');
  });}
  const api=new ApiClient(async(url,options)=>{
    const method=options?.method??'GET',body=options?.body?JSON.parse(options.body):undefined;
    calls.push({method,url,body});
    const reply=await raw({method,path:url,headers:options?.headers??{},body});
    if(losePostReply&&!lost&&method==='POST'&&url.endsWith('/native-feedback')){
      assert.equal(reply.status,201,JSON.stringify(reply.body));lost=true;throw Error('Fixture drops one actual POST response');
    }
    return new Response(JSON.stringify(reply.body),{status:reply.status});
  });
  return {raw,calls,api,feedback:new NativeFeedbackApi(api)};
}

async function setup(f){const value=await f.raw({fixture:'setup'});assert.equal(value.status,200,JSON.stringify(value.body));return value.body;}
const feedbackPosts=f=>f.calls.filter(row=>row.method==='POST'&&row.url.endsWith('/native-feedback'));

test('typed quote client accepts real canonical stored keys and recovers the same operation after restart', {timeout:60000},async t=>{
  const f=fixture(t),s=await setup(f),selection={quote:'A 原已确认要求',kind:'EXACT_QUOTE'},key='source71-real-quote';
  const value=await f.feedback.record(s.source,'只修改引用的要求','SUBMITTED',key,{selection});
  assert.equal(value.payload.selection_verification,'TEXT_RANGE_HASH_VERIFIED');
  assert.deepEqual(value.payload.requested_selection,selection);
  assert.equal(value.payload.write_authorized,false);
  assert.equal(value.payload.model_dispatched,false);
  await f.raw({fixture:'restart'});
  const recovered=await f.feedback.inspectRequest({operation_id:key,file_source_ref:s.source,comment:'只修改引用的要求',mode:'SUBMITTED',selection});
  assert.deepEqual(recovered.feedback_ref,value.feedback_ref);
  assert.equal(feedbackPosts(f).length,1);
});

test('saved textarea projection preserves BOM CRLF emoji and the exact second repeated range through the real API', {timeout:60000},async t=>{
  const f=fixture(t),s=await setup(f),source='\ufeff标题😀\r\n同一句\r\n中间\r\n同一句\r\n末尾';
  assert.equal((await f.raw({fixture:'tamper',path:'原页.md',text:source})).status,200);
  const file=await f.api.request(`/api/projects/${s.pid}/local-workspaces/${s.source.workspace_id}/file?path=${encodeURIComponent('原页.md')}`);
  const buffer=source.replace(/\r\n?/g,'\n'),start=buffer.lastIndexOf('同一句');
  const selected=await savedTextareaSelection(file,buffer,start,start+'同一句'.length);
  const value=await f.feedback.record(file.source_ref,'仅第二处','DRAFT','source71-real-range',{selection:selected.selection});
  assert.equal(value.payload.selection_verification,'TEXT_RANGE_HASH_VERIFIED');
  assert.equal(value.payload.file_selection.char_start,Array.from(source.slice(0,source.lastIndexOf('同一句'))).length);
  assert.deepEqual(value.payload.file_selection,selected.selection);
  assert.equal(feedbackPosts(f).length,1);
  const actual=await f.raw({fixture:'bytes'});
  assert.equal(actual.body.actual,s.original);
  assert.equal(actual.body.independent,s.independent);
});

test('missing and repeated quotes remain saved descriptions and a lost POST result uses GET without another POST', {timeout:60000},async t=>{
  const f=fixture(t,{losePostReply:true}),s=await setup(f);
  for(const [quote,status,key] of [['不存在的原句','QUOTE_NOT_FOUND','source71-missing'],['requirement','QUOTE_AMBIGUOUS','source71-repeated']]){
    const value=await f.feedback.record(s.source,'先明确修改范围','DRAFT',key,{selection:{kind:'EXACT_QUOTE',quote}});
    assert.equal(value.payload.selection_verification,status);
    assert.equal(value.payload.file_selection,null);
    assert.equal(value.payload.write_authorized,false);
    assert.equal(value.payload.model_dispatched,false);
    assert.equal(f.calls.filter(row=>row.url.endsWith('/operations/'+key)&&row.method==='GET').length,key==='source71-missing'?1:0);
  }
  assert.equal(feedbackPosts(f).length,2);
  assert.deepEqual((await f.raw({fixture:'bytes'})).body,{actual:s.original,independent:s.independent});
});
