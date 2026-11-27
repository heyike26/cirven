'use strict';
// Client UNIT contracts: the original-task producer is tested by its own owner.
// No model, disk-history synthesis, live API, Windows or VSCode host claim here.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {hash}=require('../client.cjs');
const {ownerOf,ownerToken,validateDirectory,validateFile,entryToken,SnapshotDocuments,SCHEME}=require('../session-diff.cjs');
const {PrivateState,CompanionController,draftKey}=require('../controller.cjs');
const {fixture}=require('./diff-fixture.cjs');
const clone=v=>JSON.parse(JSON.stringify(v));
test('exact historical excerpts retain location markers; full texts verify real UTF8 hashes including BOM and CRLF',()=>{
 for(const full of [false,true])for(const status of ['modified','added','deleted']){const f=fixture({full,status});assert.deepEqual(validateDirectory(f.directory,f.scope),f.directory);assert.deepEqual(validateFile(f.file,f.scope,f.directory,f.entry),f.file);}
 const f=fixture({full:true});for(const delta of [{before:f.file.before+'x'},{after_sha256:'0'.repeat(64)},{composition:'LOCATION_MARKED_HUNK_EXCERPTS'},{encoding:'UTF-16'},{before:'\ud800',before_sha256:hash('\ud800')}])assert.throws(()=>validateFile({...f.file,...delta},f.scope,f.directory,f.entry),{code:'INVALID_DIFF_RESPONSE'});
});
test('cross-session/workspace/generation, invalid refs, oversized or unavailable text fail closed before native diff',()=>{
 const f=fixture();for(const scope of [{...f.scope,project_id:'other'},{...f.scope,generation:3},{...f.scope,workspace_ref:{...f.scope.workspace_ref,revision:4}}])assert.throws(()=>validateDirectory(f.directory,scope),{code:'INVALID_DIFF_RESPONSE'});
 for(const delta of [{snapshot_ref:{...f.file.snapshot_ref,sha256:'e'.repeat(64)}},{diff_ref:{...f.file.diff_ref,sha256:'f'.repeat(64)}},{path:'other.ts'},{read_only:false},{model_calls:1}])assert.throws(()=>validateFile({...f.file,...delta},f.scope,f.directory,f.entry),{code:'DIFF_CHANGED'});
 assert.throws(()=>validateFile({...f.file,patch:'x'.repeat(131073)},f.scope,f.directory,f.entry),{code:'INVALID_DIFF_RESPONSE'});
 assert.throws(()=>validateDirectory({...f.directory,files:Array(129).fill(f.entry)},f.scope),{code:'INVALID_DIFF_RESPONSE'});
 assert.throws(()=>validateDirectory({...f.directory,files:[f.entry,f.entry]},f.scope),{code:'INVALID_DIFF_RESPONSE'});
 assert.throws(()=>validateDirectory({...f.directory,files:[{...f.entry,availability:'BINARY'}]},f.scope),{code:'INVALID_DIFF_RESPONSE'});
 assert.throws(()=>ownerOf({...f.session,workspace_ref:null}),{code:'WORKSPACE_REQUIRED'});
});
test('readonly virtual documents are immutable snapshots with bounded eviction and strict URI/cancellation checks',()=>{
 const cache=new SnapshotDocuments({maxPairs:1}),a=fixture().file,uri=value=>{const u=new URL(value);return {scheme:u.protocol.slice(0,-1),authority:u.host,path:decodeURIComponent(u.pathname),query:u.search.slice(1),fragment:u.hash.slice(1)};};
 const refs=cache.add(a);assert.equal(cache.provide(uri(refs.before)),a.before);assert.equal(cache.provide(uri(refs.after)),a.after);a.before='user edited object';assert.notEqual(cache.provide(uri(refs.before)),a.before);
 assert.throws(()=>cache.provide({...uri(refs.after),scheme:'file'}),{code:'DIFF_SNAPSHOT_EXPIRED'});assert.throws(()=>cache.provide(uri(refs.after),{isCancellationRequested:true}),{code:'DIFF_CANCELLED'});
 cache.add({...fixture({full:true}).file,path:'new.ts'});assert.throws(()=>cache.provide(uri(refs.before)),{code:'DIFF_SNAPSHOT_EXPIRED'});assert.equal(cache.entries.size,1);cache.clear();assert.equal(cache.bytes,0);assert.equal(SCHEME,'cirven-session-diff');
});
function controller(t){const dir=fs.mkdtempSync(path.join(process.env.TMPDIR||os.tmpdir(),'cirven-diff-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));const c=new CompanionController({state:new PrivateState(dir)});c.selected=fixture().session;t.after(()=>c.dispose());return c;}
test('real controller passes exact readonly refs, uses host tokens and never changes the existing private draft',async t=>{
 const f=fixture(),c=controller(t),calls=[];c.client={close(){},rpc:async(method,scope,payload)=>{calls.push({method,scope,payload});return {status:'OK',result:method==='session.diff'?f.directory:f.file};}};const key=draftKey(c.selected);c.setDraft(key,0,'保留未发手改');await c.loadDiffs(ownerToken(c.selected));const d=c.snapshot().diffs;
 await assert.rejects(()=>c.readDiff(0,'a'.repeat(64)),{code:'DIFF_CHANGED'});assert.equal(calls.length,1);const value=await c.readDiff(0,d.files[0].token);assert.deepEqual(value,f.file);assert.deepEqual(calls[1].payload,{workspace_ref:f.scope.workspace_ref,snapshot_ref:f.directory.snapshot_ref,diff_ref:f.entry.diff_ref});assert.equal(c.snapshot().draft.text,'保留未发手改');assert.equal(c.state.value.requests.length,0);assert.equal(calls.every(v=>['session.diff','session.diff.file'].includes(v.method)),true);
});
test('late directory or file response cannot replace or reveal the newly selected session; refresh invalidates old list tokens',async t=>{
 const f=fixture(),c=controller(t);let finish;c.client={close(){},rpc:async()=>new Promise(r=>finish=r)};const pending=c.loadDiffs(ownerToken(c.selected));c.epoch++;c.selected={...f.session,generation:3};c.snapshot();finish({status:'OK',result:f.directory});await pending;assert.equal(c.snapshot().diffs.loaded,false);
 c.selected=f.session;c.client.rpc=async()=>({status:'OK',result:f.directory});await c.loadDiffs(ownerToken(c.selected));const token=c.snapshot().diffs.files[0].token;c.client.rpc=async()=>new Promise(r=>finish=r);const file=c.readDiff(0,token);c.selected={...f.session,workspace_ref:{...f.session.workspace_ref,revision:4}};c.snapshot();finish({status:'OK',result:f.file});assert.equal(await file,null);assert.equal(c.snapshot().diffs.loaded,false);
 c.selected=f.session;c.client.rpc=async()=>({status:'OK',result:f.directory});await c.loadDiffs(ownerToken(c.selected));const newer=clone(f.directory);newer.snapshot_ref.sha256='e'.repeat(64);c.client.rpc=async()=>({status:'OK',result:newer});await c.loadDiffs(ownerToken(c.selected));await assert.rejects(()=>c.readDiff(0,token),{code:'DIFF_CHANGED'});
});
test('concurrent click/refresh is bounded; errors do not retry or promote missing evidence to full files',async t=>{
 const f=fixture(),c=controller(t);let calls=0,finish;c.client={close(){},rpc:async()=>{calls++;return new Promise(r=>finish=r);}};const pending=c.loadDiffs(ownerToken(c.selected));await c.loadDiffs(ownerToken(c.selected));assert.equal(calls,1);finish({status:'OK',result:{...f.directory,files:[],coverage:{...f.directory.coverage,reason_codes:['NO_HISTORICAL_DIFF']}}});await pending;assert.equal(c.snapshot().diffs.files.length,0);
 c.client.rpc=async()=>{calls++;return {status:'ERROR',error:{code:'CAPABILITY_DENIED'}};};await c.loadDiffs(ownerToken(c.selected));assert.equal(c.snapshot().diffs.error,'CAPABILITY_DENIED');assert.equal(calls,2);assert.equal(c.snapshot().diffs.loaded,false);
});
test('rapid repeated file click issues one read and late old-scope failure leaves the new session quiet',async t=>{
 const f=fixture(),c=controller(t);c.client={close(){},rpc:async()=>({status:'OK',result:f.directory})};await c.loadDiffs(ownerToken(c.selected));let calls=0,reject;const token=c.snapshot().diffs.files[0].token;c.client.rpc=async()=>{calls++;return new Promise((_resolve,r)=>reject=r);};const first=c.readDiff(0,token);assert.equal(await c.readDiff(0,token),null);assert.equal(calls,1);assert.equal(c.snapshot().diffs.error,null);c.epoch++;c.selected={...f.session,generation:3};c.snapshot();reject(Object.assign(Error('old unavailable'),{code:'ECONNRESET'}));assert.equal(await first,null);assert.equal(c.snapshot().diffs.error,null);
});
test('total diff transport is single-flight across repeated scope changes, with no pending queue or automatic follow-up',async t=>{
 const f=fixture(),c=controller(t);let calls=0,finish;c.client={close(){},rpc:async()=>{calls++;return new Promise(r=>finish=r);}};const first=c.loadDiffs(ownerToken(c.selected));for(let generation=3;generation<30;generation++){c.epoch++;c.selected={...f.session,generation};assert.equal(c.snapshot().diffs.loading,true);await c.loadDiffs(ownerToken(c.selected));}assert.equal(calls,1);finish({status:'OK',result:f.directory});await first;assert.equal(c.snapshot().diffs.loaded,false);assert.equal(c.snapshot().diffs.loading,false);await Promise.resolve();assert.equal(calls,1,'scope changes never enqueue a future read');
 const current={...f.directory,scope:ownerOf(c.selected)};c.client.rpc=async()=>{calls++;return {status:'OK',result:current};};await c.loadDiffs(ownerToken(c.selected));assert.equal(calls,2);assert.equal(c.snapshot().diffs.loaded,true);
});
test('FULL history retains exact source generation without promoting it to the currently loaded owner',()=>{
 const f=fixture({full:true,sourceGeneration:1});assert.deepEqual(validateDirectory(f.directory,f.scope),f.directory);assert.deepEqual(validateFile(f.file,f.scope,f.directory,f.entry),f.file);assert.equal(f.file.scope.generation,2);assert.equal(f.file.source_scope.generation,1);
 const invalid=[{source_generation:0},{source_generation:3,source_scope:{...f.scope,generation:3}},{source_generation:1,source_scope:{...f.scope,generation:2}},{source_scope:{...f.entry.source_scope,project_id:'other'}},{source_scope:{...f.entry.source_scope,session_ref:{...f.scope.session_ref,sha256:'0'.repeat(64)}}},{source_scope:{...f.entry.source_scope,workspace_ref:{...f.scope.workspace_ref,revision:4}}},{source_scope:{...f.entry.source_scope,path_grants:['all']}}];
 for(const delta of invalid)assert.throws(()=>validateDirectory({...f.directory,files:[{...f.entry,...delta}]},f.scope),{code:'INVALID_DIFF_RESPONSE'});
 for(const missing of ['source_generation','source_scope']){const bad=clone(f.entry);delete bad[missing];assert.throws(()=>validateDirectory({...f.directory,files:[bad]},f.scope),{code:'INVALID_DIFF_RESPONSE'});}
 const current=fixture({full:true,sourceGeneration:2});assert.throws(()=>validateFile(current.file,f.scope,f.directory,f.entry),{code:'DIFF_CHANGED'});
 const hunk=fixture();assert.throws(()=>validateDirectory({...hunk.directory,files:[{...hunk.entry,source_generation:1,source_scope:{...hunk.scope,generation:1}}]},hunk.scope),{code:'INVALID_DIFF_RESPONSE'});
});
test('origin metadata remains host-owned, binds list/cache identities and cannot change across a file response',async t=>{
 const old=fixture({full:true,sourceGeneration:1}),current=fixture({full:true,sourceGeneration:2}),c=controller(t);c.selected=old.session;c.client={close(){},rpc:async method=>({status:'OK',result:method==='session.diff'?old.directory:current.file})};await c.loadDiffs(ownerToken(c.selected));const meta=c.snapshot().diffs.files[0];assert.deepEqual(meta.source_scope,old.entry.source_scope);assert.equal(meta.source_generation,1);assert.notEqual(entryToken(old.scope,old.directory,old.entry),entryToken(current.scope,current.directory,current.entry));await assert.rejects(()=>c.readDiff(0,meta.token),{code:'DIFF_CHANGED'});
 const cache=new SnapshotDocuments(),before=cache.add(old.file),after=cache.add(current.file);assert.notEqual(before.before,after.before);assert.deepEqual([...cache.entries.values()][0].provenance.source_scope,old.entry.source_scope);assert.equal([...cache.entries.values()][0].provenance.source_generation,1);assert.equal(cache.entries.size,2);
});
