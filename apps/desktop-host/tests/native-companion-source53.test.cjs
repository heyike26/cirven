"use strict";
const test=require('node:test'),assert=require('node:assert/strict');
const {EventEmitter}=require('node:events');
const {NativeCompanion,revealOwnWindow}=require('../native-companion.cjs');

// Controlled Electron/Windows lifecycle only. No native HWND, CLI, model,
// network, desktop reservation, capture, or foreign-window actuation runs here.
function gate(){let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};}
function scene({minimized=true,restoreMaximized=true,fullscreen=false,initialMode='workbench',
  normal={x:900,y:80,width:680,height:800},restoreMode='SYNC',unmaximizeMode='SYNC'}={}){
 const calls=[],restoreStarted=gate(),unmaximizeStarted=gate();
 const area={x:0,y:0,width:1920,height:1040};
 const state={minimized,maximized:!minimized&&restoreMaximized,fullscreen:!minimized&&fullscreen,
  restoreMaximized,restoreFullscreen:fullscreen,normal:{...normal},minimum:[780,480],pinned:false,destroyed:false,opacity:1};
 const config={restoreMode,unmaximizeMode};
 const own=new EventEmitter();
 const completeRestore=()=>{state.minimized=false;state.maximized=state.restoreMaximized;
  state.fullscreen=state.restoreFullscreen;calls.push('restore.complete');own.emit('restore');};
 const completeUnmaximize=()=>{state.maximized=false;state.restoreMaximized=false;calls.push('unmaximize.complete');own.emit('unmaximize');};
 Object.assign(own,{
  isDestroyed:()=>state.destroyed,isMinimized:()=>state.minimized,
  isMaximized:()=>!state.minimized&&state.maximized,isFullScreen:()=>!state.minimized&&state.fullscreen,
  getBounds:()=>state.minimized?{x:-32000,y:-32000,width:160,height:28}:state.maximized||state.fullscreen?{...area}:{...state.normal},
  getNormalBounds:()=>({...state.normal}),getMinimumSize:()=>[...state.minimum],
  setMinimumSize(w,h){state.minimum=[w,h];calls.push(['minimum',w,h]);},
  setBounds(v){state.normal={...v};calls.push(['bounds',{...v}]);},
  restore(){calls.push('restore');restoreStarted.resolve();
   if(config.restoreMode==='THROW')throw Error('original restore failure');
   if(config.restoreMode==='EARLY_FLAG')state.minimized=false;
   if(config.restoreMode==='SYNC')completeRestore();},
  minimize(){state.minimized=true;calls.push('minimize');},
  unmaximize(){calls.push('unmaximize');unmaximizeStarted.resolve();
   if(config.unmaximizeMode==='THROW')throw Error('original unmaximize failure');
   if(config.unmaximizeMode==='SYNC')completeUnmaximize();},
  setFullScreen(value){calls.push(['fullscreen',value]);state.fullscreen=value;state.restoreFullscreen=value;own.emit('leave-full-screen');},
  isAlwaysOnTop:()=>state.pinned,setAlwaysOnTop(value){state.pinned=value;calls.push(['pin',value]);},
  show(){calls.push('show');},focus(){calls.push('focus');},
  // Source54 explicitly adds bounded opacity protection to this Windows fake;
  // the archived Source53 fixture remains in its original frozen evidence.
  getOpacity:()=>state.opacity,setOpacity(value){state.opacity=value;calls.push(['opacity',value]);},
  hide(){assert.fail('no hidden recovery state introduced');},
  setShape(){assert.fail('no desktop region introduced');},maximize(){assert.fail('companion never maximizes Main');},
 });
 const screen={getAllDisplays:()=>[{workArea:area}],getCursorScreenPoint:()=>({x:1800,y:900}),getDisplayNearestPoint:()=>({workArea:area})};
 const requests=[];
 const broker=new NativeCompanion({window:own,screen,initialMode,platform:'win32',request:async(route,body)=>{
  requests.push({route,body});assert.ok(!route.includes('/workspace/'));
  return route.endsWith('/context/status')?{configured:false}:{status:route.endsWith('/close')?'CLOSED':'ACTIVE'};
 }});
 const clean=()=>{for(const event of ['restore','unmaximize','leave-full-screen','closed'])assert.equal(own.listenerCount(event),0,event+' listener leaked');};
 return {own,broker,state,config,calls,requests,screen,completeRestore,completeUnmaximize,restoreStarted,unmaximizeStarted,clean};
}
function boundsCalls(s){return s.calls.filter(v=>Array.isArray(v)&&v[0]==='bounds');}

 test('UNIT Source52 counter semantics: generic late restore alone preserves the saved maximized state',()=>{
 const s=scene();assert.equal(s.own.isMinimized(),true);assert.equal(s.own.isMaximized(),false);
 revealOwnWindow(s.own,s.screen,{size:[520,760]});
 assert.equal(s.own.isMinimized(),false);assert.equal(s.own.isMaximized(),true);
 assert.deepEqual(boundsCalls(s),[],'generic tray recovery deliberately preserves maximized state');s.clean();
});

test('UNIT minimized maximized Main restores, unmaximizes, then sizes before ordinary reveal',async()=>{
 const s=scene();assert.equal(s.own.isMaximized(),false);
 const result=await s.broker.setMode('companion',{anchor:{x:1800,y:900}});
 assert.equal(result.mode,'companion');assert.equal(s.state.minimized,false);assert.equal(s.state.maximized,false);
 assert.deepEqual(s.state.normal,{x:1400,y:128,width:520,height:760});
 const names=s.calls.map(v=>Array.isArray(v)?v[0]:v);
 assert.ok(names.indexOf('restore')<names.indexOf('unmaximize'));
 assert.ok(names.indexOf('unmaximize.complete')<names.indexOf('bounds'));
 assert.equal(names.includes('show'),false);assert.equal(names.includes('focus'),false);
 const before=s.calls.length;revealOwnWindow(s.own,s.screen);
 assert.deepEqual(s.calls.slice(before),['show','focus']);assert.equal(s.state.maximized,false);s.clean();
});

test('UNIT asynchronous restore and unmaximize settle before bounds; premature event is not proof',async()=>{
 const s=scene({restoreMode:'DEFERRED',unmaximizeMode:'DEFERRED'});
 const pending=s.broker.setMode('companion');await s.restoreStarted.promise;
 s.own.emit('restore');await Promise.resolve();
 assert.equal(s.state.minimized,true);assert.equal(s.calls.includes('unmaximize'),false);assert.deepEqual(boundsCalls(s),[]);
 s.completeRestore();await s.unmaximizeStarted.promise;
 assert.equal(s.state.maximized,true);assert.deepEqual(boundsCalls(s),[]);
 s.own.emit('unmaximize');await Promise.resolve();assert.deepEqual(boundsCalls(s),[]);
 s.completeUnmaximize();await pending;assert.equal(s.state.normal.width,520);s.clean();
});

test('UNIT clearing minimized before the actual restore event cannot race the restored maximize state',async()=>{
 const s=scene({restoreMode:'EARLY_FLAG'});const pending=s.broker.setMode('companion');await s.restoreStarted.promise;
 await Promise.resolve();await Promise.resolve();assert.equal(s.state.minimized,false);
 assert.deepEqual(boundsCalls(s),[]);assert.equal(s.calls.includes('unmaximize'),false);
 s.completeRestore();await pending;assert.equal(s.state.maximized,false);assert.equal(s.state.normal.width,520);s.clean();
});

test('UNIT ordinary maximized Main and fullscreen restored Main both enter a normal companion',async()=>{
 for(const input of [{minimized:false},{fullscreen:true}]){
  const s=scene(input);await s.broker.setMode('companion');
  assert.equal(s.state.fullscreen,false);assert.equal(s.state.maximized,false);assert.equal(s.state.normal.width,520);
  if(input.fullscreen){const names=s.calls.map(v=>Array.isArray(v)?v[0]:v);assert.ok(names.indexOf('restore.complete')<names.indexOf('fullscreen'));}
  s.clean();
 }
});

test('UNIT minimized companion remembers real normal bounds rather than its iconic off-screen rectangle',async()=>{
 const normal={x:1200,y:100,width:640,height:820};
 const s=scene({initialMode:'companion',normal});await s.broker.setMode('companion',{anchor:{x:50,y:60}});
 assert.deepEqual(s.state.normal,normal);assert.deepEqual(s.broker.companionBounds,normal);
 assert.equal(s.state.maximized,false);s.clean();
});

test('UNIT unavailable normal bounds retain the previous companion cache and never adopt iconic bounds',async()=>{
 const cached={x:1080,y:120,width:620,height:810},s=scene({initialMode:'companion'});
 s.broker.companionBounds={...cached};s.own.getNormalBounds=()=>undefined;
 await s.broker.setMode('companion');assert.deepEqual(s.state.normal,cached);s.clean();
});

test('UNIT already normal companion retains the user rectangle and pin preference without restore or focus',async()=>{
 const normal={x:1140,y:130,width:650,height:800},s=scene({minimized:false,restoreMaximized:false,initialMode:'companion',normal});
 await s.broker.setMode('companion');assert.deepEqual(s.state.normal,normal);assert.equal(s.state.pinned,false);
 assert.equal(s.calls.includes('restore'),false);assert.equal(s.calls.includes('unmaximize'),false);
 assert.equal(s.calls.includes('show'),false);assert.equal(s.calls.includes('focus'),false);s.clean();
});

test('UNIT workbench and generic tray recovery retain the original maximized policy',async()=>{
 const s=scene();await s.broker.setMode('workbench');
 assert.equal(s.state.minimized,true);assert.equal(s.calls.includes('restore'),false);
 revealOwnWindow(s.own,s.screen);assert.equal(s.state.maximized,true);assert.equal(s.broker.mode,'workbench');s.clean();
});

test('UNIT restore or unmaximize throws the original failure before pin or sizing and a later retry can recover',async()=>{
 for(const which of ['restoreMode','unmaximizeMode']){
  const s=scene({[which]:'THROW'});await assert.rejects(s.broker.setMode('companion'),/original .* failure/);
  assert.deepEqual(boundsCalls(s),[]);assert.equal(s.calls.some(v=>Array.isArray(v)&&v[0]==='pin'),false);
  assert.equal(s.broker.mode,'workbench');s.clean();s.config[which]='SYNC';
  await s.broker.setMode('companion');assert.equal(s.state.minimized,false);assert.equal(s.state.maximized,false);s.clean();
 }
});

test('UNIT missing restore completion has a bounded timeout, cleans listeners and retains retry ability',{timeout:5000},async()=>{
 const s=scene({restoreMode:'DEFERRED'});await assert.rejects(s.broker.setMode('companion'),/尚未退出放大或最小化/);
 assert.equal(s.state.minimized,true);assert.equal(s.broker.mode,'workbench');assert.deepEqual(boundsCalls(s),[]);s.clean();
 s.config.restoreMode='SYNC';await s.broker.setMode('companion');assert.equal(s.state.maximized,false);s.clean();
});

test('UNIT newer workbench intent or closing fences a delayed restore before further window mutation',async()=>{
 for(const choice of ['workbench','close']){
  const s=scene({restoreMode:'DEFERRED'}),pending=s.broker.setMode('companion');
  const rejected=assert.rejects(pending,choice==='close'?/closed/:error=>error.code==='PRESENTATION_CHANGED');
  await s.restoreStarted.promise;
  const next=choice==='close'?s.broker.close():s.broker.setMode('workbench');
  s.completeRestore();await rejected;await next;
  assert.equal(s.calls.includes('unmaximize'),false);assert.deepEqual(boundsCalls(s),[]);
  assert.equal(s.state.pinned,false);if(choice==='workbench')assert.equal(s.broker.mode,'workbench');s.clean();
 }
});

test('UNIT owned window closing during native restore rejects promptly and releases listeners',async()=>{
 const s=scene({restoreMode:'DEFERRED'}),pending=s.broker.setMode('companion');const rejected=assert.rejects(pending,/closed/);
 await s.restoreStarted.promise;s.state.destroyed=true;s.own.emit('closed');await rejected;
 assert.equal(s.calls.includes('unmaximize'),false);assert.deepEqual(boundsCalls(s),[]);s.clean();
});
