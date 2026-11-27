import type {NativeRef} from './native-collaboration';
import type {ConversationSidebarItem} from './conversation-sidebar';
export type HistoryWorkspace={id:string;revision:number;binding_sha256:string};
type CommonSource={project_id:string|null;workspace_ref:HistoryWorkspace|null};
export type ConversationMetadataSource={kind:'conversation';project_id:string|null;workspace_ref:null;conversation_ref:ConversationRef;metadata_role:'CONVERSATION_METADATA_NO_EXECUTION_OR_FILE_GRANT'};
export type HistorySource=CommonSource&(
  {kind:'native';request_project_id:string|null;conversation_id:string;last_operation_id:string;request_sha256:string;record_origin:string;provider_profile_ref:NativeRef|null}|
  {kind:'original';project_id:string;session_ref:NativeRef;generation:number;provider_profile_ref:NativeRef|null}|
  {kind:'discovery';project_id:string;object_ref:NativeRef}|ConversationMetadataSource
);
export type ConversationRef={id:string;revision:number};
export type ConversationOrigin={kind:'native';project_id:string|null;conversation_id:string}|{kind:'original'|'discovery';project_id:string;session_id:string};
export type HistoryItem=ConversationSidebarItem&{source:HistorySource;conversationRef?:ConversationRef;conversationOrigin?:ConversationOrigin};
export type HistoryPage={items:HistoryItem[];nextCursor:string|null;partial:boolean;sourceUnavailable:boolean;organizationSupported:boolean};
const ID=/^[A-Za-z0-9_-]{1,200}$/,SHA=/^[a-f0-9]{64}$/;
const validId=(value:unknown):value is string=>typeof value==='string'&&ID.test(value);
const validProject=(value:unknown):value is string|null=>value===null||validId(value)&&value.length<=100;
const object=(value:unknown):Record<string,unknown>=>{if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('HISTORY_REFERENCE_UNAVAILABLE');return value as Record<string,unknown>;};
function check(value:unknown):asserts value{if(!value)throw new Error('HISTORY_REFERENCE_UNAVAILABLE');}
const ref=(value:unknown):value is NativeRef=>{const row=object(value);return validId(row.id)&&Number.isSafeInteger(row.revision)&&Number(row.revision)>0&&typeof row.sha256==='string'&&SHA.test(row.sha256);};
export function parseConversationMetadata(value:unknown,basis:{id:string;revision:number;project_id:string|null}):ConversationMetadataSource{
  const source=object(value),ref=object(source.conversation_ref);
  check(Object.keys(source).length===5&&Object.keys(ref).length===2&&source.kind==='conversation'&&source.project_id===basis.project_id&&source.workspace_ref===null&&source.metadata_role==='CONVERSATION_METADATA_NO_EXECUTION_OR_FILE_GRANT');
  check(ref.id===basis.id&&ref.revision===basis.revision&&Number.isSafeInteger(ref.revision)&&Number(ref.revision)>=1);
  return structuredClone(source) as ConversationMetadataSource;
}
export function parseHistoryPage(value:unknown,locale:string):HistoryPage{
  const page=object(value),coverage=object(page.coverage);check(page.version===1&&page.execution_authorized===false&&Array.isArray(page.items)&&page.items.length<=100);
  check(coverage.model_calls===0&&typeof coverage.partial==='boolean'&&typeof coverage.complete==='boolean'&&typeof coverage.has_more==='boolean');
  check(page.next_cursor===null||typeof page.next_cursor==='string'&&page.next_cursor.length>0&&page.next_cursor.length<=8192);
  check(page.organization_supported===undefined||typeof page.organization_supported==='boolean');
  const items:HistoryItem[]=page.items.map(raw=>{
    const row=object(raw),source=object(row.source);check(typeof row.id==='string'&&/^(native|original|discovery):[a-f0-9]{64}$/.test(row.id)&&['native','original','discovery'].includes(String(row.kind))&&(source.kind===row.kind||row.kind==='native'&&source.kind==='conversation')&&validProject(row.project_id)&&validProject(source.project_id)&&typeof row.read_only==='boolean');
    let conversationRef:ConversationRef|undefined;
    if(row.conversation_ref!==undefined){const ref=object(row.conversation_ref);check(ref.id===row.id&&Number.isSafeInteger(ref.revision)&&Number(ref.revision)>=1);conversationRef={id:String(ref.id),revision:Number(ref.revision)};}
    else check(row.id.startsWith(row.kind+':')&&(source.project_id===row.project_id||row.project_assignment==='DEFAULT_CHATS'&&row.kind==='original'&&row.project_id===null&&source.workspace_ref===null));
    check(row.title===null||typeof row.title==='string'&&row.title.length<=500);check(row.project_name===null||typeof row.project_name==='string'&&row.project_name.length<=500);
    check(row.updated_at===null||typeof row.updated_at==='string'&&Number.isFinite(Date.parse(row.updated_at)));
    let organization:HistoryItem['organization'];
    if(row.organization!==undefined){const record=object(row.organization);check(Number.isSafeInteger(record.revision)&&Number(record.revision)>=0&&typeof record.archived==='boolean');organization={revision:Number(record.revision),archived:record.archived};}
    if(conversationRef)check(organization?.revision===conversationRef.revision);
    let conversationOrigin:ConversationOrigin|undefined;
    if(row.conversation_origin!==undefined){const origin=object(row.conversation_origin);check(!!conversationRef&&validProject(origin.project_id));if(origin.kind==='native')check(validId(origin.conversation_id));else check(['original','discovery'].includes(String(origin.kind))&&origin.project_id!==null&&validId(origin.session_id));conversationOrigin=origin as ConversationOrigin;}
    if(source.workspace_ref!==null){const workspace=object(source.workspace_ref);check(typeof workspace.id==='string'&&SHA.test(workspace.id)&&Number.isSafeInteger(workspace.revision)&&Number(workspace.revision)>0&&typeof workspace.binding_sha256==='string'&&SHA.test(workspace.binding_sha256));}
    if(source.kind==='conversation'){
      check(conversationRef&&conversationOrigin?.kind==='native'&&row.read_only===false);
      parseConversationMetadata(source,{...conversationRef!,project_id:row.project_id as string|null});
    }else if(source.kind==='native'){
      check(validProject(source.request_project_id)&&validId(source.conversation_id)&&validId(source.last_operation_id)&&typeof source.request_sha256==='string'&&SHA.test(source.request_sha256)&&['CURRENT_HOST','HISTORICAL_READONLY'].includes(String(source.record_origin)));
      check(source.provider_profile_ref===null||ref(source.provider_profile_ref));if(source.request_project_id!==row.project_id)check(row.read_only===true);
    }else if(source.kind==='original'){check(source.project_id!==null&&ref(source.session_ref)&&Number.isSafeInteger(source.generation)&&Number(source.generation)>=0&&(source.provider_profile_ref===null||ref(source.provider_profile_ref)));}
    else check(source.project_id!==null&&ref(source.object_ref));
    const title=typeof row.title==='string'&&row.title.trim()?row.title:(locale.startsWith('en')?'Untitled chat':'未命名对话');
    return {id:row.id as string,kind:row.kind as HistoryItem['kind'],projectId:row.project_id as string|null,...typeof row.project_name==='string'?{projectName:row.project_name}:{},title,updatedAt:row.updated_at as string|null,readOnly:row.read_only,source:source as HistorySource,...organization?{organization}:{},...conversationRef?{conversationRef}:{},...conversationOrigin?{conversationOrigin}:{},...source.kind==='native'?{lastOperationId:source.last_operation_id as string}:{}};
  });
  check(Array.isArray(coverage.errors));
  check(new Set(items.map(item=>item.id)).size===items.length);return {items,nextCursor:page.next_cursor as string|null,partial:coverage.partial||coverage.has_more||!coverage.complete,sourceUnavailable:coverage.errors.length>0||coverage.has_more&&page.next_cursor===null,organizationSupported:page.organization_supported===true};
}
export function mergeHistoryItems(previous:readonly HistoryItem[],incoming:readonly HistoryItem[],limit=2000):HistoryItem[]{
  const items=new Map(previous.map(item=>[item.id,item]));
  for(const item of incoming){const current=items.get(item.id);if(!current||Date.parse(String(item.updatedAt??''))>=Date.parse(String(current.updatedAt??''))||current.updatedAt==null)items.set(item.id,item);}
  if(items.size>limit)throw new Error('HISTORY_LOADED_CAPACITY');return [...items.values()];
}
type Label={projectId:string|null;conversationId:string;text:string};
export function labelHistoryItems(items:readonly HistoryItem[],storage:Pick<Storage,'getItem'>):HistoryItem[]{
  return items.map(item=>{const origin=item.conversationOrigin;if(origin?.kind==='native'){const title=readConversationLabel(storage,origin.project_id,origin.conversation_id);return title?{...item,title}:item;}if(item.source.kind!=='native'||item.conversationRef&&origin)return item;const title=readConversationLabel(storage,item.projectId,item.source.conversation_id);return title?{...item,title}:item;});
}
const LABELS='eeas-conversation-labels-v1';
export function readConversationLabel(storage:Pick<Storage,'getItem'>,projectId:string|null,conversationId:string):string|undefined{
  try{const raw=storage.getItem(LABELS)??'[]';if(raw.length>131072)return;const rows=JSON.parse(raw);if(!Array.isArray(rows)||rows.length>1000)return;const row=rows.find((item:Label)=>item?.projectId===projectId&&item.conversationId===conversationId&&typeof item.text==='string'&&Array.from(item.text).length<=120);return row?.text;}catch{return;}
}
export function saveConversationLabel(storage:Pick<Storage,'getItem'|'setItem'>,projectId:string|null,conversationId:string,text:string){
  if(!validProject(projectId)||!validId(conversationId)||typeof text!=='string')return;
  const title=Array.from(text.trim().split(/\r?\n/u)[0]??'').slice(0,100).join('');if(!title)return;
  const raw=storage.getItem(LABELS)??'[]';if(raw.length>131072)return;const rows=JSON.parse(raw);if(!Array.isArray(rows)||rows.length>=1000)return;
  if(rows.some((item:Label)=>item?.projectId===projectId&&item.conversationId===conversationId))return;
  const next=JSON.stringify([...rows,{projectId,conversationId,text:title}]);if(next.length<=131072)storage.setItem(LABELS,next);
}
