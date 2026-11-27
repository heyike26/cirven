"use strict";
const test=require('node:test'),assert=require('node:assert/strict');
const {createAssistantOrbActions}=require('../assistant-orb-actions.cjs');
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function fixture({surface=true,open=async()=>{},mode=async value=>({mode:value})}={}){
 const events=[];let closing=false;
 const window={webContents:{send:(...args)=>events.push(args)}};
 const companion={capture:()=>{throw Error('Launcher must not capture or submit itself');},setMode:async value=>{events.push(['mode',value]);return mode(value);}};
 const quick={openAsk:()=>{events.push(['ask']);return open();},beginAnnotation:()=>{events.push(['annotate']);return open();}};
 return {events,close:()=>{closing=true;},act:createAssistantOrbActions({getCompanion:()=>companion,getWindow:()=>window,
 getQuickSurface:()=>surface?quick:undefined,reveal:()=>events.push(['reveal']),isClosing:()=>closing,
 logFailure:()=>events.push(['surface-error'])})};
}
test('ask and annotate use independent surfaces and never reveal or change Main presentation',async()=>{
 const v=fixture();assert.equal((await v.act('ask')).status,'OPENING_IN_PLACE');assert.equal((await v.act('annotate')).status,'OPENING_IN_PLACE');
 assert.deepEqual(v.events,[['ask'],['annotate']]);
});
test('a pending capture does not lock the launcher or create a delayed Main restore',async()=>{
 const gate=deferred(),v=fixture({open:()=>gate.promise});await v.act('ask');await v.act('ask');v.close();gate.resolve();
 await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(v.events,[['ask'],['ask']]);
});
test('unavailable or failed quick surface cannot fall back to showing the big Main',async()=>{
 const missing=fixture({surface:false});assert.equal((await missing.act('ask')).status,'NOT_READY');assert.equal(missing.events.length,0);
 const failed=fixture({open:()=>Promise.reject(Error('capture failure'))});await failed.act('annotate');
 await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(failed.events,[['annotate'],['surface-error']]);
});
test('only explicit conversation and workbench requests select mode and reveal Main',async()=>{
 const v=fixture();await v.act('show-agent');assert.deepEqual(v.events.map(x=>x[0]),['mode','reveal','eeas:collaboration-state','eeas:quick-question']);
 assert.deepEqual(v.events[0],['mode','companion']);v.events.length=0;await v.act('workbench');
 assert.deepEqual(v.events.map(x=>x[0]),['mode','reveal','eeas:collaboration-state']);assert.deepEqual(v.events[0],['mode','workbench']);
 await assert.rejects(v.act('unknown-command'),/Unknown/);
});
test('closing during an explicit mode change never reveals Main from its late receipt',async()=>{
 const gate=deferred(),v=fixture({mode:()=>gate.promise}),run=v.act('workbench');assert.equal((await v.act('ask')).status,'BUSY');
 v.close();gate.resolve({mode:'workbench'});assert.equal((await run).status,'CLOSING');assert.deepEqual(v.events,[['mode','workbench']]);
});

test('quick actions derive placement from the stable host orb anchor, without trusting a renderer point',async()=>{
 const anchor={x:812,y:604,orbBounds:{x:784,y:576,width:56,height:56}},calls=[];
 const act=createAssistantOrbActions({getQuickSurface:()=>({openAsk:options=>calls.push(['ask',options]),
   beginAnnotation:options=>calls.push(['annotate',options])}),getAnchor:()=>anchor,
   getCompanion:()=>{throw Error('no Main mode for in-place actions');},getWindow:()=>null,
   reveal:()=>{throw Error('must not reveal Main');},isClosing:()=>false});
 await act('ask');await act('annotate');
 assert.deepEqual(calls,[['ask',{anchor}],['annotate',{anchor}]]);
});

test('only the explicit shortcut identity reaches ask; normal orb and annotation keep their own semantics',async()=>{
 const calls=[];
 const act=createAssistantOrbActions({getQuickSurface:()=>({openAsk:options=>calls.push(options),beginAnnotation:options=>calls.push(options)}),
 getAnchor:()=>({x:1,y:2}),isClosing:()=>false});
 await act('ask',{trigger:'CTRL_SHIFT_SPACE'});await act('ask',{trigger:'unknown'});await act('annotate',{trigger:'CTRL_SHIFT_SPACE'});
 assert.deepEqual(calls,[{anchor:{x:1,y:2},trigger:'CTRL_SHIFT_SPACE'},{anchor:{x:1,y:2}},{anchor:{x:1,y:2}}]);
});

test('explicit conversation passes only the owned bubble anchor while workbench never receives floating geometry',async()=>{
 const anchor={x:-100,y:700,orbBounds:{x:-128,y:672,width:56,height:56}},calls=[];
 const act=createAssistantOrbActions({getCompanion:()=>({async setMode(...args){calls.push(args);return {mode:args[0]};}}),
 getAnchor:()=>anchor,getWindow:()=>({webContents:{send(){}}}),reveal(){},isClosing:()=>false});
 await act('show-agent');await act('workbench');assert.deepEqual(calls,[['companion',{anchor}],['workbench',undefined]]);
});

for(const next of ['ask','annotate','show-agent','workbench','closing'])test('latest explicit intent fences delayed tasks before opening: '+next,async()=>{
 const gate=deferred(),events=[];let closing=false;
 const surface={openAsk:()=>events.push('ask'),beginAnnotation:()=>events.push('annotate'),openActivity:()=>events.push('activity')};
 const act=createAssistantOrbActions({getQuickSurface:()=>surface,getTaskActivity:()=>gate.promise,isClosing:()=>closing,
 getCompanion:()=>({setMode:async mode=>{events.push(mode);return {mode};}}),getWindow:()=>({webContents:{send(){}}}),reveal:()=>events.push('reveal')});
 assert.equal((await act('tasks')).status,'OPENING_IN_PLACE');
 if(next==='closing')closing=true;else await act(next);
 const before=[...events];gate.resolve({settings:{enabled:true},tasks:[]});await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(events,before);assert.equal(events.includes('activity'),false);
});

test('latest explicit intent permits normal delayed tasks exactly once with a live continuation guard',async()=>{
 const gate=deferred(),calls=[];let closing=false;
 const surface={openActivity:(snapshot,options)=>calls.push({snapshot,options})};
 const act=createAssistantOrbActions({getQuickSurface:()=>surface,getTaskActivity:()=>gate.promise,isClosing:()=>closing});
 await act('tasks');const snapshot={original:'bounded-task-observation'};gate.resolve(snapshot);await new Promise(resolve=>setImmediate(resolve));
 assert.equal(calls.length,1);assert.equal(calls[0].snapshot,snapshot);assert.equal(calls[0].options.isCurrent(),true);
 closing=true;assert.equal(calls[0].options.isCurrent(),false);
});
