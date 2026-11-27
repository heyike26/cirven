'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),http=require('node:http');
const {createOrbPointerWatch}=require('../orb-pointer-watch.cjs');
const schema='eeas.orb-pointer-watch.v1',watch_id='a'.repeat(32),host_generation='b'.repeat(32);
const row=(kind,sequence=0,extra={})=>JSON.stringify({schema,watch_id,host_generation,kind,sequence,...extra})+'\n';
const ready=row('READY',0,{lease_ms:60000});
const flush=()=>new Promise(r=>setImmediate(r));
async function until(fn){const end=Date.now()+1500;while(!fn()&&Date.now()<end)await new Promise(r=>setTimeout(r,5));assert.ok(fn(),'bounded condition');}

function controlled(){
 let controller;const calls=[],source=new ReadableStream({start(c){controller=c;}});
 const watch=createOrbPointerWatch({openStream:async signal=>{calls.push(signal);return new Response(source,{headers:{'content-type':'application/x-ndjson'}});}});
 const events=[],stop=watch({onReady:()=>events.push('ready'),onOutside:()=>events.push('outside'),onError:()=>events.push('error')});
 return {calls,events,stop,send:s=>controller.enqueue(new TextEncoder().encode(s)),close:()=>controller.close()};
}

test('split NDJSON arms only at actual ready and terminates exactly once outside',async()=>{
 const f=controlled();f.send(ready.slice(0,23));await flush();assert.deepEqual(f.events,[]);
 f.send(ready.slice(23));await flush();assert.deepEqual(f.events,['ready']);
 f.send(row('OUTSIDE_POINTER_DOWN',1,{button:'LEFT',os_event_time:12}));await flush();
 assert.deepEqual(f.events,['ready','outside']);assert.equal(f.calls.length,1);assert.equal(f.calls[0].aborted,true);f.stop();
});
test('expiry closes the menu and user cancellation cannot deliver late readiness',async()=>{
 const f=controlled();f.send(ready);f.send(row('EXPIRED',1));await flush();assert.deepEqual(f.events,['ready','outside']);
 const late=controlled();late.stop();await flush();assert.deepEqual(late.events,[]);assert.equal(late.calls[0].aborted,true);
});
test('wrong identity, order, schema, malformed or oversized stream fails without reconnect',async()=>{
 for(const raw of [row('OUTSIDE_POINTER_DOWN',0),'{\n','x'.repeat(4097),ready+row('EXPIRED',1,{watch_id:'c'.repeat(32)}),ready+ready]){
  const f=controlled();f.send(raw);await flush();assert.equal(f.events.at(-1),'error');assert.equal(f.calls.length,1);assert.equal(f.calls[0].aborted,true);
 }
});
test('premature end and unavailable terminal close the watch without reopening',async()=>{
 for(const unavailable of [false,true]){
  const f=controlled();f.send(ready);if(unavailable)f.send(row('UNAVAILABLE',1,{code:'IDENTITY_CHANGED'}));else f.close();
  await flush();assert.deepEqual(f.events,['ready','error']);assert.equal(f.calls.length,1);
 }
});
test('HTTP conflict and wrong media type never reveal a menu',async()=>{
 for(const response of [new Response('{}',{status:409}),new Response('{}',{headers:{'content-type':'application/json'}})]){
  const events=[];let calls=0;const stop=createOrbPointerWatch({openStream:async()=>{calls++;return response;}})({onReady:()=>events.push('ready'),onOutside:()=>events.push('outside'),onError:()=>events.push('error')});
  await flush();assert.deepEqual(events,['error']);assert.equal(calls,1);stop();
 }
});
test('actual local HTTP disconnect releases its only stream; a later explicit watch has a fresh lifetime',async()=>{
 const sockets=new Set();let opened=0,closed=0;
 const server=http.createServer((_req,res)=>{opened++;res.writeHead(200,{'content-type':'application/x-ndjson'});res.write(ready);res.on('close',()=>closed++);});
 server.on('connection',socket=>{sockets.add(socket);socket.on('close',()=>sockets.delete(socket));});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 try{
  const watch=createOrbPointerWatch({openStream:signal=>fetch('http://127.0.0.1:'+server.address().port,{signal})});
  for(let n=0;n<2;n++){
   const events=[],stop=watch({onReady:()=>events.push('ready'),onOutside:()=>events.push('outside'),onError:()=>events.push('error')});
   await until(()=>events.length===1);assert.deepEqual(events,['ready']);stop();await until(()=>closed===n+1);assert.deepEqual(events,['ready']);
  }
  assert.equal(opened,2);
 }finally{for(const s of sockets)s.destroy();await new Promise(r=>server.close(r));}
});
