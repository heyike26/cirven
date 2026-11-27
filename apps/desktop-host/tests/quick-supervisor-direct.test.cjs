'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),vm=require('node:vm');
const {EventEmitter}=require('node:events'),{createRequire}=require('node:module');
const {QuickSurface,CHANNEL,STATE_CHANNEL}=require('../quick-surface.cjs');
const {SupervisorConversation,supervisorConversationDigest:digest}=require('../supervisor-conversation.cjs');
const root=path.resolve(__dirname,'../../..'),requireApp=createRequire(path.join(root,'apps/agent-shell/package.json'));
const compiled=requireApp('esbuild').transformSync(fs.readFileSync(path.join(root,'apps/agent-shell/src/supervisor-conversation-client.ts'),'utf8'),{loader:'ts',format:'cjs',target:'es2022'}).code;
const moduleValue={exports:{}};vm.runInNewContext(compiled,{module:moduleValue,exports:moduleValue.exports,require,setTimeout,clearTimeout,console});
const {SupervisorConversationClient}=moduleValue.exports;
const profile={id:'selected_model',revision:1,sha256:'a'.repeat(64)};
const snapshot=()=>({settings:{enabled:true},observer:{status:'READY',last_success_at:'2026-10-19T12:00:00Z'},tasks:[]});
class Window extends EventEmitter{
 constructor(options){super();this.bounds=options;this.destroyed=false;this.visible=false;this.webContents=new EventEmitter();this.webContents.mainFrame={url:''};this.webContents.getURL=()=>this.url;this.webContents.setWindowOpenHandler=()=>{};this.webContents.send=()=>{};}
 loadURL(url){this.url=url;this.webContents.mainFrame.url=url;return Promise.resolve();}getBounds(){return this.bounds;}setBounds(value){this.bounds=value;}setContentProtection(){}isDestroyed(){return this.destroyed;}isAlwaysOnTop(){return false;}hide(){this.visible=false;}showInactive(){this.visible=true;}show(){this.visible=true;}destroy(){this.destroyed=true;}
}
function fixture(t,dispatch){
 const dataDir=fs.mkdtempSync(path.join(os.tmpdir(),'cirven-orb-supervisor-'));t.after(()=>fs.rmSync(dataDir,{recursive:true,force:true}));
 const calls=[],saved=new Map(),listeners=new Set(),screen=new EventEmitter();screen.getCursorScreenPoint=()=>({x:500,y:500});screen.getDisplayNearestPoint=()=>({workArea:{x:0,y:0,width:1200,height:900}});
 const ipc={handle(_name,handler){this.handler=handler;},removeHandler(){}};let surface;
 const request=async(route,body)=>{calls.push({route,body});if(route==='/api/supervisor')return snapshot();if(body)saved.set(body.operation_id,body);if(dispatch)return dispatch(route,body,saved);return receipt(body??saved.get(route.split('/').at(-1)));};
 const ownerOptions={dataDir,request,onChange:value=>surface?.updateSupervisorConversation(value,profile)};let store=new SupervisorConversation(ownerOptions);
 const pageCalls=[];
 surface=new QuickSurface({BrowserWindow:Window,screen,ipcMain:ipc,onCapture:async()=>{pageCalls.push('capture');throw Error('Must not capture');},onDraft:async()=>{pageCalls.push('page-draft');},onSend:async()=>{pageCalls.push('page-send');},onInspect:async()=>{pageCalls.push('page-inspect');},onExpand:async()=>{pageCalls.push('expand');},onOpenArtifact:async()=>{},
 onGetSupervisorConversation:()=>store.snapshot(),onReadSupervisorHistory:body=>store.readHistoryPage(body),onReadSupervisorOperation:body=>store.readOperation(body),onSaveSupervisorDraft:body=>store.saveDraft(body),onAskSupervisor:body=>store.ask(body),onInspectSupervisor:id=>store.inspect(id),onSaveSupervisorLocalAnswer:body=>store.persistLocal(body),onState:value=>{if(value.supervisor.snapshot)for(const listener of listeners)listener(value.supervisor.snapshot);}});
 surface.updateSupervisorConversation(store.snapshot(),profile);
 const send=(payload,role='quickPopover')=>{const own=surface.windows[role];return ipc.handler({sender:own.webContents,senderFrame:own.webContents.mainFrame},payload);};
 const wire=async(kind,body)=>{const result=await send({kind,epoch:surface.epoch,...kind==='supervisor-inspect'?{operationId:body}:body!==undefined?{body}:{}});if(result.supervisor_error)throw Object.assign(Error(result.supervisor_error),{code:result.supervisor_error});return result;};
 const client=new SupervisorConversationClient({getSnapshot:()=>surface.visible?wire('supervisor-get'):Promise.resolve(store.snapshot()),readHistoryPage:body=>wire('supervisor-history-read',body),readOperation:body=>wire('supervisor-operation-read',body),saveDraft:body=>wire('supervisor-draft-save',body),ask:body=>wire('supervisor-ask',body),inspect:id=>wire('supervisor-inspect',id),persistLocal:body=>wire('supervisor-local-save',body),onChange:listener=>{listeners.add(listener);return()=>listeners.delete(listener);}},()=>{});
 surface.windows.quickPopover.webContents.send=(channel,value)=>{if(channel===STATE_CHANNEL&&value.kind==='supervisor-flush-request')void client.flush().then(()=>send({kind:'supervisor-flush-complete',id:value.id,epoch:value.epoch,status:'SAVED'}),()=>send({kind:'supervisor-flush-complete',id:value.id,epoch:value.epoch,status:'FAILED'}));};
 t.after(()=>surface.dispose());return{surface,get store(){return store;},reopen(){store=new SupervisorConversation(ownerOptions);surface.updateSupervisorConversation(store.snapshot(),profile);},client,send,wire,calls,pageCalls,profile};
}
function receipt(body){return{operation_id:body.operation_id,project_id:null,conversation_id:body.conversation_id,context_ref:null,file_source_ref:null,file_link_ref:null,provider_profile_ref:body.provider_profile_ref,request_sha256:digest(body),automatic_retry:false,status:'RETURNED',text:'A reply in the same coordinator conversation.'};}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('inline coordinator shares actual host conversation, public profile and local history with no page or model call',async t=>{
 const f=fixture(t);await f.surface.openActivity(snapshot());await f.send({kind:'state'});await f.client.start();
 assert.equal(f.surface.get().supervisor.snapshot.conversation_id,f.store.snapshot().conversation_id);
 f.client.edit('任务状态');await f.client.persistLocal({question:'任务状态',answer:{text:'0项运行中'}});
 assert.equal(f.store.snapshot().messages.at(-1).text,'0项运行中');assert.equal(f.client.view.draft,'');assert.equal(f.calls.length,0);assert.deepEqual(f.pageCalls,[]);
 const result=await f.send({kind:'command',epoch:f.surface.epoch,command:'activity-supervisor'});assert.equal(result.status,'SUPERVISOR_INLINE');assert.equal(f.surface.visible,true);await f.client.close();
});
test('coordinator wire denies stale, overlay, hidden, extra body fields and different profile before a service mutation',async t=>{
 const f=fixture(t);await f.surface.openActivity(snapshot());const epoch=f.surface.epoch;
 const body={operationId:'ask_one',text:'Please analyse this task status',providerProfileRef:profile};
 for(const payload of [{kind:'supervisor-ask',epoch,body:{...body,operationId:null}},{kind:'supervisor-inspect',epoch,operationId:null},{kind:'supervisor-ask',epoch,body:{...body,automatic:true}},{kind:'supervisor-ask',epoch,body:{...body,providerProfileRef:{...profile,revision:2}}},{kind:'supervisor-draft-save',epoch,body:{text:'x',expectedRevision:0,unsafe:true}},{kind:'supervisor-local-save',epoch,body:{question:'x',answer:{text:'x',unsafe:true}}}])assert.equal((await f.send(payload)).status,'ACTION_FAILED');
 await assert.rejects(f.send({kind:'supervisor-get',epoch:epoch-1}),/Stale/);await assert.rejects(f.send({kind:'supervisor-get',epoch},'annotationOverlay'),/Untrusted|Stale/);
 await f.surface.hide({save:false,animate:false});await assert.rejects(f.send({kind:'supervisor-get',epoch:f.surface.epoch}),/hidden/);
 assert.equal(f.calls.length,0);assert.equal(f.store.snapshot().draft.text,'');assert.deepEqual(f.pageCalls,[]);
});
test('actual client keeps UNKNOWN under original key and inspects it without replaying the same question',async t=>{
 let available=false;const f=fixture(t,async(route,body,saved)=>{if(body||!available)throw Error('external service unavailable');return receipt(saved.get(route.split('/').at(-1)));});
 await f.surface.openActivity(snapshot());await f.client.start();f.client.edit('Analyse current engineering risks');
 assert.equal((await f.client.ask({operationId:'original_question',text:f.client.view.draft,providerProfileRef:profile})).status,'UNKNOWN');
 await assert.rejects(f.client.ask({operationId:'different_key',text:'Analyse current engineering risks',providerProfileRef:profile}),error=>error.code==='SUPERVISOR_UNKNOWN_INPUT_REPLAY_BLOCKED');
 available=true;assert.equal((await f.client.inspect('original_question')).status,'RETURNED');assert.equal(f.calls.filter(row=>row.body).length,1);assert.deepEqual(f.pageCalls,[]);await f.client.close();
});
test('collapse flushes newest coordinator draft without waiting for a running model and keeps the page draft intact',async t=>{
 let release;const gate=new Promise(resolve=>release=resolve);const f=fixture(t,async(_route,body)=>{await gate;return receipt(body);});
 await f.surface.openActivity(snapshot());await f.send({kind:'state'});await f.client.start();
 const pageDraft={id:'retained_page',phase:'EDITING',touched:false,comment:'Original page comment',marks:[],capture:null,readonly:false,revision:0,savePending:0};f.surface.draft=pageDraft;
 f.client.edit('Analyse current engineering risks');const asking=f.client.ask({operationId:'running_question',text:f.client.view.draft,providerProfileRef:profile});
 while(!f.calls.some(row=>row.body))await tick();f.client.edit('New question typed while thinking');
 const hidden=await Promise.race([f.surface.hide({save:true,animate:false}),new Promise((_,reject)=>setTimeout(()=>reject(Error('hide waited for model')),1000))]);
 assert.equal(hidden.visible,false);assert.equal(f.store.snapshot().draft.text,'New question typed while thinking');assert.equal(f.surface.draft,pageDraft);assert.equal(pageDraft.comment,'Original page comment');
 release();await asking;assert.equal(f.store.snapshot().draft.text,'New question typed while thinking');await f.client.close();assert.deepEqual(f.pageCalls,[]);
});
test('draft CAS error reaches the real client and refuses collapse while preserving local text',async t=>{
 const f=fixture(t);await f.surface.openActivity(snapshot());await f.send({kind:'state'});await f.client.start();f.client.edit('Local input before conflict');
 f.store.saveDraft({text:'Changed in titlebar',expectedRevision:f.store.snapshot().draft.revision});
 assert.equal(f.client.view.draftConflict,true);await assert.rejects(f.surface.hide({save:true,animate:false}),/总管草稿/);
 assert.equal(f.surface.visible,true);assert.equal(f.client.view.draft,'Local input before conflict');assert.equal(f.calls.length,0);
 f.client.resolveDraftConflict('keep-local');await f.client.flush();await f.surface.hide({save:true,animate:false});assert.equal(f.store.snapshot().draft.text,'Local input before conflict');await f.client.close();
});
test('explicit connection settings preserves draft and window on failure, collapsing only after actual open acknowledgement',async t=>{
 const f=fixture(t);await f.surface.openActivity(snapshot());await f.send({kind:'state'});await f.client.start();f.client.edit('Retain this question during configuration');
 let calls=0;f.surface.onOpenSupervisor=async options=>{assert.deepEqual(options,{configure:true});calls++;return {status:'UNAVAILABLE'};};
 let result=await f.send({kind:'command',epoch:f.surface.epoch,command:'supervisor-configure'});assert.equal(result.status,'ACTION_FAILED');assert.equal(f.surface.visible,true);assert.equal(f.store.snapshot().draft.text,'Retain this question during configuration');
 f.surface.onOpenSupervisor=async()=>{calls++;return {status:'OPENED'};};result=await f.send({kind:'command',epoch:f.surface.epoch,command:'supervisor-configure'});assert.equal(result.status,'CONFIGURATION_OPENED');assert.equal(f.surface.visible,false);assert.equal(calls,2);assert.deepEqual(f.pageCalls,[]);await f.client.close();
});
test('coordinator broadcasts omit the old page PNG while retaining the exact internal annotation basis',async t=>{
 const f=fixture(t);await f.surface.openActivity(snapshot());const capture={snapshot:{preview:{status:'AVAILABLE',data_url:'data:image/png;base64,YQ=='},observation:{context_ref:{id:'page',revision:1,sha256:'b'.repeat(64)}}}};
 const old=f.surface._newDraft();old.capture=capture;old.comment='Retained page annotation';f.surface.draft=old;
 assert.equal(f.surface.get().source.image,null);assert.equal(f.surface.draft.capture,capture);assert.equal(f.surface.draft.capture.snapshot.preview.data_url,'data:image/png;base64,YQ==');
 f.surface.updateSupervisorConversation(f.store.snapshot(),profile);assert.equal(f.surface.get().source.image,null);assert.deepEqual(f.pageCalls,[]);
});
test('floating same-store history pages and old operation reads remain read-only after a real terminal segment and cold reopen',async t=>{
 const f=fixture(t);await f.surface.openActivity(snapshot());await f.client.start();await f.client.ask({operationId:'old_original',text:'旧原问题',providerProfileRef:profile});
 const state=JSON.parse(JSON.stringify(f.store.state)),old=state.operations[0];state.revision++;state.operations=Array.from({length:128},(_,i)=>i?{...old,operation_id:'seed_'+i,input_sha256:digest({operation_id:'seed_'+i,text:old.question,provider_profile_ref:profile}),request_sha256:digest({fixture:i}),sequence:i+1}:{...old,sequence:1});state.history_sequence=128;
 fs.writeFileSync(f.store.file,JSON.stringify({version:1,state,sha256:digest(state)})+'\n');f.reopen();await f.client.getSnapshot();await f.client.ask({operationId:'after_128',text:'新明确问题',providerProfileRef:profile});f.reopen();await f.client.getSnapshot();
 assert.ok(!f.client.view.snapshot.operations.some(row=>row.operation_id==='old_original'));const count=f.calls.length,head=fs.readFileSync(f.store.file);f.client.edit('分页期间保留输入');await f.client.readEarlier();await f.client.readEarlier();assert.ok(f.client.view.history.messages.length>48);
 const op=await f.client.readOperation({operation_id:'old_original',request_sha256:old.request_sha256});assert.equal(op.status,'RETURNED');assert.equal(f.client.view.draft,'分页期间保留输入');assert.equal(f.calls.length,count);assert.deepEqual(fs.readFileSync(f.store.file),head);assert.deepEqual(f.pageCalls,[]);
 for(const body of [{cursor:null,limit:25},{cursor:null,limit:24,path:'../other'}])assert.equal((await f.send({kind:'supervisor-history-read',epoch:f.surface.epoch,body})).status,'ACTION_FAILED');
 assert.equal((await f.send({kind:'supervisor-operation-read',epoch:f.surface.epoch,body:{operation_id:'old_original',request_sha256:'b'.repeat(64)}})).supervisor_error,'SUPERVISOR_QUESTION_REFERENCE_CHANGED');
 await f.client.close();
});
