import type {HistoryItem,HistorySource} from './conversation-history';
import type {ThreadScope,ThreadMessage,ThreadHistory} from './conversation-thread';
import type {DiscussionDraft} from './discussion-drafts';
import {nativeReference} from './native-collaboration.ts';

/** Organization/history metadata never grants a different execution context.
 * A mixed thread or an existing chat draft keeps its own reader and input. */
export function historyDiscoverySource(row:HistoryItem,scope:ThreadScope,draft:DiscussionDraft,page?:ThreadHistory):Extract<HistorySource,{kind:'discovery'}>|undefined{
 if(row.source.kind!=='discovery'||row.kind!=='discovery'||row.readOnly||row.organization?.archived||scope.archived||scope.metadata_source||draft.text.length||draft.attachments.length)return;
 if(row.id!==scope.conversation_ref.id||row.projectId!==scope.project_id||row.source.project_id!==scope.project_id)return;
 const roots=scope.sources.filter(source=>source.id===row.id);if(roots.length!==1)return;
 const source=roots[0],original=source.original_source;
 if(source.id!==row.id||source.identity.kind!=='discovery'||source.identity.project_id!==row.source.project_id||source.identity.session_id!==row.source.object_ref.id||original?.kind!=='discovery')return;
 const a=original.object_ref,b=row.source.object_ref;
 // An alias freezes its original revision. The selected directory reference
 // may be newer; the existing workflow reader verifies that exact version.
 if(original.project_id!==row.source.project_id||a.id!==b.id)return;
 const extra=scope.sources.filter(source=>source.id!==row.id);
 if(extra.length){
  if(extra.length!==1||!page||page.partial||page.nextCursor!==null||page.conversationRef.id!==scope.conversation_ref.id||page.conversationRef.revision!==scope.conversation_ref.revision)return;
  const reserved=extra[0];
  if(reserved.identity.kind!=='native'||reserved.original_source!==null||reserved.identity.project_id!==scope.project_id||reserved.identity.conversation_id!==scope.next_message.conversation_id)return;
  if(page.messages.some(message=>message.source.kind!=='discovery'||message.source.alias_id!==row.id))return;
 }
 return row.source;
}

/** Resolve the clicked record from its own thread alias, never from the
 * currently selected project or the newest requirements version. */
export function threadDiscoveryDocument(scope:ThreadScope,row:ThreadMessage){
 if(row.role!=='assistant'||row.source.kind!=='discovery'||!row.discovery_review||!nativeReference(row.object_ref))return;
 const alias=scope.sources.find(source=>source.id===row.source.alias_id),original=alias?.original_source;
 if(alias?.identity.kind!=='discovery'||original?.kind!=='discovery'||alias.identity.project_id!==row.source.project_id||alias.identity.session_id!==row.source.session_id||row.source.project_id!==original.project_id||row.object_ref.id!==alias.identity.session_id)return;
 return {projectId:original.project_id,objectRef:row.object_ref};
}
