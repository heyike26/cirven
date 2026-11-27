import {readNativeDraft,saveNativeDraft,type NativeDraftAttachment} from './native-collaboration.ts';
import {validateOriginalWorkChoice,type OriginalWorkChoice} from './original-work-input';

export type DiscussionDraftKey={kind:'native'|'original'|'conversation';projectId:string|null;id:string};
export type DiscussionDraft={text:string;attachments:NativeDraftAttachment[];original_work_choice?:OriginalWorkChoice};
type Storage=Pick<globalThis.Storage,'getItem'|'setItem'>;
const LIMIT=256*1024;
export function discussionDraftKey(key:DiscussionDraftKey){
  if(key.kind==='conversation'){
    if(key.projectId!==null||!/^(native|original|discovery):[a-f0-9]{64}$/.test(key.id))throw new Error('DISCUSSION_DRAFT_SCOPE_REQUIRED');
    return 'eeas-conversation-draft-v1:'+key.id;
  }
  if(!['native','original'].includes(key.kind)||!(key.projectId===null||/^[A-Za-z0-9_-]{1,100}$/.test(key.projectId))||!/^[A-Za-z0-9_-]{1,200}$/.test(key.id))throw new Error('DISCUSSION_DRAFT_SCOPE_REQUIRED');
  return 'eeas-discussion-draft-v1:'+JSON.stringify([key.kind,key.projectId,key.id]);
}
function checked(value:unknown):DiscussionDraft{
  const row=value as DiscussionDraft;
  if(!row||typeof row.text!=='string'||row.text.length>64000||!Array.isArray(row.attachments)||row.attachments.length>20)throw new Error('DISCUSSION_DRAFT_UNAVAILABLE');
  let raw='';const memory={getItem:()=>raw,setItem:(_key:string,value:string)=>{raw=value;}};
  saveNativeDraft(memory,undefined,row.attachments);const restored=readNativeDraft(memory);
  if(JSON.stringify(restored)!==JSON.stringify(row.attachments))throw new Error('DISCUSSION_DRAFT_UNAVAILABLE');
  return {text:row.text,attachments:restored,...(row.original_work_choice!==undefined?{original_work_choice:validateOriginalWorkChoice(row.original_work_choice)}:{})};
}
/** One exact discussion owns a lease. A concurrent writer cannot be silently replaced. */
export class DiscussionDraftLease {
  readonly key:string;
  private basis:string|null;
  private value:DiscussionDraft;
  private storage:Storage;
  private linkKey:string;
  private linkBasis:string|null;
  constructor(storage:Storage,scope:DiscussionDraftKey){
    this.storage=storage;
    const original=discussionDraftKey(scope);
    this.linkKey='eeas-discussion-draft-link-v1:'+original;this.linkBasis=storage.getItem(this.linkKey);
    if(scope.kind==='conversation'&&this.linkBasis!==null)throw new Error('DISCUSSION_DRAFT_SCOPE_REQUIRED');
    let target=scope;
    if(this.linkBasis!==null){
      if(this.linkBasis.length>160)throw new Error('DISCUSSION_DRAFT_UNAVAILABLE');
      target={kind:'conversation',projectId:null,id:JSON.parse(this.linkBasis)};
    }
    this.key=discussionDraftKey(target);this.basis=storage.getItem(this.key);
    if(this.linkBasis!==null&&this.basis===null)throw new Error('DISCUSSION_DRAFT_MIGRATION_INCOMPLETE');
    if(this.basis!==null&&new TextEncoder().encode(this.basis).length>LIMIT)throw new Error('DISCUSSION_DRAFT_UNAVAILABLE');
    this.value=this.basis===null?{text:'',attachments:[]}:checked(JSON.parse(this.basis));
  }
  read():DiscussionDraft{return structuredClone(this.value);}
  save(value:DiscussionDraft){
    const next=checked(value),raw=JSON.stringify(next);
    if(new TextEncoder().encode(raw).length>LIMIT)throw new Error('DISCUSSION_DRAFT_CAPACITY');
    if(this.storage.getItem(this.linkKey)!==this.linkBasis||this.storage.getItem(this.key)!==this.basis)throw new Error('DISCUSSION_DRAFT_CONFLICT');
    if(raw!==this.basis){this.storage.setItem(this.key,raw);if(this.storage.getItem(this.key)!==raw)throw new Error('DISCUSSION_DRAFT_SAVE_UNCONFIRMED');}
    this.basis=raw;this.value=next;
  }
}

function sameDraft(a:DiscussionDraft,b:DiscussionDraft){
  const canonical=(value:unknown):string=>Array.isArray(value)?'['+value.map(canonical).join(',')+']':value&&typeof value==='object'?'{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical((value as Record<string,unknown>)[key])).join(',')+'}':JSON.stringify(value);
  return canonical(a)===canonical(b);
}
export type DraftCopy={id:string;createdAt:string;local:DiscussionDraft;saved:DiscussionDraft};
const copiesKey=(lease:DiscussionDraftLease)=>'eeas-discussion-draft-copies-v1:'+lease.key;
export function readDraftCopies(storage:Storage,lease:DiscussionDraftLease):DraftCopy[]{
  const raw=storage.getItem(copiesKey(lease));if(raw===null)return [];
  if(new TextEncoder().encode(raw).length>2*1024*1024)throw Error('DISCUSSION_DRAFT_CAPACITY');
  const value=JSON.parse(raw);if(value.version!==1||!Array.isArray(value.copies)||value.copies.length>4)throw Error('DISCUSSION_DRAFT_UNAVAILABLE');
  return value.copies.map((copy:DraftCopy)=>{if(typeof copy.id!=='string'||!/^[A-Za-z0-9_-]{1,100}$/.test(copy.id)||!Number.isFinite(Date.parse(copy.createdAt)))throw Error('DISCUSSION_DRAFT_UNAVAILABLE');return {id:copy.id,createdAt:copy.createdAt,local:checked(copy.local),saved:checked(copy.saved)};});
}
function retainDraftPair(storage:Storage,lease:DiscussionDraftLease,local:DiscussionDraft,saved:DiscussionDraft){
  const key=copiesKey(lease),basis=storage.getItem(key),copies=readDraftCopies(storage,lease);
  if(!sameDraft(saved,local)&&!copies.some(copy=>sameDraft(copy.local,local)&&sameDraft(copy.saved,saved))){
    if(copies.length>=4)throw Error('DISCUSSION_DRAFT_CAPACITY');
    copies.push({id:crypto.randomUUID(),createdAt:new Date().toISOString(),local:checked(local),saved});
    const raw=JSON.stringify({version:1,copies});if(new TextEncoder().encode(raw).length>2*1024*1024)throw Error('DISCUSSION_DRAFT_CAPACITY');
    if(storage.getItem(key)!==basis)throw Error('DISCUSSION_DRAFT_CONFLICT');storage.setItem(key,raw);if(storage.getItem(key)!==raw)throw Error('DISCUSSION_DRAFT_SAVE_UNCONFIRMED');
  }
}
/** Keep both complete inputs before an explicit conflict choice. */
export function chooseDraft(storage:Storage,lease:DiscussionDraftLease,local:DiscussionDraft,choice:'local'|'saved'){
  const saved=lease.read();
  retainDraftPair(storage,lease,local,saved);
  lease.save(choice==='local'?local:saved);return lease.read();
}
export function removeDraftCopy(storage:Storage,lease:DiscussionDraftLease,id:string){
  const key=copiesKey(lease),basis=storage.getItem(key),copies=readDraftCopies(storage,lease).filter(copy=>copy.id!==id);
  if(storage.getItem(key)!==basis)throw Error('DISCUSSION_DRAFT_CONFLICT');const raw=JSON.stringify({version:1,copies});storage.setItem(key,raw);if(storage.getItem(key)!==raw)throw Error('DISCUSSION_DRAFT_SAVE_UNCONFIRMED');
}
export function seedConversationDraft(storage:Storage,id:string,seed:DiscussionDraft){
  const lease=new DiscussionDraftLease(storage,{kind:'conversation',projectId:null,id}),saved=lease.read();
  if((saved.text||saved.attachments.length||saved.original_work_choice)&&!sameDraft(saved,seed))return {lease,conflict:true as const,saved,local:checked(seed)};
  lease.save(seed);return {lease,conflict:false as const};
}
const nonempty=(value:DiscussionDraft)=>!!value.text||!!value.attachments.length||!!value.original_work_choice;

export class DiscussionDraftMigrationConflict extends Error{
  constructor(readonly lease:DiscussionDraftLease,readonly local:DiscussionDraft,readonly saved:DiscussionDraft,
    readonly resolveDraft:(choice:'local'|'saved')=>void){super('DISCUSSION_DRAFT_CONFLICT');}
}

/** Link old exact identities to one stable draft. Keep every old value as backup.
 * A conflict leaves both values intact; it never merges or overwrites user prose.
 */
export function migrateConversationDraft(storage:Storage,id:string,sources:readonly DiscussionDraftKey[],live?:{scope:DiscussionDraftKey;value:DiscussionDraft}):DiscussionDraftLease{
  if(sources.length>64)throw new Error('DISCUSSION_DRAFT_CAPACITY');
  const scope:DiscussionDraftKey={kind:'conversation',projectId:null,id};
  const keys=[...new Map(sources.map(source=>[discussionDraftKey(source),source])).values()];
  if(live){
    const sourceKey=discussionDraftKey(live.scope);
    if(!keys.some(key=>discussionDraftKey(key)===sourceKey))throw new Error('DISCUSSION_DRAFT_SCOPE_REQUIRED');
    const source=new DiscussionDraftLease(storage,live.scope),old=source.read();
    if(nonempty(old)&&!sameDraft(old,live.value))throw new Error('DISCUSSION_DRAFT_CONFLICT');
    source.save(live.value);
  }
  const target=new DiscussionDraftLease(storage,scope),targetRaw=storage.getItem(target.key);
  const candidates=keys.map(key=>{
    if(key.kind==='conversation')throw new Error('DISCUSSION_DRAFT_SCOPE_REQUIRED');
    const sourceKey=discussionDraftKey(key),linkKey='eeas-discussion-draft-link-v1:'+sourceKey;
    const link=storage.getItem(linkKey),raw=storage.getItem(sourceKey);
    if(link!==null&&link!==JSON.stringify(id))throw new Error('DISCUSSION_DRAFT_CONFLICT');
    const lease=new DiscussionDraftLease(storage,key);
    return {sourceKey,linkKey,link,raw,value:lease.read()};
  });
  function checkedRow(row:typeof candidates[number]){
    if(storage.getItem(row.sourceKey)!==row.raw||storage.getItem(row.linkKey)!==row.link)throw Error('DISCUSSION_DRAFT_CONFLICT');
  }
  function checkBasis(){
    if(storage.getItem(target.key)!==targetRaw)throw Error('DISCUSSION_DRAFT_CONFLICT');
    candidates.forEach(checkedRow);
  }
  function commit(value:DiscussionDraft,rows:typeof candidates){
    checkBasis();
    target.save(value);
    for(const row of rows){
      checkedRow(row);
      if(row.link!==null)continue;
      storage.setItem(row.linkKey,JSON.stringify(id));
      if(storage.getItem(row.linkKey)!==JSON.stringify(id))throw Error('DISCUSSION_DRAFT_SAVE_UNCONFIRMED');
    }
  }
  let value=target.read();
  for(const row of candidates){
    if(row.link!==null||!nonempty(row.value))continue;
    if(nonempty(value)&&!sameDraft(value,row.value)){
      const saved=value,local=row.value;
      const resolved=candidates.filter(candidate=>candidate.link===null
        &&(sameDraft(candidate.value,saved)||sameDraft(candidate.value,local)));
      throw new DiscussionDraftMigrationConflict(target,structuredClone(local),structuredClone(saved),choice=>{
        checkBasis();
        retainDraftPair(storage,target,local,saved);
        commit(choice==='local'?local:saved,resolved);
      });
    }
    value=row.value;
  }
  commit(value,candidates);
  return target;
}
