import {AuthoredUIError} from './authored-ui-copy.ts';
import {nativeReference,type FileAssociationScope,type NativeCollaborationBridge,type NativeDocxRevisionArtifact,type NativeDocxRevisionChange,type NativeMessage,type NativeMessageArtifact} from './native-collaboration.ts';

const id=(value:unknown):value is string=>typeof value==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(value);
const sha=(value:unknown):value is string=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
export type NativeReportArtifactTarget={projectId:string;artifactId:string;sha256:string;downloadUrl:string;revision?:NativeDocxRevisionArtifact};

/** This is a typed projection, never a path or write authority. Main/API still
 * resolve the exact immutable artifact and create its independent working copy. */
export function nativeReportArtifact(artifact:NativeMessageArtifact,message?:Pick<NativeMessage,'operation_id'|'conversation_id'|'project_id'|'file_link_ref'>):NativeReportArtifactTarget|undefined{
 if(!artifact||typeof artifact!=='object'||!id(artifact.project_id)||!sha(artifact.id)||!sha(artifact.sha256)||typeof artifact.download_url!=='string')return;
 const projectId=artifact.project_id,artifactId=artifact.id,url=artifact.download_url;
 const revision=url===`/api/projects/${projectId}/docx-revisions/artifacts/${artifactId}/download`;
 const generic=url===`/api/projects/${projectId}/report-artifacts/${artifactId}/download`;
 const planning=new RegExp(`^/api/projects/${projectId}/(?:planning/proposals|discovery/sessions)/[A-Za-z0-9_-]{1,100}/reports/${artifactId}/(?:download|pending/download|pending/json/download|source/download)$`).test(url);
 if(!revision&&!generic&&!planning||message?.project_id&&message.project_id!==projectId)return;
 let docx:NativeDocxRevisionArtifact|undefined;
 if(revision||artifact.kind==='docx_revision'){
  const item=artifact as NativeDocxRevisionArtifact,owner=item.owner;
  if(!revision||item.kind!=='docx_revision'||typeof item.filename!=='string'||!item.filename.endsWith('.docx')||item.filename.length>240||/[\\/\u0000-\u001f\u007f]/.test(item.filename)
   ||!owner||!id(owner.operation_id)||!id(owner.generation)||owner.conversation_id!=null&&!id(owner.conversation_id)
   ||!nativeReference(item.proposal_ref)||!nativeReference(item.source_link_ref)||item.source_link_ref.project_id!==projectId
   ||!nativeReference(item.snapshot_ref)||item.snapshot_ref.project_id!==projectId||!sha(item.source_sha256)
   ||item.source_files_modified!==false||item.changes_applied!==false||item.later_source_edits_included!==false
   ||item.execution_effects!=='CREATE_NEW_DERIVED_DOCX'||item.review?.mode!=='WORD_TRACKED_INSERTIONS_AND_DELETIONS')return;
  if(message&&(owner.operation_id!==message.operation_id||(owner.conversation_id??null)!==(message.conversation_id??null)
   ||message.file_link_ref&&JSON.stringify([message.file_link_ref.project_id,message.file_link_ref.id,message.file_link_ref.revision,message.file_link_ref.sha256])!==JSON.stringify([item.source_link_ref.project_id,item.source_link_ref.id,item.source_link_ref.revision,item.source_link_ref.sha256])))return;
  docx=item;
 }
 return {projectId,artifactId,sha256:artifact.sha256,downloadUrl:url,...docx?{revision:docx}:{}};
}

export function nativeRevisionChanges(artifact:NativeDocxRevisionArtifact):NativeDocxRevisionChange[]|undefined{
 const changes=artifact.review?.changes;
 if(!Array.isArray(changes)||!changes.length||changes.length>256)return;
 if(changes.some(item=>!item||item.part!=='word/document.xml'||typeof item.paragraph_path!=='string'||!item.paragraph_path||item.paragraph_path.length>2048
  ||!Number.isSafeInteger(item.char_start)||!Number.isSafeInteger(item.char_end)||item.char_start<0||item.char_end<item.char_start
  ||typeof item.quote!=='string'||Array.from(item.quote).length>8000||!sha(item.quote_sha256)
  ||typeof item.replacement!=='string'||Array.from(item.replacement).length>8000||typeof item.reason!=='string'||Array.from(item.reason).length>2000))return;
 if(new TextEncoder().encode(JSON.stringify(changes)).byteLength>2*1024*1024)return;
 return changes;
}

/** One user click, one original IPC. Unknown acknowledgements are not replayed. */
export async function openNativeReportArtifact(bridge:NativeCollaborationBridge,artifact:NativeMessageArtifact,scope:FileAssociationScope,
 options:{message?:NativeMessage;current?:()=>boolean}={}){
 const target=nativeReportArtifact(artifact,options.message);
 if(!target)throw new AuthoredUIError('产物来源尚未核对，原回复与文件保留。');
 if(scope.projectId!==target.projectId||options.message?.conversation_id&&options.message.conversation_id!==scope.conversationId||options.current?.()===false)
  throw new AuthoredUIError('当前工作已切换，已核对的文件未自动打开');
 if(!bridge.openReportArtifact)throw new AuthoredUIError('原软件打开尚未接入当前宿主。');
 const receipt=await bridge.openReportArtifact(target.projectId,target.artifactId,target.sha256,target.downloadUrl,{scope:{...scope}});
 if((receipt as {status?:string})?.status!=='OPEN_REQUESTED')throw new AuthoredUIError('系统未确认产物打开请求');
 return receipt;
}
