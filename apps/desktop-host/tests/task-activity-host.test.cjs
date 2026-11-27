'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {TaskActivityHost}=require('../task-activity-host.cjs');
const snapshot=()=>({settings:{enabled:true,notifications_enabled:true,interval_seconds:17},tasks:[],alerts:[]});
const defer=()=>{let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve};};
function fixture(request){const events=[],timers=new Map();let n=0;
 const host=new TaskActivityHost({request,notifications:{prime:()=>events.push('prime'),observe:()=>events.push('observe'),close:()=>events.push('close'),get:()=>({})},
 onSnapshot:()=>events.push('snapshot'),onError:()=>events.push('unavailable'),clock:{setTimeout:(fn,delay)=>{timers.set(++n,{fn,delay});return n;},clearTimeout:id=>timers.delete(id)}});
 return {host,events,timers};}
test('cached supervisor reads coalesce, baseline once, retain interval and never invoke task POSTs',async()=>{
 const calls=[],gate=defer(),v=fixture(async(...args)=>{calls.push(args);return calls.length===1?gate.promise:snapshot();});
 v.host.start();const again=v.host.refresh();assert.equal(calls.length,1);gate.resolve(snapshot());await again;
 assert.deepEqual(calls,[['/api/supervisor']]);assert.deepEqual(v.events,['prime','snapshot']);
 assert.equal([...v.timers.values()][0].delay,17000);await v.host.refresh();
 assert.deepEqual(v.events,['prime','snapshot','observe','snapshot']);assert.equal(v.timers.size,1);
 await v.host.close();assert.equal(v.timers.size,0);assert.equal(v.events.at(-1),'close');
});
test('late read after close cannot publish, notify or schedule a new timer',async()=>{
 const gate=defer(),v=fixture(()=>gate.promise);v.host.start();const closing=v.host.close();gate.resolve(snapshot());await closing;
 assert.deepEqual(v.events,['close']);assert.equal(v.host.get().snapshot,null);assert.equal(v.timers.size,0);
});
test('a failed GET retains last facts and marks observation failure without replaying tasks',async()=>{
 let reads=0;const v=fixture(async()=>{if(++reads===1)return snapshot();throw Error('local API temporarily unavailable');});
 await v.host.refresh();const previous=v.host.get().snapshot;await v.host.refresh();
 assert.deepEqual(v.host.get().snapshot,previous);assert.equal(v.host.get().readStatus,'OBSERVATION_UNAVAILABLE');
 assert.equal(v.events.filter(x=>x==='observe').length,0);await v.host.close();
});
test('a change event refreshes cached facts immediately and close aborts and joins the long GET',async()=>{
 let reads=0,watchCalls=0,release;
 const host=new TaskActivityHost({request:async()=>({...snapshot(),change_sequence:++reads}),
 watchRequest:async(path,signal)=>{watchCalls++;assert.match(path,/^\/api\/supervisor\/changes\?after=\d+$/);
   if(watchCalls===1)return new Promise(resolve=>{release=()=>resolve({change_sequence:2,closed:false});});
   return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(Error('aborted')),{once:true}));}});
 host.start();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(reads,1);assert.equal(watchCalls,1);
 release();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(reads,2);assert.equal(watchCalls,2);
 await host.close();assert.equal(host.watchPending,null);assert.equal(host.watchController,null);
});
test('change-channel failure keeps normal checks and cancelled backoff cannot restart',async()=>{
 const timers=new Map();let n=0,watchCalls=0;
 const host=new TaskActivityHost({request:async()=>({...snapshot(),change_sequence:3}),
   watchRequest:async()=>{watchCalls++;throw Error('channel lost');},
   clock:{setTimeout:(fn,delay)=>{timers.set(++n,{fn,delay});return n;},clearTimeout:id=>timers.delete(id)}});
 host.start();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(watchCalls,1);assert.equal(host.get().snapshot.change_sequence,3);
 assert.deepEqual([...timers.values()].map(t=>t.delay).sort((a,b)=>a-b),[5000,17000]);
 await host.close();assert.equal(timers.size,0);assert.equal(watchCalls,1);
});
