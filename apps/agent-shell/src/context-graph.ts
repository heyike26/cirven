import {api,ApiError,operationId,type ApiClient} from './api';
import type {Workspace,SourceRef as SavedFileSource} from './contracts';

export type ContextPolicy={capture:boolean;retrieve:boolean;archive_mode:'MANUAL'|'AUTO_REVIEW'|'AUTO';char_budget:number};
export type ContextDefaults={revision:number;policy:Partial<ContextPolicy>;effective_policy:ContextPolicy;source_contents_shared:false;execution_authorized:false};
export type ContextScope={id:string;project_id:string|null;parent_id:string|null;kind:'GLOBAL'|'PROJECT'|'TOPIC'|'TASK';label:string;revision:number;policy:Partial<ContextPolicy>;effective_policy:ContextPolicy;policy_basis:Record<string,string>};
export type ContextSource={kind:'DOCUMENT'|'FILE_SNAPSHOT'|'CONVERSATION_MESSAGE'|'COMMAND_OBSERVATION'|'NOTE'|'NOTE_FRAGMENT';project_id:string|null;object_id?:string;revision?:number;sha256?:string;conversation_id?:string;message_id?:string;cursor?:string;link_ref?:{project_id:string;id:string;revision:number;sha256:string};file_identity_sha256?:string;source_anchor?:Record<string,unknown>;offset?:number;length?:number;command_owner?:{session_id:string;session_sha256:string;generation:number;workspace_id:string;binding_revision:number;binding_sha256:string}};
export type ContextReadPreview={source_ref:ContextSource;text:string;title:string;fingerprint:string;origin_id:string;scope_id:string;conversation_id:string|null;source_project_id:string|null;review_sha256:string;expected_revision:number;expected_grant_revision:number;restore_file_exclusions:FileExclusionRef[]};
export type ContextReadGrant={id:string;scope_id:string;conversation_id:string|null;origin_id:string;source_ref:ContextSource;source_project_id:string|null;title:string;state:ContextAssociation['state'];revision:number;association_revision:number;review_sha256:string;forgotten:number};
export type ContextCatalogItem={origin_id:string;title:string;kind:ContextSource['kind'];source_ref:ContextSource};
export type ContextNoteBasis={origin_id:string;title:string;grant_ref?:{id:string;revision:number}};
export type NativePageRelationships={context_ref:{id:string;revision:number;sha256:string};status:'UNAVAILABLE'|'MATCHED'|'NO_REGISTERED_MATCH'|'AMBIGUOUS'|'PARTIAL'|'AMBIGUOUS_REGISTRATIONS'|'INDEX_LIMIT_REACHED';automatic_selection:false;saved_bytes_equal_page:false;unsaved_changes_observed?:boolean;items:{project_id:string;project_name:string;filename:string;file_key:string;link_ref:NonNullable<ContextSource['link_ref']>;source_ref:ContextSource;relationships:FileRelationship[];next_relationship_offset:number|null;document_location_verified:true;unsaved_changes_observed:boolean}[]};
export type FileExclusionRef={id:string;revision:number;scope_id?:string;conversation_id?:string|null};
export type ContextAssociation={id:string;scope_id:string;origin_id:string;conversation_id:string|null;state:'CANDIDATE'|'ACTIVE'|'REJECTED'|'RETRACTED'|'EXCLUDED';manual_locked:number;revision:number;title:string;kind:string;forgotten:number;source_ref:ContextSource;relation?:'DIRECT'|'INHERITED';via_directory?:boolean;file_exclusions?:FileExclusionRef[]};
export type ContextSelection={operation_id?:string;selection_id:string;project_id:string|null;conversation_id:string|null;scope_id:string;characters:number;char_budget:number;conversation_preferences_basis?:{conversation_id:string|null;revision:number};selected:{origin_id:string;association_id:string|null;association_revision:number|null;relation?:'DIRECT'|'INHERITED';folder_rule_refs?:{id:string;revision:number}[];fingerprint:string;characters:number;scope_id:string}[];omitted:{origin_id:string;reason:string}[]};
export type ContextFolderRef={project_id:string;workspace_id:string;binding_revision:number;binding_sha256:string;segments:string[];device:number|string;inode:number|string};
export type ContextFolderRule={id:string;scope_id:string;conversation_id:string|null;folder_ref:ContextFolderRef;range_kind:'CURRENT_DIRECTORY'|'INCLUDING_DESCENDANTS';state:'ACTIVE'|'CANDIDATE'|'REJECTED'|'RETRACTED';revision:number;source_current:boolean};
export type ContextReceipt={version:1;operation_id:string;operation_kind:string;request_sha256:string;[key:string]:unknown};
export type ContextFileTarget={kind:'WORKSPACE_FILE';source_ref:SavedFileSource}|{kind:'LINKED_FILE';link_ref:NonNullable<ContextSource['link_ref']>};
export type RelatedDiscussion={id:string;available:boolean;conversation_ref?:{id:string;revision:number};project_id?:string|null;title?:string|null;archived?:boolean};
export type FileRelationship={id:string;origin_id:string|null;relation:'DIRECT'|'INHERITED';scope_id:string;scope_label:string;conversation_id:string|null;revision:number;state:ContextAssociation['state'];source_ref:ContextSource|null;saved_version_matches:boolean|null;file_excluded:boolean;discussion:RelatedDiscussion|null};
export type ConversationContextFile={id:string;origin_id:string;title:string;scope_id:string;scope_label:string;conversation_id:string|null;relation:'DIRECT'|'INHERITED';state:ContextAssociation['state'];revision:number;source_ref:ContextSource;file_excluded:boolean;source_project_id:string};
export type ContextConversationLink={id:string;scope_id:string;navigation_only?:number;conversation_id:string;state:'CANDIDATE'|'ACTIVE'|'REJECTED'|'RETRACTED';revision:number;recorded_conversation_ref:{id:string;revision:number};current_project_matches:boolean;discussion?:RelatedDiscussion|null;label?:string;kind?:ContextScope['kind']};
export type ContextConversationPreferences={conversation_id:string;preferred_scope_id:string|null;retrieval_mode:'AUTO'|'MANUAL'|'OFF';include_global:boolean;policy:{retrieve?:boolean;capture?:boolean;char_budget?:number};revision:number};
export type ContextConversationState={conversation_ref:{id:string;revision:number};project_id:string|null;links:ContextConversationLink[];routing:{scope_id:string|null;status:string;settings:ContextConversationPreferences;link_basis:{id:string;revision:number}[]}};
type Pending={operation_id:string;kind:string};
const SHA=/^[a-f0-9]{64}$/;

/** One unresolved write per local panel. Recovery reads that same operation;
 * it never retries a POST or replaces a user's newer draft or selection. */
export class ContextGraphClient{
  readonly storageKey='eeas.context-pending.v1';
  pending:Pending|null=null;
  recoveryBlocked=false;
  get unresolved(){return this.pending!==null||this.recoveryBlocked;}
  private client:ApiClient;
  private storage:Pick<Storage,'getItem'|'setItem'|'removeItem'>;
  constructor(client:ApiClient=api,storage:Pick<Storage,'getItem'|'setItem'|'removeItem'>=localStorage){
    this.client=client;this.storage=storage;
    try{const raw=storage.getItem(this.storageKey);
      if(raw){const v=JSON.parse(raw);if(v&&/^[A-Za-z0-9_-]{1,100}$/.test(v.operation_id)&&typeof v.kind==='string'&&v.kind.length<=220)this.pending=v;else this.recoveryBlocked=true;}
    }catch{this.recoveryBlocked=true;} // Preserve the original record; keep the rest of the App usable.
  }
  private checked(value:ContextReceipt,pending:Pending){
    if(value?.version!==1||value.operation_id!==pending.operation_id||value.operation_kind!==pending.kind||!SHA.test(value.request_sha256))throw Error('Context operation identity differs');
    return value;
  }
  async write(route:string,kind:string,body:Record<string,unknown>,method:'POST'|'PUT'='POST'){
    if(typeof navigator!=='undefined'&&navigator.locks)return navigator.locks.request('eeas-context-index-writes',()=>this.performWrite(route,kind,body,method));
    return this.performWrite(route,kind,body,method);
  }
  private async performWrite(route:string,kind:string,body:Record<string,unknown>,method:'POST'|'PUT'){
    if(this.unresolved)throw Error('CONTEXT_OPERATION_UNRESOLVED');
    const saved=this.storage.getItem(this.storageKey);
    if(saved){this.pending=JSON.parse(saved);throw Error('CONTEXT_OPERATION_UNRESOLVED');}
    const pending={operation_id:operationId(),kind};
    this.storage.setItem(this.storageKey,JSON.stringify(pending));this.pending=pending;
    try{
      const value=this.checked(await this.client.request<ContextReceipt>(route,method,{...body,operation_id:pending.operation_id},undefined,false),pending);
      this.clearOwned(pending);this.pending=null;return value;
    }catch(error){
      // Only an explicit validation/conflict rejection proves no new commit.
      if(error instanceof ApiError&&[400,401,403,404,409,422].includes(error.status)){
        this.clearOwned(pending);this.pending=null;
      }
      throw error;
    }
  }
  private clearOwned(pending:Pending){const saved=this.storage.getItem(this.storageKey);if(saved&&JSON.parse(saved).operation_id===pending.operation_id)this.storage.removeItem(this.storageKey);}
  async recover(){
    if(this.recoveryBlocked)throw Error('LOCAL_CONTEXT_RECORD_UNREADABLE');
    const pending=this.pending;if(!pending)return null;
    const value=this.checked(await this.client.request<ContextReceipt>('/api/context/operations/'+encodeURIComponent(pending.operation_id)),pending);
    this.clearOwned(pending);this.pending=null;return value;
  }
  scopes(projectId:string|null){return this.client.request<{scopes:ContextScope[]}>('/api/context/scopes'+(projectId?'?project_id='+encodeURIComponent(projectId):''));}
  defaults(){return this.client.request<ContextDefaults>('/api/context/defaults');}
  saveDefaults(revision:number,policy:Partial<ContextPolicy>){return this.write('/api/context/defaults','global_defaults',{expected_revision:revision,policy},'PUT');}
  nativePageRelationships(reference:{id:string;revision:number;sha256:string}){return this.client.request<NativePageRelationships>('/api/context/native-pages/related','POST',{context_ref:reference},undefined,false);}
  scanHistoryEvents(conversation:string,alias:string,cursor:string|null){return this.write('/api/context/history/scan-events','message_locations_scan',{conversation_id:conversation,source_alias_id:alias,cursor});}
  prepareMessageSource(conversation:string,message:string,sha256:string,cursor:string|null,offset=0,length=4000){return this.client.request<{source_ref:ContextSource;title:string;text:string}>('/api/context/conversations/'+encodeURIComponent(conversation)+'/message-source','POST',{message_id:message,expected_sha256:sha256,cursor,offset,length},undefined,false);}
  previewHistorySource(source:ContextSource){return this.client.request<{source_ref:ContextSource;title:string;text:string}>('/api/context/history/preview-source','POST',source,undefined,false);}
  eventExcerpts(conversation:string,alias:string,cursor:string|null){return this.client.request<{items:{id:string;role:'assistant';text:string;text_original_length:number;text_truncated:boolean;source_ref:ContextSource}[];next_cursor:string|null;partial:boolean}>('/api/context/conversations/'+encodeURIComponent(conversation)+'/event-excerpts?source_alias_id='+encodeURIComponent(alias)+(cursor?'&cursor='+encodeURIComponent(cursor):''));}
  sourceCatalog(project:string|null,offset=0){return this.client.request<{items:ContextCatalogItem[];next_offset:number|null}>('/api/context/source-catalog?offset='+offset+(project?'&project_id='+encodeURIComponent(project):''));}
  readGrants(scope:string,conversation:string|null,offset=0){return this.client.request<{items:ContextReadGrant[];next_offset:number|null}>('/api/context/scopes/'+encodeURIComponent(scope)+'/read-grants?offset='+offset+(conversation?'&conversation_id='+encodeURIComponent(conversation):''));}
  previewReadGrant(scope:string,source:ContextSource,conversation:string|null){return this.client.request<ContextReadPreview>('/api/context/read-grants/preview','POST',{scope_id:scope,source,conversation_id:conversation},undefined,false);}
  adoptReadGrant(preview:ContextReadPreview){return this.write('/api/context/read-grants','read_grant',{scope_id:preview.scope_id,source:preview.source_ref,conversation_id:preview.conversation_id,expected_revision:preview.expected_revision,expected_grant_revision:preview.expected_grant_revision,review_sha256:preview.review_sha256,state:'ACTIVE',manual_locked:true,restore_file_exclusions:preview.restore_file_exclusions});}
  withdrawReadGrant(grant:ContextReadGrant,state:'RETRACTED'|'REJECTED'|'EXCLUDED'='RETRACTED'){return this.write('/api/context/read-grants','read_grant',{scope_id:grant.scope_id,source:grant.source_ref,conversation_id:grant.conversation_id,expected_revision:grant.association_revision,expected_grant_revision:grant.revision,review_sha256:grant.review_sha256,state,manual_locked:true,restore_file_exclusions:[]});}
  associations(scope:string,offset=0,conversation:string|null=null){return this.client.request<{items:ContextAssociation[];next_offset:number|null}>('/api/context/scopes/'+encodeURIComponent(scope)+'/associations?offset='+offset+(conversation?'&conversation_id='+encodeURIComponent(conversation):''));}
  preview(origin:string,project:string|null){return this.client.request<{origin_id:string;title:string;text:string}>('/api/context/origins/'+encodeURIComponent(origin)+(project?'?project_id='+encodeURIComponent(project):''));}
  saveScope(scope:Partial<ContextScope>&Pick<ContextScope,'id'|'label'|'kind'|'project_id'>){return this.write('/api/context/scopes/'+encodeURIComponent(scope.id),'scope:'+scope.id,{expected_revision:scope.revision??0,project_id:scope.project_id,parent_id:scope.parent_id??null,kind:scope.kind,label:scope.label,policy:scope.policy??{}},'PUT');}
  associate(scope:string,source:ContextSource,conversation:string|null=null,previous?:Pick<ContextAssociation,'revision'>,state:ContextAssociation['state']='ACTIVE',restore:FileExclusionRef[]=[]){
    return this.write('/api/context/associations','association',{scope_id:scope,source,conversation_id:conversation,expected_revision:previous?.revision??0,state,manual_locked:true,restore_file_exclusions:restore.map(({id,revision})=>({id,revision}))});
  }
  associationBasis(scope:string,source:ContextSource,conversation:string|null=null){return this.client.request<{id:string;revision:number;state:ContextAssociation['state']|null;file_exclusions:FileExclusionRef[]}>('/api/context/associations/basis','POST',{scope_id:scope,source,conversation_id:conversation},undefined,false);}
  observeFile(link:NonNullable<ContextSource['link_ref']>){return this.client.request<{source_ref:ContextSource;title:string;text:string}>('/api/context/files/observe','POST',{link_ref:link},undefined,false);}
  fileRelationships(target:ContextFileTarget,offset=0){return this.client.request<{file_key:string;project_id:string;items:FileRelationship[];next_offset:number|null}>('/api/context/files/related?offset='+offset,'POST',target,undefined,false);}
  prepareFileSource(target:ContextFileTarget){return this.client.request<{source_ref:ContextSource;title:string;text:string}>('/api/context/files/prepare-source','POST',target,undefined,false);}
  conversationFiles(conversation:string,scope?:string,offset=0){return this.client.request<{conversation_ref:{id:string;revision:number};project_id:string|null;items:ConversationContextFile[];next_offset:number|null}>('/api/context/conversations/'+encodeURIComponent(conversation)+'/files?offset='+offset+(scope?'&scope_id='+encodeURIComponent(scope):''));}
  conversationContext(conversation:string){return this.client.request<ContextConversationState>('/api/context/conversations/'+encodeURIComponent(conversation)+'/settings');}
  scopeConversations(scope:string,offset=0){return this.client.request<{scope_id:string;children:Pick<ContextScope,'id'|'label'|'kind'|'parent_id'>[];items:ContextConversationLink[];next_offset:number|null}>('/api/context/scopes/'+encodeURIComponent(scope)+'/conversations?offset='+offset);}
  linkConversation(scope:string,reference:{id:string;revision:number},previous?:Pick<ContextConversationLink,'revision'>,state:ContextConversationLink['state']='ACTIVE',preferenceRevision?:number,allowOtherProject=false,navigationOnly=false){return this.write('/api/context/conversation-links','conversation_link',{scope_id:scope,conversation_ref:reference,expected_revision:previous?.revision??0,state,manual_locked:true,...preferenceRevision!==undefined?{preference_expected_revision:preferenceRevision}:{},...allowOtherProject?{allow_reference_to_other_project:true}:{},...navigationOnly?{navigation_only:true}:{}});}
  saveConversationContext(reference:{id:string;revision:number},preferences:ContextConversationPreferences){return this.write('/api/context/conversation-settings','conversation_preferences',{conversation_ref:reference,expected_revision:preferences.revision,preferred_scope_id:preferences.preferred_scope_id,retrieval_mode:preferences.retrieval_mode,include_global:preferences.include_global,...preferences.policy});}
  inheritedFiles(scope:string,conversation:string|null,offset=0){return this.client.request<{items:ContextAssociation[];next_offset:number|null}>('/api/context/scopes/'+encodeURIComponent(scope)+'/inherited-files?offset='+offset+(conversation?'&conversation_id='+encodeURIComponent(conversation):''));}
  folderRules(scope:string,offset=0){return this.client.request<{items:ContextFolderRule[];next_offset:number|null}>('/api/context/scopes/'+encodeURIComponent(scope)+'/folder-rules?offset='+offset);}
  resolveFolder(workspace:Workspace,segments:string[]){return this.client.request<{folder_ref:ContextFolderRef}>('/api/context/folders/resolve','POST',{project_id:workspace.project_id,workspace_id:workspace.id,binding_revision:workspace.revision,binding_sha256:workspace.binding_sha256,segments},undefined,false);}
  folderRule(scope:string,reference:ContextFolderRef,range:ContextFolderRule['range_kind'],conversation:string|null=null,previous?:ContextFolderRule,state:ContextFolderRule['state']='ACTIVE'){
    return this.write('/api/context/folder-rules','folder_rule',{scope_id:scope,conversation_id:conversation,expected_revision:previous?.revision??0,folder_ref:reference,range,state});
  }
  note(scope:string,title:string,text:string,basis:string[]=[],grants:{origin_id:string;id:string;revision:number}[]=[]){return this.write('/api/context/notes','note',{scope_id:scope,title,text,basis,...grants.length?{basis_grants:grants}:{}});}
  forget(scope:string,origin:string){return this.write('/api/context/forget','forget',{scope_id:scope,origin_id:origin});}
  async select(scope:string,conversation:string|null){return await this.write('/api/context/selections','select',{scope_id:scope,conversation_id:conversation,char_budget:16000,limit:12,include_global:false}) as ContextReceipt&ContextSelection;}
}
