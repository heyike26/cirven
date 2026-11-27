'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {IdeCompanionBridge}=require('../../../apps/desktop-host/ide-companion-bridge.cjs');
const clientModule=require('../client.cjs');
// OS/VSCode path+SecretStorage/clock are explicit UNIT boundaries. Main bridge,
// stdlib HTTP and package/discovery/journal files are real; no model or GUI.
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function until(predicate){const end=Date.now()+10000;while(!predicate()){if(Date.now()>end)throw Error('fixture condition timeout');await delay(10);}}
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
async function installation(t,{id='a',approval=deferred(),onPair=()=>{}}={}){
  const root=fs.mkdtempSync(path.join(process.env.TMPDIR||os.tmpdir(),'eeas-pair-'));fs.mkdirSync(path.join(root,'runtime','python'),{recursive:true});fs.mkdirSync(path.join(root,'scripts'));fs.writeFileSync(path.join(root,'START_EEAS.cmd'),'fixture');fs.writeFileSync(path.join(root,'runtime','python','pythonw.exe'),'not executed');fs.writeFileSync(path.join(root,'scripts','native_package_bootstrap.py'),'parser.add_argument("--background")');fs.writeFileSync(path.join(root,'PACKAGE_MANIFEST.json'),JSON.stringify({ide_companion:{protocol:clientModule.PROTOCOL.protocol,background_bootstrap:true,discovery_relative_path:clientModule.PROTOCOL.discovery_relative_path,transport:'MAIN_OWNED_LOOPBACK_HTTP',external_agent_owner:'DESKTOP_EEAS_ONLY'}}));
  const calls=[],rpc=[];const bridge=new IdeCompanionBridge({dataDir:path.join(root,'user-data'),dispatch:async method=>{rpc.push(method);if(method==='status')return{connected:true,providers:[]};if(method==='projects.list')return{projects:[{id:'p'+id,name:'Project '+id}]};return{};},requestPairing:async request=>{onPair(request);return approval.promise;}});
  bridge.server.on('request',(req)=>calls.push({method:req.method,path:req.url}));await bridge.start();t.after(async()=>{approval.resolve({approved:false});await bridge.close();fs.rmSync(root,{recursive:true,force:true});});return{root,bridge,approval,calls,rpc,winRoot:'D:\\EEAS-'+id};
}
function fixture(installations,{clock=null,spawn=null}={}){
  let selected=installations[0].winRoot;const secrets=new Map(),posted=[],changes=[],source=fs.readFileSync(path.join(__dirname,'../extension.cjs'),'utf8'),mod={exports:{}};
  const lookup=root=>{const found=installations.find(x=>x.winRoot===root);if(!found)throw Error('unapproved fixture root');return found;};
  const vscode={env:{remoteName:undefined,language:'zh-CN'},workspace:{isTrusted:true,getConfiguration:()=>({get:key=>key==='installDirectory'?selected:''})},window:{}};
  const local={...clientModule,validateInstallation:root=>clientModule.validateInstallation(lookup(root).root),readDiscovery:root=>clientModule.readDiscovery(lookup(root).root)};
  const requireBoundary=id=>id==='vscode'?vscode:id==='./client.cjs'?local:id==='node:child_process'&&spawn?{spawn}:require(require.resolve(id,{paths:[path.join(__dirname,'..')]}));
  const ctx={process:{platform:'win32',env:{}},Buffer,setTimeout,clearTimeout,console};if(clock)ctx.Date=class extends Date{static now(){return clock.now;}};
  vm.runInNewContext(`(function(require,module,exports,__dirname){${source}\n})`,ctx)(requireBoundary,mod,mod.exports,path.join(__dirname,'..'));
  const sidebar=new mod.exports.Sidebar({globalStorageUri:{fsPath:path.join(installations[0].root,'vscode-private')},subscriptions:[],secrets:{get:async key=>secrets.get(key),store:async(key,value)=>secrets.set(key,value),delete:async key=>secrets.delete(key)}});
  sidebar.view={webview:{postMessage:m=>{posted.push(m);if(m.type==='state')changes.push(m.connection);}}};return{sidebar,secrets,posted,changes,choose:root=>{sidebar.cancelConnection({resetScope:true});selected=root;}};
}
test('background App startup passes -B before bootstrap and keeps the paired desktop launch route',async t=>{
  const {EventEmitter}=require('node:events');const i=await installation(t),calls=[];
  const f=fixture([i],{spawn(command,args,options){const child=new EventEmitter();child.unref=()=>{child.unreferenced=true;};
    calls.push({command,args,options,child});queueMicrotask(()=>child.emit('spawn'));return child;}});t.after(()=>f.sidebar.dispose());
  await f.sidebar.start();assert.equal(calls.length,1);assert.equal(calls[0].command,path.join(i.root,'runtime','python','pythonw.exe'));
  assert.deepEqual(Array.from(calls[0].args),['-B',path.join(i.root,'scripts','native_package_bootstrap.py'),'--electron','--background']);
  assert.equal(calls[0].options.shell,false);assert.equal(calls[0].options.detached,true);assert.equal(calls[0].child.unreferenced,true);
  assert.equal(f.sidebar.connection,'STARTING');assert.equal(i.calls.length,0);
});
test('first actual HTTP202 PENDING automatically becomes PAIRED using GET only and loads projects',async t=>{
  const i=await installation(t),f=fixture([i]);t.after(()=>f.sidebar.dispose());const connection=f.sidebar.connect(true);await until(()=>f.sidebar.connection==='PENDING');assert.equal(f.sidebar.connecting,true);assert.equal(i.calls.filter(c=>c.method==='POST'&&c.path==='/v1/pair').length,1);i.approval.resolve({approved:true});await connection;
  assert.equal(f.sidebar.connection,'PAIRED');assert.equal(f.sidebar.connecting,false);assert.deepEqual(f.sidebar.controller.projects.map(p=>p.id),['pa']);assert.ok(f.changes.includes('PENDING'));assert.ok(i.calls.some(c=>c.method==='GET'&&c.path==='/v1/pair/status'));assert.equal(i.calls.filter(c=>c.method==='POST'&&c.path==='/v1/pair').length,1);assert.deepEqual(i.rpc.sort(),['projects.list','status']);assert.equal(f.secrets.size,1);
});
test('dispose during first PENDING cancels its wait and closes client without replay or late reveal',async t=>{
  const i=await installation(t),f=fixture([i]);const connection=f.sidebar.connect(true);await until(()=>f.sidebar.connection==='PENDING');const client=f.sidebar.controller.client;f.sidebar.dispose();i.approval.resolve({approved:true});await connection;await delay(80);assert.equal(client.closed,true);assert.equal(f.sidebar.connectionWait,null);assert.equal(f.sidebar.connecting,false);assert.equal(i.calls.filter(c=>c.path==='/v1/pair').length,1);assert.equal(i.calls.filter(c=>c.path==='/v1/pair/status').length,0);assert.deepEqual(i.rpc,[]);
});
test('config switch interrupts old pairing and old approval cannot overwrite newer connection',async t=>{
  const a=await installation(t,{id:'a'}),b=await installation(t,{id:'b'}),f=fixture([a,b]);t.after(()=>f.sidebar.dispose());const old=f.sidebar.connect(true);await until(()=>f.sidebar.connection==='PENDING');const oldClient=f.sidebar.controller.client;f.choose(b.winRoot);const next=f.sidebar.connect(true);await until(()=>b.calls.some(c=>c.path==='/v1/pair'));a.approval.resolve({approved:true});b.approval.resolve({approved:true});await Promise.all([old,next]);assert.equal(oldClient.closed,true);assert.equal(f.sidebar.connection,'PAIRED');assert.deepEqual(f.sidebar.controller.projects.map(p=>p.id),['pb']);assert.equal(a.calls.filter(c=>c.method==='GET').length,0);assert.equal(a.calls.filter(c=>c.path==='/v1/pair').length,1);assert.equal(b.calls.filter(c=>c.path==='/v1/pair').length,1);
});
for(const terminal of ['DENIED','UNKNOWN'])test(`terminal ${terminal} ends active pairing without idle polling or another POST`,async t=>{
  const i=await installation(t),f=fixture([i]);t.after(()=>f.sidebar.dispose());const task=f.sidebar.connect(true);await until(()=>f.sidebar.connection==='PENDING');if(terminal==='DENIED')i.approval.resolve({approved:false});else i.approval.reject(Error('fixture unavailable'));await task;assert.equal(f.sidebar.connection,terminal);const count=i.calls.length;await delay(80);assert.equal(i.calls.length,count);assert.equal(i.calls.filter(c=>c.path==='/v1/pair').length,1);assert.equal(f.sidebar.connectionWait,null);
});
test('real revoked ticket during wait is terminal REVOKED with no implicit pairing',async t=>{
  let pairedId;const i=await installation(t,{onPair:r=>{pairedId=r.client_id;}}),f=fixture([i]);t.after(()=>f.sidebar.dispose());const task=f.sidebar.connect(true);await until(()=>f.sidebar.connection==='PENDING');i.bridge.revoke(pairedId);await task;assert.equal(f.sidebar.connection,'REVOKED');assert.equal(i.calls.filter(c=>c.path==='/v1/pair').length,1);assert.equal(f.sidebar.connectionWait,null);
});
test('absolute pending deadline ends UNKNOWN without looping or repeating first POST',async t=>{
  const clock={now:0},i=await installation(t,{onPair:()=>{clock.now=116000;}}),f=fixture([i],{clock});t.after(()=>f.sidebar.dispose());await f.sidebar.connect(true);assert.equal(f.sidebar.connection,'UNKNOWN');assert.equal(f.sidebar.connecting,false);assert.match(f.sidebar.notice,/超时/);assert.equal(i.calls.filter(c=>c.path==='/v1/pair').length,1);assert.equal(i.calls.filter(c=>c.path==='/v1/pair/status').length,0);
});
