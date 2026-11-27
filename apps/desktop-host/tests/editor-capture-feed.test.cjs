'use strict';
// AUTHORED_NOT_RUN: actual feed decoder/lifecycle, injected HTTP body and bridge
// notification boundary. This does not qualify live Python/Main/IDE/Windows.
const test=require('node:test'),assert=require('node:assert/strict');
const {DesktopEditorCaptureFeed}=require('../editor-capture-feed.cjs');
const runtime='a'.repeat(32),ready=()=>JSON.stringify({type:'ready',runtime_id:runtime})+'\n';
function fixture(){const responses=[],notifications=[],errors=[];let opens=0,cancels=0;
 const feed=new DesktopEditorCaptureFeed({openStream:async()=>{opens++;let controller;const stream=new ReadableStream({start(value){controller=value;},cancel(){cancels++;}});responses.push(controller);return new Response(stream,{headers:{'content-type':'application/x-ndjson'}});},notify:rows=>{notifications.push(rows);},onError:e=>errors.push(e)});
 return {feed,responses,notifications,errors,get opens(){return opens;},get cancels(){return cancels;},write(text,index=responses.length-1){responses[index].enqueue(Buffer.from(text));}};}
async function settle(){for(let i=0;i<20;i++)await Promise.resolve();}
test('one idle authenticated metadata stream handles fragmented frames and forwards only notifications',async()=>{
 const f=fixture(),waiting=f.feed.start();await settle();f.write(ready().slice(0,19));f.write(ready().slice(19));assert.deepEqual(await waiting,{connected:true,read_only:true});assert.equal(f.feed.active,true);
 const rows=[{observer_id:'b'.repeat(64),editor_client_id:'original_actor',editor_window_id:'original_window',project_id:'p',capture_ref:'c'.repeat(64),expires_at_ms:Date.now()+12000}],batch=JSON.stringify({type:'editor-capture-batch',runtime_id:runtime,rows})+'\n';f.write(batch.slice(0,53));f.write(batch.slice(53));await settle();assert.deepEqual(f.notifications,[rows]);assert.equal(f.opens,1);assert.equal(await f.feed.start(),await waiting);assert.equal(f.opens,1);
 await f.feed.close();assert.equal(f.feed.active,false);assert.equal(f.cancels,1);
});
test('foreign runtime, extra command field and unbounded line close only the feed',async()=>{
 for(const invalid of [JSON.stringify({type:'editor-capture-batch',runtime_id:'d'.repeat(32),rows:[{}]})+'\n',JSON.stringify({type:'editor-capture-batch',runtime_id:runtime,rows:[{}],action:'SAVE'})+'\n','x'.repeat(16385)]){
  const f=fixture(),waiting=f.feed.start();await settle();f.write(ready());await waiting;const task=f.feed.current.task;f.write(invalid);await task;assert.equal(f.feed.active,false);assert.equal(f.notifications.length,0);assert.equal(f.errors.length,1);assert.equal(f.opens,1);await f.feed.close();
 }
});
test('transport loss does not silently reconnect or replay; the next explicit action may reconnect metadata only',async()=>{
 const f=fixture(),first=f.feed.start();await settle();f.write(ready());await first;const task=f.feed.current.task;f.responses[0].close();await task;assert.equal(f.opens,1);assert.equal(f.feed.active,false);
 const second=f.feed.start();await settle();assert.equal(f.opens,2);f.write(ready());await second;assert.equal(f.notifications.length,0);await f.feed.close();
});
test('close before readiness rejects the original waiter and releases its owned response body',async()=>{
 const f=fixture(),waiting=f.feed.start(),observed=assert.rejects(waiting);await settle();await f.feed.close();await observed;assert.equal(f.feed.current,null);assert.equal(f.opens,1);assert.equal(f.notifications.length,0);
});
