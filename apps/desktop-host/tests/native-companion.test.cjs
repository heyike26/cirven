'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { NativeCompanion, observationInput, clampOwnWindowBounds, revealOwnWindow } = require('../native-companion.cjs');

function fixture(overrides = {}) {
  const calls = [], geometry = [];
  const session = { id: 'session', revision: 1, sha256: 'a'.repeat(64) };
  // Source55 models this owned BrowserWindow's actual normal rectangle and
  // completion events. Desktop region/reflow remains forbidden below.
  let fullscreen = false, pinned = false, normal = { x: 10, y: 10, width: 520, height: 760 };
  const window = Object.assign(new EventEmitter(), {
    getNativeWindowHandle: () => { const b = Buffer.alloc(8); b.writeBigUInt64LE(9n); return b; },
    setMinimumSize: (...args) => geometry.push(['minimum', ...args]),
    getBounds: () => ({ ...normal }), getNormalBounds: () => ({ ...normal }),
    isMinimized: () => false, isMaximized: () => false,
    setSize: (...args) => { normal = { ...normal, width: args[0], height: args[1] }; geometry.push(['own-window', ...args]); },
    isFullScreen: () => fullscreen,
    setFullScreen: value => { fullscreen=value; geometry.push(['own-fullscreen',value]); window.emit(value ? 'enter-full-screen' : 'leave-full-screen'); },
    setBounds: value => { normal={...value}; geometry.push(['own-window-bounds',{...value}]); },
    setShape: () => { throw new Error('Native desktop region forbidden'); },
    isAlwaysOnTop: () => pinned,
    setAlwaysOnTop: value => { pinned=value; geometry.push(['own-pin',value]); },
    moveTop: () => {},
  });
  const request = async (url, body) => {
    calls.push({ url, body });
    if (url.endsWith('/collaboration/close')) return { status: 'CLOSED' };
    if (url.endsWith('/context/status')) return { configured: true };
    if (url.endsWith('/context/observe')) return {
      operation_id: body.operation_id, automatic_input_authorized: false,
      observation: { status: 'OBSERVED' }, preview: { status: 'UNAVAILABLE' },
    };
    return { status: 'ACTIVE', session_ref: session, surface_ref: session, input: { epoch: 1, enabled: false } };
  };
  return { calls, geometry, broker: new NativeCompanion({ window, request, initialMode:'companion', platform: 'win32', ownerPid: 8, ...overrides }) };
}

test('initialization binds only actual own identity and never uses desktop layout routes', async () => {
  const { broker, calls, geometry } = fixture();
  const value = await broker.initialize();
  assert.equal(value.desktop_layout, 'UNCHANGED');
  assert.equal(value.capabilities.native_input, false);
  assert.deepEqual(calls[0], { url: '/api/desktop-host/collaboration/bind',
    body: { main_window: { hwnd_decimal: '9', owner_pid: 8 }, owned_windows: {}, presentation: 'NATIVE_COMPANION' } });
  assert.equal(geometry.length, 0);
  assert.ok(calls.every(call => !call.url.includes('/workspace/')));
});

test('capture carries exact host session and makes no window/focus operation', async () => {
  const { broker, calls, geometry } = fixture();
  const result = await broker.capture({ intent: '这里是什么意思' });
  const call = calls.find(item => item.url.endsWith('/context/observe'));
  assert.deepEqual(call.body.expected_workspace_session_ref, { id: 'session', revision: 1, sha256: 'a'.repeat(64) });
  assert.equal(result.operation_id, call.body.operation_id);
  assert.equal(result.automatic_input_authorized, false);
  assert.equal(geometry.length, 0);
});

test('hidden auxiliary desktop entry is bound to the same host lifetime without changing its presentation', async () => {
  const auxiliary={getNativeWindowHandle(){const value=Buffer.alloc(8);value.writeBigUInt64LE(71n);return value;}};
  const {broker,calls,geometry}=fixture({ownedWindows:{assistantOrb:auxiliary}});
  await broker.initialize();
  assert.deepEqual(calls[0].body.owned_windows,{assistantOrb:{hwnd_decimal:'71',owner_pid:8}});
  assert.equal(geometry.length,0);
  await broker.close();
  assert.equal(calls.at(-1).url,'/api/desktop-host/collaboration/close');
});

test('renderer cannot supply native identity, image URL or undeclared observation mode', () => {
  for (const input of [{ hwnd: 9 }, { url: 'file:///private' }, { need: { metadata: true } },
    { need: { metadata: true, accessibility: true, visual: true, ocr: 1 } },
    { intent: '   ' }, { need: { metadata: false, accessibility: false, visual: false, ocr: false } }]) {
    assert.throws(() => observationInput(input));
  }
});

test('capture failure does not invent context or dispatch model and allows another observation', async () => {
  let fail = true;
  const { broker } = fixture({ request: async (url, body) => {
    if (url.endsWith('/context/status')) return { configured: true };
    if (url.endsWith('/context/observe')) {
      if (fail) throw new Error('Actual capture unavailable');
      return { operation_id: body.operation_id, automatic_input_authorized: false, preview: { status: 'UNAVAILABLE' } };
    }
    return { status: 'ACTIVE' };
  } });
  await assert.rejects(broker.capture(), /capture unavailable/);
  assert.equal(broker.context, null);
  fail = false;
  assert.equal((await broker.capture()).automatic_input_authorized, false);
});

test('bad image route never reaches private image reader', async () => {
  let reads = 0;
  const { broker } = fixture({ imageReader: async () => { reads++; }, request: async (url, body) => {
    if (url.endsWith('/context/status')) return { configured: true };
    if (url.endsWith('/context/observe')) return { operation_id: body.operation_id,
      automatic_input_authorized: false, preview: { status: 'AVAILABLE', image_ref: { download_url: 'https://bad.invalid/image' } } };
    return { status: 'ACTIVE' };
  } });
  await assert.rejects(broker.capture(), /Untrusted/);
  assert.equal(reads, 0);
});

test('presentation modifies only own window, never desktop reservation', async () => {
  const { broker, calls, geometry } = fixture();
  const result = await broker.setMode('workbench');
  assert.equal(result.mode, 'workbench');
  assert.deepEqual(geometry, [['minimum', 780, 480], ['own-window', 1180, 880, false]]);
  assert.ok(calls.every(call => !call.url.includes('/workspace/')));
  await assert.rejects(broker.setMode('desktop'), /Unknown/);
});

test('ordinary questions capture fresh external context and frozen annotations keep their original frame', async () => {
  const calls = [], events = [];
  let revision = 0;
  const { broker } = fixture({onContext:value=>events.push(value), request:async(url,body)=>{
    calls.push({url,body});
    if(url.endsWith('/context/status')) return {configured:true};
    if(url.endsWith('/context/observe')) return {operation_id:body.operation_id,automatic_input_authorized:false,
      observation:{context_ref:{id:'actual-page',revision:++revision,sha256:String(revision).repeat(64)}},preview:{status:'UNAVAILABLE'}};
    if(url.endsWith('/native-messages')) return {status:'ACCEPTED',context_ref:body.context_ref};
    return {status:'ACTIVE',session_ref:{id:'session',revision:1,sha256:'a'.repeat(64)}};
  }});
  const first = await broker.submitMessage({operationId:'first',rawText:'解释这里'});
  const second = await broker.submitMessage({operationId:'second',rawText:'我修改了，再看看'});
  assert.equal(first.context_ref.revision,1);
  assert.equal(second.context_ref.revision,2);
  const frozen = await broker.submitMessage({operationId:'annotation',rawText:'这里要改',contextRef:first.context_ref});
  assert.deepEqual(frozen.context_ref,first.context_ref);
  assert.equal(calls.filter(v=>v.url.endsWith('/context/observe')).length,2);
  assert.equal(events.length,2);
  await broker.submitMessage({operationId:'first',rawText:'解释这里'});
  assert.equal(calls.filter(v=>v.url.endsWith('/context/observe')).length,2);
  assert.deepEqual(calls.at(-1).body.context_ref,first.context_ref);
  await assert.rejects(broker.submitMessage({operationId:'first',rawText:'另一个问题'}),/input differs/);
});

test('a failed fresh observation never dispatches with a cached context', async () => {
  let submitted=0;
  const {broker}=fixture({request:async(url)=>{
    if(url.endsWith('/context/observe')) throw Error('Current page unavailable');
    if(url.endsWith('/context/status')) return {configured:true};
    if(url.endsWith('/native-messages')) submitted++;
    return {status:'ACTIVE'};
  }});
  broker.context={observation:{context_ref:{id:'old',revision:1,sha256:'a'.repeat(64)}}};
  await assert.rejects(broker.submitMessage({operationId:'no-stale-fallback',rawText:'当前页是什么'}),/Current page unavailable/);
  assert.equal(submitted,0);
});

test('fullscreen uses only the actual owned window and validates the explicit state', async () => {
  const {broker,geometry,calls}=fixture();
  assert.equal((await broker.setFullscreen(true)).fullscreen,true);
  assert.equal((await broker.setFullscreen(false)).fullscreen,false);
  assert.deepEqual(geometry,[['own-fullscreen',true],['own-fullscreen',false]]);
  assert.ok(calls.every(item=>!item.url.includes('/workspace/')));
  await assert.rejects(broker.setFullscreen(1),/explicit/);
  await broker.setFullscreen(true);
  assert.equal((await broker.setMode('companion')).fullscreen,false);
  await broker.close();
  await assert.rejects(broker.setFullscreen(true),/closed/);
});

test('close invalidates capture immediately and records own authority cleanup', async () => {
  const { broker, calls } = fixture();
  await broker.close();
  await assert.rejects(broker.capture(), /closed/);
  assert.equal((await broker.get()).status, 'CLOSED');
  assert.equal(calls.at(-1).url, '/api/desktop-host/collaboration/close');
});

test('sealed collaboration entry does not imply pending or unknown original owner cleanup succeeded', async () => {
  let release;
  const { broker } = fixture({request: () => new Promise(resolve => {release=resolve;})});
  const pending=broker.close();
  assert.equal((await broker.get()).status, 'CLOSE_UNKNOWN');
  await assert.rejects(broker.capture(), /closed/);
  release({status:'CLOSE_UNKNOWN'});await pending;
  assert.equal((await broker.get()).status, 'CLOSE_UNKNOWN');
  const failure=fixture({request:async()=>{throw new Error('Owner cleanup unavailable');}}).broker;
  await assert.rejects(failure.close(), /cleanup unavailable/);
  assert.equal((await failure.get()).status, 'CLOSE_UNKNOWN');
  assert.equal((await failure.get()).capabilities.native_input, false);
});

test('pin is opt-in, suspends in workbench and restores only on the own companion', async () => {
  const {broker,geometry}=fixture();
  assert.equal((await broker.initialize()).pinned,false);
  assert.equal(geometry.length,0);
  assert.equal((await broker.setCompanionPinned(true)).pinned,true);
  assert.equal((await broker.setMode('workbench')).pinned,false);
  assert.equal((await broker.setCompanionPinned(true)).pinned_preference,true);
  assert.equal((await broker.setMode('companion')).pinned,true);
  assert.equal((await broker.setCompanionPinned(false)).pinned,false);
  assert.deepEqual(geometry.filter(value=>value[0]==='own-pin'),
    [['own-pin',true],['own-pin',false],['own-pin',true],['own-pin',false]]);
  await assert.rejects(broker.setCompanionPinned('true'),/explicit/);
  await broker.close();
  await assert.rejects(broker.setCompanionPinned(true),/Only the active companion/);
});

test('long Unicode questions keep whole supplementary characters across the observation boundary', async () => {
  const {broker,calls}=fixture();
  const raw='中'.repeat(8191)+'🙂'+'余'.repeat(7808);
  await broker.submitMessage({operationId:'unicode-boundary',rawText:raw});
  const intent=calls.find(v=>v.url.endsWith('/context/observe')).body.intent;
  assert.equal(Array.from(intent).length,8192);
  assert.ok(intent.endsWith('🙂'));
  assert.equal(calls.at(-1).body.raw_text,raw);
  await assert.rejects(broker.submitMessage({operationId:'bad-surrogate',rawText:'\uD800'}),/invalid Unicode/);
});

test('confirmed native document opening leaves own workbench/fullscreen and restores the explicit pin preference', async () => {
  const {broker,geometry,calls}=fixture();
  await broker.setCompanionPinned(true);
  await broker.setMode('workbench');
  await broker.setFullscreen(true);
  const state=await broker.prepareDocumentOpen();
  assert.equal(state.mode,'companion');assert.equal(state.fullscreen,false);assert.equal(state.pinned,true);assert.equal(state.pinned_preference,true);
  const prior=geometry.length;
  await broker.prepareDocumentOpen();
  assert.equal(geometry.length,prior);
  assert.ok(calls.every(call=>!call.url.includes('/workspace/')));
  await broker.close();
  await assert.rejects(broker.prepareDocumentOpen(),/closed/);
});

function ownedWindow(bounds,minimum=[390,480]) {
  const calls=[],emitter=new EventEmitter();let current={...bounds},min=[...minimum],minimized=false;
  return {calls,window:{
    on:emitter.on.bind(emitter),removeListener:emitter.removeListener.bind(emitter),emit:emitter.emit.bind(emitter),
    isDestroyed:()=>false,isMinimized:()=>minimized,isFullScreen:()=>false,isMaximized:()=>false,
    restore(){minimized=false;calls.push('restore');emitter.emit('restore');},getBounds:()=>({...current}),getMinimumSize:()=>[...min],
    setMinimumSize(width,height){min=[width,height];calls.push(['minimum',width,height]);},
    setBounds(value,animated){current={...value};calls.push(['bounds',value,animated]);},
    show(){calls.push('show');},focus(){calls.push('focus');},moveTop(){calls.push('moveTop');},
    setShape(){assert.fail('the desktop region is never involved');},
    setFullScreen(){assert.fail('recovery cannot change OS fullscreen');},
  },get bounds(){return current;},get minimum(){return min;},minimize(){minimized=true;}};
}
function displayScreen(areas,cursorArea=areas[0]) {
  return {getAllDisplays:()=>areas.map(workArea=>({workArea})),getCursorScreenPoint:()=>({x:cursorArea.x+10,y:cursorArea.y+10}),
    getDisplayNearestPoint:()=>({workArea:cursorArea})};
}

test('own rectangle clamping respects taskbar work areas, negative monitor origins and oversized workbenches',()=>{
  assert.deepEqual(clampOwnWindowBounds({x:1384,y:200,width:1180,height:880},{x:0,y:0,width:1920,height:1040}),
    {x:740,y:160,width:1180,height:880});
  assert.deepEqual(clampOwnWindowBounds({x:-9000,y:9999,width:1180,height:880},{x:-1920,y:40,width:1920,height:1000}),
    {x:-1920,y:160,width:1180,height:880});
  assert.deepEqual(clampOwnWindowBounds({x:300,y:200,width:1180,height:880},{x:0,y:0,width:700,height:400}),
    {x:0,y:0,width:700,height:400});
  assert.equal(clampOwnWindowBounds({x:0,y:0,width:NaN,height:400},{x:0,y:0,width:700,height:400}),undefined);
});

test('hidden/minimized off-screen owner is restored and shown in the current display without new windows or foreign geometry',()=>{
  const current={x:0,y:40,width:1920,height:1000},left={x:-1920,y:0,width:1920,height:1040};
  const value=ownedWindow({x:9000,y:-4000,width:1180,height:880});value.minimize();
  assert.equal(revealOwnWindow(value.window,displayScreen([left,current],current)),true);
  assert.deepEqual(value.bounds,{x:740,y:40,width:1180,height:880});
  assert.deepEqual(value.calls,['restore',['bounds',{x:740,y:40,width:1180,height:880},false],'show','focus']);
  const before=value.calls.length;revealOwnWindow(value.window,displayScreen([left,current],current));
  assert.deepEqual(value.calls.slice(before),['show','focus'],'visible in-bounds owner keeps its user-defined size and position');
});

test('a normal window on an existing secondary display stays on that display when enlarged',()=>{
  const left={x:-1280,y:80,width:1280,height:944},main={x:0,y:0,width:1920,height:1040};
  const value=ownedWindow({x:-600,y:220,width:520,height:760});
  revealOwnWindow(value.window,displayScreen([left,main],main),{size:[1180,880],minimum:[780,480],show:false,focus:false});
  assert.deepEqual(value.bounds,{x:-1180,y:144,width:1180,height:880});
  assert.deepEqual(value.minimum,[780,480]);
  assert.equal(value.calls.includes('show'),false);assert.equal(value.calls.includes('focus'),false);
});

test('actual companion presentation uses only its owned rectangle and clamps minimum sizes on a small work area',async()=>{
  const area={x:100,y:60,width:700,height:400},value=ownedWindow({x:750,y:100,width:520,height:760});
  let calls=0;
  const broker=new NativeCompanion({window:{...value.window,isAlwaysOnTop:()=>false,setAlwaysOnTop(){assert.fail('no implicit pin');}},
    screen:displayScreen([area]),platform:'win32',request:async route=>{
      calls++;assert.ok(!route.includes('/workspace/'));return route.endsWith('/context/status')?{configured:false}:{status:'ACTIVE'};
    }});
  assert.equal((await broker.setMode('workbench')).mode,'workbench');
  assert.deepEqual(value.bounds,{x:100,y:60,width:700,height:400});assert.deepEqual(value.minimum,[700,400]);
  assert.equal(calls,3);assert.equal(value.calls.includes('focus'),false);
});

test('fullscreen/maximized owners are shown without replacing OS-managed geometry; destroyed owners stay untouched',()=>{
  const screen=displayScreen([{x:0,y:0,width:1920,height:1040}]);
  for(const state of ['isFullScreen','isMaximized']){
    const value=ownedWindow({x:0,y:0,width:1920,height:1080});value.window[state]=()=>true;
    revealOwnWindow(value.window,screen,{size:[520,760]});
    assert.deepEqual(value.calls,['show','focus']);assert.deepEqual(value.bounds,{x:0,y:0,width:1920,height:1080});
  }
  const value=ownedWindow({x:9000,y:-4000,width:1180,height:880});value.window.isDestroyed=()=>true;
  assert.equal(revealOwnWindow(value.window,screen),false);assert.deepEqual(value.calls,[]);
});

function comparisonScene({bounds={x:1180,y:120,width:610,height:810},areas=[{x:0,y:40,width:1920,height:1000}],request,initialMode='companion'}={}) {
  const own=ownedWindow(bounds),requests=[];
  let pinned=false,fullscreen=false,maximized=false;
  const window={...own.window,getBounds:()=>fullscreen||maximized?{...areas[0]}:{...own.bounds},
    getNormalBounds:()=>({...own.bounds}),isAlwaysOnTop:()=>pinned,
    setAlwaysOnTop(value){pinned=value;own.calls.push(['pin',value]);},
    isFullScreen:()=>fullscreen,setFullScreen(value){fullscreen=value;own.calls.push(['fullscreen',value]);window.emit(value?'enter-full-screen':'leave-full-screen');},
    isMaximized:()=>maximized,maximize(){maximized=true;},unmaximize(){maximized=false;own.calls.push('unmaximize');window.emit('unmaximize');}};
  const screen=displayScreen(areas);
  const broker=new NativeCompanion({window,screen,initialMode,platform:'win32',request:async(route,body)=>{
    requests.push({route,body});
    if(request)return request(route,body);
    return route.endsWith('/context/status')?{configured:true}:{status:route.endsWith('/close')?'CLOSED':'ACTIVE'};
  }});
  return {broker,window,screen,requests,calls:own.calls,get bounds(){return own.bounds;}};
}
function deferred() {
  let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};
}

test('document opening restores the actual pin and explicit pin/unpin alone changes the preference',async()=>{
  const s=comparisonScene(),before={...s.bounds};
  assert.equal((await s.broker.get()).pinned,false);
  const state=await s.broker.prepareDocumentOpen();
  assert.equal(state.pinned,false);assert.equal(state.comparison_active,false);assert.equal(s.broker.pinnedPreference,false);
  assert.deepEqual(s.bounds,before);assert.deepEqual(s.calls,[['pin',true],['pin',false]]);
  await s.broker.prepareDocumentOpen();assert.deepEqual(s.calls,[['pin',true],['pin',false],['pin',true],['pin',false]],'reopening completes one synchronous pair while keeping the user rectangle and pin');
  await s.broker.setCompanionPinned(true);
  const unpinned=await s.broker.setCompanionPinned(false);
  assert.equal(unpinned.pinned,false);assert.equal(unpinned.comparison_active,false);
  await s.broker.setMode('workbench');await s.broker.setMode('companion');
  assert.equal((await s.broker.get()).pinned,false,'temporary comparison never became a persistent pin preference');
  assert.equal(s.calls.includes('show'),false);assert.equal(s.calls.includes('focus'),false);
});

test('workbench roundtrip remembers the user companion position and size on its original negative-origin display',async()=>{
  const rectangle={x:-1040,y:130,width:620,height:800};
  const s=comparisonScene({bounds:rectangle,areas:[{x:-1280,y:60,width:1280,height:1000},{x:0,y:40,width:1920,height:1000}]});
  await s.broker.prepareDocumentOpen();await s.broker.setMode('workbench');
  assert.equal((await s.broker.get()).comparison_active,false);assert.equal((await s.broker.get()).pinned,false);
  const back=await s.broker.setMode('companion');
  assert.deepEqual(s.bounds,rectangle);assert.equal(back.pinned,false);
  const adjusted={x:-940,y:140,width:700,height:820};s.window.setBounds(adjusted,false);
  await s.broker.prepareDocumentOpen();await s.broker.setMode('workbench');await s.broker.setMode('companion');
  assert.deepEqual(s.bounds,adjusted,'a later user resize replaces the remembered companion rectangle');
  assert.equal(s.calls.includes('show'),false);assert.equal(s.calls.includes('focus'),false);
});

test('comparison preserves a separately chosen permanent pin while workbench ends only temporary comparison',async()=>{
  const s=comparisonScene();await s.broker.setCompanionPinned(true);await s.broker.prepareDocumentOpen();
  assert.equal(s.broker.pinnedPreference,true);
  await s.broker.setMode('workbench');
  assert.equal((await s.broker.get()).pinned,false);assert.equal(s.broker.comparisonActive,false);
  const returned=await s.broker.setMode('companion');
  assert.equal(returned.pinned,true);assert.equal(returned.comparison_active,false);assert.equal(s.broker.pinnedPreference,true);
});

test('first companion fallback is 520 by 760 and restored bounds clamp to a changed small work area',async()=>{
  const s=comparisonScene({bounds:{x:1400,y:200,width:1180,height:880}});
  const actualBounds=s.window.getBounds;s.window.getBounds=()=>undefined;
  await s.broker.setMode('workbench');s.window.getBounds=actualBounds;
  await s.broker.setMode('companion');
  assert.equal(s.bounds.width,520);assert.equal(s.bounds.height,760);
  await s.broker.setMode('workbench');
  const smaller={x:100,y:60,width:700,height:400};
  Object.assign(s.screen,displayScreen([smaller]));
  const state=await s.broker.setMode('companion');
  assert.equal(state.comparison_active,false);assert.equal(s.bounds.width,520);assert.equal(s.bounds.height,400);
  assert.ok(s.bounds.x>=100&&s.bounds.y>=60&&s.bounds.x+s.bounds.width<=800&&s.bounds.y+s.bounds.height<=460);
});

test('document opening leaves own fullscreen/maximized state for the remembered companion rectangle without focus',async()=>{
  for(const expanded of ['fullscreen','maximized']){
    const s=comparisonScene(),before={...s.bounds};
    if(expanded==='fullscreen')await s.broker.setFullscreen(true);else s.window.maximize();
    const state=await s.broker.prepareDocumentOpen();
    assert.equal(state.mode,'companion');assert.equal(state.fullscreen,false);assert.equal(s.window.isMaximized(),false);
    assert.deepEqual(s.bounds,before);assert.equal(state.pinned,false);
    assert.equal(s.calls.includes('show'),false);assert.equal(s.calls.includes('focus'),false);
  }
});

test('an exact document-open epoch rejects a late source callback after a newer user mode or pin choice',async()=>{
  const s=comparisonScene(),epoch=s.broker.getPresentationEpoch();
  assert.equal(s.requests.length,0,'freezing the epoch is synchronous and performs no API read');
  await s.broker.setMode('workbench');const before=s.calls.length;
  await assert.rejects(s.broker.prepareDocumentOpen({expectedEpoch:epoch}),error=>error.code==='PRESENTATION_CHANGED');
  assert.equal(s.calls.length,before);assert.equal(s.broker.mode,'workbench');
  await s.broker.setMode('companion');const current=s.broker.getPresentationEpoch();
  await s.broker.setCompanionPinned(false);
  await assert.rejects(s.broker.prepareDocumentOpen({expectedEpoch:current}),error=>error.code==='PRESENTATION_CHANGED');
  assert.equal((await s.broker.get()).pinned,false);
  for(const value of [{expectedEpoch:-1},{expectedEpoch:null},{expectedEpoch:'1'},{other:1}])await assert.rejects(s.broker.prepareDocumentOpen(value));
});

test('new user workbench or unpin intent during a delayed comparison state read wins without a late pin',async()=>{
  for(const choice of ['workbench','unpin']){
    const entered=deferred(),release=deferred();let first=true;
    const s=comparisonScene({request:async route=>{
      if(first&&route.endsWith('/collaboration/get')){first=false;entered.resolve();await release.promise;}
      return route.endsWith('/context/status')?{configured:true}:{status:'ACTIVE'};
    }});
    const pending=s.broker.prepareDocumentOpen();const rejected=assert.rejects(pending,error=>error.code==='PRESENTATION_CHANGED');
    await entered.promise;
    const latest=choice==='workbench'?s.broker.setMode('workbench'):s.broker.setCompanionPinned(false);
    release.resolve();await rejected;await latest;
    assert.equal(s.calls.some(call=>Array.isArray(call)&&call[0]==='pin'&&call[1]===true),false);
    assert.equal((await s.broker.get()).pinned,false);assert.equal(s.broker.mode,choice==='workbench'?'workbench':'companion');
  }
});

test('workbench-to-document preparation does not reenter its serial queue and a queued newer mode remains final',{timeout:2000},async()=>{
  const entered=deferred(),release=deferred();let gate=false;
  const s=comparisonScene({request:async route=>{
    if(gate&&route.endsWith('/collaboration/get')){gate=false;entered.resolve();await release.promise;}
    return route.endsWith('/context/status')?{configured:true}:{status:'ACTIVE'};
  }});
  await s.broker.setMode('workbench');gate=true;
  const pending=s.broker.prepareDocumentOpen();const rejected=assert.rejects(pending,error=>error.code==='PRESENTATION_CHANGED');
  await entered.promise;const latest=s.broker.setMode('workbench');release.resolve();
  await rejected;await latest;
  const final=await s.broker.get();assert.equal(final.mode,'workbench');assert.equal(final.pinned,false);assert.equal(final.comparison_active,false);
  const compare=await s.broker.prepareDocumentOpen();assert.equal(compare.mode,'companion');assert.equal(compare.pinned,false);
});

test('close during pending comparison or mode readiness prevents late window pin, geometry, fullscreen and reopening',async()=>{
  for(const operation of ['comparison','mode','fullscreen']){
    const entered=deferred(),release=deferred();let first=true;
    const s=comparisonScene({request:async route=>{
      if(first&&!route.endsWith('/close')){first=false;entered.resolve();await release.promise;}
      return route.endsWith('/context/status')?{configured:true}:{status:route.endsWith('/close')?'CLOSED':'ACTIVE'};
    }});
    const pending=operation==='comparison'?s.broker.prepareDocumentOpen():operation==='mode'?s.broker.setMode('workbench'):s.broker.setFullscreen(true);
    const rejected=assert.rejects(pending,/closed/);await entered.promise;await s.broker.close();release.resolve();await rejected;
    assert.deepEqual(s.calls,[],'no late own-window mutation is permitted after close');
    assert.equal((await s.broker.get()).status,'CLOSED');
    await assert.rejects(s.broker.prepareDocumentOpen(),/closed/);await assert.rejects(s.broker.setMode('companion'),/closed/);
  }
});

test('inactive collaboration cannot begin comparison and returned state reports actual pin rather than a requested preference',async()=>{
  const inactive=comparisonScene({request:async route=>route.endsWith('/context/status')?{configured:false}:{status:'UNAVAILABLE'}});
  await assert.rejects(inactive.broker.prepareDocumentOpen(),/not active/);assert.deepEqual(inactive.calls,[]);
  const s=comparisonScene();await s.broker.prepareDocumentOpen();s.window.setAlwaysOnTop(false);
  const actual=await s.broker.get();assert.equal(actual.pinned,false);assert.equal(actual.comparison_active,false);
});

test('normal default is the unpinned project workbench and stored pin only resumes on explicit companion entry',async()=>{
  const f=fixture({initialMode:'workbench',pinnedPreference:true});
  const initial=await f.broker.initialize();assert.equal(initial.mode,'workbench');assert.equal(initial.pinned,false);
  assert.equal(initial.pinned_preference,true);assert.equal(f.calls[0].body.presentation,'PROJECT_WORKBENCH');assert.deepEqual(f.geometry,[]);
  assert.equal((await f.broker.setMode('companion')).pinned,true);
  const epoch=f.broker.getPresentationEpoch();await f.broker.prepareDocumentOpen({expectedEpoch:epoch});
  assert.equal(f.broker.getPresentationEpoch(),epoch);assert.equal((await f.broker.get()).pinned,true);
});

test('only a completed direct pin choice persists; delayed state and closing cannot overwrite a newer unpin',async()=>{
  const gate=deferred(),entered=deferred(),saved=[];let wait=true;
  const f=fixture({onPinnedChange:value=>saved.push(value),request:async route=>{
    if(wait&&route.endsWith('/collaboration/get')){wait=false;entered.resolve();await gate.promise;}
    return route.endsWith('/context/status')?{configured:true}:{status:'ACTIVE'};
  }});
  const first=f.broker.setCompanionPinned(true),rejected=assert.rejects(first,error=>error.code==='PRESENTATION_CHANGED');
  await entered.promise;const latest=f.broker.setCompanionPinned(false);gate.resolve();await rejected;await latest;
  assert.deepEqual(saved,[false]);assert.equal((await f.broker.get()).pinned,false);
  let closing=false;const blocked=fixture({isClosing:()=>closing,onPinnedChange:()=>assert.fail('closing cannot persist pin')});
  closing=true;await assert.rejects(blocked.broker.setCompanionPinned(true),/closed/);
});

test('failed preference persistence retains the actual pin and previous preference without pretending success',async()=>{
  const f=fixture({onPinnedChange:()=>{throw Error('original persistence error');}});
  await assert.rejects(f.broker.setCompanionPinned(true),/persistence error/);
  assert.equal((await f.broker.get()).pinned,false);assert.equal(f.broker.pinnedPreference,false);assert.deepEqual(f.geometry,[]);
});

test('first explicit bubble conversation anchors inside the same DIP display and later user bounds survive re-entry',async()=>{
 const area={x:-1280,y:40,width:1280,height:1000},s=comparisonScene({bounds:{x:100,y:100,width:1180,height:880},areas:[area],initialMode:'workbench'});
 await s.broker.setMode('workbench');const state=await s.broker.setMode('companion',{anchor:{x:-1228,y:988}});
 assert.equal(state.mode,'companion');assert.equal(state.pinned,false);assert.deepEqual(s.bounds,{x:-1212,y:216,width:520,height:760});
 const user={x:-900,y:180,width:620,height:800};s.window.setBounds(user);await s.broker.setMode('workbench');
 await s.broker.setMode('companion',{anchor:{x:-20,y:60}});assert.deepEqual(s.bounds,user,'a changed bubble location cannot undo the user conversation rectangle');
 assert.equal(s.calls.includes('show'),false);assert.equal(s.calls.includes('focus'),false);
});

test('document handoff exposes only the same visible companion without focus and never changes the saved pin choice',async()=>{
  for(const pinned of [false,true]){
    const s=comparisonScene({initialMode:'workbench'}),saved=[];
    s.broker.onPinnedChange=value=>saved.push(value);
    await s.broker.setCompanionPinned(pinned);saved.length=0;
    s.window.showInactive=()=>s.calls.push('showInactive');s.window.isVisible=()=>true;
    const state=await s.broker.prepareDocumentOpen();
    assert.equal(state.mode,'companion');assert.equal(state.pinned,pinned);assert.deepEqual(saved,[]);
    assert.equal(s.calls.filter(item=>item==='showInactive').length,1);
    assert.equal(s.calls.filter(item=>item==='moveTop').length,pinned?1:0);
    assert.equal(s.calls.includes('show'),false);assert.equal(s.calls.includes('focus'),false);
    const bounds={...s.bounds},before=s.calls.length;await s.broker.prepareDocumentOpen();
    assert.deepEqual(s.bounds,bounds);assert.deepEqual(s.calls.slice(before),pinned?['showInactive','moveTop']:['showInactive',['pin',true],['pin',false]]);
  }
});

test('document handoff does not wake hidden/minimized Main, including while presentation readiness is pending',async()=>{
  for(const stage of ['before','get','presentation'])for(const hidden of ['hidden','minimized']){
    const gate=deferred(),entered=deferred();let waiting=stage!=='before',visible=stage!=='before'||hidden!=='hidden',minimized=stage==='before'&&hidden==='minimized';
    const s=comparisonScene({initialMode:'workbench',request:async route=>{
      if(waiting&&route.endsWith(stage==='presentation'?'/collaboration/presentation':'/collaboration/get')){
        waiting=false;entered.resolve();await gate.promise;
      }
      return route.endsWith('/context/status')?{configured:true}:{status:'ACTIVE'};
    }});
    s.window.isVisible=()=>visible;s.window.isMinimized=()=>minimized;
    s.window.showInactive=()=>s.calls.push('showInactive');
    const pending=s.broker.prepareDocumentOpen();
    const rejected=stage==='presentation'?assert.rejects(pending,error=>error.code==='PRESENTATION_CHANGED'):pending;
    if(stage!=='before'){await entered.promise;if(hidden==='hidden')visible=false;else minimized=true;gate.resolve();}
    await rejected;
    assert.equal(s.broker.mode,'workbench');assert.deepEqual(s.calls,[],stage+'/'+hidden);
  }
});

test('a newer document guard stops a delayed handoff before geometry or inactive reveal',async()=>{
  const gate=deferred(),entered=deferred();let current=true;
  const s=comparisonScene({initialMode:'workbench',request:async route=>{
    if(route.endsWith('/collaboration/presentation')){entered.resolve();await gate.promise;}
    return route.endsWith('/context/status')?{configured:true}:{status:'ACTIVE'};
  }});s.window.showInactive=()=>s.calls.push('showInactive');s.window.isVisible=()=>true;
  const pending=s.broker.prepareDocumentOpen({current:()=>{if(!current)throw Error('New document owns this handoff');}});
  const rejected=assert.rejects(pending,/New document/);await entered.promise;current=false;gate.resolve();await rejected;
  assert.deepEqual(s.calls,[]);assert.equal(s.broker.mode,'workbench');
});

test('document raise rechecks the current live visible window after inactive show',async()=>{
  for(const changed of ['hidden','minimized','destroyed','discussion']){
    const s=comparisonScene();let visible=true,minimized=false,destroyed=false,current=true,raises=0;
    s.window.isVisible=()=>visible;s.window.isMinimized=()=>minimized;s.window.isDestroyed=()=>destroyed;
    s.window.showInactive=()=>{
      s.calls.push('showInactive');
      if(changed==='hidden')visible=false;
      if(changed==='minimized')minimized=true;
      if(changed==='destroyed')destroyed=true;
      if(changed==='discussion')current=false;
    };
    s.window.moveTop=()=>{raises++;};
    await assert.rejects(s.broker.prepareDocumentOpen({current:()=>{if(!current)throw Error('Discussion changed');}}));
    assert.equal(raises,0,changed);assert.deepEqual(s.calls,['showInactive']);
  }
});

test('temporary promotion always clears its own pin after synchronous stale, hide, minimize, close or throw',async()=>{
  for(const change of ['discussion','hidden','minimized','close','promote-throw','destroyed']){
    const s=comparisonScene();let visible=true,minimized=false,destroyed=false,current=true;
    s.window.isVisible=()=>visible;s.window.isMinimized=()=>minimized;s.window.isDestroyed=()=>destroyed;
    const setPin=s.window.setAlwaysOnTop.bind(s.window);
    s.window.setAlwaysOnTop=value=>{
      setPin(value);
      if(!value)return;
      if(change==='discussion')current=false;
      if(change==='hidden')visible=false;
      if(change==='minimized')minimized=true;
      if(change==='close')void s.broker.close();
      if(change==='destroyed')destroyed=true;
      if(change==='promote-throw')throw Error('Promotion threw after changing the actual window');
    };
    await assert.rejects(s.broker.prepareDocumentOpen({current:()=>{if(!current)throw Error('New discussion');}}));
    assert.deepEqual(s.calls,change==='destroyed'?[['pin',true]]:[['pin',true],['pin',false]],change);
    if(!destroyed)assert.equal(s.window.isAlwaysOnTop(),false,change);
    assert.equal(s.broker.pinnedPreference,false);assert.equal(s.calls.includes('focus'),false);
  }
});

test('explicit pin and mode choices during temporary promotion run after cleanup and win',async()=>{
  for(const choice of ['pin','unpin','workbench','fullscreen']){
    const s=comparisonScene(),saved=[];let next;
    s.broker.onPinnedChange=value=>saved.push(value);
    const setPin=s.window.setAlwaysOnTop.bind(s.window);
    s.window.setAlwaysOnTop=value=>{
      setPin(value);
      if(value&&!next){
        next=choice==='pin'?s.broker.setCompanionPinned(true):choice==='unpin'?s.broker.setCompanionPinned(false):
          choice==='workbench'?s.broker.setMode('workbench'):s.broker.setFullscreen(true);
      }
    };
    await assert.rejects(s.broker.prepareDocumentOpen(),error=>error.code==='PRESENTATION_CHANGED');
    await next;
    assert.deepEqual(s.calls.filter(row=>Array.isArray(row)&&row[0]==='pin').slice(0,2),[['pin',true],['pin',false]],choice);
    assert.equal(s.window.isAlwaysOnTop(),choice==='pin');assert.equal(s.broker.pinnedPreference,choice==='pin');
    assert.deepEqual(saved,choice==='pin'?[true]:choice==='unpin'?[false]:[],choice);
    if(choice==='workbench')assert.equal(s.broker.mode,'workbench');
    if(choice==='fullscreen')assert.equal(s.window.isFullScreen(),true);
  }
});

test('failed temporary-pin cleanup stays a presentation failure and does not claim restoration',async()=>{
  const s=comparisonScene(),setPin=s.window.setAlwaysOnTop.bind(s.window);
  s.window.setAlwaysOnTop=value=>{
    if(!value){s.calls.push(['cleanup-attempt',false]);throw Error('Actual restore failed');}
    setPin(value);
  };
  await assert.rejects(s.broker.prepareDocumentOpen(),/Actual restore failed/);
  assert.equal(s.window.isAlwaysOnTop(),true,'failed native restoration is not fabricated as actual false');
  assert.equal(s.broker.pinnedPreference,false);
  assert.deepEqual(s.calls,[['pin',true],['cleanup-attempt',false]]);
});

test('temporary promotion requires both actual pin states even when a setter silently does nothing',async()=>{
  for(const failedPin of [true,false]){
    const s=comparisonScene(),setPin=s.window.setAlwaysOnTop.bind(s.window);
    s.window.setAlwaysOnTop=value=>{
      if(value===failedPin){s.calls.push(['silent-noop',value]);return;}
      setPin(value);
    };
    await assert.rejects(s.broker.prepareDocumentOpen(),failedPin?/Temporary window pin was not applied/:/Temporary window pin was not restored/);
    assert.equal(s.window.isAlwaysOnTop(),!failedPin);
    assert.equal(s.broker.pinnedPreference,false);
    assert.deepEqual(s.calls,failedPin?[['silent-noop',true],['pin',false]]:[['pin',true],['silent-noop',false]]);
  }
});
