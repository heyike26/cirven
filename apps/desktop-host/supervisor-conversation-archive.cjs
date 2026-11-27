'use strict';
// Segments are immutable data subordinate to the CAS head. An unreferenced
// segment (for example after a crash) never becomes conversation history.
const fs=require('node:fs'),path=require('node:path'),{createHash,randomUUID}=require('node:crypto');
const SEGMENT_BYTES=128*1024,SEGMENT_RECORDS=32,MAX_SEGMENTS=256,MAX_BYTES=32*1024*1024,MAX_DIRECTORY_ENTRIES=512;
const SHA=/^[a-f0-9]{64}$/,ID=/^[A-Za-z0-9_-]{1,100}$/,TERMINAL=new Set(['RETURNED','FAILED','CANCELLED','REJECTED']);
const cache=new Map(),CACHE_SEGMENTS=4;
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const copy=value=>JSON.parse(JSON.stringify(value));
function check(value,code='SUPERVISOR_ARCHIVE_INVALID'){if(!value)throw Object.assign(new Error(code),{code});}
function exact(value,keys){return value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).sort().join(',')===[...keys].sort().join(',');}
function directory(head){return path.join(path.dirname(head),'supervisor-conversation.archive.v1');}
function ordinaryDirectory(root,{create=false}={}){
  if(!fs.existsSync(root)){if(!create)return false;fs.mkdirSync(root,{mode:0o700});}
  const stat=fs.lstatSync(root);check(stat.isDirectory()&&!stat.isSymbolicLink());return true;
}
function empty(){return {version:1,segments:[],bytes:0,record_count:0,operation_count:0};}
function validate(value){
  if(value===undefined)return empty();
  check(exact(value,['version','segments','bytes','record_count','operation_count'])&&value.version===1&&Array.isArray(value.segments)&&value.segments.length<=MAX_SEGMENTS);
  let bytes=0,records=0,operations=0;const hashes=new Set(),ids=new Set(),sequences=new Set();
  for(const ref of value.segments){
    check(exact(ref,['sha256','bytes','sequences','operations'])&&typeof ref.sha256==='string'&&SHA.test(ref.sha256)&&!hashes.has(ref.sha256));hashes.add(ref.sha256);
    check(Number.isSafeInteger(ref.bytes)&&ref.bytes>0&&ref.bytes<=SEGMENT_BYTES&&Array.isArray(ref.sequences)&&ref.sequences.length>0&&ref.sequences.length<=SEGMENT_RECORDS&&Array.isArray(ref.operations)&&ref.operations.length<=ref.sequences.length);
    let prior=0;for(const sequence of ref.sequences){check(Number.isSafeInteger(sequence)&&sequence>prior&&!sequences.has(sequence));prior=sequence;sequences.add(sequence);}
    for(const id of ref.operations){check(typeof id==='string'&&ID.test(id)&&!ids.has(id));ids.add(id);}
    bytes+=ref.bytes;records+=ref.sequences.length;operations+=ref.operations.length;
  }
  check(bytes<=MAX_BYTES&&value.bytes===bytes&&value.record_count===records&&value.operation_count===operations);return copy(value);
}
function plan(conversationId,records){
  check(typeof conversationId==='string'&&ID.test(conversationId)&&Array.isArray(records)&&records.length>0&&records.length<=SEGMENT_RECORDS);
  const payload={version:1,conversation_id:conversationId,records:copy(records)};
  const bytes=Buffer.from(JSON.stringify(payload)+'\n');check(bytes.length<=SEGMENT_BYTES,'SUPERVISOR_STORAGE_FULL');
  const ref={sha256:hash(bytes),bytes:bytes.length,sequences:records.map(row=>row.value.sequence),operations:records.filter(row=>row.kind==='operation').map(row=>row.value.operation_id)};
  validate({version:1,segments:[ref],bytes:ref.bytes,record_count:records.length,operation_count:ref.operations.length});return {ref,bytes};
}
function signature(stat){return [stat.dev,stat.ino,stat.size,stat.mtimeNs,stat.ctimeNs,stat.nlink].map(String).join(':');}
function read(head,conversationId,ref){
  // The path is derived solely from a descriptor in the already validated head.
  check(SHA.test(ref.sha256));const root=directory(head);check(ordinaryDirectory(root));const file=path.join(root,ref.sha256+'.json');
  const lst=fs.lstatSync(file);check(lst.isFile()&&!lst.isSymbolicLink()&&lst.size===ref.bytes&&lst.size<=SEGMENT_BYTES);
  const fd=fs.openSync(file,fs.constants.O_RDONLY|(fs.constants.O_NOFOLLOW||0));let raw,stat;
  try{
    stat=fs.fstatSync(fd,{bigint:true});check(stat.isFile()&&Number(stat.size)===ref.bytes);
    const key=file+':'+ref.sha256,stored=cache.get(key),stamp=signature(stat);
    if(stored?.stamp===stamp){cache.delete(key);cache.set(key,stored);check(stored.conversationId===conversationId&&JSON.stringify(stored.ref)===JSON.stringify(ref));return copy(stored.records);}
    raw=fs.readFileSync(fd);check(signature(fs.fstatSync(fd,{bigint:true}))===stamp&&raw.length===ref.bytes&&hash(raw)===ref.sha256);
    let payload;try{payload=JSON.parse(raw.toString('utf8'));}catch{check(false);}
    check(exact(payload,['version','conversation_id','records'])&&payload.version===1&&payload.conversation_id===conversationId&&Array.isArray(payload.records)&&payload.records.length===ref.sequences.length);
    const operations=[];
    payload.records.forEach((row,index)=>{
      check(exact(row,['kind','value'])&&['operation','local'].includes(row.kind)&&row.value&&typeof row.value==='object'&&!Array.isArray(row.value)&&row.value.sequence===ref.sequences[index]);
      if(row.kind==='operation'){check(TERMINAL.has(row.value.status)&&ID.test(row.value.operation_id));operations.push(row.value.operation_id);}
      else check(ID.test(row.value.id));
    });
    check(JSON.stringify(operations)===JSON.stringify(ref.operations));
    cache.set(key,{stamp,conversationId,ref:copy(ref),records:copy(payload.records)});while(cache.size>CACHE_SEGMENTS)cache.delete(cache.keys().next().value);
    return copy(payload.records);
  }finally{fs.closeSync(fd);}
}
function budget(root,addition){
  const stream=fs.opendirSync(root);let count=0,bytes=0;
  try{let entry;while((entry=stream.readSync())){
    check(++count<=MAX_DIRECTORY_ENTRIES,'SUPERVISOR_STORAGE_FULL');check(/^(?:[a-f0-9]{64}\.json|[a-f0-9-]{36}\.tmp)$/.test(entry.name));
    const stat=fs.lstatSync(path.join(root,entry.name));check(stat.isFile()&&!stat.isSymbolicLink()&&stat.size<=SEGMENT_BYTES);bytes+=stat.size;
    check(bytes+addition<=MAX_BYTES,'SUPERVISOR_STORAGE_FULL');
  }}finally{stream.closeSync();}
  if(typeof fs.statfsSync==='function'){const stat=fs.statfsSync(root);check(stat.bavail*stat.bsize>=addition+16*1024*1024,'SUPERVISOR_STORAGE_FULL');}
}
function write(head,conversationId,prepared){
  const root=directory(head);ordinaryDirectory(root,{create:true});const file=path.join(root,prepared.ref.sha256+'.json');
  if(fs.existsSync(file)){const records=read(head,conversationId,prepared.ref);check(JSON.stringify(records)===JSON.stringify(JSON.parse(prepared.bytes).records));return;}
  budget(root,prepared.bytes.length*2);
  const temporary=path.join(root,randomUUID()+'.tmp');let fd;
  try{
    fd=fs.openSync(temporary,'wx',0o600);fs.writeFileSync(fd,prepared.bytes);fs.fsyncSync(fd);fs.closeSync(fd);fd=undefined;
    // link is an atomic CreateNew of complete bytes; rename would overwrite an
    // existing immutable segment on some hosts. No copy/overwrite fallback.
    try{fs.linkSync(temporary,file);}catch(error){if(error.code!=='EEXIST')throw error;read(head,conversationId,prepared.ref);}
  }catch(error){if(['ENOSPC','EDQUOT'].includes(error.code))check(false,'SUPERVISOR_STORAGE_FULL');throw error;}
  finally{if(fd!==undefined)fs.closeSync(fd);try{fs.unlinkSync(temporary);}catch{}}
}
module.exports={emptySupervisorArchive:empty,validateSupervisorArchive:validate,planSupervisorSegment:plan,readSupervisorSegment:read,writeSupervisorSegment:write,
  SUPERVISOR_ARCHIVE_LIMITS:Object.freeze({segment_bytes:SEGMENT_BYTES,segment_records:SEGMENT_RECORDS,segments:MAX_SEGMENTS,bytes:MAX_BYTES,directory_entries:MAX_DIRECTORY_ENTRIES,cache_segments:CACHE_SEGMENTS}),supervisorArchiveDirectory:directory};
