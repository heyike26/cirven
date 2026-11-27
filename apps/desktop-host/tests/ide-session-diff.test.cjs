'use strict';
// UNIT transport boundary. Real Store/TCP API is covered by test_session_diffs.py.
const test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {createIdeApiAdapter}=require('../ide-api-adapter.cjs');
const scope={project_id:'project',session_ref:{id:'session',revision:1,sha256:'a'.repeat(64)},generation:3};
const workspace={id:'b'.repeat(64),revision:1,binding_sha256:'c'.repeat(64)};
const canonical=v=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
const sha=v=>crypto.createHash('sha256').update(v).digest('hex');
const snapshot={id:sha(canonical({...scope,workspace_ref:workspace})),revision:1,sha256:'d'.repeat(64)};
const diff={id:'e'.repeat(64),revision:1,sha256:'f'.repeat(64)};
const shared={scope:{...scope,workspace_ref:workspace},snapshot_ref:snapshot,read_only:true,model_calls:0,current_disk_compared:false,editor_dirty_state:'NOT_OBSERVED',file_hashes:'NOT_OBSERVED'};
const row={diff_ref:diff,path:'src/实际代码.py',status:'modified',source:'ORIGINAL_TOOL_FILEDIFF',message_id:'msg_one',part_id:'prt_one',call_id:'call_one',additions:1,deletions:1,content_scope:'HUNK_EXCERPTS',availability:'AVAILABLE',reason_code:null};
const directory=()=>({...structuredClone(shared),files:[structuredClone(row)],coverage:{source:'SAVED_ORIGINAL_ROOT_MESSAGE_OBSERVATION',complete:false,available:1,returned:1,reason_codes:[],private:'MUST_NOT_PROJECT'},observation_at:'2026-10-16T00:00:00Z',content_scope:'HUNK_EXCERPTS'});
const detail=()=>({...structuredClone(shared),...structuredClone(row),composition:'LOCATION_MARKED_HUNK_EXCERPTS',before:'@@ Historical before lines 1,1 @@\nold\n',after:'@@ Historical after lines 1,1 @@\nnew\n',patch:'--- a\n+++ a\n@@ -1 +1 @@\n-old\n+new\n',hunks:[{old_start:1,old_lines:1,new_start:1,new_lines:1,before:'old\n',after:'new\n'}]});
const params=(file=false)=>({scope,payload:{workspace_ref:workspace,...(file?{snapshot_ref:snapshot,diff_ref:diff}:{})}});

test('directory then explicit content issues only two exact readonly API requests',async()=>{
 const calls=[],dispatch=createIdeApiAdapter({request:async(url,body)=>{calls.push({url,body});return calls.length===1?directory():detail();}});
 const d=await dispatch('session.diff',params()),f=await dispatch('session.diff.file',params(true));
 assert.equal(calls.length,2);assert.ok(calls.every(x=>x.body===undefined));
 assert.ok(calls[0].url.startsWith('/api/projects/project/agent-sessions/session/diffs?'));
 const q=new URL('http://fixture'+calls[1].url).searchParams;assert.equal(q.get('generation'),'3');assert.equal(q.get('session_sha256'),scope.session_ref.sha256);assert.equal(q.get('binding_sha256'),workspace.binding_sha256);assert.equal(q.get('snapshot_sha256'),snapshot.sha256);assert.equal(q.get('diff_sha256'),diff.sha256);
 assert.equal(f.content_scope,'HUNK_EXCERPTS');assert.equal(f.before,detail().before);assert.equal(f.hunks[0].before,'old\n');assert.equal(f.current_disk_compared,false);assert.ok(!JSON.stringify(d).includes('MUST_NOT_PROJECT'));
});

test('scope, generation, workspace, snapshot and file changes reject without borrowing results',async()=>{
 for(const mutate of [v=>v.scope.project_id='foreign',v=>v.scope.generation++,v=>v.scope.workspace_ref.binding_sha256='0'.repeat(64),v=>v.snapshot_ref.id='1'.repeat(64)]){
  const d=directory();mutate(d);const dispatch=createIdeApiAdapter({request:async()=>d});await assert.rejects(dispatch('session.diff',params()),e=>['STALE_SESSION_SCOPE','DIFF_CHANGED'].includes(e.code));
 }
 const d=detail();d.diff_ref.sha256='0'.repeat(64);await assert.rejects(createIdeApiAdapter({request:async()=>d})('session.diff.file',params(true)),e=>e.code==='DIFF_CHANGED');
 let calls=0;const p=params(true);p.payload.snapshot_ref={...snapshot,id:'1'.repeat(64)};await assert.rejects(createIdeApiAdapter({request:async()=>{calls++;}})('session.diff.file',p),e=>e.code==='STALE_SESSION_SCOPE');assert.equal(calls,0);
});

test('NO_DATA remains honest; failed read and abort do not POST, replay or read files',async()=>{
 const d=directory();d.files=[];d.coverage.reason_codes=['NO_HISTORICAL_DIFF'];d.coverage.returned=0;
 assert.equal((await createIdeApiAdapter({request:async()=>d})('session.diff',params())).files.length,0);
 for(const status of [404,409,500]){let calls=0;const dispatch=createIdeApiAdapter({request:async(_url,body)=>{calls++;assert.equal(body,undefined);throw Object.assign(Error('sensitive raw path must not escape'),{status});}});await assert.rejects(dispatch('session.diff',params()),e=>e.public&&e.code===(status===409?'DIFF_CHANGED':'DIFF_UNAVAILABLE')&&!e.message.includes('sensitive'));assert.equal(calls,1);}
 const c=new AbortController();c.abort();let calls=0;await assert.rejects(createIdeApiAdapter({request:async()=>{calls++;}})('session.diff',{...params(),signal:c.signal}),e=>e.code==='REQUEST_CANCELLED');assert.equal(calls,0);
});

test('future FULL_FILE requires actual exact whole-text hashes, provenance and empty absent sides',async()=>{
 // Explicit future-producer fixture, not evidence a full-file producer exists.
 const full={...detail(),source:'AUTHORIZED_IMMUTABLE_FILE_SNAPSHOTS',source_generation:1,source_scope:{...scope,generation:1,workspace_ref:workspace},content_scope:'FULL_FILE',composition:'EXACT_SAVED_BEFORE_AFTER',encoding:'UTF-8',before:'\ufeffold\r\n',after:'\ufeff新\r\n'};
 full.before_sha256=sha(full.before);full.after_sha256=sha(full.after);
 const run=value=>createIdeApiAdapter({request:async()=>value})('session.diff.file',params(true));
 const result=await run(full);assert.equal(result.before,full.before);assert.equal(result.after_sha256,full.after_sha256);assert.ok(!('hunks'in result));
 assert.equal(result.scope.generation,3);assert.equal(result.source_generation,1);assert.equal(result.source_scope.generation,1);
 for(const source_scope of [{...full.source_scope,project_id:'foreign'},{...full.source_scope,generation:4},{...full.source_scope,workspace_ref:{...workspace,revision:2}}])await assert.rejects(run({...full,source_scope}),e=>e.code==='STALE_SESSION_SCOPE');
 for(const change of [{after:'tamper'},{source:'ORIGINAL_TOOL_FILEDIFF'},{status:'added'},{status:'deleted'},{encoding:'UTF-16'}])await assert.rejects(run({...full,...change}),e=>e.code==='INVALID_UPSTREAM_RESPONSE');
 const added={...full,status:'added',before:'',before_sha256:sha('')};assert.equal((await run(added)).status,'added');
 const huge={...full,before:'x'.repeat(524288)};huge.before_sha256=sha(huge.before);await assert.rejects(run(huge),e=>e.code==='RESPONSE_TOO_LARGE');
});

test('unavailable and unsafe directory entries never become usable diff content',async()=>{
 for(const path of ['/absolute.py','../escape.py','x/../a.py','file:///a.py','\\\\server\\a.py','.git/config']){const d=directory();d.files[0].path=path;await assert.rejects(createIdeApiAdapter({request:async()=>d})('session.diff',params()),e=>e.code==='INVALID_UPSTREAM_RESPONSE');}
 const d=detail();d.availability='TOO_LARGE';await assert.rejects(createIdeApiAdapter({request:async()=>d})('session.diff.file',params(true)),e=>e.code==='DIFF_CHANGED');
});

// Child transport UNIT. Native SDK, actual VSCode and Windows remain NOT_RUN.
function childScene(){const native={id:'ses_child',parentID:'ses_root',directory:'/original'},child_ref={id:native.id,root_native_session_id:native.parentID,sha256:sha(canonical(native))};
 const raw={project_id:scope.project_id,session_ref:scope.session_ref,generation:scope.generation,original_session_id:'ses_root',children:[native],messages:[],pending_questions:[],pending_permissions:[],todo:[],guidance:[],automatic_post_replay:false};
 const wrap=value=>({...value,child_ref,snapshot_ref:{...value.snapshot_ref,id:sha(canonical({scope:value.scope,child_ref}))}});
 const d=wrap(directory());d.coverage.child_observation={history_complete:false,partial:true,source:'SAVED_ORIGINAL_CHILD_EVENTS'};
 const f=wrap(detail());return {raw,child_ref,d,f};}
test('child historical diff uses exact parent and child references and returns only GET projections',async()=>{
 const x=childScene(),calls=[],dispatch=createIdeApiAdapter({request:async(url,body)=>{calls.push({url,body});return url.includes('/children/')?(new URL('http://unit'+url).searchParams.has('diff_id')?x.f:x.d):x.raw;}});
 const base={scope,payload:{workspace_ref:workspace,child_ref:x.child_ref}},d=await dispatch('session.child.diff',base);
 const f=await dispatch('session.child.diff.file',{scope,payload:{...base.payload,snapshot_ref:d.snapshot_ref,diff_ref:diff}});
 assert.equal(calls.length,4);assert.ok(calls.every(c=>c.body===undefined));assert.equal(f.before,x.f.before);assert.deepEqual(f.child_ref,x.child_ref);
 const query=new URL('http://unit'+calls.at(-1).url).searchParams;assert.equal(query.get('child_sha256'),x.child_ref.sha256);assert.equal(query.get('diff_id'),diff.id);assert.equal(query.get('snapshot_sha256'),x.d.snapshot_ref.sha256);
});
test('child snapshot namespaces and full write provenance cannot be borrowed from root or siblings',async()=>{
 const x=childScene(),base={scope,payload:{workspace_ref:workspace,child_ref:x.child_ref}};
 const run=value=>createIdeApiAdapter({request:async url=>url.includes('/children/')?value:x.raw})('session.child.diff',base);
 for(const delta of [{child_ref:{...x.child_ref,id:'ses_sibling'}},{snapshot_ref:snapshot}])await assert.rejects(run({...x.d,...delta}),e=>['CHILD_CHANGED','DIFF_CHANGED'].includes(e.code));
 const full={...x.d.files[0],source:'AUTHORIZED_IMMUTABLE_FILE_SNAPSHOTS',content_scope:'FULL_FILE',source_generation:1,source_scope:{...shared.scope,generation:1},source_native_session_id:x.child_ref.id,child_lineage_ref:{id:'1'.repeat(64),revision:1,sha256:'2'.repeat(64)}};
 const good={...x.d,files:[full]};assert.equal((await run(good)).files[0].source_native_session_id,x.child_ref.id);
 await assert.rejects(run({...good,files:[{...full,source_native_session_id:'ses_sibling'}]}),{code:'CHILD_CHANGED'});
 await assert.rejects(run({...good,files:[{...full,child_lineage_ref:null}]}));
 let calls=0;await assert.rejects(createIdeApiAdapter({request:async()=>{calls++;return {...x.raw,children:[]};}})('session.child.diff',base),{code:'CHILD_CHANGED'});assert.equal(calls,1);
});
