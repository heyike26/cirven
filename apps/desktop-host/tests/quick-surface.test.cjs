'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {EventEmitter}=require('node:events');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {createHash}=require('node:crypto');
const os=require('node:os');
const {QuickJournal}=require('../quick-journal.cjs');
const {QuickHost}=require('../quick-host.cjs');
const {createAssistantOrbActions}=require('../assistant-orb-actions.cjs');
const {QuickSurface,CHANNEL,normalizedMarks,markRegions,annotationBounds,popupBounds,quickHtml}=require('../quick-surface.cjs');
const sha='a'.repeat(64),image='data:image/png;base64,YQ==';
// These controller tests have a minimal fake DOM. The real bundled Markdown
// renderer is exercised separately in quick-reply browser tests, without stubs.
function controllerPreloadSource(){return fs.readFileSync(path.resolve(__dirname,'../quick-surface-preload.cjs'),'utf8')
  .replace("const {renderQuickReply,replyLinkTarget}=require('./quick-reply.mjs');",`const renderQuickReply=(element,{text=''})=>{if(element){element.textContent=text;element.hidden=!text;}},replyLinkTarget=()=>null;`);}
test('sandbox preload artifact has exact registered source inputs and only Electron runtime external',()=>{
 const root=path.resolve(__dirname,'../../..'),manifest=require('../quick-surface-preload.build.json');
 const record=relative=>{const bytes=fs.readFileSync(path.join(root,relative));return {bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')};};
 assert.deepEqual(manifest.runtime_externals,['electron']);
 assert.deepEqual(record(manifest.output.path),{bytes:manifest.output.bytes,sha256:manifest.output.sha256});
 assert.deepEqual(record(manifest.host_output.path),{bytes:manifest.host_output.bytes,sha256:manifest.host_output.sha256});
 for(const [relative,pin]of Object.entries(manifest.inputs))assert.deepEqual(record(relative),pin,relative);
 assert.equal(manifest.inputs['apps/agent-shell/node_modules/marked/package.json'].sha256,record('apps/agent-shell/node_modules/marked/package.json').sha256);
 assert.ok(manifest.inputs['apps/agent-shell/src/summary-content.ts']);
});
test('host link membership uses rendered Markdown links including references and excludes media, code and raw HTML',()=>{
 const {replyLinkTargets}=require('../quick-reply-links.bundle.cjs');
 const source='[Web](https://example.com) [Mail](mailto:qa@example.com) [Ref][r]\n\n[r]: http://example.org/path\n\n'+
  '![image](https://image.invalid) `https://code.invalid`\n\n```\nhttps://block.invalid\n```\n\n<a href="https://html.invalid">raw</a> [Bad](javascript:alert(1))';
 assert.deepEqual(replyLinkTargets(source),['https://example.com/','mailto:qa@example.com','http://example.org/path']);
});
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
class OwnWindow extends EventEmitter {
  constructor(options){super();this.options=options;this.bounds={x:options.x,y:options.y,width:options.width,height:options.height};
    this.visible=false;this.destroyed=false;this.pinned=options.alwaysOnTop===true;this.calls=[];this.webContents=new EventEmitter();this.webContents.mainFrame={url:''};
    this.webContents.url='';this.webContents.send=(...args)=>this.calls.push(['send',...args]);
    this.webContents.getURL=()=>this.webContents.url;this.webContents.setWindowOpenHandler=handler=>{this.openHandler=handler;};}
  loadURL(url){this.calls.push(['load',url]);this.webContents.url=url;this.webContents.mainFrame.url=url;return Promise.resolve();}
  getBounds(){return {...this.bounds};}
  setBounds(bounds){this.bounds={...bounds};this.calls.push(['bounds',bounds]);}
  setTitle(value){this.title=value;}
  isDestroyed(){return this.destroyed;}
  isAlwaysOnTop(){return this.pinned;}
  setAlwaysOnTop(value){this.pinned=value;this.calls.push(['pin',value]);}
  setContentProtection(value){this.calls.push(['protection',value]);}
  showInactive(){this.visible=true;this.calls.push(['inactive']);}
  show(){this.visible=true;this.calls.push(['show']);}
  focus(){throw new Error('No delayed focus allowed');}
  hide(){this.visible=false;this.calls.push(['hide']);}
  destroy(){this.destroyed=true;this.calls.push(['destroy']);this.emit('closed');}
}
function capture(overrides={}){return {snapshot:{operation_id:'capture_actual',current:true,observation:{status:'OBSERVED',context_ref:{id:'context',revision:1,sha256:sha},window:{title:'正在阅读的实际页面'}},preview:{status:'AVAILABLE',data_url:image}},
  scope:{privateToken:'must-not-enter-renderer'},geometry:{verified:true,physicalBounds:{x:100,y:80,width:900,height:600},pixelWidth:900,pixelHeight:600},...overrides};}
function fixture(overrides={}){
  const events=[],errors=[],screen=new EventEmitter(),ipc={handlers:new Map(),handle(channel,handler){this.handlers.set(channel,handler);},removeHandler(channel){this.handlers.delete(channel);}};
  screen.getCursorScreenPoint=()=>({x:150,y:700});
  screen.getDisplayNearestPoint=()=>({id:1,workArea:{x:0,y:0,width:1400,height:900}});
  screen.getAllDisplays=()=>[{id:1,bounds:{x:0,y:0,width:1400,height:900}}];
  screen.screenToDipRect=(_window,bounds)=>({...bounds});
  const callbacks={BrowserWindow:OwnWindow,screen,ipcMain:ipc,
    onCapture:async value=>{events.push(['capture',value]);return capture();},
    onDraft:async value=>{events.push(['draft',value]);return {id:value.draftId,revision:value.expectedRevision+1,sha256:sha,status:value.delivery};},
    onContinuationDraft:async value=>({...value,revision:(value.expectedRef?.revision??0)+1,sha256:sha}),
    onSend:async value=>{events.push(['send',value]);return {operation_id:value.operationId,status:'RUNNING'};},
    onInspect:async value=>{events.push(['inspect',value]);return {operation_id:value,status:'COMPLETED',text:'来自原操作的真实回复'};},
    onRetainSubmitted:async value=>{events.push(['retain',value]);return {status:'RETAINED',operationId:value.operationId,ref:{id:value.draftId,revision:2,sha256:sha}};},
    onExpand:async value=>{events.push(['expand',value]);},onOpenArtifact:async value=>{events.push(['open',value]);},onError:value=>errors.push(value),...overrides};
  const surface=new QuickSurface(callbacks);
  const send=(payload,role='quickPopover',event)=>{
    const window=surface.windows[role];return ipc.handlers.get(CHANNEL)(event??{sender:window.webContents,senderFrame:window.webContents.mainFrame},payload);
  };
  const command=(value,role='quickPopover')=>send({kind:'command',epoch:surface.get().epoch,command:value},role);
  const edit=(comment='这里的接口定义需要解释',marks=[])=>send({kind:'edit',epoch:surface.get().epoch,comment,marks});
  if(overrides.autoDecode!==false){
    const own=surface.windows.annotationOverlay,originalSend=own.webContents.send;
    own.webContents.send=(channel,state)=>{
      originalSend(channel,state);
      if(state?.overlayPreparing)queueMicrotask(()=>{if(surface.get().overlayPreparing)void send({kind:'preview-ready',epoch:surface.get().epoch},'annotationOverlay');});
    };
  }
  return {surface,events,errors,screen,ipc,send,command,edit};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('explicit ask activates after unchanged native guard, while later result performs no focus read or activation',async()=>{
  let reads=0;const native={schema:'eeas.quick-activation-state.v1',status:'OBSERVED',authority_generation:'c'.repeat(32),
    foreground_identity_sha256:sha,host_scope_sha256:sha,last_input_tick:43,automatic_input_authorized:false};
  const f=fixture({onActivationState:async()=>{reads++;return native;}});
  try{
    await f.surface.openAsk();assert.equal(reads,2);assert.equal(f.surface.get().visible,true);
    assert.equal(f.surface.windows.quickPopover.calls.filter(c=>c[0]==='show').length,1);
    await f.edit('输入与原稿保留');await f.command('send');
    const shows=f.surface.windows.quickPopover.calls.filter(c=>c[0]==='show').length;
    f.surface.publishResult({operation_id:f.surface.draft.operationId,status:'RETURNED',text:'后台已返回'});
    assert.equal(reads,2);assert.equal(f.surface.windows.quickPopover.calls.filter(c=>c[0]==='show').length,shows);
  }finally{f.surface.dispose();}
});

test('shortcut release is settled only before capture; cancelling capture does not wait for another guard',async()=>{
  const pending=deferred(),options=[];
  const native={schema:'eeas.quick-activation-state.v1',status:'OBSERVED',authority_generation:'c'.repeat(32),
    foreground_identity_sha256:sha,host_scope_sha256:sha,last_input_tick:43,automatic_input_authorized:false};
  const f=fixture({onActivationState:async option=>{options.push(option);return native;},onCapture:()=>pending.promise});
  try{
    const first=f.surface.openAsk({trigger:'CTRL_SHIFT_SPACE'});await tick();
    assert.equal(f.surface.get().capturePending,true);
    assert.deepEqual(options,[{phase:'begin',trigger:'CTRL_SHIFT_SPACE'}]);
    assert.equal((await f.surface.openAsk({trigger:'CTRL_SHIFT_SPACE'})).status,'CANCELLED');
    assert.equal(options.length,1);pending.resolve(capture());assert.equal((await first).status,'STALE');
    assert.equal(f.surface.get().visible,false);
  }finally{pending.resolve(capture());f.surface.dispose();}
});

test('input or window change during capture leaves Quick visible without activation and keeps the exact text',async()=>{
  for(const field of ['last_input_tick','foreground_identity_sha256','host_scope_sha256']){
    const pending=deferred();let reads=0;
    const f=fixture({onActivationState:async()=>{
      const state={schema:'eeas.quick-activation-state.v1',status:'OBSERVED',authority_generation:'c'.repeat(32),
        foreground_identity_sha256:sha,host_scope_sha256:sha,last_input_tick:43,automatic_input_authorized:false};
      if(++reads===2)state[field]=field==='last_input_tick'?44:'b'.repeat(64);
      return state;
    },onCapture:()=>pending.promise});
    try{
      const opened=f.surface.openAsk();await tick();pending.resolve(capture());await opened;
      assert.equal(f.surface.get().visible,true);assert.equal(reads,2);
      assert.equal(f.surface.windows.quickPopover.calls.filter(c=>c[0]==='show').length,0);
      await f.edit('用户新输入，保持原对象');assert.equal(f.surface.draft.comment,'用户新输入，保持原对象');
    }finally{f.surface.dispose();}
  }
});

test('hide during final activation read invalidates the explicit gesture without a late reveal',async()=>{
  const final=deferred();let reads=0;
  const state={schema:'eeas.quick-activation-state.v1',status:'OBSERVED',authority_generation:'c'.repeat(32),
    foreground_identity_sha256:sha,host_scope_sha256:sha,last_input_tick:43,automatic_input_authorized:false};
  const f=fixture({onActivationState:()=>++reads===1?Promise.resolve(state):final.promise});
  try{
    const opened=f.surface.openAsk();await tick();assert.equal(reads,2);
    await f.surface.hide({save:false});final.resolve(state);assert.equal((await opened).status,'STALE');
    assert.equal(f.surface.get().visible,false);assert.equal(f.surface.windows.quickPopover.calls.filter(c=>c[0]==='show').length,0);
  }finally{f.surface.dispose();}
});

test('guarded native reveal focuses the same editor only after document focus; stale or interrupted requests never steal it',async()=>{
  const native={schema:'eeas.quick-activation-state.v1',status:'OBSERVED',authority_generation:'c'.repeat(32),
    foreground_identity_sha256:sha,host_scope_sha256:sha,last_input_tick:43,automatic_input_authorized:false};
  const f=fixture({onActivationState:async()=>native});
  try{
    await f.surface.ready;const r=preloadBridge(f,'popover');let focused=false;
    r.document.hasFocus=()=>focused;
    await f.surface.openAsk();const comment=r.elements.get('#comment');
    assert.notEqual(r.document.activeElement,comment);
    focused=true;r.callbacks.focus();assert.equal(r.document.activeElement,comment);
    comment.selectionStart=1;comment.selectionEnd=3;
    r.callbacks.state(null,{kind:'focus-input',epoch:f.surface.epoch,draftId:f.surface.draft.id,requestedAt:Date.now()});
    assert.equal(comment.selectionStart,1);assert.equal(comment.selectionEnd,3);
    const other=r.elements.get('#source-label');r.document.activeElement=other;
    for(const patch of [{draftId:'other'}, {epoch:f.surface.epoch-1}, {requestedAt:Date.now()-501}]){
      r.callbacks.state(null,{kind:'focus-input',epoch:f.surface.epoch,draftId:f.surface.draft.id,requestedAt:Date.now(),...patch});
      assert.equal(r.document.activeElement,other);
    }
    focused=false;r.callbacks.state(null,{kind:'focus-input',epoch:f.surface.epoch,draftId:f.surface.draft.id,requestedAt:Date.now()});
    r.callbacks.pointerdown();focused=true;r.callbacks.focus();assert.equal(r.document.activeElement,other);
  }finally{f.surface.dispose();}
});

test('local send preflight preserves the visible editor and never dispatches or hides before acceptance',async()=>{
  let allowed=false,preflights=0,sends=0;
  const f=fixture({onPrepareSend:async()=>{preflights++;if(!allowed)throw Error('Unverified input');},
    onSend:async value=>{sends++;return {operation_id:value.operationId,status:'RETURNED',text:'Only accepted submission'};}});
  try{
    await f.surface.openAsk();await f.edit('原稿与 emoji 😀');const draft=f.surface.draft;
    const hides=f.surface.windows.quickPopover.calls.filter(x=>x[0]==='hide').length;
    assert.equal((await f.command('send')).status,'ACTION_FAILED');
    assert.equal(f.surface.draft,draft);assert.equal(draft.comment,'原稿与 emoji 😀');assert.equal(draft.phase,'EDITING');
    assert.equal(draft.operationId??null,null);assert.equal(f.surface.get().visible,true);assert.equal(sends,0);
    assert.equal(f.surface.windows.quickPopover.calls.filter(x=>x[0]==='hide').length,hides);
    assert.match(f.surface.get().error,/尚未发送/);
    allowed=true;await f.command('send');assert.equal(preflights,2);assert.equal(sends,1);assert.equal(draft.phase,'COMPLETED');
  }finally{f.surface.dispose();}
});

test('trusted local refusal restores only the matching unsent draft; fake and unrelated claims stay unknown',async()=>{
  const {QuickNotSubmittedError}=require('../quick-not-submitted.cjs');
  for(const kind of ['exact','serialized','other-operation','other-version']){
    let count=0;
    const f=fixture({onSend:async value=>{
      count++;
      if(count>1)return {operation_id:value.operationId,status:'RETURNED',text:'One explicit retry'};
      const ref={id:value.draftId,revision:f.surface.draft.revision,sha256:sha};
      if(kind==='serialized')throw Object.assign(Error('Untrusted claim'),{operationId:value.operationId,draftRef:ref});
      throw new QuickNotSubmittedError(kind==='other-operation'?'another-operation':value.operationId,
        {...ref,revision:ref.revision+(kind==='other-version'?1:0)},Error('Local validation failed'));
    }});
    try{
      await f.surface.openAsk();await f.edit('保留问题和原对象');await f.command('send');
      assert.equal(count,1);assert.equal(f.surface.draft.comment,'保留问题和原对象');
      assert.equal(f.surface.draft.phase,kind==='exact'?'EDITING':'UNKNOWN');
      if(kind==='exact'){
        assert.equal(f.surface.draft.operationId,null);assert.equal(f.surface.get().visible,true);
        await f.command('send');assert.equal(count,2);assert.equal(f.surface.draft.phase,'COMPLETED');
      }else{await f.surface.openAsk();await f.command('send');assert.equal(count,1);}
    }finally{f.surface.dispose();}
  }
});

test('late local preflight refusal cannot reopen or change a hidden draft',async()=>{
  const check=deferred();const f=fixture({onPrepareSend:()=>check.promise});
  try{
    await f.surface.openAsk();await f.edit('迟回仍保留');const sending=f.command('send');await tick();
    await f.surface.hide({save:false});check.reject(Error('Late local refusal'));await sending;
    assert.equal(f.surface.get().visible,false);assert.equal(f.events.filter(x=>x[0]==='send').length,0);
    assert.equal(f.surface.draft.comment,'迟回仍保留');assert.equal(f.surface.draft.operationId??null,null);
  }finally{f.surface.dispose();}
});

function discussionFixture(t,{save,readCapture}={}){
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'quick-discussion-'));
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  let captures=0;
  const journal=new QuickJournal({dataDir:directory,hostGeneration:'discussion_host'});
  const host=new QuickHost({journal,getCompanion:()=>({capture:readCapture??(async()=>({operation_id:'no_page_'+(++captures),current:false,observation:{status:'UNAVAILABLE'}}))}),
    request:async()=>{throw Error('Discussion switch cannot invoke a model or dispatch');}});
  const f=fixture({onEmptyCapture:()=>host.emptyCapture(),onCapture:body=>host.capture(body),
    onDraft:body=>save?save(body,host):host.save(body),onLoadDraft:body=>host.load(body)});
  const scope=name=>({conversationId:'chat_'+name,conversationRef:{id:'native:'+name.repeat(64),revision:1}});
  return {...f,host,journal,directory,scope,select:async name=>f.surface.syncDiscussion(host.setScope(scope(name)))};
}

test('switching untouched empty input creates no stored draft, while clearing a previously saved draft stays durable',async t=>{
  const f=discussionFixture(t);
  try{
    await f.select('a');await f.surface.openAsk();await f.edit('');await f.select('b');
    assert.equal(Object.keys(f.journal.state.records).length,0);
    await f.surface.openAsk();await f.edit('B 旧文字');await f.select('a');await f.surface.openAsk();await f.select('b');await f.surface.openAsk();
    const id=f.surface.draft.id;await f.edit('');await f.select('a');assert.equal(f.journal.find(id).comment,'');
  }finally{f.surface.dispose();}
});

test('provider or owner revision refresh within the same discussion preserves the active editor and capture',async t=>{
  const f=discussionFixture(t);
  try{
    await f.select('a');await f.surface.openAsk();await f.edit('同讨论不重置');const draft=f.surface.draft,epoch=f.surface.epoch;
    const scope=f.host.setScope({...f.scope('a'),conversationRef:{...f.scope('a').conversationRef,revision:2},providerProfileRef:{id:'p',revision:1,sha256:sha}});
    await f.surface.syncDiscussion(scope);assert.equal(f.surface.draft,draft);assert.equal(f.surface.epoch,epoch);assert.equal(f.surface.visible,true);
  }finally{f.surface.dispose();}
});

test('actual Surface Host Journal isolate A B A drafts and restore B from a fresh journal without dispatch',async t=>{
  const f=discussionFixture(t);
  try{
    await f.select('a');await f.surface.openAsk();await f.edit('A 的草稿 😀');const a=f.surface.draft.id;
    await f.select('b');assert.equal(f.surface.get().visible,false);assert.equal(f.surface.draft,null);
    assert.equal(f.journal.find(a).scope.conversationRef.id,f.scope('a').conversationRef.id);
    await f.surface.openAsk();assert.equal(f.surface.draft.comment,'');await f.edit('B 保留自己的文字');const b=f.surface.draft.id;
    await f.select('a');await f.surface.openAsk();assert.equal(f.surface.draft.id,a);assert.equal(f.surface.draft.comment,'A 的草稿 😀');
    assert.equal(f.journal.find(b).comment,'B 保留自己的文字');
    const cold=new QuickJournal({dataDir:f.directory,hostGeneration:'cold_reader'});
    const host=new QuickHost({journal:cold,getCompanion:()=>({}),request:async()=>{throw Error('No dispatch on reload');}});
    host.setScope(f.scope('b'));const restored=await host.load();assert.equal(restored.id,b);assert.equal(restored.comment,'B 保留自己的文字');
    assert.deepEqual(Object.keys(cold.state.commands),[]);
  }finally{f.surface.dispose();}
});

test('failed switch preserves exact old draft, blocks send, and explicit open retries old save before loading target',async t=>{
  let fail=true;
  const f=discussionFixture(t,{save:(body,host)=>{if(fail)throw Object.assign(Error('disk full'),{code:'ENOSPC'});return host.save(body);}});
  try{
    await f.select('a');await f.surface.openAsk();await f.edit('没有保存成功前不要丢失');const id=f.surface.draft.id;
    await assert.rejects(f.select('b'),/disk full/);
    assert.equal(f.surface.draft.id,id);assert.equal(f.surface.draft.comment,'没有保存成功前不要丢失');assert.equal(f.surface.get().canSend,false);
    await assert.rejects(f.command('send'),/原稿仍保留/);assert.deepEqual(Object.keys(f.journal.state.commands),[]);
    fail=false;await f.surface.openAsk();assert.equal(f.surface.draft.comment,'');
    assert.equal(f.surface.draft.capture.scope.conversationId,'chat_b');assert.equal(f.journal.find(id).scope.conversationId,'chat_a');
    assert.equal(f.journal.find(id).comment,'没有保存成功前不要丢失');
  }finally{f.surface.dispose();}
});

test('successive selections share one bounded save and only latest C opens after the original A barrier',async t=>{
  const gate=deferred();let writes=0;
  const f=discussionFixture(t,{save:async(body,host)=>{writes++;await gate.promise;return host.save(body);}});
  try{
    await f.select('a');await f.surface.openAsk();await f.edit('A 的原稿');
    const b=f.select('b');await tick();const c=f.select('c');const open=f.surface.openAsk();await tick();
    assert.equal(f.surface.scopeSwitching,true);assert.equal(writes,1);
    gate.resolve();await Promise.all([b,c,open]);assert.equal(f.surface.draft.capture.scope.conversationId,'chat_c');
    assert.equal(f.surface.draft.comment,'');assert.equal(writes,1);
    assert.deepEqual(f.journal.list({editing:true}).map(row=>row.scope.conversationId),['chat_a']);
  }finally{gate.resolve();f.surface.dispose();}
});

test('late old capture cannot reappear after discussion change and target capture remains bound to B',async t=>{
  const gate=deferred();let reads=0;
  const f=discussionFixture(t,{readCapture:async()=>++reads===1?gate.promise:{operation_id:'capture_b',current:false,observation:{status:'UNAVAILABLE'}}});
  try{
    await f.select('a');const old=f.surface.openAsk();await tick();assert.equal(f.surface.capturePending,true);
    await f.select('b');await f.surface.openAsk();const id=f.surface.draft.id;
    gate.resolve({operation_id:'late_capture_a',current:false,observation:{status:'UNAVAILABLE'}});
    assert.equal((await old).status,'STALE');assert.equal(f.surface.draft.id,id);
    assert.equal(f.surface.draft.capture.scope.conversationId,'chat_b');assert.equal(f.surface.visible,true);
  }finally{gate.resolve({});f.surface.dispose();}
});

test('pure-text draft retains its original trusted basis even when page capture throws before a switch',async t=>{
  const f=discussionFixture(t);f.surface.onCapture=async()=>{throw Error('Source unavailable');};
  try{
    await f.select('a');await f.surface.openAsk();assert.equal(f.surface.draft.capture,null);await f.edit('无截图的A意见');const id=f.surface.draft.id;
    await f.select('b');assert.equal(f.journal.find(id).scope.conversationId,'chat_a');
    assert.equal(f.journal.find(id).comment,'无截图的A意见');
  }finally{f.surface.dispose();}
});

test('real Surface Host and Journal keep rejected page input then explicitly send one corrected WGC attachment',async t=>{
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'wgc-input-admission-'));
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  let valid=false,captures=0;const calls=[],page={id:'frozen_page',revision:1,sha256:sha},target={id:sha,revision:1,sha256:sha};
  const bounds={x:0,y:0,width:100,height:80},imageRef={sha256:sha,width:100,height:80};
  const journal=new QuickJournal({dataDir:directory,hostGeneration:'same_host'});
  const host=new QuickHost({journal,getCompanion:()=>({capture:async()=>({operation_id:'capture_contract_fixture_'+(++captures),current:true,
    preview:{status:'AVAILABLE',data_url:image,image_ref:imageRef},observation:{status:'READY_OBSERVATION',context_ref:{...page,id:page.id+'_'+captures},
      window_ref:target,window_lifetime_sha256:target.id,window:{title:'Controlled producer contract',bounds},coverage:{visual:true},
      observations:{visual:{width:100,height:80,image_ref:imageRef,target_ref:target,pixel_source:'EXACT_TARGET_WINDOW',
        pixel_source_proof:{status:'EXACT_TARGET_WINDOW',capture_method:valid?'WINDOWS_GRAPHICS_CAPTURE_EXACT_HWND':'UNVERIFIED',
          coordinate_space:'PHYSICAL_PIXELS_OWN_THREAD_PMV2',self_exclusion_verified:true,image_sha256:sha,
          target_identity_sha256:target.id,target_bounds:bounds,capture_bounds:bounds}}}}})}),
    request:async(url,body)=>{calls.push({url,body});return {operation_id:body.operation_id,conversation_ref:{id:'native:'+sha,revision:1},
      status:'RETURNED',response:{text:'同一讨论的回答'},automatic_post_replay:false,model_calls_by_query:0,owner_started_by_query:false};}});
  host.setScope({conversationId:'same_chat',conversationRef:{id:'native:'+sha,revision:1},providerProfileRef:{id:'provider',revision:1,sha256:sha}});
  const f=fixture({onCapture:body=>host.capture(body),onDraft:body=>host.save(body),
    onPrepareSend:body=>host.preflightSend(body),onSend:body=>host.send(body)});
  try{
    await f.surface.openAsk();await f.edit('不要丢失这条意见 😀');const id=f.surface.draft.id;
    await f.command('send');assert.equal(f.surface.get().visible,true);assert.equal(f.surface.draft.phase,'EDITING');
    assert.equal(calls.length,0);assert.equal(Object.keys(journal.state.commands).length,0);
    const rejected=new QuickJournal({dataDir:directory,hostGeneration:'reader'}).find(id);
    assert.equal(rejected.comment,'不要丢失这条意见 😀');assert.equal(rejected.delivery,'EDITING');
    valid=true;await f.command('recapture');assert.equal(f.surface.draft.id,id);assert.equal(f.surface.draft.comment,rejected.comment);
    await f.command('send');assert.equal(f.surface.draft.phase,'COMPLETED');assert.equal(calls.length,1);
    assert.equal(calls[0].url,'/api/conversations/native%3A'+sha+'/messages');
    assert.equal(calls[0].body.raw_text,rejected.comment);assert.deepEqual(calls[0].body.input_attachments[0].context_ref,{...page,id:'frozen_page_2'});
    assert.equal(calls[0].body.input_attachments[0].image_sha256,sha);
    const reopened=new QuickJournal({dataDir:directory,hostGeneration:'restart'});
    assert.equal(reopened.command(f.surface.draft.operationId).message.text,'同一讨论的回答');
    assert.equal(Object.keys(reopened.state.commands).length,1);
  }finally{f.surface.dispose();host.close();}
});

function pageReviewCatalog(){return {schema:'eeas.native-review-capabilities.v1',capability_binding_sha256:sha,skills:[{id:'acceptance-review',title:'验收检查',config_ref:{id:'acceptance-review',revision:1,sha256:sha},selectable:true}],tools:[{id:'planning_acceptance_coverage',title:'任务验收覆盖',selection:{kind:'BUILTIN_READONLY',tool_ref:{id:'planning_acceptance_coverage',revision:1,sha256:sha}},selectable:true}],basis_ref:{kind:'REPORT_PLAN',file_link_ref:{id:sha,project_id:'project',revision:1,sha256:sha},proposal_ref:{id:'proposal',revision:3,sha256:sha},source_snapshot_sha256:sha,candidate_sha256:sha},permission_hint:{required_effects:['PROCESS_START'],can_submit:true}};}
test('selection publishes busy before consent can be clicked; stale catalog cannot unlock or overwrite latest selection',async()=>{
 const first=deferred(),second=deferred(),published=[];let reads=0;
 const f=fixture({onState:value=>published.push(value),onReviewCatalog:async()=>++reads===1?pageReviewCatalog():reads===2?first.promise:second.promise});
 try{await f.surface.ready;await f.surface.openAsk();await f.edit('草稿与选择保留');await f.command('review-catalog');published.length=0;
  const selecting=f.send({kind:'review-selection',epoch:f.surface.epoch,skillId:'acceptance-review',toolId:'planning_acceptance_coverage'});
  assert.equal(f.surface.get().reviewBusy,true);assert.equal(f.surface.get().canSend,false);
  assert.equal(published[0].reviewBusy,true);assert.equal(published[0].draft.nativeReview.process_start_consent,false);
  await f.send({kind:'review-consent',epoch:f.surface.epoch,enabled:true});await f.command('send');
  assert.equal(f.surface.draft.nativeReview.process_start_consent,false);assert.equal(f.events.filter(row=>row[0]==='send').length,0);
  const latest=f.send({kind:'review-selection',epoch:f.surface.epoch,skillId:'acceptance-review',toolId:''});
  first.resolve(pageReviewCatalog());assert.equal((await selecting).status,'STALE');assert.equal(f.surface.get().reviewBusy,true);
  second.resolve(pageReviewCatalog());await latest;assert.equal(f.surface.get().reviewBusy,false);assert.equal(f.surface.draft.nativeReview.selection.tool_selection,null);
  assert.equal(f.surface.draft.comment,'草稿与选择保留');
  await f.send({kind:'review-consent',epoch:f.surface.epoch,enabled:true});assert.equal(f.surface.draft.nativeReview.process_start_consent,true);
 }finally{await f.surface.dispose();}
});
test('catalog success or failure after another saved draft is opened has no lock, error or selection effect',async()=>{
 for(const reject of [false,true]){const waiting=deferred();const f=fixture({onReviewCatalog:()=>waiting.promise,onLoadDraft:async input=>input?.draftId?({id:'other',revision:1,comment:'另一草稿',marks:[],restored:false,capture:capture()}):null});
  try{await f.surface.ready;await f.surface.openAsk();const reading=f.command('review-catalog');await f.surface.openAsk({draftId:'other'});const before=f.surface.get();
   if(reject)waiting.reject(new Error('old catalogue failure'));else waiting.resolve(pageReviewCatalog());
   assert.equal((await reading).status,'STALE');const after=f.surface.get();assert.equal(after.reviewBusy,false);assert.equal(after.error,before.error);assert.equal(after.draft.comment,'另一草稿');assert.equal(after.draft.nativeReview,null);
  }finally{await f.surface.dispose();}
 }
});
test('directory response for a cleared document cannot restore its old scope permission hint',async()=>{
 const waiting=deferred(),file={project_id:'project',id:'b'.repeat(64),revision:1,sha256:sha};let reads=0;
 const f=fixture({onCapture:async()=>capture({scope:{projectId:'project',conversationId:'discussion',fileLinkRef:file,fileLabel:'文稿.docx'}}),
 onClearFileContext:async old=>({...old,scope:{projectId:'project',conversationId:'discussion'}}),onReviewCatalog:async()=>++reads===1?pageReviewCatalog():waiting.promise});
 try{await f.surface.ready;await f.surface.openAsk();await f.edit('保留这个问题');await f.command('review-catalog');
  const reading=f.send({kind:'review-selection',epoch:f.surface.epoch,skillId:'acceptance-review',toolId:'planning_acceptance_coverage'});
  await f.command('file-clear');const fresh=f.surface.draft.capture;
  const old=pageReviewCatalog();old.permission_hint.required_effects=[];waiting.resolve(old);
  assert.equal((await reading).status,'STALE');assert.equal(f.surface.get().reviewBusy,false);
  assert.equal(f.surface.get().reviewProcessRequired,true);assert.equal(f.surface.draft.nativeReview.process_start_consent,false);
  assert.equal(f.surface.draft.capture,fresh);assert.equal(fresh.scope.fileLinkRef,undefined);assert.equal(f.surface.draft.comment,'保留这个问题');assert.equal(f.events.filter(row=>row[0]==='send').length,0);
 }finally{await f.surface.dispose();}
});
test('normal reopening of the same draft invalidates an old directory busy lock and ignores its acknowledgement',async()=>{
 const waiting=deferred(),f=fixture({onReviewCatalog:()=>waiting.promise});
 try{await f.surface.ready;await f.surface.openAsk();await f.edit('同一草稿');const draft=f.surface.draft,epoch=f.surface.epoch;
  const reading=f.command('review-catalog');assert.equal(f.surface.get().reviewBusy,true);
  await f.surface.hide({save:false,animate:false});await f.surface.openAsk();assert.equal(f.surface.draft,draft);assert.notEqual(f.surface.epoch,epoch);assert.equal(f.surface.get().reviewBusy,false);
  waiting.resolve(pageReviewCatalog());assert.equal((await reading).status,'STALE');assert.equal(f.surface.get().reviewBusy,false);assert.equal(f.surface.get().draft.comment,'同一草稿');assert.equal(f.surface.get().canSend,true);
 }finally{await f.surface.dispose();}
});
test('full reply survives view projection; external link requires current operation/body and allowed protocol',async()=>{
 const opened=[],f=fixture({onOpenExternal:async url=>opened.push(url)});
 try{await f.surface.ready;await f.surface.openAsk();await f.edit('解释');await f.command('send');await f.surface.openAsk();const operationId=f.surface.draft.operationId;
  const text='完整段落 '.repeat(8000)+'END_OF_LONG_REPLY\n\n[Source](https://example.com/report)';f.surface.publishResult({operation_id:operationId,status:'RETURNED',text});
  assert.equal(f.surface.get().result.text,text);const payload={kind:'reply-link',epoch:f.surface.epoch,operationId,text_sha256:f.surface.get().result.text_sha256,url:'https://example.com/report'};
  await f.send(payload);assert.deepEqual(opened,['https://example.com/report']);
  for(const url of ['javascript:alert(1)','data:text/html,x','file:///D:/x','command:run','https://user:pass@example.com','https://example.com/\nother'])await f.send({...payload,url});
  await f.send({...payload,operationId:'other'});await f.send({...payload,text_sha256:sha});await f.send({...payload,extra:true});
  for(const text of ['No links here','`https://example.com/report`','![image](https://example.com/report)','<a href="https://example.com/report">HTML</a>']){
   f.surface.publishResult({operation_id:operationId,status:'RETURNED',text});
   await f.send({...payload,text_sha256:f.surface.get().result.text_sha256});
  }
  f.surface.publishResult({operation_id:operationId,status:'RETURNED',text:'new body'});await f.send(payload);
  assert.equal(opened.length,1);await assert.rejects(f.send({...payload,epoch:payload.epoch-1}),/Stale/);
  await assert.rejects(f.send(payload,'annotationOverlay'),/Untrusted|Stale/);assert.equal(f.events.filter(row=>row[0]==='send').length,1);
 }finally{await f.surface.dispose();}
});
test('review picker freezes exact builtin/skill into save and single send; running new question retains old operation',async()=>{
 const catalog=pageReviewCatalog(),f=fixture({onReviewCatalog:async()=>catalog});try{await f.surface.ready;await f.surface.openAsk();await f.command('review-catalog');assert.equal(f.events.filter(x=>x[0]==='send').length,0);
  await f.send({kind:'review-selection',epoch:f.surface.epoch,skillId:'acceptance-review',toolId:'planning_acceptance_coverage'});await f.send({kind:'review-consent',epoch:f.surface.epoch,enabled:true});await f.edit('检查此计划的验收缺项');await f.command('send');
  const sent=f.events.find(row=>row[0]==='send')[1],saved=f.events.filter(row=>row[0]==='draft').at(-1)[1];assert.deepEqual(sent.nativeReview,saved.nativeReview);assert.equal(sent.nativeReview.selection.tool_selection.kind,'BUILTIN_READONLY');assert.equal('probe_ref' in sent.nativeReview.selection.tool_selection,false);assert.equal(sent.nativeReview.process_start_consent,true);
  await f.surface.openAsk();await f.command('new-question');assert.equal(f.surface.draft.operationId,null);assert.equal(f.surface.draft.comment,'');assert.equal(f.surface.draft.nativeReview.process_start_consent,false);assert.equal(f.events.filter(row=>row[0]==='send').length,1);assert.equal(f.events.filter(row=>row[0]==='retain').length,1);
 }finally{await f.surface.dispose();}
});
test('unknown review guidance retains its child id and next explicit click inspects instead of redispatching',async()=>{
 const calls=[],f=fixture({onReviewControl:async input=>{calls.push(input);return {operation_id:input.body.operation_id,status:'UNKNOWN_OUTCOME'};}});try{await f.surface.ready;await f.surface.openAsk();await f.edit('检查');await f.command('send');const parent=f.surface.draft.operationId;f.surface.publishResult({operation_id:parent,status:'RUNNING',review:{review_ref:{id:'review',revision:1,sha256:sha},can_guide:true}});
  await f.surface.openAsk();await f.send({kind:'review-guidance-edit',epoch:f.surface.epoch,text:'只检查圈选部分'});await f.command('review-guidance');await f.command('review-guidance');assert.equal(calls.length,2);assert.equal(calls[0].inspect,undefined);assert.equal(calls[1].inspect,true);assert.equal(calls[0].body.operation_id,calls[1].body.operation_id);assert.equal(calls[0].parentOperationId,parent);assert.equal(f.events.filter(row=>row[0]==='send').length,1);
 }finally{await f.surface.dispose();}
});

test('same quick editor continues a canonical task after a reply and queries an unknown child without a second send',async()=>{
 const calls=[];let outcome='UNKNOWN_OUTCOME';
 const f=fixture({onReviewControl:async value=>{calls.push(value);return {operation_id:value.body.operation_id,status:outcome};}});
 try{
  await f.surface.ready;await f.surface.openAsk();await f.edit('原始问题');await f.command('send');
  await f.surface.openAsk();const parent=f.surface.draft.operationId,draft=f.surface.draft,epoch=f.surface.epoch;
  const reference={id:parent,revision:1,sha256:sha};
  f.surface.publishResult({operation_id:parent,status:'RETURNED',text:'完整原回答',canonical_original:true,canonical_message_ref:reference,control_capabilities:{can_guide:true,can_status:true,can_cancel:false}});
  assert.equal(f.surface.get().guidanceEditable,true);
  await f.send({kind:'review-guidance-edit',epoch,text:'保留手改并继续 😀'});await f.command('send');
  assert.equal(f.surface.draft,draft);assert.equal(f.surface.epoch,epoch);assert.equal(draft.guidanceText,'保留手改并继续 😀');
  assert.deepEqual(calls[0].body.expected_canonical_message_ref,reference);assert.equal(calls[0].body.expected_native_message_ref,undefined);
  const key=calls[0].body.operation_id;outcome='ACCEPTED';await f.command('send');
  assert.equal(calls[1].inspect,true);assert.equal(calls[1].body.operation_id,key);assert.equal(draft.guidanceText,'');
  assert.equal(f.events.filter(row=>row[0]==='send').length,1);assert.equal(f.events.filter(row=>row[0]==='capture').length,1);
 }finally{await f.surface.dispose();}
});

test('a later canonical message can be stopped from the same completed original question view',async()=>{
 const stops=[],f=fixture({onCancel:async id=>{stops.push(id);return {operation_id:id,status:'RETURNED',canonical_original:true,control_capabilities:{can_cancel:false}};}});
 try{
  await f.surface.ready;await f.surface.openAsk();await f.edit('原始问题');await f.command('send');await f.surface.openAsk();
  const parent=f.surface.draft.operationId;
  f.surface.publishResult({operation_id:parent,status:'RETURNED',canonical_original:true,canonical_message_ref:{id:parent,revision:1,sha256:sha},canonical_stop_ref:{id:'later_task',revision:1,sha256:sha},control_capabilities:{can_cancel:true,can_guide:true}});
  assert.equal(f.surface.get().canStop,true);await f.command('stop');assert.deepEqual(stops,[parent]);
 }finally{await f.surface.dispose();}
});
test('late review catalog cannot replace another restored draft, and overlay cannot select or consent',async()=>{
 const wait=deferred(),f=fixture({onReviewCatalog:()=>wait.promise,onLoadDraft:async({draftId})=>draftId?{id:draftId,revision:1,comment:'other',marks:[],capture:capture(),restored:false}:undefined});try{await f.surface.ready;await f.surface.openAsk();const reading=f.command('review-catalog');await f.surface.openAsk({draftId:'other'});wait.resolve(pageReviewCatalog());assert.equal((await reading).status,'STALE');assert.equal(f.surface.draft.comment,'other');assert.equal(f.surface.reviewCatalog,undefined);
  await assert.rejects(f.send({kind:'review-consent',epoch:f.surface.epoch,enabled:true},'annotationOverlay'),/Untrusted|Stale/);assert.equal(f.surface.draft.nativeReview,null);
 }finally{await f.surface.dispose();}
});
test('explicit new question reads one fresh permission hint without opening picker or repeating original send',async()=>{
 let reads=0;const catalog=pageReviewCatalog(),f=fixture({onReviewCatalog:async()=>{reads++;return catalog;}});try{await f.surface.ready;await f.surface.openAsk();await f.command('review-catalog');await f.send({kind:'review-selection',epoch:f.surface.epoch,skillId:'acceptance-review',toolId:'planning_acceptance_coverage'});await f.send({kind:'review-consent',epoch:f.surface.epoch,enabled:true});await f.edit('检查');await f.command('send');await f.surface.openAsk();const before=reads;catalog.permission_hint={required_effects:[],can_submit:true,expires_at:new Date(Date.now()+60000).toISOString()};await f.command('new-question');assert.equal(reads,before+1);assert.equal(f.surface.get().reviewOpen,false);assert.equal(f.surface.get().reviewProcessRequired,false);assert.equal(f.events.filter(row=>row[0]==='send').length,1);catalog.permission_hint.expires_at='2000-01-01T00:00:00Z';assert.equal(f.surface.get().reviewProcessRequired,true);
 }finally{await f.surface.dispose();}
});
test('known undispatched guidance keeps text; lost child acknowledgement remains inspectable after parent pending item disappears',async()=>{
 const calls=[];let status='FAILED_NOT_DISPATCHED';const f=fixture({onReviewControl:async input=>{calls.push(input);return {operation_id:input.body.operation_id,status};}});try{await f.surface.ready;await f.surface.openAsk();await f.edit('检查');await f.command('send');const parent=f.surface.draft.operationId;await f.surface.openAsk();f.surface.publishResult({operation_id:parent,status:'RUNNING',review:{review_ref:{id:'review',revision:1,sha256:sha},can_guide:true}});await f.send({kind:'review-guidance-edit',epoch:f.surface.epoch,text:'保留这条指导'});await f.command('review-guidance');assert.equal(f.surface.draft.guidanceText,'保留这条指导');assert.equal(f.surface.draft.reviewControl,null);
  status='UNKNOWN_OUTCOME';f.surface.publishResult({operation_id:parent,status:'RUNNING',review:{review_ref:{id:'review',revision:1,sha256:sha},can_guide:true}});await f.command('review-guidance');const unknown=f.surface.draft.reviewControl;assert.ok(unknown);f.surface.publishResult({operation_id:parent,status:'RETURNED',review:{review_ref:{id:'review',revision:1,sha256:sha},can_guide:false,pending_permissions:[],pending_questions:[]}});status='RETURNED';await f.command('inspect');assert.equal(calls.at(-1).inspect,true);assert.equal(calls.at(-1).body.operation_id,unknown.body.operation_id);assert.equal(calls.filter(v=>!v.inspect).length,2);assert.equal(f.surface.draft.reviewControl,null);
 }finally{await f.surface.dispose();}
});

test('constructs both durable hidden own windows before any capture, with sandbox and no external navigation',async()=>{
  const f=fixture();try{
    assert.equal(Object.keys(f.surface.windows).length,2);
    for(const window of Object.values(f.surface.windows)){
      assert.equal(window.visible,false);assert.equal(window.options.show,false);assert.equal(window.options.skipTaskbar,true);
      assert.equal(window.options.roundedCorners,false);
      assert.deepEqual(window.options.webPreferences,{preload:path.resolve(__dirname,'../quick-surface-preload.bundle.cjs'),nodeIntegration:false,contextIsolation:true,sandbox:true,webviewTag:false});
      assert.deepEqual(window.openHandler({url:'https://untrusted.invalid'}),{action:'deny'});
      let prevented=0;window.webContents.emit('will-navigate',{preventDefault(){prevented++;}},'file:///D:/not-authorized');
      assert.equal(prevented,1);assert.deepEqual(window.calls[0],['protection',false]);
    }
    await f.surface.ready;assert.deepEqual(f.events,[]);
  }finally{f.surface.dispose();}
});

test('ask freezes actual source then shows only its inactive quick window, never expands Main or dispatches',async()=>{
  const f=fixture();try{
    await f.surface.openAsk();const state=f.surface.get();
    assert.equal(state.visible,true);assert.equal(state.source.available,true);assert.equal(state.source.label,'正在阅读的实际页面');
    assert.equal(state.source.image,image);assert.equal(state.annotationAvailable,true);
    assert.deepEqual(f.events.map(row=>row[0]),['capture']);
    assert.equal(f.surface.windows.quickPopover.calls.filter(row=>row[0]==='show').length,0);
    assert.equal(JSON.stringify(state).includes('must-not-enter-renderer'),false);
    assert.equal(JSON.stringify(f.surface.windows.quickPopover.calls).includes('must-not-enter-renderer'),false);
  }finally{f.surface.dispose();}
});

test('annotation is local until confirmation, undo data and exact frozen basis reach only explicit delivery',async()=>{
  const f=fixture();try{
    await f.surface.beginAnnotation();assert.equal(f.surface.get().overlayVisible,true);assert.equal(f.surface.get().visible,false);
    const marks=[{type:'rect',x:.2,y:.2,width:.3,height:.4},{type:'freehand',points:[{x:.2,y:.3},{x:.6,y:.7}]}];
    await f.send({kind:'edit',epoch:f.surface.get().epoch,comment:'这个流程中间缺少异常处理',marks},'annotationOverlay');
    assert.equal(f.events.some(row=>row[0]==='send'||row[0]==='expand'),false);
    await f.command('review','annotationOverlay');assert.equal(f.surface.get().overlayVisible,false);assert.equal(f.surface.get().visible,true);
    assert.equal(f.surface.windows.quickPopover.calls.filter(row=>row[0]==='show').length,1);
    await f.command('draft');
    const saved=f.events.find(row=>row[0]==='draft')[1];
    assert.equal(saved.delivery,'DRAFT');assert.equal(saved.capture.scope.privateToken,'must-not-enter-renderer');
    assert.deepEqual(saved.marks,marks);assert.equal(saved.regions.length,2);
    assert.equal(f.surface.get().visible,false);assert.equal(f.events.some(row=>row[0]==='send'||row[0]==='expand'),false);
  }finally{f.surface.dispose();}
});

test('draft confirmation waits for actual durable receipt; persistence failure retains input and HWND',async()=>{
  const save=deferred();let writes=0;
  const f=fixture({onDraft:async value=>{writes++;return save.promise;}});
  try{
    await f.surface.openAsk();await f.edit('需要保留的人工意见');
    const running=f.command('draft');await tick();assert.equal(writes,1);assert.equal(f.surface.get().visible,true);
    save.reject(new Error('private disk error D:/sensitive'));await running;
    assert.equal(f.surface.get().visible,true);assert.equal(f.surface.get().draft.comment,'需要保留的人工意见');
    assert.equal(f.surface.get().draft.phase,'EDITING');assert.match(f.surface.get().error,/保存未完成/);
    assert.doesNotMatch(f.surface.get().error,/sensitive/);
    for(const window of Object.values(f.surface.windows))assert.equal(window.destroyed,false);
  }finally{f.surface.dispose();}
});

test('autosave and explicit confirmation serialize expected revisions instead of racing stale writes',async()=>{
  const first=deferred(),calls=[];
  const f=fixture({onDraft:async value=>{calls.push(value);if(calls.length===1)await first.promise;return {id:value.draftId,revision:value.expectedRevision+1,sha256:sha,status:value.delivery};}});
  try{
    await f.surface.openAsk();await f.edit('第一段意见');
    const save=f.surface._persist('EDITING');await tick();
    await f.edit('第二段修改完整意见');const commit=f.command('draft');await tick();
    assert.equal(calls.length,1);assert.equal(calls[0].expectedRevision,0);
    first.resolve();await save;await commit;
    assert.equal(calls.length,2);assert.equal(calls[1].expectedRevision,1);assert.equal(calls[1].comment,'第二段修改完整意见');assert.equal(calls[1].delivery,'DRAFT');
  }finally{f.surface.dispose();}
});

test('send hides only the local surface, uses one operation, background result never focuses or expands',async()=>{
  const response=deferred(),requests=[];
  const f=fixture({onSend:async value=>{requests.push(value);return response.promise;}});
  try{
    await f.surface.openAsk();await f.edit('请说明这里的调用顺序');const sending=f.command('send');await tick();
    assert.equal(requests.length,1);assert.equal(f.surface.get().visible,false);
    const counts=Object.values(f.surface.windows).map(window=>window.calls.filter(row=>['show','inactive'].includes(row[0])).length);
    response.resolve({operation_id:requests[0].operationId,status:'COMPLETED',text:'原操作回复'});await sending;
    assert.equal(f.surface.get().unread,true);assert.equal(f.surface.get().draft.phase,'COMPLETED');
    assert.deepEqual(Object.values(f.surface.windows).map(window=>window.calls.filter(row=>['show','inactive'].includes(row[0])).length),counts);
    assert.equal(f.events.some(row=>row[0]==='expand'),false);
    await f.surface.openAsk();assert.equal(f.surface.get().visible,true);assert.equal(requests.length,1);
    assert.equal((await f.command('send')).status,'BUSY');assert.equal(requests.length,1);
  }finally{f.surface.dispose();}
});

test('unretained unknown send stays inspectable and late unrelated replies cannot replace it',async()=>{
  let sends=0,operation;
  const f=fixture({onRetainSubmitted:async()=>{throw new Error('No durable record');},onSend:async value=>{sends++;operation=value.operationId;throw new Error('private network failure');}});
  try{
    await f.surface.openAsk();await f.edit('当前问题');await f.command('send');
    assert.equal(f.surface.get().draft.phase,'UNKNOWN');assert.equal(sends,1);
    assert.deepEqual(f.surface.publishResult({operation_id:'another-operation',status:'COMPLETED',text:'错误来源'}),{status:'STALE'});
    await f.surface.openAsk();assert.equal((await f.command('new-question')).status,'ACTION_FAILED');
    assert.equal((await f.command('send')).status,'BUSY');assert.equal(sends,1);
    await f.command('inspect');assert.equal(f.events.find(row=>row[0]==='inspect')[1],operation);
    assert.equal(f.surface.get().result.text,'来自原操作的真实回复');assert.equal(sends,1);
  }finally{f.surface.dispose();}
});

test('cancel while capture is pending fences the late frame and never calls model, Main or destroy',async()=>{
  const pending=deferred();const f=fixture({onCapture:()=>pending.promise});
  try{
    const open=f.surface.beginAnnotation();await tick();assert.equal(f.surface.get().capturePending,true);
    await f.surface.hide();pending.resolve(capture());assert.deepEqual(await open,{status:'STALE'});
    assert.equal(f.surface.get().visible,false);assert.equal(f.surface.get().overlayVisible,false);
    assert.equal(f.surface.get().draft.capture,undefined);
    assert.deepEqual(f.events,[]);assert.equal(f.surface.windows.annotationOverlay.destroyed,false);
  }finally{f.surface.dispose();}
});

test('same-kind second invocation cancels capture without adding an overlay to the uncollected page',async()=>{
  const pending=deferred(),f=fixture({onCapture:()=>pending.promise});
  try{
    const opening=f.surface.openAsk();await tick();assert.equal(f.surface.get().capturePending,true);
    assert.deepEqual(await f.surface.openAsk(),{status:'CANCELLED'});
    pending.resolve(capture());assert.deepEqual(await opening,{status:'STALE'});
    assert.equal(f.surface.get().visible,false);assert.equal(f.surface.get().overlayVisible,false);
  }finally{f.surface.dispose();}
});

test('overlay stays hidden until its exact own current renderer decodes the frozen image; decode failure stays local',async()=>{
  const f=fixture({autoDecode:false});try{
    await f.surface.beginAnnotation();assert.equal(f.surface.get().overlayPreparing,true);assert.equal(f.surface.get().overlayVisible,false);
    assert.equal(f.surface.windows.annotationOverlay.visible,false);
    await f.send({kind:'preview-failed',epoch:f.surface.get().epoch},'annotationOverlay');
    assert.equal(f.surface.get().visible,true);assert.equal(f.surface.get().overlayVisible,false);assert.match(f.surface.get().error,/画面未能加载/);
    await f.command('annotate');await f.send({kind:'preview-ready',epoch:f.surface.get().epoch},'annotationOverlay');
    assert.equal(f.surface.get().overlayVisible,true);assert.equal(f.surface.windows.annotationOverlay.calls.some(row=>row[0]==='show'),false);
    assert.equal(f.events.some(row=>row[0]==='send'||row[0]==='expand'),false);
  }finally{f.surface.dispose();}
});

test('disposal during capture or send suppresses late presentation and owner recreation',async()=>{
  const pending=deferred();const f=fixture({onCapture:()=>pending.promise});
  const opening=f.surface.openAsk();await tick();f.surface.dispose();pending.resolve(capture());await opening;
  assert.equal(f.ipc.handlers.has(CHANNEL),false);
  for(const window of Object.values(f.surface.windows)){assert.equal(window.calls.filter(row=>row[0]==='destroy').length,1);assert.equal(window.calls.filter(row=>row[0]==='show'||row[0]==='inactive').length,0);}
});

test('restored drafts are visible but never sent using old authority; explicit recapture preserves text',async()=>{
  const original=capture();const f=fixture({onLoadDraft:async()=>({id:'saved_draft',revision:2,comment:'昨天的意见',marks:[{type:'rect',x:.1,y:.1,width:.2,height:.2}],capture:original})});
  try{
    await f.surface.openAsk();assert.equal(f.events.length,0);assert.equal(f.surface.get().draft.restored,true);assert.equal(f.surface.get().source.available,false);
    assert.equal((await f.command('send')).status,'ACTION_FAILED');assert.equal(f.events.length,0);
    assert.equal((await f.command('recapture')).status,'CONFIRM_RECAPTURE');assert.equal(f.surface.get().draft.marks.length,1);
    await f.command('recapture-confirm');assert.equal(f.surface.get().draft.comment,'昨天的意见');assert.deepEqual(f.surface.get().draft.marks,[]);
    assert.equal(f.surface.get().source.available,true);assert.equal(f.events.filter(row=>row[0]==='capture').length,1);
    assert.equal(f.events.some(row=>row[0]==='send'||row[0]==='expand'),false);
  }finally{f.surface.dispose();}
});

test('only explicit expand controls invoke Main mode callback; background progress never does',async()=>{
  const f=fixture();try{
    await f.surface.openAsk();await f.command('expand');assert.deepEqual(f.events.filter(row=>row[0]==='expand'),[['expand','companion']]);
    await f.surface.openAsk();await f.command('workbench');assert.deepEqual(f.events.filter(row=>row[0]==='expand'),[['expand','companion'],['expand','workbench']]);
  }finally{f.surface.dispose();}
});

test('artifact opening resolves an index only from the exact original trusted operation receipt',async()=>{
  const artifact={filename:'任务书.docx',file_source_ref:{privateBoundSource:'actual-source'}};
  const f=fixture({onSend:async body=>({operation_id:body.operationId,status:'COMPLETED',text:'已生成',artifacts:[artifact]})});
  try{
    await f.surface.openAsk();await f.edit('整理任务书');await f.command('send');await f.surface.openAsk();
    assert.equal(JSON.stringify(f.surface.get()).includes('privateBoundSource'),false);
    await f.send({kind:'artifact',epoch:f.surface.get().epoch,index:0});assert.equal(f.events.find(row=>row[0]==='open')[1],artifact);
    const count=f.events.length;await f.send({kind:'artifact',epoch:f.surface.get().epoch,index:-1});assert.equal(f.events.length,count);
    assert.equal(f.events.some(row=>row[0]==='expand'),false);
  }finally{f.surface.dispose();}
});

test('top-frame and own document identity are mandatory; stale epochs, hidden actions and arbitrary payloads are rejected',async()=>{
  const f=fixture();try{
    await f.surface.openAsk();const window=f.surface.windows.quickPopover,epoch=f.surface.get().epoch;
    for(const event of [{sender:{},senderFrame:window.webContents.mainFrame},{sender:window.webContents,senderFrame:{url:f.surface.urls.quickPopover}}]){
      await assert.rejects(f.send({kind:'state'},'quickPopover',event),/Untrusted/);
    }
    await assert.rejects(f.send({kind:'command',epoch:epoch-1,command:'expand'}),/Stale/);
    for(const payload of [{kind:'command',epoch,command:'shell'},{kind:'command',epoch,command:'expand',url:'file:///x'},
      {kind:'edit',epoch,comment:'x',marks:[{type:'rect',x:0,y:0,width:99,height:1}]}])assert.equal((await f.send(payload)).status,'ACTION_FAILED');
    assert.equal(f.events.some(row=>row[0]==='expand'||row[0]==='send'),false);
    await f.surface.hide();await assert.rejects(f.command('expand'),/hidden/);
  }finally{f.surface.dispose();}
});

test('single-display geometry uses the supplied physical-to-DIP API and rejects mixed-display spanning or unverifiable pixels',()=>{
  const own={},screen={getAllDisplays:()=>[{id:9,bounds:{x:-1600,y:-200,width:1600,height:1000}}],screenToDipRect:(window,physical)=>{assert.equal(window,null);assert.deepEqual(physical,{x:-1800,y:0,width:1200,height:600});return {x:-1200,y:0,width:800,height:400};}};
  const value=capture({geometry:{verified:true,physicalBounds:{x:-1800,y:0,width:1200,height:600},pixelWidth:1200,pixelHeight:600}});
  assert.deepEqual(annotationBounds(value,screen,own),{bounds:{x:-1200,y:0,width:800,height:400},displayId:'9'});
  assert.equal(annotationBounds({...value,geometry:{...value.geometry,verified:false}},screen,own),null);
  assert.equal(annotationBounds({...value,geometry:{...value.geometry,pixelWidth:1199}},screen,own),null);
  screen.screenToDipRect=()=>({x:-100,y:0,width:800,height:400});assert.equal(annotationBounds(value,screen,own),null);
});

test('fractional DIP endpoints round inward instead of expanding a 2880-pixel source at 2.24 scale',()=>{
  const value=capture({geometry:{verified:true,physicalBounds:{x:0,y:0,width:2880,height:1840},pixelWidth:2880,pixelHeight:1840}});
  const converted={x:0,y:0,width:2880/2.24,height:1840/2.24};
  const screen={screenToDipRect:(window,physical)=>{assert.equal(window,null);assert.deepEqual(physical,value.geometry.physicalBounds);return converted;},
    getAllDisplays:()=>[{id:1,bounds:{x:0,y:0,width:2880/2.24,height:1920/2.24}}]};
  const {bounds}=annotationBounds(value,screen);
  assert.deepEqual(bounds,{x:0,y:0,width:1285,height:821});
  assert.ok((bounds.x+bounds.width)*2.24<=2880);assert.ok((bounds.y+bounds.height)*2.24<=1840);
  assert.ok(Math.round(converted.width)*2.24>2880,'the old independent width rounding exceeded the source');
});

test('negative fractional origins keep both endpoints inside the converted source',()=>{
  const converted={x:-799.75,y:-399.25,width:799.5,height:398.9};
  const screen={screenToDipRect:()=>converted,getAllDisplays:()=>[{id:'left',bounds:{x:-800,y:-400,width:800,height:400}}]};
  const result=annotationBounds(capture(),screen);
  assert.deepEqual(result,{bounds:{x:-799,y:-399,width:798,height:398},displayId:'left'});
  assert.ok(result.bounds.x>=converted.x&&result.bounds.y>=converted.y);
  assert.ok(result.bounds.x+result.bounds.width<=converted.x+converted.width);
  assert.ok(result.bounds.y+result.bounds.height<=converted.y+converted.height);
});

test('inward rounding cannot disguise fractional cross-display sources or create a zero-sized canvas',()=>{
  const screen={screenToDipRect:()=>({x:-.25,y:0,width:100.1,height:80}),getAllDisplays:()=>[
    {id:'left',bounds:{x:-100,y:0,width:100,height:100}},{id:'right',bounds:{x:0,y:0,width:200,height:100}}]};
  assert.equal(annotationBounds(capture(),screen),null);
  for(const converted of [{x:.2,y:0,width:.4,height:80},{x:0,y:.3,width:80,height:.5},{x:0,y:0,width:0,height:80}]){
    screen.screenToDipRect=()=>converted;assert.equal(annotationBounds(capture(),screen),null);
  }
});

test('smaller owned canvas retains the complete source image and full-image normalized annotation endpoints',async()=>{
  const f=fixture();try{
    f.screen.screenToDipRect=()=>({x:10.2,y:20.8,width:899.1,height:600.1});
    await f.surface.beginAnnotation();
    assert.deepEqual(f.surface.windows.annotationOverlay.getBounds(),{x:11,y:21,width:898,height:599});
    assert.equal(f.surface.get().source.image,image);
    const marks=[{type:'rect',x:0,y:0,width:1,height:1},{type:'arrow',points:[{x:0,y:0},{x:1,y:1}]}];
    await f.send({kind:'edit',epoch:f.surface.get().epoch,comment:'整张图都要保留',marks},'annotationOverlay');
    await f.command('review','annotationOverlay');await f.command('draft');
    const saved=f.events.filter(row=>row[0]==='draft').at(-1)[1];
    assert.equal(saved.capture.snapshot.preview.data_url,image);assert.deepEqual(saved.marks,marks);
    assert.equal(f.events.some(row=>row[0]==='expand'||row[0]==='send'),false);
  }finally{f.surface.dispose();}
});

test('missing precise geometry stays in quick question with explicit error and no fake overlay',async()=>{
  const f=fixture({onCapture:async()=>capture({geometry:undefined})});try{
    await f.surface.beginAnnotation();assert.equal(f.surface.get().visible,true);assert.equal(f.surface.get().overlayVisible,false);
    assert.equal(f.surface.get().annotationAvailable,false);assert.match(f.surface.get().error,/不能精确原位圈选/);
    assert.equal(f.events.some(row=>row[0]==='send'||row[0]==='expand'),false);
  }finally{f.surface.dispose();}
});

test('display change cancels only annotation presentation and preserves marks/text without touching desktop area',async()=>{
  const f=fixture();try{
    await f.surface.beginAnnotation();await f.send({kind:'edit',epoch:f.surface.get().epoch,comment:'保留意见',marks:[{type:'ellipse',x:.1,y:.2,width:.3,height:.4}]},'annotationOverlay');
    f.screen.emit('display-metrics-changed');assert.equal(f.surface.get().overlayVisible,false);assert.equal(f.surface.get().visible,true);
    assert.equal(f.surface.get().draft.comment,'保留意见');assert.equal(f.surface.get().draft.marks.length,1);
    assert.match(f.surface.get().error,/显示器已变化/);assert.equal(f.events.some(row=>row[0]==='expand'),false);
    assert.deepEqual(popupBounds(f.screen,{x:-5000,y:99999}),{x:0,y:724,width:380,height:176});
  }finally{f.surface.dispose();}
});

test('marks are bounded normalized geometry; invalid structures cannot smuggle commands or exceed point budget',()=>{
  assert.deepEqual(markRegions(normalizedMarks([{type:'arrow',points:[{x:.1,y:.2},{x:.4,y:.8}]}])),[{x:.1,y:.2,width:.30000000000000004,height:.6000000000000001}]);
  for(const value of [null,[{type:'rect',x:0,y:0,width:1,height:1,url:'https://x'}],[{type:'freehand',points:[{x:0,y:0},{x:2,y:1}]}],
    Array.from({length:33},()=>({type:'rect',x:0,y:0,width:1,height:1})),Array.from({length:5},()=>({type:'freehand',points:Array.from({length:256},()=>({x:0,y:0}))}))])assert.throws(()=>normalizedMarks(value));
});

test('preload composition handling prevents IME Enter from dispatching and flushes edits before Ctrl+Enter',async()=>{
  const listeners={},windowListeners={},requests=[],elements=new Map();
  function element(id){return {id,value:'',dataset:{},listeners:{},style:{},children:[],parentElement:{clientWidth:380},
    addEventListener(name,fn){this.listeners[name]=fn;},setAttribute(){},removeAttribute(){},replaceChildren(){},append(){},getBoundingClientRect(){return {left:0,top:0,width:100,height:100};}};}
  for(const id of ['#comment','#source-image','#marks'])elements.set(id,element(id));
  const document={body:{dataset:{role:'popover'}},querySelector:key=>elements.get(key)||null,querySelectorAll:()=>[],createElementNS:()=>element('svg'),createElement:()=>element('button')};
  const context=vm.createContext({require:name=>{assert.equal(name,'electron');return {ipcRenderer:{invoke:async(_channel,payload)=>{requests.push(payload);return {};},on:(name,fn)=>{listeners[name]=fn;}}};},
    document,window:{addEventListener:(name,fn)=>{windowListeners[name]=fn;},requestAnimationFrame:()=>1},setTimeout,clearTimeout,console});
  const code=controllerPreloadSource();
  vm.runInContext(code+'\nstate={epoch:1,draft:{id:"draft",phase:"EDITING",comment:""}};',context);
  windowListeners.DOMContentLoaded();await tick();requests.length=0;
  const input=elements.get('#comment');input.value='中文正在输入';input.listeners.compositionstart();
  const key={key:'Enter',ctrlKey:true,preventDefault(){}};windowListeners.keydown(key);await tick();assert.deepEqual(requests,[]);
  input.listeners.compositionend();windowListeners.keydown(key);await tick();await tick();
  assert.equal(requests[0].kind,'edit');assert.equal(requests[0].comment,'中文正在输入');assert.equal(requests[1].command,'send');
});

test('local HTML and preload do not expose generic execution, external resources or auto-upload',()=>{
  for(const role of ['popover','overlay']){
    const html=quickHtml(role);assert.match(html,/connect-src 'none'/);assert.match(html,/script-src 'none'/);assert.match(html,/img-src data:/);
    assert.doesNotMatch(html,/https?:\/\/|onclick=|onload=/);assert.match(html,/prefers-reduced-motion/);
  }
  const preload=fs.readFileSync(path.resolve(__dirname,'../quick-surface-preload.cjs'),'utf8');
  assert.doesNotMatch(preload,/contextBridge|readFile|writeFile|openExternal|exec\(|fetch\(|shell\.|clipboard/);
});

test('hide awaits persistence and failed close retains the original input in an inactive visible window',async()=>{
  const wait=deferred();let writes=0;
  const f=fixture({onDraft:async()=>{writes++;return wait.promise;}});
  try{
    await f.surface.openAsk();await f.edit('还没保存成功的文字');const closing=f.command('dismiss');await tick();
    assert.equal(writes,1);assert.equal(f.surface.get().visible,true);assert.equal(f.surface.get().draft.phase,'SAVING');
    wait.reject(new Error('D volume unavailable'));assert.equal((await closing).status,'ACTION_FAILED');
    assert.equal(f.surface.get().visible,true);assert.equal(f.surface.get().draft.comment,'还没保存成功的文字');
    assert.equal(f.surface.get().draft.phase,'EDITING');assert.equal(f.surface.windows.quickPopover.calls.some(row=>row[0]==='show'),false);
    assert.match(f.surface.get().error,/保存未完成/);
  }finally{f.surface.dispose();}
});

test('DRAFT confirmation finalizes its record and the next ask starts a fresh id and capture',async()=>{
  const f=fixture();try{
    await f.surface.openAsk();const id=f.surface.get().draft.id;await f.edit('已确认给聊天的反馈');
    const old=f.surface.draft;await f.command('draft');assert.equal(f.surface.get().draft,null);
    assert.equal(f.events.filter(row=>row[0]==='draft').length,1);assert.equal(f.events.find(row=>row[0]==='draft')[1].delivery,'DRAFT');
    await f.surface._persist('EDITING',old);assert.equal(f.events.filter(row=>row[0]==='draft').length,1);
    await f.surface.openAsk();assert.notEqual(f.surface.get().draft.id,id);assert.equal(f.surface.get().draft.comment,'');
    assert.equal(f.events.filter(row=>row[0]==='capture').length,2);
    assert.equal(f.events.some(row=>row[0]==='expand'||row[0]==='send'),false);
  }finally{f.surface.dispose();}
});

test('explicit saved draft selection first persists current input then loads exactly that record',async()=>{
  const calls=[];
  const f=fixture({onLoadDraft:async value=>{calls.push(['load',value]);return value.draftId?{id:'selected_clone',revision:1,comment:'指定稿件',marks:[],capture:{...capture(),restored:false}}:null;},
    onDraft:async value=>{calls.push(['save',value]);return {id:value.draftId,revision:value.expectedRevision+1,status:value.delivery,sha256:sha};}});
  try{
    await f.surface.openAsk();await f.edit('当前不能丢的意见');calls.length=0;
    await f.surface.openAsk({draftId:'selected_draft'});
    assert.deepEqual(calls.map(row=>row[0]),['save','load']);assert.equal(calls[0][1].comment,'当前不能丢的意见');
    assert.deepEqual(calls[1][1],{draftId:'selected_draft'});assert.equal(f.surface.get().draft.id,'selected_clone');
    assert.equal(f.surface.get().draft.restored,false);assert.equal(f.surface.get().canConfirm,true);
  }finally{f.surface.dispose();}
});

test('failed explicit draft switch preserves current private draft and never falls through to overwrite another id',async()=>{
  const f=fixture({onLoadDraft:async value=>{if(value.draftId)throw new Error('Missing archived draft');return null;}});
  try{
    await f.surface.openAsk();await f.edit('仍保留当前意见');const id=f.surface.get().draft.id;
    await f.surface.openAsk({draftId:'missing_draft'});assert.equal(f.surface.get().draft.id,id);
    assert.equal(f.surface.get().draft.comment,'仍保留当前意见');assert.equal(f.surface.get().visible,true);
    assert.equal(f.events.some(row=>row[0]==='send'||row[0]==='expand'),false);
  }finally{f.surface.dispose();}
});

test('ordinary questions work without an observable page and send no borrowed capture or context',async()=>{
  const requests=[],f=fixture({onCapture:async()=>{throw new Error('No external window');},
    onSend:async value=>{requests.push(value);return {operation_id:value.operationId,status:'RETURNED',text:'通用问题的实际回答'};}});
  try{
    await f.surface.openAsk();await f.edit('怎样把一个工程问题说明清楚？');
    assert.equal(f.surface.get().source.available,false);assert.equal(f.surface.get().source.label,'未引用页面');assert.equal(f.surface.get().canConfirm,true);
    await f.command('send');assert.equal(requests.length,1);assert.equal(requests[0].capture,null);
    assert.equal(f.surface.get().draft.phase,'COMPLETED');assert.equal(f.surface.get().result.statusLabel,'回复已返回');
  }finally{f.surface.dispose();}
});

test('unavailable capture can save pure text DRAFT but cannot submit annotation marks without their frame',async()=>{
  const f=fixture({onCapture:async()=>capture({snapshot:{operation_id:'missing',current:false,observation:{status:'UNAVAILABLE'}}})});
  try{
    await f.surface.openAsk();await f.edit('单纯文字问题');assert.equal(f.surface.get().canConfirm,true);await f.command('draft');
    assert.equal(f.events.find(row=>row[0]==='draft')[1].delivery,'DRAFT');
    await f.surface.beginAnnotation();await f.edit('这块区域问题', [{type:'rect',x:.1,y:.1,width:.2,height:.2}]);
    assert.equal(f.surface.get().canConfirm,false);assert.equal((await f.command('send')).status,'ACTION_FAILED');
    assert.equal(f.events.some(row=>row[0]==='send'),false);
  }finally{f.surface.dispose();}
});

test('RETURNED completes; NOT_CONFIGURED and INTERRUPTED terminate honestly with human-readable status',async()=>{
  const f=fixture();try{
    await f.surface.openAsk();await f.edit('原问题');await f.command('send');const operation=f.surface.get().draft.operationId;
    for(const [status,phase,label] of [['RETURNED','COMPLETED','回复已返回'],['NOT_CONFIGURED','FAILED','请先配置模型连接'],['INTERRUPTED','FAILED','处理已中断'],['A_FUTURE_ENUM','UNKNOWN','结果待核实']]){
      f.surface.publishResult({operation_id:operation,status});assert.equal(f.surface.get().draft.phase,phase);assert.equal(f.surface.get().result.statusLabel,label);
    }
  }finally{f.surface.dispose();}
});

test('malformed or mismatched send receipt becomes UNKNOWN instead of pretending submission is still running',async()=>{
  let sends=0;const f=fixture({onSend:async()=>{sends++;return {operation_id:'someone_else',status:'COMPLETED',text:'foreign reply'};}});
  try{
    await f.surface.openAsk();await f.edit('需要真实回执的问题');await f.command('send');
    assert.equal(f.surface.get().draft.phase,'UNKNOWN');assert.equal(f.surface.get().result,null);assert.equal(sends,1);
    assert.match(f.surface.get().error,/不会重复发送/);assert.equal(f.surface.get().visible,false);
  }finally{f.surface.dispose();}
});

test('replacing a marked source requires explicit second confirmation; cancellation preserves basis and marks',async()=>{
  const f=fixture();try{
    await f.surface.openAsk();await f.edit('需要保留意见',[{type:'ellipse',x:.2,y:.2,width:.2,height:.3}]);
    const before=f.surface.draft.capture;assert.equal((await f.command('recapture')).status,'CONFIRM_RECAPTURE');
    assert.equal(f.surface.get().draft.retakePending,true);assert.equal(f.events.filter(row=>row[0]==='capture').length,1);
    await f.command('recapture-cancel');assert.equal(f.surface.draft.capture,before);assert.equal(f.surface.get().draft.marks.length,1);
    await f.command('recapture');await f.command('recapture-confirm');
    assert.equal(f.surface.get().draft.comment,'需要保留意见');assert.equal(f.surface.get().draft.marks.length,0);
    assert.equal(f.events.filter(row=>row[0]==='capture').length,2);assert.equal(f.events.find(row=>row[0]==='draft')[1].marks.length,1);
  }finally{f.surface.dispose();}
});

test('invalid mark input is atomic and cannot partially replace the current comment',async()=>{
  const f=fixture();try{
    await f.surface.openAsk();await f.edit('先前完整意见');
    await f.send({kind:'edit',epoch:f.surface.get().epoch,comment:'无效payload的替换',marks:[{type:'freehand',points:[]} ]});
    assert.equal(f.surface.get().draft.comment,'先前完整意见');assert.deepEqual(f.surface.get().draft.marks,[]);
  }finally{f.surface.dispose();}
});

test('actual preload pointer workflow draws bounded marks, supports undo/redo and cancels without dispatching model commands',async()=>{
  const callbacks={},requests=[],elements=new Map();
  function element(){return {dataset:{},listeners:{},style:{},src:'',children:[],parentElement:{clientWidth:380},
    addEventListener(name,fn){this.listeners[name]=fn;},setAttribute(){},removeAttribute(){},replaceChildren(){},append(){},
    setPointerCapture(){},hasPointerCapture(){return false;},releasePointerCapture(){},getBoundingClientRect(){return {left:0,top:0,width:100,height:100};}};}
  for(const id of ['#canvas','#source-image','#marks','.overlay-status'])elements.set(id,element());
  const context=vm.createContext({require:()=>({ipcRenderer:{invoke:async(_channel,payload)=>{requests.push(payload);return {};},on(){}}}),
    document:{body:{dataset:{role:'overlay'}},querySelector:key=>elements.get(key)||null,querySelectorAll:()=>[],createElementNS:element},
    window:{addEventListener:(name,fn)=>{callbacks[name]=fn;},requestAnimationFrame:()=>1},console,setTimeout,clearTimeout});
  const code=controllerPreloadSource();
  vm.runInContext(code+'\nstate={epoch:1,draft:{id:"d",phase:"EDITING",comment:"用户原意见"}};globalThis.check={command,marks:()=>localMarks};',context);
  callbacks.DOMContentLoaded();await tick();requests.length=0;const canvas=elements.get('#canvas');
  canvas.listeners.pointerdown({button:0,pointerId:1,clientX:10,clientY:20,preventDefault(){}});
  canvas.listeners.pointermove({pointerId:1,clientX:40,clientY:60});canvas.listeners.pointerup({pointerId:1,clientX:40,clientY:60});await tick();
  assert.equal(requests[0].kind,'edit');assert.equal(requests[0].marks.length,1);assert.equal(requests[0].marks[0].type,'rect');
  assert.equal(requests[0].comment,'用户原意见');assert.equal(requests[0].marks[0].x,.1);
  await context.check.command('undo');await tick();assert.equal(requests.at(-1).marks.length,0);
  await context.check.command('redo');await tick();assert.equal(requests.at(-1).marks.length,1);
  canvas.listeners.pointerdown({button:0,pointerId:2,clientX:0,clientY:0,preventDefault(){}});
  canvas.listeners.pointercancel({pointerId:2,clientX:90,clientY:90});assert.equal(context.check.marks().length,1);
  callbacks.keydown({key:'Escape',preventDefault(){}});await tick();
  assert.equal(requests.at(-1).command,'dismiss');assert.equal(requests.some(row=>row.command==='send'||row.command==='expand'),false);
});

test('native close save failure is handled and leaves content visible without destroying authority-owned HWND',async()=>{
  const f=fixture({onDraft:async()=>{throw new Error('Actual write failure');}});
  try{
    await f.surface.openAsk();await f.edit('必须保住的原意见');let prevented=false;
    f.surface.windows.quickPopover.emit('close',{preventDefault(){prevented=true;}});await tick();await tick();
    assert.equal(prevented,true);assert.equal(f.surface.get().visible,true);assert.equal(f.surface.get().draft.comment,'必须保住的原意见');
    assert.equal(f.surface.windows.quickPopover.destroyed,false);assert.match(f.surface.get().error,/保存未完成/);
  }finally{f.surface.dispose();}
});

test('renderer failure resets presentation flags and suppresses late capture without recreating owned windows',async()=>{
  const pending=deferred(),f=fixture({onCapture:()=>pending.promise});
  try{
    const opening=f.surface.openAsk();await tick();const old=f.surface.windows.quickPopover;
    old.webContents.emit('render-process-gone');assert.equal(f.surface.get().capturePending,false);assert.equal(f.surface.get().loaded,false);
    pending.resolve(capture());assert.deepEqual(await opening,{status:'STALE'});
    assert.deepEqual(await f.surface.openAsk(),{status:'UNAVAILABLE'});assert.equal(f.surface.windows.quickPopover,old);
    assert.equal(old.destroyed,false);assert.equal(f.surface.get().visible,false);assert.equal(f.surface.get().overlayPreparing,false);
  }finally{f.surface.dispose();}
});

test('marks-only feedback can enter DRAFT, reopen preserved marks, add actual intent and send exactly once',async()=>{
  const saved=new Map(),requests=[];
  const f=fixture({onDraft:async value=>{
    saved.set(value.draftId,{...value,revision:value.expectedRevision+1});
    return {id:value.draftId,revision:value.expectedRevision+1,sha256:sha,status:value.delivery==='DRAFT'?'SAVED':'EDITING'};
  },onLoadDraft:async({draftId})=>{
    if(!draftId)return null;const original=saved.get(draftId);
    assert.equal(original.delivery,'DRAFT');assert.equal(original.comment,'');
    return {id:'reopened_marks',revision:1,comment:original.comment,marks:original.marks,
      capture:{...original.capture,restored:false},restored:false};
  },onSend:async body=>{requests.push(body);return {operation_id:body.operationId,status:'RETURNED',text:'原标记上的实际回复'};}});
  try{
    await f.surface.beginAnnotation();
    const marks=[{type:'rect',x:.15,y:.25,width:.2,height:.35}];
    await f.send({kind:'edit',epoch:f.surface.get().epoch,comment:'',marks},'annotationOverlay');
    await f.command('review','annotationOverlay');
    assert.equal(f.surface.get().canDraft,true);assert.equal(f.surface.get().canSend,false);
    const originalId=f.surface.get().draft.id;assert.deepEqual(await f.command('draft'),{status:'DRAFT_SAVED'});
    assert.equal(f.surface.get().visible,false);assert.equal(f.surface.get().draft,null);assert.equal(requests.length,0);
    assert.deepEqual(saved.get(originalId).marks,marks);assert.equal(saved.get(originalId).delivery,'DRAFT');
    await f.surface.openAsk({draftId:originalId});assert.deepEqual(f.surface.get().draft.marks,marks);
    await f.edit('请解释标记区域中这两个步骤为什么要依次执行',marks);
    assert.equal(f.surface.get().canSend,true);await f.command('send');
    assert.equal(requests.length,1);assert.equal(requests[0].comment,'请解释标记区域中这两个步骤为什么要依次执行');
    assert.deepEqual(requests[0].marks,marks);assert.equal(requests[0].capture.snapshot.operation_id,'capture_actual');
    assert.equal(f.surface.get().draft.phase,'COMPLETED');assert.equal(saved.get(originalId).delivery,'DRAFT');
    await f.surface.openAsk();assert.deepEqual(await f.command('send'),{status:'BUSY'});assert.equal(requests.length,1);
    assert.equal(f.events.some(row=>row[0]==='expand'),false);
  }finally{f.surface.dispose();}
});

test('marks-only SEND remains visible with precise inline intent request and never persists SEND or calls model',async()=>{
  const f=fixture();try{
    await f.surface.beginAnnotation();const marks=[{type:'freehand',points:[{x:.1,y:.2},{x:.4,y:.7}]}];
    await f.send({kind:'edit',epoch:f.surface.get().epoch,comment:'  ',marks},'annotationOverlay');await f.command('review','annotationOverlay');
    assert.equal(f.surface.get().canDraft,true);assert.equal(f.surface.get().canSend,false);
    assert.deepEqual(await f.command('send'),{status:'INTENT_REQUIRED'});
    assert.equal(f.surface.get().visible,true);assert.equal(f.surface.get().draft.phase,'EDITING');assert.deepEqual(f.surface.get().draft.marks,marks);
    assert.equal(f.surface.get().error,'请补充要解释或修改什么，标记已保留。');
    assert.equal(f.events.some(row=>['send','draft','expand'].includes(row[0])),false);assert.equal(f.surface.get().draft.operationId,null);
  }finally{f.surface.dispose();}
});

test('real QuickHost and durable journal preserve marks-only DRAFT, then dispatch once after explicit text',async t=>{
  const directory=fs.mkdtempSync(path.join(process.env.EEAS_QA_TMP||os.tmpdir(),'eeas-surface-marks-integration-'));
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  const journal=new QuickJournal({dataDir:directory,hostGeneration:'integration_host'}),requests=[];
  const snapshot=capture().snapshot,imageRef={sha256:sha,width:900,height:600};
  snapshot.preview.image_ref=imageRef;
  snapshot.observation.observations={visual:{image_ref:imageRef,width:900,height:600,pixel_source:'VISIBLE_TARGET_RECTANGLE',
    pixel_source_proof:{status:'VISIBLE_TARGET_RECTANGLE',capture_method:'PILLOW_ACTUAL_DESKTOP_CROP',
      coordinate_space:'PHYSICAL_PIXELS_OWN_THREAD_PMV2',image_sha256:sha,target_bounds:{x:100,y:80,width:900,height:600}}}};
  const host=new QuickHost({journal,getCompanion:()=>({capture:async()=>snapshot}),loadSnapshot:async()=>snapshot,
    request:async(url,body)=>{
      requests.push({url,body});assert.equal(journal.command(body.operation_id).status,'DISPATCHING');
      return {operation_id:body.operation_id,status:'RETURNED',text:'实际持久边界之后的模型夹具回复'};
    }});
  host.setScope({projectId:'project_actual',conversationId:'conversation_actual'});
  const f=fixture({onCapture:body=>host.capture(body),onDraft:body=>host.save(body),onLoadDraft:body=>host.load(body),
    onSend:body=>host.send(body),onInspect:id=>host.inspect(id)});
  try{
    await f.surface.beginAnnotation();const marks=[{type:'rect',x:.2,y:.2,width:.2,height:.3}];
    await f.send({kind:'edit',epoch:f.surface.get().epoch,comment:'',marks},'annotationOverlay');await f.command('review','annotationOverlay');
    assert.deepEqual(await f.command('send'),{status:'INTENT_REQUIRED'});assert.equal(f.surface.get().visible,true);assert.equal(requests.length,0);
    assert.deepEqual(await f.command('draft'),{status:'DRAFT_SAVED'});assert.equal(requests.length,0);
    const original=host.list().records[0];assert.equal(original.delivery,'DRAFT');assert.equal(original.comment,'');assert.equal(original.marks[0].kind,'rect');
    const persisted=new QuickJournal({dataDir:directory,hostGeneration:'reader_only'});
    assert.equal(persisted.find(original.id).marks[0].kind,'rect');
    await f.surface.openAsk({draftId:original.id});assert.notEqual(f.surface.get().draft.id,original.id);assert.deepEqual(f.surface.get().draft.marks,marks);
    await f.edit('请解释这个标记区域的处理顺序',marks);await f.command('send');assert.equal(requests.length,1);
    assert.equal(requests[0].url,'/api/desktop-host/native-messages');assert.equal(requests[0].body.conversation_id,'conversation_actual');
    assert.match(requests[0].body.raw_text,/请解释这个标记区域的处理顺序/);assert.equal(requests[0].body.context_ref.sha256,sha);
    assert.equal(journal.get({id:original.id,revision:original.revision,sha256:original.sha256}).delivery,'DRAFT');
    assert.equal(f.surface.get().draft.phase,'COMPLETED');assert.equal(f.events.some(row=>row[0]==='expand'),false);
  }finally{f.surface.dispose();host.close();}
});

test('known result with local persistence failure stays completed and renders a separate visible warning plus original inspect',async()=>{
  let requests=0;const f=fixture({onSend:async body=>{requests++;return {operation_id:body.operationId,status:'RETURNED',text:'确定收到的结果',
    local_persistence_status:'FAILED',local_persistence_message:'private exception must not leak',local_projection:{complete:false,original_operation_id:body.operationId}};}});
  try{
    await f.surface.openAsk();await f.edit('解释当前页');await f.command('send');await f.surface.openAsk();
    const actual=f.surface.get();assert.equal(actual.draft.phase,'COMPLETED');assert.equal(actual.result.status,'RETURNED');
    assert.equal(actual.result.local_persistence_status,'FAILED');assert.equal(JSON.stringify(actual).includes('private exception'),false);
    const elements=new Map(),callbacks={},buttons=[{dataset:{command:'inspect'}},{dataset:{command:'send'}},{dataset:{command:'draft'}}];
    function element(){return {value:'',src:'',dataset:{},style:{},children:[],parentElement:{clientWidth:380},listeners:{},
      addEventListener(name,fn){this.listeners[name]=fn;},setAttribute(){},removeAttribute(){},replaceChildren(){this.children=[];},append(value){this.children.push(value);}};}
    for(const id of ['#comment','#source-image','#marks','#source-label','#preview','#toggle-preview','#editor','#question','#keyboard-hint','#retake-confirm','#heading','#status','#reply','#files'])elements.set(id,element());
    const context=vm.createContext({require:()=>({ipcRenderer:{invoke:async()=>({}),on(){}}}),document:{body:{dataset:{role:'popover'}},activeElement:null,
      querySelector:key=>elements.get(key)||null,querySelectorAll:selector=>selector==='[data-command]'?buttons:[],createElementNS:element,createElement:element},
      window:{addEventListener:(name,fn)=>{callbacks[name]=fn;},requestAnimationFrame:()=>1},console,setTimeout,clearTimeout});
    for(const button of buttons)button.addEventListener=()=>{};
    vm.runInContext(controllerPreloadSource()+'\nglobalThis.check={render};',context);
    callbacks.DOMContentLoaded();context.check.render(actual);
    assert.match(elements.get('#status').textContent,/回复已收到，本机保存未成功；请核查原操作，勿重复发送/);
    assert.match(elements.get('#status').textContent,/仅保留部分回执/);assert.equal(buttons[0].hidden,false);assert.equal(buttons[0].textContent,'核查原操作');
    assert.equal(elements.get('#reply').textContent,'确定收到的结果');assert.equal(actual.draft.phase,'COMPLETED');
    const unknown={...actual,draft:{...actual.draft,phase:'UNKNOWN'},result:{...actual.result,status:'UNKNOWN_OUTCOME'}};
    context.check.render(unknown);assert.match(elements.get('#status').textContent,/结果尚不确定/);
    assert.doesNotMatch(elements.get('#status').textContent,/回复已收到/);assert.equal(requests,1);
    await f.command('inspect');assert.equal(f.events.filter(row=>row[0]==='inspect').length,1);assert.equal(requests,1);
  }finally{f.surface.dispose();}
});

test('text mark schema bounds Unicode, aggregate text and mark count without inventing a region',()=>{
  const text={type:'text',x:1,y:0,text:'🙂'.repeat(2000)};
  assert.deepEqual(normalizedMarks([text]),[text]);assert.deepEqual(markRegions([text]),[]);
  assert.equal(normalizedMarks(Array.from({length:8},()=>text)).length,8);
  for(const marks of [[{...text,text:' ' }],[{...text,text:'🙂'.repeat(2001)}],
    Array.from({length:9},()=>text),Array.from({length:33},()=>({...text,text:'一个'})),
    [{...text,x:NaN}],[{...text,y:1.01}],[{...text,text:'\ud800'}],[{...text,path:'/not/a/source'}]]){
    assert.throws(()=>normalizedMarks(marks));
  }
});

test('pending on-image text commits on review, supplies intent and sends without duplicate comment or recapture',async()=>{
  const f=fixture();try{
    await f.surface.beginAnnotation();const original=f.surface.draft.capture;
    await f.send({kind:'text-edit',epoch:f.surface.get().epoch,editor:{index:null,x:.25,y:.4,text:'这里应先检查接口版本，再开始处理。'}},'annotationOverlay');
    assert.equal(f.surface.get().draft.textEditor.text,'这里应先检查接口版本，再开始处理。');
    assert.equal(f.surface.get().draft.marks.length,0);assert.equal(f.events.filter(row=>row[0]==='draft').length,0);
    await f.command('review','annotationOverlay');
    assert.equal(f.surface.get().canSend,true);assert.equal(f.surface.get().draft.comment,'');
    assert.deepEqual(f.surface.get().draft.marks,[{type:'text',x:.25,y:.4,text:'这里应先检查接口版本，再开始处理。'}]);
    await f.command('send');const sent=f.events.filter(row=>row[0]==='send');assert.equal(sent.length,1);
    assert.equal(sent[0][1].comment,'');assert.equal(sent[0][1].marks[0].text,'这里应先检查接口版本，再开始处理。');
    assert.equal(sent[0][1].capture,original);assert.equal(f.events.filter(row=>row[0]==='capture').length,1);
    assert.equal(f.events.some(row=>row[0]==='expand'),false);
  }finally{f.surface.dispose();}
});

test('native hide commits valid pending text, and actual save error retains it until explicit retry',async()=>{
  let attempts=0;const f=fixture({onDraft:async input=>{
    attempts++;if(attempts===1)throw new Error('Disk write denied');
    return {id:input.draftId,revision:input.expectedRevision+1,sha256:sha,status:'EDITING'};
  }});try{
    await f.surface.beginAnnotation();await f.send({kind:'text-edit',epoch:f.surface.get().epoch,
      editor:{index:null,x:.3,y:.2,text:'还没按确认也不能丢掉这条文字'}},'annotationOverlay');
    await assert.rejects(f.surface.hide({save:true}),/Disk write denied/);
    assert.equal(f.surface.get().draft.marks[0].text,'还没按确认也不能丢掉这条文字');
    assert.equal(f.surface.get().draft.revision,0);assert.equal(f.surface.get().visible,true);
    assert.match(f.surface.get().error,/保存未完成/);assert.equal(f.surface.get().draft.textEditor,null);
    await f.surface.hide({save:true});assert.equal(attempts,2);assert.equal(f.surface.get().draft.revision,1);
    assert.equal(f.surface.get().visible,false);assert.equal(f.surface.get().overlayVisible,false);
  }finally{f.surface.dispose();}
});

test('over-limit pending text blocks hide and mode change, preserving input until explicit cancel',async()=>{
  const f=fixture();try{
    await f.surface.beginAnnotation();const epoch=f.surface.get().epoch,original=f.surface.draft.capture;
    await f.send({kind:'text-edit',epoch,editor:{index:null,x:.1,y:.2,text:'中'.repeat(2001)}},'annotationOverlay');
    assert.deepEqual(await f.command('text-confirm','annotationOverlay'),{status:'ACTION_FAILED'});
    await assert.rejects(f.surface.hide());await assert.rejects(f.surface.openAsk());
    assert.equal(f.surface.get().draft.textEditor.text.length,2001);assert.equal(f.surface.get().epoch,epoch);
    assert.equal(f.surface.get().overlayVisible,true);assert.equal(f.surface.draft.capture,original);
    assert.equal(f.events.some(row=>row[0]==='draft'),false);assert.equal(f.events.filter(row=>row[0]==='capture').length,1);
    await f.command('text-cancel','annotationOverlay');assert.equal(f.surface.get().draft.textEditor,null);
    assert.equal(f.surface.get().draft.marks.length,0);await f.surface.hide();
  }finally{f.surface.dispose();}
});

test('text can be reselected, cancelled, changed and deleted while switching modes retains the frozen capture',async()=>{
  const f=fixture();try{
    await f.surface.beginAnnotation();const original=f.surface.draft.capture;
    const type=editor=>f.send({kind:'text-edit',epoch:f.surface.get().epoch,editor},'annotationOverlay');
    await type({index:null,x:.2,y:.3,text:'第一条意见'});await f.surface.openAsk();
    assert.equal(f.surface.get().draft.marks[0].text,'第一条意见');assert.equal(f.surface.get().visible,true);
    await f.command('annotate');await tick();
    await type({index:0,x:.2,y:.3,text:'这个修改明确取消'});await f.command('text-cancel','annotationOverlay');
    assert.equal(f.surface.get().draft.marks[0].text,'第一条意见');
    await type({index:0,x:.2,y:.3,text:'第二版意见'});await f.command('text-confirm','annotationOverlay');
    assert.equal(f.surface.get().draft.marks[0].text,'第二版意见');
    await type({index:0,x:.2,y:.3,text:''});await f.command('text-delete','annotationOverlay');
    assert.deepEqual(f.surface.get().draft.marks,[]);assert.equal(f.surface.get().draft.textEditor,null);
    assert.equal(f.surface.draft.capture,original);assert.equal(f.events.filter(row=>row[0]==='capture').length,1);
  }finally{f.surface.dispose();}
});

// Real preload event handlers connected to the real QuickSurface. DOM/window
// objects are explicit portable boundaries; this does not qualify native IME,
// pixels or Windows focus. No renderer behavior is substituted by success data.
function preloadBridge(f,role='overlay'){
  const callbacks={},elements=new Map(),requests=[];
  const document={body:{dataset:{role,i18n:JSON.stringify(require('../../../packages/i18n/host.json').messages)}},activeElement:null,hasFocus:()=>true};
  function element(){return {value:'',src:'',dataset:{},style:{},children:[],listeners:{},hidden:false,complete:true,naturalWidth:900,naturalHeight:600,parentElement:{clientWidth:380},
    addEventListener(name,fn){this.listeners[name]=fn;},setAttribute(){},removeAttribute(){this.src='';},replaceChildren(){this.children=[];},
    append(...values){this.children.push(...values);},focus(){document.activeElement=this;},select(){this.selected=true;},
    setPointerCapture(){},hasPointerCapture(){return false;},releasePointerCapture(){},getBoundingClientRect(){return {left:0,top:0,width:900,height:600};}};}
  for(const id of ['#canvas','#source-image','#marks','.overlay-status','#text-marks','#text-editor','#text-value','#text-count','[data-text-command="text-delete"]',
    '#comment','#source-label','#context-indicator','#mark-count','#preview','#toggle-preview','#editor','#question','#text-summary',
    '#keyboard-hint','#retake-confirm','#heading','#status','#reply','#files','#file-context','#file-context-label','#ask-panel','#activity-panel','#activity-list','#activity-status','#activity-error'])elements.set(id,element());
  const tools=['rect','freehand','arrow','text'].map(tool=>Object.assign(element(),{dataset:{tool}}));
  const textButtons=['text-confirm','text-cancel','text-delete'].map(textCommand=>Object.assign(element(),{dataset:{textCommand}}));
  const commands=['send','draft','annotate','recapture','inspect','stop','file-clear','new-question','activity-refresh','activity-all','activity-back','dismiss'].map(command=>Object.assign(element(),{dataset:{command}}));
  Object.assign(document,{querySelector:key=>elements.get(key)||null,querySelectorAll:key=>key==='[data-tool]'?tools:key==='[data-text-command]'?textButtons:key==='[data-command]'?commands:[],createElementNS:element,createElement:element});
  const hostRole=role==='overlay'?'annotationOverlay':'quickPopover';
  const context=vm.createContext({require:()=>({ipcRenderer:{
    invoke:async(_channel,payload)=>{requests.push(payload);return f.send(payload,hostRole);},
    on(_channel,handler){callbacks.state=handler;}}}),document,
    window:{addEventListener:(name,handler)=>{callbacks[name]=handler;},requestAnimationFrame:fn=>{queueMicrotask(fn);return 1;}},console,setTimeout,clearTimeout});
  vm.runInContext(controllerPreloadSource()+
    '\nglobalThis.bridge={command,render,startText,marks:()=>localMarks,editor:()=>editor,history:()=>({undo:history.length,redo:future.length})};',context);
  const own=f.surface.windows[hostRole],original=own.webContents.send;
  own.webContents.send=(channel,next)=>{original(channel,next);if(channel==='eeas:quick-surface:state')callbacks.state?.(null,next);};
  callbacks.DOMContentLoaded();
  return {api:context.bridge,callbacks,requests,elements,tools,textButtons,commands,document,
    pointer(name,x,y,id=1){elements.get('#canvas').listeners[name]({button:0,pointerId:id,clientX:x,clientY:y,preventDefault(){}});},
    input(value){const input=elements.get('#text-value');input.value=value;input.listeners.input();},
    key(key,extras={}){let prevented=false;callbacks.keydown({key,...extras,preventDefault(){prevented=true;}});return prevented;}};
}

test('actual preload text click/IME/confirm/reselect/cancel/delete operate on the same frozen picture',async()=>{
  const f=fixture();try{
    await f.surface.beginAnnotation();const r=preloadBridge(f);await tick();
    await r.tools.find(button=>button.dataset.tool==='text').listeners.click();
    r.pointer('pointerdown',225,240);await tick();assert.equal(r.api.editor().x,.25);
    const input=r.elements.get('#text-value');assert.equal(r.elements.get('#text-editor').hidden,false);
    input.listeners.compositionstart();r.input('这里的接口需要解释🙂');await tick();
    const before=r.requests.length;assert.equal(r.key('Enter',{ctrlKey:true,isComposing:true}),false);
    assert.equal(r.key('Escape',{keyCode:229}),false);await tick();assert.equal(r.requests.length,before);
    input.listeners.compositionend();await tick();assert.equal(r.key('Enter',{ctrlKey:true}),true);await tick();await tick();
    assert.equal(f.surface.get().draft.marks[0].text,'这里的接口需要解释🙂');assert.equal(r.api.editor(),null);
    assert.equal(r.elements.get('#text-marks').children[0].textContent,'这里的接口需要解释🙂');
    r.elements.get('#text-marks').children[0].listeners.click();await tick();r.input('这次编辑取消');await tick();
    r.key('Escape');await tick();assert.equal(f.surface.get().draft.marks[0].text,'这里的接口需要解释🙂');
    await r.api.startText({x:.25,y:.4},0);r.input('修改后只保留这条');await tick();await r.api.command('text-confirm');
    assert.equal(f.surface.get().draft.marks[0].text,'修改后只保留这条');
    await r.api.startText({x:.25,y:.4},0);await r.api.command('text-delete');assert.equal(f.surface.get().draft.marks.length,0);
    await r.api.command('undo');await tick();assert.equal(f.surface.get().draft.marks[0].text,'修改后只保留这条');
    assert.equal(f.events.filter(row=>row[0]==='capture').length,1);assert.equal(r.elements.get('#source-image').src,image);
    assert.equal(f.events.some(row=>row[0]==='send'||row[0]==='expand'),false);
  }finally{f.surface.dispose();}
});

test('real preload undo/review/reopen/redo and repeated same-image state publications preserve history',async()=>{
  const f=fixture();try{
    await f.surface.beginAnnotation();const r=preloadBridge(f);await tick();
    r.pointer('pointerdown',90,120);r.pointer('pointermove',360,420);r.pointer('pointerup',360,420);await tick();
    assert.equal(f.surface.get().draft.marks.length,1);await r.api.command('undo');await tick();assert.equal(f.surface.get().draft.marks.length,0);
    assert.equal(r.api.history().redo,1);const original=f.surface.draft.capture;
    await r.api.command('review');await f.command('annotate');await tick();
    for(let index=0;index<8;index++){r.api.render(f.surface.get());r.elements.get('#source-image').listeners.load();}
    assert.equal(r.api.history().redo,1);await r.api.command('redo');await tick();
    assert.equal(f.surface.get().draft.marks.length,1);assert.equal(f.surface.draft.capture,original);
    assert.equal(f.events.filter(row=>row[0]==='capture').length,1);assert.equal(r.api.history().undo,1);
    await r.api.command('review');assert.equal(f.surface.get().canDraft,true);
  }finally{f.surface.dispose();}
});

test('orb invocation anchor positions the quick view while keyboard falls back to cursor; neither recaptures a draft',async()=>{
  const f=fixture();try{
    await f.surface.openAsk({anchor:{x:1100,y:800,orbBounds:{x:1072,y:772,width:56,height:56}}});
    assert.deepEqual(f.surface.anchor,{x:1100,y:800});assert.deepEqual(f.surface.windows.quickPopover.getBounds(),{x:1020,y:612,width:380,height:176});
    await f.surface.beginAnnotation({anchor:{x:200,y:800}});await f.surface.openAsk();
    assert.deepEqual(f.surface.anchor,{x:150,y:700});assert.equal(f.events.filter(row=>row[0]==='capture').length,1);
    assert.equal(f.surface.get().source.image,image);
  }finally{f.surface.dispose();}
});

test('real Journal/Host persist pending image text, reload it without authority promotion, then single-send with text intent',async t=>{
  const directory=fs.mkdtempSync(path.join(process.env.EEAS_QA_TMP||os.tmpdir(),'eeas-text-journal-integration-'));
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  const journal=new QuickJournal({dataDir:directory,hostGeneration:'image_text_host'}),requests=[];
  const snapshot=capture().snapshot,imageRef={sha256:sha,width:900,height:600};snapshot.preview.image_ref=imageRef;
  snapshot.observation.observations={visual:{image_ref:imageRef,width:900,height:600,pixel_source:'VISIBLE_TARGET_RECTANGLE',
    pixel_source_proof:{status:'VISIBLE_TARGET_RECTANGLE',capture_method:'PILLOW_ACTUAL_DESKTOP_CROP',coordinate_space:'PHYSICAL_PIXELS_OWN_THREAD_PMV2',
      image_sha256:sha,target_bounds:{x:100,y:80,width:900,height:600}}}};
  const host=new QuickHost({journal,getCompanion:()=>({capture:async()=>snapshot}),loadSnapshot:async()=>snapshot,
    request:async(url,body)=>{requests.push({url,body});assert.equal(journal.command(body.operation_id).status,'DISPATCHING');
      return {operation_id:body.operation_id,status:'RETURNED',text:'文字意见处理回执'};}});
  host.setScope({projectId:'text_project',conversationId:'text_conversation'});
  const f=fixture({onCapture:body=>host.capture(body),onDraft:body=>host.save(body),onLoadDraft:body=>host.load(body),onSend:body=>host.send(body)});
  let readerHost;
  try{
    await f.surface.beginAnnotation();const id=f.surface.get().draft.id;
    await f.send({kind:'text-edit',epoch:f.surface.get().epoch,editor:{index:null,x:.3,y:.4,text:'请解释这里的调用次序🙂'}},'annotationOverlay');
    await f.surface.hide({save:true});const original=journal.find(id);
    assert.equal(original.delivery,'EDITING');assert.equal(original.comment,'');
    assert.deepEqual(original.marks,[{kind:'text',x:.3,y:.4,text:'请解释这里的调用次序🙂'}]);
    const fresh=new QuickJournal({dataDir:directory,hostGeneration:'new_readonly_host'});assert.equal(fresh.find(id).sha256,original.sha256);
    readerHost=new QuickHost({journal:fresh,getCompanion:()=>({}),request:async()=>{throw Error('Reload cannot dispatch');}});
    readerHost.setScope({projectId:'text_project',conversationId:'text_conversation'});
    const restored=await readerHost.load({draftId:id});assert.equal(restored.restored,true);
    assert.equal(restored.marks[0].type,'text');assert.equal(restored.marks[0].text,'请解释这里的调用次序🙂');
    assert.equal(requests.length,0);
    await f.surface.openAsk();assert.equal(f.surface.get().canSend,true);await f.command('send');
    assert.equal(requests.length,1);assert.match(requests[0].body.raw_text,/请解释这里的调用次序🙂/);
    assert.equal(requests[0].body.conversation_id,'text_conversation');assert.equal(f.surface.get().draft.phase,'COMPLETED');
    assert.equal(journal.find(id).marks[0].text,'请解释这里的调用次序🙂');assert.equal(journal.find(id).comment,'');
    assert.equal(requests[0].body.context_ref.sha256,sha);assert.equal(f.events.some(row=>row[0]==='expand'),false);
  }finally{f.surface.dispose();host.close();readerHost?.close();}
});

test('compact ask renderer defaults to input with no preview/status/footer space; evidence is explicit and does not recapture',async()=>{
  const html=quickHtml('popover');assert.match(html,/placeholder="问当前页"/);assert.match(html,/<h1 id="heading" hidden\b[^>]*>/);
  assert.match(html,/<span id="source-label" hidden>/);assert.match(html,/<button data-command="expand" hidden/);assert.doesNotMatch(html.replace(/ data-i18n="[^"]*"/,''),/data-command="workbench"|Ctrl\+Enter/);
  const f=fixture();try{
    await f.surface.openAsk();const r=preloadBridge(f,'popover');await tick();
    assert.equal(f.surface.windows.quickPopover.getBounds().width,380);assert.equal(f.surface.windows.quickPopover.getBounds().height,176);
    assert.equal(r.elements.get('#preview').hidden,true);assert.equal(r.elements.get('#status').hidden,true);
    assert.equal(r.elements.get('#keyboard-hint').hidden,true);assert.equal(r.elements.get('#mark-count').hidden,true);
    assert.equal(r.elements.get('#context-indicator').textContent,'当前页');assert.equal(r.commands.find(button=>button.dataset.command==='draft').hidden,true);
    r.elements.get('#toggle-preview').listeners.click();await tick();
    assert.equal(r.elements.get('#preview').hidden,false);assert.equal(f.surface.windows.quickPopover.getBounds().height,301);
    r.elements.get('#toggle-preview').listeners.click();await tick();assert.equal(r.elements.get('#preview').hidden,true);
    assert.equal(f.surface.windows.quickPopover.getBounds().height,176);assert.equal(f.events.filter(row=>row[0]==='capture').length,1);
    assert.equal(f.events.some(row=>row[0]==='send'||row[0]==='expand'),false);
  }finally{f.surface.dispose();}
});

test('compact ask keeps real unconfigured-model feedback visible and text marks readable without expanding a screenshot',async()=>{
  const f=fixture({onSend:async body=>({operation_id:body.operationId,status:'NOT_CONFIGURED',error:{message:'模型尚未配置。'}})});
  try{
    await f.surface.openAsk();const r=preloadBridge(f,'popover');await tick();
    await f.edit('',[{type:'text',x:.4,y:.2,text:'这里的版本信息是什么意思？'}]);await tick();
    assert.equal(r.elements.get('#preview').hidden,true);assert.equal(r.elements.get('#text-summary').textContent,'这里的版本信息是什么意思？');
    assert.equal(r.elements.get('#mark-count').textContent,'1 处批注');assert.equal(r.commands.find(button=>button.dataset.command==='draft').hidden,false);
    await f.command('send');await tick();assert.equal(f.surface.get().draft.phase,'FAILED');
    await f.surface.openAsk();await tick();assert.equal(r.elements.get('#preview').hidden,true);
    assert.equal(r.elements.get('#reply').textContent,'模型尚未配置。');assert.equal(r.elements.get('#status').hidden,false);
    assert.equal(f.surface.windows.quickPopover.getBounds().height,320);assert.equal(f.events.some(row=>row[0]==='expand'),false);
  }finally{f.surface.dispose();}
});

test('display change moves pending text into compact editing without losing over-limit input or claiming save',async()=>{
  const f=fixture();try{
    await f.surface.beginAnnotation();await f.send({kind:'text-edit',epoch:f.surface.get().epoch,
      editor:{index:null,x:.3,y:.3,text:'字'.repeat(2001)}},'annotationOverlay');
    f.screen.emit('display-metrics-changed');assert.equal(f.surface.get().overlayVisible,false);assert.equal(f.surface.get().visible,true);
    const r=preloadBridge(f,'popover');await tick();assert.equal(r.elements.get('#text-value').value.length,2001);
    assert.equal(r.elements.get('#text-editor').hidden,false);assert.equal(r.elements.get('#editor').hidden,true);
    assert.equal(f.events.filter(row=>row[0]==='draft').length,0);await r.api.command('text-confirm');
    assert.equal(f.surface.get().draft.textEditor.text.length,2001);assert.equal(f.surface.get().draft.revision,0);
    r.input('显示器变化后仍保留这条意见');await tick();await r.api.command('text-confirm');
    assert.equal(f.surface.get().draft.marks[0].text,'显示器变化后仍保留这条意见');
    await f.surface.hide();assert.equal(f.surface.get().draft.revision,1);
    assert.equal(f.events.filter(row=>row[0]==='capture').length,1);assert.equal(f.events.some(row=>row[0]==='send'),false);
  }finally{f.surface.dispose();}
});

function activityTask(name,extra={}){return {key:{kind:'original_session',project_id:'independent_project',object_id:name,operation_id:'operation_'+name,generation:2},
  label:'独立任务 '+name,classification:'RUNNING',source_status:'RUNNING',observation_status:'CURRENT',actual_error_code:null,observed_at:'2026-10-15T08:00:00+00:00',...extra};}
function activitySnapshot(tasks=[activityTask('one')],extra={}){return {settings:{enabled:true},observer:{status:'READY'},tasks,...extra};}

test('activity reuses registered quick HWND without creating a draft, capture, model request or Main reveal; bounded metadata excludes trusted keys',async()=>{
  const f=fixture({onOpenTask:()=>assert.fail('not an automatic open'),onExpandActivity:()=>assert.fail('not an automatic expand')});try{
    await f.surface.ready;const original={...f.surface.windows},calls=f.events.length;
    const tasks=Array.from({length:25},(_,i)=>activityTask('item_'+i));tasks[24]=activityTask('needs_input',{classification:'WAITING_USER',source_status:'WAITING_USER'});
    await f.surface.openActivity(activitySnapshot(tasks),{anchor:{x:900,y:700}});
    const state=f.surface.get();assert.equal(state.presentation,'ACTIVITY');assert.equal(state.draft,null);
    assert.equal(state.activity.items.length,20);assert.equal(state.activity.full_count,25);assert.equal(state.activity.has_more,true);
    assert.equal(state.activity.items[0].group,'attention');assert.deepEqual(state.activity.counts,{attention:1,running:24,recent:0});
    assert.equal(JSON.stringify(state.activity).includes('independent_project'),false);assert.equal(JSON.stringify(state.activity).includes('operation_'),false);
    assert.deepEqual(f.surface.windows,original);assert.equal(f.surface.windows.quickPopover.getBounds().width,380);assert.equal(f.surface.windows.quickPopover.getBounds().height,420);
    assert.equal(f.events.length,calls);assert.equal(f.surface.windows.quickPopover.calls.filter(row=>row[0]==='show').length,0);
  }finally{f.surface.dispose();}
});

test('activity commits pending image text through actual save, preserves old task scope and returns to the same frozen ask draft',async()=>{
  const f=fixture();try{
    await f.surface.beginAnnotation();const original=f.surface.draft.capture,id=f.surface.draft.id;
    await f.send({kind:'text-edit',epoch:f.surface.get().epoch,editor:{index:null,x:.2,y:.4,text:'这份旧任务的未确认文字'}},'annotationOverlay');
    await f.surface.openActivity(activitySnapshot());
    const persisted=f.events.filter(row=>row[0]==='draft');assert.equal(persisted.length,1);
    assert.equal(persisted[0][1].capture,original);assert.equal(persisted[0][1].marks[0].text,'这份旧任务的未确认文字');
    assert.equal(f.surface.draft.capture,original);assert.equal(f.surface.draft.id,id);assert.equal(f.surface.get().draft.revision,1);
    await f.command('activity-back');assert.equal(f.surface.get().presentation,'ASK');assert.equal(f.surface.draft.capture,original);
    assert.equal(f.surface.get().draft.marks[0].text,'这份旧任务的未确认文字');assert.equal(f.events.filter(row=>row[0]==='capture').length,1);
    assert.equal(f.events.some(row=>row[0]==='send'||row[0]==='expand'),false);
  }finally{f.surface.dispose();}
});

test('failed or over-limit draft save cannot be bypassed by opening activity',async()=>{
  const f=fixture({onDraft:async()=>{throw new Error('Original disk failure');}});try{
    await f.surface.beginAnnotation();await f.send({kind:'text-edit',epoch:f.surface.get().epoch,
      editor:{index:null,x:.2,y:.4,text:'必须先保存的意见'}},'annotationOverlay');
    await assert.rejects(f.surface.openActivity(activitySnapshot()),/Original disk failure/);
    assert.equal(f.surface.get().presentation,'ASK');assert.equal(f.surface.get().visible,true);assert.equal(f.surface.get().draft.revision,0);
    assert.equal(f.surface.get().draft.marks[0].text,'必须先保存的意见');
  }finally{f.surface.dispose();}
  const bad=fixture();try{
    await bad.surface.beginAnnotation();await bad.send({kind:'text-edit',epoch:bad.surface.get().epoch,editor:{index:null,x:.2,y:.4,text:'字'.repeat(2001)}},'annotationOverlay');
    await assert.rejects(bad.surface.openActivity(activitySnapshot()));assert.equal(bad.surface.get().presentation,'ASK');
    assert.equal(bad.surface.get().draft.textEditor.text.length,2001);assert.equal(bad.surface.get().overlayVisible,true);
  }finally{bad.surface.dispose();}
});

test('background activity updates never show/focus a hidden window or mutate an ask draft, and invalid metadata keeps the previous list',async()=>{
  const f=fixture();try{
    await f.surface.openAsk();const original=f.surface.draft.capture;
    const before=f.surface.windows.quickPopover.calls.length;f.surface.updateActivity(activitySnapshot());
    assert.equal(f.surface.windows.quickPopover.calls.length,before);assert.equal(f.surface.draft.capture,original);
    await f.surface.openActivity(activitySnapshot());const item=f.surface.get().activity.items[0];
    f.surface.updateActivity(activitySnapshot([activityTask('unsafe',{label:'password=never-display'})]));
    assert.equal(f.surface.get().activity.items[0].token,item.token);assert.match(f.surface.get().error,/保留上次列表/);
    assert.equal(JSON.stringify(f.surface.get()).includes('never-display'),false);
    await f.surface.hide();const hidden=f.surface.windows.quickPopover.calls.length;
    f.surface.updateActivity(activitySnapshot([activityTask('background',{classification:'EXECUTION_RETURNED',source_status:'RETURNED'})]));
    assert.equal(f.surface.windows.quickPopover.calls.length,hidden);assert.equal(f.surface.get().visible,false);
    assert.equal(f.surface.draft.capture,original);assert.equal(f.events.filter(row=>row[0]==='capture').length,1);
  }finally{f.surface.dispose();}
});

test('actual activity preload sends only opaque exact-list selections; stale token cannot open a refreshed different task',async()=>{
  const opened=[],f=fixture({onOpenTask:key=>opened.push(key)});try{
    await f.surface.openActivity(activitySnapshot());const r=preloadBridge(f,'popover');await tick();
    assert.equal(r.elements.get('#ask-panel').hidden,true);assert.equal(r.elements.get('#activity-panel').hidden,false);
    const old=r.elements.get('#activity-list').children.find(item=>item.className==='activity-task');assert.ok(old);
    f.surface.updateActivity(activitySnapshot([activityTask('different')]));old.listeners.click();await tick();assert.equal(opened.length,0);
    const current=r.elements.get('#activity-list').children.find(item=>item.className==='activity-task');current.listeners.click();await tick();
    assert.deepEqual(opened,[activityTask('different').key]);assert.equal(f.surface.get().visible,false);
    const requests=r.requests.filter(item=>item.kind==='activity-open');assert.equal(requests.length,2);
    assert.deepEqual(Object.keys(requests[1]).sort(),['activity_revision','epoch','kind','token']);
    assert.equal(f.events.filter(row=>row[0]==='capture').length,0);assert.equal(f.events.some(row=>row[0]==='send'||row[0]==='expand'),false);
  }finally{f.surface.dispose();}
});

test('activity keyboard and stale ask controls cannot dispatch the saved question; only explicit overview expands Main',async()=>{
  let expanded=0;const f=fixture({onExpandActivity:()=>{expanded++;}});try{
    await f.surface.openAsk();await f.edit('旧任务意见仍保留');await f.surface.openActivity(activitySnapshot());
    const r=preloadBridge(f,'popover');await tick();const before=r.requests.length;
    assert.equal(r.key('Enter',{ctrlKey:true}),false);await tick();assert.equal(r.requests.length,before);
    assert.deepEqual(await f.command('send'),{status:'ACTION_FAILED'});assert.equal(f.events.some(row=>row[0]==='send'),false);
    assert.equal(expanded,0);await f.command('activity-all');assert.equal(expanded,1);assert.equal(f.surface.get().visible,false);
    assert.equal(f.surface.get().draft.comment,'旧任务意见仍保留');assert.equal(f.events.filter(row=>row[0]==='capture').length,1);
  }finally{f.surface.dispose();}
});

test('late local refresh after activity closes cannot reopen it or replace returned ask scope; newer background snapshot wins',async()=>{
  const gate=deferred(),f=fixture({onRefreshActivity:()=>gate.promise});try{
    await f.surface.openAsk();const original=f.surface.draft.capture;await f.surface.openActivity(activitySnapshot());
    const pending=f.command('activity-refresh');await tick();assert.equal(f.surface.get().activity.busy,true);
    await f.command('activity-back');const calls=f.surface.windows.quickPopover.calls.length;gate.resolve(activitySnapshot([activityTask('late')]));await pending;
    assert.equal(f.surface.get().presentation,'ASK');assert.equal(f.surface.draft.capture,original);assert.equal(f.surface.windows.quickPopover.calls.length,calls);
    assert.equal(f.surface.get().activity.busy,false);assert.equal(f.events.filter(row=>row[0]==='capture').length,1);
  }finally{f.surface.dispose();}
  const later=deferred(),second=fixture({onRefreshActivity:()=>later.promise});try{
    await second.surface.openActivity(activitySnapshot());const pending=second.command('activity-refresh');await tick();
    second.surface.updateActivity(activitySnapshot([activityTask('newer')]));later.resolve(activitySnapshot([activityTask('older')]));await pending;
    assert.equal(second.surface.get().activity.items[0].label,'独立任务 newer');
  }finally{second.surface.dispose();}
});

test('activity unavailable, disabled/empty and partial source states stay readable without auto GET or opening a question',async()=>{
  let reads=0;const f=fixture({onRefreshActivity:async()=>{reads++;return activitySnapshot([],{observer:{status:'PARTIAL'}});}});try{
    await f.surface.openActivity(undefined);const r=preloadBridge(f,'popover');await tick();
    assert.equal(r.elements.get('#activity-status').textContent,'尚未取得任务状态');assert.equal(reads,0);
    await f.command('activity-refresh');assert.equal(reads,1);assert.equal(r.elements.get('#activity-status').textContent,'部分任务状态待核查');
    f.surface.updateActivity(activitySnapshot([],{settings:{enabled:false},observer:{status:'DISABLED'}}));
    assert.equal(r.elements.get('#activity-status').textContent,'监督已关闭，保留最后观察');
    assert.equal(r.elements.get('#activity-list').children[0].textContent,'还没有可观察的任务');
    assert.equal(f.surface.get().draft,null);assert.equal(f.events.length,0);
  }finally{f.surface.dispose();}
});

test('actual Journal preserves old project/conversation and image text when global activity belongs to another independent task',async t=>{
  const directory=fs.mkdtempSync(path.join(process.env.EEAS_QA_TMP||os.tmpdir(),'eeas-activity-real-journal-'));
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  const journal=new QuickJournal({dataDir:directory,hostGeneration:'activity_original_host'}),snapshot=capture().snapshot;
  const imageRef={sha256:sha,width:900,height:600};snapshot.preview.image_ref=imageRef;
  snapshot.observation.observations={visual:{image_ref:imageRef,width:900,height:600,pixel_source:'VISIBLE_TARGET_RECTANGLE',pixel_source_proof:{status:'VISIBLE_TARGET_RECTANGLE',
    capture_method:'PILLOW_ACTUAL_DESKTOP_CROP',coordinate_space:'PHYSICAL_PIXELS_OWN_THREAD_PMV2',image_sha256:sha,target_bounds:{x:100,y:80,width:900,height:600}}}};
  let captures=0;const opened=[];
  const host=new QuickHost({journal,getCompanion:()=>({capture:async()=>{captures++;return snapshot;}}),loadSnapshot:async()=>snapshot,
    request:()=>assert.fail('Opening activity cannot invoke the model')});host.setScope({projectId:'old_project',conversationId:'old_conversation'});
  const f=fixture({onCapture:body=>host.capture(body),onDraft:body=>host.save(body),onLoadDraft:body=>host.load(body),onOpenTask:key=>opened.push(key)});
  try{
    await f.surface.beginAnnotation();const id=f.surface.draft.id,original=f.surface.draft.capture;
    await f.send({kind:'text-edit',epoch:f.surface.get().epoch,editor:{index:null,x:.2,y:.3,text:'保留旧项目图上的这句话'}},'annotationOverlay');
    await f.surface.openActivity(activitySnapshot());const fresh=new QuickJournal({dataDir:directory,hostGeneration:'readback_only'}),stored=fresh.find(id);
    assert.equal(stored.scope.projectId,'old_project');assert.equal(stored.scope.conversationId,'old_conversation');assert.equal(stored.contextRef.sha256,sha);
    assert.equal(stored.marks[0].kind,'text');assert.equal(stored.marks[0].text,'保留旧项目图上的这句话');
    const entry=f.surface.get().activity.items[0];await f.send({kind:'activity-open',epoch:f.surface.get().epoch,activity_revision:f.surface.get().activity.revision,token:entry.token});
    assert.deepEqual(opened,[activityTask('one').key]);await f.surface.openAsk();
    assert.equal(f.surface.draft.capture,original);assert.equal(f.surface.draft.capture.scope.projectId,'old_project');assert.equal(f.surface.draft.id,id);
    assert.equal(captures,1);assert.equal(journal.find(id).marks[0].text,'保留旧项目图上的这句话');
  }finally{f.surface.dispose();host.close();}
});

test('duplicate task clicks do not repeat an in-flight callback and a late callback cannot reopen a dismissed task list',async()=>{
  const gate=deferred();let opens=0;const f=fixture({onOpenTask:async()=>{opens++;await gate.promise;}});try{
    await f.surface.openActivity(activitySnapshot());const state=f.surface.get(),entry=state.activity.items[0];
    const payload={kind:'activity-open',epoch:state.epoch,activity_revision:state.activity.revision,token:entry.token};
    const first=f.send(payload);await tick();assert.equal(f.surface.get().activity.busy,true);
    assert.deepEqual(await f.send(payload),{status:'BUSY'});assert.equal(opens,1);
    await f.surface.hide();const calls=f.surface.windows.quickPopover.calls.length;gate.resolve();await first;
    assert.equal(f.surface.get().visible,false);assert.equal(f.surface.windows.quickPopover.calls.length,calls);assert.equal(opens,1);
  }finally{f.surface.dispose();}
});

test('opening activity and receiving an old question reply cannot silently consume that question unread state',async()=>{
  const f=fixture();try{
    await f.surface.openAsk();await f.edit('原问题在后台继续');await f.command('send');
    const operation=f.surface.get().draft.operationId;assert.equal(f.surface.get().unread,true);
    await f.surface.openActivity(activitySnapshot());assert.equal(f.surface.get().unread,true);
    const calls=f.surface.windows.quickPopover.calls.filter(row=>row[0]==='show'||row[0]==='inactive').length;
    f.surface.publishResult({operation_id:operation,status:'RETURNED',text:'旧问题的后台回复'});
    assert.equal(f.surface.get().presentation,'ACTIVITY');assert.equal(f.surface.get().unread,true);
    assert.equal(f.surface.windows.quickPopover.calls.filter(row=>row[0]==='show'||row[0]==='inactive').length,calls);
    await f.surface.openAsk();assert.equal(f.surface.get().presentation,'ASK');assert.equal(f.surface.get().unread,false);
    assert.equal(f.surface.get().result.text,'旧问题的后台回复');assert.equal(f.events.filter(row=>row[0]==='send').length,1);
  }finally{f.surface.dispose();}
});

test('project document chip renders the exact frozen file, clears through registered host clone and saves a new revision without opening Main',async t=>{
  const directory=fs.mkdtempSync(path.join(process.env.EEAS_QA_TMP||os.tmpdir(),'eeas-linked-quick-'));t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  const journal=new QuickJournal({dataDir:directory,hostGeneration:'linked_host'}),host=new QuickHost({journal,getCompanion:()=>({}),request:async()=>{throw Error('No dispatch expected');}});t.after(()=>host.close());
  const file={project_id:'linked_project',id:'b'.repeat(64),revision:1,sha256:'c'.repeat(64)};
  host.setScope({projectId:'linked_project',conversationId:'linked_conversation'});host.setFileContext({file_link_ref:file,filename:'真实人工修改稿.docx'});
  const f=fixture({onCapture:async()=>host.emptyCapture(),onDraft:value=>host.save(value),onClearFileContext:value=>host.clearDraftFileContext(value)});
  try{
    const r=preloadBridge(f,'popover');await f.surface.openAsk();r.api.render(f.surface.get());
    assert.equal(r.elements.get('#file-context').hidden,false);assert.equal(r.elements.get('#file-context-label').textContent,'文稿 · 真实人工修改稿.docx');assert.equal(f.surface.get().source.available,false);
    await f.edit('接着解释我修改过的内容');await f.surface._persist('EDITING');const before=journal.find(f.surface.draft.id),oldCapture=f.surface.draft.capture;
    await r.api.command('file-clear');const saved=journal.find(before.id);assert.ok(saved.revision>before.revision);assert.equal(saved.scope.fileLinkRef,undefined);assert.equal(saved.comment,before.comment);assert.equal(saved.scope.conversationId,before.scope.conversationId);
    assert.deepEqual(journal.get({id:before.id,revision:before.revision,sha256:before.sha256}).scope.fileLinkRef,file);assert.equal(f.surface.draft.capture.snapshot,oldCapture.snapshot);
    assert.equal(r.elements.get('#file-context').hidden,true);assert.equal(f.events.some(row=>row[0]==='expand'||row[0]==='send'),false);assert.equal(f.surface.visible,true);
  }finally{f.surface.dispose();}
});

test('actual preload stop targets only the bound original operation, remains in place, and uncertain cancellation offers GET inspection',async()=>{
  let stops=0,inspects=0;const f=fixture({onCancel:async operation=>{stops++;return {operation_id:operation,status:'RUNNING',cancellation_status:'UNKNOWN'};},onInspect:async operation=>{inspects++;return {operation_id:operation,status:'RETURNED',text:'已核查的实际回复',cancellation_status:'SETTLED'};}});
  try{
    const r=preloadBridge(f,'popover');await f.surface.openAsk();await f.edit('分析当前问题');await f.command('send');await f.surface.openAsk();r.api.render(f.surface.get());
    const operation=f.surface.draft.operationId,button=r.commands.find(item=>item.dataset.command==='stop');assert.equal(button.hidden,false);assert.equal(button.disabled,false);
    await r.api.command('stop');assert.equal(stops,1);assert.equal(f.surface.draft.operationId,operation);assert.equal(f.surface.get().draft.phase,'RUNNING');assert.match(r.elements.get('#status').textContent,/停止请求结果待核查/);assert.equal(button.disabled,true);assert.equal(button.textContent,'停止待核查');
    await r.api.command('stop');assert.equal(stops,1);assert.equal(inspects,1);assert.equal(f.surface.get().draft.phase,'COMPLETED');assert.equal(r.elements.get('#reply').textContent,'已核查的实际回复');assert.equal(f.surface.visible,true);assert.equal(f.events.some(item=>item[0]==='expand'),false);
  }finally{f.surface.dispose();}
});

test('canonical native quick/annotation titles retain existing trusted channels and two owned lifetimes',()=>{
  const {WINDOW_TITLES}=require('../product-brand.cjs'),f=fixture();try{
    assert.equal(f.surface.windows.quickPopover.options.title,WINDOW_TITLES.quick);assert.equal(f.surface.windows.annotationOverlay.options.title,WINDOW_TITLES.annotation);
    assert.match(quickHtml('popover'),new RegExp('<title>'+WINDOW_TITLES.quick+'</title>'));assert.match(quickHtml('overlay'),new RegExp('<title>'+WINDOW_TITLES.annotation+'</title>'));
    assert.equal(CHANNEL,'eeas:quick-surface:event');assert.equal(Object.keys(f.surface.windows).length,2);
  }finally{f.surface.dispose();}
});

test('live UI language keeps the same frozen picture, unfinished text mark, undo history and private draft',async()=>{
  const f=fixture();try{
    await f.surface.beginAnnotation();const r=preloadBridge(f);await tick();
    r.pointer('pointerdown',90,120);r.pointer('pointermove',360,420);r.pointer('pointerup',360,420);await tick();
    await r.api.command('undo');await tick();assert.equal(r.api.history().redo,1);
    await r.api.startText({x:.25,y:.4});r.input('确认');await tick();
    const before=f.surface.get(),draft=f.surface.draft,capture=f.surface.draft.capture;
    const requestCount=r.requests.length,events=JSON.stringify(f.events),windows={...f.surface.windows};
    f.surface.setLocale('en');await tick();
    assert.equal(f.surface.get().epoch,before.epoch);assert.equal(f.surface.draft,draft);assert.equal(f.surface.draft.capture,capture);
    assert.equal(f.surface.get().error,'');assert.equal(f.surface.get().notice,'');assert.equal(r.api.editor().text,'确认');
    assert.equal(r.elements.get('#text-value').value,'确认');assert.equal(r.api.history().redo,1);
    assert.match(r.elements.get('.overlay-status').textContent,/Ctrl\+Enter/);assert.doesNotMatch(r.elements.get('.overlay-status').textContent,/[一-龥]/);
    assert.equal(f.surface.windows.annotationOverlay.title,'Cirven annotation');assert.equal(r.requests.length,requestCount);
    assert.equal(JSON.stringify(f.events),events);for(const key of Object.keys(windows))assert.equal(f.surface.windows[key],windows[key]);
    f.surface.setLocale('zh-CN');await tick();assert.equal(r.api.editor().text,'确认');assert.equal(r.api.history().redo,1);
    assert.match(r.elements.get('.overlay-status').textContent,/在图上输入文字/);
  }finally{f.surface.dispose();}
});

test('language change renders original-operation status locally and leaves reply and artifact names untranslated',async()=>{
  const f=fixture();try{
    await f.surface.openAsk();await f.edit('发送');await f.command('send');const operation=f.surface.draft.operationId;
    f.surface.publishResult({operation_id:operation,status:'RETURNED',text:'发送',artifacts:[{filename:'发送.docx'}],local_persistence_status:'FAILED'});
    await f.surface.openAsk();const r=preloadBridge(f,'popover');await tick();const before=r.requests.length;
    f.surface.setLocale('en');await tick();
    assert.match(r.elements.get('#status').textContent,/reply|received/i);assert.doesNotMatch(r.elements.get('#status').textContent,/[一-龥]/);
    assert.equal(r.elements.get('#reply').textContent,'发送');assert.equal(r.elements.get('#question').textContent,'发送');
    assert.equal(r.elements.get('#files').children[0].textContent,'发送.docx');assert.equal(f.surface.draft.operationId,operation);assert.equal(r.requests.length,before);
    assert.equal(f.events.filter(row=>row[0]==='send').length,1);assert.equal(f.events.some(row=>row[0]==='expand'),false);
  }finally{f.surface.dispose();}
});


// Presentation UNIT: real QuickSurface + real sandbox preload callbacks. The
// explicit transport/Electron/DOM fixtures do not qualify Windows or a model.
test('model RETURNED and every unfinished publication state remain independent and visible in both locales',async()=>{
  const cases=[
    ['PUBLICATION_UNKNOWN','修订文件的生成结果尚未确认；请核查原操作，勿重新生成。'],
    ['CANCELLED_NOT_PUBLISHED','已停止生成修订文件；原回复、原文件和提案保留。'],
    ['NEEDS_CLARIFICATION','这段内容需要进一步确认，尚未生成修订文件。'],
    ['PROPOSED_NOT_PUBLISHED','修订提案已保留，尚未生成文件。'],
    ['UNAVAILABLE','修订文件暂不可用，原回复与提案保留；请核查原操作，勿重复发送。'],
  ];
  const translate=require('../../../packages/i18n/index.cjs').t;
  for(const locale of ['zh-CN','en'])for(const [artifact_status,copy]of cases){
    let sends=0,inspects=0;const answer='发送：这是模型原始回复，不应被翻译。';
    const f=fixture({locale,onSend:async body=>{sends++;return {operation_id:body.operationId,status:'RETURNED',text:answer,artifact_status};},
      onInspect:async operation=>{inspects++;return {operation_id:operation,status:'RETURNED',text:answer,artifact_status};}});
    try{
      await f.surface.openAsk();await f.edit('保留人工阈值42，只修改坏句');await f.command('send');
      const state=f.surface.get();assert.equal(state.result.status,'RETURNED');assert.equal(state.draft.phase,'COMPLETED');
      assert.equal(state.result.artifact_status,artifact_status);assert.equal(state.result.artifact_error,null);
      assert.equal(f.surface.get().visible,false,'background response cannot reopen the small window');
      const before=f.surface.windows.quickPopover.calls.filter(row=>['show','inactive'].includes(row[0])).length;
      f.surface.publishResult({...f.surface.draft.result});
      assert.equal(f.surface.windows.quickPopover.calls.filter(row=>['show','inactive'].includes(row[0])).length,before);
      await f.surface.openAsk();const r=preloadBridge(f,'popover');await tick();
      const expected=translate('renderer-agent',copy,locale);
      assert.equal(r.elements.get('#status').textContent,expected);assert.equal(r.elements.get('#status').hidden,false);
      assert.equal(r.elements.get('#status').dataset.warning,'true');assert.equal(r.elements.get('#reply').textContent,answer);
      const inspect=r.commands.find(button=>button.dataset.command==='inspect');assert.equal(inspect.hidden,false);
      assert.equal(inspect.textContent,translate('host','核查原操作',locale));
      await r.api.command('inspect');assert.equal(inspects,1);assert.equal(sends,1);
      assert.equal((await f.command('send')).status,'BUSY');assert.equal(sends,1);
      assert.equal(f.events.some(row=>row[0]==='expand'),false);
      assert.equal(f.surface.get().result.status,'RETURNED');assert.equal(f.surface.get().draft.phase,'COMPLETED');
    }finally{f.surface.dispose();}
  }
});

test('publication error survives projection, remains text-only and coexists with persistence warnings and live locale',async()=>{
  const copy='回复已收到，修订文件未完成生成；原文件和提案保留，请核查原操作，勿自动重发。';
  let sends=0;const f=fixture({onSend:async body=>{sends++;return {operation_id:body.operationId,status:'RETURNED',text:'取消',artifact_status:'PROPOSED_NOT_PUBLISHED',
    artifact_error:{message:copy,private_detail:'must not enter the sandbox'},local_persistence_status:'FAILED'};}});
  try{
    await f.surface.openAsk();await f.edit('同一条原问题');await f.command('send');await f.surface.openAsk();const r=preloadBridge(f,'popover');await tick();
    const operation=f.surface.draft.operationId,requests=r.requests.length;
    assert.deepEqual(f.surface.get().result.artifact_error,{message:copy});
    assert.doesNotMatch(JSON.stringify(f.surface.get()),/must not enter the sandbox/);
    assert.match(r.elements.get('#status').textContent,/本机保存未成功/);assert.ok(r.elements.get('#status').textContent.includes(copy));
    f.surface.setLocale('en');await tick();
    const expected=require('../../../packages/i18n/index.cjs').t('renderer-agent',copy,'en');
    assert.ok(r.elements.get('#status').textContent.includes(expected));assert.doesNotMatch(r.elements.get('#status').textContent,/[一-龥]/);
    assert.equal(r.elements.get('#reply').textContent,'取消');assert.equal(f.surface.draft.operationId,operation);
    assert.equal(r.requests.length,requests);assert.equal(sends,1);assert.equal(f.events.some(row=>row[0]==='expand'),false);
    // Unknown authored details are bounded plain text, never HTML or inferred success.
    f.surface.publishResult({operation_id:operation,status:'RETURNED',text:'原答复',artifact_status:'constructor',artifact_error:{message:'<img src=x onerror=alert(1)>'+('x'.repeat(900))}});
    assert.equal(f.surface.get().result.artifact_error.message.length,600);
    assert.equal(r.elements.get('#status').textContent,f.surface.get().result.artifact_error.message);
    assert.equal(r.elements.get('#status').children.length,0);assert.equal(r.elements.get('#status').dataset.warning,'true');
  }finally{f.surface.dispose();}
});

test('known new artifact and ordinary replies keep the same explicit file-open callback without a false publication warning',async()=>{
  let sends=0;const artifact={kind:'docx_revision',filename:'发送.docx',id:'a'.repeat(64)};
  const f=fixture({onSend:async body=>{sends++;return {operation_id:body.operationId,status:'RETURNED',text:'原模型回答',artifact_status:'NEW_ARTIFACT',artifacts:[artifact]};}});
  try{
    await f.surface.openAsk();await f.edit('生成修订稿');await f.command('send');await f.surface.openAsk();const r=preloadBridge(f,'popover');await tick();
    assert.equal(r.elements.get('#status').hidden,true);assert.equal(r.commands.find(button=>button.dataset.command==='inspect').hidden,true);
    assert.equal(r.elements.get('#files').children[0].textContent,'发送.docx');
    r.elements.get('#files').children[0].listeners.click();await tick();
    assert.deepEqual(f.events.filter(row=>row[0]==='open').map(row=>row[1]),[artifact]);assert.equal(sends,1);
    assert.equal(f.events.some(row=>row[0]==='expand'),false);
    f.surface.publishResult({operation_id:f.surface.draft.operationId,status:'RETURNED',text:'普通解释，不生成文件'});
    assert.equal(f.surface.get().result.artifact_status,null);assert.equal(r.elements.get('#status').hidden,true);
    assert.equal(r.elements.get('#files').hidden,true);assert.equal(r.elements.get('#reply').textContent,'普通解释，不生成文件');
  }finally{f.surface.dispose();}
});

test('UNKNOWN can start one empty question from current scope while exact durable old intent and first diagnostic remain queryable',async t=>{
  const dataDir=fs.mkdtempSync(path.join(os.tmpdir(),'quick-new-question-'));t.after(()=>fs.rmSync(dataDir,{recursive:true,force:true}));
  const journal=new QuickJournal({dataDir,hostGeneration:'host_actual'}),calls=[],seen=[];let captures=0;
  const host=new QuickHost({journal,getCompanion:()=>({capture:async()=>{captures++;return capture().snapshot;}}),
    request:async(url,body)=>{calls.push({url,body});if(body)throw Object.assign(Error('never leak'),{quick_submission_observation:{kind:'HTTP_RESPONSE',http_status:409,reason_code:'LINKED_DOCUMENT_UNAVAILABLE',api_code:'REVISION_CONFLICT'}});throw Object.assign(Error('not found'),{status:404});}});
  host.setScope({projectId:'project_a',conversationId:'conversation_a'});
  const f=fixture({onCapture:body=>host.capture(body),onDraft:body=>host.save(body),onLoadDraft:body=>host.load(body),
    onSend:body=>host.send(body),onInspect:id=>host.inspect(id),onRetainSubmitted:body=>host.retainSubmitted(body),onState:value=>seen.push(value)});
  try{
    await f.surface.openAsk();await f.edit('旧问题仍须完整保留');await f.command('send');
    const old=f.surface.get(),oldId=old.draft.id,oldOp=old.draft.operationId,command=journal.command(oldOp),original=journal.find(oldId);
    assert.equal(old.draft.phase,'UNKNOWN');assert.match(old.result.firstSubmissionLabel,/HTTP 409/);
    await f.surface.hide();await f.surface.openAsk();assert.equal(f.surface.get().draft.operationId,oldOp);assert.equal(captures,1);
    host.setScope({projectId:'project_b',conversationId:'conversation_b'});
    await f.command('new-question');const fresh=f.surface.get();
    assert.equal(fresh.draft.phase,'EDITING');assert.notEqual(fresh.draft.id,oldId);assert.equal(fresh.draft.comment,'');
    assert.deepEqual(fresh.draft.marks,[]);assert.equal(fresh.draft.operationId,null);assert.equal(fresh.result,null);assert.equal(captures,2);
    assert.deepEqual(journal.command(oldOp),command);assert.deepEqual(journal.find(oldId),original);assert.equal(calls.length,1);
    await f.edit('新问题用户正在编辑');await f.surface.hide();await f.surface.openAsk();const newId=f.surface.get().draft.id;
    assert.equal(journal.latestEditing().scope.projectId,'project_b');assert.equal(journal.latestEditing().scope.conversationId,'conversation_b');
    const oldObserved=await host.inspect(oldOp);assert.equal(oldObserved.status,'UNKNOWN_OUTCOME');assert.equal(oldObserved.first_submission_observation.http_status,409);
    assert.equal(f.surface.publishResult({...oldObserved,status:'RETURNED',text:'旧问题迟答'}).status,'STALE');
    assert.equal(f.surface.get().draft.id,newId);assert.equal(f.surface.get().draft.comment,'新问题用户正在编辑');assert.equal(f.surface.get().result,null);
    const restart=new QuickJournal({dataDir,hostGeneration:'restart_host'});assert.equal(restart.find(oldId).comment,'旧问题仍须完整保留');
    assert.equal(restart.command(oldOp).message.first_submission_observation.http_status,409);
    assert.equal(restart.latestEditing().comment,'新问题用户正在编辑');
    assert.equal(calls.filter(value=>value.body).length,1);assert.equal(calls.filter(value=>!value.body).length,1);
    assert.equal(f.events.some(value=>['expand','open','send'].includes(value[0])),false);
  }finally{f.surface.dispose();host.close();}
});

test('new-question requires exact durable disk retention; missing journal leaves original UNKNOWN untouched',async t=>{
  const dataDir=fs.mkdtempSync(path.join(os.tmpdir(),'quick-retain-failure-'));t.after(()=>fs.rmSync(dataDir,{recursive:true,force:true}));
  const journal=new QuickJournal({dataDir,hostGeneration:'host_actual'});let calls=0,captures=0;
  const host=new QuickHost({journal,getCompanion:()=>({capture:async()=>{captures++;return capture().snapshot;}}),request:async()=>{calls++;throw Error('unknown');}});
  const f=fixture({onCapture:body=>host.capture(body),onDraft:body=>host.save(body),onSend:body=>host.send(body),onRetainSubmitted:body=>host.retainSubmitted(body)});
  try{
    await f.surface.openAsk();await f.edit('完整旧内容');await f.command('send');await f.surface.openAsk();const old=f.surface.get().draft;
    fs.renameSync(journal.file,journal.file+'.retained-original');
    assert.equal((await f.command('new-question')).status,'ACTION_FAILED');assert.deepEqual(f.surface.get().draft,old);
    assert.match(f.surface.get().error,/本机保存尚未核实/);assert.equal(calls,1);assert.equal(captures,1);
  }finally{f.surface.dispose();host.close();}
});

test('two explicit new-question clicks share no old content and fence late retention/inspection without duplicate capture',async()=>{
  const retain=deferred(),inspection=deferred();let retained=0,captures=0;
  const f=fixture({onCapture:async()=>{captures++;return capture();},onRetainSubmitted:async body=>{retained++;await retain.promise;return {status:'RETAINED',operationId:body.operationId,ref:{id:body.draftId,revision:2,sha256:sha}};},
    onSend:async body=>({operation_id:body.operationId,status:'UNKNOWN_OUTCOME'}),onInspect:()=>inspection.promise});
  try{
    await f.surface.openAsk();await f.edit('旧内容');await f.command('send');await f.surface.openAsk();const old=f.surface.get().draft.operationId;
    const read=f.command('inspect'),one=f.command('new-question'),two=f.command('new-question');await tick();assert.equal(retained,2);
    retain.resolve();await Promise.all([one,two]);assert.equal(captures,2);assert.equal(f.surface.get().draft.comment,'');
    await f.edit('不要被旧答覆盖');inspection.resolve({operation_id:old,status:'RETURNED',text:'迟答'});assert.equal((await read).status,'STALE');
    assert.equal(f.surface.get().draft.comment,'不要被旧答覆盖');assert.equal(f.surface.get().result,null);
  }finally{f.surface.dispose();}
});

test('pin is explicit, single flight, shared-host controlled and never changes the frozen draft or overlay top policy',async()=>{
 const gate=deferred(),seen=[];const f=fixture({onSetPinned:async enabled=>{seen.push(enabled);await gate.promise;f.surface.setPinned(enabled);}});
 try{
  await f.surface.openAsk();await f.edit('原文字与标记保留',[{type:'rect',x:.1,y:.2,width:.3,height:.2}]);
  const before=f.surface.get().draft;
  assert.equal(f.surface.windows.quickPopover.isAlwaysOnTop(),false);assert.equal(f.surface.windows.annotationOverlay.isAlwaysOnTop(),true);
  const first=f.command('pin');assert.equal(f.surface.get().pinBusy,true);assert.equal((await f.command('pin')).status,'BUSY');
  gate.resolve();await first;assert.deepEqual(seen,[true]);assert.equal(f.surface.get().pinned,true);assert.equal(f.surface.get().pinnedPreference,true);
  assert.deepEqual(f.surface.get().draft,before);assert.equal(f.events.some(row=>['send','expand','open'].includes(row[0])),false);
  f.surface.setLocale('en');assert.equal(f.surface.get().pinned,true);assert.deepEqual(f.surface.get().draft,before);
  await f.surface.hide();await f.surface.openAsk();assert.equal(f.surface.get().pinned,true);
  await f.command('pin');assert.deepEqual(seen,[true,false]);assert.equal(f.surface.get().pinned,false);
  assert.equal(f.surface.windows.annotationOverlay.isAlwaysOnTop(),true);
 }finally{f.surface.dispose();}
});

test('failed or late pin change cannot destroy or re-show the preserved input',async()=>{
 const gate=deferred(),f=fixture({onSetPinned:()=>gate.promise});
 try{
  await f.surface.openAsk();await f.edit('未发送原稿');const pending=f.command('pin');await f.surface.hide();gate.reject(Error('failed original pin'));
  await pending;assert.equal(f.surface.get().pinned,false);assert.equal(f.surface.get().visible,false);assert.equal(f.surface.get().draft.comment,'未发送原稿');
 }finally{f.surface.dispose();}
});

test('bubble reveal is finite per explicit opening; dragging survives publications and next explicit reopen returns to its current anchor',async()=>{
 const f=fixture();try{
  await f.surface.openAsk({anchor:{x:52,y:808}});const initial=f.surface.get(),own=f.surface.windows.quickPopover;
  assert.equal(initial.motion.phase,'OPEN');assert.equal(initial.motion.origin.x,0);assert.equal(initial.motion.origin.y,176);
  const sequence=initial.motion.sequence;f.surface.setLocale('en');assert.equal(f.surface.get().motion.sequence,sequence);
  const user={x:700,y:300,width:own.bounds.width,height:own.bounds.height};own.setBounds(user);own.emit('move');
  const before=own.calls.filter(row=>row[0]==='bounds').length;await f.edit('手拖以后仍保留');f.surface.setLocale('zh-CN');
  assert.deepEqual(own.bounds,user);assert.equal(own.calls.filter(row=>row[0]==='bounds').length,before);
  await f.surface.hide();await f.surface.openAsk({anchor:{x:1380,y:20}});
  assert.equal(f.surface.get().motion.phase,'OPEN');assert.ok(f.surface.get().motion.sequence>sequence);
  assert.ok(own.bounds.x>=0&&own.bounds.x+own.bounds.width<=1400&&own.bounds.y>=0&&own.bounds.y+own.bounds.height<=900);
  assert.equal(f.surface.get().draft.comment,'手拖以后仍保留');assert.equal(own.calls.some(row=>row[0]==='focus'),false);
 }finally{f.surface.dispose();}
});

test('a new explicit open interrupts the saved close reveal and late hide/dispose cannot hide a newer question',async()=>{
 const f=fixture();try{
  await f.surface.openAsk();await f.edit('动画中仍保留');const first=f.surface.get().draft.id;
  const closing=f.surface.hide();await tick();assert.equal(f.surface.get().motion.phase,'CLOSE');
  await f.surface.openAsk({anchor:{x:20,y:20}});assert.equal((await closing).status,'STALE');
  assert.equal(f.surface.get().visible,true);assert.equal(f.surface.get().draft.id,first);assert.equal(f.surface.get().draft.comment,'动画中仍保留');
  const disposed=f.surface.hide({save:false});f.surface.dispose();await disposed;
  assert.equal(f.surface.get().disposed,true);assert.equal(f.surface.collapse,null);
 }finally{f.surface.dispose();}
});

test('bubble anchors clamp on both display edges and negative DIP origins without borrowing a global DPI scale',()=>{
 for(const area of [{x:0,y:40,width:1400,height:860},{x:-1280,y:-200,width:1280,height:720}]){
  const screen={getDisplayNearestPoint:()=>({workArea:area})};
  for(const point of [{x:area.x+28,y:area.y+28},{x:area.x+area.width-28,y:area.y+area.height-28}]){
   const b=popupBounds(screen,point);assert.ok(b.x>=area.x&&b.y>=area.y&&b.x+b.width<=area.x+area.width&&b.y+b.height<=area.y+area.height);
   assert.equal(b.width,380);assert.ok(b.height<=area.height);
  }
 }
});

function roleLoads(surface,name){return surface.windows[name].calls.filter(row=>row[0]==='load').length;}
function delayedOverlay(pending){return class extends OwnWindow{loadURL(url){super.loadURL(url);return this.options.hasShadow?Promise.resolve():pending.promise;}};}

test('lazy overlay keeps both registered windows but startup and ordinary asks load only the immediately usable popover',async()=>{
  const f=fixture();try{
    const windows={...f.surface.windows};await f.surface.ready;
    assert.equal(f.surface.get().loaded,true);assert.deepEqual(f.surface.get().roleLoaded,{quickPopover:true,annotationOverlay:false});
    assert.equal(roleLoads(f.surface,'quickPopover'),1);assert.equal(roleLoads(f.surface,'annotationOverlay'),0);
    assert.equal(windows.annotationOverlay.webContents.getURL(),'');
    assert.equal(windows.annotationOverlay.calls.filter(v=>v[0]==='send').length,0);
    await f.surface.openAsk();await f.edit('先直接提问，不开批注页');
    assert.equal(f.surface.get().visible,true);assert.equal(f.surface.get().draft.comment,'先直接提问，不开批注页');
    assert.equal(roleLoads(f.surface,'annotationOverlay'),0);assert.deepEqual(f.surface.windows,windows);
    assert.deepEqual(f.events.map(v=>v[0]),['capture']);
  }finally{f.surface.dispose();}
});

test('lazy overlay first annotation loads the original URL once and reopening retains the exact capture and draft',async()=>{
  const f=fixture();try{
    await f.surface.beginAnnotation();assert.equal(f.surface.get().overlayVisible,true);
    assert.deepEqual(f.surface.get().roleLoaded,{quickPopover:true,annotationOverlay:true});
    const frozen=f.surface.draft.capture,window=f.surface.windows.annotationOverlay;
    assert.equal(window.calls.find(v=>v[0]==='load')[1],f.surface.urls.annotationOverlay);
    await f.send({kind:'edit',epoch:f.surface.get().epoch,comment:'保留同一页意见',marks:[]},'annotationOverlay');
    await f.command('review','annotationOverlay');await f.surface.hide({animate:false});await f.surface.beginAnnotation();
    assert.equal(f.surface.get().overlayVisible,true);assert.equal(roleLoads(f.surface,'annotationOverlay'),1);
    assert.equal(f.surface.windows.annotationOverlay,window);assert.equal(f.surface.draft.capture,frozen);
    assert.equal(f.surface.get().draft.comment,'保留同一页意见');assert.equal(f.events.filter(v=>v[0]==='capture').length,1);
    assert.equal(f.events.some(v=>v[0]==='send'||v[0]==='expand'),false);
  }finally{f.surface.dispose();}
});

test('lazy overlay concurrent requests share one HTML load and cannot acknowledge decode before that role is ready',async()=>{
  const load=deferred(),f=fixture({BrowserWindow:delayedOverlay(load)});try{
    await f.surface.openAsk();const first=f.command('annotate');await tick();
    assert.equal(f.surface.get().overlayLoading,true);assert.equal(f.surface.get().overlayPreparing,false);
    assert.equal(f.surface.get().visible,true);assert.equal(f.surface.get().roleLoaded.annotationOverlay,false);
    await assert.rejects(f.send({kind:'preview-ready',epoch:f.surface.get().epoch},'annotationOverlay'),/Stale or hidden/);
    const newer=await f.command('annotate');assert.equal(newer.overlayLoading,true);assert.equal(roleLoads(f.surface,'annotationOverlay'),1);
    load.resolve();await first;await tick();
    assert.equal(f.surface.get().overlayVisible,true);assert.equal(f.surface.get().roleLoaded.annotationOverlay,true);
    assert.equal(f.surface.windows.annotationOverlay.calls.filter(v=>v[0]==='inactive').length,1);
    assert.equal(f.surface.windows.annotationOverlay.calls.some(v=>v[0]==='show'),false);
  }finally{f.surface.dispose();}
});

test('lazy overlay loading never blocks a newer ask and late completion cannot replace that current question',async()=>{
  const load=deferred(),f=fixture({BrowserWindow:delayedOverlay(load)});try{
    await f.surface.openAsk();await f.edit('当前问题不能被迟到批注遮挡');const draft=f.surface.draft;
    const first=f.command('annotate');await tick();await f.surface.openAsk();const epoch=f.surface.get().epoch;
    assert.equal(f.surface.get().visible,true);load.resolve();assert.equal((await first).status,'STALE');await tick();
    assert.equal(f.surface.get().epoch,epoch);assert.equal(f.surface.get().overlayVisible,false);assert.equal(f.surface.get().visible,true);
    assert.equal(f.surface.draft,draft);assert.equal(draft.comment,'当前问题不能被迟到批注遮挡');
    assert.equal(f.surface.windows.annotationOverlay.calls.some(v=>v[0]==='inactive'),false);
    await f.command('annotate');assert.equal(f.surface.get().overlayVisible,true);assert.equal(roleLoads(f.surface,'annotationOverlay'),1);
  }finally{f.surface.dispose();}
});

test('lazy overlay close invalidates presentation before a pending draft save and preserves the saved text',async()=>{
  const load=deferred(),save=deferred(),f=fixture({BrowserWindow:delayedOverlay(load),onDraft:()=>save.promise});try{
    await f.surface.openAsk();await f.edit('先保存这条文字');const opening=f.command('annotate');await tick();
    const closing=f.surface.hide({animate:false});await tick();load.resolve();assert.equal((await opening).status,'STALE');
    assert.equal(f.surface.windows.annotationOverlay.calls.some(v=>v[0]==='inactive'),false);
    save.resolve({id:f.surface.draft.id,revision:1,sha256:sha,status:'EDITING'});await closing;
    assert.equal(f.surface.get().draft.comment,'先保存这条文字');assert.equal(f.surface.get().draft.revision,1);
    assert.equal(f.surface.get().visible,false);assert.equal(f.surface.get().overlayVisible,false);
  }finally{f.surface.dispose();}
});

test('lazy overlay disposal or host closing during load cannot publish ready or show destroyed windows',async()=>{
  for(const closingOnly of [false,true]){
    const load=deferred();let closing=false;const f=fixture({BrowserWindow:delayedOverlay(load),isClosing:()=>closing});
    try{
      await f.surface.openAsk();const opening=f.command('annotate');await tick();
      if(closingOnly)closing=true;else f.surface.dispose();load.resolve();assert.equal((await opening).status,'STALE');await tick();
      assert.equal(f.surface.get().roleLoaded.annotationOverlay,false);
      assert.equal(f.surface.windows.annotationOverlay.calls.some(v=>v[0]==='inactive'||v[0]==='show'),false);
    }finally{f.surface.dispose();}
  }
});

test('lazy overlay failed HTML load is not ready or automatically retried and ordinary questions retain the same draft',async()=>{
  class FailedOverlay extends OwnWindow{loadURL(url){super.loadURL(url);return this.options.hasShadow?Promise.resolve():Promise.reject(new Error('controlled HTML load failure'));}}
  const f=fixture({BrowserWindow:FailedOverlay});try{
    await f.surface.openAsk();await f.edit('失败后仍可继续这条问题');const draft=f.surface.draft;
    await f.command('annotate');assert.equal(f.surface.get().loaded,true);assert.equal(f.surface.get().visible,true);
    assert.equal(f.surface.get().roleLoaded.annotationOverlay,false);assert.equal(f.surface.get().annotationAvailable,false);
    assert.equal(f.surface.get().overlayLoading,false);assert.equal(f.surface.get().overlayVisible,false);assert.ok(f.surface.get().error);
    await f.command('annotate');await f.surface.openAsk();assert.equal(roleLoads(f.surface,'annotationOverlay'),1);
    assert.equal(f.surface.draft,draft);assert.equal(f.surface.get().draft.comment,'失败后仍可继续这条问题');
    assert.equal(f.events.some(v=>v[0]==='send'||v[0]==='expand'),false);
  }finally{f.surface.dispose();}
});

test('lazy overlay geometry or display invalidation does not wake or expose an unready annotation page',async()=>{
  const f=fixture({onCapture:async()=>capture({geometry:null})});try{
    await f.surface.beginAnnotation();assert.equal(roleLoads(f.surface,'annotationOverlay'),0);
    assert.equal(f.surface.get().visible,true);assert.equal(f.surface.get().overlayVisible,false);
  }finally{f.surface.dispose();}
  const load=deferred(),g=fixture({BrowserWindow:delayedOverlay(load)});try{
    const opening=g.surface.beginAnnotation();await tick();assert.equal(g.surface.get().overlayLoading,true);
    g.screen.emit('display-metrics-changed');load.resolve();await opening;await tick();
    assert.equal(g.surface.get().overlayVisible,false);assert.equal(g.surface.get().visible,true);assert.equal(g.surface.draft.geometryInvalidated,true);
    assert.equal(g.surface.windows.annotationOverlay.calls.some(v=>v[0]==='inactive'),false);
  }finally{g.surface.dispose();}
});


function latestIntentActions(f,getTaskActivity,isClosing=()=>false){
  const events=[];return {events,act:createAssistantOrbActions({getQuickSurface:()=>f.surface,getTaskActivity,isClosing,
    getCompanion:()=>({setMode:async mode=>{events.push(mode);return {mode};}}),
    getWindow:()=>({webContents:{send(){}}}),reveal:()=>events.push('reveal'),logFailure:error=>events.push(error.message)})};
}
for(const next of ['ask','annotate'])test('latest explicit intent with actual actions and QuickSurface fences delayed task GET: '+next,async()=>{
  const gate=deferred(),f=fixture(),a=latestIntentActions(f,()=>gate.promise);let pending;
  const open=f.surface.openActivity.bind(f.surface);f.surface.openActivity=(...args)=>pending=open(...args);
  try{
    await f.surface.ready;await a.act('tasks');await a.act(next);await tick();const before=f.surface.get();
    assert.equal(before.presentation,'ASK');assert.equal(next==='annotate'?before.overlayVisible:before.visible,true);
    gate.resolve(activitySnapshot());await tick();if(pending)await pending;
    const state=f.surface.get();assert.equal(state.presentation,'ASK');assert.equal(state.draft.id,before.draft.id);
    assert.equal(next==='annotate'?state.overlayVisible:state.visible,true);assert.equal(pending,undefined);
    assert.equal(f.events.some(row=>row[0]==='send'||row[0]==='expand'),false);
  }finally{f.surface.dispose();}
});
for(const next of ['ask','annotate'])test('latest explicit intent fences task presentation waiting for Quick readiness: '+next,async()=>{
  const gate=deferred(),f=fixture();try{
    await f.surface.ready;f.surface.ready=gate.promise;
    const pending=f.surface.openActivity(activitySnapshot()),newer=next==='ask'?f.surface.openAsk():f.surface.beginAnnotation();
    gate.resolve();assert.equal((await pending).status,'STALE');await newer;await tick();
    assert.equal(f.surface.get().presentation,'ASK');assert.equal(next==='annotate'?f.surface.get().overlayVisible:f.surface.get().visible,true);
  }finally{f.surface.dispose();}
});
for(const next of ['ask','annotate'])test('latest explicit intent fences task presentation during actual draft persistence: '+next,async()=>{
  const gate=deferred();let writes=0;
  const f=fixture({onDraft:async value=>{writes++;await gate.promise;return {id:value.draftId,revision:value.expectedRevision+1,sha256:sha,status:value.delivery};}});
  try{
    await f.surface.openAsk();await f.edit('保存期间必须保留的原意见');
    const draftId=f.surface.get().draft.id,pending=f.surface.openActivity(activitySnapshot());await tick();assert.equal(writes,1);
    await (next==='ask'?f.surface.openAsk():f.surface.beginAnnotation());gate.resolve();assert.equal((await pending).status,'STALE');
    assert.equal(f.surface.get().presentation,'ASK');assert.equal(f.surface.get().visible,true);
    assert.equal(f.surface.get().draft.id,draftId);assert.equal(f.surface.get().draft.comment,'保存期间必须保留的原意见');
    assert.equal(f.surface.get().draft.revision,1);assert.equal(f.surface.get().draft.phase,'EDITING');assert.equal(writes,1);
    assert.equal(f.events.some(row=>row[0]==='send'||row[0]==='expand'),false);
  }finally{f.surface.dispose();}
});
for(const next of ['ask','annotate','workbench','show-agent','closing'])test('latest explicit intent fences actual task hide animation: '+next,async()=>{
  let closing=false;const f=fixture({isClosing:()=>closing}),a=latestIntentActions(f,async()=>activitySnapshot(),()=>closing);let pending;
  const open=f.surface.openActivity.bind(f.surface);f.surface.openActivity=(...args)=>pending=open(...args);
  try{
    await f.surface.openAsk();const id=f.surface.get().draft.id;await a.act('tasks');await tick();assert.equal(f.surface.get().motion.phase,'CLOSE');
    if(next==='closing')closing=true;else await a.act(next);
    await pending;await tick();const state=f.surface.get();assert.equal(state.presentation,'ASK');assert.equal(state.draft.id,id);
    if(next!=='closing'){assert.equal(state.motion.phase,'OPEN');assert.equal(next==='annotate'?state.overlayVisible:state.visible,true);}
    if(next==='workbench'||next==='show-agent')assert.equal(a.events.includes('reveal'),true);
    assert.equal(f.events.some(row=>row[0]==='send'||row[0]==='expand'),false);
  }finally{f.surface.dispose();}
});


test('a display change while current-page capture is pending keeps that frozen source but blocks its old geometry until explicit reread',async t=>{
  for(const event of ['display-metrics-changed','display-removed'])for(const kind of ['ask','annotate'])await t.test(`${event} / ${kind}`,async()=>{
    const pending=deferred(),started=deferred(),oldCapture=capture();let captures=0;
    const f=fixture({onCapture:async()=>{captures++;started.resolve();return pending.promise;}});
    try{
      const opening=kind==='annotate'?f.surface.beginAnnotation():f.surface.openAsk();
      await started.promise;f.screen.emit(event);pending.resolve(oldCapture);await opening;await tick();
      assert.equal(f.surface.get().capturePending,false);
      assert.equal(f.surface.get().source.available,true,'the exact frozen observation remains readable');
      assert.equal(f.surface.draft.capture,oldCapture);
      assert.equal(f.surface.get().annotationAvailable,false,'late capture cannot revive pre-change screen geometry');
      assert.equal(f.surface.get().overlayVisible,false);assert.equal(f.surface.get().visible,true);
      assert.match(f.surface.get().error,/显示器已变化/);
      assert.equal(f.surface.roleLoads.annotationOverlay,undefined,'invalidated capture does not wake the overlay');
      assert.equal(captures,1);assert.equal(f.events.some(row=>['send','expand','draft'].includes(row[0])),false);
    }finally{pending.resolve(oldCapture);f.surface.dispose();}
  });
});

test('an invalidated captured page preserves text on failed save and only a successful explicit reread restores annotation',async()=>{
  const pending=deferred(),started=deferred(),oldCapture=capture(),newCapture=capture({snapshot:{...capture().snapshot,operation_id:'new_exact_capture'}});
  let captures=0,rejectSave=true,saves=0;
  const f=fixture({onCapture:async()=>{captures++;if(captures===1){started.resolve();return pending.promise;}return newCapture;},
    onDraft:async value=>{saves++;if(rejectSave)throw new Error('own fixture disk unavailable');return {id:value.draftId,revision:value.expectedRevision+1,sha256:sha,status:value.delivery};}});
  try{
    const opening=f.surface.beginAnnotation();await started.promise;f.screen.emit('display-removed');pending.resolve(oldCapture);await opening;await tick();
    await f.edit('保留这段已写的意见');const originalId=f.surface.get().draft.id;
    assert.equal((await f.command('recapture')).status,'ACTION_FAILED');
    assert.equal(captures,1);assert.equal(f.surface.get().draft.id,originalId);assert.equal(f.surface.get().draft.comment,'保留这段已写的意见');
    assert.equal(f.surface.draft.capture,oldCapture);assert.equal(f.surface.get().annotationAvailable,false);assert.equal(f.surface.get().visible,true);
    assert.equal(f.surface.get().overlayVisible,false);assert.match(f.surface.get().error,/保存未完成/);
    rejectSave=false;await f.command('recapture');await tick();
    assert.equal(captures,2,'only the explicit retry captures a new page');assert.equal(saves,2);
    assert.equal(f.surface.get().draft.id,originalId);assert.equal(f.surface.get().draft.comment,'保留这段已写的意见');
    assert.equal(f.surface.draft.capture,newCapture);assert.equal(f.surface.get().annotationAvailable,true);assert.equal(f.surface.get().overlayVisible,true);
    assert.equal(f.events.some(row=>['send','expand'].includes(row[0])),false);
  }finally{pending.resolve(oldCapture);f.surface.dispose();}
});

test('a late invalidated capture cannot replace a newer user intent captured after the display change',async()=>{
  const first=deferred(),firstStarted=deferred(),second=deferred(),secondStarted=deferred();let captures=0;
  const older=capture(),newer=capture({snapshot:{...capture().snapshot,operation_id:'newer_ask_capture'}});
  const f=fixture({onCapture:async()=>{if(++captures===1){firstStarted.resolve();return first.promise;}secondStarted.resolve();return second.promise;}});
  try{
    const oldOpening=f.surface.beginAnnotation();await firstStarted.promise;f.screen.emit('display-metrics-changed');
    const newOpening=f.surface.openAsk();await secondStarted.promise;second.resolve(newer);await newOpening;
    first.resolve(older);assert.equal((await oldOpening).status,'STALE');await tick();
    assert.equal(f.surface.get().kind,'ask');assert.equal(f.surface.draft.capture,newer);
    assert.equal(f.surface.get().annotationAvailable,true,'a new stable capture uses the new geometry version');
    assert.equal(f.surface.get().overlayVisible,false);assert.equal(f.surface.get().visible,true);assert.equal(f.surface.get().error,'');
    assert.equal(captures,2);assert.equal(f.events.some(row=>['send','expand'].includes(row[0])),false);
  }finally{first.resolve(older);second.resolve(newer);f.surface.dispose();}
});


test('actual preload long filename cards keep their original artifact indices and callback identities after scrolling-independent state refresh',async()=>{
  const artifacts=Array.from({length:8},(_,i)=>({kind:'docx_revision',id:String(i).repeat(64),project_id:'qa_project',filename:`原创QA长文稿-${i}-${'original-result-'.repeat(12)}.docx`}));
  const f=fixture({onSend:async body=>({operation_id:body.operationId,status:'RETURNED',text:'原任务回复',artifacts})});
  try{
    await f.surface.openAsk();await f.edit('打开这批原任务产物');await f.command('send');await f.surface.openAsk();const renderer=preloadBridge(f,'popover');await tick();
    for(const index of [0,7,3]){
      const button=renderer.elements.get('#files').children[index];assert.equal(button.textContent,artifacts[index].filename);
      button.listeners.click();await tick();assert.equal(f.events.filter(row=>row[0]==='open').at(-1)[1],artifacts[index]);
      f.surface.setLocale(index===7?'en':'zh-CN');
    }
    assert.deepEqual(f.events.filter(row=>row[0]==='open').map(row=>row[1].id),[artifacts[0].id,artifacts[7].id,artifacts[3].id]);
    assert.equal(f.events.filter(row=>row[0]==='capture').length,1);assert.equal(f.events.some(row=>row[0]==='expand'),false);
  }finally{f.surface.dispose();}
});

function savedReadonly(overrides={}){return {id:'saved_send',revision:3,comment:'原问题',marks:[],capture:{snapshot:{current:false,observation:{status:'UNAVAILABLE'}},restored:true},restored:true,readonly:true,operationId:'original_completed',message:{operation_id:'original_completed',status:'RETURNED',text:'## 保存的完整回复\n\n最后一行'},...overrides};}
test('signed SEND history waits for latest renderer input, preserves the original draft object and never replays authority',async()=>{
 const f=fixture({onLoadDraft:async args=>args.draftRef?savedReadonly():undefined});
 try{await f.surface.openAsk();await f.edit('旧输入');const original=f.surface.draft,cap=original.capture;
  const r=preloadBridge(f,'popover');await tick();const input=r.elements.get('#comment');input.value='最后输入尚未触发 input event';input.selectionStart=2;input.selectionEnd=6;input.scrollTop=19;r.elements.get('#comment').focus();
  const shown=await f.surface.openAsk({draftRef:{id:'saved_send',revision:3,sha256:sha}});
  assert.equal(shown.draft.readonly,true);assert.equal(shown.draft.phase,'COMPLETED');assert.equal(shown.result.text,'## 保存的完整回复\n\n最后一行');
  assert.equal(original.comment,'最后输入尚未触发 input event');assert.equal(f.events.filter(row=>row[0]==='draft').at(-1)[1].comment,original.comment);
  assert.equal(f.events.filter(row=>['send','inspect','retain'].includes(row[0])).length,0);assert.equal(f.events.filter(row=>row[0]==='capture').length,1);
  for(const command of ['send','draft','stop','review-catalog','review-clear','review-guidance','review-continue','recapture','file-clear'])assert.equal((await f.command(command)).status,'ACTION_FAILED',command);
  assert.equal((await f.send({kind:'review-control',epoch:f.surface.epoch,action:'once',requestId:'old-grant',answers:null})).status,'ACTION_FAILED');
  await f.command('new-question');assert.equal(f.surface.draft,original);assert.equal(original.capture,cap);assert.equal(input.value,original.comment);assert.equal(input.scrollTop,19);assert.equal(f.events.filter(row=>row[0]==='capture').length,1);
  await f.surface.openAsk({draftRef:{id:'saved_send',revision:3,sha256:sha}});await f.surface.hide({animate:false});assert.equal(f.surface.draft,original);assert.equal(f.surface.get().visible,false);
  await f.surface.openAsk();assert.equal(f.surface.draft,original);assert.equal(f.surface.get().draft.comment,'最后输入尚未触发 input event');
 }finally{f.surface.dispose();}
});
test('history handoff refuses active composition and keeps the exact visible draft without loading history',async()=>{
 let loads=0;const f=fixture({onLoadDraft:async args=>{loads++;return args.draftRef?savedReadonly():undefined;}});
 try{await f.surface.openAsk();const original=f.surface.draft,r=preloadBridge(f,'popover');await tick();const input=r.elements.get('#comment');input.value='正在输入中文';input.listeners.compositionstart();
  await assert.rejects(f.surface.openAsk({draftRef:{id:'saved_send',revision:3,sha256:sha}}),/完成当前输入/);assert.equal(f.surface.draft,original);assert.equal(loads,1,'only initial EDITING restoration was attempted');assert.equal(f.surface.get().visible,true);assert.equal(input.disabled,false);
  input.listeners.compositionend();await tick();assert.equal(original.comment,'正在输入中文');
 }finally{f.surface.dispose();}
});
test('scope changes, concurrent opens and close during delayed history load cannot replace the preserved draft',async()=>{
 const wait=deferred();let current=true,loading=false;const f=fixture({onLoadDraft:async args=>{if(args.draftRef){loading=true;return wait.promise;}}});
 try{await f.surface.openAsk();await f.edit('保留当前范围与输入');const original=f.surface.draft,epoch=f.surface.epoch;
  const opening=f.surface.openAsk({draftRef:{id:'saved_send',revision:3,sha256:sha},isCurrent:()=>current});await tick();assert.equal(loading,true);
  assert.equal((await f.surface.openAsk({draftRef:{id:'other_send',revision:3,sha256:sha}})).status,'BUSY');await assert.rejects(f.surface.hide(),/正在切换/);
  current=false;wait.resolve(savedReadonly());assert.equal((await opening).status,'STALE');assert.equal(f.surface.draft,original);assert.equal(f.surface.epoch,epoch);assert.equal(original.comment,'保留当前范围与输入');
 }finally{f.surface.dispose();}
});
test('history shows pending as pending; explicit inspect rereads durable projection without replaying old controls',async()=>{
 const pending={operation_id:'original_completed',status:'RUNNING',review:{can_guide:true,pending_permissions:[{request_ref:{id:'old',revision:1,sha256:sha}}]}};
 const f=fixture({onLoadDraft:async args=>args.draftRef?savedReadonly({message:pending}):undefined,onInspect:async()=>({operation_id:'original_completed',status:'RETURNED',text:'memory-only',local_persistence_status:'FAILED'}),onLoadReadonlyResult:()=>pending});
 try{await f.surface.openAsk({draftRef:{id:'saved_send',revision:3,sha256:sha}});assert.equal(f.surface.get().draft.phase,'RUNNING');assert.equal(f.surface.get().canStop,false);
  const before=f.surface.get().result;assert.equal(f.surface.publishResult({operation_id:'original_completed',status:'RETURNED',text:'late cache'}).status,'STALE');assert.deepEqual(f.surface.get().result,before);
  await f.command('inspect');assert.equal(f.surface.get().draft.phase,'RUNNING');assert.equal(f.surface.get().result.text,'');assert.match(f.surface.get().notice,/保存未成功/);
  assert.equal(f.events.filter(row=>['send','retain'].includes(row[0])).length,0);
 }finally{f.surface.dispose();}
});
test('history cannot interrupt the exact once send persistence transition or create a second owner',async()=>{
 const saving=deferred();let loads=0;const f=fixture({onDraft:()=>saving.promise,onLoadDraft:async args=>{if(args.draftRef){loads++;return savedReadonly();}}});
 try{await f.surface.openAsk();await f.edit('一次发送的原问题');const draft=f.surface.draft;const sending=f.command('send');await tick();
  assert.equal(draft.phase,'SAVING');assert.equal((await f.surface.openAsk({draftRef:{id:'saved_send',revision:3,sha256:sha}})).status,'BUSY');assert.equal(loads,0);
  saving.resolve({id:draft.id,revision:1,sha256:sha,status:'EDITING'});await sending;assert.equal(f.events.filter(row=>row[0]==='send').length,1);assert.equal(f.surface.draft,draft);
 }finally{f.surface.dispose();}
});

test('activity focuses the shared inline coordinator without opening Main or capturing a page',async()=>{
  let opened=0;const f=fixture({onOpenSupervisor:()=>{opened++;},onGetSupervisorConversation:()=>({}),onSaveSupervisorDraft:()=>({}),onAskSupervisor:()=>({}),onInspectSupervisor:()=>({}),onSaveSupervisorLocalAnswer:()=>({})});
  try{
    await f.surface.openActivity(activitySnapshot());assert.equal(opened,0);assert.equal(f.surface.get().activity.can_ask,true);
    const result=await f.command('activity-supervisor');assert.equal(result.status,'SUPERVISOR_INLINE');assert.equal(opened,0);
    assert.equal(f.surface.get().visible,true);assert.equal(f.events.some(row=>['capture','send','expand'].includes(row[0])),false);
  }finally{f.surface.dispose();}
});


test('saved local status is labelled as historical rather than currently idle in both languages',async()=>{
 for(const locale of ['zh-CN','en']){
  const f=fixture({locale,onReviewControl:async value=>({operation_id:value.body.operation_id,status:'RETURNED',role:'LOCAL_STATUS',local_status:{active_operation_id:null,observation_is_current:false}})});
  try{await f.surface.ready;await f.surface.openAsk();await f.edit('原问题');await f.command('send');await f.surface.openAsk();const parent=f.surface.draft.operationId;
   f.surface.publishResult({operation_id:parent,status:'RETURNED',canonical_original:true,canonical_message_ref:{id:parent,revision:1,sha256:sha},control_capabilities:{can_guide:true,can_status:true}});
   await f.send({kind:'review-guidance-edit',epoch:f.surface.epoch,text:'进度'});await f.command('send');
   assert.equal(f.surface.get().notice,locale==='en'?'Last saved progress loaded':'已读取上次保存的进度');
  }finally{f.surface.dispose();}
 }
});
