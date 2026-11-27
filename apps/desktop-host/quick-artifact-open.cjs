'use strict';

// Map a clicked artifact from its actual frozen question to the same native
// document resolver as Main. This is neither a model call nor a file reader.
const {quickAssociationScope,quickDigest,quickFileLinkRef}=require('./quick-journal.cjs');
const ID=/^[A-Za-z0-9_-]{1,100}$/;
const HASH=/^[a-f0-9]{64}$/;

async function openQuickArtifact({artifact,quickHost,resolveAndOpenDocument,translate=key=>key}) {
  const refuse=(key,code)=>{const error=new Error(translate(key));error.code=code;throw error;};
  if(!quickHost||typeof quickHost.ownsArtifact!=='function'||!quickHost.ownsArtifact(artifact))
    refuse('artifactUnowned','QUICK_ARTIFACT_UNOWNED');
  const matches=quickHost.list().records.filter(item=>item.delivery==='SEND'&&item.operationId&&
    item.message?.operation_id===item.operationId&&item.message.artifacts?.some(value=>quickDigest(value)===quickDigest(artifact)));
  if(!matches.length)refuse('artifactUnowned','QUICK_ARTIFACT_UNOWNED');
  const record=matches[0],scope=quickAssociationScope(record.scope);
  if(matches.some(item=>quickDigest(quickAssociationScope(item.scope))!==quickDigest(scope))||
    !scope.projectId||!scope.conversationId||quickDigest(scope)!==quickDigest(quickAssociationScope(quickHost.scope)))
    refuse('presentationChanged','QUICK_ARTIFACT_SCOPE_CHANGED');

  if(artifact?.kind==='docx_revision') {
    const message=record.message,owner=artifact.owner;
    const route=`/api/projects/${scope.projectId}/docx-revisions/artifacts/${artifact.id}/download`;
    if(!ID.test(scope.projectId)||!HASH.test(artifact.id)||!HASH.test(artifact.sha256)||artifact.revision!==1||
      artifact.project_id!==scope.projectId||message.project_id!==scope.projectId||
      artifact.download_url!==route||owner?.operation_id!==record.operationId||!ID.test(owner?.generation)||
      (owner?.conversation_id??null)!==scope.conversationId||message.conversation_id!==scope.conversationId||
      artifact.source_files_modified!==false||artifact.changes_applied!==false||artifact.later_source_edits_included!==false)
      refuse('artifactUnowned','QUICK_ARTIFACT_BINDING_DIFFERS');
    let link;
    try{link=quickFileLinkRef(artifact.source_link_ref);}catch{refuse('artifactUnowned','QUICK_ARTIFACT_BINDING_DIFFERS');}
    if(link.project_id!==scope.projectId||quickDigest(link)!==quickDigest(record.scope.fileLinkRef??null)||
      quickDigest(link)!==quickDigest(message.file_link_ref??null)||
      quickDigest(link)!==quickDigest(message.file_observation?.link_ref??null)||
      quickDigest(artifact.snapshot_ref??null)!==quickDigest(message.file_observation?.snapshot_ref??null)||
      !HASH.test(artifact.source_sha256)||artifact.source_sha256!==message.file_observation?.current_sha256)
      refuse('artifactUnowned','QUICK_ARTIFACT_BINDING_DIFFERS');
    // Exactly one resolver call. An unconfirmed shell/IPC result is never replayed.
    return resolveAndOpenDocument('/api/desktop-host/native-open/report',{
      project_id:scope.projectId,artifact_id:artifact.id,sha256:artifact.sha256,download_url:route},
      {keepHidden:true,associationScope:scope});
  }

  const source=artifact?.file_source_ref??artifact?.source_ref;
  if(!source||source.kind!=='local_file'||source.project_id!==scope.projectId)
    refuse('artifactNoFile','QUICK_ARTIFACT_UNSUPPORTED');
  return resolveAndOpenDocument('/api/desktop-host/native-open/file',{
    project_id:source.project_id,file_source_ref:source},{keepHidden:true,associationScope:scope});
}

module.exports={openQuickArtifact};
