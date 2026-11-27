'use strict';
const fs=require('node:fs'),path=require('node:path');
const {randomUUID}=require('node:crypto');
const {canonical,hash,validateScope,safePath,boundedFile}=require('./client.cjs');
const {ownerOf,ownerToken,validateDirectory,validateFile,entryToken}=require('./session-diff.cjs');
const nativeConversation=require('./native-conversation.cjs');
const canonicalConversations=require('./canonical-conversations.cjs');
const connectionConfiguration=require('./connection-configuration.cjs');
const workRequirements=require('./work-requirements.cjs');
const workInput=require('./work-input-wire.cjs');
const workCases=require('./work-cases.cjs');
const workRetests=require('./work-retests.cjs');
const workSources=require('./work-sources.cjs');
const workDeliveries=require('./work-deliveries.cjs');
const nativeExports=require('./native-exports.cjs');
const workMaintenance=require('./work-maintenance.cjs');
const sessionDiff=require('./session-diff.cjs');
const editorChange=require('./editor-change.cjs');
const editorFiles=require('./editor-files.cjs');
const editorObserver=require('./editor-observer.cjs');
const contextRecovery=require('./context-recovery.cjs');
const nativeContextRecovery=require('./native-context-recovery.cjs');
// The desktop independently enforces the same system document allowlist.
const nativeOpenTypes=new Set(['.docx','.dotx','.xlsx','.pptx','.pdf','.vsdx','.xmind','.png','.jpg','.jpeg']);
const safeCode=e=>typeof e?.code==='string'&&/^[A-Z0-9_]{1,80}$/.test(e.code)?e.code:'CONNECTION_UNAVAILABLE';
function fail(code,message){throw Object.assign(new Error(message),{code});}
const copy=v=>JSON.parse(JSON.stringify(v));
function scopeOf(s){if(canonicalConversations.canonicalSession(s)&&!s.session_ref)return canonicalConversations.nav(s);return nativeConversation.native(s)?nativeConversation.scope(s):validateScope({project_id:s.project_id,session_ref:s.session_ref,generation:s.generation});}
function navigationScope(s){return canonicalConversations.canonicalSession(s)?canonicalConversations.nav(s):scopeOf(s);}
function draftKey(s){if(s.conversation_ref)return hash(canonical({kind:'CANONICAL_CONVERSATION',id:s.conversation_ref.id}));return hash(canonical(s.conversation_id?{project_id:s.project_id,conversation_id:s.conversation_id}:{project_id:s.project_id,session_ref:s.session_ref}));}
const sameDiscussion=(a,b)=>a?.conversation_ref||b?.conversation_ref?a?.conversation_ref?.id===b?.conversation_ref?.id:a?.conversation_id||b?.conversation_id?a?.conversation_id===b?.conversation_id&&a?.project_id===b?.project_id:a?.session_ref?.id===b?.session_ref?.id;
function artifactToken(session,artifact){return hash(canonical({scope:scopeOf(session),artifact}));}
function reportArtifact(session,item){return item&&item.kind==='report'&&item.project_id===session.project_id&&item.revision===1&&item.id===item.artifact_id&&/^[a-f0-9]{64}$/.test(item.artifact_id)&&/^[a-f0-9]{64}$/.test(item.sha256)&&item.verification==='REGISTRY_BYTES_VERIFIED'&&item.media_type==='application/vnd.openxmlformats-officedocument.presentationml.presentation'&&item.download_url==='/api/projects/'+session.project_id+'/report-artifacts/'+item.artifact_id+'/download';}
function workspaceArtifact(session,item){const selected=session?.workspace_ref,file=item?.file_ref;return item?.kind==='workspace-file'&&item.project_id===session.project_id&&['EXACT_MESSAGE_FILE_URL_AND_WORKSPACE_BINDING','EXACT_ORIGINAL_MARKDOWN_HREF_AND_WORKSPACE_BINDING'].includes(item.verification)&&['editor','native'].includes(item.target)&&selected&&file&&canonical(file.workspace_ref)===canonical(selected)&&typeof file.path==='string'&&file.path.length<=1024&&file.source===undefined&&typeof file.message_id==='string'&&typeof file.part_id==='string';}
function validArtifact(session,item){return reportArtifact(session,item)||workspaceArtifact(session,item);}
// Original work metadata lives in the existing draft and original request only.
function validWorkDraft(d){try{if(!d||typeof d!=='object')return true;if(d.original_work_choice!==undefined)workInput.validateOriginalWorkChoice(d.original_work_choice);const rows=d.original_work_copies;if(rows!==undefined){if(!Array.isArray(rows)||rows.length>4||new Set(rows.map(r=>r.id)).size!==rows.length)return false;for(const r of rows){if(!/^[A-Za-z0-9_-]{1,100}$/.test(r.id??'')||typeof r.text!=='string'||Array.from(r.text).length>16000||!['plan','build','workflow'].includes(r.agent??'build')||Object.keys(r).some(k=>!['id','text','agent','original_work_choice'].includes(k)))return false;if(r.original_work_choice!==undefined)workInput.validateOriginalWorkChoice(r.original_work_choice);}}return true;}catch{return false;}}
function validWorkEntry(e){try{if(e.work_input_request===undefined)return e.work_input_choice===undefined&&e.work_input_rejection===undefined;if(e.work_input_rejection!==undefined&&(!e.work_input_rejection||e.work_input_rejection.code!=='WORK_INPUT_ACTIVE_CHANGED'||e.status!=='ERROR'))return false;const r=e.work_input_request;workInput.validateOriginalWorkChoice(e.work_input_choice);return ['session.message','session.guidance'].includes(e.method)&&e.operation_id===r.operation_id&&typeof r.raw_text==='string'&&Array.from(r.raw_text).length<=16000&&canonical(workInput.originalWorkRef(r.original_work_ref))===canonical(e.work_input_choice.reference)&&canonical(r.session_ref)===canonical(e.scope.session_ref)&&r.generation===e.scope.generation&&e.scope.project_id===e.work_input_choice.owner.project_id&&canonical(e.scope.session_ref)===canonical(e.work_input_choice.owner.session_ref)&&e.work_input_request_sha256===hash(canonical(r))&&e.draft_body_sha256===hash(r.raw_text);}catch{return false;}}
function workInputCategory(entry,v){let category='UNKNOWN';try{const r=entry.work_input_request;if(validWorkEntry(entry)&&v?.id===entry.operation_id&&v.request_sha256===entry.work_input_request_sha256&&canonical(v.session_ref)===canonical(entry.scope.session_ref)&&v.automatic_replay===false&&v.kind===(r.intent?'GUIDANCE':'MESSAGE')){
      const local=workInput.originalWorkStatusText(r.raw_text);if(local&&v.status!=='RETURNED')return 'UNKNOWN';if(local&&v.status==='RETURNED'){workInput.verifyOriginalWorkInputReceipt({...v,request:r},entry.scope,r.original_work_ref,r.raw_text,true);category='ACCEPTED';}
      else if(v.native_dispatch_acknowledged===true&&!['UNKNOWN','UNKNOWN_OUTCOME','PENDING'].includes(v.status))category='ACCEPTED';else if(['FAILED','STOPPED','REJECTED'].includes(v.status)&&v.native_post_attempted===false)category='FAILED';
    }}catch{}return category;
}
function workInputCompact(v){const names=['id','kind','status','request_sha256','session_ref','automatic_replay','native_dispatch_acknowledged','native_post_attempted','role','model_calls','native_post_count','semantic_acceptance','local_status'];const result=Object.fromEntries(names.filter(k=>v[k]!==undefined).map(k=>[k,copy(v[k])]));if(Buffer.byteLength(JSON.stringify(result))>8192)fail('INVALID_OPERATION_RECEIPT','原工作回执超出保存范围。');return result;}
function workInputReserved(requests){return requests.reduce((n,r)=>{let direct=0,logical=0;if(r.work_input_request&&!(r.status==='ERROR'&&r.work_input_rejection?.code==='WORK_INPUT_ACTIVE_CHANGED')&&workInputCategory(r,r.work_input_receipt)==='UNKNOWN')direct=8448;if(r.canonical_work_choice){try{if(canonicalConversations.receipt(r.canonical_receipt,r)==='UNKNOWN')logical=12544;}catch{logical=12544;}}return n+direct+logical;},0);}

// Each extension activation owns a separate bounded slot. No cross-window CAS claim.
// Explicit recovery copies an old slot without changing it or replaying its requests.
class PrivateState{
  constructor(directory,{slot=randomUUID()}={}){
    safePath(directory);fs.mkdirSync(directory,{recursive:true,mode:0o700});safePath(directory);
    const entries=fs.readdirSync(directory).filter(n=>/^[a-f0-9-]{36}\.json$/.test(n));
    if(entries.length>=32)fail('LOCAL_CAPACITY','本地私稿槽位已满，请先备份并清理已结束槽位。');
    this.filename=path.join(directory,slot+'.json');this.value={schema:'eeas.ide-private.v1',slot,revision:0,drafts:{},child_drafts:{},editor_changes:{},editor_observers:{},canonical_choices:{},configuration_drafts:{},work_requirement_drafts:{},work_case_drafts:{},work_retest_drafts:{},work_source_drafts:{},work_delivery_drafts:{},native_export_records:{},work_maintenance_drafts:{},requests:[],selected:null,updated_at:new Date().toISOString()};
    fs.writeFileSync(this.filename,JSON.stringify(this.value),{flag:'wx',mode:0o600});
    this.directory=directory;
  }
  commit(change){const next=copy(this.value);change(next);next.revision++;next.updated_at=new Date().toISOString();
    if(!workMaintenance.validStores(next.work_maintenance_drafts)||!nativeExports.validStores(next.native_export_records)||!workDeliveries.validStores(next.work_delivery_drafts)||!workSources.validStores(next.work_source_drafts)||!workRetests.validStores(next.work_retest_drafts)||!workCases.validStores(next.work_case_drafts)||!workRequirements.validDrafts(next.work_requirement_drafts)||!canonicalConversations.validChoices(next.canonical_choices)||!connectionConfiguration.validDrafts(next.configuration_drafts)||Object.keys(next.drafts).length+Object.keys(next.child_drafts||{}).length>64||Object.keys(next.editor_changes||{}).length>32||Object.keys(next.editor_observers||{}).length>16||next.requests.length>128)fail('LOCAL_CAPACITY','本地私稿或待核查请求已满，请先核查旧请求。');
    if(Object.values(next.drafts).some(d=>!validWorkDraft(d))||next.requests.some(e=>!validWorkEntry(e)))fail('INVALID_LOCAL_STATE','原工作私稿或请求引用需要核对。');
    const bytes=JSON.stringify(next);if(Buffer.byteLength(bytes)+workInputReserved(next.requests)+workDeliveries.reservedBytes(next.work_delivery_drafts)+nativeExports.reservedBytes(next.native_export_records)+workMaintenance.reservedBytes(next.work_maintenance_drafts)>1024*1024)fail('LOCAL_CAPACITY','本地私稿已达到 1 MiB 上限。');
    const old=JSON.parse(boundedFile(this.filename,1024*1024));if(old.revision!==this.value.revision)fail('LOCAL_STATE_CONFLICT','私稿版本发生冲突，已停止发送。');
    const temp=this.filename+'.next';try{const fd=fs.openSync(temp,'wx',0o600);try{fs.writeFileSync(fd,bytes);fs.fsyncSync(fd);}finally{fs.closeSync(fd);}fs.renameSync(temp,this.filename);this.value=next;}catch(e){if(fs.existsSync(temp))fs.unlinkSync(temp);throw e;}
  }
  recoverable(){return fs.readdirSync(this.directory).filter(n=>/^[a-f0-9-]{36}\.json$/.test(n)&&path.join(this.directory,n)!==this.filename).slice(0,32).flatMap(name=>{
    try{const v=JSON.parse(boundedFile(path.join(this.directory,name),1024*1024));return v.schema==='eeas.ide-private.v1'&&Array.isArray(v.requests)&&v.drafts&&typeof v.drafts==='object'&&(Object.keys(v.drafts).length+Object.keys(v.child_drafts||{}).length+Object.keys(v.editor_observers||{}).length+Object.keys(v.configuration_drafts||{}).length+Object.keys(v.work_requirement_drafts||{}).length+Object.keys(v.work_case_drafts||{}).length+Object.keys(v.work_retest_drafts||{}).length+Object.keys(v.work_source_drafts||{}).length+Object.keys(v.work_delivery_drafts||{}).length+Object.keys(v.native_export_records||{}).length+Object.keys(v.work_maintenance_drafts||{}).length||v.requests.length)?[{id:name,updated_at:v.updated_at,drafts:Object.keys(v.drafts).length+Object.keys(v.child_drafts||{}).length+Object.keys(v.configuration_drafts||{}).length+Object.keys(v.work_requirement_drafts||{}).length+Object.keys(v.work_case_drafts||{}).length+Object.keys(v.work_retest_drafts||{}).length+Object.keys(v.work_source_drafts||{}).length+Object.keys(v.work_delivery_drafts||{}).length+Object.keys(v.native_export_records||{}).length+Object.keys(v.work_maintenance_drafts||{}).length,observers:Object.keys(v.editor_observers||{}).length,requests:v.requests.length}]:[];}catch{return [];}
  });}
  recover(id){if(!/^[a-f0-9-]{36}\.json$/.test(id))fail('INVALID_LOCAL_REF','私稿引用无效。');const old=JSON.parse(boundedFile(path.join(this.directory,id),1024*1024));
    if(old.schema!=='eeas.ide-private.v1'||!old.drafts||!Array.isArray(old.requests))fail('INVALID_LOCAL_STATE','旧私稿格式无效。');
    try{if(old.selected!=null){const selected=validateScope(old.selected);if(!selected.conversation_ref&&(!selected.project_id||!selected.conversation_id&&!selected.session_ref))throw Error('Incomplete selection');}}catch{fail('INVALID_LOCAL_STATE','旧私稿引用无效。');}
    for(const [key,d]of Object.entries(old.drafts)){if(!validWorkDraft(d)||!/^[a-f0-9]{64}$/.test(key)||typeof d.text!=='string'||Array.from(d.text).length>16000||!Number.isSafeInteger(d.revision)||d.revision<0||draftKey(validateScope(d.scope))!==key||d.agent!==undefined&&!['plan','build','workflow'].includes(d.agent))fail('INVALID_LOCAL_STATE','旧私稿引用无效。');}
    if(old.child_drafts!==undefined&&(!old.child_drafts||typeof old.child_drafts!=='object'||Array.isArray(old.child_drafts)))fail('INVALID_LOCAL_STATE','旧子任务私稿格式无效。');
    for(const [key,d]of Object.entries(old.child_drafts||{})){const ref={id:d.child_id,root_native_session_id:d.root_native_session_id,sha256:'0'.repeat(64)};
      if(!/^[a-f0-9]{64}$/.test(key)||!sessionChild.validRef(ref)||sessionChild.draftKey(validateScope(d.scope),ref)!==key||typeof d.text!=='string'||Array.from(d.text).length>16000||!Number.isSafeInteger(d.revision)||d.revision<0)fail('INVALID_LOCAL_STATE','旧子任务私稿引用无效。');}
    for(const r of old.requests)if(typeof r.request_id!=='string'||!/^[A-Za-z0-9_-]{1,100}$/.test(r.request_id)||!['session.work.requirements.link','conversation.configuration.adoption.command','conversation.owner.select','conversation.configuration.continue','conversation.configure','conversation.message','conversation.connect','conversation.create','conversation.native.recovery.command','native.context.recovery.command','session.context.recovery.command','session.message','session.guidance','session.cancel','session.question.reply','session.question.reject','session.permission.reply','session.child.message','session.diff.review','session.editor.reserve','session.editor.control','session.editor.file.command','native.message','native.guidance','native.cancel','artifact.open','lifecycle.reveal','conversation.file.open','native.file.open','native.feedback.record','native.rework.apply'].includes(r.method)||!['OK','ERROR','UNKNOWN','PENDING'].includes(r.status)){fail('INVALID_LOCAL_STATE','旧请求引用无效。');}else validateScope(r.scope);
    for(const r of old.requests)if(r.method==='session.child.message'&&(!/^[a-f0-9]{64}$/.test(r.child_request_sha256||'')||!sessionChild.validRef(r.child_target)||!['CHILD_MESSAGE','CHILD_GUIDANCE'].includes(r.child_kind)||r.child_parent!==null&&typeof r.child_parent!=='string'||!Number.isSafeInteger(r.child_draft_revision)||r.child_draft_revision<0||r.child_draft_key!==sessionChild.draftKey(r.scope,r.child_target)))fail('INVALID_LOCAL_STATE','旧子任务请求证据无效。');
    for(const r of old.requests)if(r.method==='session.diff.review'&&(!sessionDiff.validReviewRequest(r.review_request,r.scope,r.operation_id)||r.review_key!==sessionDiff.reviewKey(r.scope,r.review_request)))fail('INVALID_LOCAL_STATE','旧审阅操作引用无效。');
    if(old.editor_changes!==undefined&&(!old.editor_changes||typeof old.editor_changes!=='object'||Array.isArray(old.editor_changes)||Object.keys(old.editor_changes).length>32))fail('INVALID_LOCAL_STATE','旧编辑交接记录无效。');
    for(const [key,value]of Object.entries(old.editor_changes||{}))if(!editorChange.validStored(value,key))fail('INVALID_LOCAL_STATE','旧编辑交接来源无效。');
    for(const r of old.requests)if(['session.editor.reserve','session.editor.control'].includes(r.method)){const slot=old.editor_changes?.[r.editor_slot_key];if(!slot||r.operation_id!==r.editor_request?.operation_id||canonical(r.scope)!==canonical(slot.scope)||(r.method==='session.editor.reserve'?canonical(r.editor_request)!==canonical(slot.request):!editorChange.controlValid(r.editor_request,slot)))fail('INVALID_LOCAL_STATE','旧编辑控制请求无效。');}
    if(!workMaintenance.validStores(old.work_maintenance_drafts))fail('INVALID_LOCAL_STATE','旧维护私稿无效。');
    if(!nativeExports.validStores(old.native_export_records))fail('INVALID_LOCAL_STATE','旧保存记录无效。');
    if(!workDeliveries.validStores(old.work_delivery_drafts))fail('INVALID_LOCAL_STATE','旧交付私稿无效。');
    if(!workSources.validStores(old.work_source_drafts))fail('INVALID_LOCAL_STATE','旧只读来源私稿无效。');
    if(!workRetests.validStores(old.work_retest_drafts))fail('INVALID_LOCAL_STATE','旧复测私稿无效。');
    if(!workCases.validStores(old.work_case_drafts))fail('INVALID_LOCAL_STATE','旧用例对应记录无效。');
    if(!workRequirements.validDrafts(old.work_requirement_drafts)||old.requests.some(r=>r.method==='session.work.requirements.link'&&!workRequirements.validEntry(r)))fail('INVALID_LOCAL_STATE','旧要求关联记录无效。');
    if(!connectionConfiguration.validDrafts(old.configuration_drafts))fail('INVALID_LOCAL_STATE','旧执行配置草稿无效。');
    if(!canonicalConversations.validChoices(old.canonical_choices))fail('INVALID_LOCAL_STATE','旧模型选择无效。');
    for(const r of old.requests)if(!validWorkEntry(r))fail('INVALID_LOCAL_STATE','旧原工作请求无效。');
    for(const r of old.requests)if(r.canonical_kind&&!canonicalConversations.validEntry(r))fail('INVALID_LOCAL_STATE','旧逻辑会话请求无效。');
    for(const r of old.requests)if(['native.context.recovery.command','conversation.native.recovery.command'].includes(r.method)&&!nativeContextRecovery.validEntry(r))fail('INVALID_LOCAL_STATE','旧原生来源恢复记录无效。');
    for(const r of old.requests)if(r.method==='session.context.recovery.command'&&!contextRecovery.validEntry(r))fail('INVALID_LOCAL_STATE','旧来源恢复记录无效。');
    for(const r of old.requests)if(r.method==='session.editor.file.command'){const slot=old.editor_changes?.[r.editor_slot_key];if(!slot||r.operation_id!==r.file_request?.operation_id||canonical(r.scope)!==canonical(slot.scope)||!editorFiles.requestValid(r.file_request,slot))fail('INVALID_LOCAL_STATE','旧文件操作请求无效。');}
    if(old.editor_observers!==undefined&&(!old.editor_observers||typeof old.editor_observers!=='object'||Array.isArray(old.editor_observers)||Object.keys(old.editor_observers).length>16))fail('INVALID_LOCAL_STATE','旧编辑器观察记录无效。');
    for(const [key,value]of Object.entries(old.editor_observers||{}))if(!editorObserver.validStored(value,key))fail('INVALID_LOCAL_STATE','旧编辑器观察来源无效。');
    this.commit(v=>{if(Object.keys(v.work_maintenance_drafts||{}).length)fail('LOCAL_STATE_NOT_EMPTY','当前已有私稿，请在新窗口恢复。');v.work_maintenance_drafts=workMaintenance.recoverStores(old.work_maintenance_drafts);if(Object.keys(v.native_export_records||{}).length)fail('LOCAL_STATE_NOT_EMPTY','当前已有私稿，请在新窗口恢复。');v.native_export_records=nativeExports.recoverStores(old.native_export_records);if(Object.keys(v.work_delivery_drafts||{}).length)fail('LOCAL_STATE_NOT_EMPTY','当前已有私稿，请在新窗口恢复。');v.work_delivery_drafts=workDeliveries.recoverStores(old.work_delivery_drafts);if(Object.keys(v.work_source_drafts||{}).length)fail('LOCAL_STATE_NOT_EMPTY','当前已有私稿，请在新窗口恢复。');v.work_source_drafts=workSources.recoverStores(old.work_source_drafts);if(Object.keys(v.work_retest_drafts||{}).length)fail('LOCAL_STATE_NOT_EMPTY','当前已有私稿，请在新窗口恢复。');v.work_retest_drafts=workRetests.recoverStores(old.work_retest_drafts);if(Object.keys(v.work_case_drafts||{}).length)fail('LOCAL_STATE_NOT_EMPTY','当前已有私稿，请在新窗口恢复。');v.work_case_drafts=workCases.recoverStores(old.work_case_drafts);if(Object.keys(v.work_requirement_drafts||{}).length)fail('LOCAL_STATE_NOT_EMPTY','当前已有私稿，请在新窗口恢复。');v.work_requirement_drafts=copy(old.work_requirement_drafts||{});v.canonical_choices={...(old.canonical_choices||{}),...(v.canonical_choices||{})};if(Object.keys(v.drafts).length||Object.keys(v.child_drafts||{}).length||Object.keys(v.editor_changes||{}).length||Object.keys(v.editor_observers||{}).length||v.requests.length)fail('LOCAL_STATE_NOT_EMPTY','当前已有私稿，请在新窗口恢复。');v.configuration_drafts={...(old.configuration_drafts||{}),...(v.configuration_drafts||{})};v.drafts=copy(old.drafts);v.child_drafts=copy(old.child_drafts||{});v.editor_changes=Object.fromEntries(Object.entries(old.editor_changes||{}).map(([key,value])=>[key,{...copy(value),recovered:true,stage:['CLOSED','REJECTED'].includes(value.stage)?value.stage:'RECOVERED'}]));v.editor_observers=Object.fromEntries(Object.entries(old.editor_observers||{}).map(([key,value])=>[key,{...copy(value),recovered:true,stage:value.stage==='RETIRED'?'RETIRED':'RECOVERED',intents:value.intents.map(i=>({...copy(i),status:'UNKNOWN'})),recovery:value.recovery?{...copy(value.recovery),status:value.recovery.status==='PENDING'?'UNKNOWN':value.recovery.status}:null,capture:value.capture?{...copy(value.capture),status:value.capture.status==='PENDING'?'UNKNOWN':value.capture.status}:null}]));v.requests=copy(old.requests).map(r=>({...r,status:r.status==='PENDING'?'UNKNOWN':r.status,...(r.work_requirement_request&&['PENDING','UNKNOWN'].includes(r.status)?{work_requirement_initial_unknown:true,work_requirement_initial_observation:r.work_requirement_initial_observation??'UNKNOWN'}:{})}));v.selected=old.selected;});
  }
}
const sessionTool=require('./session-tool.cjs');
const {CommandTestReports}=require('./command-test-reports.cjs');
const sessionChild=require('./session-child.cjs');
class CompanionController{
  constructor({state,onChange=()=>{},newId=randomUUID}={}){this.state=state;this.onChange=onChange;this.newId=newId;this.client=null;this.epoch=0;this.refreshSequence=0;this.refreshError=null;this.projects=[];this.sessions=[];this.selected=null;this.projectId=null;this.status=null;this.error=null;this.loading=false;this.disposed=false;this.inflight=false;this.known=new Map();this.operationObservations=new Map();this.diffEpoch=0;this.childDiffEpoch=0;this.reviewEpoch=0;this.reviewContext=null;this.reviewLoading=null;this.reviewError=null;this.diffRead=null;this.diffs={owner:null,directory:null,loading:false,opening:null,error:null};this.childDiffs={owner:null,directory:null,loading:false,opening:null,error:null};this.nativeContext=null;this.nativeReadSequence=0;this.commandReports=new CommandTestReports(this,scopeOf,safeCode);this.workRequirements=new workRequirements.WorkRequirements(this,scopeOf,safeCode);this.workCases=new workCases.WorkCases(this,scopeOf,safeCode);this.workRetests=new workRetests.WorkRetests(this,scopeOf,safeCode);this.workSources=new workSources.WorkSources(this,scopeOf,safeCode);this.workDeliveries=new workDeliveries.WorkDeliveries(this,scopeOf,safeCode);this.nativeExports=new nativeExports.NativeExports(this,scopeOf,safeCode);this.workMaintenance=new workMaintenance.WorkMaintenance(this,scopeOf,safeCode);this.toolReadEpoch=0;this.toolDetail=null;this.toolLoading=null;this.toolError=null;this.childReadEpoch=0;this.childDetail=null;this.childLoading=null;this.childError=null;this.editor=new editorChange.EditorChangeController(this);this.editorObservers=new editorObserver.EditorObserverController(this);this.sourceRecovery=new contextRecovery.ContextRecovery(this);this.nativeSourceRecovery=new nativeContextRecovery.NativeContextRecovery(this);this.canonicalConversations=new canonicalConversations.CanonicalConversations(this);this.connectionConfiguration=new connectionConfiguration.ConnectionConfiguration(this);}
  resetDiffs(){this.workMaintenance?.reset();this.nativeExports?.reset();this.workDeliveries?.reset();this.workSources?.reset();this.workRetests?.reset();this.workCases?.reset();this.commandReports?.reset();this.workRequirements?.reset();this.nativeSourceRecovery?.reset();this.reviewEpoch++;this.reviewContext=null;this.reviewLoading=null;this.reviewError=null;this.childDiffEpoch++;this.childReadEpoch++;this.childDetail=null;this.childLoading=null;this.childError=null;this.toolReadEpoch++;this.toolDetail=null;this.toolLoading=null;this.toolError=null;this.nativeContext=null;this.diffEpoch++;this.diffs={owner:null,directory:null,loading:false,opening:null,error:null};this.childDiffs={owner:null,directory:null,loading:false,opening:null,error:null};}
  diffSnapshot(){const token=this.selected?ownerToken(this.selected):null;if(token!==this.diffs.owner){this.resetDiffs();this.diffs.owner=token;}const d=this.diffs.directory;
    return {scopeToken:token,loaded:Boolean(d),loading:this.diffs.loading||Boolean(this.diffRead),opening:this.diffs.opening,error:this.diffs.error,files:(d?.files||[]).map(file=>({path:file.path,status:file.status,additions:file.additions,deletions:file.deletions,content_scope:file.content_scope,availability:file.availability,...(file.source_scope?{source_generation:file.source_generation,source_scope:file.source_scope}:{}),token:entryToken(d.scope,d,file)})),coverage:d?.coverage||null};}
  emit(){if(!this.disposed)this.onChange(this.snapshot());}
  snapshot(){const s=this.selected,key=s?draftKey(s):null,d=key?this.state.value.drafts[key]:null;const differences=this.diffSnapshot();
    const unresolved=this.state.value.requests.filter(r=>['PENDING','UNKNOWN'].includes(r.status)&&!r.work_requirement_disposition&&!r.configuration_recovery_resolved),pending=unresolved.filter(r=>(!r.scope.conversation_id&&!r.scope.session_ref&&!r.scope.conversation_ref)||(key&&(draftKey(r.scope)===key||this.currentScope(r.scope))));
    const draftUnresolved=Boolean(d&&this.state.value.requests.some(r=>(r.draft_key===key||d.legacy_draft_key&&r.draft_key===d.legacy_draft_key)&&(r.draft_revision===d.revision||r.draft_body_sha256===hash(d.text))&&['PENDING','UNKNOWN'].includes(r.status)));
    return {workInput:this.workInputSnapshot(d,key),configurationPairingRequired:Boolean(this.client?.supportedMethods?.includes('conversation.configure')&&!this.connectionConfiguration.supported()||this.client?.supportedMethods?.includes('conversation.configuration.continue')&&!this.client?.methods?.includes('conversation.configuration.continue')),ownerSelection:this.canonicalConversations.ownerSnapshot(),connectionConfiguration:this.connectionConfiguration.snapshot(),canonical:this.canonicalConversations.snapshot(),sourceRecovery:this.sourceRecovery.snapshot(),nativeSourceRecovery:this.nativeSourceRecovery.snapshot(),editorObservers:this.editorObservers.snapshot(),editorChanges:this.editor.snapshot(),diffReview:this.reviewSnapshot(),privateChildDrafts:this.childPrivateSnapshot(),delegation:this.childSnapshot(),tools:this.toolSnapshot(),toolDetailsSupported:this.client?.methods?.includes("session.tool")===true,pendingControls:this.pendingControls(),pendingReplyMethods:this.client?.methods?.filter(m=>['session.question.reply','session.question.reject','session.permission.reply'].includes(m))||[],draftUnresolved,otherPendingCount:unresolved.length-pending.length,native:this.nativeSnapshot(),nativePairingRequired:Boolean(this.client?.supportedMethods?.includes('native.conversation.get')&&(!this.client?.methods?.includes('native.conversation.get')||!this.client?.methods?.includes('native.conversations.list'))),projects:this.projects,sessions:this.sessions,sessionTokens:this.sessions.map(s=>hash(canonical(navigationScope(s)))),projectId:this.projectId,session:s,artifactTokens:(s?.artifacts||[]).map(item=>validArtifact(s,item)?artifactToken(s,item):null),diffs:differences,scopeToken:key,draft:d||{text:'',revision:0},status:this.status,error:this.error,loading:this.loading,pending:pending.map(r=>{const observed=this.observationFor(r);return {request_id:r.request_id,method:r.method,scope:r.scope,operation_id:r.operation_id,status:r.status,observed_status:observed?.status||r.observed_status||null,observation_only:Boolean(observed)};}),recoverable:this.state.recoverable()};}
  workInputSnapshot(d,key){const choice=d?.original_work_choice,matches=workInput.originalWorkChoiceMatches(choice,this.selected);let local_status=null;
    if(choice&&matches)for(const e of [...this.state.value.requests].reverse()){if(e.draft_key!==key||e.status!=='OK')continue;try{
      if(e.work_input_choice&&canonical(e.work_input_choice.reference)===canonical(choice.reference)&&workInputCategory(e,e.work_input_receipt)==='ACCEPTED'&&e.work_input_receipt.role==='LOCAL_STATUS'){local_status=copy(e.work_input_receipt.local_status);break;}
      if(e.canonical_work_choice&&canonical(e.canonical_work_choice.reference)===canonical(choice.reference)&&canonicalConversations.receipt(e.canonical_receipt,e)==='ACCEPTED'&&e.canonical_receipt.local_status_returned){local_status=copy(e.canonical_receipt.local_status);break;}
    }catch{}}
    return {matches,local_status_input:!!choice&&matches&&workInput.originalWorkStatusText(d.text),local_status};
  }
  attach(client){this.operationObservations.clear();this.client?.close();this.client=client;this.epoch++;this.resetDiffs();this.refreshError=null;this.error=null;}
  nativeSnapshot(){
    const current=this.nativeContext,c=this.selected,isNative=nativeConversation.native(c),applied=[],seen=new Set();
    if(isNative)for(const entry of this.state.value.requests){
      if(entry.method!=='native.rework.apply'||entry.status!=='OK'||entry.native_receipt?.status!=='APPLIED'||!nativeConversation.same(entry.scope,scopeOf(c)))continue;
      try{
        nativeConversation.receipt(entry.native_receipt,entry);
        const message=c.native_messages?.find(m=>m.operation_id===entry.parent_operation_id),plan=entry.native_receipt.plan_ref;
        if(!message?.rework_previews?.some(p=>nativeConversation.planSummary(p)&&nativeConversation.same(p.record_ref,plan)))continue;
        // An actual current preview read wins over an old local observation.
        if(current&&this.nativeCurrent(current.owner)&&current.owner.parent===entry.parent_operation_id&&nativeConversation.same(current.preview?.plan_ref,plan)&&!current.preview.already_applied)continue;
        const key=hash(canonical([entry.parent_operation_id,plan]));if(seen.has(key))continue;seen.add(key);
        applied.push({parent_operation_id:entry.parent_operation_id,plan_ref:plan});
      }catch{/* An invalid local receipt never becomes a successful application display. */}
    }
    return {owner:isNative?draftKey(c)+':'+this.epoch:null,methods:(this.client?.methods||[]).filter(m=>m.startsWith('native.')),applied,
      context:current&&this.nativeCurrent(current.owner)?{parent_operation_id:current.owner.parent,token:current.owner.token,file:current.file?{file_source_ref:current.file.file_source_ref}:null,preview:current.preview?{token:current.previewToken,plan_ref:current.preview.plan_ref,path:current.preview.file_source_ref.path,can_apply:current.preview.can_apply,already_applied:current.preview.already_applied,current_basis_status:current.preview.current_basis_status}:null}:null};
  }

  nativeOwner(parent,token,method){if(!nativeConversation.native(this.selected)||!this.client?.methods?.includes(method))fail('NATIVE_CAPABILITY_REQUIRED','请重新配对以批准文件与返工操作。');const message=this.selected.native_messages?.find(m=>m.operation_id===parent);if(!message||token!==nativeConversation.messageToken(this.selected,message))fail('SOURCE_CHANGED','原消息或来源已变化，请重新读取。');return {parent,token,epoch:this.epoch,scope:scopeOf(this.selected),client:this.client,message};}
  nativeCurrent(owner){return !!owner&&!this.disposed&&owner.epoch===this.epoch&&owner.client===this.client&&nativeConversation.native(this.selected)&&nativeConversation.same(owner.scope,scopeOf(this.selected))&&this.selected.native_messages?.some(m=>m.operation_id===owner.parent&&nativeConversation.messageToken(this.selected,m)===owner.token);}
  async nativeFile(parent,token,{forOpen=false}={}){
    const sequence=++this.nativeReadSequence,owner=this.nativeOwner(parent,token,'native.file.read');
    const file=nativeConversation.validateFile(await this.read('native.file.read',owner.scope,{
      parent_operation_id:parent,version:'CURRENT_SAVED',
    }),owner.scope,parent);
    if(sequence!==this.nativeReadSequence||!this.nativeCurrent(owner))return null;
    if(!nativeConversation.fileIdentity(owner.message.file_source_ref,file.baseline_file_source_ref))
      fail('SOURCE_CHANGED','文件来源已变化。');
    const target=forOpen&&nativeOpenTypes.has(path.extname(file.file_source_ref.path).toLowerCase())?'native':'editor';
    if(!file.current||file.live_sha256!==file.file_source_ref.sha256||target==='editor'&&file.availability!=='AVAILABLE')
      fail('SOURCE_UNAVAILABLE','已保存文件暂不可读取。');
    if(target==='editor'){
      const previous=this.nativeContext;
      this.nativeContext={owner,file,...(previous&&previous.owner.token===token?
        {preview:previous.preview,previewToken:previous.previewToken}:{})};
      this.emit();
    }
    return {owner,file,target};
  }
  async nativeOpen(parent,token){
    const result=await this.nativeFile(parent,token,{forOpen:true});
    if(!result)return null;
    const {owner,file,target}=result;
    this.nativeOwner(parent,token,'native.file.open');
    const key=hash(canonical({scope:owner.scope,parent,file:file.file_source_ref}));
    if(this.state.value.requests.some(r=>r.native_open_key===key&&['PENDING','UNKNOWN'].includes(r.status)))
      fail('ORIGINAL_REQUEST_UNRESOLVED','请先核查原打开请求。');
    const envelope=await this.mutation('native.file.open',owner.scope,{
      parent_operation_id:parent,file_source_ref:file.file_source_ref,target,
    },{parent_operation_id:parent,native_open_key:key});
    if(!this.nativeCurrent(owner)||envelope.status!=='OK')return null;
    const opened=envelope.result;
    const expected=target==='native'?'OPEN_REQUESTED':'EDITOR_READY';
    if(opened?.kind!=='NATIVE_FILE_OPEN'||!nativeConversation.same(nativeConversation.responseScope(opened),owner.scope)
      ||opened.parent_operation_id!==parent||opened.status!==expected
      ||!nativeConversation.same(opened.file_source_ref,file.file_source_ref)||opened.editor_buffer_modified!==false
      ||target==='native'&&(opened.native_software_open_confirmed!==false||opened.editor_uri!==undefined))
      fail('SOURCE_CHANGED','打开回执与原文件不符。');
    return {owner,file,opened};
  }
  async nativePreview(parent,token,index){const sequence=++this.nativeReadSequence,owner=this.nativeOwner(parent,token,'native.rework.preview'),summary=owner.message.rework_previews?.[index];if(!nativeConversation.planSummary(summary))fail('SOURCE_CHANGED','预览已变化。');const value=nativeConversation.validatePreview(await this.read('native.rework.preview',owner.scope,{parent_operation_id:parent,plan_ref:summary.record_ref}),owner.scope,parent,summary.record_ref);if(sequence!==this.nativeReadSequence||!this.nativeCurrent(owner))return null;for(const k of ['mapping_ref','native_feedback_ref','proposal_ref'])if(!nativeConversation.same(value[k],summary[k]))fail('SOURCE_CHANGED','预览引用不符。');this.nativeContext={owner,...(this.nativeContext?.owner.token===token?{file:this.nativeContext.file}:{}),preview:value,previewToken:hash(canonical(value))};this.emit();return {owner,value};}
  nativeQuote(owner,file,quote,selection,expectedRevision){if(!this.nativeCurrent(owner))fail('DRAFT_SCOPE_MISMATCH','会话已变化。');const key=draftKey(this.selected),old=this.state.value.drafts[key]||{text:'',revision:0,scope:owner.scope};if(old.revision!==expectedRevision)fail('DRAFT_CONFLICT','输入已变化，请重新选文。');const checked=nativeConversation.selection(file.file_source_ref,file.content,selection.start,selection.end);if(checked.quote!==quote)fail('SOURCE_CHANGED','选文已变化。');this.state.commit(v=>{v.drafts[key]={...old,revision:old.revision+1,native_quote:{parent_operation_id:owner.parent,token:owner.token,file_source_ref:file.file_source_ref,...checked}};});this.emit();}
  clearNativeQuote(token,revision){if(!this.selected||draftKey(this.selected)!==token)fail('DRAFT_SCOPE_MISMATCH','会话已变化。');const d=this.state.value.drafts[token];if(!d||d.revision!==revision)fail('DRAFT_CONFLICT','输入已变化。');this.state.commit(v=>{delete v.drafts[token].native_quote;v.drafts[token].revision++;});this.emit();}
  async nativeFeedback(token,revision){const d=this.state.value.drafts[token],q=d?.native_quote;if(!this.selected||draftKey(this.selected)!==token||!q||d.revision!==revision||!d.text.trim())fail('DRAFT_CONFLICT','请保留选句并填写反馈。');const owner=this.nativeOwner(q.parent_operation_id,q.token,'native.feedback.record'),basis=hash(canonical({scope:owner.scope,parent:owner.parent,source:q.file_source_ref,selection:q.selection,comment:d.text}));const prior=this.state.value.requests.find(r=>r.feedback_basis===basis&&r.status!=='ERROR');if(prior){if(['PENDING','UNKNOWN'].includes(prior.status)){await this.inspectOperation(prior.request_id);return null;}return prior.native_receipt?.status==='RECORDED'?prior.native_receipt:null;}const request={operation_id:this.newId(),file_source_ref:q.file_source_ref,comment:d.text,selection:q.selection};const result=await this.mutation('native.feedback.record',owner.scope,{parent_operation_id:owner.parent,...request},{parent_operation_id:owner.parent,native_request:request,feedback_basis:basis});return result.status==='OK'&&this.nativeCurrent(owner)?result.result:null;}
  async nativeApply(parent,token,previewToken,file,userApproved,allowRebase=false){const owner=this.nativeOwner(parent,token,'native.rework.apply'),context=this.nativeContext;if(userApproved!==true||!context?.preview||context.previewToken!==previewToken||!this.nativeCurrent(context.owner)||!context.preview.can_apply||context.preview.already_applied||!nativeConversation.fileIdentity(file,context.preview.file_source_ref))fail('REVIEW_REQUIRED','请读取精确预览并明确批准。');if(this.state.value.requests.some(r=>r.method==='native.rework.apply'&&nativeConversation.same(r.native_request?.plan_ref,context.preview.plan_ref)&&(['PENDING','UNKNOWN'].includes(r.status)||r.native_receipt?.status==='APPLIED')))fail('ORIGINAL_REQUEST_UNRESOLVED','请先核查原应用操作。');const request={operation_id:this.newId(),plan_ref:context.preview.plan_ref,expected_current_sources:[file],allow_rebase:allowRebase===true};const result=await this.mutation('native.rework.apply',owner.scope,{parent_operation_id:parent,...request,user_approved:true},{parent_operation_id:parent,native_request:request});if(this.nativeCurrent(owner)&&result.status==='OK'&&result.result.status==='APPLIED'){context.preview.can_apply=false;context.preview.already_applied=true;this.emit();}return result;}
  async loadDiffs(token){const owner=ownerOf(this.selected||{});if(token!==ownerToken(this.selected))fail('DIFF_CHANGED','会话已变化，请刷新改动');this.diffSnapshot();if(this.diffRead)return;const lease=this.diffRead={},epoch=this.epoch,seq=++this.diffEpoch;this.diffs.loading=true;this.diffs.opening=null;this.diffs.error=null;this.emit();
    try{const value=validateDirectory(await this.read('session.diff',scopeOf(this.selected),{workspace_ref:owner.workspace_ref}),owner);if(epoch!==this.epoch||seq!==this.diffEpoch||token!==ownerToken(this.selected||{}))return;this.diffs.directory=value;}
    catch(e){if(epoch===this.epoch&&seq===this.diffEpoch){this.diffs.error=safeCode(e);this.diffs.directory=null;}}
    finally{if(this.diffRead===lease)this.diffRead=null;if(epoch===this.epoch&&seq===this.diffEpoch)this.diffs.loading=false;this.emit();}}
  async readDiff(index,token){this.diffSnapshot();const directory=this.diffs.directory,owner=ownerOf(this.selected||{}),entry=directory?.files[index];if(!entry||this.diffs.loading||entryToken(owner,directory,entry)!==token)fail('DIFF_CHANGED','快照已变化，请刷新改动');if(this.diffRead)return null;if(entry.availability!=='AVAILABLE')fail('DIFF_UNAVAILABLE','部分改动暂不可读取');const lease=this.diffRead={},epoch=this.epoch,seq=this.diffEpoch,scopeToken=ownerToken(this.selected);this.diffs.opening=token;this.diffs.error=null;this.emit();
    try{const value=await this.read('session.diff.file',scopeOf(this.selected),{workspace_ref:owner.workspace_ref,snapshot_ref:directory.snapshot_ref,diff_ref:entry.diff_ref});if(epoch!==this.epoch||seq!==this.diffEpoch||scopeToken!==ownerToken(this.selected||{}))return null;return validateFile(value,owner,directory,entry);}
    catch(e){if(epoch!==this.epoch||seq!==this.diffEpoch||scopeToken!==ownerToken(this.selected||{}))return null;this.diffs.error=safeCode(e);throw e;}
    finally{if(this.diffRead===lease)this.diffRead=null;if(epoch===this.epoch&&seq===this.diffEpoch)this.diffs.opening=null;this.emit();}}
  toolSnapshot(){const scope=this.selected?scopeOf(this.selected):null,detail=this.toolDetail;
    if(detail&&(!scope||!sessionTool.find(this.selected,scope,detail.token)))this.toolDetail=null;
    if(this.toolLoading&&(!scope||!sessionTool.find(this.selected,scope,this.toolLoading))){this.toolReadEpoch++;this.toolLoading=null;}
    if(this.toolError&&(!scope||!sessionTool.find(this.selected,scope,this.toolError.token)))this.toolError=null;
    return {maintenance:this.workMaintenance.snapshot(),native_exports:this.nativeExports.snapshot(),deliveries:this.workDeliveries.snapshot(),sources:this.workSources.snapshot(),retests:this.workRetests.snapshot(),cases:this.workCases.snapshot(),requirements:this.workRequirements.snapshot(),reports:this.commandReports.snapshot(),scopeToken:this.selected?draftKey(this.selected):null,detail:this.toolDetail,loading:this.toolLoading,error:this.toolError,
      entries:scope?(this.selected.messages||[]).flatMap(message=>(message.parts||[]).flatMap(part=>sessionTool.validRef(part.tool_ref)?[{message_id:message.info?.id,part_id:part.id,token:sessionTool.token(scope,part.tool_ref)}]:[])):[]};}
  childSnapshot(){const scope=this.selected?scopeOf(this.selected):null;
    if(this.childDetail&&(!scope||!sessionChild.find(this.selected,scope,this.childDetail.token)))this.childDetail=null;
    if(this.childLoading&&(!scope||!sessionChild.find(this.selected,scope,this.childLoading))){this.childReadEpoch++;this.childLoading=null;}
    if(this.childError&&(!scope||!sessionChild.find(this.selected,scope,this.childError.token)))this.childError=null;
    const reference=this.childDetail&&scope?sessionChild.find(this.selected,scope,this.childDetail.token):null,
      key=reference?sessionChild.draftKey(scope,reference):null,saved=key?this.state.value.child_drafts?.[key]:null;
    return {supported:this.client?.methods?.includes('session.child')===true,sendSupported:this.client?.methods?.includes('session.child.message')===true,scopeToken:this.selected?draftKey(this.selected):null,
      draft:key?{key,token:this.childDetail.token,text:saved?.text??'',revision:saved?.revision??0}:null,
      draftUnresolved:Boolean(key&&this.state.value.requests.some(row=>row.child_draft_key===key&&['PENDING','UNKNOWN'].includes(row.status))),
      active:this.selected?.active_child_operation??null,ownerReady:this.selected?.owner_status==='READY'&&this.selected?.connected_owner===true&&this.selected?.configuration_current===true,
      canGuide:Boolean(reference&&!this.selected?.active_child_operation&&(this.selected?.child_guidance_targets||[]).some(row=>row.parent_operation_id===this.selected.active_operation_id&&row.generation===this.selected.generation&&row.status==='RUNNING_FOREGROUND'&&canonical(row.child_ref)===canonical(reference))),
      detail:this.childDetail,loading:this.childLoading,error:this.childError,diffs:this.childDiffSnapshot(),
      entries:scope?(this.selected.children||[]).flatMap(child=>sessionChild.validRef(child.child_ref)?[{title:child.title,token:sessionChild.token(scope,child.child_ref)}]:[]):[]};}
  reviewCurrent(context=this.reviewContext){if(!context||!this.selected||ownerToken(this.selected)!==context.ownerToken)return false;
    const directory=context.child?this.childDiffs.directory:this.diffs.directory;
    return Boolean(directory&&canonical(directory.snapshot_ref)===canonical(context.directory.snapshot_ref)&&directory.files.some(entry=>entryToken(directory.scope,directory,entry)===context.entryToken)&&(!context.child||this.childDiffBasis()?.token===context.childToken));}
  reviewSnapshot(){if(this.reviewContext&&!this.reviewCurrent())this.reviewContext=null;const c=this.reviewContext,p=c?.value;
    return {loading:this.reviewLoading,error:this.reviewError,supported:['session.diff.prepare','session.diff.review','session.diff.review.operation'].every(m=>this.client?.methods?.includes(m)),
      detail:p?{token:c.token,path:p.diff.path,matches_before:p.matches_before,matches_after:p.matches_after,review:p.review,recorded:c.recorded===true,fileStatus:p.diff.status,editorSupported:['session.editor.reserve','session.editor.get','session.editor.control','session.editor.control.operation',...(p.diff.status==='modified'?[]:editorFiles.METHODS)].every(m=>this.client?.methods?.includes(m)),canRestore:['modified','added','deleted'].includes(p.diff.status)&&p.matches_after,canApply:['modified','added','deleted'].includes(p.diff.status)&&p.matches_before,unresolved:this.state.value.requests.some(r=>r.review_key===c.reviewKey&&['PENDING','UNKNOWN'].includes(r.status))}:null};}
  async prepareDiffReview(index,token,child=false){this.diffSnapshot();this.childDiffSnapshot();const directory=child?this.childDiffs.directory:this.diffs.directory,owner=ownerOf(this.selected||{}),entry=directory?.files[index];
    if(!entry||entryToken(owner,directory,entry)!==token||entry.content_scope!=='FULL_FILE'||entry.availability!=='AVAILABLE')fail('DIFF_CHANGED','需要重新读取完整历史差异。');
    if(!this.client?.methods?.includes('session.diff.prepare'))fail('CAPABILITY_DENIED','请重新配对以审阅原差异。');if(this.diffRead)return;
    const lease=this.diffRead={},epoch=this.epoch,seq=++this.reviewEpoch,context={ownerToken:ownerToken(this.selected),child,childToken:child?this.childDiffBasis()?.token:null,directory,entryToken:token};this.reviewContext=null;this.reviewError=null;this.reviewLoading=token;this.emit();
    const current=()=>epoch===this.epoch&&seq===this.reviewEpoch&&this.reviewCurrent(context);
    try{const selection=sessionDiff.reviewSelection(owner,directory,entry),value=sessionDiff.validatePreparation(await this.read('session.diff.prepare',scopeOf(this.selected),{workspace_ref:selection.workspace_ref,snapshot_ref:selection.snapshot_ref,diff_ref:selection.diff_ref,child_ref:selection.child_ref}),owner,directory,entry);
      if(current())this.reviewContext={...context,value,selection,scope:scopeOf(this.selected),reviewKey:sessionDiff.reviewKey(owner,selection),token:hash(canonical({selection,preparation_sha256:value.preparation_sha256}))};}
    catch(error){if(current())this.reviewError=safeCode(error);}
    finally{if(this.diffRead===lease)this.diffRead=null;if(epoch===this.epoch&&seq===this.reviewEpoch)this.reviewLoading=null;this.emit();}}
  async decideDiffReview(token,decision){const c=this.reviewContext;if(!this.reviewCurrent(c)||c.recorded||c.token!==token||!['ACCEPT','REJECT'].includes(decision))fail('DIFF_CHANGED','原审阅版本已变化。');
    if(!this.client?.methods?.includes('session.diff.review'))fail('CAPABILITY_DENIED','请重新配对以记录审阅。');
    if(this.state.value.requests.some(r=>r.review_key===c.reviewKey&&['PENDING','UNKNOWN'].includes(r.status)))fail('ORIGINAL_REQUEST_UNRESOLVED','请先核查原审阅请求。');
    const request={...c.selection,operation_id:this.newId(),decision,preparation_sha256:c.value.preparation_sha256,expected_review_revision:c.value.review_revision};
    const result=await this.mutation('session.diff.review',c.scope,{workspace_ref:request.workspace_ref,snapshot_ref:request.snapshot_ref,diff_ref:request.diff_ref,child_ref:request.child_ref,operation_id:request.operation_id,decision,preparation_sha256:request.preparation_sha256,expected_review_revision:request.expected_review_revision},{review_request:request,review_key:c.reviewKey});
    if(this.reviewCurrent(c)&&this.reviewContext===c&&result.status==='OK'&&result.result?.status==='RETURNED'){this.reviewError=null;this.emit();}return result;}
  applyReviewEnvelope(entry,envelope){let category='UNKNOWN';try{if(envelope.status==='OK')category=sessionDiff.reviewReceipt(envelope.result,entry);else if(envelope.status==='ERROR'&&envelope.error?.code==='API_REQUEST_REJECTED')category='ERROR';}catch{category='UNKNOWN';}
    if(category==='OK'&&this.reviewCurrent()&&this.reviewContext.value.preparation_sha256===entry.review_request.preparation_sha256){this.reviewContext.recorded=true;this.reviewContext.value.review={revision:envelope.result.review_ref.revision,decision:envelope.result.review_decision,operation_id:entry.operation_id,review_ref:envelope.result.review_ref};}
    try{this.mark(entry.request_id,category);if(category==='UNKNOWN')this.known.delete(entry.request_id);else this.known.set(entry.request_id,envelope);if(this.currentScope(entry.scope))this.error=category==='UNKNOWN'?'ORIGINAL_REQUEST_UNRESOLVED':category==='ERROR'?'API_REQUEST_REJECTED':null;}catch{this.error='LOCAL_RECEIPT_UNPERSISTED';}this.emit();}
  childDiffBasis(){const session=this.selected,scope=session?scopeOf(session):null,reference=scope&&this.childDetail?sessionChild.find(session,scope,this.childDetail.token):null;
    if(!reference)return null;try{const owner=ownerOf(session);return {scope,owner,reference,token:hash(canonical({owner,child_ref:reference}))};}catch{return null;}}
  childDiffSnapshot(){const basis=this.childDiffBasis(),token=basis?.token??null;
    if(this.childDiffs.owner!==token){this.childDiffEpoch++;this.childDiffs={owner:token,directory:null,loading:false,opening:null,error:null};}
    const d=this.childDiffs.directory;return {scopeToken:token,supported:['session.child.diff','session.child.diff.file'].every(m=>this.client?.methods?.includes(m)),loaded:Boolean(d),loading:this.childDiffs.loading||Boolean(this.diffRead),opening:this.childDiffs.opening,error:this.childDiffs.error,
      files:(d?.files||[]).map(file=>({path:file.path,status:file.status,additions:file.additions,deletions:file.deletions,content_scope:file.content_scope,availability:file.availability,source_generation:file.source_generation,token:entryToken(d.scope,d,file)})),coverage:d?.coverage??null};}
  async loadChildDiffs(token){this.diffSnapshot();this.childDiffSnapshot();const basis=this.childDiffBasis();
    if(!basis||token!==basis.token)fail('CHILD_CHANGED','原子任务已变化，请刷新。');
    if(!['session.child.diff','session.child.diff.file'].every(m=>this.client?.methods?.includes(m)))fail('CAPABILITY_DENIED','请重新配对以读取子任务改动。');
    if(this.diffRead)return;const lease=this.diffRead={},epoch=this.epoch,seq=++this.childDiffEpoch;this.childDiffs.loading=true;this.childDiffs.opening=null;this.childDiffs.error=null;this.emit();
    const current=()=>epoch===this.epoch&&seq===this.childDiffEpoch&&this.childDiffBasis()?.token===token;
    try{const value=validateDirectory(await this.read('session.child.diff',basis.scope,{workspace_ref:basis.owner.workspace_ref,child_ref:basis.reference}),basis.owner,basis.reference);if(current())this.childDiffs.directory=value;}
    catch(error){if(current()){this.childDiffs.error=safeCode(error);this.childDiffs.directory=null;}}
    finally{if(this.diffRead===lease)this.diffRead=null;if(current())this.childDiffs.loading=false;this.emit();}}
  async readChildDiff(index,token){this.diffSnapshot();this.childDiffSnapshot();const basis=this.childDiffBasis(),directory=this.childDiffs.directory,entry=directory?.files[index];
    if(!basis||!entry||this.childDiffs.loading||entryToken(basis.owner,directory,entry)!==token)fail('DIFF_CHANGED','快照已变化，请刷新改动');
    if(this.diffRead)return null;if(entry.availability!=='AVAILABLE')fail('DIFF_UNAVAILABLE','部分改动暂不可读取');
    const lease=this.diffRead={},epoch=this.epoch,seq=this.childDiffEpoch;this.childDiffs.opening=token;this.childDiffs.error=null;this.emit();
    const current=()=>epoch===this.epoch&&seq===this.childDiffEpoch&&this.childDiffBasis()?.token===basis.token;
    try{const value=await this.read('session.child.diff.file',basis.scope,{workspace_ref:basis.owner.workspace_ref,child_ref:basis.reference,snapshot_ref:directory.snapshot_ref,diff_ref:entry.diff_ref});return current()?validateFile(value,basis.owner,directory,entry):null;}
    catch(error){if(!current())return null;this.childDiffs.error=safeCode(error);throw error;}
    finally{if(this.diffRead===lease)this.diffRead=null;if(current())this.childDiffs.opening=null;this.emit();}}
  childPrivateSnapshot(){const drafts=this.state.value.child_drafts||{},current=this.privateChildDraftView,
    entries=Object.entries(drafts).filter(([_key,row])=>row.text?.length).map(([key,row])=>({key,revision:row.revision}));
    if(current&&drafts[current.key]?.revision!==current.revision)this.privateChildDraftView=null;
    return {entries,view:this.privateChildDraftView??null};}
  readPrivateChildDraft(key){const draft=this.state.value.child_drafts?.[key];
    if(!/^[a-f0-9]{64}$/.test(key)||!draft||typeof draft.text!=='string'||Array.from(draft.text).length>16000)fail('INVALID_LOCAL_REF','原子任务私稿不可读取。');
    this.privateChildDraftView={key,revision:draft.revision,text:draft.text};this.emit();}
  setChildDraft(token,revision,text){const scope=this.selected?scopeOf(this.selected):null,reference=scope&&sessionChild.find(this.selected,scope,token);
    if(!reference||typeof text!=='string'||Array.from(text).length>16000)fail('DRAFT_SCOPE_MISMATCH','子任务私稿目标已变化或内容过长。');
    const key=sessionChild.draftKey(scope,reference),old=this.state.value.child_drafts?.[key]??{text:'',revision:0};
    if(old.revision!==revision)fail('DRAFT_CONFLICT','子任务私稿版本已变化，输入已保留。');
    this.state.commit(v=>{v.child_drafts??={};v.child_drafts[key]={scope,child_id:reference.id,root_native_session_id:reference.root_native_session_id,text,revision:revision+1};});
    this.emit();return revision+1;}
  async sendChild(token,revision){const scope=this.selected?scopeOf(this.selected):null,reference=scope&&sessionChild.find(this.selected,scope,token);
    if(!reference)fail('CHILD_CHANGED','原子任务已变化，输入已保留。');
    if(!this.client?.methods?.includes('session.child.message'))fail('CAPABILITY_DENIED','请重新配对以继续原子任务。');
    const key=sessionChild.draftKey(scope,reference),draft=this.state.value.child_drafts?.[key];
    if(!draft||draft.revision!==revision||!draft.text.trim())fail('DRAFT_CONFLICT','子任务私稿未同步或为空。');
    if(this.state.value.requests.some(row=>row.child_draft_key===key&&(row.child_draft_revision===revision||row.child_body_sha256===hash(draft.text))&&['PENDING','UNKNOWN'].includes(row.status)))fail('ORIGINAL_REQUEST_UNRESOLVED','这条子任务输入已提交，请先核查原操作。');
    const active=this.selected.active_child_operation,parent=this.selected.active_operation_id??null;
    const direct=active&&active.operation_id===parent&&active.status==='ACCEPTED'&&canonical(active.child_ref)===canonical(reference)&&!['UNKNOWN_OUTCOME','FAILED','DISPATCHING'].includes(active.guidance_status),
      delegated=!active&&(this.selected.child_guidance_targets||[]).some(row=>row.parent_operation_id===parent&&row.generation===scope.generation&&row.status==='RUNNING_FOREGROUND'&&canonical(row.child_ref)===canonical(reference));
    if(parent&&!direct&&!delegated)fail('CHILD_CHANGED','当前原操作不能接收这条子任务指导。');
    const epoch=this.epoch;await this.editorObservers.ensureSelected(null,()=>this.epoch===epoch&&this.currentScope(scope)&&this.state.value.child_drafts?.[key]?.revision===revision&&(this.selected?.active_operation_id??null)===parent&&canonical(sessionChild.find(this.selected,scope,token))===canonical(reference));
    const body={operation_id:this.newId(),session_ref:scope.session_ref,generation:scope.generation,child_ref:reference,
      raw_text:draft.text,timeout_seconds:300,expected_active_operation_id:parent};
    const result=await this.mutation('session.child.message',scope,{operation_id:body.operation_id,child_ref:reference,
      raw_text:body.raw_text,timeout_seconds:300,expected_active_operation_id:parent},{child_request_sha256:hash(canonical(body)),
      child_target:reference,child_parent:parent,child_kind:parent?'CHILD_GUIDANCE':'CHILD_MESSAGE',
      child_draft_key:key,child_draft_revision:revision,child_body_sha256:hash(draft.text)});
    await this.refreshSelected();return result;}
  async readChild(token){this.diffSnapshot();const scope=this.selected?scopeOf(this.selected):null,reference=scope&&sessionChild.find(this.selected,scope,token);
    if(!reference)fail('CHILD_CHANGED','原子任务身份已变化，请刷新。');
    if(!this.client?.methods?.includes('session.child'))fail('CAPABILITY_DENIED','请重新配对以读取子任务记录。');
    const epoch=this.epoch,sequence=++this.childReadEpoch;this.childLoading=token;this.childError=null;this.childDetail=null;this.emit();
    try{const value=sessionChild.validate(await this.read('session.child',scope,{child_ref:reference}),scope,reference);
      if(epoch===this.epoch&&sequence===this.childReadEpoch&&sessionChild.find(this.selected,scope,token))this.childDetail={token,value};
    }catch(error){if(epoch===this.epoch&&sequence===this.childReadEpoch)this.childError={token,code:safeCode(error)};}
    finally{if(epoch===this.epoch&&sequence===this.childReadEpoch){this.childLoading=null;this.emit();}}}
  async readTool(token){this.diffSnapshot();const scope=this.selected?scopeOf(this.selected):null,reference=scope&&sessionTool.find(this.selected,scope,token);
    if(!reference)fail('TOOL_CHANGED','原工具记录已变化，请刷新。');
    if(!this.client?.methods?.includes('session.tool'))fail('CAPABILITY_DENIED','请重新配对以读取工具详情。');
    const epoch=this.epoch,sequence=++this.toolReadEpoch;this.toolLoading=token;this.toolError=null;this.toolDetail=null;this.emit();
    try{const value=sessionTool.validate(await this.read('session.tool',scope,{tool_ref:reference}),scope,reference);
      if(epoch===this.epoch&&sequence===this.toolReadEpoch&&sessionTool.find(this.selected,scope,token))this.toolDetail={token,value};
    }catch(error){if(epoch===this.epoch&&sequence===this.toolReadEpoch)this.toolError={token,code:safeCode(error)};}
    finally{if(epoch===this.epoch&&sequence===this.toolReadEpoch){this.toolLoading=null;this.emit();}}}
  async read(method,scope={},payload={}){if(!this.client)fail('APP_NOT_CONNECTED','请先连接桌面 App。');const e=await this.client.rpc(method,scope,payload);if(e.status!=='OK')fail(e.error?.code||'READ_UNAVAILABLE','读取未完成，请稍后刷新。');return e.result;}
  async directory(projectId){
    if(this.canonicalConversations.supported())return this.canonicalConversations.directory(projectId);
    const legacy=await this.read('sessions.list',{project_id:projectId});
    if(!Array.isArray(legacy?.sessions))fail('READ_UNAVAILABLE','会话列表暂不可读取。');
    if(!this.client?.methods?.includes('native.conversations.list')||!this.client.methods.includes('native.conversation.get'))return legacy;
    const current=nativeConversation.directory(await this.read('native.conversations.list',{project_id:projectId}),projectId);
    return {sessions:[...legacy.sessions,...current.conversations],coverage:{truncated:legacy.coverage?.truncated===true||current.coverage.has_more}};
  }
  async readSelected(scope){if(scope.conversation_ref)return this.canonicalConversations.read(scope);const value=await this.read(scope.conversation_id?'native.conversation.get':'session.get',scope);return scope.conversation_id?nativeConversation.present(value,scope):value;}
  async refresh(){const epoch=this.epoch,projectId=this.projectId,sequence=++this.refreshSequence,current=()=>epoch===this.epoch&&projectId===this.projectId&&sequence===this.refreshSequence;this.loading=true;this.emit();try{
      const [status,projects,directory]=await Promise.all([this.read('status'),this.read('projects.list'),projectId?this.directory(projectId):null]);if(!current())return;
      let sessions=this.sessions,retainSelected=!projectId;if(projectId){if(!Array.isArray(directory?.sessions))fail('READ_UNAVAILABLE','会话列表暂不可读取。');for(const item of directory.sessions)if(navigationScope(item).project_id!==this.canonicalConversations.projectId(projectId))fail('SCOPE_MISMATCH','会话不属于当前工程。');sessions=directory.sessions.slice(-100);if(this.selected){const retained=directory.sessions.find(item=>canonical(navigationScope(item))===canonical(navigationScope(this.selected))),changed=directory.sessions.some(item=>sameDiscussion(item,this.selected)&&!retained);retainSelected=Boolean(retained)||directory.coverage?.truncated===true&&!changed;const visible=retained||(retainSelected?this.selected:null);if(visible&&!sessions.includes(visible))sessions=[visible,...sessions.slice(sessions.length>=100?1:0)];}}
      this.status=status;this.projects=this.canonicalConversations.projects((Array.isArray(projects?.projects)?projects.projects:[]).slice(0,128).map(p=>({id:p.id,name:p.name||p.title||p.id})).filter(p=>/^[A-Za-z0-9_-]{1,100}$/.test(p.id)));this.sessions=sessions;
      if(this.selected&&!retainSelected){this.selected=null;this.resetDiffs();}this.refreshError=null;this.error=null;
    }catch(e){if(current()){this.refreshError=safeCode(e);this.error=this.refreshError;}}finally{if(sequence===this.refreshSequence){this.loading=false;this.emit();}}}
  async selectProject(id){await this.workCases.flushAll();await this.workRetests.flushAll();await this.workSources.flushAll();await this.workDeliveries.flushAll();await this.nativeExports.flushAll();await this.workMaintenance.flushAll();if(!this.projects.some(p=>p.id===id))fail('INVALID_PROJECT','请选择已授权工程。');const epoch=++this.epoch;this.projectId=id;this.selected=null;this.sessions=[];this.refreshError=null;this.emit();
    try{const result=await this.directory(id);if(epoch!==this.epoch)return;this.sessions=(result?.sessions||[]).slice(-100);this.error=null;}catch(e){if(epoch===this.epoch)this.error=safeCode(e);}finally{if(epoch===this.epoch)this.emit();}}
  async selectSession(index,token){await this.workCases.flushAll();await this.workRetests.flushAll();await this.workSources.flushAll();await this.workDeliveries.flushAll();await this.nativeExports.flushAll();await this.workMaintenance.flushAll();const item=this.sessions[index];if(!item||token!==hash(canonical(navigationScope(item))))fail('INVALID_SESSION','会话列表已更新，请重新选择。');let scope=navigationScope(item);if(scope.project_id!==this.canonicalConversations.projectId(this.projectId))fail('SCOPE_MISMATCH','会话不属于当前工程。');
    const epoch=++this.epoch;this.selected=item;this.state.commit(v=>{v.selected=scope;});this.emit();
    try{if(scope.conversation_ref?.revision===0){const selected=await this.read('conversation.select',scope,{source_ref:item.source_ref});if(epoch!==this.epoch)return;scope={project_id:scope.project_id,conversation_ref:selected.conversation_ref};}const result=await this.readSelected(scope);if(epoch!==this.epoch)return;if(canonical(navigationScope(result))!==canonical(scope))fail('SCOPE_MISMATCH','返回的会话引用不匹配。');if(scope.conversation_ref){const key=draftKey(result),legacy=result.session_ref?hash(canonical({project_id:result.project_id,session_ref:result.session_ref})):null,old=legacy&&this.state.value.drafts[legacy];if(old&&!this.state.value.drafts[key])this.state.commit(v=>{v.drafts[key]={...copy(old),scope:copy(scope),legacy_draft_key:legacy};});this.state.commit(v=>{v.selected=copy(scope);});}this.selected=result;this.error=this.refreshError;}catch(e){if(epoch===this.epoch)this.error=safeCode(e);}finally{if(epoch===this.epoch)this.emit();}}
  async refreshSelected(){if(this.inflight||!this.selected)return;this.inflight=true;const epoch=this.epoch,scope=navigationScope(this.selected),current=()=>epoch===this.epoch&&this.selected&&canonical(navigationScope(this.selected))===canonical(scope);try{const s=await this.readSelected(scope);if(!current())return;if(canonical(navigationScope(s))!==canonical(scope))fail('SCOPE_MISMATCH','返回的会话引用不匹配。');this.selected=s;this.error=this.refreshError;}catch(e){if(current())this.error=safeCode(e);}finally{this.inflight=false;this.emit();}}
  setDraft(token,expectedRevision,text,agent){if(!this.selected||token!==draftKey(this.selected)||typeof text!=='string'||Array.from(text).length>16000)fail('DRAFT_SCOPE_MISMATCH','草稿范围已变化或内容过长。');
    const scope=navigationScope(this.selected),old=this.state.value.drafts[token]||{text:'',revision:0,scope};if(old.revision!==expectedRevision)fail('DRAFT_CONFLICT','草稿版本已变化，请保留输入并重新同步。');
    const mode=agent??old.agent;if(mode!==undefined&&(!['plan','build','workflow'].includes(mode)||nativeConversation.native(this.selected)&&mode==='workflow'))fail('INVALID_AGENT','请选择已支持的原Agent模式。');
    this.state.commit(v=>{v.drafts[token]={...old,text,...(mode!==undefined?{agent:mode}:{}),revision:old.revision+1,scope};});this.emit();return old.revision+1;}
  flushOriginalWorkInputs(){for(const [id,envelope]of [...this.known]){const e=this.state.value.requests.find(r=>r.request_id===id);if(!e||(!e.work_input_request&&!e.canonical_work_choice)||e.work_input_receipt||e.work_input_rejection||e.canonical_receipt)continue;if(e.canonical_work_choice)this.canonicalConversations.onEnvelope(e,envelope);else this.applyWorkInputEnvelope(e,envelope);const row=this.state.value.requests.find(r=>r.request_id===id);if(!row?.work_input_receipt&&!row?.work_input_rejection&&!row?.canonical_receipt)fail('LOCAL_RECEIPT_UNPERSISTED','原工作回执尚未保存。');}}
  async chooseOriginalWorkInput(token,revision,context){
    const selected=this.selected,epoch=this.epoch,view=this.workRequirements.view;
    if(!selected||nativeConversation.native(selected)||draftKey(selected)!==token||!this.workRequirements.current()||context!==this.workRequirements.contextToken())fail('ORIGINAL_WORK_INPUT_CHANGED','原工作依据已变化，输入保留。');
    const before=this.state.value.drafts[token]??{text:'',revision:0,scope:navigationScope(selected)};
    if(before.revision!==revision)fail('DRAFT_CONFLICT','先同步输入，再选择原工作。');
    this.flushOriginalWorkInputs();this.workRequirements.flushAll();for(const service of [this.workCases,this.workRetests,this.workSources,this.workDeliveries,this.nativeExports,this.workMaintenance])await service.flushAll();
    const choice=await workInput.makeOriginalWorkChoice(selected,{root_basis:view.root_basis,root_sha256:view.root_sha256,label:view.label});
    if(epoch!==this.epoch||this.selected!==selected||view!==this.workRequirements.view||context!==this.workRequirements.contextToken())fail('ORIGINAL_WORK_INPUT_CHANGED','原工作依据已变化，输入保留。');
    this.changeOriginalWorkInput(token,revision,choice);return choice;
  }
  changeOriginalWorkInput(token,revision,choice,copyId=null){
    if(!this.selected||draftKey(this.selected)!==token||nativeConversation.native(this.selected))fail('DRAFT_SCOPE_MISMATCH','请选择原会话。');
    if(choice!==undefined&&!workInput.originalWorkChoiceMatches(choice,this.selected))fail('ORIGINAL_WORK_INPUT_CHANGED','所选工作属于其他执行范围。');
    this.state.commit(v=>{const old=v.drafts[token]??{text:'',revision:0,scope:navigationScope(this.selected)};if(old.revision!==revision)fail('DRAFT_CONFLICT','输入已变化，请先同步。');
      const copies=copy(old.original_work_copies??[]),restored=copyId?copies.find(r=>r.id===copyId):null;if(copyId&&!restored)fail('DRAFT_CONFLICT','原工作副本已变化。');
      if(copyId)copies.splice(copies.indexOf(restored),1);
      if((choice!==undefined||copyId)&&!workInput.sameOriginalWorkChoice(old.original_work_choice,restored?.original_work_choice??choice)&&(old.text||old.original_work_choice)){
        const saved={text:old.text,...(old.agent?{agent:old.agent}:{}),...(old.original_work_choice?{original_work_choice:copy(old.original_work_choice)}:{})};
        if(!copies.some(r=>canonical({...r,id:null})===canonical({...saved,id:null}))){if(copies.length>=4)fail('LOCAL_CAPACITY','原工作副本已满；输入与旧副本均保留。');copies.push({id:this.newId(),...saved});}
      }
      const next={...old,...(restored?{text:restored.text,agent:restored.agent??old.agent}:{}),revision:old.revision+1,original_work_copies:copies};delete next.original_work_choice;
      const ref=restored?restored.original_work_choice:choice;if(ref)next.original_work_choice=workInput.validateOriginalWorkChoice(ref);v.drafts[token]=next;
    });this.emit();
  }
  restoreOriginalWorkInput(token,revision,id){const d=this.state.value.drafts[token],row=d?.original_work_copies?.find(r=>r.id===id);if(!row)fail('DRAFT_CONFLICT','原工作副本不存在。');return this.changeOriginalWorkInput(token,revision,row.original_work_choice,id);}
  applyWorkInputEnvelope(entry,envelope){
    const category=envelope.status==='ERROR'&&envelope.error?.code==='WORK_INPUT_ACTIVE_CHANGED'?'NOT_DISPATCHED':envelope.status==='OK'?workInputCategory(entry,envelope.result):'UNKNOWN';const cached=this.known.get(entry.request_id);if(category==='UNKNOWN'&&cached?.error?.code==='WORK_INPUT_ACTIVE_CHANGED')return this.applyWorkInputEnvelope(entry,cached);if(category==='UNKNOWN'&&['OK','ERROR'].includes(cached?.status)&&workInputCategory(entry,cached.result)!=='UNKNOWN')return this.applyWorkInputEnvelope(entry,cached);if(category!=='UNKNOWN')this.known.set(entry.request_id,category==='NOT_DISPATCHED'?copy(envelope):{status:category==='ACCEPTED'?'OK':'ERROR',result:workInputCompact(envelope.result)});
    const previous=this.state.value.requests.find(r=>r.request_id===entry.request_id);if(category==='UNKNOWN'&&previous?.work_input_rejection?.code==='WORK_INPUT_ACTIVE_CHANGED'){this.emit();return;}if(category==='UNKNOWN'&&['OK','ERROR'].includes(previous?.status)&&workInputCategory(previous,previous.work_input_receipt)!=='UNKNOWN'){this.known.set(entry.request_id,{status:previous.status,result:copy(previous.work_input_receipt)});this.emit();return;}
    try{this.state.commit(v=>{const row=v.requests.find(r=>r.request_id===entry.request_id);row.status=category==='ACCEPTED'?'OK':['FAILED','NOT_DISPATCHED'].includes(category)?'ERROR':'UNKNOWN';if(category==='NOT_DISPATCHED')row.work_input_rejection=copy(envelope.error);else if(category!=='UNKNOWN')row.work_input_receipt=workInputCompact(envelope.result);
      const d=v.drafts[entry.draft_key];if(category==='ACCEPTED'&&d&&d.revision===entry.draft_revision&&hash(d.text)===entry.draft_body_sha256&&workInput.sameOriginalWorkChoice(d.original_work_choice,entry.work_input_choice)){d.text='';d.revision++;}});
      if(category==='UNKNOWN')this.known.delete(entry.request_id);else this.known.set(entry.request_id,category==='NOT_DISPATCHED'?copy(envelope):{status:category==='ACCEPTED'?'OK':'ERROR',result:workInputCompact(envelope.result)});if(this.currentScope(entry.scope))this.error=category==='UNKNOWN'?'ORIGINAL_REQUEST_UNRESOLVED':['FAILED','NOT_DISPATCHED'].includes(category)?'API_REQUEST_REJECTED':null;
    }catch{this.error='LOCAL_RECEIPT_UNPERSISTED';}this.emit();
  }
  observationFor(entry){const observed=this.operationObservations.get(entry.request_id);return observed&&observed.operation_id===entry.operation_id&&nativeConversation.same(observed.scope,entry.scope)?observed:null;}
  currentScope(scope){if(!scope)return false;if(scope.conversation_ref)return !!this.selected&&canonical(navigationScope(this.selected))===canonical(scope);if(!scope.conversation_id&&!scope.session_ref)return scope.project_id===null?this.projectId===canonicalConversations.CHATS:!scope.project_id||scope.project_id===this.projectId;return Boolean(this.selected&&nativeConversation.same(scopeOf(this.selected),scope));}
  async mutation(method,scope,payload,meta={}){
    if(!this.client)fail('APP_NOT_CONNECTED','请先连接桌面 App。');const request_id=this.newId();const entry={request_id,method,scope:validateScope(scope),operation_id:payload.operation_id||meta.configuration_request?.request?.operation_id||null,status:'PENDING',...meta};
    this.state.commit(v=>{v.requests.push(entry);});this.emit();let envelope;
    try{envelope=await this.client.rpc(method,scope,payload,request_id);}catch(e){if(meta.work_requirement_request)return this.workRequirements.transportUnknown(request_id,e);this.mark(request_id,'UNKNOWN',meta.native_recovery_request?{native_recovery_initial_transport_status:'UNKNOWN',native_recovery_initial_transport_error:safeCode(e)}:{});if(this.currentScope(scope))this.error=safeCode(e);this.emit();return {status:'UNKNOWN',request_id};}
    this.applyEnvelope(request_id,envelope);return (meta.work_input_request||meta.work_requirement_request||meta.native_recovery_request||meta.canonical_kind||meta.recovery_request||meta.native_request||meta.child_request_sha256||meta.review_request||meta.editor_request||meta.file_request)&&['UNKNOWN','PENDING'].includes(this.state.value.requests.find(r=>r.request_id===request_id)?.status)?{status:'UNKNOWN',request_id}:envelope;
  }
  mark(id,status,extra={}){this.state.commit(v=>{const r=v.requests.find(r=>r.request_id===id);if(r)Object.assign(r,{status},extra);});}
  applyChildEnvelope(entry,envelope){let category='UNKNOWN';
    try{if(envelope.status==='OK')category=sessionChild.receipt(envelope.result,entry);
      else if(envelope.status==='ERROR'&&envelope.error?.code==='API_REQUEST_REJECTED')category='FAILED';}catch{category='UNKNOWN';}
    try{this.state.commit(v=>{const row=v.requests.find(r=>r.request_id===entry.request_id);if(!row)throw Error('Missing original child request');
      row.status=category==='UNKNOWN'?'UNKNOWN':category==='FAILED'?'ERROR':'OK';if(category!=='UNKNOWN'&&envelope.result)row.child_receipt=copy(envelope.result);
      const draft=v.child_drafts?.[entry.child_draft_key];if(category==='ACCEPTED'&&draft&&draft.revision===entry.child_draft_revision&&hash(draft.text)===entry.child_body_sha256){draft.text='';draft.revision++;}});
      if(category==='UNKNOWN')this.known.delete(entry.request_id);else this.known.set(entry.request_id,envelope);
      if(this.currentScope(entry.scope))this.error=category==='UNKNOWN'?'ORIGINAL_REQUEST_UNRESOLVED':['FAILED','NOT_DISPATCHED'].includes(category)?'API_REQUEST_REJECTED':null;
    }catch{this.error='LOCAL_RECEIPT_UNPERSISTED';}this.emit();}
  applyEnvelope(id,envelope){const entry=this.state.value.requests.find(r=>r.request_id===id);if(entry?.work_input_request)return this.applyWorkInputEnvelope(entry,envelope);if(entry?.work_requirement_request)return this.workRequirements.onEnvelope(entry,envelope);if(entry?.native_recovery_request)return this.nativeSourceRecovery.onEnvelope(entry,envelope);if(entry?.canonical_kind)return this.canonicalConversations.onEnvelope(entry,envelope);if(entry?.recovery_request)return this.sourceRecovery.onEnvelope(entry,envelope);if(entry?.file_request)return this.editor.files.onEnvelope(entry,envelope);if(entry?.editor_request)return this.editor.onEnvelope(entry,envelope);if(entry?.review_request)return this.applyReviewEnvelope(entry,envelope);if(entry?.child_request_sha256)return this.applyChildEnvelope(entry,envelope);if(entry?.native_request){try{if(envelope.status!=='OK')throw Error('Unconfirmed receipt');nativeConversation.receipt(envelope.result,entry);if(['UNKNOWN','PENDING','UNKNOWN_OUTCOME','INCOMPLETE_REVIEW_REQUIRED'].includes(envelope.result.status))throw Error('Pending receipt');this.mark(id,'OK',{native_receipt:envelope.result});if(entry.method==='native.rework.apply'&&envelope.result.status==='APPLIED'&&this.nativeContext?.preview&&this.nativeCurrent(this.nativeContext.owner)&&this.nativeContext.owner.parent===entry.parent_operation_id&&nativeConversation.same(this.nativeContext.preview.plan_ref,entry.native_request.plan_ref)){this.nativeContext.preview.can_apply=false;this.nativeContext.preview.already_applied=true;}this.known.set(id,envelope);this.emit();return;}catch{this.mark(id,'UNKNOWN');this.known.delete(id);if(this.selected&&nativeConversation.same(scopeOf(this.selected),entry.scope))this.error='ORIGINAL_REQUEST_UNRESOLVED';this.emit();return;}}const uncertain=(entry?.method?.startsWith('native.')||['session.question.reply','session.question.reject','session.permission.reply','conversation.file.open'].includes(entry?.method))&&(envelope.status==='OK'&&['UNKNOWN_OUTCOME','PENDING','UNKNOWN'].includes(envelope.result?.status)||envelope.status==='ERROR'&&['OPERATION_OUTCOME_UNKNOWN','OPERATION_INPUT_MISMATCH','BRIDGE_DISPATCH_FAILED','DISPATCH_UNCONFIRMED','RESPONSE_UNAVAILABLE'].includes(envelope.error?.code));if(uncertain)this.known.delete(id);else this.known.set(id,envelope);
    try{this.mark(id,uncertain?'UNKNOWN':envelope.status,{observed_status:envelope.result?.observed_status||null});
      const nativeAccepted=!entry?.method?.startsWith('native.')||['ACCEPTED','PROCESSING','RUNNING','RETURNED','SUCCEEDED','COMPLETED'].includes(envelope.result?.status);
      if(envelope.status==='OK'&&nativeAccepted&&entry?.draft_key){this.state.commit(v=>{const d=v.drafts[entry.draft_key];if(d&&d.revision===entry.draft_revision){d.text='';delete d.native_quote;d.revision++;}});}
      if(this.currentScope(entry?.scope))this.error=envelope.status==='ERROR'?(envelope.error?.code||'REQUEST_FAILED'):null;
    }catch{this.error='LOCAL_RECEIPT_UNPERSISTED';}this.emit();}
  async send(token,expectedRevision,agent='build',beforeNativeDispatch=null){if(!['plan','build','workflow'].includes(agent)||nativeConversation.native(this.selected)&&agent==='workflow')fail('INVALID_AGENT','请选择已支持的原Agent模式。');if(!this.selected||token!==draftKey(this.selected))fail('DRAFT_SCOPE_MISMATCH','请选择原会话后发送。');const d=this.state.value.drafts[token];if(d?.original_work_choice&&!workInput.originalWorkChoiceMatches(d.original_work_choice,this.selected))fail('ORIGINAL_WORK_INPUT_CHANGED','原工作范围已变化，输入保留。');if(!d||d.revision!==expectedRevision||!d.text.trim())fail('DRAFT_CONFLICT','草稿未同步或为空，请稍后再发。');
    if(d.agent!==undefined&&d.agent!==agent)fail('DRAFT_CONFLICT','草稿中的模式已变化，原输入保留。');
    if(!(d.original_work_choice&&workInput.originalWorkStatusText(d.text))&&agent==='workflow'&&!this.selected.active_operation_id&&(this.selected.workflow_mode_supported!==true||!this.selected.available_agent_modes?.includes('workflow')))fail('AGENT_MODE_UNAVAILABLE','原会话尚未确认此模式，请刷新连接后再发送。');
    if(this.state.value.requests.some(r=>r.draft_key===token&&(r.draft_revision===d.revision||r.draft_body_sha256===hash(d.text))&&['PENDING','UNKNOWN'].includes(r.status)))fail('ORIGINAL_REQUEST_UNRESOLVED','这条草稿已提交，请先核查原请求。');
    if(canonicalConversations.canonicalSession(this.selected))return this.canonicalConversations.send(token,expectedRevision,agent);
    const scope=scopeOf(this.selected);if(scope.conversation_id){
      if(d.original_work_choice)fail('ORIGINAL_WORK_INPUT_CHANGED','原生文稿协议不能使用工程工作关联。');
      const selected=this.selected,parent=selected.active_native_message;if(['STARTING','DISPATCHING','STOP_REQUESTED'].includes(parent?.native_message_phase))fail('SESSION_NOT_READY','原任务正在启动或停止，输入已保留。');if(d.native_quote&&parent)fail('SESSION_NOT_READY','请等原任务结束后发送选句反馈。');let feedback=null;if(d.native_quote){feedback=await this.nativeFeedback(token,expectedRevision);if(!feedback||feedback.selection_verification!=='TEXT_RANGE_HASH_VERIFIED'||!feedback.file_selection) return {status:'UNKNOWN'};if(this.selected!==selected||this.state.value.drafts[token]?.revision!==expectedRevision)return {status:'STALE'};}
      let method='native.message',payload={operation_id:this.newId(),raw_text:d.text},meta={draft_key:token,draft_revision:d.revision,draft_body_sha256:hash(d.text)};
      if(parent){if(parent.record_origin!=='CURRENT_HOST'||!(parent.control_capabilities?.can_guide||parent.control_capabilities?.can_status)||!nativeConversation.profile(parent.native_message_ref))fail('SESSION_NOT_READY','原任务暂不能补充，请核查原操作。');method='native.guidance';payload={...payload,parent_operation_id:parent.operation_id,expected_native_message_ref:parent.native_message_ref,intent:'AUTO'};meta.parent_operation_id=parent.operation_id;}
      else{const profile=nativeConversation.profile(selected.latest_provider_profile_ref);if(!profile)fail('NATIVE_MODEL_SELECTION_REQUIRED','请在桌面 App 选择模型后继续。');payload.provider_profile_ref=profile;if(feedback){payload.file_source_ref=feedback.file_source_ref;payload.native_feedback_ref=feedback.feedback_ref;}}
      if(method==='native.message')meta.native_body_sha256=hash(canonical({...scope,operation_id:payload.operation_id,raw_text:payload.raw_text,context_ref:null,provider_profile_ref:payload.provider_profile_ref,file_source_ref:payload.file_source_ref??null,file_link_ref:payload.file_link_ref??null,...(payload.native_feedback_ref?{native_feedback_ref:payload.native_feedback_ref}:{})}));
      if(feedback&&beforeNativeDispatch){await beforeNativeDispatch();if(this.selected!==selected||this.state.value.drafts[token]?.revision!==expectedRevision)return {status:'STALE'};}const result=await this.mutation(method,scope,payload,meta);await this.refreshSelected();return result;
    }
    if(scope.generation<1)fail('SESSION_NOT_READY','原会话尚未就绪，请在桌面端处理。');
    const selected=this.selected,epoch=this.epoch;if(!(d.original_work_choice&&workInput.originalWorkStatusText(d.text)))await this.editorObservers.ensureSelected(null,()=>this.selected===selected&&this.epoch===epoch&&this.state.value.drafts[token]?.revision===expectedRevision);
    if(this.selected!==selected||this.epoch!==epoch||this.state.value.drafts[token]?.revision!==expectedRevision)fail('DRAFT_CONFLICT','输入或会话已变化，尚未发送。');
    const payload={operation_id:this.newId(),raw_text:d.text,agent,...(d.original_work_choice?{original_work_ref:copy(d.original_work_choice.reference),expected_active_operation_id:selected.active_operation_id??null}:{})},meta={draft_key:token,draft_revision:d.revision,draft_body_sha256:hash(d.text)};
    if(d.original_work_choice){const request={operation_id:payload.operation_id,session_ref:copy(scope.session_ref),generation:scope.generation,raw_text:d.text,...(selected.active_operation_id||workInput.originalWorkStatusText(d.text)?{intent:'AUTO',expected_active_operation_id:selected.active_operation_id??null}:{agent,timeout_seconds:60}),original_work_ref:copy(d.original_work_choice.reference)};Object.assign(meta,{work_input_request:request,work_input_request_sha256:hash(canonical(request)),work_input_choice:copy(d.original_work_choice)});}
    const result=await this.mutation('session.message',scope,payload,meta);await this.refreshSelected();return result;}
  pendingControls(){
    const selected=this.selected;
    if(!selected||nativeConversation.native(selected))return [];
    return [...(selected.pending_questions||[]),...(selected.pending_permissions||[])].map(item=>({...item,
      token:hash(canonical({scope:scopeOf(selected),parent:selected.active_operation_id,kind:item.kind,request_ref:item.request_ref}))}));
  }
  async replyPending(token,{answers,reply,reject=false}={}){
    const selected=this.selected,item=this.pendingControls().find(row=>row.token===token);
    if(!item||item.display_complete!==true)fail('PENDING_REQUEST_CHANGED','原请求已变化或显示不完整，请刷新或在桌面 App 核查。');
    const permission=item.kind==='PERMISSION',method=permission?'session.permission.reply':reject?'session.question.reject':'session.question.reply';
    if(!this.client?.methods?.includes(method))fail('CAPABILITY_DENIED','此连接尚未获准回复问题或权限，请在桌面 App 更新配对授权。');
    if(this.state.value.requests.some(r=>r.pending_token===token&&['PENDING','UNKNOWN','OK'].includes(r.status)))fail('ORIGINAL_REQUEST_UNRESOLVED','请核查原答复，不能重复发送。');
    const payload={operation_id:this.newId(),request_ref:copy(item.request_ref)};
    if(permission){if(!item.allowed_choices?.includes(reply))fail('PERMISSION_CHOICE_DENIED','原权限策略不允许该答复。');payload.reply=reply;}
    else if(!reject){if(!Array.isArray(answers)||answers.length!==item.native_request.questions.length||answers.some(a=>!Array.isArray(a)||a.length<1||a.length>32||a.some(v=>typeof v!=='string'||!v.trim()||v.length>2000)))fail('INVALID_ANSWERS','请逐项回答原问题。');payload.answers=copy(answers);}
    if(permission&&reply!=='reject'&&item.native_request?.permission==='edit'){const epoch=this.epoch;await this.editorObservers.ensureSelected(item.request_ref,()=>this.selected===selected&&this.epoch===epoch&&this.pendingControls().some(r=>r.token===token));}
    const result=await this.mutation(method,scopeOf(selected),payload,{pending_token:token});
    await this.refreshSelected();return result;
  }
  async cancel(){if(!this.selected?.active_operation_id)fail('NO_ACTIVE_OPERATION','当前没有可请求停止的操作。');if(nativeConversation.native(this.selected)){const parent=this.selected.active_native_message;if(!parent?.control_capabilities?.can_cancel||parent.record_origin!=='CURRENT_HOST'||!nativeConversation.profile(parent.native_message_ref))fail('NO_ACTIVE_OPERATION','原任务暂不能请求停止。');return this.mutation('native.cancel',scopeOf(this.selected),{operation_id:parent.operation_id,expected_native_message_ref:parent.native_message_ref});}return this.mutation('session.cancel',scopeOf(this.selected),{operation_id:this.newId(),expected_active_operation_id:this.selected.active_operation_id});}
  async openArtifact(index,token){const session=this.selected,item=session?.artifacts?.[index];if(!session||!validArtifact(session,item)||token!==artifactToken(session,item))fail('ARTIFACT_CHANGED','文件已更新，请刷新');
    if(this.state.value.requests.some(row=>row.artifact_key===token&&['PENDING','UNKNOWN'].includes(row.status)))fail('ORIGINAL_REQUEST_UNRESOLVED','原请求尚未核清，未重复发送。');
    const payload=item.kind==='workspace-file'?{kind:'workspace-file',file_ref:item.file_ref}:{kind:'report',artifact_id:item.artifact_id,sha256:item.sha256,download_url:item.download_url};return this.mutation('artifact.open',scopeOf(session),payload,{artifact_key:token});}
  async inspect(id){const entry=this.state.value.requests.find(r=>r.request_id===id);if(!entry)fail('INVALID_REQUEST','原请求不存在。');
    const envelope=this.known.has(id)&&['OK','ERROR'].includes(this.known.get(id).status)?this.known.get(id):await this.client.inspect(id);
    if(envelope.status==='ERROR'&&['REQUEST_NOT_FOUND','PAIRING_REQUIRED','PAIRING_REVOKED','PAIRING_NOT_FOUND','CLIENT_SECRET_MISMATCH','BRIDGE_UNAVAILABLE'].includes(envelope.error?.code)){this.error=envelope.error.code;this.emit();return envelope;}
    this.applyEnvelope(id,envelope);return envelope;}
  async checkResult(id){const entry=this.state.value.requests.find(r=>r.request_id===id);if(!entry)fail('INVALID_REQUEST','原请求不存在。');if(entry.work_input_request||entry.work_requirement_request||entry.native_recovery_request||entry.canonical_kind||entry.recovery_request||entry.file_request||entry.editor_request||entry.review_request||entry.child_request_sha256||entry.scope.conversation_id&&entry.operation_id&&['native.message','native.feedback.record','native.rework.apply'].includes(entry.method))return this.inspectOperation(id);return this.inspect(id);}
  async inspectOperation(id){
    const entry=this.state.value.requests.find(r=>r.request_id===id);if(!entry?.operation_id)fail('NO_OPERATION_REF','此请求没有原业务操作引用。');
    if(entry.work_requirement_request)return this.workRequirements.inspect(id);
    if(entry.canonical_kind)return this.canonicalConversations.inspect(id);
    if(entry.native_recovery_request)return this.nativeSourceRecovery.inspectEntry(id);
    if(entry.recovery_request)return this.sourceRecovery.inspectEntry(id);
    if(entry.file_request){const value=await this.read('session.editor.file.operation',entry.scope,{operation_id:entry.operation_id});this.applyEnvelope(id,{status:'OK',result:value});return value;}
    if(entry.editor_request){const value=await this.read(entry.method==='session.editor.reserve'?'session.editor.get':'session.editor.control.operation',entry.scope,{operation_id:entry.operation_id});this.applyEnvelope(id,{status:'OK',result:value});return value;}
    if(entry.review_request){const value=await this.read('session.diff.review.operation',entry.scope,{operation_id:entry.operation_id});this.applyEnvelope(id,{status:'OK',result:value});return value;}
    if(entry.scope.conversation_id){
      if(entry.native_request){const value=await this.read(entry.method==='native.feedback.record'?'native.feedback.operation':'native.rework.operation',entry.scope,{parent_operation_id:entry.parent_operation_id,operation_id:entry.operation_id});this.applyEnvelope(id,{status:'OK',result:value});return value;}
      const value=await this.read(entry.method==='native.guidance'?'native.control':'native.operation',entry.scope,{operation_id:entry.operation_id,...(entry.method==='native.guidance'?{parent_operation_id:entry.parent_operation_id}:{})});
      if(entry.method==='native.message'){
        if(value?.kind!=='NATIVE_MESSAGE'||value.operation_id!==entry.operation_id||!nativeConversation.same(nativeConversation.responseScope(value),entry.scope)||!/^[a-f0-9]{64}$/.test(value.request_sha256||'')||typeof value.status!=='string'||value.status.length>80||entry.native_body_sha256&&value.request_sha256!==entry.native_body_sha256)fail('OPERATION_INPUT_MISMATCH','原消息回执与完整输入不一致，原稿保留。');
        if(entry.native_body_sha256)this.applyEnvelope(id,{status:'OK',result:value});
        else{this.operationObservations.set(id,{scope:copy(entry.scope),operation_id:entry.operation_id,status:value.status});this.emit();}
      }
      return value;
    }
    if(!entry.scope.session_ref)fail('NO_OPERATION_REF','此请求没有原业务操作引用。');const value=await this.read('session.operation',entry.scope,{operation_id:entry.operation_id});if(entry.child_request_sha256||entry.work_input_request)this.applyEnvelope(id,{status:'OK',result:value});return value;
  }

  async recover(id){await this.workCases.flushAll();await this.workRetests.flushAll();await this.workSources.flushAll();await this.workDeliveries.flushAll();await this.nativeExports.flushAll();await this.workMaintenance.flushAll();
    this.state.recover(id);
    const saved=this.state.value.selected,epoch=++this.epoch;
    if(!saved){this.emit();return {status:'COPIED_NO_SELECTION'};}
    const scope=validateScope(saved);
    if(scope.conversation_ref){if(!this.canonicalConversations.supported())fail('CAPABILITY_DENIED','请重新配对后恢复此会话。');await this.canonicalConversations.openReference(scope);const key=draftKey(this.selected);return {status:'RESTORED',scopeToken:key,revision:this.state.value.drafts[key]?.revision??0};}
    const current=()=>epoch===this.epoch&&this.projectId===scope.project_id;
    this.projectId=scope.project_id;this.selected=null;this.sessions=[];this.resetDiffs();this.emit();
    try{
      const directory=await this.directory(scope.project_id);if(!current())return {status:'STALE'};
      if(!Array.isArray(directory?.sessions))fail('READ_UNAVAILABLE','会话列表暂不可读取。');
      for(const item of directory.sessions)if(scopeOf(item).project_id!==scope.project_id)fail('SCOPE_MISMATCH','会话不属于当前工程。');
      const match=directory.sessions.find(item=>canonical(scopeOf(item))===canonical(scope));
      this.sessions=directory.sessions.slice(-100);
      if(!match)fail('RECOVERED_SESSION_CHANGED','原会话需重新核对，私稿已保留，请重新选择。');
      if(!this.sessions.includes(match))this.sessions=[match,...this.sessions.slice(this.sessions.length>=100?1:0)];
      const selected=await this.readSelected(scope);if(!current())return {status:'STALE'};
      if(canonical(scopeOf(selected))!==canonical(scope))fail('SCOPE_MISMATCH','返回的会话引用不匹配。');
      this.selected=selected;this.error=this.refreshError;
      const key=draftKey(selected);return {status:'RESTORED',scopeToken:key,revision:this.state.value.drafts[key]?.revision??0};
    }catch(e){if(!current())return {status:'STALE'};this.error=safeCode(e);throw e;}
    finally{if(current())this.emit();}
  }
  dispose(){this.editorObservers.dispose();this.editor.dispose();this.operationObservations.clear();this.disposed=true;this.epoch++;this.resetDiffs();this.client?.close();}
}
module.exports={PrivateState,CompanionController,scopeOf,draftKey,safeCode};
