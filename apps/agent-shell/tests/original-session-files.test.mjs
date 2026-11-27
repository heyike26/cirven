import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {ApiClient} from '../src/api.ts';
import {OriginalSessionFilesApi,originalFileScope,originalFilesObservation,parseOriginalFileCatalog} from '../src/original-session-files.ts';

const hash=v=>createHash('sha256').update(v).digest('hex'),ref={id:'a'.repeat(32),revision:1,sha256:'b'.repeat(64)},w={id:'c'.repeat(64),revision:1,binding_sha256:'d'.repeat(64)};
const session=()=>({project_id:'project',session_ref:ref,generation:2,workspace_ref:w,original_session_id:'ses_root',active_operation_id:null,pending_permissions:[],pending_questions:[],messages:[{info:{id:'assistant',role:'assistant',sessionID:'ses_root'},parts:[{id:'part',type:'tool',tool:'apply_patch',callID:'call',state:{status:'completed'}}]}]});
const row=(patch={})=>({diff_ref:{id:'e'.repeat(64),revision:1,sha256:'f'.repeat(64)},path:'交付/测试 😀.txt',status:'added',source:'AUTHORIZED_IMMUTABLE_FILE_SNAPSHOTS',message_id:'assistant',part_id:'part',call_id:'call',content_scope:'FULL_FILE',availability:'AVAILABLE',source_generation:2,source_scope:originalFileScope(session()),...patch});
const raw=(files=[row()],patch={})=>({scope:originalFileScope(session()),snapshot_ref:{id:'1'.repeat(64),revision:1,sha256:'2'.repeat(64)},files,read_only:true,model_calls:0,...patch});

test('only exact completed admitted root files are offered; metadata never becomes a file grant',()=>{
 const s=session(),value=parseOriginalFileCatalog(raw([row(),row({content_scope:'HUNK_EXCERPTS',path:'unproven.txt'})]),s);assert.equal(value.files.length,1);assert.equal(value.files[0].path,'交付/测试 😀.txt');
 for(const patch of [{scope:{...raw().scope,project_id:'other'}},{read_only:false},{model_calls:1},{snapshot_ref:{...raw().snapshot_ref,sha256:'bad'}}])assert.throws(()=>parseOriginalFileCatalog(raw(undefined,patch),s));
 for(const patch of [{path:'../private'},{path:'D:\\private.txt'},{path:'/private'},{path:'a//b'},{message_id:'another'},{part_id:'other'},{call_id:'other'},{source_generation:3},{source_scope:{...row().source_scope,project_id:'other'}}])assert.throws(()=>parseOriginalFileCatalog(raw([row(patch)]),s));
 const old=row({source_generation:1,source_scope:{...row().source_scope,generation:1}});assert.equal(parseOriginalFileCatalog(raw([old]),s).files.length,1);
 assert.equal(Object.hasOwn(value,'allowed_effects'),false);
});

test('later supported deletion removes the earlier file link; one current entry per path',()=>{
 const s=session();s.messages.push({info:{id:'later',role:'assistant',sessionID:'ses_root'},parts:[{id:'p2',type:'tool',tool:'edit',callID:'c2',state:{status:'completed'}}]});
 const later=row({message_id:'later',part_id:'p2',call_id:'c2',status:'modified',diff_ref:{...row().diff_ref,id:'3'.repeat(64)}});
 assert.deepEqual(parseOriginalFileCatalog(raw([row(),later]),s).files,[later]);
 assert.deepEqual(parseOriginalFileCatalog(raw([row(),{...later,status:'deleted'}]),s).files,[]);
});

test('idle observation has a stable read key; active and read-only sessions do not scan files',()=>{
 const s=session(),key=originalFilesObservation(s);assert(key);
 assert.equal(originalFilesObservation({...s,observation_at:'later',last_event_seq:90}),key);
 assert.equal(originalFilesObservation({...s,active_operation_id:'working'}),undefined);
 assert.equal(originalFilesObservation({...s,pending_permissions:[{}]}),undefined);
 assert.equal(originalFilesObservation({...s,workspace_ref:null}),undefined);
 assert.equal(originalFilesObservation({...s,messages:[]}),undefined);
 assert.notEqual(originalFilesObservation({...s,generation:3}),key);
});

function wire({later=false,detailPatch={},filePatch={}}={}){
 const catalog=parseOriginalFileCatalog(raw(),session()),file=catalog.files[0],before='generated\n',current=later?'USER EDIT MUST SURVIVE\n':before,requests=[];
 const detail={...row(),scope:catalog.scope,snapshot_ref:catalog.snapshot_ref,after:before,after_sha256:hash(before),read_only:true,model_calls:0,...detailPatch};
 const content={source_ref:{kind:'local_file',project_id:'project',workspace_id:w.id,binding_revision:1,binding_sha256:w.binding_sha256,path:file.path,revision:later?2:1,sha256:hash(current)},content:current,encoding:'utf-8',byte_length:Buffer.byteLength(current),current:true,live_sha256:hash(current),...filePatch};
 const api=new OriginalSessionFilesApi(new ApiClient(async(url,init)=>{if(url==='/api/bootstrap')return Response.json({csrf_token:'local'});requests.push({url,method:init.method});return Response.json(url.includes('/diffs/')?detail:url.includes('/file?')?content:raw());}));
 return{api,catalog,file,requests,current};
}

test('opening reads immutable evidence and exact current bytes, without POST or model requests',async()=>{
 const f=wire();assert.equal((await f.api.list(session())).files.length,1);
 const result=await f.api.read(f.catalog,f.file);assert.equal(result.changed,false);assert.equal(result.file.content,f.current);
 assert.equal(f.requests.length,3);assert(f.requests.every(r=>r.method==='GET'));
 assert(f.requests[1].url.includes('snapshot_sha256=')&&f.requests[1].url.includes('diff_sha256='));
 const changed=wire({later:true}),later=await changed.api.read(changed.catalog,changed.file);assert.equal(later.changed,true);assert.equal(later.file.content,'USER EDIT MUST SURVIVE\n');assert(changed.requests.every(r=>r.method==='GET'));
});

test('changed catalog, corrupt snapshot, another workspace, or stale leaf fails before any open',async()=>{
 for(const patch of [{detailPatch:{after:'CORRUPTED'}},{detailPatch:{scope:{...raw().scope,generation:3}}},{detailPatch:{diff_ref:{...row().diff_ref,sha256:'0'.repeat(64)}}}]){
  const f=wire(patch);await assert.rejects(()=>f.api.read(f.catalog,f.file));assert.equal(f.requests.length,1);
 }
 for(const patch of [{current:false},{live_sha256:'0'.repeat(64)},{source_ref:{kind:'local_file',project_id:'other'}}]){const f=wire({filePatch:patch});await assert.rejects(()=>f.api.read(f.catalog,f.file));assert.equal(f.requests.length,2);}
 const f=wire();await assert.rejects(()=>f.api.read(f.catalog,{...f.file,path:'another'}));assert.equal(f.requests.length,0);
});
