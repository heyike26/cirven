import type {ApiClient} from './api';
import type {HistoryItem,HistorySource,ConversationRef} from './conversation-history';
import {parseConversationMetadata,type ConversationMetadataSource} from './conversation-history.ts';
import {nativeReference,nativeInputAttachments,type NativeRef,type NativeInputAttachment} from './native-collaboration.ts';
import type {DiscussionDraftKey} from './discussion-drafts';

const IDENT=/^(native|original|discovery):[a-f0-9]{64}$/,ID=/^[A-Za-z0-9_-]{1,200}$/,SHA=/^[a-f0-9]{64}$/;
const object=(value:unknown):Record<string,unknown>=>value&&typeof value==='object'&&!Array.isArray(value)?value as Record<string,unknown>:{};
const check=(value:unknown)=>{if(!value)throw new Error('CONVERSATION_THREAD_UNAVAILABLE');};
const pid=(value:unknown)=>value===null||typeof value==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(value);
export type ThreadSource={id:string;identity:{kind:'native'|'original'|'discovery';project_id:string|null;conversation_id?:string;session_id?:string};original_source:Exclude<HistorySource,ConversationMetadataSource>|null};
export type ThreadCurrentOwner={status:'LEGACY'}|{status:'SELECTION_REQUIRED';selection_operation_id:string}|{status:'SELECTED';selection_operation_id:string;provider_profile_ref:NativeRef;original_source:{project_id:string;session_ref:NativeRef;generation:number}}|{status:'PENDING';provider_generation:number;connection_operation_id:string}|{status:'CURRENT';provider_generation:number;connection_operation_id:string;provider_profile_ref:NativeRef;original_source:{project_id:string;session_ref:NativeRef;generation:number}};
export type ThreadScope={version:1;conversation_ref:ConversationRef;project_id:string|null;archived:boolean;sources:ThreadSource[];next_message:{project_id:string|null;conversation_id:string};title?:string|null;metadata_source?:ConversationMetadataSource;current_owner?:ThreadCurrentOwner};
export type ThreadMessage={id:string;role:'user'|'assistant';text:string;text_truncated:boolean;text_sha256:string;observed_at:string;source:ThreadSource['identity']&{alias_id:string};operation_id?:string|null;native_message_id?:string|null;status?:string;failure_code?:'MODEL_AUTH_REQUIRED';input_attachments?:NativeInputAttachment[];input_file?:{filename:string;verified:boolean};native_feedback_ref?:NativeRef|null;file_source_ref?:unknown;object_ref?:NativeRef;discovery_review?:{source_role:string;claims:{id:string;text:string}[];questions:{id:string;text:string}[]};source_sha256?:string;request_sha256?:string;turn_id?:string;context_summary?:unknown};
export type ThreadHistory={messages:ThreadMessage[];nextCursor:string|null;partial:boolean;conversationRef:ConversationRef};
const exactKeys=(value:Record<string,unknown>,keys:string[])=>Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key));
const ownerRef=(value:unknown):value is NativeRef=>nativeReference(value)&&ID.test(value.id)&&exactKeys(object(value),['id','revision','sha256']);
const sameRef=(a:unknown,b:unknown)=>nativeReference(a)&&nativeReference(b)&&a.id===b.id&&a.revision===b.revision&&a.sha256===b.sha256;
function parseCurrentOwner(value:unknown):ThreadCurrentOwner{
 const owner=object(value);
 if(owner.status==='LEGACY'){check(exactKeys(owner,['status']));return {status:'LEGACY'};}
 if(owner.status==='SELECTION_REQUIRED'){check(exactKeys(owner,['status','selection_operation_id'])&&typeof owner.selection_operation_id==='string'&&ID.test(owner.selection_operation_id));return {status:'SELECTION_REQUIRED',selection_operation_id:String(owner.selection_operation_id)};}
 if(owner.status==='SELECTED'){const source=object(owner.original_source);check(exactKeys(owner,['status','selection_operation_id','provider_profile_ref','original_source'])&&typeof owner.selection_operation_id==='string'&&ID.test(owner.selection_operation_id)&&ownerRef(owner.provider_profile_ref)&&exactKeys(source,['project_id','session_ref','generation'])&&typeof source.project_id==='string'&&pid(source.project_id)&&ownerRef(source.session_ref)&&Number.isSafeInteger(source.generation)&&Number(source.generation)>=1);return structuredClone(owner) as ThreadCurrentOwner;}
 check(['PENDING','CURRENT'].includes(String(owner.status))&&Number.isSafeInteger(owner.provider_generation)&&Number(owner.provider_generation)>=1&&typeof owner.connection_operation_id==='string'&&ID.test(owner.connection_operation_id));
 if(owner.status==='PENDING'){
  check(exactKeys(owner,['status','provider_generation','connection_operation_id']));
  return {status:'PENDING',provider_generation:Number(owner.provider_generation),connection_operation_id:String(owner.connection_operation_id)};
 }
 const original=object(owner.original_source);
 check(exactKeys(owner,['status','provider_generation','connection_operation_id','provider_profile_ref','original_source'])&&ownerRef(owner.provider_profile_ref));
 check(exactKeys(original,['project_id','session_ref','generation'])&&typeof original.project_id==='string'&&pid(original.project_id)&&ownerRef(original.session_ref)&&Number.isSafeInteger(original.generation)&&Number(original.generation)>=1);
 return structuredClone(owner) as ThreadCurrentOwner;
}
/** Legacy metadata may adopt a sole actor. Multiple actors need the shared
 * explicit choice; pending/unavailable heads remain readable without an actor. */
export function threadOriginalOwner(scope:ThreadScope,fallbackProjectId:string|null):ThreadSource|undefined{
 const owner=scope.current_owner===undefined?undefined:parseCurrentOwner(scope.current_owner);
 const sources=scope.sources.filter(source=>source.original_source?.kind==='original');
 if(owner===undefined||owner.status==='LEGACY')return sources.length===1?sources[0]:undefined;
 if(owner.status!=='CURRENT'&&owner.status!=='SELECTED')return undefined;
 const selected=sources.filter(source=>source.identity.project_id===owner.original_source.project_id&&source.identity.session_id===owner.original_source.session_ref.id&&source.original_source?.kind==='original'&&sameRef(source.original_source.session_ref,owner.original_source.session_ref));
 check(selected.length===1);
 const original=selected[0].original_source;
 // Aliases are immutable registration metadata (bind_original records 0),
 // while current_owner carries the separately observed live generation.
 check(original?.kind==='original'&&sameRef(original.provider_profile_ref,owner.provider_profile_ref));
 return selected[0];
}
/** The owner GET is a separate observation. Verify it before any draft or
 * writable selection is adopted, even when its immutable session ref matches. */
export function verifyThreadOriginalOwner(scope:ThreadScope,source:ThreadSource,value:unknown):void{
 const expected=source.original_source,actual=object(value);
 if(expected?.kind!=='original')throw new Error('CONVERSATION_THREAD_UNAVAILABLE');
 check(actual.project_id===expected.project_id&&sameRef(actual.session_ref,expected.session_ref)&&Number.isSafeInteger(actual.generation)&&Number(actual.generation)>=0);
 check(expected.provider_profile_ref==null?actual.provider_profile_ref==null:sameRef(actual.provider_profile_ref,expected.provider_profile_ref));
 const owner=scope.current_owner===undefined?undefined:parseCurrentOwner(scope.current_owner);
 if(owner!==undefined&&owner.status!=='LEGACY')check((owner.status==='CURRENT'||owner.status==='SELECTED')&&threadOriginalOwner(scope,scope.project_id)?.id===source.id&&actual.generation===owner.original_source.generation);
}
export function parseThreadScope(value:unknown,id:string):ThreadScope{
 const row=object(value),ref=object(row.conversation_ref),route=object(row.next_message);
 check(row.version===1&&IDENT.test(id)&&ref.id===id&&Number.isSafeInteger(ref.revision)&&Number(ref.revision)>=1&&pid(row.project_id));
 check(typeof row.archived==='boolean'&&row.execution_started===false&&row.files_moved===false&&row.automatic_input_authorized===false&&row.file_write_authorized===false);
 check(pid(route.project_id)&&route.project_id===row.project_id&&typeof route.conversation_id==='string'&&ID.test(route.conversation_id));
 check(Array.isArray(row.sources)&&row.sources.length>=1&&row.sources.length<=64);
 const sources=(row.sources as unknown[]).map(raw=>{const source=object(raw),identity=object(source.identity),original=source.original_source;
  check(typeof source.id==='string'&&IDENT.test(source.id)&&['native','original','discovery'].includes(String(identity.kind))&&pid(identity.project_id));
  check(String(source.id).startsWith(String(identity.kind)+':'));
  check(typeof identity[identity.kind==='native'?'conversation_id':'session_id']==='string'&&ID.test(String(identity[identity.kind==='native'?'conversation_id':'session_id'])));
  check(identity.kind==='native'||identity.project_id!==null);check(original===null||object(original).kind===identity.kind&&object(original).project_id===identity.project_id);
  check(identity.kind==='native'||original!==null);
  if(original!==null)check(identity.kind==='native'?object(original).conversation_id===identity.conversation_id:object(object(original)[identity.kind==='original'?'session_ref':'object_ref']).id===identity.session_id);
  if(original!==null&&identity.kind==='original')check(nativeReference(object(original).session_ref));
  if(original!==null&&identity.kind==='discovery')check(nativeReference(object(original).object_ref));
  return {id:source.id,identity:{...identity},original_source:original} as ThreadSource;
 });
 check(new Set(sources.map(item=>item.id)).size===sources.length);
 check(row.title===undefined||row.title===null||typeof row.title==='string'&&row.title.length<=500);
 const metadata=row.metadata_source===undefined?undefined:parseConversationMetadata(row.metadata_source,{id,revision:Number(ref.revision),project_id:row.project_id as string|null});
 if(metadata)check(sources.some(source=>source.id===id&&source.identity.kind==='native'));
 const currentOwner=row.current_owner===undefined?undefined:parseCurrentOwner(row.current_owner);
 const scope:ThreadScope={version:1,conversation_ref:{id,revision:Number(ref.revision)},project_id:row.project_id as string|null,archived:row.archived as boolean,sources,next_message:{project_id:route.project_id as string|null,conversation_id:String(route.conversation_id)},...(row.title!==undefined?{title:row.title as string|null}:{}),...(metadata?{metadata_source:metadata}:{}),...(currentOwner?{current_owner:currentOwner}:{})};
 if(currentOwner?.status==='CURRENT'||currentOwner?.status==='SELECTED')threadOriginalOwner(scope,scope.project_id);
 return scope;
}
export function parseThreadHistory(value:unknown,scope:ThreadScope):ThreadHistory{
 const row=object(value),ref=object(row.conversation_ref),coverage=object(row.coverage);
 check(row.version===1&&ref.id===scope.conversation_ref.id&&ref.revision===scope.conversation_ref.revision&&row.project_id===scope.project_id&&row.execution_authorized===false&&row.automatic_input_authorized===false&&row.file_write_authorized===false);
 check(Array.isArray(row.messages)&&row.messages.length<=100&&typeof coverage.complete==='boolean'&&coverage.model_calls===0&&typeof coverage.source_partial==='boolean');
 check(row.next_cursor===null||typeof row.next_cursor==='string'&&row.next_cursor.length<=8192);
 const aliases=new Map(scope.sources.map(source=>[source.id,source]));
 const messages=(row.messages as unknown[]).map(raw=>{const message=object(raw),source=object(message.source),alias=aliases.get(String(source.alias_id));
  check(alias&&['user','assistant'].includes(String(message.role))&&typeof message.id==='string'&&SHA.test(message.id)&&typeof message.text==='string'&&message.text.length<=128000&&Array.from(message.text).length<=64000&&typeof message.text_truncated==='boolean'&&typeof message.text_sha256==='string'&&SHA.test(message.text_sha256)&&typeof message.observed_at==='string'&&message.observed_at.length<=64&&Number.isFinite(Date.parse(message.observed_at)));
  check(Object.entries(alias!.identity).every(([key,value])=>source[key]===value)&&message.history_role==='READONLY_DATA_NO_EXECUTION_OR_FILE_GRANT');
  check(message.operation_id===undefined||message.operation_id===null||typeof message.operation_id==='string'&&ID.test(message.operation_id));
  check(message.native_message_id===undefined||message.native_message_id===null||typeof message.native_message_id==='string'&&ID.test(message.native_message_id));
  check(message.failure_code===undefined||message.failure_code==='MODEL_AUTH_REQUIRED'&&message.role==='assistant'&&source.kind==='original'&&message.status==='FAILED');
  check(message.input_attachments===undefined||Array.isArray(message.input_attachments)&&message.input_attachments.length<=20);
  check(message.input_file===undefined||message.role==='user'&&typeof object(message.input_file).filename==='string'&&String(object(message.input_file).filename).length<=500&&typeof object(message.input_file).verified==='boolean');
  check(message.native_feedback_ref===undefined||message.native_feedback_ref===null||nativeReference(message.native_feedback_ref));
  if(message.object_ref!==undefined)check(nativeReference(message.object_ref));
  if(message.discovery_review!==undefined){const document=object(message.discovery_review);check(source.kind==='discovery'&&document.source_role==='DISCOVERY_DOCUMENT_REVISION_NOT_A_CHAT_MODEL_REPLY'&&Array.isArray(document.claims)&&document.claims.length<=12&&Array.isArray(document.questions)&&document.questions.length<=12);}
  let inputs:NativeInputAttachment[]|undefined;
  if(message.input_attachments!==undefined)inputs=nativeInputAttachments((message.input_attachments as unknown[]).map(value=>{
    const item=object(value);check(['CURRENT_PAGE','SCREENSHOT'].includes(String(item.kind))&&nativeReference(item.context_ref));
    check(item.kind==='SCREENSHOT'?typeof item.image_sha256==='string'&&SHA.test(item.image_sha256):item.image_sha256===undefined||item.image_sha256===null);
    check(item.user_note===undefined||item.user_note===null||typeof item.user_note==='string');
    return {context_ref:item.context_ref as NativeRef,label:item.label as string,comment:item.user_note as string??'',regions:item.regions as NativeInputAttachment['regions'],...(item.kind==='SCREENSHOT'?{preview_sha256:String(item.image_sha256)}:{})};
  }));
  return {...structuredClone(message),...(inputs?{input_attachments:inputs}:{})} as ThreadMessage;
 });
 check(new Set(messages.map(item=>item.id)).size===messages.length);
 return {messages,nextCursor:row.next_cursor as string|null,partial:!coverage.complete||coverage.source_partial===true,conversationRef:{...scope.conversation_ref}};
}
export function threadDraftSources(scope:ThreadScope):DiscussionDraftKey[]{
 return scope.sources.flatMap(source=>source.identity.kind==='discovery'?[]:[{kind:source.identity.kind,projectId:source.identity.project_id,id:source.identity.kind==='native'?source.identity.conversation_id!:source.identity.session_id!}]);
}
type Transport=Pick<ApiClient,'request'>;
export class ConversationThreadClient{
 private api:Transport;
 constructor(api:Transport){this.api=api;}
 async select(item:HistoryItem,signal?:AbortSignal){return parseThreadScope(await this.api.request('/api/conversations/'+encodeURIComponent(item.id)+'/selection','POST',{source:item.source},signal,false),item.id);}
 async scope(id:string,signal?:AbortSignal){check(IDENT.test(id));return parseThreadScope(await this.api.request('/api/conversations/'+encodeURIComponent(id)+'/scope','GET',undefined,signal),id);}
 async history(scope:ThreadScope,cursor?:string|null,signal?:AbortSignal){return parseThreadHistory(await this.api.request('/api/conversations/'+encodeURIComponent(scope.conversation_ref.id)+'/history?limit=50'+(cursor?'&cursor='+encodeURIComponent(cursor):''),'GET',undefined,signal),scope);}
}
