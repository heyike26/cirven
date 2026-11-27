'use strict';
// Explicit client UNIT transport data; not a produced task or runtime receipt.
const {hash}=require('../client.cjs');
const {ownerOf}=require('../session-diff.cjs');
function fixture({full=false,status='modified',sourceGeneration=2}={}){const session={project_id:'qa',session_ref:{id:'session',revision:1,sha256:'a'.repeat(64)},generation:2,workspace_ref:{id:'e'.repeat(64),revision:3,binding_sha256:'b'.repeat(64)}};
 const scope=ownerOf(session),entry={diff_ref:{id:'f'.repeat(64),revision:1,sha256:'c'.repeat(64)},path:'src/中文 long name.ts',status,source:full?'AUTHORIZED_IMMUTABLE_FILE_SNAPSHOTS':'ORIGINAL_TOOL_FILEDIFF',additions:1,deletions:1,content_scope:full?'FULL_FILE':'HUNK_EXCERPTS',availability:'AVAILABLE',...(full?{source_generation:sourceGeneration,source_scope:{...scope,generation:sourceGeneration}}:{})};
 const directory={scope,snapshot_ref:{id:'1'.repeat(64),revision:1,sha256:'d'.repeat(64)},files:[entry],coverage:{source:'SAVED_ORIGINAL_ROOT_MESSAGE_OBSERVATION',complete:false,truncated:false,unavailable:0,reason_codes:[]},read_only:true,current_disk_compared:false,editor_dirty_state:'NOT_OBSERVED',model_calls:0};
 const rawBefore=status==='added'?'':'\ufeffconst old = 1;\r\n',rawAfter=status==='deleted'?'':'\ufeffconst newValue = 42;\r\n';
 const file={...entry,scope,snapshot_ref:directory.snapshot_ref,read_only:true,model_calls:0,current_disk_compared:false,editor_dirty_state:'NOT_OBSERVED',before:full?rawBefore:'@@ Historical before lines 20,1 @@\nold\n',after:full?rawAfter:'@@ Historical after lines 20,1 @@\nnew\n',composition:full?'EXACT_SAVED_BEFORE_AFTER':'LOCATION_MARKED_HUNK_EXCERPTS',...(full?{encoding:'UTF-8',before_sha256:hash(rawBefore),after_sha256:hash(rawAfter)}:{patch:'--- a\n+++ b\n@@ -20,1 +20,1 @@\n-old\n+new\n',hunks:[{old_start:20,old_lines:1,new_start:20,new_lines:1,before:'old\n',after:'new\n'}]})};return {session,scope,entry,directory,file};}
module.exports={fixture};
