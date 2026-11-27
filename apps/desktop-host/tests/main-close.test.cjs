'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const { NativeCompanion } = require('../native-companion.cjs');
const {EventEmitter}=require('node:events');
const {spawn}=require('node:child_process');
const os=require('node:os');
const ownPreferenceDirectories=[];
test.after(()=>{for(const directory of ownPreferenceDirectories)fs.rmSync(directory,{recursive:true,force:true});});

async function flush(){for(let index=0;index<24;index++)await Promise.resolve();}
function fixture(firstStatus = 'CLOSED',options={}) {
  const dataDir=fs.mkdtempSync(path.join(os.tmpdir(),'cirven-close-locale-'));ownPreferenceDirectories.push(dataDir);
  const fixtureProcess=Object.create(process);
  Object.defineProperty(fixtureProcess,'env',{value:{...process.env,EEAS_DATA_DIR:dataDir}});
  const events = [], errors = [], handlers=new Map();
  let timerSequence=0;const timers=new Map();
  const child=options.child??Object.assign(new EventEmitter(),{exitCode:null,signalCode:null,
    kill(signal){events.push('own-controller-'+signal);this.finish(null,signal);return true;},
    finish(code,signal=null){this.exitCode=code;this.signalCode=signal;this.emit('exit',code,signal);}});
  child.once('exit',(code,signal)=>events.push('own-controller-exit:'+code+':'+signal));
  const mainPath = path.resolve(__dirname, '../main.cjs');
  const actualRequire = createRequire(mainPath);
  const electron = {
    app: { setPath() {}, getLocale:()=>options.locale??'zh-CN', requestSingleInstanceLock: () => true,
      whenReady: () => new Promise(() => {}), on() {}, quit: () => events.push('app-quit') },
    globalShortcut: { unregisterAll: () => events.push('unregister-shortcuts') },
    dialog: { showErrorBox: (...args) => errors.push(args) },
    ipcMain:{handle:(channel,callback)=>handlers.set(channel,callback)},
  };
  const context = vm.createContext({
    require: id => id === 'electron' ? electron : id === 'node:fs'
      ? { mkdirSync() {} } : actualRequire(id),
    __dirname: path.dirname(mainPath), process:fixtureProcess, Buffer, console,
    setTimeout, clearTimeout, AbortSignal, fetch,
  });
  // Execute the actual Main functions. The appended harness only supplies
  // external process/window boundaries; it does not replace close decisions.
  vm.runInContext(fs.readFileSync(mainPath, 'utf8') + `
    globalThis.mainHarness = {
      bind(values) {
        companion=values.companion; controller=values.controller;
        nativeAppend=values.nativeAppend; window=values.window;
        cookie=values.unauthenticated?undefined:'fixture-cookie';
        csrf=values.unauthenticated?undefined:'fixture-csrf'; request=values.request;
        origin=values.origin;quickSurface=values.quickSurface;quickHost=values.quickHost;
        assistantOrb=values.assistantOrb;
        supervisorConversation=values.supervisorConversation;
      },
      quitSafely,registerIpc,
      state: () => ({quitting,stopped})
    };`, context, { filename: mainPath });
  let status = firstStatus;
  const request = async route => {
    events.push(route);
    if (route.endsWith('/collaboration/close')) {
      const current=typeof status === 'function' ? await status() : status;
      if (current instanceof Error) throw current;
      return { status:current, outcomes: { tasks: { status:current } } };
    }
    if(route==='/api/desktop-host/close')return {status:'CLOSED'};
    assert.equal(route,'/api/shutdown');
    const result=options.shutdown===undefined?{status:'stopping',owner_shutdown:{status:'CLOSED'}}:
      typeof options.shutdown==='function'?await options.shutdown():options.shutdown;
    if(result instanceof Error)throw result;
    if(options.autoExit!==false)queueMicrotask(()=>child.finish(0));
    return result;
  };
  const companion = new NativeCompanion({ window: {}, request });
  const nativeAppend = { takeoverEpoch: 3,
    activate: { cancelPending: () => events.push('cancel-own-helper') } };
  const contents={mainFrame:{url:'http://127.0.0.1:9183/'},getURL:()=>'http://127.0.0.1:9183/',
    send:(channel,body)=>events.push({channel,body})};
  const ownedWindow={ isDestroyed: () => false, destroy: () => events.push('own-window-destroy'),webContents:contents };
  context.mainHarness.bind({ companion:options.unauthenticated?undefined:companion,
    nativeAppend,request,unauthenticated:options.unauthenticated,controller:child,
    window:ownedWindow,origin:options.product?'http://127.0.0.1:9183':undefined,
    quickSurface:options.quickSurface,quickHost:options.quickHost,
    assistantOrb:options.assistantOrb,
    supervisorConversation:options.supervisorConversation,
  });
  // Own timer control only: no 45-second wall-clock sleeps in unknown tests.
  context.setTimeout=(fn,delay)=>{assert.ok([12000,45000].includes(delay));const id=++timerSequence;timers.set(id,fn);return id;};
  context.clearTimeout=id=>timers.delete(id);
  return { ...context.mainHarness, events, errors, companion, nativeAppend,child,timers,
    invoke(channel,body){return handlers.get(channel)({sender:contents,senderFrame:contents.mainFrame},body);},
    invokeForeign(channel,body){return handlers.get(channel)({sender:contents,senderFrame:{url:'https://foreign.invalid/'}},body);},
    acknowledge(body){return handlers.get('eeas:close-flush-complete')({sender:contents,senderFrame:contents.mainFrame},body);},
    async expireWait(){for(const [id,fn]of [...timers]){timers.delete(id);fn();}await flush();},
    setStatus: value => { status = value; } };
}

test('history routes the exact coordinator discussion to its public store without exposing native wire',async()=>{
  const operation={operation_id:'owned-op',request_sha256:'a'.repeat(64)};let reads=0;
  const value=fixture('CLOSED',{product:true,supervisorConversation:{
    snapshot:()=>({conversation_id:'owned-supervisor',operations:[operation]}),
    lookupOperation:id=>id===operation.operation_id?operation:undefined,
    displayForOperation:(id,sha)=>{reads++;assert.equal(id,operation.operation_id);assert.equal(sha,operation.request_sha256);return{...operation,question:'用户原话',text:'总管原答复',status:'RETURNED'};},
  }});value.registerIpc();
  const result=await value.invoke('eeas:native-conversation-get',{projectId:null,conversationId:'owned-supervisor',operationId:'owned-op'});
  assert.equal(result.kind,'SUPERVISOR_DISCUSSION');assert.equal(result.selection.question,'用户原话');assert.equal(reads,1);assert.deepEqual(value.events,[]);
  await assert.rejects(value.invoke('eeas:native-conversation-get',{projectId:null,conversationId:'owned-supervisor',operationId:'different-op'}),/REFERENCE_CHANGED/);
  await assert.rejects(value.invoke('eeas:native-conversation-get',{projectId:null,conversationId:'owned-supervisor',operationId:'owned-op',url:'https://other.invalid'}));
  assert.equal(reads,1);assert.deepEqual(value.events,[]);
});
test('Main history and old-operation IPC enforce exact keys and the owned renderer origin before any read',async()=>{
 const reads=[];const value=fixture('CLOSED',{product:true,supervisorConversation:{readHistoryPage:body=>{reads.push(['page',body]);return {same:'page'};},readOperation:body=>{reads.push(['operation',body]);return {same:'operation'};}}});value.registerIpc();
 assert.equal((await value.invoke('eeas:supervisor-history-read',{cursor:null,limit:24})).same,'page');assert.equal((await value.invoke('eeas:supervisor-operation-read',{operation_id:'old',request_sha256:'a'.repeat(64)})).same,'operation');
 for(const channel of ['eeas:supervisor-history-read','eeas:supervisor-operation-read']){
  await assert.rejects(Promise.resolve().then(()=>value.invoke(channel,{cursor:null,limit:24,path:'../other'})));
  await assert.rejects(Promise.resolve().then(()=>value.invokeForeign(channel,{cursor:null,limit:24})));
 }assert.equal(reads.length,2);assert.deepEqual(value.events,[]);
});

test('Main closes original owner before legacy host, then only its controller and window', async () => {
  const value = fixture();
  await value.quitSafely();
  assert.deepEqual(value.events, ['unregister-shortcuts', 'cancel-own-helper',
    '/api/desktop-host/collaboration/close', '/api/desktop-host/close',
    '/api/shutdown','own-controller-exit:0:null', 'own-window-destroy', 'app-quit']);
  assert.equal(value.nativeAppend.takeoverEpoch, 4);
  assert.equal(value.companion.closed, true);
  assert.equal(value.state().stopped, true);
  assert.equal(value.errors.length, 0);
});

test('normal product close saves auxiliary then renderer drafts before stopping any owner',async()=>{
  const trace=[];const value=fixture('CLOSED',{product:true,
    quickSurface:{async hide(body){assert.equal(body.save,true);trace.push('quick-saved');},dispose(){trace.push('quick-disposed');}},
    quickHost:{close(){trace.push('quick-host-closed');}},assistantOrb:{dispose(){trace.push('orb-disposed');}}});
  value.registerIpc();const pending=value.quitSafely();await flush();
  assert.deepEqual(trace,['quick-saved']);
  const request=value.events.find(item=>item.channel==='eeas:flush-drafts');
  assert.ok(request);assert.equal(value.events.includes('/api/shutdown'),false);
  assert.throws(()=>value.acknowledge({id:'0'.repeat(32),status:'SAVED'}),/不属于/);
  value.acknowledge({...request.body,status:'SAVED'});await pending;
  assert.deepEqual(trace,['quick-saved','quick-host-closed','quick-disposed','orb-disposed']);
  assert.equal(value.state().stopped,true);assert.equal(value.timers.size,0);
});
test('failed renderer flush preserves API, app and auxiliary windows for recovery',async()=>{
  const value=fixture('CLOSED',{product:true});value.registerIpc();
  const pending=value.quitSafely();await flush();
  const request=value.events.find(item=>item.channel==='eeas:flush-drafts');
  value.acknowledge({...request.body,status:'FAILED'});await pending;
  assert.equal(value.state().quitting,false);assert.equal(value.state().stopped,false);
  assert.equal(value.events.some(item=>typeof item==='string'&&item.startsWith('/api/')),false);
  assert.equal(value.events.includes('own-window-destroy'),false);assert.match(value.errors[0][1],/未能保存/);
});
test('failed annotation save cannot be hidden by a successful owner shutdown',async()=>{
  const value=fixture('CLOSED',{product:true,quickSurface:{hide:async()=>{throw Error('标记保存失败');}}});
  await value.quitSafely();
  assert.equal(value.state().quitting,false);assert.equal(value.events.length,0);
  assert.match(value.errors[0][1],/标记保存失败/);
});

test('unknown owner close cannot be hidden by legacy close and permits explicit close retry', async () => {
  const value = fixture('CLOSE_UNKNOWN');
  await value.quitSafely();
  assert.deepEqual(value.events, ['unregister-shortcuts', 'cancel-own-helper',
    '/api/desktop-host/collaboration/close']);
  assert.equal(value.state().stopped, false);
  assert.equal(value.state().quitting, false);
  assert.equal(value.errors.length, 1);
  assert.match(value.errors[0][1], /尚未确认关闭/);
  value.setStatus('CLOSED');
  await value.quitSafely();
  assert.equal(value.events.filter(item => item.endsWith('/collaboration/close')).length, 2);
  assert.equal(value.events.filter(item => item === '/api/desktop-host/close').length, 1);
  assert.equal(value.events.filter(item => item === '/api/shutdown').length, 1);
  assert.equal(value.events.includes('own-controller-SIGTERM'),false);
  assert.equal(value.state().stopped, true);
});

test('missing or rejected close receipt leaves owner and window available for investigation', async () => {
  for (const status of [undefined, 'ACTIVE', new Error('Original owner close unavailable')]) {
    const value = fixture(); value.setStatus(status);
    await value.quitSafely();
    assert.equal(value.state().stopped, false);
    assert.equal(value.state().quitting, false);
    assert.equal(value.errors.length, 1);
    assert.equal(value.events.includes('/api/desktop-host/close'), false);
    assert.equal(value.events.some(item => item.startsWith('own-')), false);
  }
});

test('repeated unknown cleanup never becomes a successful forced exit', async () => {
  const value=fixture('CLOSE_UNKNOWN');
  await value.quitSafely();await value.quitSafely();
  assert.equal(value.events.filter(item=>item.endsWith('/collaboration/close')).length,2);
  assert.equal(value.events.includes('/api/desktop-host/close'),false);
  assert.equal(value.events.some(item=>item.startsWith('own-')||item==='app-quit'),false);
  assert.equal(value.state().stopped,false);
  assert.equal(value.errors.length,2);
});

test('concurrent close gestures share the single in-flight Main shutdown', {timeout:2500}, async () => {
  let release,entered;
  const originalCloseEntered=new Promise(resolve=>{entered=resolve;});
  const value=fixture(()=>new Promise(resolve=>{release=resolve;entered();}));
  const pending=value.quitSafely();
  await value.quitSafely();
  // Main drains real pending export writes before reaching owner shutdown.
  // Await the actual phase instead of assuming a fixed microtask count.
  await originalCloseEntered;
  assert.equal(value.events.filter(item=>item.endsWith('/collaboration/close')).length,1);
  assert.equal(value.events.filter(item=>item==='cancel-own-helper').length,1);
  assert.equal(value.state().stopped,false);
  release('CLOSED');await pending;
  assert.equal(value.events.filter(item=>item==='/api/shutdown').length,1);
  assert.equal(value.events.includes('own-controller-SIGTERM'),false);
  assert.equal(value.events.filter(item=>item==='own-window-destroy').length,1);
  assert.equal(value.events.filter(item=>item==='app-quit').length,1);
});

test('stopping plus aggregate CLOSED waits for the actual own child exit event before destroying the window',async()=>{
  const value=fixture('CLOSED',{autoExit:false});const pending=value.quitSafely();await flush();
  assert.equal(value.state().stopped,false);assert.equal(value.events.includes('own-window-destroy'),false);
  assert.equal(value.timers.size,1);value.child.finish(0);await pending;
  assert.equal(value.state().stopped,true);assert.equal(value.timers.size,0);
  assert.equal(value.events.includes('own-controller-SIGTERM'),false);
});

test('a 45-second exit timeout retains the UI, never rePOSTs, and only late exit0 on the same child permits closure',async()=>{
  const value=fixture('CLOSED',{autoExit:false});const pending=value.quitSafely();await flush();
  await value.expireWait();await pending;
  assert.equal(value.state().stopped,false);assert.match(value.errors[0][1],/45 秒/);
  assert.equal(value.events.includes('own-window-destroy'),false);assert.equal(value.timers.size,0);
  await value.quitSafely();assert.equal(value.state().stopped,false);
  value.child.finish(0);await value.quitSafely();assert.equal(value.state().stopped,true);
  assert.equal(value.events.filter(item=>item==='/api/shutdown').length,1);
  assert.equal(value.events.filter(item=>item.endsWith('/collaboration/close')).length,1);
  assert.equal(value.events.filter(item=>item==='/api/desktop-host/close').length,1);
  assert.equal(value.events.includes('own-controller-SIGTERM'),false);
});

test('unknown, missing nested closure and rejected shutdown responses never force exit or replay the POST',async()=>{
  for(const shutdown of [{status:'CLOSE_UNKNOWN',owner_shutdown:{status:'CLOSE_UNKNOWN'}},
    {status:'stopping',owner_shutdown:{status:'CLOSE_UNKNOWN'}},{status:'stopping'},
    {status:'CLOSE_UNKNOWN',owner_shutdown:{status:'CLOSED'}},null,new Error('Reply unavailable')]){
    const value=fixture('CLOSED',{autoExit:false,shutdown});await value.quitSafely();await value.quitSafely();
    assert.equal(value.state().stopped,false);assert.equal(value.errors.length,2);assert.equal(value.timers.size,0);
    assert.equal(value.events.includes('own-window-destroy'),false);assert.equal(value.events.includes('app-quit'),false);
    assert.equal(value.events.includes('own-controller-SIGTERM'),false);
    assert.equal(value.events.filter(item=>item==='/api/shutdown').length,1);
  }
});

test('nonzero or signaled child exits stay unknown without another shutdown request',async()=>{
  for(const [code,signal]of [[2,null],[null,'SIGTERM']]){
    const value=fixture('CLOSED',{autoExit:false});const pending=value.quitSafely();await flush();
    value.child.finish(code,signal);await pending;await value.quitSafely();
    assert.equal(value.state().stopped,false);assert.equal(value.timers.size,0);
    assert.equal(value.events.filter(item=>item==='/api/shutdown').length,1);
    assert.equal(value.events.includes('own-window-destroy'),false);
  }
});

test('an unknown POST outcome can later be resolved only by the same retained child exit0',async()=>{
  const value=fixture('CLOSED',{autoExit:false,shutdown:new Error('Response lost')});await value.quitSafely();
  value.child.finish(0);await value.quitSafely();assert.equal(value.state().stopped,true);
  assert.equal(value.events.filter(item=>item==='/api/shutdown').length,1);
  assert.equal(value.events.filter(item=>item==='own-window-destroy').length,1);
});

test('unauthenticated startup cancellation can signal only the freshly spawned own child without owner/shutdown POSTs',async()=>{
  const value=fixture('CLOSED',{unauthenticated:true});await value.quitSafely();
  assert.equal(value.state().stopped,true);assert.equal(value.events.filter(item=>item==='own-controller-SIGTERM').length,1);
  assert.equal(value.events.some(item=>item.startsWith('/api/')),false);
});

test('actual local Node ChildProcess exits normally after the shutdown seam and is never killed',async t=>{
  const child=spawn(process.execPath,['-e','process.stdin.resume();process.stdin.on("end",()=>process.exit(0));'],
    {stdio:['pipe','ignore','ignore']});
  t.after(()=>{if(child.exitCode===null&&child.signalCode===null)child.kill('SIGTERM');});
  let killCalls=0;const kill=child.kill.bind(child);child.kill=(...args)=>{killCalls++;return kill(...args);};
  const value=fixture('CLOSED',{child,autoExit:false,shutdown:()=>{
    child.stdin.end();return {status:'stopping',owner_shutdown:{status:'CLOSED'}};
  }});
  await value.quitSafely();
  assert.equal(child.exitCode,0);assert.equal(child.signalCode,null);assert.equal(killCalls,0);
  assert.equal(value.state().stopped,true);assert.equal(value.timers.size,0);
  assert.equal(value.events.filter(item=>item==='/api/shutdown').length,1);
});
