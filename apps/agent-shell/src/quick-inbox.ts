import type {OriginalAgent} from './agent-sessions';
import type {NativeReviewInput} from './native-review';
import {authoredCopy,authoredErrorCopy,type AuthoredUICopy} from './authored-ui-copy.ts';
import type {NativeMessage,NativeRef,NativeRegion,NativeContextSnapshot} from './native-collaboration';
export type QuickScope={projectId?:string;workspaceId?:string;sessionId?:string;providerProfileRef?:NativeRef;conversationId?:string;generation?:number;conversationRef?:{id:string;revision:number};originalSource?:{project_id:string;session_ref:NativeRef;generation:number};agent?:OriginalAgent;contextSelectionId?:string;contextSelectionOperationId?:string;originalWorkChoice?:import('./original-work-input').OriginalWorkChoice;workInputUnavailable?:true};
export type QuickRecord={id:string;revision:number;sha256:string;scope:QuickScope;delivery:'EDITING'|'DRAFT'|'SEND';comment:string;regions:NativeRegion[];marks?:unknown[];contextRef?:NativeRef;snapshot?:NativeContextSnapshot;operationId?:string;message?:NativeMessage;acknowledged?:boolean;nativeReview?:NativeReviewInput};
export type QuickBridge={setQuickScope?:(scope:QuickScope)=>Promise<QuickScope>;getQuickDrafts?:()=>Promise<{records:QuickRecord[]}>;ackQuickDraft?:(ref:NativeRef)=>Promise<unknown>;onQuickDraftsChanged?:(listener:()=>void)=>()=>void;submitQuickDraft?:(body:{ref:NativeRef;text?:string})=>Promise<NativeMessage>;openQuickDraft?:(ref:NativeRef)=>Promise<unknown>};
const indexKey='eeas-quick-inbox-v1';
const validRef=(v:unknown):v is NativeRef=>{const r=v as NativeRef;return !!r&&typeof r.id==='string'&&r.id.length<=100&&r.revision>0&&Number.isSafeInteger(r.revision)&&typeof r.sha256==='string'&&/^[a-f0-9]{64}$/.test(r.sha256);};
export const quickRef=(value:QuickRecord):NativeRef=>({id:value.id,revision:value.revision,sha256:value.sha256});
export const quickIdentity=(value:NativeRef)=>JSON.stringify([value.id,value.revision,value.sha256]);
export function sameQuickScope(a:QuickScope,b:QuickScope){return a.projectId===b.projectId&&a.workspaceId===b.workspaceId&&a.sessionId===b.sessionId&&(!a.conversationId||!b.conversationId||a.conversationId===b.conversationId);}
export function validQuickRecord(value:unknown):value is QuickRecord{const r=value as QuickRecord;if(!validRef(r)||!r.scope||typeof r.scope!=='object'||!(['DRAFT','SEND'].includes(r.delivery)||r.delivery==='EDITING'&&(r.scope.originalWorkChoice||r.scope.workInputUnavailable))||typeof r.comment!=='string'||!Array.isArray(r.regions)||Array.from(r.comment).length>16000||r.contextRef&&!validRef(r.contextRef))return false;return typeof r.snapshot?.preview?.data_url!=='string';}
export type QuickTextNote={x:number;y:number;text:string};
/** Only the frozen text-mark contract counts as intent. Shape-only marks never
 * synthesize a request; malformed text is retained in the record, not guessed. */
export function quickTextNotes(record:Pick<QuickRecord,'marks'>):QuickTextNote[]{
 if(!Array.isArray(record.marks)||record.marks.length>32)return [];
 const notes:QuickTextNote[]=[];let total=0;
 for(const value of record.marks){if(!value||typeof value!=='object'||Array.isArray(value))continue;const mark=value as Record<string,unknown>;if(mark.kind!=='text'&&mark.type!=='text')continue;
  if(mark.kind!==undefined&&mark.kind!=='text'||mark.type!==undefined&&mark.type!=='text'||Object.keys(mark).some(key=>!['kind','type','x','y','text'].includes(key))||typeof mark.x!=='number'||!Number.isFinite(mark.x)||mark.x<0||mark.x>1||typeof mark.y!=='number'||!Number.isFinite(mark.y)||mark.y<0||mark.y>1||typeof mark.text!=='string'||!mark.text.trim()||/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(mark.text))return [];
  const length=Array.from(mark.text).length;total+=length;if(length>2000||total>16000)return [];notes.push({x:mark.x,y:mark.y,text:mark.text});
 }return notes;
}
export const quickHasIntent=(record:Pick<QuickRecord,'comment'|'marks'>)=>!!record.comment.trim()||quickTextNotes(record).length>0;
/** Read-only transcript summary. Host alone builds the original SEND request. */
export function quickIntentSummary(record:Pick<QuickRecord,'comment'|'marks'>){return [record.comment,...quickTextNotes(record).map(note=>'图上文字：'+note.text)].filter(value=>value.trim()).join('\n');}
export function conversationIdFor(storage:Pick<Storage,'getItem'|'setItem'>,scope:QuickScope){const key='eeas-conversation:'+JSON.stringify([scope.projectId??'',scope.sessionId??'']);const old=storage.getItem(key);if(old&&/^[A-Za-z0-9_-]{1,100}$/.test(old))return old;const value=crypto.randomUUID();storage.setItem(key,value);if(storage.getItem(key)!==value)throw new Error('会话标识未能保存');return value;}
export function conversationStorage(storage:Pick<Storage,'getItem'|'setItem'>,id:string){return {getItem:(key:string)=>storage.getItem(key+':'+id),setItem:(key:string,value:string)=>storage.setItem(key+':'+id,value)};}
/** A single atomic local record contains both identities and payloads. A failed
 * persist/readback never permits ack, nor does ingest modify a composer. */
export class QuickInbox{
 records:QuickRecord[]=[];error='';error_copy?:AuthoredUICopy;private disposed=false;private release?:()=>void;private tail=Promise.resolve();private changed:()=>void;
 private storage:Pick<Storage,'getItem'|'setItem'>;private bridge:QuickBridge;constructor(storage:Pick<Storage,'getItem'|'setItem'>,bridge:QuickBridge,changed:()=>void){this.storage=storage;this.bridge=bridge;this.changed=changed;try{const raw=JSON.parse(storage.getItem(indexKey)??'null');if(raw?.schema===1&&Array.isArray(raw.records))this.records=raw.records.filter(validQuickRecord);}catch{this.error='快捷收件记录暂未读到，原生收件仍保留。';}}
 start(){this.release=this.bridge.onQuickDraftsChanged?.(()=>{void this.refresh();});return this.refresh();}
 refresh(){const next=this.tail.then(()=>this.ingest());this.tail=next.catch(()=>{});return next;}
 private async ingest(){if(this.disposed||!this.bridge.getQuickDrafts)return;try{const {records}=await this.bridge.getQuickDrafts();if(this.disposed)return;if(!Array.isArray(records)||records.some(record=>!validQuickRecord(record)))throw new Error('快捷反馈回执不完整');const all=new Map(this.records.map(value=>[quickIdentity(value),value]));for(const record of records)all.set(quickIdentity(record),structuredClone(record));const next=[...all.values()];const payload=JSON.stringify({schema:1,records:next});this.storage.setItem(indexKey,payload);if(this.storage.getItem(indexKey)!==payload)throw new Error('本机收件未能保存核对');this.records=next;this.error='';this.error_copy=undefined;this.changed();for(const record of records){if(this.disposed)return;if(record.acknowledged)continue;await this.bridge.ackQuickDraft?.(quickRef(record));}}
 catch(error){this.error='快捷反馈尚未完成本机接收，原记录保留：'+(error instanceof Error?error.message:String(error));this.error_copy=authoredCopy('快捷反馈尚未完成本机接收，原记录保留：{detail}',{detail:authoredErrorCopy(error)});this.changed();}}
 async flush(){await this.refresh();if(this.error)throw new Error(this.error);}
 stop(){this.disposed=true;this.release?.();}
}
