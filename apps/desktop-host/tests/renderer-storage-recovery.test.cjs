'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),vm=require('node:vm');
const {recoverRendererStorage,candidates,checkedRows,importExpression,selectSource,validOrigin}=require('../renderer-storage-recovery.cjs');
function fixture(t){const root=fs.mkdtempSync(path.join(os.tmpdir(),'cirven-origin-recovery-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));const proof={schema_version:'eeas.restore-provenance.v1',target_data_dir:root,snapshot_sha256:'a'.repeat(64),snapshot_id:'b'.repeat(32)};fs.writeFileSync(path.join(root,'restore-provenance.json'),JSON.stringify(proof));return {root,proof};}
class Storage{
  constructor(rows=[]){this.values=new Map(rows);this.writes=0;this.failAfter=Infinity;}
  get length(){return this.values.size;}key(index){return [...this.values.keys()][index];}getItem(key){return this.values.has(key)?this.values.get(key):null;}
  setItem(key,value){if(this.writes>=this.failAfter)throw Error('quota fixture');this.values.set(key,value);this.writes++;}
}
function chromiumBoundary(stores){let active,created=0,destroyed=0;const handlers=new Map();const ses={flushStorageData(){},protocol:{handle(scheme,fn){assert(!handlers.has(scheme));handlers.set(scheme,fn);},unhandle(scheme){handlers.delete(scheme);}}};
  class Window{
    constructor(options){assert.equal(options.show,false);assert.equal(options.webPreferences.nodeIntegration,false);assert.equal(options.webPreferences.session,ses);created++;this.dead=false;this.webContents={setWindowOpenHandler(){},on(){},executeJavaScript:async expression=>vm.runInNewContext(expression,{location:{origin:active},localStorage:stores.get(active),TextEncoder})};}
    async loadURL(url){active=new URL(url).origin;if(!stores.has(active))stores.set(active,new Storage());const response=await handlers.get('http')({url,method:'GET'});assert.equal(response.status,200);}
    isDestroyed(){return this.dead;}destroy(){this.dead=true;destroyed++;}
  }return {ses,BrowserWindow:Window,handlers,counts:()=>({created,destroyed})};
}
test('loopback recovery cannot accept a privileged port, remote host or URL with credentials/path',()=>{
  for(const origin of ['http://127.0.0.1:1000','http://127.0.0.1:65536','https://127.0.0.1:2526','http://localhost:2526','http://127.0.0.1:2526/','http://user@127.0.0.1:2526'])assert.equal(validOrigin(origin),false);
  assert.equal(validOrigin('http://127.0.0.1:2526'),true);
});
test('legacy origin candidates are bounded local file hints; linked storage and ambiguous real origins refuse',t=>{
  const {root,proof}=fixture(t),dir=path.join(root,'session','Local Storage','leveldb');fs.mkdirSync(dir,{recursive:true});const file=path.join(dir,'000001.log');fs.writeFileSync(file,'META:http://127.0.0.1:2526');assert.deepEqual(candidates(root,proof),['http://127.0.0.1:2526']);
  assert.throws(()=>selectSource([{origin:'http://127.0.0.1:2526',rows:[['eeas-x','a']]},{origin:'http://127.0.0.1:3526',rows:[['eeas-x','b']]}],'http://127.0.0.1:3984'));
  const other=path.join(root,'other');fs.writeFileSync(other,'x');fs.unlinkSync(file);fs.symlinkSync(other,file);assert.throws(()=>candidates(root,proof));assert.equal(fs.readFileSync(other,'utf8'),'x');
});
test('a conflicting existing destination is fully preflighted; no draft is overwritten or partially inserted',()=>{
  const storage=new Storage([['eeas-b','user edit']]);assert.throws(()=>vm.runInNewContext(importExpression([['eeas-a','new'],['eeas-b','old']],'http://127.0.0.1:3984'),{location:{origin:'http://127.0.0.1:3984'},localStorage:storage}),/CONFLICT/);assert.equal(storage.writes,0);assert.equal(storage.getItem('eeas-a'),null);assert.equal(storage.getItem('eeas-b'),'user edit');
  assert.throws(()=>checkedRows([['eeas-a','x'],['eeas-a','y']]));assert.throws(()=>checkedRows([['unowned','x']]));assert.throws(()=>checkedRows([['eeas-a','x'.repeat(2*1024*1024+1)]]));
});
test('different-origin restoration preserves opaque unknown/control drafts and user edits on restart',async t=>{
  const {root,proof}=fixture(t);proof.renderer_source_origin='http://127.0.0.1:2526';fs.writeFileSync(path.join(root,'restore-provenance.json'),JSON.stringify(proof));
  const rows=[['eeas-conversation-draft-v1:original','{"text":"未提交 😀\\n原话","attachments":[]}'],['eeas-original-resume-v1:original','{"unknown":"do not replay"}']];const source=new Storage(rows),target=new Storage(),stores=new Map([[proof.renderer_source_origin,source],['http://127.0.0.1:3984',target]]),boundary=chromiumBoundary(stores);
  const value=await recoverRendererStorage({dataDir:root,origin:'http://127.0.0.1:3984',...boundary});assert.equal(value.keys,2);assert.deepEqual([...source.values],rows);assert.deepEqual([...target.values],rows);assert.equal(source.writes,0);assert.equal(boundary.handlers.size,0);assert.deepEqual(boundary.counts(),{created:1,destroyed:1});
  target.setItem(rows[0][0],'later manual draft');const repeated=await recoverRendererStorage({dataDir:root,origin:'http://127.0.0.1:3984',...boundary});assert.equal(repeated.status,'ALREADY_RESTORED');assert.equal(target.getItem(rows[0][0]),'later manual draft');assert.equal(boundary.counts().created,1);
});
test('same-origin restoration preserves existing bytes; a copied receipt cannot authorize another snapshot or port',async t=>{
  const {root,proof}=fixture(t);proof.renderer_source_origin='http://127.0.0.1:2526';fs.writeFileSync(path.join(root,'restore-provenance.json'),JSON.stringify(proof));const original='{"process_start_consent":true,"raw":"historical bytes"}',storage=new Storage([['eeas-native-review-draft:old',original]]),boundary=chromiumBoundary(new Map([[proof.renderer_source_origin,storage]]));
  const options={dataDir:root,origin:proof.renderer_source_origin,...boundary};await recoverRendererStorage(options);assert.equal(storage.writes,0);assert.equal(storage.getItem('eeas-native-review-draft:old'),original);
  await assert.rejects(recoverRendererStorage({...options,origin:'http://127.0.0.1:3984'}),/UNAVAILABLE/);assert.equal(boundary.counts().created,1);
  proof.snapshot_sha256='c'.repeat(64);fs.writeFileSync(path.join(root,'restore-provenance.json'),JSON.stringify(proof));await assert.rejects(recoverRendererStorage(options),/UNAVAILABLE/);assert.equal(storage.writes,0);
});
test('quota failure keeps source, publishes no completion receipt, and a later attempt only fills missing identical rows',async t=>{
  const {root,proof}=fixture(t);proof.renderer_source_origin='http://127.0.0.1:2526';fs.writeFileSync(path.join(root,'restore-provenance.json'),JSON.stringify(proof));const rows=[['eeas-a','first'],['eeas-b','second']],source=new Storage(rows),target=new Storage();target.failAfter=1;const stores=new Map([[proof.renderer_source_origin,source],['http://127.0.0.1:3984',target]]),boundary=chromiumBoundary(stores),options={dataDir:root,origin:'http://127.0.0.1:3984',...boundary};
  await assert.rejects(recoverRendererStorage(options),/quota/);assert.equal(boundary.handlers.size,0);assert.deepEqual([...source.values],rows);assert(!fs.existsSync(path.join(root,'renderer-storage-restorations',proof.snapshot_id+'.json')));assert.equal(target.writes,1);
  target.failAfter=Infinity;await recoverRendererStorage(options);assert.equal(target.writes,2);assert.deepEqual([...target.values],rows);
});
test('fresh installations do not create windows, intercept protocols or touch browser storage',async t=>{
  const {root}=fixture(t);fs.unlinkSync(path.join(root,'restore-provenance.json'));const boundary=chromiumBoundary(new Map());assert.equal((await recoverRendererStorage({dataDir:root,origin:'http://127.0.0.1:3984',...boundary})).status,'NOT_A_RESTORED_INSTALLATION');assert.equal(boundary.counts().created,0);assert.equal(boundary.handlers.size,0);
});

test('exact experience journal prefix retains unknown bytes and its eight MiB bound without broadening other keys',()=>{
 const key='cirven-task-experience-v1:{"original":"work"}',value='{"phase":"UNKNOWN","note":"😀"}';
 assert.deepEqual(checkedRows([[key,value]]),[[key,value]]);
 const large='x'.repeat(3*1024*1024);assert.equal(checkedRows([[key,large]])[0][1],large);
 assert.throws(()=>checkedRows([['cirven-unrelated-private',value]]));assert.throws(()=>checkedRows([['eeas-old-bound',large]]));
 assert.throws(()=>checkedRows([[key,'x'.repeat(8*1024*1024+1)]]));
});
test('experience unknown journal import preserves original bytes and refuses conflicting target input',()=>{
 const origin='http://127.0.0.1:8108',key='cirven-task-experience-v1:owned',value=' {"phase":"UNKNOWN","input":"手工 😀"} ',storage=new Storage();
 assert.equal(vm.runInNewContext(importExpression([[key,value]],origin),{location:{origin},localStorage:storage}),1);assert.equal(storage.getItem(key),value);
 assert.throws(()=>vm.runInNewContext(importExpression([[key,'different']],origin),{location:{origin},localStorage:storage}));assert.equal(storage.getItem(key),value);
});
test('restored origin export includes exact experience and allowance namespaces before importing',async t=>{
 const {root,proof}=fixture(t),source='http://127.0.0.1:2526',target='http://127.0.0.1:3526';
 proof.renderer_source_origin=source;fs.writeFileSync(path.join(root,'restore-provenance.json'),JSON.stringify(proof));
 const rows=[['cirven-task-experience-v1:owned','{"phase":"UNKNOWN"}'],['eeas-resource-allowance-v1:owned','{"phase":"PENDING"}']],stores=new Map([[source,new Storage(rows)]]),boundary=chromiumBoundary(stores);
 await recoverRendererStorage({dataDir:root,origin:target,...boundary});
 for(const [key,value]of rows)assert.equal(stores.get(target).getItem(key),value);
});

test('workflow drafts, request identity and reading position survive an origin change without importing other namespaces',async t=>{
 const {root,proof}=fixture(t);
 const source='http://127.0.0.1:2526',target='http://127.0.0.1:3526';
 proof.renderer_source_origin=source;
 fs.writeFileSync(path.join(root,'restore-provenance.json'),JSON.stringify(proof));
 const view='cirven-stage-view-v1:'+JSON.stringify(['project','stage-session']);
 const draft='cirven-stage-input-v1:'+JSON.stringify(['project','owner','run','node','stage-session']);
 const rows=[
  [view,'workflow'],
  [view+':pending-transition',' {"operation_id":"original","plan":{"kind":"approve"}} '],
  [draft,'{"input":{},"note":"未提交的本步笔记 😀"}'],
  ['cirven-workflow-reading-v1','[{"operation":"recorded","row":"OR-006","top":1105}]'],
 ];
 const unrelated=[['cirven-stage-view-v2:other','keep original only'],['cirven-workflow-reading-v1:other','not a saved position']];
 const before=new Storage([...rows,...unrelated]),after=new Storage([[draft,'user has a newer draft']]);
 const boundary=chromiumBoundary(new Map([[source,before],[target,after]]));
 const options={dataDir:root,origin:target,...boundary};
 await assert.rejects(recoverRendererStorage(options),/RESTORE_DRAFT_CONFLICT/);
 assert.equal(after.writes,0);
 after.values.delete(draft);
 assert.equal((await recoverRendererStorage(options)).keys,rows.length);
 assert.deepEqual([...before.values],[...rows,...unrelated]);
 assert.deepEqual([...after.values],rows);
 assert.equal(before.writes,0);
});
