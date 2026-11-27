'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const os=require('node:os');
const {EventEmitter}=require('node:events');
const {createRequire}=require('node:module');
const nativeCompanion=require('../native-companion.cjs');
const {NativeCompanion}=nativeCompanion;
const {DISPLAY_NAME,SHORT_NAME}=require('../product-brand.cjs');

function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
async function flush(){for(let index=0;index<32;index++)await Promise.resolve();}
async function settlePresentation(predicate){for(let turn=0;turn<32;turn++){if(predicate())return;await flush();}assert.ok(predicate(),'actual presentation did not settle within bounded promise turns');}
function clock(){
  let now=0,sequence=0;const timers=new Map();
  return {get now(){return now;},get pending(){return timers.size;},
    setTimeout(fn,delay){const id=++sequence;timers.set(id,{fn,at:now+delay});return id;},
    clearTimeout(id){timers.delete(id);},
    async advance(milliseconds){
      const target=now+milliseconds;await flush();
      while(true){
        let next;for(const [id,value]of timers)if(value.at<=target&&(!next||value.at<next[1].at))next=[id,value];
        if(!next)break;
        now=next[1].at;timers.delete(next[0]);next[1].fn();await flush();
      }
      now=target;await flush();
    },
  };
}

// Execute actual Main + NativeCompanion orchestration. Only OS/window/network
// boundaries and time are controlled; no Electron, controller, SDK or GUI runs.
function fixture(options={}){
  const time=clock(),events=[],requests=[],logs=[],errors=[],windows=[],trays=[],ipc=[],handlers=new Map(),shortcuts=new Map(),ideBridges=[],spawnCalls=[],appPaths=[];
  const dataDir=fs.mkdtempSync(path.join(os.tmpdir(),'eeas-main-startup-'));
  if(options.preferences)fs.writeFileSync(path.join(dataDir,'desktop-preferences.json'),JSON.stringify({schema:'eeas.desktop-preferences.v1',...options.preferences}));
  if(options.restored)fs.writeFileSync(path.join(dataDir,'restore-provenance.json'),'{}');
  const ready=deferred(),app=new EventEmitter(),child=new EventEmitter();
  let foreground='external-public-qa-page',spawns=0,zOrder=['external-public-qa-page'];
  Object.assign(child,{pid:19156,exitCode:null,signalCode:null,
    kill(signal){events.push('kill:'+signal);this.signalCode=signal;this.emit('exit',null,signal);return true;}});
  Object.assign(app,{setPath(key,value){appPaths.push({key,value});},getPath:()=>'/qa-d/downloads',requestSingleInstanceLock:()=>true,
    getLocale:()=>options.systemLocale??'zh-CN',whenReady:()=>ready.promise,quit:()=>events.push('app.quit')});
  class OwnWindow extends EventEmitter{
    constructor(configuration){super();this.configuration=configuration;this.urls=[];this.destroyed=false;this.minimized=false;this.visible=false;
    this.bounds={x:configuration.x??0,y:configuration.y??0,width:configuration.width,height:configuration.height};this.minimum=[configuration.minWidth,configuration.minHeight];
      this.pinned=configuration.alwaysOnTop===true;this.fullscreen=false;this.maximized=false;
      this.role=configuration.title===DISPLAY_NAME?'window':configuration.title===SHORT_NAME+' 快捷问答'?'quick':configuration.title===SHORT_NAME+' 页面批注'?'overlay':'orb';
      this.handle=88n+BigInt(windows.length);this.webContents=new EventEmitter();this.webContents.mainFrame={url:''};
      this.webContents.getURL=()=>this.webContents.mainFrame.url;this.webContents.setWindowOpenHandler=()=>{};
      this.webContents.send=(...args)=>{events.push([this.role==='window'?'send':this.role+'.send',...args]);
        if(args[0]==='eeas:flush-drafts')handlers.get('eeas:close-flush-complete')(
          {sender:this.webContents,senderFrame:this.webContents.mainFrame},{id:args[1].id,status:'SAVED'});};
      windows.push(this);events.push(this.role+'.create');}
    setBounds(bounds){this.bounds={...bounds};events.push(this.role==='window'?'own-window.position':this.role+'.position');}
    setShape(rects){this.shape=structuredClone(rects);events.push(this.role+'.shape');}
    getBounds(){return {...this.bounds};}getNormalBounds(){return {...this.bounds};}getMinimumSize(){return [...this.minimum];}
    setMinimumSize(width,height){this.minimum=[width,height];events.push('own-window.minimum');}
    setContentProtection(value){this.captureProtected=value;events.push('content-protection:'+value);}
    getNativeWindowHandle(){const value=Buffer.alloc(8);value.writeBigUInt64LE(this.handle);return value;}
    // Source55's target ACK waits for actual Electron completion events; keep
    // this controlled lifecycle explicit rather than relying on early flags.
    isFullScreen(){return this.fullscreen;}setFullScreen(value){this.fullscreen=value;events.push([this.role+'.fullscreen',value]);this.emit(value?'enter-full-screen':'leave-full-screen');}
    isMaximized(){return this.maximized;}maximize(){this.maximized=true;events.push(this.role+'.maximize');}unmaximize(){this.maximized=false;events.push(this.role+'.unmaximize');this.emit('unmaximize');}
    isAlwaysOnTop(){return this.pinned;}setAlwaysOnTop(value){
      this.pinned=value;events.push([this.role+'.pin',value]);
      // Root's real HWND_TOP succeeded without changing z-order. A TOPMOST /
      // NOTOPMOST pair leaves the own window above normal windows, unfocused.
      if(value)zOrder=[this,...zOrder.filter(item=>item!==this)];
      this.emit('always-on-top-changed',{},value);
      if(options.setAlwaysOnTop)options.setAlwaysOnTop(value,this);
    }
    isDestroyed(){return this.destroyed;}isMinimized(){return this.minimized;}
    isVisible(){return this.visible;}
    restore(){this.minimized=false;events.push('window.restore');this.emit('restore');}
    showInactive(){this.visible=true;events.push(this.role+'.showInactive');}
    moveTop(){events.push(this.role+'.moveTop');if(options.moveTop)options.moveTop(this);/* successful HWND_TOP can leave z-order unchanged */}
    show(){this.visible=true;events.push(this.role+'.show');foreground='own-eeas-'+this.role;}
    focus(){events.push(this.role+'.focus');foreground='own-eeas-'+this.role;}
    hide(){this.visible=false;events.push(this.role+'.hide');}
    destroy(){this.destroyed=true;events.push(this.role+'.destroy');this.emit('closed');}
    loadURL(url){this.urls.push(url);this.webContents.mainFrame.url=url;
      events.push(this.role==='window'?(url.startsWith('data:')?'load.local':'load.business'):this.role+'.load.local');return Promise.resolve();}
  }
  const reference={id:'own-session',revision:1,sha256:'a'.repeat(64)};
  const response=(body,headers={})=>({ok:true,headers:{get:key=>headers[key]??null},json:async()=>body});
  const fetch=async(url,init)=>{
    requests.push({url,init,at:time.now});
    if(url.endsWith('/api/bootstrap')){
      if(options.bootstrap)return options.bootstrap({url,init,time,response});
      if(time.now<(options.readyAt??0))return {ok:false};
      return response({csrf_token:'PRIVATE-CSRF-DO-NOT-LOG'},{'set-cookie':'private-cookie=DO-NOT-LOG; HttpOnly'});
    }
    const route=new URL(url).pathname;events.push(route);
    assert.ok(init.headers.Cookie);assert.ok(init.headers['X-EEAS-CSRF']);
    if(options.request){
      const result=await options.request({route,body:init.body?JSON.parse(init.body):undefined,response,time});
      if(result!==undefined)return result;
    }
    if(route.endsWith('/collaboration/bind'))return response({status:'ACTIVE'});
    if(route.endsWith('/collaboration/get'))return response({status:'ACTIVE',session_ref:reference});
    if(route.endsWith('/collaboration/presentation'))return response({status:'ACTIVE',session_ref:reference});
    if(route.endsWith('/context/status'))return response({configured:true});
    if(route.endsWith('/context/observe')){
      const body=JSON.parse(init.body);events.push('capture.foreground:'+foreground);
      assert.deepEqual(body.need,body.intent==='初始化当前原生页面身份'
        ?{metadata:true,accessibility:false,visual:false,ocr:false}
        :{metadata:true,accessibility:true,visual:true,ocr:false});
      if(options.observe)await options.observe({body,foreground});
      return response({operation_id:body.operation_id,automatic_input_authorized:false,
        observation:{status:'OBSERVED'},preview:{status:'UNAVAILABLE'}});
    }
    if(route.endsWith('/collaboration/close'))return options.close
      ? options.close(response):response({status:'CLOSED'});
    if(route==='/api/desktop-host/close')return response({status:'CLOSED'});
    if(route==='/api/desktop-host/native-messages')return response({operation_id:JSON.parse(init.body).operation_id,
      status:'RETURNED',text:'原创运输边界回执；未调用任何真实模型',...(options.artifacts?{artifacts:options.artifacts}:{})});
    if(route==='/api/shutdown'){
      child.exitCode=0;child.signalCode=null;child.emit('exit',0,null);
      return response({status:'stopping',owner_shutdown:{status:'CLOSED'}});
    }
    throw Error('Unexpected route: '+route);
  };
  const mainPath=path.resolve(__dirname,'../main.cjs'),actualRequire=createRequire(mainPath);
  const fakeFs={mkdirSync(){},existsSync:file=>path.basename(file)==='restore-provenance.json'?fs.existsSync(file):options.packaged!==false||path.basename(file)!=='PACKAGE_MANIFEST.json',openSync:()=>7,closeSync(){},
    appendFileSync(file,text){logs.push({file,text});}};
  class OwnTray extends EventEmitter{
    constructor(image){super();this.image=image;this.destroyed=false;trays.push(this);events.push('tray.create');}
    setToolTip(value){this.tooltip=value;}setContextMenu(value){this.menu=value;}
    destroy(){this.destroyed=true;events.push('tray.destroy');}
  }
  const electron={app,BrowserWindow:OwnWindow,Tray:OwnTray,...options.taskActivity?{Notification:class {static isSupported(){return false;}}}:{},Menu:{buildFromTemplate:items=>({items})},nativeImage:{createFromBuffer:bytes=>({isEmpty:()=>false,pngSize:[bytes.readUInt32BE(16),bytes.readUInt32BE(20)]})},dialog:{showErrorBox:(...args)=>errors.push(args),
    showMessageBox:async(...args)=>{events.push(['dialog.pair',...args]);return options.pairing?options.pairing(...args):{response:1};}},
    session:{defaultSession:{setPermissionRequestHandler(){},on(){}}},
    ipcMain:{handle(channel,handler){ipc.push(channel);handlers.set(channel,handler);},removeHandler:channel=>handlers.delete(channel)},
    shell:{async openPath(filename){events.push(['shell.openPath',filename]);return options.openPath?options.openPath(filename):'';}},
    screen:{getCursorScreenPoint:()=>({x:0,y:0}),getAllDisplays:()=>options.displays??[{workArea:{x:0,y:0,width:1920,height:1080}}],getDisplayNearestPoint:()=>({workArea:{x:0,y:0,width:1920,height:1080}})},
    globalShortcut:{register:(key,handler)=>{shortcuts.set(key,handler);events.push('shortcut.register');},unregisterAll:()=>events.push('shortcut.unregister')}};
  const context=vm.createContext({
    require(id){
      if(id==='electron')return electron;if(id==='node:fs')return fakeFs;
      if(id==='node:path'&&options.nativeWindowsPaths)return path.win32;
      if(id==='node:child_process')return {spawn(command,args,options){spawns++;spawnCalls.push({command,args:[...args],options});events.push('controller.spawn');return child;}};
      if(id==='./controller-port.cjs')return {controllerPort:()=>options.port??Promise.resolve(5017)};
      if(id==='./renderer-storage-recovery.cjs'&&options.rendererRecovery)return {recoverRendererStorage:options.rendererRecovery};
      if(id==='./ide-companion-bridge.cjs')return {IdeCompanionBridge:class {
        constructor(args){this.args=args;this.closed=false;ideBridges.push(this);events.push('ide.create');}
        async start(){events.push('ide.start');if(options.ideStartError)throw options.ideStartError;return {protocol:'eeas.ide-companion.v1'};}
        async close(){this.closed=true;events.push('ide.close');return {status:'CLOSED',external_tasks_cancelled:false};}
      }};
      if(id==='./ide-api-adapter.cjs')return {createIdeApiAdapter(args){events.push('ide.adapter');return async(method,params,meta)=>
        options.ideDispatch?options.ideDispatch({method,params,meta,args}):{status:'UNIT_NOT_BUSINESS_QUALIFIED'};}};
      if(id==='./assistant-orb.cjs'&&options.actualOrb){
        const original=actualRequire(id);
        return {...original,AssistantOrb:class extends original.AssistantOrb{
          constructor(args){super({...args,clock:{now:()=>time.now,setTimeout:time.setTimeout,clearTimeout:time.clearTimeout}});}
        }};
      }
      if(id==='./assistant-orb.cjs')return {AssistantOrb:class {
        constructor(){this.ready=Promise.resolve();this.window=new OwnWindow({title:SHORT_NAME+' 快捷助手',width:72,height:72,show:false});
          this.enabled=true;this.visible=false;events.push('orb.create');}
        async setEnabled(value){this.enabled=value;return this.get();}
        async setHostMinimized(value){this.visible=value&&this.enabled;events.push('orb.minimized:'+value);return this.get();}
        getAnchor(){return {x:52,y:808,orbBounds:{x:24,y:780,width:56,height:56}};}
        get(){return {enabled:this.enabled,visible:this.visible};}
        setSurfaceActive(value){this.surfaceActive=value;}
        setFeedback(value){this.feedback=value;}
        setTaskActivity(value){this.taskActivity=value;}
        dispose(){this.window.destroy();events.push('orb.dispose');}
      }};
      if(id==='./native-companion.cjs')return {...nativeCompanion,NativeCompanion:class extends NativeCompanion{
        constructor(args){super({...args,platform:'win32',ownerPid:21492});events.push('companion.create');}}};
      if(id==='./native-append-activation.cjs')return {createNativeAppendActivation:()=>Object.assign(()=>{}, {cancelPending(){}})};
      return actualRequire(id);
    },
    __dirname:path.dirname(mainPath),process:{platform:'win32',pid:21492,argv:options.argv??[],env:{EEAS_DATA_DIR:dataDir,...options.environment}},
    Buffer,URL,URLSearchParams,AbortController,AbortSignal,fetch,console,
    Date:class extends Date{static now(){return time.now;}},
    setTimeout:time.setTimeout,clearTimeout:time.clearTimeout,
  });
  vm.runInContext(fs.readFileSync(mainPath,'utf8')+`
    globalThis.startupHarness={boot:()=>startupPromise,state:()=>({quitting,stopped,startupCancelled,
      hasCompanion:!!companion,hasCookie:!!cookie,hasCsrf:!!csrf}),quick:()=>quickSurface,orb:()=>assistantOrb,activity:()=>taskActivity,
      fileContext:projectId=>quickHost.scope.projectId===projectId?quickJournal.fileContext(quickHost.scope):undefined,quitSafely};`,context,{filename:mainPath});
  ready.resolve();
  return {...context.startupHarness,time,events,requests,logs,errors,windows,trays,child,app,ipc,shortcuts,ideBridges,dataDir,spawnCalls,appPaths,
    secondInstance(argv){return app.listeners('second-instance')[0]({},argv);},
    invoke(channel,...args){const own=windows[0],handler=handlers.get(channel);assert.ok(handler,'actual Main IPC handler exists: '+channel);
      return handler({sender:own.webContents,senderFrame:own.webContents.mainFrame},...args);},
    trayAction(label){const item=trays[0].menu.items.find(item=>item.label===label);assert.ok(item);return item.click();},
    quickEvent(payload){const own=windows.find(value=>value.role==='quick');return handlers.get('eeas:quick-surface:event')(
      {sender:own.webContents,senderFrame:own.webContents.mainFrame},{epoch:context.startupHarness.quick().get().epoch,...payload});},
    orbEvent(payload){const own=context.startupHarness.orb().window;return handlers.get('eeas:assistant-orb:event')(
      {sender:own.webContents,senderFrame:own.webContents.mainFrame},payload);},
    setForeground(value){foreground=value;zOrder=[value,...zOrder.filter(item=>item!==value)];},
    get foreground(){return foreground;},get topWindow(){return zOrder[0];},
    get spawns(){return spawns;},stages:()=>logs.filter(row=>row.text.startsWith('{')).map(row=>JSON.parse(row.text)),
    closeWindow(){let prevented=false;windows[0].emit('close',{preventDefault(){prevented=true;}});return prevented;}};
}

test('restored page-state recovery finishes before business renderer, Quick surfaces or IDE become available',{timeout:5000},async()=>{
  const entered=deferred(),release=deferred(),calls=[];
  const value=fixture({restored:true,rendererRecovery:async args=>{calls.push(args);entered.resolve();await release.promise;return {status:'RESTORED',keys:2};}});
  await entered.promise;
  assert.equal(value.events.includes('load.business'),false);assert.equal(value.ideBridges.length,0);assert.equal(value.quick(),undefined);
  assert.equal(calls[0].dataDir,value.dataDir);assert.equal(calls[0].origin,'http://127.0.0.1:5017');assert.equal(calls[0].signal.aborted,false);
  release.resolve();await value.boot();assert.equal(calls.length,1);assert.equal(value.events.filter(item=>item==='load.business').length,1);await value.quitSafely();
});

test('failed restored page-state recovery blocks an empty business page and ends its own startup safely',{timeout:5000},async()=>{
  const value=fixture({restored:true,rendererRecovery:async()=>{throw Error('RESTORE_DRAFT_CONFLICT');}});await value.boot();
  assert.equal(value.events.includes('load.business'),false);assert.equal(value.ideBridges.length,0);assert.equal(value.quick(),undefined);
  assert.equal(value.errors.length,1);assert.match(value.errors[0][1],/原数据已保留/);assert.equal(value.child.exitCode,0);
  assert.equal(value.requests.some(item=>/native-messages|agent-sessions.*\/open/.test(item.url)),false);
});

test('capability read IPC uses private GETs and rejects arbitrary MCP references before requesting',async()=>{
  const value=fixture({request:({route,response})=>{
    if(route.endsWith('/skills/import'))return {...response({detail:'Exact version conflict'}),ok:false,status:409};
    if(route.endsWith('/mcp/configure'))throw Error('Controlled transport loss');
    return route.startsWith('/api/desktop-host/capabilities/')?response({read:true}):undefined;
  }});
  await value.boot();const before=value.requests.length,ref={id:'review-local',revision:2,sha256:'a'.repeat(64)},scope={project_id:'project',session_ref:{id:'b'.repeat(32),revision:1,sha256:'b'.repeat(64)},generation:1};
  try{
    await value.invoke('eeas:agent-mcp-read',ref.id,ref);await value.invoke('eeas:agent-tools-read',scope);await value.invoke('eeas:agent-capability-operation','save-once');
    const requests=value.requests.slice(before);assert.equal(requests.length,3);
    assert.equal(new URL(requests[0].url).searchParams.get('sha256'),ref.sha256);
    assert.deepEqual(JSON.parse(new URL(requests[1].url).searchParams.get('scope')),scope);
    assert.equal(new URL(requests[2].url).pathname,'/api/desktop-host/capabilities/operations/save-once');
    assert.ok(requests.every(item=>!item.init.body&&item.init.headers['X-EEAS-Desktop-Host']));
    for(const args of [['../other',ref],[ref.id,{...ref,id:'other'}],[ref.id,{...ref,revision:0}],[ref.id,{...ref,path:'D:/private'}]])await assert.rejects(()=>value.invoke('eeas:agent-mcp-read',...args));
    assert.equal(value.requests.length,before+3);
    const rejection=await value.invoke('eeas:agent-skill-import',{operation_id:'explicit-save'});
    assert.equal(rejection.schema,'eeas.capability-rejection.v1');assert.equal(rejection.confirmed_rejected,true);assert.equal(rejection.status,409);
    await assert.rejects(()=>value.invoke('eeas:agent-mcp-configure',{operation_id:'unknown-save'}),/Controlled transport loss/);
  }finally{await value.quitSafely();fs.rmSync(value.dataDir,{recursive:true,force:true});}
});

test('controller disables bytecode at Python startup before either packaged bootstrap or development module',async()=>{
  for(const packaged of [true,false]){
    const value=fixture({packaged});await value.boot();assert.equal(value.spawnCalls.length,1);
    const args=value.spawnCalls[0].args;
    assert.equal(args[0],'-B');
    if(packaged){assert.equal(path.basename(args[1]),'native_package_bootstrap.py');assert.deepEqual(args.slice(2),['--api']);}
    else assert.deepEqual(args,['-B','-m','apps.api','serve']);
    await value.quitSafely();fs.rmSync(value.dataDir,{recursive:true,force:true});
  }
});

test('first actual Main API spawn isolates all profile fields and preserves four Electron data paths',async()=>{
  const dataDir='D:\\Cirven Native Profile Boundary\\private-data';
  const inherited={EEAS_DATA_DIR:dataDir,EEAS_PYTHON:'D:\\Runtime\\python.exe',
    HOME:'C:\\Users\\Original',USERPROFILE:'C:\\Users\\Original',
    APPDATA:'C:\\Users\\Original\\AppData\\Roaming',LOCALAPPDATA:'C:\\Users\\Original\\AppData\\Local',
    HOMEDRIVE:'C:',HOMEPATH:'\\Users\\Original',XDG_CONFIG_HOME:'C:\\Old Config',XDG_DATA_HOME:'C:\\Old Data',
    PATHEXT:'.COM;.EXE;.BAT;.CMD',OPENAI_API_KEY:'DUMMY_NOT_A_CREDENTIAL',
    ELECTRON_RUN_AS_NODE:'1',electron_run_as_node:'1'};
  const value=fixture({nativeWindowsPaths:true,environment:inherited});
  try{
    await value.boot();assert.equal(value.spawnCalls.length,1);
    const call=value.spawnCalls[0],env=call.options.env,home=path.win32.join(dataDir,'home');
    assert.equal(env.HOME,home);assert.equal(env.USERPROFILE,home);
    assert.equal(env.APPDATA,path.win32.join(dataDir,'appdata'));
    assert.equal(env.LOCALAPPDATA,path.win32.join(dataDir,'local-appdata'));
    assert.equal(env.HOMEDRIVE,'D:');assert.equal(env.HOMEPATH,home.slice(2));
    for(const [key,folder]of [['TEMP','tmp'],['TMP','tmp'],['TMPDIR','tmp'],['XDG_CACHE_HOME','cache'],['XDG_CONFIG_HOME','config'],['XDG_DATA_HOME','home']]){
      assert.equal(env[key],path.win32.join(dataDir,folder));
    }
    assert.deepEqual(value.appPaths.map(({key,value})=>[key,value]),[
      ['userData',path.win32.join(dataDir,'shell')],['sessionData',path.win32.join(dataDir,'session')],
      ['crashDumps',path.win32.join(dataDir,'crashes')],['downloads',path.win32.join(dataDir,'downloads')]]);
    assert.equal(env.PATHEXT,inherited.PATHEXT);assert.equal(env.PYTHONUTF8,'1');
    assert.equal(env.PYTHONIOENCODING,'utf-8');assert.equal(env.PYTHONDONTWRITEBYTECODE,'1');
    assert.equal(Object.keys(env).some(key=>key.toUpperCase()==='ELECTRON_RUN_AS_NODE'),false);
    assert.equal(env.OPENAI_API_KEY,undefined);assert.equal(inherited.HOME,'C:\\Users\\Original');
    assert.equal(call.options.windowsHide,true);assert.equal(call.args[0],'-B');
  }finally{await value.quitSafely();fs.rmSync(value.dataDir,{recursive:true,force:true});}
});

test('native append helper disables bytecode before its fixed bootstrap without changing ticket or authority',async()=>{
  const {createNativeAppendActivation}=require('../native-append-activation.cjs');
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'eeas-append-bytecode-'));fs.writeFileSync(path.join(root,'PACKAGE_MANIFEST.json'),'{}');
  const calls=[],ticket={operation_id:'original-own-helper',ticket_ref:{sha256:'a'.repeat(64)}};
  const activate=createNativeAppendActivation({root,dataDir:root,platform:'win32',launch(command,args,options){
    const child=new EventEmitter();child.stdout=new EventEmitter();child.stderr=new EventEmitter();child.stdin=new EventEmitter();
    child.stdin.end=input=>{calls.push({command,args,options,input:JSON.parse(input)});queueMicrotask(()=>{
      child.stdout.emit('data',Buffer.from(JSON.stringify({status:'ACTIVATION_REQUEST_RETURNED'})));child.emit('close',0,null);});};
    return child;
  }});
  try{const result=await activate(ticket);assert.equal(result.status,'ACTIVATION_REQUEST_RETURNED');assert.equal(calls.length,1);
    assert.deepEqual(calls[0].args,['-B',path.join(root,'scripts','native_package_bootstrap.py'),'--script','packages/desktop/native_append_handoff.py']);
    assert.deepEqual(calls[0].input,ticket);assert.equal(calls[0].options.shell,false);
  }finally{fs.rmSync(root,{recursive:true,force:true});}
});

test('IDE background launch binds and loads the same App without taking the user page; explicit recovery still works',async()=>{
  const value=fixture({argv:['electron','main.cjs','--eeas-background']});await value.boot();
  assert.equal(value.spawns,1);assert.equal(value.windows.length,4);assert.equal(value.errors.length,0);
  assert.equal(value.time.pending,0);
  assert.equal(value.requests.filter(row=>row.url.endsWith('/collaboration/bind')).length,1);
  assert.ok(value.events.includes('load.business'));
  assert.equal(value.events.filter(item=>['window.show','window.showInactive','window.focus'].includes(item)).length,0);
  assert.equal(value.orb().get().visible,true);
  assert.equal(value.stages().find(item=>item.phase==='APP_READY').background,true);
  value.app.emit('second-instance',{},['electron','main.cjs','--eeas-background']);
  assert.equal(value.events.filter(item=>item==='window.show').length,0);
  value.trayAction('打开 '+DISPLAY_NAME);
  assert.equal(value.events.filter(item=>item==='window.show').length,1);
  assert.equal(value.events.filter(item=>item==='window.focus').length,1);
  assert.equal(value.closeWindow(),true);assert.equal(value.windows[0].visible,false);
  const shows=value.events.filter(item=>item==='window.show').length;
  value.app.emit('second-instance',{},['electron','main.cjs','--eeas-background']);
  assert.equal(value.events.filter(item=>item==='window.show').length,shows);
  value.app.emit('activate');assert.equal(value.windows[0].visible,true);
  await value.quitSafely();assert.equal(value.child.exitCode,0);
});

test('UNIT Main keeps IDE pairing explicit and background consent never reveals its main window',async()=>{
  for(const approved of [false,true]){
    const value=fixture({argv:['--eeas-background'],pairing:async()=>({response:approved?0:1})});await value.boot();
    assert.equal(value.ideBridges.length,1);const bridge=value.ideBridges[0],controller=new AbortController();
    assert.equal(value.events.filter(item=>Array.isArray(item)&&item[0]==='dialog.pair').length,0);
    const result=await bridge.args.requestPairing({client_label:'VS Code 测试窗口',signal:controller.signal});
    assert.equal(result.approved,approved);
    const calls=value.events.filter(item=>Array.isArray(item)&&item[0]==='dialog.pair');
    assert.equal(calls.length,1);assert.equal(calls[0].length,2,'hidden Main is not revealed or used as a modal parent');
    const options=calls[0][1];assert.equal(options.defaultId,1);assert.equal(options.cancelId,1);
    assert.equal(options.signal,controller.signal);assert.ok(options.detail.includes('模型配置与密钥仍由 '+DISPLAY_NAME+' 管理'));
    assert.equal(value.events.filter(item=>['window.show','window.focus'].includes(item)).length,0);
    const reveal=await bridge.args.reveal();assert.equal(reveal.status,'OPEN_REQUESTED');
    assert.equal(value.windows[0].visible,true);
    await value.quitSafely();assert.equal(bridge.closed,true);
    assert.ok(value.events.indexOf('ide.close')<value.events.indexOf('/api/shutdown'));
    assert.throws(()=>bridge.args.reveal(),error=>error.code==='DESKTOP_UNAVAILABLE');
    const denied=await bridge.args.requestPairing({client_label:'late',signal:controller.signal});assert.equal(denied.approved,false);
  }
});

test('UNIT a consent returning after cancellation cannot approve the IDE connection',async()=>{
  const entered=deferred(),release=deferred();
  const value=fixture({pairing:async()=>{entered.resolve();return release.promise;}});await value.boot();
  const controller=new AbortController(),pending=value.ideBridges[0].args.requestPairing({client_label:'VS Code',signal:controller.signal});
  await entered.promise;controller.abort();release.resolve({response:0});assert.equal((await pending).approved,false);
  const call=value.events.find(item=>Array.isArray(item)&&item[0]==='dialog.pair');assert.equal(call[1],value.windows[0]);
  await value.quitSafely();
});

test('UNIT IDE transport failure leaves the existing App available and does not create another controller',async()=>{
  const value=fixture({ideStartError:new Error('controlled private state failure')});await value.boot();
  assert.equal(value.spawns,1);assert.equal(value.errors.length,0);assert.equal(value.windows[0].visible,true);
  assert.equal(value.ideBridges[0].closed,true);assert.ok(value.stages().some(item=>item.phase==='IDE_COMPANION_UNAVAILABLE'));
  await value.quitSafely();assert.equal(value.child.exitCode,0);
});

test('UNIT IDE artifact opening uses the original resolver and leaves the desktop App hidden',async()=>{
  let injected;
  const value=fixture({argv:['--eeas-background'],ideDispatch:async({args})=>{injected=args;return {status:'INJECTED_UNIT'};},
    request:({route,body,response})=>{
      if(!route.endsWith('/native-open/report'))return;
      assert.equal(Object.hasOwn(body,'kind'),false,'transport discriminator is not an API body field');
      assert.equal(body.download_url,'/api/projects/'+DOCUMENT_PROJECT+'/report-artifacts/'+'b'.repeat(64)+'/download');
      return response(resolvedDocument('b'));
    }});await value.boot();
  await value.ideBridges[0].args.dispatch('status',{},{});
  const result=await injected.openArtifact({kind:'report',project_id:DOCUMENT_PROJECT,artifact_id:'b'.repeat(64),sha256:'b'.repeat(64),
    download_url:'/api/projects/'+DOCUMENT_PROJECT+'/report-artifacts/'+'b'.repeat(64)+'/download'});
  assert.equal(result.status,'OPEN_REQUESTED');assert.equal(nativeOpens(value).length,1);
  assert.equal(value.windows[0].visible,false);assert.equal(value.windows[0].pinned,false);
  assert.equal(value.fileContext(DOCUMENT_PROJECT),undefined,'IDE open without a desktop discussion must not create a project-only file selection');assert.equal(result.file_context_status,'NOT_AVAILABLE');
  await value.quitSafely();
});

test('normal startup captures the external page before showing and focusing the same ready business window',async()=>{
  const value=fixture();await value.boot();
  assert.equal(value.spawns,1);assert.equal(value.windows.length,4);assert.equal(value.errors.length,0);
  assert.deepEqual(value.windows[0].configuration.icon.pngSize,[256,256]);
  assert.equal(value.time.pending,0);
  assert.equal(value.events.filter(item=>item==='window.showInactive').length,1);
  assert.ok(value.events.indexOf('window.showInactive')<value.events.indexOf('controller.spawn'));
  assert.ok(value.events.indexOf('capture.foreground:external-public-qa-page')<value.events.indexOf('window.show'));
  assert.equal(value.events.filter(item=>item==='window.focus').length,1);
  assert.ok(value.events.indexOf('window.show')<value.events.indexOf('window.focus'));
  assert.ok(value.events.indexOf('capture.foreground:external-public-qa-page')<value.events.indexOf('window.focus'));
  assert.equal(value.requests.filter(row=>row.url.endsWith('/collaboration/bind')).length,1);
  const bind=value.requests.find(row=>row.url.endsWith('/collaboration/bind'));
  const binding=JSON.parse(bind.init.body);
  assert.deepEqual(Object.keys(binding.owned_windows).sort(),['annotationOverlay','assistantOrb','quickPopover']);
  assert.equal(new Set([binding.main_window,...Object.values(binding.owned_windows)].map(value=>value.hwnd_decimal)).size,4);
  for(const role of ['quick','overlay','orb']){
    assert.ok(value.events.indexOf(role+'.create')<value.events.indexOf('/api/desktop-host/collaboration/bind'));
    assert.equal(value.events.includes(role+'.show'),false);
    assert.equal(value.events.includes(role+'.focus'),false);
    assert.equal(value.windows.find(window=>window.role===role).visible,false);
  }
  assert.equal(value.windows[0].urls.at(-1),'http://127.0.0.1:5017/');
  const html=decodeURIComponent(value.windows[0].urls[0].split(',').slice(1).join(','));
  assert.match(html,/script-src 'none'/);assert.match(html,/connect-src 'none'/);
  assert.match(html,/<main role="status" aria-busy="true"/);assert.match(html,/<h1>Cirven<\/h1>/);
  assert.doesNotMatch(html,/<p\b|class="rail"|正在启动本地服务|Starting the local service|很快|your work/i);
  assert.doesNotMatch(html,/class="cancel"|关闭窗口可留在后台|右键托盘|Close this window to keep running|right-click the tray icon/);
  assert.doesNotMatch(html,/<script|<iframe|<form|\b(?:href|src)\s*=\s*["']https?:/);
  assert.deepEqual(value.stages().map(row=>row.phase),['PREPARING','SPAWNED','WAITING_FOR_API','API_READY','IDE_COMPANION_READY','APP_READY']);
  assert.match(value.logs[0].file,/controller-boot\.log$/);
  assert.doesNotMatch(value.logs.map(row=>row.text).join(''),/PRIVATE-CSRF|private-cookie|DO-NOT-LOG/);
});

test('opening presentation never delays readiness or replays when recovering a ready App',async()=>{
  for(const background of [false,true]){
    const value=fixture({argv:background?['--eeas-background']:[]});await value.boot();
    const own=value.windows[0],pages=own.urls.filter(url=>url.startsWith('data:')).map(url=>decodeURIComponent(url.slice(url.indexOf(',')+1)));
    assert.equal(pages.length,1,'all internal phases retain the same startup document');
    assert.ok(pages[0].includes(`data-opening="${!background}"`));
    assert.ok(pages[0].includes(`class="startup-fill" data-animated="${!background}"`));
    assert.equal(value.time.now,0,'App readiness never waits for a decorative animation');
    assert.equal(value.time.pending,0);
    const before=[...own.urls];
    value.app.emit('activate');value.app.emit('second-instance',{},[]);
    assert.deepEqual(own.urls,before,'recovering the same App never starts another opening page');
    await value.quitSafely();assert.equal(value.child.exitCode,0);
  }
});

test('delayed API readiness retains one composed opening/fill document and never delays the actual ready navigation',async()=>{
  const ready=deferred();
  const value=fixture({bootstrap:({response})=>ready.promise.then(()=>response({csrf_token:'PRIVATE-CSRF-DO-NOT-LOG'},
    {'set-cookie':'private-cookie=DO-NOT-LOG; HttpOnly'}))});
  await flush();const own=value.windows[0];
  assert.equal(own.urls.filter(url=>url.startsWith('data:')).length,1);
  assert.ok(value.stages().some(row=>row.phase==='WAITING_FOR_API'));
  await value.time.advance(120);
  assert.equal(own.urls.length,1,'a still-running opening never causes phase navigation');
  ready.resolve();await value.boot();
  assert.deepEqual(own.urls.map(url=>url.startsWith('data:')?'opening':url),['opening','http://127.0.0.1:5017/']);
  assert.equal(value.time.now,120,'readiness interrupts the opening without waiting for its CSS duration');
  assert.equal(value.time.pending,0);
  await value.quitSafely();fs.rmSync(value.dataDir,{recursive:true,force:true});
});

test('Windows app explicitly permits its own UI capture; a second launch and activation restore/show the existing owner in the current work area',async()=>{
  const value=fixture();await value.boot();
  const own=value.windows[0];
  assert.equal(own.captureProtected,false);
  own.unmaximize();own.visible=false;own.minimized=true;own.bounds={x:9000,y:-4000,width:1180,height:880};
  const prior=value.events.length;await value.secondInstance();
  assert.equal(own.minimized,false);assert.equal(own.visible,true);
  assert.deepEqual(own.bounds,{x:740,y:0,width:1180,height:880});
  for(const action of ['window.restore','own-window.position','window.maximize','window.show','window.focus'])assert.ok(value.events.slice(prior).includes(action));
  own.visible=false;value.app.emit('activate');assert.equal(own.visible,true);
  assert.equal(value.windows.length,4);assert.equal(value.spawns,1);
  assert.equal(value.requests.filter(row=>row.url.endsWith('/collaboration/bind')).length,1);
  assert.equal(value.requests.filter(row=>row.url.endsWith('/context/observe')).length,1,'window recovery cannot capture or invoke another task');
});

test('quick question captures before any focus, opens only an inactive popover, and results never reveal Main',async()=>{
  for(const failed of [false,true]){
    const gate=deferred();
    const value=fixture({observe:async({body})=>{
      if(body.intent==='提问当前页面'){await gate.promise;if(failed)throw Error('Original external capture unavailable');}
    }});await value.boot();
    let anchorReceived;const openAsk=value.quick().openAsk.bind(value.quick());
    value.quick().openAsk=invocation=>{anchorReceived=invocation.anchor;return openAsk(invocation);};
    value.setForeground('actual-external-page');
    const prior=value.events.length,previousCapture=value.requests.filter(row=>row.url.endsWith('/context/observe')).length;
    value.shortcuts.get('CommandOrControl+Shift+Space')();await flush();
    assert.ok(value.events.slice(prior).includes('capture.foreground:actual-external-page'));
    assert.equal(value.events.slice(prior).includes('window.show'),false);
    assert.equal(value.events.slice(prior).includes('window.focus'),false);
    gate.resolve();await flush();
    assert.equal(value.quick().anchor.x,52);assert.equal(value.quick().anchor.y,808);
    assert.equal(anchorReceived.orbBounds.width,56,'Main forwards the actual orb anchor to the quick surface');
    const latest=value.events.slice(prior);
    assert.ok(latest.indexOf('capture.foreground:actual-external-page')<latest.indexOf('quick.showInactive'));
    assert.equal(latest.includes('window.show'),false);assert.equal(latest.includes('window.focus'),false);
    assert.equal(latest.includes('window.restore'),false);assert.equal(latest.includes('quick.show'),false);
    assert.equal(latest.includes('quick.focus'),false);assert.equal(latest.includes('overlay.show'),false);
    assert.equal(value.requests.filter(row=>row.url.endsWith('/context/observe')).length,previousCapture+1);
    assert.ok(latest.some(row=>Array.isArray(row)&&row[0]==='quick.send'&&row[1]==='eeas:quick-surface:state'&&row[2].visible));
    assert.equal(latest.some(row=>Array.isArray(row)&&row[0]==='send'&&row[1]==='eeas:quick-question'),false);
    assert.equal(value.requests.some(row=>row.url.endsWith('/native-messages')),false);
    if(!failed){
      await value.quickEvent({kind:'edit',comment:'解释这处原创测试问题',marks:[]});
      const sent=await value.quickEvent({kind:'command',command:'send'});await flush();
      assert.equal(sent.status,'COMPLETED');assert.equal(value.quick().get().result.text,'原创运输边界回执；未调用任何真实模型');
      const after=value.events.slice(prior);
      assert.equal(after.includes('window.show'),false);assert.equal(after.includes('window.focus'),false);
      assert.equal(after.includes('quick.show'),false);assert.equal(after.includes('quick.focus'),false);
      assert.equal(value.requests.filter(row=>row.url.endsWith('/native-messages')).length,1);
      assert.equal(value.quick().get().visible,false,'background result cannot reopen even the dismissed popover');
    }
    await value.quick().hide({save:false});
  }
});

// Actual Main -> AssistantOrbActions -> QuickSurface -> QuickHost ->
// NativeCompanion -> HTTP boundary. Only Electron/OS, HTTP and time are fixtures.
test('UNIT real Main pointer stream arms the real orb once and outside closure never moves its canvas or shows Main',async()=>{
  const streams=[];
  const value=fixture({actualOrb:true,request:async({route})=>{
    if(route!=='/api/desktop-host/collaboration/orb-pointer-events')return;
    const entry={cancelled:0};const body=new ReadableStream({start(controller){entry.controller=controller;},cancel(){entry.cancelled++;}});streams.push(entry);
    return {ok:true,headers:{get:key=>key==='content-type'?'application/x-ndjson':null},body};
  }});
  const send=(index,kind,sequence)=>streams[index].controller.enqueue(new TextEncoder().encode(JSON.stringify({schema:'eeas.orb-pointer-watch.v1',watch_id:'a'.repeat(32),host_generation:'b'.repeat(32),kind,sequence,...kind==='READY'?{lease_ms:60000}:{}})+'\n'));
  try{
    await value.boot();value.closeWindow();await flush();const orb=value.orb(),bounds=orb.get().bounds,anchor=orb.getAnchor(),prior=value.events.length;
    await value.orbEvent({kind:'hover',inside:true});await flush();assert.equal(streams.length,1);assert.equal(orb.get().expansionPending,true);assert.equal(orb.get().expanded,false);
    send(0,'READY',0);await flush();assert.equal(orb.get().expanded,true);
    await value.orbEvent({kind:'action',action:'annotate'});await settlePresentation(()=>value.quick().get().visible);assert.equal(orb.get().expanded,true);assert.equal(value.quick().get().visible,true);
    send(0,'OUTSIDE_POINTER_DOWN',1);await flush();assert.equal(orb.get().expanded,false);await value.time.advance(220);
    assert.equal(streams[0].cancelled,1);assert.deepEqual(orb.get().bounds,bounds);assert.deepEqual(orb.getAnchor(),anchor);assert.equal(orb.get().visible,true);
    const requests=value.requests.filter(row=>row.url.endsWith('/orb-pointer-events'));assert.equal(requests.length,1);assert.equal(requests[0].init.body,undefined);assert.equal(requests[0].init.signal.aborted,true);
    assert.equal(value.events.slice(prior).some(event=>['orb.position','orb.hide','orb.showInactive','window.show','window.focus'].includes(event)),false);
    assert.equal(value.requests.some(row=>row.url.endsWith('/native-messages')),false);
  }finally{await value.quitSafely();fs.rmSync(value.dataDir,{recursive:true,force:true});}
});

for(const action of ['ask','annotate'])for(const failed of [false,true]){
  test(`UNIT real orb ${action} remains visible through delayed ${failed?'failed':'successful'} capture without a new window or anchor change`,async()=>{
    const gate=deferred(),value=fixture({actualOrb:true,observe:async({body})=>{
      if(body.need.visual){await gate.promise;if(failed)throw Error('Original external capture unavailable');}
    }});
    try{
      await value.boot();assert.equal(value.closeWindow(),true);await flush();
      const orb=value.orb(),own=orb.window,anchor=orb.getAnchor(),bounds=own.getBounds(),windows=[...value.windows];
      assert.equal(orb.get().visible,true);
      value.setForeground('actual-external-page');const prior=value.events.length;
      assert.equal((await value.orbEvent({kind:'action',action})).status,'ACTION_DELIVERED');await flush();
      const assertStable=()=>{
        assert.equal(orb.get().visible,true,'capture must not hide the owned orb');assert.equal(own.visible,true);
        assert.deepEqual(orb.getAnchor(),anchor);assert.deepEqual(own.getBounds(),bounds);
        assert.deepEqual(value.windows,windows);assert.equal(orb.window,own);
        assert.equal(value.events.slice(prior).some(event=>['orb.hide','orb.show','orb.showInactive','orb.destroy','window.show','window.focus'].includes(event)),false);
      };
      assert.equal(value.quick().get().capturePending,true);assertStable();
      assert.equal(own.captureProtected,true);assert.equal(orb.get().captureExclusion,'REQUESTED','an API request is not native pixel qualification');
      await value.time.advance(1000);assert.equal(value.quick().get().capturePending,true);assertStable();
      gate.resolve();await flush();assert.equal(value.quick().get().capturePending,false);assertStable();
      assert.equal(value.quick().get().visible,true);assert.equal(value.windows[0].visible,false);
      assert.equal(value.requests.filter(row=>row.url.endsWith('/context/observe')).length,2,'one startup metadata read and one explicit page capture');
      assert.equal(value.requests.some(row=>row.url.endsWith('/native-messages')),false);
    }finally{gate.resolve();await flush();await value.quitSafely();fs.rmSync(value.dataDir,{recursive:true,force:true});}
  });
}

test('UNIT real orb cancellation fences a late capture and reopening a retained page does not flash or recapture',async()=>{
  const gate=deferred(),value=fixture({actualOrb:true,observe:async({body})=>{if(body.need.visual)await gate.promise;}});
  try{
    await value.boot();value.closeWindow();await flush();
    const orb=value.orb(),anchor=orb.getAnchor(),prior=value.events.length;
    await value.orbEvent({kind:'action',action:'ask'});await flush();assert.equal(value.quick().get().capturePending,true);
    await value.orbEvent({kind:'action',action:'ask'});await flush();assert.equal(value.quick().get().capturePending,false);
    gate.resolve();await flush();assert.equal(value.quick().get().visible,false,'cancelled capture cannot reopen Quick');
    assert.equal(orb.get().visible,true);assert.deepEqual(orb.getAnchor(),anchor);
    assert.equal(value.events.slice(prior).some(event=>['orb.hide','orb.showInactive','orb.destroy'].includes(event)),false);
    await value.orbEvent({kind:'action',action:'ask'});await settlePresentation(()=>value.quick().get().visible);
    assert.equal(value.quick().get().visible,true);const reads=value.requests.filter(row=>row.url.endsWith('/context/observe')).length;
    await value.quick().hide({save:false});await value.orbEvent({kind:'action',action:'ask'});await flush();
    assert.equal(value.quick().get().visible,true);assert.equal(orb.get().visible,true);
    assert.equal(value.requests.filter(row=>row.url.endsWith('/context/observe')).length,reads,'retained context is not captured again');
    assert.equal(value.events.slice(prior).some(event=>['orb.hide','orb.showInactive','orb.destroy'].includes(event)),false);
    assert.equal(value.requests.some(row=>row.url.endsWith('/native-messages')),false);
  }finally{gate.resolve();await flush();await value.quitSafely();fs.rmSync(value.dataDir,{recursive:true,force:true});}
});

for(const transition of ['preference','main-visible','close']){
  test(`UNIT late real capture cannot undo ${transition} or reveal Main`,async()=>{
    const gate=deferred(),value=fixture({actualOrb:true,observe:async({body})=>{if(body.need.visual)await gate.promise;}});
    let exit;
    try{
      await value.boot();value.closeWindow();await flush();
      const orb=value.orb(),windows=[...value.windows];
      await value.orbEvent({kind:'action',action:'ask'});await flush();assert.equal(value.quick().get().capturePending,true);
      if(transition==='preference')await value.invoke('eeas:assistant-orb-set',false);
      if(transition==='main-visible')value.trayAction('打开 '+DISPLAY_NAME);
      if(transition==='close'){exit=value.quitSafely();await flush();assert.equal(value.state().quitting,true);}
      if(transition!=='close')assert.equal(orb.get().visible,false);
      const prior=value.events.length;gate.resolve();await flush();if(exit)await exit;
      assert.equal(orb.get().visible,false);assert.deepEqual(value.windows,windows);
      assert.equal(value.events.slice(prior).some(event=>['orb.show','orb.showInactive','window.show','window.focus'].includes(event)),false);
      if(transition==='preference'){assert.equal(orb.get().enabled,false);assert.equal(value.windows[0].visible,false);}
      if(transition==='main-visible')assert.equal(value.windows[0].visible,true);
      if(transition==='close')assert.equal(value.child.exitCode,0);
      assert.equal(value.requests.some(row=>row.url.endsWith('/native-messages')),false);
    }finally{gate.resolve();await flush();if(exit)await exit;else await value.quitSafely();fs.rmSync(value.dataDir,{recursive:true,force:true});}
  });
}

test('a cold API ready after 60 seconds passes the old 45-second boundary without restart or POST replay',async()=>{
  const value=fixture({readyAt:60000});await flush();await value.time.advance(45000);
  assert.equal(value.state().hasCompanion,false);assert.equal(value.errors.length,0);
  assert.ok(value.requests.every(row=>row.url.endsWith('/api/bootstrap')&&!row.init.body));
  await value.time.advance(15150);await value.boot();
  assert.equal(value.spawns,1);assert.equal(value.windows.length,4);assert.equal(value.time.pending,0);
  const ready=value.stages().find(row=>row.phase==='API_READY');
  assert.ok(ready.readiness_wait_ms>=60000&&ready.readiness_wait_ms<=60150);
  assert.equal(value.requests.filter(row=>row.url.endsWith('/collaboration/bind')).length,1);
});

test('explicit exit during a stalled GET cancels immediately and a late ready response cannot set credentials, bind or load business',async()=>{
  const late=deferred();const value=fixture({bootstrap:()=>late.promise});await flush();
  value.trayAction('退出 '+DISPLAY_NAME);value.trayAction('退出 '+DISPLAY_NAME);await flush();await value.boot();
  assert.equal(value.errors.length,0);assert.equal(value.state().hasCookie,false);
  assert.equal(value.state().hasCompanion,false);assert.equal(value.time.pending,0);
  assert.equal(value.events.filter(item=>item==='kill:SIGTERM').length,1);
  assert.equal(value.events.includes('load.business'),false);
  late.resolve({ok:true,headers:{get:()=> 'late-private-cookie'},json:async()=>({csrf_token:'late-private-csrf'})});await flush();
  assert.equal(value.windows.length,1);assert.equal(value.state().hasCookie,false);
  assert.equal(value.requests.filter(row=>row.url.endsWith('/collaboration/bind')).length,0);
});

test('explicit exit before the saved-port check completes never spawns a late controller',async()=>{
  const port=deferred();const value=fixture({port:port.promise});await flush();
  assert.equal(value.spawns,0);value.trayAction('退出 '+DISPLAY_NAME);await flush();await value.boot();
  port.resolve(5017);await flush();
  assert.equal(value.spawns,0);assert.equal(value.requests.length,0);assert.equal(value.time.pending,0);
  assert.equal(value.events.includes('load.business'),false);
});

test('an early owned-child exit interrupts a pending probe and records the actual wait without a 180-second delay',async()=>{
  const late=deferred();const value=fixture({bootstrap:()=>late.promise});await flush();
  await value.time.advance(350);value.child.exitCode=1;value.child.emit('exit',1,null);await value.boot();
  assert.equal(value.errors.length,1);assert.match(value.errors[0][1],/就绪前已退出/);
  assert.equal(value.time.pending,0);assert.equal(value.state().hasCompanion,false);
  assert.equal(value.events.includes('kill:SIGTERM'),false);
  const failure=value.stages().find(row=>row.phase==='NOT_READY');
  assert.equal(failure.readiness_wait_ms,350);assert.equal(failure.reason,'STARTUP_PROCESS_EXIT');
});

test('the complete 180-second readiness budget is bounded and disposes every owned startup timer',async()=>{
  const value=fixture({readyAt:Infinity});await flush();await value.time.advance(180000);await value.boot();
  assert.equal(value.errors.length,1);assert.match(value.errors[0][1],/180 秒/);
  assert.equal(value.spawns,1);assert.equal(value.time.pending,0);assert.equal(value.state().hasCompanion,false);
  assert.equal(value.events.filter(item=>item==='kill:SIGTERM').length,1);
  assert.equal(value.stages().find(row=>row.phase==='NOT_READY').readiness_wait_ms,180000);
});

test('late bootstrap JSON after cancellation cannot commit authentication or initialize a session',async()=>{
  const body=deferred();const value=fixture({bootstrap:async()=>({ok:true,headers:{get:()=> 'late-cookie'},json:()=>body.promise})});
  await flush();value.trayAction('退出 '+DISPLAY_NAME);await flush();await value.boot();body.resolve({csrf_token:'late-csrf'});await flush();
  assert.equal(value.state().hasCsrf,false);assert.equal(value.state().hasCookie,false);
  assert.equal(value.state().hasCompanion,false);assert.equal(value.time.pending,0);
});

test('repeated close and app quit gestures cannot bypass a pending UNKNOWN owner shutdown',async()=>{
  const close=deferred();const value=fixture({close:()=>close.promise});await value.boot();
  value.trayAction('退出 '+DISPLAY_NAME);await flush();assert.equal(value.closeWindow(),true);
  let prevented=false;value.app.emit('before-quit',{preventDefault(){prevented=true;}});assert.equal(prevented,true);
  close.resolve({ok:true,json:async()=>({status:'CLOSE_UNKNOWN'})});await flush();
  assert.equal(value.state().stopped,false);assert.equal(value.windows[0].destroyed,false);
  assert.equal(value.events.includes('app.quit'),false);assert.equal(value.errors.length,1);
  assert.equal(value.requests.filter(row=>row.url.endsWith('/collaboration/close')).length,1);
});

test('closing Main preserves the real quick draft and controller; tray restores even with the orb disabled',async()=>{
  const value=fixture();await value.boot();await value.quick().openAsk({anchor:{x:52,y:808,orbBounds:{x:24,y:780,width:56,height:56}}});
  await value.quickEvent({kind:'edit',comment:'用户还没有提交的真实意见',marks:[]});
  await value.orb().setEnabled(false);
  const draft=value.quick().get().draft,prior=value.events.length;
  assert.equal(value.closeWindow(),true);await flush();
  assert.equal(value.windows[0].visible,false);assert.equal(value.windows[0].destroyed,false);
  assert.equal(value.state().quitting,false);assert.equal(value.state().startupCancelled,false);
  assert.equal(value.quick().get().draft.id,draft.id);assert.equal(value.quick().get().draft.comment,'用户还没有提交的真实意见');
  assert.equal(value.requests.some(row=>row.url.endsWith('/api/shutdown')||row.url.endsWith('/collaboration/close')),false);
  assert.equal(value.events.slice(prior).some(row=>Array.isArray(row)&&row[1]==='eeas:flush-drafts'),false);
  assert.ok(value.events.includes('orb.minimized:true'));assert.equal(value.trays[0].destroyed,false);
  value.trayAction('打开 '+DISPLAY_NAME);assert.equal(value.windows[0].visible,true);
  assert.equal(value.orb().get().enabled,false);
  value.closeWindow();value.trays[0].emit('double-click');assert.equal(value.windows[0].visible,true);
  assert.equal(value.spawns,1);assert.equal(value.windows.length,4);
  await value.quitSafely();
});

test('closing during cold startup does not cancel; late APP_READY stays hidden until explicit tray or second-instance recovery',async()=>{
  const gate=deferred();const value=fixture({bootstrap:()=>gate.promise});await flush();
  assert.equal(value.closeWindow(),true);const prior=value.events.length;
  assert.equal(value.state().startupCancelled,false);assert.equal(value.events.some(item=>String(item).startsWith('kill:')),false);
  gate.resolve({ok:true,headers:{get:()=> 'own-cookie'},json:async()=>({csrf_token:'own-csrf'})});await value.boot();
  assert.equal(value.windows[0].visible,false);assert.equal(value.state().hasCompanion,true);
  assert.equal(value.stages().at(-1).phase,'APP_READY');
  assert.equal(value.events.slice(prior).includes('window.show'),false);assert.equal(value.events.slice(prior).includes('window.focus'),false);
  assert.ok(value.events.includes('orb.minimized:true'));
  value.trayAction('打开 '+DISPLAY_NAME);assert.equal(value.windows[0].visible,true);
  value.closeWindow();await value.secondInstance();assert.equal(value.windows[0].visible,true);
  assert.equal(value.spawns,1);assert.equal(value.requests.filter(row=>row.url.endsWith('/collaboration/bind')).length,1);
});

test('minimizing during cold startup is respected by late business readiness',async()=>{
  const gate=deferred();const value=fixture({bootstrap:()=>gate.promise});await flush();
  value.windows[0].minimized=true;value.windows[0].emit('minimize');const prior=value.events.length;
  gate.resolve({ok:true,headers:{get:()=> 'own-cookie'},json:async()=>({csrf_token:'own-csrf'})});await value.boot();
  assert.equal(value.windows[0].minimized,true);assert.equal(value.events.slice(prior).includes('window.restore'),false);
  assert.equal(value.events.slice(prior).includes('window.focus'),false);assert.ok(value.events.includes('orb.minimized:true'));
  value.trays[0].emit('double-click');assert.equal(value.windows[0].minimized,false);assert.equal(value.windows[0].visible,true);
});

test('explicit tray exit alone flushes drafts before qualified shutdown and removes the retained recovery icon',{timeout:2500},async()=>{
  const value=fixture();await value.boot();value.closeWindow();const prior=value.events.length;
  const closed=new Promise(resolve=>value.windows[0].once('closed',resolve));
  value.trayAction('退出 '+DISPLAY_NAME);await closed;await flush();
  assert.equal(value.state().stopped,true);assert.equal(value.windows[0].destroyed,true);assert.equal(value.trays[0].destroyed,true);
  const trace=value.events.slice(prior),flushIndex=trace.findIndex(row=>Array.isArray(row)&&row[1]==='eeas:flush-drafts');
  assert.ok(flushIndex>=0&&flushIndex<trace.indexOf('/api/desktop-host/collaboration/close'));
  assert.ok(trace.indexOf('/api/shutdown')<trace.indexOf('tray.destroy'));
  assert.ok(trace.indexOf('tray.destroy')<trace.indexOf('app.quit'));
  assert.equal(value.requests.filter(row=>row.url.endsWith('/api/shutdown')).length,1);
});

test('UNKNOWN explicit tray exit retains the hidden owner and tray recovery without forced shutdown',async()=>{
  const value=fixture({close:response=>response({status:'CLOSE_UNKNOWN'})});await value.boot();value.closeWindow();
  value.trayAction('退出 '+DISPLAY_NAME);await flush();
  assert.equal(value.state().quitting,false);assert.equal(value.state().stopped,false);
  assert.equal(value.trays[0].destroyed,false);assert.equal(value.windows[0].destroyed,false);assert.equal(value.errors.length,1);
  assert.equal(value.requests.some(row=>row.url.endsWith('/api/shutdown')),false);
  assert.equal(value.events.some(item=>String(item).startsWith('kill:')),false);
  value.trayAction('打开 '+DISPLAY_NAME);assert.equal(value.windows[0].visible,true);
});

// UNIT integration: production Main handlers and the real NativeCompanion /
// Quick journal run together. Only Electron OS actions and HTTP are fixtures.
const DOCUMENT_PROJECT='comparison-project';
function documentSource(letter){return {kind:'local_file',project_id:DOCUMENT_PROJECT,workspace_id:'selected-workspace',
  path:'报告-'+letter+'.docx',revision:1,sha256:letter.repeat(64)};}
function resolvedDocument(letter){return {status:'RESOLVED',filename:'报告-'+letter+'.docx',
  native_path:path.resolve('/qa-owned-documents','报告-'+letter+'.docx'),
  file_link_ref:{project_id:DOCUMENT_PROJECT,id:letter.repeat(64),revision:1,sha256:letter.repeat(64)}};}
async function openFile(value,letter){if(!value.documentDiscussionSelected){await value.invoke('eeas:quick-scope',{projectId:DOCUMENT_PROJECT,conversationId:'document-conversation'});value.documentDiscussionSelected=true;}return value.invoke('eeas:open-document',{projectId:DOCUMENT_PROJECT,fileSourceRef:documentSource(letter)});}
function nativeOpens(value){return value.events.filter(row=>Array.isArray(row)&&row[0]==='shell.openPath');}

test('UNIT an original context-file reference opens without changing discussion, file input or saved draft',async t=>{
  const source={kind:'FILE_SNAPSHOT',project_id:'original-context-project',object_id:'a'.repeat(64),
    revision:1,sha256:'b'.repeat(64),file_identity_sha256:'c'.repeat(64),
    link_ref:{project_id:'original-context-project',id:'d'.repeat(64),revision:1,sha256:'e'.repeat(64)}};
  const value=fixture({request:({route,body,response})=>{
    if(route.endsWith('/native-open/file'))return response(resolvedDocument('b'));
    if(route.endsWith('/native-open/context-file')){
      assert.deepEqual(body,{source});
      return response({...resolvedDocument('d'),file_link_ref:source.link_ref,
        context_source_ref:source,context_file_key:source.file_identity_sha256});
    }
  }});t.after(async()=>{await value.quitSafely();fs.rmSync(value.dataDir,{recursive:true,force:true});});
  await value.boot();await openFile(value,'b');await value.quick().openAsk();
  await value.quickEvent({kind:'edit',comment:'Keep my current unsent draft',marks:[]});
  const context=JSON.parse(JSON.stringify(value.fileContext(DOCUMENT_PROJECT))),drafts=JSON.parse(JSON.stringify(await value.invoke('eeas:quick-drafts-get'))),
    scope={projectId:DOCUMENT_PROJECT,conversationId:'document-conversation'},before=nativeOpens(value).length;
  const opened=await value.invoke('eeas:open-context-file',{source,scope});
  assert.equal(opened.status,'OPEN_REQUESTED');assert.equal(opened.context_file_key,source.file_identity_sha256);
  assert.equal(opened.file_context_status,'NOT_AVAILABLE');assert.equal(opened.file_context_scope,null);
  assert.deepEqual(value.fileContext(DOCUMENT_PROJECT),context);assert.deepEqual(await value.invoke('eeas:quick-drafts-get'),drafts);
  assert.equal(nativeOpens(value).length,before+1);
  assert.equal(value.requests.some(row=>row.url.endsWith('/native-messages')),false);
});

test('UNIT a pending context-file resolution cannot open after discussion navigation changes',async t=>{
  const entered=deferred(),release=deferred(),value=fixture({request:async({route,response})=>{
    if(route.endsWith('/native-open/context-file')){entered.resolve();await release.promise;return response(resolvedDocument('d'));}
  }});t.after(async()=>{release.resolve();await value.quitSafely();fs.rmSync(value.dataDir,{recursive:true,force:true});});
  await value.boot();const scope={projectId:DOCUMENT_PROJECT,conversationId:'first-discussion'};
  await value.invoke('eeas:quick-scope',scope);
  const source={kind:'FILE_SNAPSHOT',project_id:'original-project',object_id:'a'.repeat(64),revision:1,
    sha256:'b'.repeat(64),file_identity_sha256:'c'.repeat(64),
    link_ref:{project_id:'original-project',id:'d'.repeat(64),revision:1,sha256:'e'.repeat(64)}};
  const opening=value.invoke('eeas:open-context-file',{source,scope});
  const rejected=assert.rejects(opening,/已有新的打开操作|讨论已变化|PRESENTATION_CHANGED/);
  await entered.promise;await value.invoke('eeas:quick-scope',{...scope,conversationId:'second-discussion'});
  release.resolve();await rejected;assert.equal(nativeOpens(value).length,0);
});

test('UNIT overlong Word handoffs preserve discussion context, draft and presentation without issuing a shell request',async t=>{
  let next=resolvedDocument('b');
  const value=fixture({request:({route,response})=>{
    if(route.endsWith('/native-open/file')||route.endsWith('/native-open/report')||route.endsWith('/native-open/link'))return response(next);
  }});t.after(()=>{value.quick()?.dispose();fs.rmSync(value.dataDir,{recursive:true,force:true});});
  await value.boot();await openFile(value,'b');
  const scope={projectId:DOCUMENT_PROJECT,conversationId:'document-conversation'};
  await value.quick().openAsk();await value.quickEvent({kind:'edit',comment:'人工草稿必须保留',marks:[]});
  const beforeContext=value.fileContext(DOCUMENT_PROJECT),beforeDrafts=await value.invoke('eeas:quick-drafts-get');
  assert.equal(value.quick().get().draft.comment,'人工草稿必须保留');
  const beforeState=await value.invoke('eeas:collaboration-get'),beforeOpens=nativeOpens(value).length;
  // An emoji consumes two UTF-16 code units. A code-point count would admit
  // this path even though it crosses the native WCHAR boundary.
  next={...resolvedDocument('c'),native_path:'/'+ 'a'.repeat(252)+'😀.docx'};
  assert.equal(next.native_path.length,260);assert.equal([...next.native_path].length,259);
  for(const [channel,args] of [
    ['eeas:open-document',{projectId:DOCUMENT_PROJECT,fileSourceRef:documentSource('c')}],
    ['eeas:open-report-artifact',{projectId:DOCUMENT_PROJECT,artifactId:'c'.repeat(64),sha256:'c'.repeat(64),downloadUrl:'/api/projects/'+DOCUMENT_PROJECT+'/reports/c/download'}],
    ['eeas:open-linked-file',{fileLinkRef:next.file_link_ref,scope}],
  ])await assert.rejects(value.invoke(channel,args),/较短|shorter/);
  assert.equal(nativeOpens(value).length,beforeOpens);
  assert.deepEqual(value.fileContext(DOCUMENT_PROJECT),beforeContext);
  assert.deepEqual(await value.invoke('eeas:quick-drafts-get'),beforeDrafts);
  assert.equal(value.quick().get().draft.comment,'人工草稿必须保留');
  assert.deepEqual(await value.invoke('eeas:collaboration-get'),beforeState);
  next={...resolvedDocument('c'),native_path:'/'+'a'.repeat(253)+'.docx'};
  assert.equal(next.native_path.length,259);
  assert.equal((await openFile(value,'c')).status,'OPEN_REQUESTED');
  assert.equal(nativeOpens(value).length,beforeOpens+1);
});

function noMainRevealOrNewWindow(value,prior,count){
  assert.equal(value.windows.length,count,'document handoff cannot create another window');
  for(const action of ['window.show','window.showInactive','window.moveTop','window.focus','window.restore','window.hide'])
    assert.equal(value.events.slice(prior).includes(action),false,'document handoff cannot '+action);
  assert.equal(value.windows[0].minimized,false);
}

test('UNIT successful Main file/report opening presents the same companion after shell success without focus or a new window',async()=>{
  const value=fixture({request:({route,body,response})=>{
    if(route.endsWith('/native-open/file'))return response(resolvedDocument(body.file_source_ref.sha256[0]));
    if(route.endsWith('/native-open/report'))return response(resolvedDocument('c'));
  }});await value.boot();
  const own=value.windows[0],count=value.windows.length;
  assert.equal(own.maximized,true,'normal launch remains a full workbench');
  const prior=value.events.length,file=await openFile(value,'b'),state=await value.invoke('eeas:collaboration-get');
  assert.equal(file.status,'OPEN_REQUESTED');assert.equal(state.mode,'companion');assert.equal(own.maximized,false);
  assert.equal(file.companion_presentation_status,'COMPANION_VISIBLE_WITHOUT_FOCUS');
  assert.equal(state.pinned,false);assert.equal(state.comparison_active,false);assert.equal(own.visible,true);
  assert.deepEqual({width:own.bounds.width,height:own.bounds.height},{width:520,height:760});
  const actions=value.events.slice(prior),shell=actions.findIndex(row=>Array.isArray(row)&&row[0]==='shell.openPath');
  assert.ok(shell>=0&&actions.indexOf('window.unmaximize')>shell&&actions.indexOf('window.showInactive')>shell);
  assert.ok(actions.findIndex(row=>Array.isArray(row)&&row[0]==='window.pin'&&row[1]===true)>actions.indexOf('window.showInactive'));
  for(const action of ['window.show','window.focus','window.restore','window.hide'])assert.equal(actions.includes(action),false);
  assert.equal(value.windows.length,count);assert.equal(value.fileContext(DOCUMENT_PROJECT).fileLinkRef.id,'b'.repeat(64));
  const adjusted={x:1090,y:130,width:700,height:800};own.setBounds(adjusted);
  await value.invoke('eeas:companion-pinned',true);
  const preference=fs.readFileSync(path.join(value.dataDir,'desktop-preferences.json'),'utf8');
  await value.invoke('eeas:collaboration-mode','workbench');own.setFullScreen(true);
  const reportPrior=value.events.length,report=await value.invoke('eeas:open-report-artifact',{projectId:DOCUMENT_PROJECT,artifactId:'c'.repeat(64),
    sha256:'c'.repeat(64),downloadUrl:'/api/projects/'+DOCUMENT_PROJECT+'/reports/c/download'});
  assert.equal(report.status,'OPEN_REQUESTED');assert.equal(own.fullscreen,false);assert.deepEqual(own.bounds,adjusted);assert.equal(own.pinned,true);
  assert.equal(fs.readFileSync(path.join(value.dataDir,'desktop-preferences.json'),'utf8'),preference,'automatic opening never persists another pin choice');
  assert.equal(value.events.slice(reportPrior).includes('window.focus'),false);assert.equal(nativeOpens(value).length,2);
  const repeated=value.events.length;await openFile(value,'b');
  assert.equal(value.events.slice(repeated).includes('own-window.position'),false,'repeated open retains the exact current companion rectangle');
  assert.equal(value.events.slice(repeated).includes('window.showInactive'),true);
  await value.invoke('eeas:companion-pinned',false);await openFile(value,'c');assert.equal(own.pinned,false);
  assert.equal(JSON.parse(fs.readFileSync(path.join(value.dataDir,'desktop-preferences.json'),'utf8')).companion_pinned,false);
});

test('UNIT native opening raises an unpinned companion above the viewer while the viewer keeps foreground',async()=>{
  const value=fixture({request:({route,response})=>route.endsWith('/native-open/file')?response(resolvedDocument('b')):undefined,
    openPath:async()=>{value.setForeground('qa-notepad');return '';}});
  await value.boot();await value.invoke('eeas:companion-pinned',false);
  const own=value.windows[0],prior=value.events.length;
  const preference=fs.readFileSync(path.join(value.dataDir,'desktop-preferences.json'),'utf8');
  const originalShow=own.showInactive.bind(own);
  own.showInactive=()=>{originalShow();assert.equal(value.topWindow,'qa-notepad','inactive show alone does not raise the existing window');};
  const result=await openFile(value,'b');
  assert.equal(result.status,'OPEN_REQUESTED');assert.equal(result.file_context_status,'SAVED');
  assert.equal(value.foreground,'qa-notepad');assert.equal(value.topWindow,own);
  assert.equal(own.maximized,false);assert.equal(own.fullscreen,false);assert.equal(own.pinned,false);
  assert.deepEqual({width:own.bounds.width,height:own.bounds.height},{width:520,height:760});
  assert.deepEqual(value.events.slice(prior).filter(item=>Array.isArray(item)&&item[0]==='window.pin'),[['window.pin',true],['window.pin',false]]);
  assert.equal(value.events.slice(prior).some(item=>['window.show','window.focus','window.restore'].includes(item)),false);
  assert.equal(fs.readFileSync(path.join(value.dataDir,'desktop-preferences.json'),'utf8'),preference);
  value.setForeground('qa-notepad');assert.equal(value.topWindow,'qa-notepad','the viewer can cover the unpinned companion again');
});

test('UNIT a pulse error preserves the single successful shell request and saved file context',async()=>{
  for(const failedPin of [true,false]){
    const value=fixture({request:({route,response})=>route.endsWith('/native-open/file')?response(resolvedDocument('b')):undefined,
      openPath:async()=>{value.setForeground('qa-notepad');return '';},
      setAlwaysOnTop:pin=>{if(pin===failedPin)throw Error('Original pulse failure');}});
    await value.boot();const prior=value.events.length,result=await openFile(value,'b');
    assert.equal(result.status,'OPEN_REQUESTED');assert.equal(result.companion_presentation_status,'UNCONFIRMED');
    assert.equal(result.companion_presentation_error.message,'Original pulse failure');
    assert.equal(result.file_context_status,'SAVED');assert.equal(nativeOpens(value).length,1);
    assert.equal(value.foreground,'qa-notepad');assert.equal(value.windows[0].pinned,false);
    assert.deepEqual(value.events.slice(prior).filter(item=>Array.isArray(item)&&item[0]==='window.pin'),[['window.pin',true],['window.pin',false]]);
    assert.equal(value.fileContext(DOCUMENT_PROJECT).fileLinkRef.id,'b'.repeat(64));
    assert.equal(value.events.slice(prior).includes('window.focus'),false);
  }
});

test('UNIT a silent promotion or restoration failure stays unconfirmed without repeating ShellOpen',async()=>{
  for(const failedPin of [true,false]){
    const value=fixture({request:({route,response})=>route.endsWith('/native-open/file')?response(resolvedDocument('b')):undefined,
      openPath:async()=>{value.setForeground('qa-notepad');return '';}});
    await value.boot();const own=value.windows[0],setPin=own.setAlwaysOnTop.bind(own);
    const preferencePath=path.join(value.dataDir,'desktop-preferences.json');
    const preference=fs.existsSync(preferencePath)?fs.readFileSync(preferencePath,'utf8'):null;
    own.setAlwaysOnTop=pin=>{if(pin===failedPin){value.events.push(['silent-noop',pin]);return;}setPin(pin);};
    const result=await openFile(value,'b');
    assert.equal(result.status,'OPEN_REQUESTED');assert.equal(result.companion_presentation_status,'UNCONFIRMED');
    assert.equal(result.companion_presentation_error.message,failedPin?'Temporary window pin was not applied':'Temporary window pin was not restored');
    assert.equal(result.file_context_status,'SAVED');assert.equal(nativeOpens(value).length,1);
    assert.equal(value.fileContext(DOCUMENT_PROJECT).fileLinkRef.id,'b'.repeat(64));
    assert.equal(value.foreground,'qa-notepad');assert.equal(own.pinned,!failedPin);
    assert.equal(value.topWindow,failedPin?'qa-notepad':own);
    assert.equal(fs.existsSync(preferencePath)?fs.readFileSync(preferencePath,'utf8'):null,preference,'presentation never changes a saved pin choice');
  }
});

test('UNIT hidden Main file opening and an owned Quick artifact both keep Main hidden and never pin it',async()=>{
  const source=documentSource('b'),artifact={filename:'报告-b.docx',sha256:'b'.repeat(64),file_source_ref:source};
  const value=fixture({artifacts:[artifact],request:({route,response})=>route.endsWith('/native-open/file')?response(resolvedDocument('b')):undefined});
  await value.boot();value.closeWindow();const own=value.windows[0],count=value.windows.length,prior=value.events.length;
  const opened=await openFile(value,'b');assert.equal(opened.status,'OPEN_REQUESTED');
  assert.equal(own.visible,false);assert.equal(own.pinned,false);noMainRevealOrNewWindow(value,prior,count);
  await value.invoke('eeas:quick-scope',{projectId:DOCUMENT_PROJECT});
  await value.quick().openAsk();await value.quickEvent({kind:'edit',comment:'生成一份原创演示报告',marks:[]});
  const sent=await value.quickEvent({kind:'command',command:'send'});assert.equal(sent.status,'COMPLETED');
  await value.quick().openAsk();assert.equal(value.quick().get().visible,true,'reopen the retained result before a real artifact click');
  const quickPrior=value.events.length,result=await value.quickEvent({kind:'artifact',index:0});
  assert.equal(result.status,'OPENED');assert.equal(nativeOpens(value).length,2);
  assert.equal(own.visible,false);assert.equal(own.pinned,false);noMainRevealOrNewWindow(value,quickPrior,count);
  assert.equal(value.windows.find(window=>window.role==='quick').pinned,false,'Quick follows the same opt-in pin preference');
  assert.equal(value.fileContext(DOCUMENT_PROJECT).fileLinkRef.id,'b'.repeat(64));
});

test('UNIT file resolution cannot override a newer mode/unpin decision or call the OS opener, including hidden Main recovery',async()=>{
  for(const choice of ['workbench','unpin','hidden-workbench']){
    const entered=deferred(),release=deferred();
    const value=fixture({request:async({route,response})=>{
      if(route.endsWith('/native-open/file')){entered.resolve();await release.promise;return response(resolvedDocument('b'));}
    }});await value.boot();if(choice==='hidden-workbench')value.closeWindow();
    const pending=openFile(value,'b'),rejected=assert.rejects(pending,/协作视图已变化|PRESENTATION_CHANGED/);
    await entered.promise;
    if(choice==='unpin')await value.invoke('eeas:companion-pinned',false);
    else {if(choice==='hidden-workbench')value.trayAction('打开 '+DISPLAY_NAME);await value.invoke('eeas:collaboration-mode','workbench');}
    const prior=value.events.length;release.resolve();await rejected;
    assert.equal(nativeOpens(value).length,0);assert.equal(value.fileContext(DOCUMENT_PROJECT),undefined);
    assert.equal(value.events.slice(prior).some(row=>Array.isArray(row)&&row[0]==='window.pin'&&row[1]===true),false);
    assert.equal((await value.invoke('eeas:collaboration-get')).mode,'workbench');
  }
});

test('UNIT reversed native resolve replies open only the latest explicit file and retain its exact Quick context',async()=>{
  const gates={b:deferred(),c:deferred()},entered={b:deferred(),c:deferred()};
  const value=fixture({request:async({route,body,response})=>{
    if(route.endsWith('/native-open/file')){const letter=body.file_source_ref.sha256[0];entered[letter].resolve();await gates[letter].promise;return response(resolvedDocument(letter));}
  }});await value.boot();
  const old=openFile(value,'b'),rejected=assert.rejects(old,/已有新的打开操作/);await entered.b.promise;
  const latest=openFile(value,'c');await entered.c.promise;gates.c.resolve();
  assert.equal((await latest).file_context_status,'SAVED');gates.b.resolve();await rejected;
  assert.deepEqual(nativeOpens(value).map(row=>row[1]),[resolvedDocument('c').native_path]);
  assert.equal(value.fileContext(DOCUMENT_PROJECT).fileLinkRef.id,'c'.repeat(64));
});

test('UNIT changing or clearing the same-project discussion blocks an in-flight native open before shell authority',async()=>{
 for(const next of [{projectId:DOCUMENT_PROJECT,conversationId:'another-discussion'},{}]){
  const entered=deferred(),release=deferred(),value=fixture({request:async({route,response})=>{
   if(route.endsWith('/native-open/file')){entered.resolve();await release.promise;return response(resolvedDocument('b'));}
  }});await value.boot();const opening=openFile(value,'b'),rejected=assert.rejects(opening,/已有新的打开操作|讨论已变化|PRESENTATION_CHANGED/);await entered.promise;
  await value.invoke('eeas:quick-scope',next);release.resolve();await rejected;
  assert.equal(nativeOpens(value).length,0);assert.equal(value.invoke('eeas:file-context-get').selected,null);
 }
});

test('UNIT exact scoped file clearing has CAS and late/malformed scopes cannot remove another discussion selection',async()=>{
 const value=fixture({request:({route,body,response})=>route.endsWith('/native-open/file')?response(resolvedDocument(body.file_source_ref.sha256[0])):undefined});await value.boot();await openFile(value,'b');
 const current=value.invoke('eeas:file-context-get'),scope=current.scope,expectedRef=current.selected.fileLinkRef;
 assert.equal(scope.conversationId,'document-conversation');await openFile(value,'c');
 await assert.rejects(async()=>value.invoke('eeas:file-context-clear',{scope,expectedRef}),/newer selection/);
 const latest=value.invoke('eeas:file-context-get');assert.equal(latest.selected.fileLinkRef.id,'c'.repeat(64));
 await value.invoke('eeas:quick-scope',{...scope,conversationId:'new-discussion'});
 await assert.rejects(async()=>value.invoke('eeas:file-context-clear',{scope,expectedRef:latest.selected.fileLinkRef}),/讨论已变化/);
 await value.invoke('eeas:quick-scope',scope);
 const cleared=value.invoke('eeas:file-context-clear',{scope,expectedRef:latest.selected.fileLinkRef});assert.equal(cleared.selected,null);assert.equal(nativeOpens(value).length,2);
});

test('UNIT reopen linked file is an explicit exact-scope request and forwards no arbitrary native path',async()=>{
 const value=fixture({request:({route,response})=>route.endsWith('/native-open/file')||route.endsWith('/native-open/link')?response(resolvedDocument('b')):undefined});await value.boot();await openFile(value,'b');
 const context=value.invoke('eeas:file-context-get');await value.invoke('eeas:open-linked-file',{scope:context.scope,fileLinkRef:context.selected.fileLinkRef});
 const request=value.requests.find(row=>row.url.endsWith('/native-open/link'));assert.ok(request);assert.deepEqual(JSON.parse(request.init.body),{link_ref:context.selected.fileLinkRef});
 await assert.rejects(async()=>value.invoke('eeas:open-linked-file',{scope:{...context.scope,conversationId:'new'},fileLinkRef:context.selected.fileLinkRef}),/讨论已变化/);
 assert.equal(nativeOpens(value).length,2);
});

test('UNIT an OS-open acknowledgement arriving after a newer open cannot replay or replace the newer file context',async()=>{
  const shellEntered=deferred(),shellAck=deferred();
  const value=fixture({request:({route,body,response})=>route.endsWith('/native-open/file')?response(resolvedDocument(body.file_source_ref.sha256[0])):undefined,
    openPath:async filename=>{if(filename===resolvedDocument('b').native_path){shellEntered.resolve();await shellAck.promise;}return '';}});
  await value.boot();const old=openFile(value,'b');await shellEntered.promise;
  const latest=await openFile(value,'c');assert.equal(latest.file_context_status,'SAVED');
  shellAck.resolve();const superseded=await old;
  assert.equal(superseded.status,'OPEN_REQUESTED');assert.equal(superseded.file_context_status,'SUPERSEDED');
  assert.equal(nativeOpens(value).length,2);assert.equal(value.fileContext(DOCUMENT_PROJECT).fileLinkRef.id,'c'.repeat(64));
});

test('UNIT a newer open while companion preparation waits leaves the already issued old shell request superseded',async()=>{
  const entered=deferred(),release=deferred();let gate=false;
  const value=fixture({request:async({route,body,response})=>{
    if(route.endsWith('/native-open/file'))return response(resolvedDocument(body.file_source_ref.sha256[0]));
    if(gate&&route.endsWith('/collaboration/get')){gate=false;entered.resolve();await release.promise;return response({status:'ACTIVE'});}
  }});await value.boot();gate=true;
  const old=openFile(value,'b');await entered.promise;const latest=openFile(value,'c');
  await flush();release.resolve();const priorResult=await old,newResult=await latest;
  assert.equal(priorResult.status,'OPEN_REQUESTED');assert.equal(priorResult.file_context_status,'SUPERSEDED');
  assert.equal(newResult.file_context_status,'SAVED');assert.deepEqual(nativeOpens(value).map(row=>row[1]),[resolvedDocument('b').native_path,resolvedDocument('c').native_path]);
  assert.equal(value.fileContext(DOCUMENT_PROJECT).fileLinkRef.id,'c'.repeat(64));
});

test('UNIT a failed native shell open never saves its file link or retries the OS request',async()=>{
  const value=fixture({request:({route,response})=>route.endsWith('/native-open/file')?response(resolvedDocument('b')):undefined,
    openPath:async()=> 'No associated viewer in the controlled OS fixture'});await value.boot();
  await assert.rejects(openFile(value,'b'),/No associated viewer/);
  assert.equal(nativeOpens(value).length,1);assert.equal(value.fileContext(DOCUMENT_PROJECT),undefined);
});

test('UNIT closing during native resolve blocks opening; an already issued shell call is not replayed or saved after exit',async()=>{
  for(const stage of ['resolve','shell']){
    const entered=deferred(),release=deferred();
    const value=fixture({request:async({route,response})=>{
      if(route.endsWith('/native-open/file')){if(stage==='resolve'){entered.resolve();await release.promise;}return response(resolvedDocument('b'));}
    },openPath:async()=>{entered.resolve();await release.promise;return '';}});await value.boot();
    const pending=openFile(value,'b');const outcome=stage==='resolve'?assert.rejects(pending,/应用正在关闭/):pending;
    await entered.promise;await value.quitSafely();release.resolve();await outcome;
    assert.equal(nativeOpens(value).length,stage==='resolve'?0:1);assert.equal(value.fileContext(DOCUMENT_PROJECT),undefined);
    assert.equal(value.state().stopped,true);
  }
});

test('UNIT live language switches persist alongside Orb and keep window, page, and original task owners',async()=>{
 const value=fixture();await value.boot();
 await value.invoke('eeas:assistant-orb-set',false);
 const own=value.windows[0],quick=value.quick(),beforeWindows=[...value.windows],urls=own.urls.length,requests=value.requests.length,events=value.events.length;
 const priorState=quick.get();
 const result=value.invoke('eeas:ui-language-set','en');
 assert.equal(result.preference,'en');assert.equal(result.resolved_locale,'en');
 assert.equal(value.windows[0],own);assert.equal(value.quick(),quick);assert.equal(own.urls.length,urls);assert.deepEqual(value.windows,beforeWindows);
 assert.equal(quick.get().locale,'en');assert.equal(quick.get().epoch,priorState.epoch);
 assert.equal(value.requests.length,requests);assert.equal(value.spawns,1);assert.equal(value.child.exitCode,null);
 assert.equal(value.events.slice(events).some(e=>typeof e==='string'&&(/\.show$|\.focus$|\.create$/.test(e)||e==='controller.spawn')),false);
 let persisted=JSON.parse(fs.readFileSync(path.join(value.dataDir,'desktop-preferences.json')));
 assert.equal(persisted.ui_language,'en');assert.equal(persisted.assistant_orb_enabled,false);
 assert.ok(value.trays[0].menu.items.some(item=>item.label==='Exit '+DISPLAY_NAME));
 const languageEvent=value.events.find(e=>Array.isArray(e)&&e[1]==='eeas:ui-language-changed');assert.equal(languageEvent[2].resolved_locale,'en');
 assert.throws(()=>value.invoke('eeas:ui-language-set','fr'),/Choose System/);
 value.invoke('eeas:ui-language-set','zh-CN');assert.equal(value.invoke('eeas:ui-language-get').resolved_locale,'zh-CN');
 persisted=JSON.parse(fs.readFileSync(path.join(value.dataDir,'desktop-preferences.json')));assert.equal(persisted.ui_language,'zh-CN');assert.equal(persisted.assistant_orb_enabled,false);
 assert.equal(own.urls.length,urls);assert.equal(value.requests.length,requests);
});

test('UNIT system locale selects English startup and pairing without changing business identifiers',async()=>{
 const value=fixture({systemLocale:'en-GB'});await value.boot();
 assert.equal(value.invoke('eeas:ui-language-get').resolved_locale,'en');
 const html=decodeURIComponent(value.windows[0].urls[0]);
 assert.match(html,/<html lang="en"/);assert.match(html,/aria-label="Cirven · Starting"/);
 assert.doesNotMatch(html,/Preparing your workspace|Starting the local service|正在启动本地服务/);
 assert.ok(value.windows[0].configuration.icon);assert.equal(value.windows[0].configuration.title,DISPLAY_NAME);
 const call=value.requests.find(item=>item.url.endsWith('/api/desktop-host/collaboration/bind'));
 assert.equal(call.init.headers['Accept-Language'],'en');
 assert.equal(value.ideBridges.length,1);assert.equal(value.ideBridges[0].closed,false);
 assert.ok(value.trays[0].menu.items.some(item=>item.label==='Exit '+DISPLAY_NAME));
});

test('actual Main wires durable UNKNOWN retention and independent blank question without repeat send, cancel or Main reveal',async t=>{
  const value=fixture({request:async({route})=>{
    if(route==='/api/desktop-host/native-messages')return {ok:false,status:409,json:async()=>({error:{code:'REVISION_CONFLICT',message:'Selected Windows file is busy or its namespace changed',detail:'DO_NOT_JOURNAL_TOKEN'}})};
  }});
  t.after(()=>{value.quick()?.dispose();fs.rmSync(value.dataDir,{recursive:true,force:true});});
  await value.boot();await value.quick().openAsk();await value.quickEvent({kind:'edit',comment:'独立原创问题，旧请求不可重复发送',marks:[]});
  await value.quickEvent({kind:'command',command:'send'});await value.quick().openAsk();
  const old=value.quick().get(),list=value.invoke('eeas:quick-drafts-get'),record=list.records.find(item=>item.operationId===old.draft.operationId);
  assert.equal(old.draft.phase,'UNKNOWN');assert.equal(record.message.first_submission_observation.http_status,409);
  const prior=value.events.length;await value.quickEvent({kind:'command',command:'new-question'});
  assert.equal(value.quick().get().draft.comment,'');assert.equal(value.quick().get().draft.operationId,null);
  assert.notEqual(value.quick().get().draft.id,old.draft.id);
  const preserved=value.invoke('eeas:quick-drafts-get').records.find(item=>item.id===record.id);
  assert.equal(JSON.stringify(preserved),JSON.stringify(record));
  assert.equal(value.requests.filter(item=>item.url.endsWith('/native-messages')).length,1);
  assert.equal(value.requests.some(item=>item.url.endsWith('/cancel')),false);
  assert.equal(value.events.slice(prior).some(item=>['window.show','window.focus','window.restore'].includes(item)),false);
  assert.doesNotMatch(fs.readFileSync(path.join(value.dataDir,'quick-assistant-journal.json'),'utf8'),/DO_NOT_JOURNAL_TOKEN/);
});

test('UNIT normal launch maximizes the same workbench without fullscreen; background launch stays hidden and never maximizes',async()=>{
 for(const background of [false,true]){
  const f=fixture({argv:background?['--eeas-background']:[]});await f.boot();
  const own=f.windows[0],state=await f.invoke('eeas:collaboration-get');
  assert.equal(state.mode,'workbench');assert.equal(state.fullscreen,false);assert.equal(state.pinned,false);
  assert.equal(own.maximized,!background);assert.equal(own.configuration.width,1180);assert.equal(own.configuration.height,880);
  assert.equal(f.events.filter(event=>event==='window.maximize').length,background?0:1);
  assert.equal(f.events.some(event=>Array.isArray(event)&&event[0]==='window.fullscreen'),false);
  if(background)assert.equal(f.events.some(event=>['window.show','window.showInactive','window.focus'].includes(event)),false);
  await f.quitSafely();assert.equal(f.child.exitCode,0);
 }
});

test('UNIT Quick and Main share persisted opt-in pin while capture overlay stays top without changing that preference',async()=>{
 const f=fixture({preferences:{companion_pinned:true,assistant_orb_enabled:false,ui_language:'zh-CN'}});await f.boot();
 const own=f.windows[0],quick=f.windows.find(window=>window.role==='quick'),overlay=f.windows.find(window=>window.role==='overlay');
 assert.equal(own.pinned,false);assert.equal(quick.pinned,true);assert.equal(overlay.pinned,true);
 await f.quick().openAsk();await f.quickEvent({kind:'edit',comment:'保留的输入，不发送模型',marks:[]});
 await f.quickEvent({kind:'command',command:'pin'});
 assert.equal(quick.pinned,false);assert.equal(overlay.pinned,true);assert.equal(own.pinned,false);
 assert.equal(f.quick().get().draft.comment,'保留的输入，不发送模型');
 await f.invoke('eeas:collaboration-mode','companion');assert.equal(own.pinned,false);
 await f.invoke('eeas:companion-pinned',true);assert.equal(own.pinned,true);assert.equal(quick.pinned,true);
 await f.invoke('eeas:collaboration-mode','workbench');assert.equal(own.pinned,false);assert.equal(quick.pinned,true);
 const prefs=JSON.parse(fs.readFileSync(path.join(f.dataDir,'desktop-preferences.json')));
 assert.equal(prefs.companion_pinned,true);assert.equal(prefs.assistant_orb_enabled,false);assert.equal(prefs.ui_language,'zh-CN');
 await f.quitSafely();assert.equal(f.child.exitCode,0);
});

test('UNIT a second normal START opens the workbench maximized while background and tray recovery preserve the chosen small chat',async()=>{
 const f=fixture();await f.boot();await f.invoke('eeas:collaboration-mode','companion');await f.invoke('eeas:companion-pinned',true);
 f.closeWindow();f.trayAction('打开 '+DISPLAY_NAME);
 assert.equal((await f.invoke('eeas:collaboration-get')).mode,'companion');assert.equal(f.windows[0].pinned,true);assert.equal(f.windows[0].maximized,false);
 f.app.emit('second-instance',{},['--eeas-background']);await flush();assert.equal((await f.invoke('eeas:collaboration-get')).mode,'companion');
 await f.invoke('eeas:collaboration-fullscreen',true);await f.secondInstance([]);
 const state=await f.invoke('eeas:collaboration-get');assert.equal(state.mode,'workbench');assert.equal(state.fullscreen,false);assert.equal(state.maximized,true);
 assert.equal(state.pinned,false);assert.equal(state.pinned_preference,true);assert.equal(f.quick().get().pinnedPreference,true);
 await f.quitSafely();assert.equal(f.child.exitCode,0);
});

test('UNIT known shell failure preserves the original large/fullscreen Main and source context without another OS request',async()=>{
  for(const throws of [false,true]){
    const value=fixture({request:({route,response})=>route.endsWith('/native-open/file')?response(resolvedDocument('b')):undefined,
      openPath:async()=>{if(throws)throw Error('Original shell failure');return 'Original shell failure';}});
    await value.boot();const own=value.windows[0];own.setFullScreen(true);const bounds={...own.bounds},prior=value.events.length;
    await assert.rejects(openFile(value,'b'),/Original shell failure/);
    assert.equal(nativeOpens(value).length,1);assert.equal(own.fullscreen,true);assert.equal(own.maximized,true);
    assert.deepEqual(own.bounds,bounds);assert.equal((await value.invoke('eeas:collaboration-get')).mode,'workbench');
    assert.equal(value.fileContext(DOCUMENT_PROJECT),undefined);
    assert.equal(value.events.slice(prior).some(item=>['own-window.position','window.unmaximize','window.showInactive','window.moveTop','window.show','window.focus'].includes(item)),false);
  }
});

test('UNIT a newer user presentation or hidden/minimized Main while shell waits wins over the late success',async()=>{
  for(const choice of ['workbench','unpin','fullscreen','hidden','minimized']){
    const entered=deferred(),release=deferred();
    const value=fixture({request:({route,response})=>route.endsWith('/native-open/file')?response(resolvedDocument('b')):undefined,
      openPath:async()=>{entered.resolve();await release.promise;return '';}});
    await value.boot();const own=value.windows[0],pending=openFile(value,'b');await entered.promise;
    if(choice==='unpin')await value.invoke('eeas:companion-pinned',false);
    else if(choice==='fullscreen')await value.invoke('eeas:collaboration-fullscreen',true);
    else if(choice==='hidden')own.hide();
    else if(choice==='minimized')own.minimized=true;
    else await value.invoke('eeas:collaboration-mode','workbench');
    const prior=value.events.length,bounds={...own.bounds},full=own.fullscreen,max=own.maximized;
    release.resolve();const result=await pending;
    assert.equal(result.status,'OPEN_REQUESTED');assert.equal(nativeOpens(value).length,1);
    assert.deepEqual(own.bounds,bounds);assert.equal(own.fullscreen,full);assert.equal(own.maximized,max);
    assert.equal(value.events.slice(prior).some(item=>['own-window.position','window.unmaximize','window.showInactive','window.moveTop','window.show','window.focus','window.restore'].includes(item)),false,choice);
    assert.equal((await value.invoke('eeas:collaboration-get')).mode,'workbench');
    if(choice==='hidden')assert.equal(own.visible,false);
    if(choice==='minimized')assert.equal(own.minimized,true);
  }
});

test('global coordinator IPC uses its one persisted conversation without foreground capture or task replay',async()=>{
  const {supervisorConversationDigest:digest}=require('../supervisor-conversation.cjs');
  const receipts=new Map(),posts=[];
  const value=fixture({request:({route,body,response})=>{
    if(route==='/api/supervisor')return response({tasks:[],alerts:[],observer:{status:'READY',last_success_at:new Date().toISOString()}});
    if(route==='/api/desktop-host/native-messages'){
      posts.push(body);const receipt={...body,request_sha256:digest(body),automatic_retry:false,status:'RETURNED',text:'任务状态已核对'};
      receipts.set(body.operation_id,receipt);return response(receipt);
    }
    if(route.startsWith('/api/desktop-host/native-messages/'))return response(receipts.get(route.split('/').pop()));
  }});
  await value.boot();
  try{
    const initial=await value.invoke('eeas:supervisor-conversation-get');
    const captures=value.events.filter(row=>typeof row==='string'&&row.startsWith('capture.foreground:')).length;
    await value.invoke('eeas:supervisor-draft-save',{text:'哪项任务需要我处理？',expectedRevision:initial.draft.revision});
    await value.invoke('eeas:supervisor-local-save',{question:'现在进度如何',answer:{text:'没有正在执行的任务。'}});
    assert.equal(posts.length,0);
    const profile={id:'public-profile',revision:1,sha256:'a'.repeat(64)};
    const body={operationId:'coordinator-exact-one',text:'帮我解释目前的情况',providerProfileRef:profile};
    const result=await value.invoke('eeas:supervisor-ask',body);
    assert.equal(result.status,'RETURNED');assert.equal(posts.length,1);
    assert.equal(posts[0].conversation_id,initial.conversation_id);
    for(const key of ['context_ref','project_id','file_source_ref','file_link_ref'])assert.equal(posts[0][key],null);
    await value.invoke('eeas:supervisor-ask',body);await value.invoke('eeas:supervisor-inspect',body.operationId);
    assert.equal(posts.length,1);
    const latest=await value.invoke('eeas:supervisor-conversation-get');
    assert.equal(latest.messages.find(row=>row.operation_id===body.operationId&&row.role==='user').text,body.text);
    assert.equal(latest.messages.some(row=>row.text.includes('Do not expose this envelope')),false);
    assert.equal(value.events.filter(row=>typeof row==='string'&&row.startsWith('capture.foreground:')).length,captures);
    assert.throws(()=>value.invoke('eeas:supervisor-ask',{...body,contextRef:profile}));
    assert.throws(()=>value.invoke('eeas:supervisor-draft-save',{text:'冲突不能覆盖',expectedRevision:0}),/SUPERVISOR_DRAFT_CONFLICT/);
    assert.equal(posts.length,1);
  }finally{await value.activity()?.close();fs.rmSync(value.dataDir,{recursive:true,force:true});}
});

test('floating coordinator and titlebar use the actual one Main store and current public model without screen capture',async()=>{
  const {supervisorConversationDigest:digest}=require('../supervisor-conversation.cjs');
  const posts=[];const profile={id:'selected-public',revision:1,sha256:'b'.repeat(64)};
  const value=fixture({request:({route,body,response})=>{
    if(route==='/api/supervisor')return response({settings:{enabled:true,notifications_enabled:false,interval_seconds:30},tasks:[],alerts:[],observer:{status:'READY',last_success_at:new Date().toISOString()}});
    if(route==='/api/desktop-host/native-messages'){
      posts.push(body);return response({...body,request_sha256:digest(body),automatic_retry:false,status:'RETURNED',text:'来自同一个总管'});
    }
  }});
  await value.boot();
  try{
    const initial=await value.invoke('eeas:supervisor-conversation-get');
    await value.invoke('eeas:quick-scope',{conversationId:'page-conversation',providerProfileRef:profile});
    await value.quick().openActivity({settings:{enabled:true},tasks:[],alerts:[],observer:{status:'READY'}});
    const epoch=value.quick().get().epoch;
    const floating=await value.quickEvent({kind:'supervisor-get',epoch});
    assert.equal(floating.conversation_id,initial.conversation_id);
    assert.deepEqual(value.quick().get().supervisor.providerProfileRef,profile);
    const captures=value.events.filter(row=>typeof row==='string'&&row.startsWith('capture.foreground:')).length;
    await value.quickEvent({kind:'supervisor-draft-save',epoch,body:{text:'浮窗里的原问题',expectedRevision:floating.draft.revision}});
    const inMain=await value.invoke('eeas:supervisor-conversation-get');assert.equal(inMain.draft.text,'浮窗里的原问题');
    await value.invoke('eeas:supervisor-draft-save',{text:'主窗口继续修改',expectedRevision:inMain.draft.revision});
    assert.equal(value.quick().get().supervisor.snapshot.draft.text,'主窗口继续修改');
    const result=await value.quickEvent({kind:'supervisor-ask',epoch,body:{operationId:'floating-unique-question',text:'主窗口继续修改',providerProfileRef:profile}});
    assert.equal(result.status,'RETURNED');assert.equal(posts.length,1);
    assert.equal(posts[0].conversation_id,initial.conversation_id);assert.equal(posts[0].project_id,null);assert.equal(posts[0].context_ref,null);
    const saved=await value.invoke('eeas:supervisor-conversation-get');
    assert.equal(saved.messages.find(row=>row.operation_id==='floating-unique-question'&&row.role==='assistant').text,'来自同一个总管');
    assert.equal(value.events.filter(row=>typeof row==='string'&&row.startsWith('capture.foreground:')).length,captures);
    assert.equal(value.events.some(row=>Array.isArray(row)&&row[1]==='eeas:supervisor-open'),false);
  }finally{fs.rmSync(value.dataDir,{recursive:true,force:true});}
});

test('Main resolves an owned coordinator task back inside the same floating conversation without revealing Main',async()=>{
  const {supervisorConversationDigest:digest}=require('../supervisor-conversation.cjs');
  let task,original;const snapshot=()=>({settings:{enabled:true,notifications_enabled:false,interval_seconds:30},tasks:task?[task]:[],alerts:[],observer:{status:'READY',last_success_at:new Date().toISOString()}});
  const value=fixture({taskActivity:true,request:({route,body,response})=>{
    if(route==='/api/supervisor')return response(snapshot());
    if(route==='/api/desktop-host/native-messages'){original??=body;return response({...body,request_sha256:digest(body),automatic_retry:false,status:'RETURNED',text:'这条原问题的回答'});}
    if(route==='/api/desktop-host/native-messages/selected_original_question')return response({...original,request_sha256:digest(original),automatic_retry:false,status:'RETURNED',text:'原操作核对后的回答'});
    if(route==='/api/supervisor/task-detail')return response({key:task.key,task,input_text:original.raw_text,answer_text:'这条原问题的回答',artifacts:[]});
  }});
  await value.boot();
  try{
    await value.invoke('eeas:quick-scope',{conversationId:'current_page_discussion',providerProfileRef:{id:'public',revision:1,sha256:'a'.repeat(64)}});
    await value.invoke('eeas:supervisor-ask',{operationId:'selected_original_question',text:'我要返回这条原问题',providerProfileRef:{id:'public',revision:1,sha256:'a'.repeat(64)}});
    task={key:{kind:'native_message',project_id:null,object_id:original.operation_id,operation_id:original.operation_id,generation:1},label:'原问题',source_status:'RETURNED',source_ref:{id:original.operation_id,revision:1,sha256:digest({request_sha256:digest(original),status:'RETURNED'})},classification:'EXECUTION_RETURNED',observation_status:'CURRENT',observed_at:new Date().toISOString()};
    await value.quick().openActivity(snapshot());let navigationError;const originalOpen=value.quick().onOpenTask;value.quick().onOpenTask=async key=>{try{return await originalOpen(key);}catch(error){navigationError=error.message;throw error;}};
    const state=value.quick().get(),before=value.events.length,requests=value.requests.length;
    const result=await value.quickEvent({kind:'activity-open',epoch:state.epoch,activity_revision:state.activity.revision,token:state.activity.items[0].token});
    assert.equal(result.status,'SUPERVISOR',JSON.stringify({result,navigationError,error:value.quick().get().error,errors:value.errors}));assert.equal(value.quick().get().visible,true);
    const selected=value.quick().get().supervisor.selection;
    assert.equal(selected.question,'我要返回这条原问题');assert.equal(selected.answer_text,'这条原问题的回答');assert.equal(selected.request_sha256,digest(original));
    assert.equal(value.events.slice(before).some(row=>typeof row==='string'&&(['window.show','window.showInactive','window.focus','window.restore','own-window.position'].includes(row)||row.startsWith('capture.foreground:'))),false);
    assert.equal(value.events.slice(before).some(row=>Array.isArray(row)&&row[1]==='eeas:supervised-task'),false);
    assert.equal(value.requests.slice(requests).some(row=>row.init.method==='POST'),false);
    const selectionRevision=value.quick().get().supervisor.selection_revision;
    for(let index=0;index<14;index++)await value.invoke('eeas:supervisor-local-save',{question:'本地状态 '+index,answer:{text:'本地结果 '+index}});
    assert.equal(value.quick().get().supervisor.snapshot.messages.some(row=>row.operation_id===original.operation_id),false);
    await value.invoke('eeas:supervisor-inspect',original.operation_id);
    assert.equal(value.quick().get().supervisor.selection.answer_text,'原操作核对后的回答');
    assert.equal(value.quick().get().supervisor.selection.question,'我要返回这条原问题');
    assert.equal(value.quick().get().supervisor.selection_revision,selectionRevision);
    assert.equal(value.requests.filter(row=>new URL(row.url).pathname==='/api/desktop-host/native-messages'&&row.init.method==='POST').length,1);
    const followupRef={operation_id:original.operation_id,request_sha256:digest(original)};
    const followup=await value.quickEvent({kind:'supervisor-ask',epoch:state.epoch,body:{operationId:'followup_original_question',text:'这里继续解释',providerProfileRef:original.provider_profile_ref,followupRef}});
    assert.equal(followup.status,'RETURNED');
    const followupWire=value.requests.filter(row=>new URL(row.url).pathname==='/api/desktop-host/native-messages'&&row.init.method==='POST').map(row=>JSON.parse(row.init.body)).at(-1);
    assert.match(followupWire.raw_text,/selected_discussion/);assert.match(followupWire.raw_text,/我要返回这条原问题/);assert.match(followupWire.raw_text,/原操作核对后的回答/);
    for(const field of ['project_id','context_ref','file_source_ref','file_link_ref'])assert.equal(followupWire[field],null);
    assert.equal((await value.invoke('eeas:supervisor-conversation-get')).messages.find(row=>row.role==='user'&&row.operation_id==='followup_original_question').text,'这里继续解释');
  }finally{await value.activity()?.close();fs.rmSync(value.dataDir,{recursive:true,force:true});}
});

test('the existing orb sends the most recent workflow page identity and skips context capture for chat and markup',async()=>{
  const value=fixture({actualOrb:true});
  await value.boot();
  const orb=value.orb(), nativeWindow=orb.window;
  const a={project_id:'project-a',run_id:'wfr_'+'1'.repeat(32),node_id:'requirements',stage_session_id:'native:'+'a'.repeat(64)};
  const b={...a,node_id:'cases',stage_session_id:'native:'+'b'.repeat(64)};
  const last=()=>value.events.filter(row=>Array.isArray(row)&&row[1]==='eeas:workflow-conversation-open').at(-1)?.[2];
  const captures=()=>value.requests.filter(r=>r.url.endsWith('/context/observe')).length;
  const initial=captures();
  try{
    await value.invoke('eeas:workflow-orb-scope',a);
    assert.equal(orb.get().visible,true);
    await value.orbEvent({kind:'action',action:'ask'});
    assert.deepEqual(JSON.parse(JSON.stringify(last())),{...a,intent:'chat'});
    await value.invoke('eeas:workflow-orb-scope',b);
    await value.orbEvent({kind:'action',action:'annotate'});
    assert.deepEqual(JSON.parse(JSON.stringify(last())),{...b,intent:'annotate'});
    assert.equal(captures(),initial);
    assert.equal(orb.window,nativeWindow);
    await assert.rejects(value.invoke('eeas:workflow-orb-scope',{...b,path:'external'}),/Invalid workflow scope/);
    await value.invoke('eeas:workflow-orb-scope',null);
    assert.equal(orb.get().visible,false);
  }finally{
    await value.activity()?.close();
    fs.rmSync(value.dataDir,{recursive:true,force:true});
  }
});
