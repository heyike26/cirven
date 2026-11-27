'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {EventEmitter}=require('node:events');
const {OwnedWindowCorners}=require('../window-corners.cjs');

function fixture(options={}){
  const window=new EventEmitter();
  const forbidden=name=>()=>assert.fail('Native corners cannot call '+name);
  Object.assign(window,{maximized:false,fullscreen:false,destroyed:false,
    isDestroyed(){return this.destroyed;},isMaximized(){return this.maximized;},isFullScreen(){return this.fullscreen;},
    // Native v6 measured a whole HWND of 895x1288 but the old HRGN was 876x1281.
    // Neither logical nor content bounds may be used to reconstruct a mask.
    getBounds:forbidden('getBounds'),getContentBounds:forbidden('getContentBounds'),
    getNativeWindowHandle:forbidden('getNativeWindowHandle'),setShape:forbidden('setShape'),
    setBounds:forbidden('setBounds'),setResizable:forbidden('setResizable'),
    show:forbidden('show'),focus:forbidden('focus')});
  const control=new OwnedWindowCorners({window,platform:'win32',systemVersion:'10.0.19045',
    setTimeout:forbidden('setTimeout'),clearTimeout:forbidden('clearTimeout'),...options});
  return {window,control};
}

test('Win10 preserves the entire native frame without experimental shape or guessed geometry',()=>{
  const f=fixture();assert.deepEqual(f.control.get(),{strategy:'SYSTEM_RECTANGLE',
    status:'NATIVE_RECTANGULAR_PRESERVED',experimental:false,native_verified:false,
    reason:'WIN10_CUSTOM_MASK_DISABLED_AFTER_NATIVE_CLIPPING',expanded:false,radius_dip:0,disposed:false,started:false,pending:false});
  f.window.emit('restore');f.control.apply();assert.equal(f.control.get().radius_dip,0);f.control.dispose();
});
test('explicit rectangular windows never create a binary region or request geometry on any expansion',()=>{
  const f=fixture({rectangular:true,readGeometry:()=>assert.fail('No geometry RPC for the rectangular window')});
  f.control.start();assert.equal(f.control.get().strategy,'RECTANGULAR');assert.equal(f.control.get().radius_dip,0);
  assert.equal(f.control.get().native_verified,false);
  f.window.maximized=true;f.window.emit('maximize');assert.equal(f.control.get().radius_dip,0);
  f.window.maximized=false;f.window.emit('unmaximize');assert.equal(f.control.get().radius_dip,0);f.control.dispose();
});
test('normal/maximized/fullscreen/restore never introduce or clear a custom region',()=>{
  const f=fixture();
  for(const [flag,value,event]of [['maximized',true,'maximize'],['maximized',false,'unmaximize'],
    ['fullscreen',true,'enter-full-screen'],['fullscreen',false,'leave-full-screen']]){
    f.window[flag]=value;f.window.emit(event);
    assert.equal(f.control.get().expanded,value);assert.equal(f.control.get().radius_dip,0);
    assert.equal(f.control.get().status,'NATIVE_RECTANGULAR_PRESERVED');
  }
  f.control.dispose();
});
test('moving or resizing at mixed-DPI/negative-origin locations has no region or timer work',()=>{
  const f=fixture();for(let n=0;n<100;n++){f.window.emit('move',{x:-1200+n,y:30});f.window.emit('resize',{width:520+n,height:761});}
  assert.equal(f.control.get().started,false);
  assert.equal(f.control.get().status,'NATIVE_RECTANGULAR_PRESERVED');f.control.dispose();
});
test('Win11/macOS leave corners to the native system without claiming visual qualification or exact R18',()=>{
  for(const [platform,systemVersion]of [['win32','10.0.22631'],['darwin','15.0.0']]){
    const f=fixture({platform,systemVersion});assert.equal(f.control.get().strategy,'NATIVE');
    assert.equal(f.control.get().status,'NATIVE_SYSTEM_REQUESTED');assert.equal(f.control.get().radius_dip,null);
    assert.equal(f.control.get().native_verified,false);assert.equal(f.control.get().experimental,false);
    f.window.maximized=true;f.window.emit('maximize');assert.equal(f.control.get().radius_dip,0);
    f.window.maximized=false;f.window.emit('unmaximize');assert.equal(f.control.get().radius_dip,null);f.control.dispose();
  }
});
test('unknown OS versions preserve native ownership without guessing either corner support or a mask',()=>{
  for(const [platform,systemVersion]of [['win32',undefined],['win32','bad'],['win32','10.0'],['linux','6.6.0']]){
    const f=fixture({platform,systemVersion});assert.equal(f.control.get().strategy,'UNVERIFIED');
    assert.equal(f.control.get().status,'NATIVE_OUTER_CORNERS_UNVERIFIED');assert.equal(f.control.get().radius_dip,null);f.control.dispose();
  }
});
test('closed/disposed owners remove state listeners and late events cannot update presentation',()=>{
  for(const closed of [false,true]){
    const f=fixture();if(closed){f.window.destroyed=true;f.window.emit('closed');}else f.control.dispose();
    const state=f.control.get();f.window.maximized=true;f.window.emit('maximize');f.window.emit('restore');f.control.apply();f.control.dispose();
    assert.deepEqual(f.control.get(),state);assert.equal(state.status,'DISPOSED');
    assert.equal(f.window.eventNames().length,0);
  }
  const dead=new EventEmitter();dead.isDestroyed=()=>true;
  const control=new OwnedWindowCorners({window:dead,platform:'win32',systemVersion:'10.0.19045'});
  assert.equal(control.get().disposed,true);assert.equal(dead.eventNames().length,0);
});

// Real controller + native/clock boundaries only; this does not qualify Win32.
function waiting(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
async function drain(){for(let n=0;n<16;n++)await Promise.resolve();}
function nativeCandidate(){
  let now=0,seq=0,presentation=0,closing=false;const timers=new Map(),calls=[],reads=[],errors=[],conversions=[];
  const time={setTimeout(fn,delay){const id=++seq;timers.set(id,{fn,at:now+delay});return id;},clearTimeout(id){timers.delete(id);},
    async advance(ms){const end=now+ms;await drain();for(;;){const entry=[...timers].filter(([,v])=>v.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!entry)break;
      now=entry[1].at;timers.delete(entry[0]);entry[1].fn();await drain();}now=end;await drain();},get pending(){return timers.size;}};
  const window=new EventEmitter();Object.assign(window,{bounds:{x:-800,y:35,width:520,height:760},maximized:false,fullscreen:false,minimized:false,destroyed:false,
    isDestroyed(){return this.destroyed;},isMaximized(){return this.maximized;},isFullScreen(){return this.fullscreen;},isMinimized(){return this.minimized;},
    getBounds(){return {...this.bounds};},setShape(shape){calls.push(structuredClone(shape));this.emit('resize');},
    show(){assert.fail('corners cannot reveal');},focus(){assert.fail('corners cannot focus');},setBounds(){assert.fail('corners cannot move a window');}});
  const screen=Object.assign(new EventEmitter(),{screenToDipRect(own,rect){assert.equal(own,window);conversions.push({...rect});
    // Explicit boundary counter, not an Electron scale qualification. Physical
    // geometry intentionally differs from getBounds and has negative origin.
    return {x:-500,y:20,width:598,height:859};}});
  const control=new OwnedWindowCorners({window,screen,platform:'win32',systemVersion:'10.0.19045',...time,
    getPresentationEpoch:()=>presentation,isClosing:()=>closing,onError:error=>errors.push(error),readGeometry({signal}){const item=waiting();reads.push({...item,signal});return item.promise;}});
  const packet={status:'OWN_MAIN_GEOMETRY',coordinate_space:'PHYSICAL_SCREEN_PIXELS_THREAD_PMV2',geometry_sha256:'a'.repeat(64),
    surface_ref:{id:'own',revision:1,sha256:'b'.repeat(64)},host_scope:{generation:'exact-own'},input_epoch:1,
    window_bounds_physical:{x:-1200,y:52,width:895,height:1288},dpi:144,minimized:false,maximized:false};
  return {window,screen,control,time,calls,reads,errors,conversions,packet,nextPresentation(){presentation++;},setClosing(value){closing=value;}};
}

test('candidate starts only after bind, uses actual whole HWND conversion, and never claims native qualification',async()=>{
  const f=nativeCandidate();await f.time.advance(1000);assert.equal(f.reads.length,0);f.control.start();f.control.start();
  await f.time.advance(70);assert.equal(f.reads.length,1);f.reads[0].resolve(f.packet);await drain();
  assert.deepEqual(f.conversions,[f.packet.window_bounds_physical]);assert.equal(f.calls.length,1);
  assert.equal(Math.max(...f.calls[0].map(r=>r.x+r.width)),598);assert.equal(Math.max(...f.calls[0].map(r=>r.y+r.height)),859);
  assert.equal(f.control.get().status,'OWN_GEOMETRY_MASK_APPLIED_UNVERIFIED');assert.equal(f.control.get().native_verified,false);
  await f.time.advance(10000);assert.equal(f.reads.length,1);assert.equal(f.calls.length,1);f.control.dispose();assert.deepEqual(f.calls.at(-1),[]);
});
test('resize clears old small mask, debounces flood, and discards late geometry before one latest read',async()=>{
  const f=nativeCandidate();f.control.start();await f.time.advance(70);f.reads[0].resolve(f.packet);await drain();
  f.window.emit('will-resize');assert.deepEqual(f.calls.at(-1),[]);
  for(let n=0;n<100;n++){f.window.bounds.width++;f.window.emit('resize');}await f.time.advance(70);assert.equal(f.reads.length,2);
  f.window.bounds.width++;f.window.emit('resize');assert.equal(f.reads[1].signal.aborted,true);await f.time.advance(500);assert.equal(f.reads.length,2);
  f.reads[1].resolve(f.packet);await drain();assert.equal(f.calls.length,2);await f.time.advance(70);assert.equal(f.reads.length,3);
  f.reads[2].resolve({...f.packet,geometry_sha256:'c'.repeat(64)});await drain();assert.equal(f.calls.length,3);f.control.dispose();
});
test('maximize/fullscreen close and presentation changes cannot accept late masks',async()=>{
  for(const action of ['maximize','fullscreen','close','presentation']){
    const f=nativeCandidate();f.control.start();await f.time.advance(70);
    if(action==='maximize'){f.window.maximized=true;f.window.emit('maximize');}
    if(action==='fullscreen'){f.window.fullscreen=true;f.window.emit('enter-full-screen');}
    if(action==='close'){f.window.destroyed=true;f.window.emit('closed');}
    if(action==='presentation')f.nextPresentation();
    f.reads[0].resolve(f.packet);await drain();assert.equal(f.calls.length,0,action);
    if(action==='presentation'){await f.time.advance(70);assert.equal(f.reads.length,2);}
    f.control.dispose();await f.time.advance(10000);assert.equal(f.calls.length,0,action);
  }
});
test('normal expanded roundtrip clears only this mask and reapplies from fresh geometry',async()=>{
  const f=nativeCandidate();f.control.start();await f.time.advance(70);f.reads[0].resolve(f.packet);await drain();
  f.window.maximized=true;f.window.emit('maximize');assert.deepEqual(f.calls.at(-1),[]);await f.time.advance(200);assert.equal(f.reads.length,1);
  f.window.maximized=false;f.window.emit('unmaximize');await f.time.advance(70);f.reads[1].resolve(f.packet);await drain();assert.equal(f.calls.length,3);f.control.dispose();
});
test('geometry failure deadline and changed lifetime leave system rectangle without replay or external effects',async()=>{
  const f=nativeCandidate();f.control.start();await f.time.advance(70);await f.time.advance(1800);assert.equal(f.reads[0].signal.aborted,true);
  f.reads[0].resolve(f.packet);await drain();assert.equal(f.calls.length,0);await f.time.advance(5000);assert.equal(f.reads.length,1);
  f.control.apply();await f.time.advance(70);f.reads[1].reject(Error('geometry unavailable'));await drain();assert.equal(f.calls.length,0);
  f.control.apply();await f.time.advance(70);f.reads[2].resolve(f.packet);await drain();assert.equal(f.calls.length,1);
  f.control.apply();await f.time.advance(70);f.reads[3].resolve({...f.packet,host_scope:{generation:'new-host'}});await drain();
  assert.deepEqual(f.calls.at(-1),[]);assert.equal(f.control.get().status,'NATIVE_RECTANGULAR_PRESERVED');f.control.dispose();
});
test('tiny and fractional physical conversion boundaries never create empty/outside rectangles',()=>{
  const {roundWindowShape}=require('../window-corners.cjs');
  for(const [w,h]of [[1,1],[2,2],[3,9],[598,859]])for(const r of roundWindowShape(w,h)){
    assert.ok(r.width>0&&r.height>0);assert.ok(r.x>=0&&r.y>=0&&r.x+r.width<=w&&r.y+r.height<=h);
  }
  assert.throws(()=>roundWindowShape(1.5,12));assert.throws(()=>roundWindowShape(0,12));
});

test('closing prevents reads, late mask application and clear writes; UNKNOWN recovery can reobserve explicitly',async()=>{
  const f=nativeCandidate();f.control.start();await f.time.advance(70);f.setClosing(true);
  f.reads[0].resolve(f.packet);await drain();assert.equal(f.calls.length,0);
  f.window.bounds.width++;f.window.emit('resize');f.control.apply();await f.time.advance(5000);assert.equal(f.reads.length,1);
  f.setClosing(false);f.control.apply();await f.time.advance(70);f.reads[1].resolve(f.packet);await drain();assert.equal(f.calls.length,1);
  f.setClosing(true);f.window.bounds.width++;f.window.emit('will-resize');assert.equal(f.calls.length,1);
  f.control.dispose();await f.time.advance(5000);assert.equal(f.calls.length,1);assert.equal(f.control.get().status,'DISPOSED');
});

test('display DPI metrics trigger fresh physical geometry even with unchanged logical bounds and detach on dispose',async()=>{
  const f=nativeCandidate();f.control.start();await f.time.advance(70);f.reads[0].resolve(f.packet);await drain();
  f.screen.emit('display-metrics-changed');assert.deepEqual(f.calls.at(-1),[]);await f.time.advance(70);assert.equal(f.reads.length,2);
  f.reads[1].resolve({...f.packet,geometry_sha256:'d'.repeat(64),window_bounds_physical:{x:-1200,y:52,width:1193,height:1718}});await drain();
  assert.equal(f.conversions.at(-1).width,1193);assert.equal(f.calls.length,3);
  f.control.dispose();assert.equal(f.screen.eventNames().length,0);f.screen.emit('display-metrics-changed');await f.time.advance(1000);assert.equal(f.reads.length,2);
});
