'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {EventEmitter}=require('node:events');
const {QuickJournal,quickDigest}=require('../quick-journal.cjs');
const {QuickHost}=require('../quick-host.cjs');
const {QuickSurface,CHANNEL}=require('../quick-surface.cjs');
const ref=id=>({id,revision:1,sha256:'a'.repeat(64)});
const scope={conversationId:'discussion',conversationRef:{id:'native:'+'a'.repeat(64),revision:1},providerProfileRef:ref('profile')};
class Window extends EventEmitter{
 constructor(options){super();this.bounds=options;this.webContents=new EventEmitter();this.webContents.mainFrame={url:''};this.webContents.getURL=()=>this.webContents.mainFrame.url;this.webContents.send=()=>{};this.webContents.setWindowOpenHandler=()=>{};}
 loadURL(url){this.webContents.mainFrame.url=url;return Promise.resolve();}isDestroyed(){return false;}getBounds(){return this.bounds;}setBounds(value){this.bounds=value;}setContentProtection(){}show(){}showInactive(){}hide(){}destroy(){}
}
function fixture(t,dataDir=fs.mkdtempSync(path.join(os.tmpdir(),'continuation-draft-'))){
 const journal=new QuickJournal({dataDir}),requests=[];
 const host=new QuickHost({journal,getCompanion:()=>null,request:async(url,body)=>{requests.push({url,body});return {operation_id:body.operation_id,status:'RETURNED',conversation_ref:scope.conversationRef,canonical_message_ref:ref(body.operation_id),can_continue:true,can_cancel:false,response:{text:'Initial reply'},owner_started_by_query:false,model_calls_by_query:0,automatic_post_replay:false};}});host.setScope(scope);
 const screen=new EventEmitter();screen.getCursorScreenPoint=()=>({x:600,y:600});screen.getDisplayNearestPoint=()=>({workArea:{x:0,y:0,width:1400,height:900}});screen.getAllDisplays=()=>[{bounds:{x:0,y:0,width:1400,height:900}}];
 const handlers=new Map(),ipcMain={handle:(name,fn)=>handlers.set(name,fn),removeHandler:()=>{}};
 const surface=new QuickSurface({BrowserWindow:Window,screen,ipcMain,onCapture:async()=>host.emptyCapture(),onDraft:v=>host.save(v),onContinuationDraft:v=>host.saveContinuation(v),onSend:v=>host.send(v),onInspect:id=>host.inspect(id),onReviewControl:v=>host.reviewControl(v),onLoadDraft:v=>host.load(v),onExpand:async()=>{},onOpenArtifact:async()=>{}});
 const send=value=>handlers.get(CHANNEL)({sender:surface.windows.quickPopover.webContents,senderFrame:surface.windows.quickPopover.webContents.mainFrame},value);
 const edit=text=>send({kind:'review-guidance-edit',epoch:surface.epoch,text});const command=command=>send({kind:'command',epoch:surface.epoch,command});
 t.after(()=>{surface.dispose();host.close();fs.rmSync(dataDir,{recursive:true,force:true});});
 return {dataDir,journal,host,surface,requests,send,edit,command};
}
async function initial(f){await f.surface.ready;await f.surface.openAsk();await f.send({kind:'edit',epoch:f.surface.epoch,comment:'Original question',marks:[]});await f.command('send');await f.surface.openAsk();return f.surface.draft.operationId;}
test('actual controller saves Unicode continuation beside immutable original and default cold load restores without requests',async t=>{
 const f=fixture(t),parent=await initial(f),original=quickDigest(f.journal.command(parent).input),text='用户手写的后续意见 😀';await f.edit(text);await f.surface.hide({save:true});
 assert.equal(f.journal.continuationDraft(parent).text,text);assert.equal(quickDigest(f.journal.command(parent).input),original);
 const g=fixture(t,f.dataDir);await g.surface.ready;await g.surface.openAsk();assert.equal(g.surface.get().draft.guidanceText,text);assert.equal(g.surface.draft.operationId,parent);assert.equal(g.surface.draft.readonly,false);assert.equal(g.requests.length,0);assert.equal(g.surface.draft.capture.restored,true);
 g.host.setScope({...scope,conversationId:'other'});assert.equal(await g.host.load(),undefined);
});
test('failed disk replacement retains latest input, blocks hide, then explicit save recovers',async t=>{
 const f=fixture(t),parent=await initial(f);await f.edit('saved');const old=f.journal.commit.bind(f.journal);f.journal.commit=()=>{throw Error('ENOSPC injected');};
 assert.equal((await f.edit('latest 未保存')).status,'ACTION_FAILED');assert.equal(f.surface.get().draft.guidanceText,'latest 未保存');await assert.rejects(f.surface.hide({save:true}));assert.equal(f.surface.visible,true);assert.equal(f.journal.continuationDraft(parent).text,'saved');
 f.journal.commit=old;await f.surface.hide({save:true});assert.equal(new QuickJournal({dataDir:f.dataDir}).continuationDraft(parent).text,'latest 未保存');
});
test('late successful ACK cannot clear a newer edit; close waits queued persistence',async t=>{
 const f=fixture(t),parent=await initial(f);await f.edit('first');let release;const reply=new Promise(resolve=>release=resolve);f.surface.onReviewControl=async value=>{await reply;return {operation_id:value.body.operation_id,status:'ACCEPTED'};};
 const sent=f.command('send');await new Promise(resolve=>setImmediate(resolve));await f.edit('second');release();await sent;assert.equal(f.surface.get().draft.guidanceText,'second');await f.surface.hide({save:true});assert.equal(new QuickJournal({dataDir:f.dataDir}).continuationDraft(parent).text,'second');
});
test('CAS conflict preserves both copies and unknown recovery keeps exact child key for inspection',async t=>{
 const f=fixture(t),parent=await initial(f);await f.edit('original draft');let key;f.surface.onReviewControl=async value=>{key=value.body.operation_id;f.journal.prepareReviewControl(parent,{operationId:key,kind:value.kind,body:value.body});return {operation_id:key,status:'UNKNOWN_OUTCOME'};};await f.command('send');
 const g=fixture(t,f.dataDir);await g.surface.ready;await g.surface.openAsk();assert.equal(g.surface.draft.reviewControl.body.operation_id,key);assert.equal(g.surface.draft.reviewControl.inspect,true);assert.equal(g.requests.length,0);
 const saved=f.journal.continuationDraft(parent);f.journal.saveContinuation({parentOperationId:parent,expectedRef:{id:parent,revision:saved.revision,sha256:saved.sha256},basis:saved.basis,text:'other writer',version:saved.version+1});assert.equal((await f.edit('my new text')).status,'ACTION_FAILED');assert.equal(f.surface.draft.guidanceText,'my new text');assert.equal(f.journal.continuationDraft(parent).text,'other writer');
});
test('acknowledged submission whose clear fails retains text and original key for read-only recovery',async t=>{
 const f=fixture(t),parent=await initial(f);await f.edit('keep until durable clear');const commit=f.journal.commit.bind(f.journal);let key;
 f.surface.onReviewControl=async value=>{key=value.body.operation_id;f.journal.prepareReviewControl(parent,{operationId:key,kind:value.kind,body:value.body});f.journal.commit=()=>{throw Error('disk full after ACK');};return {operation_id:key,status:'ACCEPTED'};};
 assert.equal((await f.command('send')).status,'ACTION_FAILED');assert.equal(f.surface.draft.guidanceText,'keep until durable clear');assert.equal(f.surface.draft.reviewControl.body.operation_id,key);assert.equal(f.surface.draft.reviewControl.inspect,true);
 assert.equal(new QuickJournal({dataDir:f.dataDir}).continuationDraft(parent).request.body.operation_id,key);f.journal.commit=commit;
});
test('hide waits for typing arriving during an earlier durable save',async t=>{
 const f=fixture(t),parent=await initial(f);await f.edit('first');const save=f.surface.onContinuationDraft;let release,started;const startedPromise=new Promise(resolve=>started=resolve),held=new Promise(resolve=>release=resolve);let once=true;
 f.surface.onContinuationDraft=async value=>{if(once){once=false;started();await held;}return save(value);};
 const hide=f.surface.hide({save:true});await startedPromise;const typing=f.edit('last input during save');release();await typing;await hide;
 assert.equal(new QuickJournal({dataDir:f.dataDir}).continuationDraft(parent).text,'last input during save');
});
test('lost save ACK is reconciled by an identical explicit save without overwriting or another model request',async t=>{
 const f=fixture(t),parent=await initial(f),save=f.surface.onContinuationDraft;let lose=true;f.surface.onContinuationDraft=async value=>{const result=save(value);if(lose){lose=false;throw Error('save response lost');}return result;};
 assert.equal((await f.edit('exact durable input')).status,'ACTION_FAILED');assert.equal(f.surface.draft.continuationRef,null);const revision=f.journal.state.revision;
 await f.surface.hide({save:true});assert.equal(f.journal.state.revision,revision);assert.equal(f.surface.draft.continuationRef.sha256,f.journal.continuationDraft(parent).sha256);assert.equal(f.requests.length,1);
});
