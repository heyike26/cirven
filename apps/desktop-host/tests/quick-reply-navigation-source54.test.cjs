'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),vm=require('node:vm');
const {EventEmitter}=require('node:events');
const {QuickJournal,quickRef}=require('../quick-journal.cjs');
const {QuickHost}=require('../quick-host.cjs');
const {QuickSurface,CHANNEL}=require('../quick-surface.cjs');
const {createAssistantOrbActions}=require('../assistant-orb-actions.cjs');
const target={kind:'QUICK_REPLY',operationId:'op_b',draftRef:{id:'draft_b',revision:2,sha256:'a'.repeat(64)},scope:{conversationId:'discussion'}};
const reference=item=>quickRef({id:item.id,revision:item.revision,sha256:item.sha256});
function fixture(t){const dataDir=fs.mkdtempSync(path.join(os.tmpdir(),'cirven-s54-navigation-')),journal=new QuickJournal({dataDir,hostGeneration:'owned'}),calls=[];
 const host=new QuickHost({journal,getCompanion:()=>null,request:()=>{calls.push('request');throw Error('Navigation must not dispatch');}});host.setScope(target.scope);
 t.after(()=>{host.close();fs.rmSync(dataDir,{force:true,recursive:true});});
 const record=host.save({draftId:'draft_b',expectedRevision:0,comment:'B问题',regions:[],marks:[],delivery:'DRAFT'}),command=journal.prepareSend(reference(record),{operationId:'op_b',text:'B问题'});
 journal.observe('op_b',{operation_id:'op_b',conversation_id:'discussion',status:'RETURNED',text:'B回答'});return {host,journal,calls,command};}
test('canonical navigation is read-only owned SEND revision; pre-SEND reference is never reused and observations do not alter target',t=>{
 const f=fixture(t),before=fs.readFileSync(f.journal.file),canonical=f.host.replyTarget({draftId:'draft_b',operationId:'op_b'});
 assert.deepEqual(canonical.draftRef,reference(f.journal.find('draft_b')));assert.equal(canonical.draftRef.revision,f.command.input.draftRef.revision+1);
 assert.equal(canonical.scope.conversationId,'discussion');assert.deepEqual(fs.readFileSync(f.journal.file),before);assert.deepEqual(f.calls,[]);
 canonical.scope.conversationId='changed';assert.equal(f.host.replyTarget({draftId:'draft_b',operationId:'op_b'}).scope.conversationId,'discussion');
 assert.throws(()=>f.host.replyTarget({draftId:'other',operationId:'op_b'}),/differ/);
 f.host.setScope({conversationId:'other'});assert.throws(()=>f.host.replyTarget({draftId:'draft_b',operationId:'op_b'}),/another discussion/);assert.deepEqual(f.calls,[]);
});
function mainExpand(bindings){const source=fs.readFileSync(path.join(__dirname,'../main.cjs'),'utf8'),begin=source.indexOf('onExpand:async(mode,origin)=>'),end=source.indexOf(',\n        onOpenArtifact:',begin);assert.ok(begin>0&&end>begin);return vm.runInNewContext('('+source.slice(begin+'onExpand:'.length,end)+')',bindings);}
class Window extends EventEmitter{
 constructor(options){super();this.bounds={x:0,y:0,width:options.width,height:options.height};this.visible=false;this.calls=[];this.webContents=new EventEmitter();this.webContents.mainFrame={url:''};this.webContents.send=(...args)=>this.calls.push(args);this.webContents.getURL=()=>this.url;this.webContents.setWindowOpenHandler=()=>{};}
 loadURL(url){this.url=url;this.webContents.mainFrame.url=url;return Promise.resolve();}isDestroyed(){return false;}getBounds(){return this.bounds;}setBounds(bounds){this.bounds=bounds;}hide(){this.visible=false;}showInactive(){this.visible=true;}show(){this.visible=true;}isAlwaysOnTop(){return false;}setAlwaysOnTop(){}setContentProtection(){}destroy(){}setTitle(){}
}
async function surfaceFixture(t){const f=fixture(t),handlers=new Map(),screen=new EventEmitter();screen.getCursorScreenPoint=()=>({x:400,y:400});screen.getDisplayNearestPoint=()=>({id:1,workArea:{x:0,y:0,width:1600,height:1000}});screen.getAllDisplays=()=>[{id:1,bounds:{x:0,y:0,width:1600,height:1000}}];
 const events=[],main={webContents:{send:(...args)=>events.push(args)}},companion={setMode:async mode=>({mode})};let surface;
 const act=createAssistantOrbActions({getCompanion:()=>companion,getWindow:()=>main,getQuickSurface:()=>surface,reveal:()=>events.push(['reveal']),isClosing:()=>false});
 const bindings={quickHost:f.host,orbAction:act,quitting:false,stopped:false};
 surface=new QuickSurface({BrowserWindow:Window,screen,onCapture:async()=>({snapshot:{operation_id:'page',current:false,observation:{status:'UNAVAILABLE'}},scope:f.host.scope}),onSend:async()=>assert.fail('not a dispatch'),onInspect:async()=>assert.fail('not an inspection'),onOpenArtifact:async()=>assert.fail('not an artifact open'),ipcMain:{handle:(key,value)=>handlers.set(key,value),removeHandler:key=>handlers.delete(key)},onExpand:(...args)=>mainExpand({...bindings,quickSurface:surface})(...args),onDraft:async body=>({id:body.draftId,revision:body.expectedRevision+1,sha256:'b'.repeat(64),status:'EDITING'}),onLoadDraft:body=>f.host.load(body)});
 t.after(()=>surface.dispose());await surface.ready;
 const send=command=>{const window=surface.windows.quickPopover;return handlers.get(CHANNEL)({sender:window.webContents,senderFrame:window.webContents.mainFrame},{kind:'command',epoch:surface.epoch,command});};
 return {...f,surface,events,send};}
test('real QuickSurface -> actual Main callback -> owned host -> orb action keeps exact old reply before restoring suspended draft',async t=>{
 const f=await surfaceFixture(t);await f.surface.openAsk();const suspended=f.surface.draft;suspended.comment='未发送C';suspended.touched=true;
 await f.surface.openAsk({draftRef:reference(f.journal.find('draft_b'))});assert.equal(f.surface.draft.readonly,true);assert.equal(f.surface.suspendedDraft,suspended);
 const result=await f.send('expand');assert.equal(result.status,'SHOWN');assert.equal(f.surface.visible,false);assert.equal(f.surface.draft,suspended);assert.equal(f.surface.draft.comment,'未发送C');
 const sent=f.events.find(row=>row[0]==='eeas:quick-question');assert.equal(sent[1].operationId,'op_b');assert.equal(sent[1].draftRef.id,'draft_b');assert.deepEqual(JSON.parse(JSON.stringify(sent[1])),f.host.replyTarget({draftId:'draft_b',operationId:'op_b'}));assert.deepEqual(f.calls,[]);
});
test('invalid owned reply does not hide Quick, reveal Main or fall back to generic compose',async t=>{
 const f=await surfaceFixture(t);await f.surface.openAsk();f.surface.draft.operationId='not-owned';f.surface.draft.phase='RETURNED';
 assert.equal((await f.send('expand')).status,'ACTION_FAILED');assert.equal(f.surface.visible,true);assert.equal(f.events.length,0);assert.deepEqual(f.calls,[]);
});
test('scope changing during native mode await suppresses late reveal/target; generic open still has no navigation payload',async()=>{
 let release,current=true;const events=[],act=createAssistantOrbActions({getCompanion:()=>({setMode:()=>new Promise(resolve=>release=resolve)}),getWindow:()=>({webContents:{send:(...args)=>events.push(args)}}),reveal:()=>events.push(['reveal']),isClosing:()=>false});
 const pending=act('show-agent',{replyTarget:target,isCurrent:()=>current});current=false;release({mode:'companion'});assert.equal((await pending).status,'STALE');assert.deepEqual(events,[]);
 const generic=createAssistantOrbActions({getCompanion:()=>({setMode:async mode=>({mode})}),getWindow:()=>({webContents:{send:(...args)=>events.push(args)}}),reveal(){},isClosing:()=>false});await generic('show-agent');assert.deepEqual(events.at(-1),['eeas:quick-question']);
});
test('real preload passes one valid bounded target, preserves undefined generic open, and ignores malformed input without invoking listener',()=>{
 const ipc=new EventEmitter();let bridge;vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../preload.cjs'),'utf8'),{require:name=>{assert.equal(name,'electron');return {contextBridge:{exposeInMainWorld:(_name,value)=>bridge=value},ipcRenderer:ipc};}});
 const calls=[],release=bridge.onQuickQuestion(value=>calls.push(value));ipc.emit('eeas:quick-question',{},target);ipc.emit('eeas:quick-question',{});
 for(const value of [null,{}, {...target,scope:{}},{...target,scope:{...target.scope,unexpected:'x'}},{...target,draftRef:{...target.draftRef,revision:0}}])ipc.emit('eeas:quick-question',{},value);
 assert.equal(calls.length,2);assert.deepEqual(calls[0],target);assert.equal(calls[1],undefined);release();ipc.emit('eeas:quick-question',{},target);assert.equal(calls.length,2);
});
