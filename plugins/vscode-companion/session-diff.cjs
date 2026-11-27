'use strict';
const {canonical,hash,validateScope}=require('./client.cjs');
const {validRef}=require('./session-child.cjs');
const SCHEME='cirven-session-diff',MAX_BYTES=524288,MAX_PATCH_BYTES=131072;
function fail(code='INVALID_DIFF_RESPONSE'){throw Object.assign(new Error(code),{code});}
function object(v){return v&&typeof v==='object'&&!Array.isArray(v);}
function ref(v){if(!object(v)||Object.keys(v).sort().join(',')!=='id,revision,sha256'||!/^[a-f0-9]{64}$/.test(v.id)||v.revision!==1||!/^[a-f0-9]{64}$/.test(v.sha256))fail();return v;}
function workspace(v){if(!object(v)||Object.keys(v).sort().join(',')!=='binding_sha256,id,revision'||!/^[a-f0-9]{64}$/.test(v.id)||!Number.isSafeInteger(v.revision)||v.revision<1||!/^[a-f0-9]{64}$/.test(v.binding_sha256))fail('WORKSPACE_REQUIRED');return v;}
function ownerOf(session){const scope=validateScope({project_id:session.project_id,session_ref:session.session_ref,generation:session.generation});if(!scope.project_id||!scope.session_ref||scope.generation===undefined)fail('INVALID_SCOPE');return {...scope,workspace_ref:workspace(session.workspace_ref)};}
function ownerToken(session){try{return hash(canonical(ownerOf(session)));}catch{return null;}}
function text(v,max=MAX_BYTES){if(typeof v!=='string'||v.includes('\0')||Buffer.byteLength(v,'utf8')>max)fail();return v;}
function bounded(v){if(!object(v)||Buffer.byteLength(JSON.stringify(v),'utf8')>MAX_BYTES)fail();}
function same(a,b){return canonical(a)===canonical(b);}
function validateEntry(v){if(!object(v)||!ref(v.diff_ref)||!text(v.path,1024)||/[\\:\x00-\x1f\x7f]/.test(v.path)||v.path.startsWith('/')||v.path.split('/').some(p=>!p||p==='.'||p==='..'||['.git','.eeas-runtime','.eeas-sdk-state'].includes(p.toLowerCase()))||!['modified','added','deleted','UNKNOWN'].includes(v.status)||!['AVAILABLE','BINARY','TOO_LARGE','UNAVAILABLE'].includes(v.availability)||!['HUNK_EXCERPTS','FULL_FILE','UNAVAILABLE'].includes(v.content_scope)||!['ORIGINAL_TOOL_FILEDIFF','ORIGINAL_MESSAGE_SUMMARY','ORIGINAL_TOOL_METADATA','AUTHORIZED_IMMUTABLE_FILE_SNAPSHOTS'].includes(v.source)||['additions','deletions'].some(key=>v[key]!=null&&(!Number.isSafeInteger(v[key])||v[key]<0||v[key]>10000000)))fail();
  if((v.availability==='AVAILABLE')!==['HUNK_EXCERPTS','FULL_FILE'].includes(v.content_scope))fail();return v;}
function validateSource(v,owner,child=null){
  if(child&&v.content_scope==='FULL_FILE'){if(v.source_native_session_id!==child.id||!ref(v.child_lineage_ref))fail();}
  else if(v.source_native_session_id!==undefined||v.child_lineage_ref!==undefined)fail();const present='source_generation' in v||'source_scope' in v;
  // Legacy hunks have no attributable turn generation. Never synthesize one.
  if(v.content_scope==='HUNK_EXCERPTS'){if(present)fail();return;}
  if(v.content_scope!=='FULL_FILE'&&!present)return;
  if(v.source!=='AUTHORIZED_IMMUTABLE_FILE_SNAPSHOTS'||!Number.isSafeInteger(v.source_generation)||v.source_generation<1||v.source_generation>owner.generation||!object(v.source_scope)||Object.keys(v.source_scope).sort().join(',')!=='generation,project_id,session_ref,workspace_ref')fail();
  const expected={...owner,generation:v.source_generation};if(!same(v.source_scope,expected))fail();
}
function validateChild(value,owner,child){
  if(child){if(!validRef(child)||!same(value.child_ref,child)||value.snapshot_ref?.id!==hash(canonical({scope:owner,child_ref:child})))fail('CHILD_CHANGED');}
  else if(value.child_ref!==undefined)fail('CHILD_CHANGED');
}
function validateDirectory(value,owner,child=null){bounded(value);validateChild(value,owner,child);if(!same(value.scope,owner)||!ref(value.snapshot_ref)||!Array.isArray(value.files)||value.files.length>128||!object(value.coverage)||typeof value.coverage.complete!=='boolean'||typeof value.coverage.truncated!=='boolean'||value.read_only!==true||value.current_disk_compared!==false||value.editor_dirty_state!=='NOT_OBSERVED'||value.model_calls!==0)fail();
  const ids=new Set();for(const file of value.files){validateEntry(file);validateSource(file,owner,child);if(ids.has(file.diff_ref.id))fail();ids.add(file.diff_ref.id);}return JSON.parse(JSON.stringify(value));}
function validateFile(value,owner,directory,entry){bounded(value);const child=directory.child_ref??null;validateChild(value,owner,child);validateEntry(value);if(!same(value.scope,owner)||!same(ref(value.snapshot_ref),directory.snapshot_ref)||!same(ref(value.diff_ref),entry.diff_ref)||['path','status','source','availability','content_scope'].some(key=>value[key]!==entry[key])||value.availability!=='AVAILABLE'||value.read_only!==true||value.model_calls!==0||value.current_disk_compared!==false||value.editor_dirty_state!=='NOT_OBSERVED')fail('DIFF_CHANGED');
  validateSource(value,owner,child);if(!same({generation:value.source_generation,scope:value.source_scope,native:value.source_native_session_id,lineage:value.child_lineage_ref},{generation:entry.source_generation,scope:entry.source_scope,native:entry.source_native_session_id,lineage:entry.child_lineage_ref}))fail('DIFF_CHANGED');
  text(value.before);text(value.after);if(Buffer.byteLength(value.before)+Buffer.byteLength(value.after)>MAX_BYTES)fail();
  if(value.content_scope==='HUNK_EXCERPTS'){
    if(value.composition!=='LOCATION_MARKED_HUNK_EXCERPTS'||!Array.isArray(value.hunks)||!value.hunks.length||value.hunks.length>128)fail();text(value.patch,MAX_PATCH_BYTES);
    for(const hunk of value.hunks){if(!object(hunk)||['old_start','old_lines','new_start','new_lines'].some(key=>!Number.isSafeInteger(hunk[key])||hunk[key]<0))fail();text(hunk.before);text(hunk.after);}
  }else if(value.content_scope==='FULL_FILE'){
    // Full texts come exclusively from the host's historical snapshot resolver.
    // A projection ref is never treated as a content hash or a current-disk ref.
    if(value.source!=='AUTHORIZED_IMMUTABLE_FILE_SNAPSHOTS'||value.composition!=='EXACT_SAVED_BEFORE_AFTER'||value.encoding!=='UTF-8'||Buffer.from(value.before).toString('utf8')!==value.before||Buffer.from(value.after).toString('utf8')!==value.after||hash(value.before)!==value.before_sha256||hash(value.after)!==value.after_sha256)fail();
    if(value.status==='added'&&value.before!==''||value.status==='deleted'&&value.after!=='')fail();
  }else fail();return JSON.parse(JSON.stringify(value));}
function entryToken(owner,directory,entry){return hash(canonical({owner,child_ref:directory.child_ref,snapshot_ref:directory.snapshot_ref,diff_ref:entry.diff_ref,source_native_session_id:entry.source_native_session_id,child_lineage_ref:entry.child_lineage_ref,source_generation:entry.source_generation,source_scope:entry.source_scope}));}
function reviewKey(scope,v){return hash(canonical({project_id:scope.project_id,session_ref:scope.session_ref,generation:scope.generation,workspace_ref:v.workspace_ref,child_ref:v.child_ref,diff_ref:v.diff_ref}));}
function reviewSelection(owner,directory,entry){return {session_ref:owner.session_ref,generation:owner.generation,workspace_ref:owner.workspace_ref,snapshot_ref:directory.snapshot_ref,diff_ref:entry.diff_ref,child_ref:directory.child_ref??null};}
function validatePreparation(v,owner,directory,entry){bounded(v);const selection=reviewSelection(owner,directory,entry),disk=v.current_saved;
 if(v.kind!=='DIFF_REVIEW_PREPARATION'||!same(v.scope,owner)||!same(v.child_ref,selection.child_ref)||v.read_only!==true||v.workspace_writes!==0||v.native_posts!==0||v.model_calls!==0||v.editor_dirty_state!=='NOT_OBSERVED'||v.editor_barrier!=='NOT_ACQUIRED'||v.effect_scope!=='HISTORICAL_USER_REVIEW_ONLY_NOT_FILE_APPLICATION_OR_ROLLBACK'||!object(disk)||typeof disk.exists!=='boolean'||!Number.isSafeInteger(disk.byte_length)||disk.byte_length<0||disk.byte_length>MAX_BYTES||disk.exists&&!/^[a-f0-9]{64}$/.test(disk.sha256??'')||!disk.exists&&(disk.sha256!==null||disk.byte_length!==0)||!Number.isSafeInteger(v.review_revision)||v.review_revision<0||v.review_revision>1024)fail();
 const d=validateFile(v.diff,owner,directory,entry);if(d.content_scope!=='FULL_FILE')fail('DIFF_UNAVAILABLE');
 const basis={selection,path:d.path,disk,review_revision:v.review_revision,before_sha256:d.before_sha256,after_sha256:d.after_sha256,before_exists:d.status!=='added',after_exists:d.status!=='deleted'};
 if(v.preparation_sha256!==hash(canonical(basis))||v.matches_before!==(disk.exists===basis.before_exists&&(!basis.before_exists||disk.sha256===d.before_sha256))||v.matches_after!==(disk.exists===basis.after_exists&&(!basis.after_exists||disk.sha256===d.after_sha256)))fail('DIFF_CHANGED');
 if(v.review_revision===0?v.review!==null:!object(v.review)||v.review.revision!==v.review_revision||!['ACCEPT','REJECT'].includes(v.review.decision)||!object(v.review.review_ref)||v.review.review_ref.id!==reviewKey(owner,selection)||v.review.review_ref.revision!==v.review_revision||!/^[a-f0-9]{64}$/.test(v.review.review_ref.sha256??''))fail();
 return JSON.parse(JSON.stringify(v));}
function validReviewRequest(v,scope,operation){try{if(!object(v)||Object.keys(v).sort().join(',')!=='child_ref,decision,diff_ref,expected_review_revision,generation,operation_id,preparation_sha256,session_ref,snapshot_ref,workspace_ref'||v.operation_id!==operation||!same(v.session_ref,scope.session_ref)||v.generation!==scope.generation||!['ACCEPT','REJECT'].includes(v.decision)||!Number.isSafeInteger(v.expected_review_revision)||v.expected_review_revision<0||v.expected_review_revision>1024||!/^[a-f0-9]{64}$/.test(v.preparation_sha256)||v.child_ref!==null&&!validRef(v.child_ref))return false;workspace(v.workspace_ref);ref(v.snapshot_ref);ref(v.diff_ref);return true;}catch{return false;}}
function reviewReceipt(v,entry){const request=entry.review_request,scope=entry.scope;if(!validReviewRequest(request,scope,entry.operation_id)||!object(v)||v.id!==entry.operation_id||v.kind!=='DIFF_REVIEW'||v.generation!==scope.generation||!same(v.session_ref,scope.session_ref)||v.request_sha256!==hash(canonical(request))||v.automatic_replay!==false)fail('OPERATION_INPUT_MISMATCH');
 if(v.status==='RETURNED'){const selection={...request};for(const k of ['operation_id','decision','preparation_sha256','expected_review_revision'])delete selection[k];
  if(v.review_decision!==request.decision||v.preparation_sha256!==request.preparation_sha256||!same(v.review_selection,selection)||!object(v.review_ref)||v.review_ref.id!==reviewKey(scope,request)||!/^[a-f0-9]{64}$/.test(v.review_ref.sha256)||v.review_ref.revision!==request.expected_review_revision+1||v.workspace_writes!==0||v.native_posts!==0||v.model_calls!==0||v.effect_scope!=='HISTORICAL_USER_REVIEW_ONLY_NOT_FILE_APPLICATION_OR_ROLLBACK')fail('OPERATION_INPUT_MISMATCH');return 'OK';}
 if(v.status==='FAILED')return 'ERROR';return 'UNKNOWN';}
// In-memory, immutable virtual documents. Unknown/evicted URIs throw instead of
// inserting an error message into a code diff. No filesystem or buffer access.
class SnapshotDocuments{
  constructor({maxPairs=8,maxBytes=4*1024*1024}={}){this.maxPairs=maxPairs;this.maxBytes=maxBytes;this.entries=new Map();this.bytes=0;}
  add(value){const provenance=JSON.parse(canonical({scope:value.scope,child_ref:value.child_ref,snapshot_ref:value.snapshot_ref,diff_ref:value.diff_ref,source_native_session_id:value.source_native_session_id,child_lineage_ref:value.child_lineage_ref,source_generation:value.source_generation,source_scope:value.source_scope}));const size=Buffer.byteLength(value.before)+Buffer.byteLength(value.after);if(size>MAX_BYTES||size>this.maxBytes)fail('DIFF_TOO_LARGE');const token=hash(canonical({...provenance,before:value.before,after:value.after}));
    if(!this.entries.has(token)){while(this.entries.size>=this.maxPairs||this.bytes+size>this.maxBytes){const oldest=this.entries.keys().next().value;this.bytes-=this.entries.get(oldest).bytes;this.entries.delete(oldest);}this.entries.set(token,{before:value.before,after:value.after,provenance,bytes:size});this.bytes+=size;}
    const basename=value.path.replace(/\\/g,'/').split('/').pop()||'snapshot.txt';return {before:`${SCHEME}:/${token}/before/${encodeURIComponent(basename)}`,after:`${SCHEME}:/${token}/after/${encodeURIComponent(basename)}`};}
  provide(uri,cancellation){if(cancellation?.isCancellationRequested)fail('DIFF_CANCELLED');if(uri.scheme!==SCHEME||uri.authority||uri.query||uri.fragment)fail('DIFF_SNAPSHOT_EXPIRED');const match=/^\/([a-f0-9]{64})\/(before|after)\/[^/]+$/.exec(uri.path);const entry=match&&this.entries.get(match[1]);if(!entry)fail('DIFF_SNAPSHOT_EXPIRED');return entry[match[2]];}
  clear(){this.entries.clear();this.bytes=0;}
}
module.exports={SCHEME,MAX_BYTES,ownerOf,ownerToken,validateDirectory,validateFile,entryToken,reviewKey,reviewSelection,validatePreparation,validReviewRequest,reviewReceipt,SnapshotDocuments};
