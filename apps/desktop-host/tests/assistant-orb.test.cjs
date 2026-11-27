'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {EventEmitter}=require('node:events');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {AssistantOrb,ACTIONS,CHANNEL,STATE_CHANNEL,clampOrb,expandedBounds,arcLayout,orbHtml,noticeLayout}=require('../assistant-orb.cjs');

class OwnWindow extends EventEmitter {
  constructor(options){super();this.options=options;this.bounds={x:options.x,y:options.y,width:options.width,height:options.height};
    this.visible=false;this.destroyed=false;this.destroyCalls=0;this.boundsCalls=[];this.shapeCalls=[];this.protection=[];
    this.webContents=new EventEmitter();this.webContents.mainFrame={url:''};this.webContents.url='';
    this.webContents.sends=[];this.webContents.send=(...args)=>this.webContents.sends.push(args);
    this.webContents.getURL=()=>this.webContents.url;
    this.webContents.setWindowOpenHandler=handler=>{this.webContents.openHandler=handler;};}
  loadURL(url){this.loadCalls=(this.loadCalls??0)+1;this.webContents.url=url;this.webContents.mainFrame.url=url;return Promise.resolve();}
  getBounds(){return {...this.bounds};}
  setBounds(value){this.bounds={...value};this.boundsCalls.push({...value});}
  setShape(value){this.shapeCalls.push(structuredClone(value));this.shape=structuredClone(value);}
  moveTop(){this.moveTopCalls=(this.moveTopCalls??0)+1;}
  setTitle(value){this.title=value;}
  isDestroyed(){return this.destroyed;}
  setContentProtection(value){this.protection.push(value);}
  showInactive(){this.visible=true;}
  hide(){this.visible=false;}
  show(){throw new Error('Orb must never activate a window');}
  focus(){throw new Error('Orb must never focus a window');}
  destroy(){this.destroyCalls++;this.destroyed=true;this.emit('closed');}
}
function fixture(options={}){
  const ipc={handlers:new Map(),handle(name,handler){if(this.handlers.has(name))throw new Error('duplicate channel');this.handlers.set(name,handler);},removeHandler(name){this.handlers.delete(name);}};
  const screen=new EventEmitter();screen.cursor={x:300,y:200};
  screen.area={x:0,y:0,width:1280,height:860};
  screen.getCursorScreenPoint=()=>({...screen.cursor});
  screen.getDisplayNearestPoint=()=>({workArea:{...screen.area}});
  const calls=[],errors=[];
  const orb=new AssistantOrb({BrowserWindow:OwnWindow,screen,ipcMain:ipc,onAction:async action=>calls.push(action),onError:error=>errors.push(error),...options});
  const event=()=>({sender:orb.window.webContents,senderFrame:orb.window.webContents.mainFrame});
  const send=(payload,source=event())=>ipc.handlers.get(CHANNEL)(source,payload);
  return {orb,ipc,screen,calls,errors,event,send};
}
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function assertCenterOnly(state){
  const {center}=state.layout;
  assert.ok(state.shape.length>0&&state.shape.length<512);
  for(const r of state.shape)assert.ok(r.x>=center.x-28&&r.y>=center.y-28&&r.x+r.width<=center.x+28&&r.y+r.height<=center.y+28);
  assert.equal(state.shape.some(r=>center.x-27>=r.x&&center.y-27>=r.y&&center.x-27<r.x+r.width&&center.y-27<r.y+r.height),false,'transparent center corner is not a native hit');
}

test('constructs one hidden native lifetime and requests OS capture exclusion without claiming native pixel proof',async()=>{
  const {orb}=fixture();const own=orb.window;
  try{
    await orb.ready;
    assert.deepEqual(own.getBounds(),orb.get().bounds);assert.ok(own.options.width>=56&&own.options.height>=56);
    for(const field of ['transparent','alwaysOnTop','skipTaskbar'])assert.equal(own.options[field],true);
    for(const field of ['show','frame','focusable','resizable','minimizable','maximizable','fullscreenable'])assert.equal(own.options[field],false);
    assert.deepEqual(own.protection,[true]);
    assert.deepEqual(own.options.webPreferences,{preload:path.resolve(__dirname,'../assistant-orb-preload.cjs'),nodeIntegration:false,contextIsolation:true,sandbox:true,webviewTag:false});
    assert.equal(orb.get().enabled,true);assert.equal(orb.get().visible,false);assert.equal(own.visible,false);
    assert.equal(orb.get().captureAllowed,false);assert.equal(orb.get().captureExclusion,'REQUESTED');
    assert.equal(orb.getAnchor().orbBounds.x,24);assert.equal(orb.getAnchor().orbBounds.y,780);
    assert.equal(own.bounds.x+orb.get().layout.center.x,52);assert.equal(own.bounds.y+orb.get().layout.center.y,808);
    const initialHtml=decodeURIComponent(orb.pageURL.slice(orb.pageURL.indexOf(',')+1));
    assert.ok(initialHtml.includes(`.orb{position:absolute;left:${orb.get().layout.orb.x}px;top:${orb.get().layout.orb.y}px;`),'initial HTML places the center before its first IPC update');
    assert.equal(own.options.thickFrame,false);assert.equal(own.shapeCalls.length,1);
  }finally{orb.dispose();}
});

test('preference and minimized presentation are separate and retain the bound HWND lifetime',async()=>{
  const {orb}=fixture();const own=orb.window;
  try{
    await orb.setEnabled(true);assert.equal(orb.get().visible,false);
    await orb.setHostMinimized(true);assert.equal(orb.get().visible,true);
    await orb.setEnabled(false);assert.equal(orb.get().visible,false);assert.equal(orb.get().hostMinimized,true);
    await orb.setEnabled(true);assert.equal(orb.get().visible,true);
    await orb.setHostMinimized(false);assert.equal(orb.get().visible,false);assert.equal(orb.get().enabled,true);
    assert.equal(orb.window,own);assert.equal(own.destroyCalls,0);
    await assert.rejects(orb.setEnabled('true'));await assert.rejects(orb.setHostMinimized(1));
  }finally{orb.dispose();}
});

test('bounds stay in existing negative-coordinate display without altering its work area',()=>{
  const area={x:-1600,y:-120,width:1600,height:1000},before={...area};
  for(const point of [{x:-9999,y:-9999},{x:9999,y:9999},{x:-900,y:300}]){
    const bubble=clampOrb(point,area),expanded=expandedBounds(bubble,area);
    for(const value of [bubble,expanded]){
      assert.ok(value.x>=area.x&&value.y>=area.y);
      assert.ok(value.x+value.width<=area.x+area.width&&value.y+value.height<=area.y+area.height);
    }
    assert.equal(bubble.width,56);assert.ok(expanded.width<=280&&expanded.height<=280);
  }
  assert.deepEqual(area,before);
  assert.throws(()=>clampOrb({x:NaN,y:5},area));
});

test('only exact own top frame and exact local document may invoke orb IPC',async()=>{
  const f=fixture();try{
    await f.orb.setHostMinimized(true);
    for(const event of [{sender:{},senderFrame:f.orb.window.webContents.mainFrame},
      {sender:f.orb.window.webContents,senderFrame:{url:f.orb.pageURL}},
      {sender:f.orb.window.webContents,senderFrame:null}]){
      await assert.rejects(f.send({kind:'action',action:'ask'},event),/Untrusted/);
    }
    f.orb.window.webContents.url='https://untrusted.invalid';
    await assert.rejects(f.send({kind:'state'}),/Untrusted/);
    assert.deepEqual(f.calls,[]);
  }finally{f.orb.dispose();}
});

test('unknown actions, URLs, extra fields and invalid hover/drag payloads do nothing',async()=>{
  const f=fixture();try{
    await f.orb.setHostMinimized(true);const before=f.orb.window.getBounds();
    for(const value of [null,[],{kind:'action',action:'shell'},{kind:'action',action:'https://example.com'},
      {kind:'action',action:'ask',command:'anything'},{kind:'hover',inside:'true'},{kind:'drag',phase:'move',x:123},
      {kind:'drag',phase:'execute'},{kind:'state',token:'not-used'}]){
      await assert.rejects(f.send(value));
    }
    assert.deepEqual(f.calls,[]);assert.deepEqual(f.orb.window.getBounds(),before);
    assert.deepEqual(f.orb.window.webContents.openHandler({url:'file:///D:/unexpected'}),{action:'deny'});
    let prevented=0;f.orb.window.webContents.emit('will-navigate',{preventDefault(){prevented++;}},'file:///D:/unexpected');
    assert.equal(prevented,1);
  }finally{f.orb.dispose();}
});

test('explicit action reaches the host before any focus and concurrent clicks do not duplicate it',async()=>{
  let release;const actions=[];
  const f=fixture({onAction:async action=>{actions.push('freeze-external:'+action);await new Promise(resolve=>release=resolve);}});
  try{
    await f.orb.setHostMinimized(true);
    const first=f.send({kind:'action',action:'ask'});
    assert.deepEqual(actions,['freeze-external:ask']);assert.equal(f.orb.get().busy,true);
    assert.deepEqual(await f.send({kind:'action',action:'ask'}),{status:'BUSY'});
    assert.equal(f.orb.window.visible,true); // Only Root decides when Main restores.
    release();assert.deepEqual(await first,{status:'ACTION_DELIVERED',action:'ask'});
    assert.equal(f.orb.get().busy,false);
  }finally{f.orb.dispose();}
});

test('all five real actions are enumerated and delegated without executing tools or reading files',async()=>{
  const f=fixture();try{
    await f.orb.setHostMinimized(true);
    for(const action of ACTIONS)assert.equal((await f.send({kind:'action',action})).status,'ACTION_DELIVERED');
    assert.deepEqual(f.calls,[...ACTIONS]);
  }finally{f.orb.dispose();}
});

test('hover changes only native shape and retains fixed client and global center through exit motion',async()=>{
  const f=fixture();try{
    await f.orb.setHostMinimized(true);const old=f.orb.get(),anchor=old.anchor,boundsCalls=f.orb.window.boundsCalls.length;
    await f.send({kind:'hover',inside:true});
    const open=f.orb.get();assert.deepEqual(open.bounds,old.bounds);assert.ok(open.shape.length>1);
    assert.equal(open.bounds.x+open.layout.center.x,anchor.x);assert.equal(open.bounds.y+open.layout.center.y,anchor.y);
    assert.equal(open.expanded,true);
    f.screen.cursor={x:800,y:100};await f.send({kind:'hover',inside:false});assert.equal(f.orb.get().expanded,true);
    await pause(170);assert.equal(f.orb.get().expanded,false);assert.equal(f.orb.get().bounds.width,open.bounds.width);
    await pause(250);assert.deepEqual(f.orb.get().bounds,old.bounds);assertCenterOnly(f.orb.get());
    await f.send({kind:'hover',inside:true});await f.send({kind:'dismiss'});await pause(250);
    assert.deepEqual(f.orb.get().bounds,old.bounds);assert.equal(f.orb.window.boundsCalls.length,boundsCalls);
  }finally{f.orb.dispose();}
});

test('own drag uses current native cursor only, clamps bounds, and cannot execute an action mid-drag',async()=>{
  const f=fixture();try{
    await f.orb.setHostMinimized(true);
    await assert.rejects(f.send({kind:'drag',phase:'move'}),/not started/);
    f.screen.cursor={x:45,y:800};await f.send({kind:'drag',phase:'start'});
    f.screen.cursor={x:-5000,y:9999};await f.send({kind:'drag',phase:'move'});
    assert.deepEqual(f.orb.getAnchor().orbBounds,{x:0,y:804,width:56,height:56});
    assert.equal(f.orb.window.getBounds().x+f.orb.get().layout.center.x,28);
    assert.equal(f.orb.window.getBounds().y+f.orb.get().layout.center.y,832);
    assert.deepEqual(await f.send({kind:'action',action:'ask'}),{status:'BUSY'});
    await f.send({kind:'drag',phase:'end'});assert.equal(f.orb.get().dragging,false);
    assert.deepEqual(f.calls,[]);
  }finally{f.orb.dispose();}
});

test('unexpected close and renderer failure preserve the registered HWND until explicit dispose',async()=>{
  const f=fixture();try{
    await f.orb.setHostMinimized(true);let prevented=false;
    f.orb.window.emit('close',{preventDefault(){prevented=true;}});await pause(0);
    assert.equal(prevented,true);assert.equal(f.orb.window.destroyCalls,0);
    assert.equal(f.orb.get().visible,false);
    await f.orb.setEnabled(true);assert.equal(f.orb.get().visible,true);
    f.orb.window.webContents.emit('render-process-gone');
    assert.equal(f.orb.window.destroyCalls,0);assert.equal(f.orb.get().visible,false);
    assert.equal(f.errors.length,1);assert.ok(f.orb.get().error);
  }finally{f.orb.dispose();}
  assert.equal(f.orb.window.destroyCalls,1);assert.equal(f.ipc.handlers.has(CHANNEL),false);
});

test('disabled or closing presentation rejects actions and a failed callback exposes no private error',async()=>{
  let closing=false;
  const f=fixture({isClosing:()=>closing,onAction:async()=>{throw new Error('private-value-must-not-leak');}});
  try{
    await f.orb.ready;await assert.rejects(f.send({kind:'action',action:'ask'}),/not active/);
    await f.orb.setHostMinimized(true);
    assert.deepEqual(await f.send({kind:'action',action:'ask'}),{status:'ACTION_FAILED',action:'ask'});
    assert.equal(f.orb.get().error.includes('private-value'),false);
    assert.equal(f.errors.length,1);
    closing=true;await assert.rejects(f.send({kind:'action',action:'ask'}),/not active/);
    await f.orb.setEnabled(false);assert.equal(f.orb.get().visible,false);
  }finally{f.orb.dispose();}
});

test('local document and preload have no network, shell or general IPC execution surface',()=>{
  const html=orbHtml(),preload=fs.readFileSync(path.resolve(__dirname,'../assistant-orb-preload.cjs'),'utf8');
  assert.match(html,/script-src 'none'/);assert.match(html,/connect-src 'none'/);
  assert.match(html,/prefers-reduced-motion:reduce/);
  assert.equal((html.match(/data-action=/g)||[]).length,4);
  assert.match(preload,/event.key==='Escape'/);
  assert.doesNotMatch(preload,/openExternal|fetch\(|exec\(|shell\.|readFile|contextBridge/);
  assert.match(preload,/ipcRenderer.invoke\(CHANNEL,payload\)/);
  assert.match(html,/transition:transform 220ms/);
});


test('expanded menu choices stay inside a narrow work area and display removal reclamps only this window',async()=>{
  const f=fixture();try{
    await f.orb.setHostMinimized(true);
    f.screen.area={x:-300,y:0,width:300,height:300};f.screen.emit('display-removed');
    await f.send({kind:'hover',inside:true});
    const state=f.orb.get();
    for(const item of state.layout.actions){
      const x=state.layout.center.x+item.dx,y=state.layout.center.y+item.dy;
      assert.ok(x-28>=-1e-9&&x+28<=state.bounds.width+1e-9);
      assert.ok(y-28>=-1e-9&&y+28<=state.bounds.height+1e-9);
      assert.ok(Math.hypot(item.dx,item.dy)<=200+1e-9);
    }
    assert.equal(state.bounds.x>=-300,true);assert.equal(state.bounds.x+state.bounds.width<=0,true);
    assert.deepEqual(f.screen.area,{x:-300,y:0,width:300,height:300});
  }finally{f.orb.dispose();}
});


test('explicit disposal fences late renderer/error notifications without touching another window',async()=>{
  let rejectAction;const errors=[];
  const f=fixture({onAction:()=>new Promise((_resolve,reject)=>rejectAction=reject),onError:error=>errors.push(error)});
  await f.orb.setHostMinimized(true);const action=f.send({kind:'action',action:'ask'});
  f.orb.dispose();f.orb.window.webContents.emit('render-process-gone');
  rejectAction(new Error('late native context cancellation'));await action;
  assert.deepEqual(errors,[]);assert.equal(f.orb.window.destroyCalls,1);
});

test('background feedback does not expand or disable the entry and only real unread state shows the badge',async()=>{
  const f=fixture();try{
    await f.orb.setHostMinimized(true);const old=f.orb.get().bounds;
    f.orb.setFeedback({busy:true,unread:false});assert.equal(f.orb.get().processing,true);assert.equal(f.orb.get().busy,false);
    assert.deepEqual(f.orb.get().bounds,old);assert.equal(f.orb.get().expanded,false);
    await f.send({kind:'action',action:'ask'});assert.deepEqual(f.calls,['ask']);
    f.orb.setFeedback({busy:false,unread:true});assert.equal(f.orb.get().unread,true);
    f.orb.setFeedback({busy:false,unread:false});assert.equal(f.orb.get().unread,false);
    assert.throws(()=>f.orb.setFeedback({busy:'yes',unread:true}));
  }finally{f.orb.dispose();}
});

test('active quick surface retains the orb; preference and host presentation still suppress it',async()=>{
  const f=fixture();try{
    await f.orb.setHostMinimized(true);assert.equal(f.orb.get().visible,true);
    f.orb.setSurfaceActive(true);assert.equal(f.orb.get().visible,true);
    await f.send({kind:'hover',inside:true});assert.equal(f.orb.get().expanded,true);
    await f.send({kind:'action',action:'annotate'});assert.deepEqual(f.calls,['annotate']);
    await f.orb.setHostMinimized(false);assert.equal(f.orb.get().visible,false);
    await f.orb.setHostMinimized(true);assert.equal(f.orb.get().visible,true);
    await f.orb.setEnabled(false);f.orb.setSurfaceActive(false);assert.equal(f.orb.get().visible,false);
    await f.orb.setEnabled(true);assert.equal(f.orb.get().visible,true);
    assert.equal(f.orb.window.destroyCalls,0);assert.throws(()=>f.orb.setSurfaceActive('yes'));
  }finally{f.orb.dispose();}
});

test('outside watcher READY gates the native menu region; repeated hover and late callbacks cannot replay it',async()=>{
  const clock=fakeClock(),watches=[];let stops=0;
  const f=fixture({clock,watchOutside:callbacks=>{watches.push(callbacks);return ()=>stops++;}});
  try{
    await f.orb.setHostMinimized(true);const bounds=f.orb.get().bounds,anchor=f.orb.getAnchor(),moves=f.orb.window.boundsCalls.length;
    await f.send({kind:'hover',inside:true});await f.send({kind:'hover',inside:true});
    assert.equal(watches.length,1);assert.equal(f.orb.get().expanded,false);assert.equal(f.orb.get().expansionPending,true);assertCenterOnly(f.orb.get());
    watches[0].onReady();assert.equal(f.orb.get().expanded,true);assert.ok(f.orb.get().shape.length>1);assert.equal(f.orb.window.moveTopCalls,1);
    watches[0].onReady();assert.equal(f.orb.window.moveTopCalls,1,'duplicate readiness cannot keep raising the window');
    const shapes=f.orb.window.shapeCalls.length;f.orb.setFeedback({busy:true,unread:false});f.orb.setSurfaceActive(true);
    assert.equal(f.orb.window.shapeCalls.length,shapes,'unchanged geometry is not resubmitted');assert.equal(f.orb.window.moveTopCalls,1,'background updates do not raise the window');
    watches[0].onOutside();assert.equal(stops,1);assert.equal(f.orb.get().expanded,false);
    clock.advance(220);assertCenterOnly(f.orb.get());watches[0].onReady();watches[0].onOutside();
    assert.equal(f.orb.get().expanded,false);assert.equal(stops,1);assert.equal(f.orb.window.boundsCalls.length,moves);
    assert.deepEqual(f.orb.get().bounds,bounds);assert.deepEqual(f.orb.getAnchor(),anchor);assert.equal(f.orb.get().visible,true);
  }finally{f.orb.dispose();}
});

test('outside watcher errors retain the center and do not retry or toast on repeated hover',async()=>{
  const clock=fakeClock(),watches=[];let stops=0;
  const f=fixture({clock,watchOutside:callbacks=>{watches.push(callbacks);return ()=>stops++;}});
  try{
    await f.orb.setHostMinimized(true);await f.send({kind:'hover',inside:true});watches[0].onError(Error('private watcher failure'));
    assert.equal(stops,1);assert.equal(f.errors.length,1);assert.equal(f.orb.get().expanded,false);assertCenterOnly(f.orb.get());
    for(let n=0;n<5;n++)await f.send({kind:'hover',inside:true});watches[0].onError(Error('late'));watches[0].onReady();
    assert.equal(watches.length,1);assert.equal(f.errors.length,1);assert.equal(f.orb.get().visible,true);assert.equal(f.orb.get().error,null);
    await f.send({kind:'action',action:'ask'});assert.deepEqual(f.calls,['ask']);
    await f.send({kind:'hover',inside:false});await f.send({kind:'hover',inside:true});assert.equal(watches.length,2,'a new explicit hover may retry');
    watches[1].onReady();assert.equal(f.orb.get().expanded,true);
  }finally{f.orb.dispose();}
});

test('actual preload re-arms a native outside collapse without requiring a DOM pointerleave',async()=>{
  const watches=[],f=fixture({watchOutside:callbacks=>{watches.push(callbacks);return ()=>{};}});
  try{
    await f.orb.setHostMinimized(true);const ui=mountPreload(f);await pause(0);
    ui.orb.fire('pointerenter');await pause(0);watches[0].onReady();assert.equal(ui.stage.dataset.expanded,'true');
    watches[0].onOutside();assert.equal(ui.stage.dataset.expanded,'false');
    ui.orb.fire('pointerenter');await pause(0);assert.equal(watches.length,2);watches[1].onReady();assert.equal(ui.stage.dataset.expanded,'true');
  }finally{f.orb.dispose();}
});

for(const transition of ['escape','disabled','main','drag','display','dispose'])test(`pending outside readiness is fenced by ${transition}`,async()=>{
  const clock=fakeClock(),watches=[];let stops=0;
  const f=fixture({clock,watchOutside:callbacks=>{watches.push(callbacks);return ()=>stops++;}});
  try{
    await f.orb.setHostMinimized(true);await f.send({kind:'hover',inside:true});
    if(transition==='escape')await f.send({kind:'dismiss'});
    if(transition==='disabled')await f.orb.setEnabled(false);
    if(transition==='main')await f.orb.setHostMinimized(false);
    if(transition==='drag')await f.send({kind:'drag',phase:'start'});
    if(transition==='display')f.screen.emit('display-metrics-changed');
    if(transition==='dispose')f.orb.dispose();
    assert.equal(stops,1);clock.advance(1000);const before=f.orb.get();watches[0].onReady();watches[0].onOutside();watches[0].onError(Error('late'));
    assert.deepEqual(f.orb.get(),before);assert.equal(f.orb.get().expanded,false);assert.equal(f.orb.get().expansionPending,false);assert.equal(f.errors.length,0);
  }finally{f.orb.dispose();}
});

test('in-place menu actions remain continuously reachable on one watcher, fixed client and unchanged center',async()=>{
  const watches=[];let stops=0;const f=fixture({watchOutside:callbacks=>{watches.push(callbacks);return ()=>stops++;}});
  try{
    await f.orb.setHostMinimized(true);const bounds=f.orb.get().bounds,anchor=f.orb.getAnchor(),moves=f.orb.window.boundsCalls.length;
    await f.send({kind:'hover',inside:true});watches[0].onReady();
    for(const action of ['ask','annotate','tasks','ask','tasks']){
      assert.equal((await f.send({kind:'action',action})).status,'ACTION_DELIVERED');assert.equal(f.orb.get().expanded,true);
      assert.deepEqual(f.orb.get().bounds,bounds);assert.deepEqual(f.orb.getAnchor(),anchor);
    }
    assert.equal(watches.length,1);assert.equal(stops,0);assert.equal(f.orb.window.boundsCalls.length,moves);
    await f.send({kind:'action',action:'workbench'});assert.equal(f.orb.get().expanded,false);assert.equal(stops,1);
    assert.deepEqual(f.calls,['ask','annotate','tasks','ask','tasks','workbench']);
  }finally{f.orb.dispose();}
});

test('background Quick and task updates cannot interrupt a real cursor drag or change its canvas',async()=>{
  const f=fixture();try{
    await f.orb.setHostMinimized(true);f.screen.cursor={x:52,y:808};await f.send({kind:'drag',phase:'start'});
    f.screen.cursor={x:152,y:708};await f.send({kind:'drag',phase:'move'});const bounds=f.orb.get().bounds,anchor=f.orb.getAnchor(),moves=f.orb.window.boundsCalls.length;
    for(const active of [true,false,true]){
      f.orb.setSurfaceActive(active);f.orb.setFeedback({busy:active,unread:!active});f.orb.setTaskActivity(activity({eventId:'drag:'+active,notice:'后台已返回'}));
      assert.equal(f.orb.get().dragging,true);assert.deepEqual(f.orb.get().bounds,bounds);assert.deepEqual(f.orb.getAnchor(),anchor);assertCenterOnly(f.orb.get());
    }
    assert.equal(f.orb.window.boundsCalls.length,moves);f.screen.cursor={x:202,y:658};await f.send({kind:'drag',phase:'move'});
    assert.equal(f.orb.getAnchor().x,202);assert.equal(f.orb.getAnchor().y,658);await f.send({kind:'drag',phase:'end'});assert.equal(f.orb.get().dragging,false);
    assert.deepEqual(f.calls,[]);
  }finally{f.orb.dispose();}
});

test('arc targets follow a common nearby radius, never overlap, and turn inward at every edge and corner',()=>{
  for(const area of [{x:0,y:0,width:1280,height:860},{x:-1600,y:-240,width:1600,height:1000}]){
    for(const x of [area.x,area.x+(area.width-56)/2,area.x+area.width-56])for(const y of [area.y,area.y+(area.height-56)/2,area.y+area.height-56]){
      const bubble=clampOrb({x,y},area),arc=arcLayout(bubble,area,3);assert.ok(arc);
      assert.ok(arc.bounds.width<=(arc.offsets.length===5?320:280)&&arc.bounds.height<=(arc.offsets.length===5?320:280));assert.ok(arc.bounds.x>=area.x&&arc.bounds.y>=area.y);
      assert.ok(arc.bounds.x+arc.bounds.width<=area.x+area.width&&arc.bounds.y+arc.bounds.height<=area.y+area.height);
      assert.equal(arc.offsets.length,3);assert.ok(arc.sweepDegrees>=76&&arc.sweepDegrees<=100);
      for(const offset of arc.offsets){
        assert.ok(Math.abs(Math.hypot(offset.dx,offset.dy)-arc.radius)<1);
        const cx=bubble.x+28+offset.dx,cy=bubble.y+28+offset.dy;
        assert.ok(cx-28>=arc.bounds.x-1e-9&&cx+28<=arc.bounds.x+arc.bounds.width+1e-9);
        assert.ok(cy-28>=arc.bounds.y-1e-9&&cy+28<=arc.bounds.y+arc.bounds.height+1e-9);
      }
      for(let i=0;i<3;i++)for(let j=i+1;j<3;j++){
        const a=arc.offsets[i],b=arc.offsets[j];
        assert.ok(Math.hypot(a.dx-b.dx,a.dy-b.dy)>=57.75,'56 DIP circular hit regions remain disjoint');
      }
    }
  }
  const area={x:0,y:0,width:56,height:56},bubble=clampOrb({x:0,y:0},area);
  assert.equal(arcLayout(bubble,area),null);assert.deepEqual(expandedBounds(bubble,area),bubble);
  for(const count of [4,5]){const arc=arcLayout({x:484,y:356,width:56,height:56},{x:0,y:0,width:1024,height:768},count);assert.equal(arc.offsets.length,count);assert.ok(arc.bounds.width<=(arc.offsets.length===5?320:280)&&arc.bounds.height<=(arc.offsets.length===5?320:280));}
  assert.throws(()=>arcLayout(bubble,area,6));
});

test('four real children and future five stay reachable with disjoint circular hit regions at every edge and corner',()=>{
  for(const count of [4,5])for(const area of [{x:0,y:0,width:1280,height:860},{x:-1600,y:-240,width:1600,height:1000},{x:-300,y:0,width:300,height:300}]){
    for(const x of [area.x,area.x+(area.width-56)/2,area.x+area.width-56])for(const y of [area.y,area.y+(area.height-56)/2,area.y+area.height-56]){
      const bubble=clampOrb({x,y},area),before={...bubble},arc=arcLayout(bubble,area,count);assert.ok(arc,`${count} choices must fit this work area`);
      assert.deepEqual(bubble,before);assert.equal(arc.offsets.length,count);for(const offset of arc.offsets)assert.ok(Math.abs(Math.hypot(offset.dx,offset.dy)-arc.radius)<1e-9,'every node shares the actual orb circle');
      assert.ok(arc.bounds.x>=area.x&&arc.bounds.y>=area.y&&arc.bounds.x+arc.bounds.width<=area.x+area.width&&arc.bounds.y+arc.bounds.height<=area.y+area.height);
      assert.ok(arc.bounds.width<=(arc.offsets.length===5?320:280)&&arc.bounds.height<=(arc.offsets.length===5?320:280));
      for(const [index,a]of arc.offsets.entries()){
        assert.ok(Math.hypot(a.dx,a.dy)<=(count===4?112:152)+1e-9,'one nearby circle keeps its original orb center');
        assert.ok(Math.hypot(a.dx,a.dy)>=54,'circles do not overlap the center orb');
        const cx=bubble.x+28+a.dx,cy=bubble.y+28+a.dy;
        assert.ok(cx-28>=arc.bounds.x-1e-9&&cx+28<=arc.bounds.x+arc.bounds.width+1e-9&&cy-28>=arc.bounds.y-1e-9&&cy+28<=arc.bounds.y+arc.bounds.height+1e-9);
        for(const b of arc.offsets.slice(index+1))assert.ok(Math.hypot(a.dx-b.dx,a.dy-b.dy)>=57.75,'circular hit regions keep a real gap');
      }
    }
  }
  const corner=arcLayout({x:0,y:0,width:56,height:56},{x:0,y:0,width:800,height:600},4);
  assert.equal(corner.variableRadius,undefined);assert.equal(corner.radius,112);assert.equal(corner.bounds.width,168);assert.equal(corner.bounds.height,168);
  assert.match(orbHtml(),/translate:var\(--dx,0px\) var\(--dy,0px\);transform:scale\(\.84\)/,'opening scales circles at their fixed final positions');
});

test('public anchor stays at the global orb center while the arc opens and host callbacks receive a detached copy',async()=>{
  const delivered=[];const f=fixture({onAction:async(action,anchor)=>delivered.push({action,anchor})});try{
    await f.orb.setHostMinimized(true);const anchor=f.orb.getAnchor();
    assert.deepEqual(anchor,{x:52,y:808,orbBounds:{x:24,y:780,width:56,height:56}});
    await f.send({kind:'hover',inside:true});assert.deepEqual(f.orb.getAnchor(),anchor);assert.deepEqual(f.orb.get().anchor,anchor);
    await f.send({kind:'action',action:'annotate'});assert.deepEqual(delivered,[{action:'annotate',anchor}]);
    delivered[0].anchor.orbBounds.x=9000;assert.deepEqual(f.orb.getAnchor(),anchor);
    assert.deepEqual(f.orb.get().actions,['ask','show-agent','workbench','annotate','tasks']);
  }finally{f.orb.dispose();}
});

test('transient resize leave and travel between orb and arc never collapse under the actual cursor',async()=>{
  const f=fixture();try{
    await f.orb.setHostMinimized(true);await f.send({kind:'hover',inside:true});const state=f.orb.get();
    const item=state.layout.actions[0],anchor=f.orb.getAnchor(),bounds=f.orb.get().bounds;
    f.screen.cursor={x:anchor.x+item.dx/2,y:anchor.y+item.dy/2};
    await f.send({kind:'hover',inside:false});await pause(170);assert.equal(f.orb.get().expanded,true);
    f.screen.cursor={x:900,y:100};await f.send({kind:'hover',inside:false});
    await pause(40);await f.send({kind:'hover',inside:true});await pause(170);assert.equal(f.orb.get().expanded,true);
    await f.send({kind:'hover',inside:false});await pause(170);assert.equal(f.orb.get().expanded,false);
    await pause(250);assert.deepEqual(f.orb.get().bounds,bounds);assertCenterOnly(f.orb.get());
  }finally{f.orb.dispose();}
});

function mountPreload(f){
  class Node {
    constructor(){this.dataset={};this.listeners=new Map();this.attrs={};this.captured=new Set();this.disabled=false;this.style={setProperty:(key,value)=>this.attrs[key]=value};}
    addEventListener(name,listener){this.listeners.set(name,[...this.listeners.get(name)??[],listener]);}
    setAttribute(key,value){this.attrs[key]=value;}
    setPointerCapture(id){this.captured.add(id);}hasPointerCapture(id){return this.captured.has(id);}releasePointerCapture(id){this.captured.delete(id);}
    fire(name,event={}){for(const listener of this.listeners.get(name)??[])listener({type:name,preventDefault(){},...event});}
  }
  const stage=new Node(),orb=new Node(),guide=new Node(),notice=new Node(),noticeText=new Node();
  const buttons=['annotate','tasks','show-agent','workbench'].map(action=>{const button=new Node();button.dataset.action=action;return button;});
  stage.querySelector=selector=>selector==='#arc-guide'?guide:buttons.find(button=>selector===`[data-action="${button.dataset.action}"]`);
  stage.querySelectorAll=()=>buttons;
  const listeners=new Map(),messages=[];let render;
  f.orb.window.webContents.send=(channel,state)=>{f.orb.window.webContents.sends.push([channel,state]);if(channel===STATE_CHANNEL)render?.({},state);};
  const ipcRenderer={on:(_channel,listener)=>{render=listener;},invoke:(_channel,payload)=>{messages.push(payload);return f.send(payload);}};
  const initialTitle=decodeURIComponent(f.orb.pageURL.slice(f.orb.pageURL.indexOf(',')+1)).match(/<title>([^<]*)<\/title>/)[1];
  const document={title:initialTitle,body:{dataset:{i18n:JSON.stringify(require('../../../packages/i18n/host.json').messages)}},querySelectorAll:()=>[],querySelector:selector=>selector==='.stage'?stage:selector==='#orb'?orb:selector==='#task-notice'?notice:selector==='#task-notice-text'?noticeText:null};
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../assistant-orb-preload.cjs'),'utf8'),{
    require:name=>{assert.equal(name,'electron');return {ipcRenderer};},setTimeout,
    window:{addEventListener:(name,listener)=>listeners.set(name,listener)},
    document
  });
  listeners.get('DOMContentLoaded')();return {stage,orb,guide,buttons,notice,noticeText,messages,listeners,document};
}

test('actual preload keeps the hover path open, updates the arc, and delegates one real child action',async()=>{
  const f=fixture();try{
    await f.orb.setHostMinimized(true);const ui=mountPreload(f);await pause(0);
    f.screen.cursor={x:52,y:808};ui.stage.fire('pointerenter');await pause(0);
    assert.equal(ui.stage.dataset.expanded,'true');assert.equal(ui.guide.attrs.d,'');assert.equal(f.orb.get().layout.actions.length,4);
    const state=f.orb.get(),item=state.layout.actions[0];f.screen.cursor={x:52+item.dx/2,y:808+item.dy/2};
    ui.stage.fire('pointerleave');await pause(170);assert.equal(f.orb.get().expanded,true);
    ui.stage.fire('pointerenter');ui.buttons[0].fire('click');await pause(0);
    assert.deepEqual(f.calls,['annotate']);assert.equal(f.orb.get().visible,true);
    ui.stage.fire('pointermove');await pause(0);assert.equal(f.orb.get().expanded,true,'movement can re-open after a completed action without leaving the orb');
  }finally{f.orb.dispose();}
});

test('actual tasks child delegates once, retains the orb for switching, and obeys explicit preference',async()=>{
  const f=fixture();try{
    await f.orb.setHostMinimized(true);f.orb.setSurfaceActive(true);const ui=mountPreload(f);await pause(0);
    ui.stage.fire('pointerenter');await pause(0);const before=f.orb.getAnchor();
    const tasks=ui.buttons.find(button=>button.dataset.action==='tasks');assert.ok(tasks);tasks.fire('click');await pause(0);
    assert.deepEqual(f.calls,['tasks']);assert.equal(f.orb.get().visible,true);assert.deepEqual(f.orb.getAnchor(),before);
    ui.stage.fire('pointermove');await pause(0);assert.equal(f.orb.get().expanded,true);
    await f.orb.setEnabled(false);assert.equal(f.orb.get().visible,false);assert.equal(f.orb.get().expanded,false);
    await assert.rejects(f.send({kind:'action',action:'tasks'}),/not active/);
    await f.orb.setEnabled(true);assert.equal(f.orb.get().visible,true);
  }finally{f.orb.dispose();}
});

test('actual preload drag, pointer cancellation and delayed hover cleanup never dispatch a center ask',async()=>{
  const f=fixture();try{
    await f.orb.setHostMinimized(true);const ui=mountPreload(f);await pause(0);
    ui.orb.fire('pointerdown',{button:0,pointerId:7,screenX:52,screenY:808});
    f.screen.cursor={x:72,y:788};ui.orb.fire('pointermove',{pointerId:7,screenX:72,screenY:788});
    f.screen.cursor={x:92,y:768};ui.orb.fire('pointermove',{pointerId:7,screenX:92,screenY:768});
    ui.orb.fire('pointerup',{pointerId:7,screenX:92,screenY:768});await pause(0);
    assert.equal(f.orb.get().dragging,false);assert.deepEqual(f.calls,[]);
    assert.deepEqual(ui.messages.filter(message=>message.kind==='drag').map(message=>message.phase),['start','move','move','end']);
    ui.orb.fire('pointerdown',{button:0,pointerId:8,screenX:92,screenY:768});ui.orb.fire('pointercancel',{pointerId:8});await pause(0);
    assert.deepEqual(f.calls,[]);
    await f.send({kind:'hover',inside:true});await f.send({kind:'hover',inside:false});
    f.orb.dispose();await pause(170);assert.equal(f.orb.window.destroyCalls,1);
  }finally{f.orb.dispose();}
});

function fakeClock(){
  let time=0,id=0;const pending=new Map(),callbacks=[];
  return {now:()=>time,setTimeout(callback,delay){const key=++id;pending.set(key,{at:time+delay,callback});callbacks.push(callback);return key;},
    clearTimeout:key=>pending.delete(key),pending,callbacks,
    advance(ms){const end=time+ms;for(;;){const next=[...pending].sort((a,b)=>a[1].at-b[1].at)[0];if(!next||next[1].at>end)break;time=next[1].at;pending.delete(next[0]);next[1].callback();}time=end;}};
}
const activity=(delta={})=>({running:0,attention:0,unread:1,urgency:'complete',...delta});

test('first task snapshot is quiet; only exact new events show for at most 4500ms and same event never reappears',async()=>{
  const clock=fakeClock(),f=fixture({clock});try{
    await f.orb.setHostMinimized(true);const anchor=f.orb.getAnchor(),small=f.orb.get().bounds;
    f.orb.setTaskActivity(activity({running:2,notice:'后台任务已返回'}));
    assert.equal(f.orb.get().notice.visible,false);assert.deepEqual(f.orb.get().bounds,small);assert.equal(clock.pending.size,0);
    f.orb.setTaskActivity(activity({eventId:'evt:1',notice:'后台任务已返回'}));
    assert.equal(f.orb.get().notice.visible,true);assert.deepEqual(f.orb.getAnchor(),anchor);
    clock.advance(4499);assert.equal(f.orb.get().notice.visible,true);
    clock.advance(1);assert.equal(f.orb.get().notice.visible,false);assert.deepEqual(f.orb.get().bounds,small);
    f.orb.setTaskActivity(activity({eventId:'evt:1',notice:'重复轮询不会弹出'}));assert.equal(f.orb.get().notice.visible,false);
    const state=f.orb.get();state.taskActivity.running=100;assert.equal(f.orb.get().taskActivity.running,0);
    assert.deepEqual(f.calls,[]);assert.equal(f.orb.window.options.focusable,false);
  }finally{f.orb.dispose();}
});

test('new event replaces the short notice, acknowledgement clears it, and malformed activity cannot mutate state',async()=>{
  const clock=fakeClock(),f=fixture({clock});try{
    await f.orb.setHostMinimized(true);
    f.orb.setTaskActivity(activity({eventId:'evt:1',notice:'第一项已返回'}));clock.advance(2000);
    f.orb.setTaskActivity(activity({eventId:'evt:2',notice:'两项需要检查',unread:2,attention:2,urgency:'waiting'}));
    assert.equal(f.orb.get().notice.text,'两项需要检查');assert.equal(clock.pending.size,1);
    clock.advance(2500);assert.equal(f.orb.get().notice.visible,true,'old deadline cannot clear the replacement');
    const before=f.orb.get();
    for(const delta of [{running:-1},{unread:Infinity},{attention:1.5},{urgency:'timeout'},{eventId:'file:///anything'},{notice:'x'.repeat(81)},{notice:'line\nbreak'},{extra:true}])assert.throws(()=>f.orb.setTaskActivity(activity(delta)),/safe task activity/);
    assert.deepEqual(f.orb.get(),before);
    f.orb.setTaskActivity(activity({unread:0,attention:0,urgency:'none',eventId:'evt:2'}));
    assert.equal(f.orb.get().notice.visible,false);assert.equal(clock.pending.size,0);
  }finally{f.orb.dispose();}
});

test('notice and arc are mutually exclusive; busy and drag defer only still-current notice without moving the center',async()=>{
  const clock=fakeClock();let release;
  const f=fixture({clock,onAction:()=>new Promise(resolve=>release=resolve)});try{
    await f.orb.setHostMinimized(true);const anchor=f.orb.getAnchor();
    await f.send({kind:'hover',inside:true});f.orb.setTaskActivity(activity({eventId:'evt:arc',notice:'稍后可检查'}));
    assert.equal(f.orb.get().expanded,true);assert.equal(f.orb.get().notice.visible,false);assert.equal(f.orb.get().layout.notice,null);
    await f.send({kind:'dismiss'});clock.advance(220);assert.equal(f.orb.get().notice.visible,false,'explicit dismiss clears the pending notice');
    f.orb.setFeedback({busy:true,unread:false});f.orb.setTaskActivity(activity({eventId:'evt:processing',notice:'处理中收到结果'}));
    assert.equal(f.orb.get().notice.visible,true,'another running question must not hide an independent task result');clock.advance(4500);f.orb.setFeedback({busy:false,unread:false});assert.equal(f.orb.get().notice.visible,false);
    const sent=f.send({kind:'action',action:'ask'});f.orb.setTaskActivity(activity({eventId:'evt:busy',notice:'另一项返回'}));
    assert.equal(f.orb.get().notice.visible,false);clock.advance(100);release();await sent;assert.equal(f.orb.get().notice.visible,true);
    await f.send({kind:'drag',phase:'start'});assert.equal(f.orb.get().notice.visible,false);assertCenterOnly(f.orb.get());
    await f.send({kind:'drag',phase:'end'});assert.equal(f.orb.get().notice.visible,true);
    assert.deepEqual(f.orb.getAnchor(),anchor);assert.deepEqual(f.calls,[]);
  }finally{f.orb.dispose();}
});

test('hide, disable and closing fence all notice timers including queued late callbacks',async()=>{
  for(const transition of ['disabled','host','closing','dispose']){
    const clock=fakeClock();let closing=false;const f=fixture({clock,isClosing:()=>closing});
    try{
      await f.orb.setHostMinimized(true);f.orb.setTaskActivity(activity({eventId:'evt:hidden',notice:'待检查'}));
      const late=clock.callbacks.at(-1);
      if(transition==='disabled')await f.orb.setEnabled(false);
      if(transition==='host')await f.orb.setHostMinimized(false);
      if(transition==='closing'){closing=true;f.orb.setTaskActivity(activity({eventId:'evt:closing',notice:'关闭中'}));}
      if(transition==='dispose')f.orb.dispose();
      assert.equal(clock.pending.size,0,transition);assert.equal(f.orb.get().notice.visible,false);assert.equal(f.orb.get().visible,false);
      const calls=f.orb.window.boundsCalls.length;late();clock.advance(10000);assert.equal(f.orb.window.boundsCalls.length,calls,'late callback cannot resize/reveal');
      assert.deepEqual(f.calls,[]);
    }finally{f.orb.dispose();}
  }
});

test('notice layout stays inside negative-origin and edge displays, never intersects orb, and leaves arc geometry unchanged',()=>{
  for(const area of [{x:0,y:0,width:1280,height:860},{x:-1600,y:-240,width:1600,height:1000},{x:-300,y:0,width:300,height:300}]){
    for(const x of [area.x,area.x+(area.width-56)/2,area.x+area.width-56])for(const y of [area.y,area.y+(area.height-56)/2,area.y+area.height-56]){
      const bubble=clampOrb({x,y},area),before={...bubble},layout=noticeLayout(bubble,area);assert.ok(layout);
      const b=layout.bounds,p=layout.panel;assert.deepEqual(bubble,before);
      assert.ok(b.x>=area.x&&b.y>=area.y&&b.x+b.width<=area.x+area.width&&b.y+b.height<=area.y+area.height);
      const px=b.x+p.x,py=b.y+p.y;
      assert.ok(px+p.width<=bubble.x||px>=bubble.x+56||py+p.height<=bubble.y||py>=bubble.y+56,'full notice rectangle does not obscure the orb');
      assert.ok(p.width>=44&&p.height>=44);const arc=arcLayout(bubble,area,4);assert.ok(arc);assert.ok(Math.max(...arc.offsets.map(o=>Math.hypot(o.dx,o.dy)))<=200+1e-9);
    }
  }
  assert.equal(noticeLayout({x:0,y:0,width:56,height:56},{x:0,y:0,width:56,height:56}),null);
});

test('real preload renders truthful ambient severity and safe text, then notice click delegates only in-place tasks',async()=>{
  const clock=fakeClock(),f=fixture({clock});try{
    await f.orb.setHostMinimized(true);const ui=mountPreload(f);await pause(0);
    f.orb.setTaskActivity(activity({running:3,unread:0,urgency:'error'}));assert.equal(ui.stage.dataset.taskUrgency,'none');assert.equal(ui.stage.dataset.unread,'false');
    f.orb.setTaskActivity(activity({attention:1,urgency:'waiting'}));assert.equal(ui.stage.dataset.taskUrgency,'waiting');
    f.orb.setTaskActivity(activity({attention:1,urgency:'error',eventId:'evt:error',notice:'<img src=private> 任务需要检查'}));
    assert.equal(ui.stage.dataset.taskUrgency,'error');assert.equal(ui.notice.hidden,false);assert.equal(ui.noticeText.textContent,'<img src=private> 任务需要检查');assert.equal(ui.noticeText.innerHTML,undefined);
    ui.stage.fire('pointerenter');ui.stage.fire('pointermove',{target:ui.notice});await pause(0);assert.equal(f.orb.get().expanded,false,'notice hover does not accidentally replace it with the arc');
    ui.notice.fire('click');await pause(0);assert.deepEqual(f.calls,['tasks']);assert.equal(f.orb.get().visible,true);assert.equal(f.orb.get().notice.visible,false);
    f.orb.setTaskActivity(activity({eventId:'evt:complete',notice:'已返回，请检查结果'}));assert.equal(ui.stage.dataset.taskUrgency,'none');assert.equal(ui.stage.dataset.unread,'true');
    ui.orb.fire('pointerenter');await pause(0);assert.equal(f.orb.get().expanded,true);assert.equal(ui.notice.hidden,true);assert.equal(ui.buttons.length,4);
    await f.orb.setHostMinimized(false);assert.equal(ui.notice.hidden,true);assert.equal(f.orb.get().visible,false);
  }finally{f.orb.dispose();}
});

test('the selected three-part canonical SVG is the exact orb face without extra outline or a second asset',async()=>{
  const brand=require('../product-brand.cjs'),{orb}=fixture();try{
    assert.equal(orb.window.options.title,brand.WINDOW_TITLES.orb);assert.match(orbHtml(),new RegExp('<title>'+brand.WINDOW_TITLES.orb+'</title>'));
    const html=orbHtml();assert.ok(html.includes(brand.ICON_SVG.replace('<svg ','<svg class="brand-mark" aria-hidden="true" ')));
    assert.deepEqual([...brand.ICON_SVG.matchAll(/data-motion-part="([^"]+)"/g)].map(match=>match[1]),['outer-C','inner-fold','lower-sweep']);
    assert.match(html,/\.orb \.brand-mark\{width:32px;height:32px;stroke:none;/);
    assert.doesNotMatch(html,/\.orb svg\{[^}]*stroke:/,'the old global stroke must not distort a filled canonical mark');
    assert.equal(CHANNEL,'eeas:assistant-orb:event');assert.equal(STATE_CHANNEL,'eeas:assistant-orb:state');assert.equal(ACTIONS.length,5);
  }finally{orb.dispose();}
});

test('real preload press/cancel and rapidly reversed menu motion keep the same anchor without duplicate asks or late shrinking',async()=>{
  const clock=fakeClock(),f=fixture({clock});try{
    await f.orb.setHostMinimized(true);const ui=mountPreload(f);await pause(0);const anchor=f.orb.getAnchor();
    ui.orb.fire('pointerdown',{button:0,pointerId:21,screenX:anchor.x,screenY:anchor.y});assert.equal(ui.orb.dataset.pressed,'true');
    ui.orb.fire('pointerup',{pointerId:21});ui.orb.fire('click',{detail:1});await pause(0);
    assert.equal(ui.orb.dataset.pressed,'false');assert.deepEqual(f.calls,['ask']);assert.deepEqual(f.orb.getAnchor(),anchor);
    ui.orb.fire('pointerdown',{button:0,pointerId:22,screenX:anchor.x,screenY:anchor.y});ui.orb.fire('pointercancel',{pointerId:22});await pause(0);
    assert.equal(ui.orb.dataset.pressed,'false');assert.deepEqual(f.calls,['ask']);
    await f.send({kind:'hover',inside:true});const bounds=f.orb.get().bounds;
    await f.send({kind:'dismiss'});clock.advance(100);await f.send({kind:'hover',inside:true});clock.advance(500);
    assert.equal(ui.stage.dataset.expanded,'true');assert.deepEqual(f.orb.get().bounds,bounds);assert.deepEqual(f.orb.getAnchor(),anchor);
    f.screen.cursor={x:900,y:100};await f.send({kind:'hover',inside:false});clock.advance(140);assert.equal(ui.stage.dataset.expanded,'false');
    await f.send({kind:'hover',inside:true});clock.advance(500);assert.equal(ui.stage.dataset.expanded,'true');assert.deepEqual(f.orb.get().bounds,bounds);
    assert.deepEqual(f.calls,['ask']);assert.deepEqual(f.orb.getAnchor(),anchor);assert.equal(f.orb.window.options.focusable,false);
  }finally{f.orb.dispose();}
});

test('live orb language updates accessible descriptions without native tooltips, moving, focusing or translating task labels',async()=>{
  const f=fixture();try{
    await f.orb.setHostMinimized(true);const ui=mountPreload(f);await pause(0);
    f.orb.setTaskActivity({running:2,attention:1,unread:1,urgency:'waiting',notice:'发送',eventId:'locale-notice'});
    await pause(0);const before=f.orb.getAnchor(),own=f.orb.window,count=ui.messages.length;
    f.orb.setLocale('en');assert.equal(f.orb.get().locale,'en');assert.equal(own.title,'Cirven quick access');
    assert.match(ui.orb.attrs['aria-description'],/running/);assert.doesNotMatch(ui.orb.attrs['aria-description'],/[一-龥]/);assert.match(ui.orb.attrs['aria-label'],/running/);assert.equal(f.orb.get().taskActivity.notice,'发送');
    assert.equal(ui.orb.title,undefined);assert.equal(ui.orb.attrs.title,undefined);
    assert.equal(f.orb.window,own);assert.deepEqual(f.orb.getAnchor(),before);assert.equal(ui.messages.length,count);assert.deepEqual(f.calls,[]);
    f.orb.setLocale('zh-CN');assert.match(ui.orb.attrs['aria-description'],/运行中/);assert.match(ui.orb.attrs['aria-label'],/运行中/);assert.equal(ui.orb.title,undefined);assert.deepEqual(f.calls,[]);
  }finally{f.orb.dispose();}
});

test('center and four actions have accessible labels without native tooltip attributes and errors remain described',async()=>{
  for(const locale of ['zh-CN','en']){
    const html=orbHtml(locale),buttons=[...html.matchAll(/<button\b[^>]*>/g)].map(match=>match[0]);
    const controls=buttons.filter(tag=>/class="(?:orb|action)"/.test(tag));assert.equal(controls.length,5);
    for(const tag of controls){assert.match(tag,/aria-label="[^"]+"/);assert.doesNotMatch(tag,/\s(?:title|data-ui-title)=/);}
  }
  const f=fixture({onAction:async()=>{throw new Error('CONTROLLED_ACTION_FAILURE');}});try{
    await f.orb.setHostMinimized(true);const ui=mountPreload(f);await pause(0);
    assert.ok(ui.orb.attrs['aria-description']);ui.orb.fire('click',{detail:0});await pause(0);
    assert.ok(f.orb.get().error);assert.equal(ui.orb.attrs['aria-description'],f.orb.get().error);
    assert.match(ui.orb.attrs['aria-label'],/询问当前页面/);assert.equal(ui.orb.title,undefined);assert.equal(ui.orb.attrs.title,undefined);
  }finally{f.orb.dispose();}
});

test('actual document title follows the common catalog through repeated language switches without reload or losing an active action',async()=>{
  let release;const actions=[];
  const f=fixture({onAction:action=>{actions.push(action);return action==='ask'?new Promise(resolve=>release=resolve):Promise.resolve();}});
  try{
    await f.orb.setHostMinimized(true);const ui=mountPreload(f);await pause(0);
    const {t}=require('../../../packages/i18n/index.cjs'),{SHORT_NAME}=require('../product-brand.cjs');
    assert.equal(ui.document.title,t('common','window.orb','zh-CN',{short_name:SHORT_NAME}));
    f.orb.setTaskActivity({running:2,attention:1,unread:1,urgency:'waiting',notice:'发送',eventId:'title-locale-notice'});
    const running=f.send({kind:'action',action:'ask'}),own=f.orb.window,anchor=f.orb.getAnchor(),url=own.webContents.url,loads=own.loadCalls,messageCount=ui.messages.length;
    for(const locale of ['en','zh-CN','en','zh-CN','en']){
      f.orb.setLocale(locale);const title=t('common','window.orb',locale,{short_name:SHORT_NAME});
      assert.equal(ui.document.title,title);assert.equal(own.title,title);assert.equal(f.orb.get().documentTitle,title);
      assert.equal(f.orb.get().busy,true);assert.equal(ui.stage.dataset.busy,'true');assert.equal(f.orb.get().taskActivity.notice,'发送');
      assert.deepEqual(f.orb.getAnchor(),anchor);assert.equal(f.orb.window,own);assert.equal(own.webContents.url,url);assert.equal(own.loadCalls,loads);
      assert.equal(ui.messages.length,messageCount);assert.deepEqual(actions,['ask']);
    }
    release();await running;ui.buttons.find(button=>button.dataset.action==='tasks').fire('click');await pause(0);
    assert.deepEqual(actions,['ask','tasks']);assert.equal(f.orb.get().busy,false);assert.equal(f.orb.get().visible,true);assert.equal(ui.document.title,own.title);
  }finally{release?.();f.orb.dispose();}
});

test('the real four-action guide and targets share the unchanged global orb center through expand, switch and collapse',async()=>{
  const f=fixture();try{
    await f.orb.setHostMinimized(true);const ui=mountPreload(f);await pause(0);
    for(const point of [{x:0,y:0},{x:1224,y:804},{x:612,y:402}]){
      f.orb.bubble=clampOrb(point,f.screen.area);f.orb._syncVisibility();const anchor=f.orb.getAnchor();
      for(const action of ['annotate','tasks','show-agent','workbench']){
        await f.send({kind:'hover',inside:true});const state=f.orb.get();
        assert.equal(state.bounds.x+state.layout.orb.x+24,anchor.x);assert.equal(state.bounds.y+state.layout.orb.y+24,anchor.y);
        assert.equal(state.layout.guide,'','transparent gaps have no decorative hit band');
        for(const item of state.layout.actions)assert.ok(Math.abs(Math.hypot(item.dx,item.dy)-state.layout.radius)<1e-9);
        await f.send({kind:'action',action});assert.deepEqual(f.orb.getAnchor(),anchor);assert.equal(f.orb.get().visible,true);
      }
      ui.listeners.get('keydown')({key:'Escape'});await pause(0);assert.equal(f.orb.get().expanded,false);assert.deepEqual(f.orb.getAnchor(),anchor);
    }
    assert.equal(f.calls.length,12);assert.equal(f.orb.window.destroyCalls,0);
  }finally{f.orb.dispose();}
});

test('same-circle fallback handles fractional-DIP/negative-origin work areas without guessing DPI or moving the orb',()=>{
  for(const area of [{x:-1536.25,y:-128.5,width:1536.5,height:864.25},{x:0,y:0,width:800,height:600}]){
    for(let row=0;row<=8;row++)for(let column=0;column<=10;column++){
      const bubble=clampOrb({x:area.x+(area.width-56)*column/10,y:area.y+(area.height-56)*row/8},area),before={...bubble};
      const arc=arcLayout(bubble,area,4);assert.ok(arc);assert.deepEqual(bubble,before);assert.ok(arc.radius<=112);
      for(const a of arc.offsets){assert.ok(Math.abs(Math.hypot(a.dx,a.dy)-arc.radius)<1e-9);for(const b of arc.offsets)if(a!==b)assert.ok(Math.hypot(a.dx-b.dx,a.dy-b.dy)>=57.75);}
    }
  }
});

test('all selected arcs have equal angle gaps including four/five-item rotated corner fallbacks',()=>{
  const seenCorners=new Set();
  for(const count of [3,4,5])for(const area of [{x:0,y:0,width:1280,height:860},
      {x:-1536.25,y:-128.5,width:1536.5,height:864.25},{x:-380,y:17,width:380,height:380}]){
    const normalized={x:Math.round(area.x),y:Math.round(area.y),width:Math.floor(area.width),height:Math.floor(area.height)};
    const positions=area.width===380?[0,1]:[0,.25,.5,.75,1];
    for(const column of positions)for(const row of positions){
      const bubble=clampOrb({x:normalized.x+(normalized.width-56)*column,
        y:normalized.y+(normalized.height-56)*row},area),before={...bubble};
      const arc=arcLayout(bubble,area,count);assert.ok(arc,`${count} choices at ${column},${row}`);
      assert.deepEqual(bubble,before);const angles=arc.offsets.map(item=>Math.atan2(item.dy,item.dx));
      const expected=arc.sweepDegrees*Math.PI/(180*(count-1));
      for(let index=1;index<angles.length;index++){
        const difference=(angles[index]-angles[index-1]+2*Math.PI)%(2*Math.PI);
        assert.ok(Math.abs(difference-expected)<1e-10,'all adjacent angles match the selected uniform sweep');
      }
      assert.ok(arc.bounds.width<=(arc.offsets.length===5?320:280)&&arc.bounds.height<=(arc.offsets.length===5?320:280));
      assert.ok(arc.bounds.x>=normalized.x&&arc.bounds.y>=normalized.y);
      assert.ok(arc.bounds.x+arc.bounds.width<=normalized.x+normalized.width);
      assert.ok(arc.bounds.y+arc.bounds.height<=normalized.y+normalized.height);
      if(count>=4&&[0,1].includes(column)&&[0,1].includes(row)){
        seenCorners.add(`${count}:${arc.direction}`);assert.equal(arc.sweepDegrees,90);
        assert.equal(arc.radius,count===4?112:152);
        for(const [index,a]of arc.offsets.entries())for(const b of arc.offsets.slice(index+1)){
          const separation=Math.hypot(a.dx-b.dx,a.dy-b.dy);
          assert.ok(separation>=57.75,'true circles stay separated despite overlapping empty rectangle corners');
        }
      }
    }
  }
  assert.deepEqual([...seenCorners].sort(),[4,5].flatMap(count=>['NE','NW','SE','SW'].map(direction=>`${count}:${direction}`)).sort());
});

test('Electron DIP layout stays stable across display scale metadata while opening the real corner menu',async()=>{
  let baseline;
  for(const scaleFactor of [1,1.25,1.5,2,2.24]){
    const f=fixture();try{
      f.screen.area={x:-1536,y:-128,width:1536,height:864};
      f.screen.getDisplayNearestPoint=()=>({workArea:{...f.screen.area},scaleFactor});
      f.orb.bubble=clampOrb({x:-1536,y:-128},f.screen.area);
      await f.orb.setHostMinimized(true);const anchor=f.orb.getAnchor();
      await f.send({kind:'hover',inside:true});const state=f.orb.get();
      assert.deepEqual(f.orb.getAnchor(),anchor);assert.equal(state.layout.radius,112);
      const actual={bounds:state.bounds,anchor:state.anchor,layout:state.layout};
      if(baseline)assert.deepEqual(actual,baseline,'DIP coordinates must not be multiplied by a global DPI factor');
      else baseline=actual;
      await f.send({kind:'action',action:'tasks'});assert.deepEqual(f.calls,['tasks']);
      assert.deepEqual(f.orb.getAnchor(),anchor);assert.equal(f.orb.get().visible,true);
    }finally{f.orb.dispose();}
  }
});

test('four compact 56-DIP targets fit throughout a 380-DIP work area without shifting their center',()=>{
  const area={x:-380,y:-40,width:380,height:720},before={...area};
  for(let row=0;row<=10;row++)for(let column=0;column<=10;column++){
    const bubble=clampOrb({x:area.x+(area.width-56)*column/10,y:area.y+(area.height-56)*row/10},area);
    const arc=arcLayout(bubble,area,4);assert.ok(arc,`380 DIP placement ${column},${row}`);
    assert.ok(arc.bounds.width<=280&&arc.bounds.height<=280);
    for(const [index,a]of arc.offsets.entries()){
      assert.ok(Math.abs(Math.hypot(a.dx,a.dy)-arc.radius)<1e-9);
      assert.ok(Math.hypot(a.dx,a.dy)>=54,'56px circle keeps clear of the center 48px target');
      for(const b of arc.offsets.slice(index+1))assert.ok(Math.hypot(a.dx-b.dx,a.dy-b.dy)>=57.75);
      const cx=bubble.x+28+a.dx,cy=bubble.y+28+a.dy;
      assert.ok(cx-28>=arc.bounds.x-1e-9&&cx+28<=arc.bounds.x+arc.bounds.width+1e-9);
      assert.ok(cy-28>=arc.bounds.y-1e-9&&cy+28<=arc.bounds.y+arc.bounds.height+1e-9);
    }
    const step=arc.sweepDegrees*Math.PI/540;
    for(let i=1;i<4;i++){
      const a=arc.offsets[i-1],b=arc.offsets[i];
      assert.ok(Math.abs((Math.atan2(b.dy,b.dx)-Math.atan2(a.dy,a.dx)+2*Math.PI)%(2*Math.PI)-step)<1e-10);
    }
  }
  assert.deepEqual(area,before);
  assert.match(orbHtml(),/width:56px;height:56px;min-width:56px/);
});


test('compact circular targets allow empty rectangle corners to overlap while keeping actual hit regions apart',()=>{
  const area={x:0,y:0,width:380,height:720},corner=arcLayout({x:0,y:0,width:56,height:56},area,4);
  assert.equal(corner.radius,112);assert.equal(corner.sweepDegrees,90);
  assert.equal(corner.bounds.width,168);assert.equal(corner.bounds.height,168);
  assert.ok(corner.offsets.some((a,i)=>corner.offsets.slice(i+1).some(b=>Math.abs(a.dx-b.dx)<56&&Math.abs(a.dy-b.dy)<56)),'a rectangle-only guard would wrongly reject the compact arc');
  for(const[a,b]of corner.offsets.slice(1).map((b,i)=>[corner.offsets[i],b]))assert.ok(Math.hypot(a.dx-b.dx,a.dy-b.dy)>57.9);
  const ordinary=arcLayout({x:372,y:272,width:56,height:56},{x:0,y:0,width:800,height:600});
  assert.equal(ordinary.radius,80);assert.match(orbHtml(),/clip-path:circle\(50%\)/);
  assert.match(orbHtml(),/\.action:focus-visible\{outline-offset:-4px\}/);
});


test('the default 24-DIP bottom-left inset uses the closer diagonal arc before exact-corner fallback',()=>{
  const area={x:0,y:0,width:380,height:720},bubble={x:24,y:640,width:56,height:56};
  const arc=arcLayout(bubble,area,4);assert.equal(arc.radius,88);assert.equal(arc.sweepDegrees,120);
  assert.equal(arc.direction,'NE');
  for(const[index,a]of arc.offsets.entries()){
    const x=bubble.x+28+a.dx,y=bubble.y+28+a.dy;
    assert.ok(x-28>=1.2&&y+28<=area.height-1.2,'the closest circles still remain fully on screen');
    for(const b of arc.offsets.slice(index+1))assert.ok(Math.hypot(a.dx-b.dx,a.dy-b.dy)>60.1);
  }
  assert.equal(arcLayout({x:0,y:664,width:56,height:56},area,4).radius,112);
});

// Actual Main indicator function + real QuickSurface/Orb. Native HWND z-order,
// capture pixels and PNG decode acknowledgement are explicit UNIT boundaries.
function mainQuickIndicator(assistantOrb){
  const source=fs.readFileSync(path.join(__dirname,'../main.cjs'),'utf8');
  const start=source.indexOf('function syncQuickIndicator('),end=source.indexOf('\nasync function openSupervisedTask(',start);
  assert.ok(start>=0&&end>start,'Execute the current actual Main indicator function');
  return vm.runInNewContext('('+source.slice(start,end)+')',{
    assistantOrb,quickSurface:null,quickHost:null,quickJournal:null,taskActivity:null,quickUnread:false,
  });
}
function quickForOrb(orb){
  const {Module}=require('node:module'),file=path.join(__dirname,'quick-surface.test.cjs'),raw=fs.readFileSync(file,'utf8');
  const module=new Module(file);module.filename=file;module.paths=Module._nodeModulePaths(path.dirname(file));
  const firstTest=raw.indexOf('\ntest('),setupStart=raw.indexOf('\nfunction deferred()'),setupEnd=raw.indexOf('\nfunction pageReviewCatalog()',setupStart);
  assert.ok(firstTest>0&&setupStart>firstTest&&setupEnd>setupStart,'Load the actual QuickSurface fixture without registering its tests');
  module._compile(raw.slice(0,firstTest)+raw.slice(setupStart,setupEnd)+'\nmodule.exports={fixture};',file);
  const sync=mainQuickIndicator(orb),quick=module.exports.fixture({autoDecode:false,onState:sync});
  const order=[];const moveTop=orb.window.moveTop.bind(orb.window);
  orb.window.moveTop=()=>{moveTop();order.push('orb.moveTop');};
  for(const [role,window]of Object.entries(quick.surface.windows)){
    const showInactive=window.showInactive.bind(window);window.showInactive=()=>{showInactive();order.push(role+'.showInactive');};
    const show=window.show.bind(window);window.show=()=>{show();order.push(role+'.show');};
  }
  return {...quick,sync,order};
}

test('actual QuickSurface late overlay show raises visible orb after decode, once per visible surface role',async()=>{
  const f=fixture(),quick=quickForOrb(f.orb);try{
    await f.orb.setHostMinimized(true);const own=f.orb.window,anchor=f.orb.getAnchor(),bounds=f.orb.get().bounds;
    await quick.surface.openAsk();const before=own.moveTopCalls??0;
    await quick.surface.beginAnnotation();assert.ok(quick.surface.get().overlayPreparing);
    assert.equal(quick.surface.get().overlayVisible,false);assert.equal(own.moveTopCalls??0,before,'Preparing an invisible overlay cannot raise Orb');
    await quick.send({kind:'preview-ready',epoch:quick.surface.get().epoch},'annotationOverlay');
    assert.equal(quick.surface.get().overlayVisible,true);
    assert.equal(own.moveTopCalls,before+1,'The newly visible overlay requires one subsequent owned Orb raise');
    assert.deepEqual(quick.order.slice(-2),['annotationOverlay.showInactive','orb.moveTop']);
    for(let index=0;index<6;index++)quick.surface.setLocale(index%2?'en':'zh-CN');
    await quick.send({kind:'edit',epoch:quick.surface.get().epoch,comment:'保留的批注草稿',marks:[]},'annotationOverlay');
    assert.equal(own.moveTopCalls,before+1,'Repeated draft/progress publications do not raise again');
    await quick.command('review','annotationOverlay');
    assert.equal(quick.surface.get().visible,true);assert.equal(quick.surface.get().draft.comment,'保留的批注草稿');
    assert.equal(own.moveTopCalls,before+2);assert.deepEqual(quick.order.slice(-2),['quickPopover.show','orb.moveTop']);
    assert.deepEqual(f.orb.getAnchor(),anchor);assert.deepEqual(f.orb.get().bounds,bounds);
    assert.equal(f.orb.window,own);assert.equal(own.destroyCalls,0);assert.equal(own.visible,true);assert.deepEqual(f.calls,[]);
  }finally{quick.surface.dispose();f.orb.dispose();}
});

test('actual Main surface role projection uses only shown popover or overlay, never preparation',()=>{
  const calls=[],sync=mainQuickIndicator({setSurfaceActive:(...args)=>calls.push(args),setFeedback(){}});
  sync({visible:true,overlayVisible:false});sync({visible:false,overlayVisible:false,overlayPreparing:{phase:'DECODING'}});
  sync({visible:false,overlayVisible:true});sync({visible:true,overlayVisible:true});sync({visible:false,overlayVisible:false});
  assert.deepEqual(calls,[[true,'quickPopover'],[false,null],[true,'annotationOverlay'],[true,'annotationOverlay'],[false,null]]);
});

test('surface identity is bounded and legacy booleans remain compatible without repeated raises',async()=>{
  const f=fixture();try{
    await f.orb.setHostMinimized(true);f.orb.setSurfaceActive(true);assert.equal(f.orb.get().surfaceActive,true);
    const before=f.orb.window.moveTopCalls??0;
    f.orb.setSurfaceActive(true,'quickPopover');assert.equal(f.orb.window.moveTopCalls,before+1);
    f.orb.setSurfaceActive(true,'quickPopover');assert.equal(f.orb.window.moveTopCalls,before+1);
    f.orb.setSurfaceActive(true,'annotationOverlay');assert.equal(f.orb.window.moveTopCalls,before+2);
    f.orb.setSurfaceActive(false);assert.equal(f.orb.get().surfaceActive,false);assert.equal(f.orb.window.moveTopCalls,before+2);
    for(const value of ['Main','unknown',{},42])assert.throws(()=>f.orb.setSurfaceActive(true,value),/surface/i);
    assert.throws(()=>f.orb.setSurfaceActive(false,'quickPopover'),/surface/i);
  }finally{f.orb.dispose();}
});

test('new surface identity cannot raise an invisible, closing or disposed orb',async()=>{
  let closing=false;const f=fixture({isClosing:()=>closing}),quick=quickForOrb(f.orb);try{
    await f.orb.ready;await quick.surface.openAsk();assert.equal(f.orb.get().visible,false);assert.equal(f.orb.window.moveTopCalls??0,0);
    await f.orb.setHostMinimized(true);await quick.surface.hide({save:false});const before=f.orb.window.moveTopCalls??0;
    assert.equal(f.orb.window.moveTopCalls??0,before,'No active visible surface does not raise Orb');
    closing=true;quick.sync({visible:false,overlayVisible:true});assert.equal(f.orb.window.moveTopCalls??0,before);
    closing=false;f.orb.dispose();quick.sync({visible:true,overlayVisible:false});assert.equal(f.orb.window.moveTopCalls??0,before);
  }finally{quick.surface.dispose();f.orb.dispose();}
});

test('long bilingual notice reserves two full lines and footer without moving the center or retaining its timer',async()=>{
  for(const locale of ['zh-CN','en']){
    const clock=fakeClock(),f=fixture({clock,locale});try{
      await f.orb.setHostMinimized(true);const anchor=f.orb.getAnchor(),before=f.orb.get(),moves=f.orb.window.boundsCalls.length;
      const text=locale==='en'?'W'.repeat(80):'等待核对当前工作副本与批注，请继续原会话任务。'.repeat(3).slice(0,80);
      f.orb.setTaskActivity(activity({eventId:'long-notice:'+locale,notice:text}));
      const shown=f.orb.get(),panel=shown.layout.notice;
      assert.equal(shown.notice.visible,true);assert.equal(shown.notice.text,text);
      assert.ok(panel.height>=34+14+3+16+2,'Two 17px lines, footer, gap, padding and border fit in full');
      assert.equal(panel.height%4,0);assert.equal(clock.pending.size,1,'Only the existing bounded dismissal timer is used');
      assert.deepEqual(f.orb.getAnchor(),anchor);assert.deepEqual(shown.layout.center,before.layout.center);
      assert.deepEqual(shown.shape.slice(0,before.shape.length),before.shape,'The center native hit region is unchanged');
      clock.advance(4500);assert.equal(f.orb.get().notice.visible,false);assert.equal(clock.pending.size,0);
      assert.deepEqual(f.orb.get().shape,before.shape);assert.deepEqual(f.orb.get().bounds,before.bounds);assert.deepEqual(f.orb.getAnchor(),anchor);
      assert.equal(f.orb.window.boundsCalls.length,moves);assert.deepEqual(f.calls,[]);
    }finally{f.orb.dispose();}
  }
});

test('the same native orb is reachable beside an open workflow and resumes its normal minimized behavior',async()=>{
  const {orb,calls,send}=fixture();
  const owned=orb.window;
  try {
    await orb.ready;
    await orb.setWorkflowActive(true);
    assert.equal(orb.window,owned);
    assert.equal(owned.visible,true);
    await send({kind:'action',action:'ask'});
    assert.deepEqual(calls,['ask']);
    await orb.setWorkflowActive(false);
    assert.equal(owned.visible,false);
    await orb.setHostMinimized(true);
    assert.equal(owned.visible,true);
  } finally { orb.dispose(); }
});
