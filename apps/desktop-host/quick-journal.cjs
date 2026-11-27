'use strict';
const {submissionObservation}=require('./quick-submission-observation.cjs');
const {conversationReference}=require('./conversation-reference.cjs');
const {validateOriginalWorkChoice,originalWorkStatusText}=require('../../plugins/vscode-companion/work-input-wire.cjs');

// Private, durable UI handoff. It grants no file/native authority and performs
// no model request. Saving/acknowledging a draft cannot dispatch a command.
const fs = require('node:fs');
const path = require('node:path');
const { createHash, randomUUID } = require('node:crypto');
const ID = /^[A-Za-z0-9_-]{1,100}$/;
const HASH = /^[a-f0-9]{64}$/;
const MAX_BYTES = 4 * 1024 * 1024;
const validId = value => typeof value === 'string' && ID.test(value);
const clone = value => JSON.parse(JSON.stringify(value));
function canonical(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}';
}
const digest = value => createHash('sha256').update(canonical(value)).digest('hex');
function requireValue(value, message) { if (!value) throw new Error(message); }
function boundedText(value, max = 16000) {
  requireValue(typeof value === 'string' && Array.from(value).length <= max && !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value), 'Invalid bounded Unicode text');
  return value;
}
function ref(value) {
  requireValue(value && typeof value === 'object' && !Array.isArray(value) &&
    Object.keys(value).sort().join(',') === 'id,revision,sha256' && validId(value.id) &&
    Number.isSafeInteger(value.revision) && value.revision >= 1 && HASH.test(value.sha256), 'Invalid exact quick reference');
  return { id: value.id, revision: value.revision, sha256: value.sha256 };
}
function fileLinkRef(value) {
  requireValue(value&&typeof value==='object'&&!Array.isArray(value)&&
    Object.keys(value).sort().join(',')==='id,project_id,revision,sha256'&&validId(value.project_id)&&
    HASH.test(value.id)&&Number.isSafeInteger(value.revision)&&value.revision>=1&&HASH.test(value.sha256),'Invalid exact project file link');
  return {project_id:value.project_id,id:value.id,revision:value.revision,sha256:value.sha256};
}
function nativeReview(value) {
  if(value==null)return null;
  const keys=(v,list)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join(',')===[...list].sort().join(',');
  if(value.version===2){
    requireValue(keys(value,['version','kind','selection','process_start_consent','remote_mcp_connect_consent','task_root_operation_id'])&&value.kind==='CAPABILITY_TASK'&&typeof value.process_start_consent==='boolean'&&typeof value.remote_mcp_connect_consent==='boolean'&&(value.task_root_operation_id===null||validId(value.task_root_operation_id)),'Invalid capability task input');
    const s=value.selection;
    requireValue(keys(s,['capability_binding_sha256','skill_ref','mcp_tool_selection'])&&HASH.test(s.capability_binding_sha256)&&Array.isArray(s.mcp_tool_selection)&&s.mcp_tool_selection.length<=64&&Buffer.byteLength(JSON.stringify(s))<=98304,'Invalid capability task selection');
    if(s.skill_ref!==null)ref(s.skill_ref);
    requireValue(s.skill_ref!==null||s.mcp_tool_selection.length>0,'Choose a skill or tool');
    const ids=new Set(),servers=new Map();
    for(const item of s.mcp_tool_selection){
      requireValue(keys(item,['server_id','config_ref','probe_ref','tool_id','name','input_schema_sha256'])&&validId(item.server_id)&&typeof item.tool_id==='string'&&/^[A-Za-z0-9_-]{1,200}$/.test(item.tool_id)&&!ids.has(item.tool_id)&&typeof item.name==='string'&&item.name.length>=1&&item.name.length<=256&&HASH.test(item.input_schema_sha256),'Invalid capability task tool');
      ref(item.config_ref);requireValue(item.config_ref.id===item.server_id&&keys(item.probe_ref,['id','sha256'])&&validId(item.probe_ref.id)&&HASH.test(item.probe_ref.sha256),'Exact real probe reference required');
      const basis=canonical([item.config_ref,item.probe_ref]);requireValue(!servers.has(item.server_id)||servers.get(item.server_id)===basis,'Conflicting server basis');servers.set(item.server_id,basis);ids.add(item.tool_id);
    }
    return clone(value);
  }
  requireValue(Buffer.byteLength(JSON.stringify(value))<=8192&&keys(value,['version','selection','process_start_consent'])&&value.version===1&&typeof value.process_start_consent==='boolean','Invalid bounded review input');
  const s=value.selection;
  requireValue(keys(s,['skill_ref','tool_selection','capability_binding_sha256','basis_ref'])&&HASH.test(s.capability_binding_sha256),'Invalid review selection');
  if(s.skill_ref!==null)ref(s.skill_ref);
  requireValue(s.skill_ref!==null||s.tool_selection!==null,'Choose a skill or tool');
  const tool=s.tool_selection;
  if(tool!==null){
    if(tool.kind==='BUILTIN_READONLY'){
      requireValue(keys(tool,['kind','tool_ref']),'Invalid builtin review tool');ref(tool.tool_ref);
      requireValue(tool.tool_ref.id==='planning_acceptance_coverage'&&tool.tool_ref.revision===1&&s.basis_ref!==null,'Exact review plan basis required');
    }else{
      requireValue(tool.kind==='USER_MCP'&&keys(tool,['kind','mcp_tool_selection']),'Invalid review tool source');
      const draft=tool.mcp_tool_selection;
      requireValue(keys(draft,['capability_binding_sha256','items'])&&draft.capability_binding_sha256===s.capability_binding_sha256&&Array.isArray(draft.items)&&draft.items.length===1,'Exact user MCP selection required');
      const item=draft.items[0];
      requireValue(keys(item,['server_id','config_ref','probe_ref','tool_id','name','input_schema_sha256'])&&validId(item.server_id)&&typeof item.tool_id==='string'&&/^[A-Za-z0-9_-]{1,200}$/.test(item.tool_id)&&typeof item.name==='string'&&item.name.length>=1&&item.name.length<=256&&HASH.test(item.input_schema_sha256),'Invalid user MCP tool');
      ref(item.config_ref);requireValue(keys(item.probe_ref,['id','sha256'])&&validId(item.probe_ref.id)&&HASH.test(item.probe_ref.sha256),'Exact real probe reference required');
    }
  }
  if(s.basis_ref!==null){const b=s.basis_ref;requireValue(keys(b,['kind','file_link_ref','proposal_ref','source_snapshot_sha256','candidate_sha256'])&&b.kind==='REPORT_PLAN'&&HASH.test(b.source_snapshot_sha256)&&HASH.test(b.candidate_sha256),'Invalid report plan basis');fileLinkRef(b.file_link_ref);ref(b.proposal_ref);}
  return clone(value);
}
function resetNativeReview(value,{newTask=false}={}) {
  const checked=nativeReview(value);if(!checked)return null;
  return checked.version===2?{...checked,process_start_consent:false,remote_mcp_connect_consent:false,...(newTask?{task_root_operation_id:null}:{})}:{...checked,process_start_consent:false};
}
function scope(value = {}) {
  const allowed = ['projectId','workspaceId','sessionId','providerProfileRef','conversationId','generation','fileLinkRef','fileLabel','conversationRef','originalSource','agent','contextSelectionId','contextSelectionOperationId','originalWorkChoice','workInputUnavailable'];
  requireValue(value && typeof value === 'object' && !Array.isArray(value) &&
    Object.keys(value).every(key => allowed.includes(key)), 'Unsupported quick scope');
  const result = {};
  for (const key of ['projectId','workspaceId','sessionId','conversationId']) if (value[key] != null) {
    requireValue(typeof value[key] === 'string' && ID.test(value[key]), 'Invalid quick scope identity');
    result[key] = value[key];
  }
  requireValue(!result.workspaceId || !!result.projectId, 'Workspace scope needs its project');
  requireValue(!result.sessionId || !!result.projectId, 'Session scope needs its project');
  if(value.contextSelectionId!=null){requireValue(typeof value.contextSelectionId==='string'&&HASH.test(value.contextSelectionId),'Invalid reviewed context selection');result.contextSelectionId=value.contextSelectionId;}
  if(value.contextSelectionOperationId!=null){requireValue(result.contextSelectionId&&validId(value.contextSelectionOperationId),'Invalid reviewed context choice operation');result.contextSelectionOperationId=value.contextSelectionOperationId;}
  if(value.conversationRef!=null) result.conversationRef=conversationReference(value.conversationRef);
  if(value.originalSource!=null){
    const source=value.originalSource;
    requireValue(result.conversationRef&&source&&typeof source==='object'&&!Array.isArray(source)&&
      Object.keys(source).sort().join(',')==='generation,project_id,session_ref'&&validId(source.project_id)&&
      Number.isSafeInteger(source.generation)&&source.generation>=1,'Invalid original quick owner');
    result.originalSource={project_id:source.project_id,session_ref:ref(source.session_ref),generation:source.generation};
  }
  if(value.originalWorkChoice!==undefined){const choice=validateOriginalWorkChoice(value.originalWorkChoice);requireValue(result.conversationRef&&result.originalSource&&choice.owner.project_id===result.originalSource.project_id&&digest(choice.owner.session_ref)===digest(result.originalSource.session_ref),'Original quick work owner differs');result.originalWorkChoice=choice;}
  if(value.workInputUnavailable!==undefined){requireValue(value.workInputUnavailable===true&&!result.originalWorkChoice,'Invalid unavailable original quick work');result.workInputUnavailable=true;}
  if(value.agent!=null){requireValue(result.conversationRef&&['plan','build','workflow'].includes(value.agent),'Invalid original quick agent');result.agent=value.agent;}
  if (value.providerProfileRef != null) result.providerProfileRef = ref(value.providerProfileRef);
  if(value.fileLinkRef!=null){result.fileLinkRef=fileLinkRef(value.fileLinkRef);requireValue(result.projectId===result.fileLinkRef.project_id,'File link belongs to another quick project');}
  if(value.fileLabel!=null){requireValue(result.fileLinkRef,'File label needs its exact project link');result.fileLabel=boundedText(value.fileLabel,240);requireValue(!/[\u0000-\u001f\u007f]/.test(result.fileLabel),'Invalid file label');}
  if (value.generation != null) {
    requireValue(Number.isSafeInteger(value.generation) && value.generation >= 0, 'Invalid scope generation');
    result.generation = value.generation;
  }
  return result;
}
function associationScope(value={}) {
  const checked=scope(value);
  return Object.fromEntries(['projectId','workspaceId','sessionId','conversationId'].filter(key=>checked[key]!==undefined).map(key=>[key,checked[key]]));
}
function associationKey(value) {
  const checked=associationScope(value);
  requireValue(checked.projectId&&checked.conversationId,'An exact project and discussion are required for a file context');
  return digest(checked);
}
function region(value) {
  requireValue(value && Object.keys(value).sort().join(',') === 'height,width,x,y' &&
    [value.x,value.y,value.width,value.height].every(n => typeof n === 'number' && Number.isFinite(n)) &&
    value.x >= 0 && value.y >= 0 && value.width > 0 && value.height > 0 &&
    value.x + value.width <= 1.000001 && value.y + value.height <= 1.000001, 'Invalid frozen frame region');
  return { x:value.x, y:value.y, width:value.width, height:value.height };
}
function regions(value = []) {
  requireValue(Array.isArray(value) && value.length <= 32, 'Too many frame regions');
  return value.map(region);
}
function marks(value = []) {
  requireValue(Array.isArray(value) && value.length <= 32, 'Too many annotation marks');
  // The overlay owns its drawing schema; only bounded normalized data crosses
  // this boundary. No path, URL, DOM selector or command can be a drawing.
  let points = 0, textCharacters = 0;
  const result = value.map(mark => {
    requireValue(mark && typeof mark === 'object' && !Array.isArray(mark) &&
      ['rect','ellipse','arrow','freehand','pen','text'].includes(mark.kind ?? mark.type), 'Invalid annotation kind');
    const kind = mark.kind ?? mark.type;
    requireValue(!mark.kind || !mark.type || mark.kind === mark.type, 'Conflicting annotation kind');
    const allowed = kind === 'text' ? ['kind','type','x','y','text'] : ['kind','type','x','y','width','height','points','start','end','color','id'];
    requireValue(Object.keys(mark).every(key => allowed.includes(key)), 'Unsupported annotation field');
    const item = { kind };
    if (kind === 'text') {
      requireValue([mark.x,mark.y].every(n => typeof n === 'number' && Number.isFinite(n) && n >= 0 && n <= 1), 'Invalid normalized text position');
      boundedText(mark.text, 2000);
      requireValue(mark.text.trim(), 'Empty annotation text');
      textCharacters += Array.from(mark.text).length;
      Object.assign(item, {x:mark.x,y:mark.y,text:mark.text});
    } else if (mark.points) {
      requireValue(Array.isArray(mark.points) && mark.points.length >= 2 && mark.points.length <= 256, 'Invalid annotation path');
      points += mark.points.length;
      item.points = mark.points.map(point => {
        const p = Array.isArray(point) ? {x:point[0],y:point[1]} : point;
        requireValue(p && Object.keys(p).sort().join(',') === 'x,y' &&
          [p.x,p.y].every(n => typeof n === 'number' && Number.isFinite(n) && n >= 0 && n <= 1), 'Invalid normalized point');
        return {x:p.x,y:p.y};
      });
    } else if (mark.start && mark.end) {
      item.points = marks([{kind:'freehand',points:[mark.start,mark.end]}])[0].points; points += 2;
    } else Object.assign(item, region({x:mark.x,y:mark.y,width:mark.width,height:mark.height}));
    return item;
  });
  requireValue(points <= 1024, 'Annotation point budget exceeded');
  requireValue(textCharacters <= 16000, 'Annotation text budget exceeded');
  return result;
}
function snapshot(value) {
  requireValue(value && typeof value === 'object' && value.observation, 'Frozen context is required');
  const observation = value.observation;
  const result = { operation_id: typeof value.operation_id === 'string' ? value.operation_id : '',
    current: value.current === true, automatic_input_authorized: false,
    observation: {status: boundedText(observation.status ?? 'UNAVAILABLE',100)} };
  for (const key of ['context_ref','window_ref','surface_ref']) if (observation[key]) result.observation[key] = ref(observation[key]);
  if (observation.window?.title) result.observation.window = {title:boundedText(observation.window.title,4000)};
  if (typeof observation.reason === 'string') result.observation.reason = boundedText(observation.reason,4000);
  if (value.preview?.image_ref) {
    const image = value.preview.image_ref;
    requireValue(HASH.test(image.sha256), 'Invalid frozen image hash');
    result.preview = {status:'AVAILABLE',image_ref:{sha256:image.sha256}};
    for (const key of ['width','height']) if (Number.isSafeInteger(image[key]) && image[key] > 0) result.preview.image_ref[key] = image[key];
  }
  // Never journal base64 pixels, accessibility text, handles or host tokens.
  return result;
}
const payloadKeys = ['id','revision','hostGeneration','scope','delivery','comment','regions','marks','contextRef','snapshot','createdAt','updatedAt','operationId','nativeReview'];
function payload(record) { return Object.fromEntries(payloadKeys.filter(key => record[key] !== undefined).map(key => [key,record[key]])); }
function recordRef(record) { return {id:record.id,revision:record.revision,sha256:record.sha256}; }
function reviewProjection(value) {
  if(!value||typeof value!=='object'||Array.isArray(value))return null;
  if(Buffer.byteLength(JSON.stringify(value))<=65536)return clone(value);
  const result={details_omitted:true,can_guide:false,pending_permissions_count:Array.isArray(value.pending_permissions)?value.pending_permissions.length:0,pending_questions_count:Array.isArray(value.pending_questions)?value.pending_questions.length:0};
  for(const key of ['version','kind','task_root_operation_id','review_ref','phase','selection','context_ref','queue_position','can_cancel','process_permission_current','coverage']){
    const limit=key==='selection'&&value.version===2&&value.kind==='CAPABILITY_TASK'?98304:8192;
    if(value[key]!==undefined&&Buffer.byteLength(JSON.stringify(value[key]))<=limit)result[key]=clone(value[key]);
  }
  return result;
}
function controlBody(kind,body) {
  requireValue(body&&typeof body==='object'&&!Array.isArray(body),'Invalid control body');
  if(Object.hasOwn(body,'expected_canonical_message_ref')){
    requireValue(kind==='guidance'&&Object.keys(body).sort().join(',')==='expected_canonical_message_ref,operation_id,raw_text','Invalid canonical continuation');
    requireValue(validId(body.operation_id)&&body.raw_text?.trim()&&Array.from(boundedText(body.raw_text)).length<=16000,'Invalid canonical continuation text');
    requireValue(ref(body.expected_canonical_message_ref).revision===1,'Canonical entry scope changed');return body;
  }
  const ordinary=Object.hasOwn(body,'expected_native_message_ref');
  requireValue(ordinary!==Object.hasOwn(body,'expected_review_ref'),'Exactly one control scope is required');
  if(ordinary){
    requireValue(kind==='guidance'&&Object.keys(body).every(key=>['operation_id','expected_native_message_ref','raw_text','intent'].includes(key)),'Invalid ordinary control');
    requireValue(typeof body.raw_text==='string'&&body.raw_text.trim()&&Array.from(body.raw_text).length<=16000,'Invalid guidance text');
    requireValue(body.intent===undefined||['AUTO','STEER','STATUS'].includes(body.intent),'Invalid guidance intent');
    const value=ref(body.expected_native_message_ref);requireValue(value.revision===1,'Ordinary control scope changed');
  }else ref(body.expected_review_ref);
  return body;
}
function resultProjection(message) {
  const result=clone(message),budget=256*1024;
  // Each full continuation reply is already stored once in reviewControls.
  // Parent records retain its identity; the host reconstructs readable text.
  if(result.canonical_original&&Array.isArray(result.canonical_controls))result.canonical_controls=result.canonical_controls.map(({text,...row})=>row);
  if(Buffer.byteLength(JSON.stringify(result))<=budget)return result;
  // The original API remains authoritative. A long event trace must not turn
  // an already observed execution result into an unknown outcome.
  for(const key of ['events','rework_previews'])if(Array.isArray(result[key])) {
    result[key+'_count']=result[key].length;delete result[key];
  }
  result.local_projection={complete:false,reason:'LONG_TRACE_OMITTED',original_operation_id:message.operation_id};
  if(Buffer.byteLength(JSON.stringify(result))<=budget)return result;
  const minimal={operation_id:message.operation_id,status:message.status,
    ...(message.canonical_original===true?{canonical_original:true,conversation_ref:message.conversation_ref,original_source:message.original_source,
      canonical_message_ref:message.canonical_message_ref,canonical_input_ref:message.canonical_input_ref,canonical_stop_ref:message.canonical_stop_ref}:{}),
    conversation_id:message.conversation_id??null,
    text:typeof message.text==='string'?message.text:'',
    local_projection:{complete:false,reason:'LONG_RECEIPT_OMITTED',original_operation_id:message.operation_id}};
  const first=submissionObservation(message.first_submission_observation);
  if(first)minimal.first_submission_observation=first;
  if(message.review)minimal.review=reviewProjection(message.review);
  for(const key of ['dispatch_state','dispatch_status','execution_started','owner_start_attempted','owner_stop_confirmed','error','native_message_ref','native_message_phase','control_capabilities','native_message_controls','native_message_controls_total','native_message_controls_truncated','controls_total','controls_truncated','role','local_status','semantic_acceptance','native_dispatch_acknowledged','original_work_ref','original_work_status_query','original_work_reference_verified','model_calls','native_post_count'])if(message[key]!==undefined&&Buffer.byteLength(JSON.stringify(message[key]))<=65536)minimal[key]=clone(message[key]);
  return minimal;
}

class QuickJournal {
  constructor({dataDir,hostGeneration=randomUUID(),clock=()=>new Date().toISOString()}) {
    requireValue(typeof dataDir === 'string' && path.isAbsolute(dataDir), 'Private absolute data directory is required');
    requireValue(validId(hostGeneration), 'Invalid host generation');
    this.file = path.join(dataDir,'quick-assistant-journal.json'); this.hostGeneration=hostGeneration;this.clock=clock;
    this.historyDir=path.join(dataDir,'quick-draft-history');
    this.state={schema:'eeas.quick-journal.v1',revision:0,records:{},commands:{}};
    if(fs.existsSync(this.file)) {
      requireValue(!fs.lstatSync(this.file).isSymbolicLink() && fs.statSync(this.file).size <= MAX_BYTES, 'Unsafe quick journal');
      const saved=JSON.parse(fs.readFileSync(this.file,'utf8'));
      requireValue(saved && saved.sha256 === digest(saved.value) && saved.value.schema === this.state.schema,
        'Quick journal integrity failed; original file retained');
      requireValue(Number.isSafeInteger(saved.value.revision) && saved.value.revision >= 0 &&
        saved.value.records && Object.keys(saved.value.records).length <= 200 && saved.value.commands &&
        Object.keys(saved.value.commands).length <= 400, 'Invalid quick journal structure');
      for(const [id,item] of Object.entries(saved.value.records)) {
        requireValue(validId(id) && item.id === id && digest(payload(item)) === item.sha256, 'Quick record integrity failed');
        ref(recordRef(item)); scope(item.scope); boundedText(item.comment); regions(item.regions); marks(item.marks);if(item.nativeReview!==undefined)nativeReview(item.nativeReview);
      }
      for(const command of Object.values(saved.value.commands))if(command.first_submission_observation!==undefined)
        requireValue(digest(submissionObservation(command.first_submission_observation))===digest(command.first_submission_observation),'Invalid submission observation');
      this.state=saved.value;
    }
    const controls=this.state.reviewControls??{};requireValue(controls&&typeof controls==='object'&&!Array.isArray(controls)&&Object.keys(controls).length<=128,'Invalid review control index');for(const [id,c]of Object.entries(controls)){requireValue(validId(id)&&c?.input?.body?.operation_id===id&&digest(c.input)===c.signature&&validId(c.input.parentOperationId),'Review control integrity failed');controlBody(c.input.kind,c.input.body);requireValue(!c.input.body.expected_native_message_ref||c.input.body.expected_native_message_ref.id===c.input.parentOperationId,'Ordinary control parent differs');}
    const drafts=this.state.continuationDrafts??{};
    requireValue(drafts&&typeof drafts==='object'&&!Array.isArray(drafts)&&Object.keys(drafts).length<=200,'Invalid continuation draft index');
    for(const [id,row]of Object.entries(drafts)){
      const {sha256,...value}=row;
      requireValue(validId(id)&&row.parentOperationId===id&&this.ownsCommand(id)&&digest(value)===sha256&&Number.isSafeInteger(row.revision)&&row.revision>=1&&Number.isSafeInteger(row.version)&&row.version>=0,'Continuation draft integrity failed');
      requireValue(Object.keys(row).sort().join(',')==='basis,parentOperationId,request,revision,sha256,text,updatedAt,version'&&typeof row.updatedAt==='string','Invalid continuation draft fields');
      boundedText(row.text);if(row.basis!==null)ref(row.basis);requireValue(!row.text||row.basis,'Missing continuation basis');
      if(row.request!==null){requireValue(Object.keys(row.request).sort().join(',')==='body,draftVersion,kind,parentOperationId'&&row.request.parentOperationId===id&&row.request.kind==='guidance'&&Number.isSafeInteger(row.request.draftVersion)&&row.request.draftVersion>=0,'Invalid saved continuation request');controlBody('guidance',row.request.body);}
    }
    const contexts=this.state.fileContexts??{};
    requireValue(contexts&&typeof contexts==='object'&&!Array.isArray(contexts)&&Object.keys(contexts).length<=64,'Invalid file context index');
    for(const [pid,item]of Object.entries(contexts)){requireValue(validId(pid)&&item&&Object.keys(item).every(key=>['fileLinkRef','filename'].includes(key)),'Invalid file context entry');scope({projectId:pid,fileLinkRef:item.fileLinkRef,...item.filename?{fileLabel:item.filename}:{}});}
    const scoped=this.state.scopedFileContexts??{};
    requireValue(scoped&&typeof scoped==='object'&&!Array.isArray(scoped)&&Object.keys(scoped).length<=64,'Invalid discussion file context index');
    for(const [key,item]of Object.entries(scoped)){
      requireValue(HASH.test(key)&&item&&Object.keys(item).every(name=>['scope','fileLinkRef','filename'].includes(name))&&associationKey(item.scope)===key,'Invalid discussion file context entry');
      scope({...item.scope,fileLinkRef:item.fileLinkRef,...item.filename?{fileLabel:item.filename}:{}});
    }
  }
  commit(next) {
    next.revision=this.state.revision+1;
    const bytes=Buffer.from(JSON.stringify({value:next,sha256:digest(next)})+'\n');
    requireValue(bytes.length <= MAX_BYTES, 'Quick draft storage is full; existing content retained');
    fs.mkdirSync(path.dirname(this.file),{recursive:true,mode:0o700});
    const temporary=this.file+'.'+randomUUID()+'.tmp';
    const fd=fs.openSync(temporary,'wx',0o600);
    try { fs.writeFileSync(fd,bytes);fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
    // A failed replacement retains the original and temporary evidence. No
    // cleanup path deletes unacknowledged drafts to make a save look successful.
    fs.renameSync(temporary,this.file);
    this.state=next;
  }
  get(reference) {
    const expected=ref(reference),record=this.state.records[expected.id];
    if(record && record.revision === expected.revision && record.sha256 === expected.sha256)return clone(record);
    return this.readHistorical(expected);
  }
  readHistorical(reference) {
    const expected=ref(reference);
    const historical=this.historyPath(expected);
    requireValue(fs.existsSync(historical)&&!fs.lstatSync(historical).isSymbolicLink()&&fs.statSync(historical).size<=MAX_BYTES,
      'Quick draft version is unavailable; original content was not replaced');
    const wrapper=JSON.parse(fs.readFileSync(historical,'utf8'));
    requireValue(wrapper.sha256===expected.sha256&&digest(wrapper.value)===expected.sha256&&
      wrapper.value.id===expected.id&&wrapper.value.revision===expected.revision,'Historical quick draft integrity failed');
    return {...clone(wrapper.value),sha256:expected.sha256};
  }
  historyPath(reference) {
    const expected=ref(reference);
    if(fs.existsSync(this.historyDir))requireValue(!fs.lstatSync(this.historyDir).isSymbolicLink(),'Quick draft history cannot be a link');
    return path.join(this.historyDir,expected.id+'.r'+expected.revision+'.'+expected.sha256+'.json');
  }
  archive(record) {
    const destination=this.historyPath(recordRef(record));
    if(fs.existsSync(destination)) {this.readHistorical(recordRef(record));return;}
    fs.mkdirSync(this.historyDir,{recursive:true,mode:0o700});
    requireValue(fs.readdirSync(this.historyDir).length<10000,'Quick history is full; old drafts retained');
    const temporary=destination+'.'+randomUUID()+'.tmp';
    const fd=fs.openSync(temporary,'wx',0o600);
    try {fs.writeFileSync(fd,JSON.stringify({value:payload(record),sha256:record.sha256})+'\n');fs.fsyncSync(fd);}
    finally {fs.closeSync(fd);}
    fs.renameSync(temporary,destination);
  }
  list({editing=false}={}) {
    return Object.values(this.state.records).filter(item=>editing||item.delivery!=='EDITING').map(clone);
  }
  latestEditing() {
    const item=Object.values(this.state.records).filter(item=>item.delivery==='EDITING').at(-1);
    return item ? clone(item) : undefined;
  }
  find(id) {
    requireValue(validId(id), 'Exact quick draft id required');
    const item=this.state.records[id];return item ? clone(item) : undefined;
  }
  legacyFileContext(projectId){requireValue(validId(projectId),'Exact file project required');const entries=this.state.fileContexts??{},item=Object.hasOwn(entries,projectId)?entries[projectId]:undefined;return item?clone(item):undefined;}
  fileContext(rawScope){const checked=associationScope(rawScope);if(!checked.projectId||!checked.conversationId)return;const entries=this.state.scopedFileContexts??{},key=associationKey(checked),item=Object.hasOwn(entries,key)?entries[key]:undefined;return item?clone(item):undefined;}
  setFileContext({file_link_ref,filename,scope:rawScope}){
    const checked=associationScope(rawScope),key=associationKey(checked),link=fileLinkRef(file_link_ref),item={scope:checked,fileLinkRef:link};
    requireValue(link.project_id===checked.projectId,'File context belongs to another discussion project');
    if(filename!=null){const name=boundedText(filename,240);requireValue(name&&!/[\u0000-\u001f\u007f]/.test(name),'Invalid file context label');item.filename=name;}
    const next=clone(this.state);next.scopedFileContexts??={};delete next.scopedFileContexts[key];Object.defineProperty(next.scopedFileContexts,key,{value:item,enumerable:true,writable:true,configurable:true});
    // This is only a last-opened lookup cache. Eviction never removes a draft,
    // command, file or the backend's durable file-link registry.
    while(Object.keys(next.scopedFileContexts).length>64)delete next.scopedFileContexts[Object.keys(next.scopedFileContexts)[0]];
    this.commit(next);return clone(item);
  }
  clearFileContext({scope:rawScope,expectedRef}){
    const key=associationKey(rawScope),existing=this.fileContext(rawScope),reference=fileLinkRef(expectedRef);
    requireValue(reference.project_id===associationScope(rawScope).projectId,'Clear reference belongs to another project');
    if(!existing)return {cleared:true};
    requireValue(digest(existing.fileLinkRef)===digest(reference),'File selection changed; the newer selection was retained');
    const next=clone(this.state);delete next.scopedFileContexts[key];this.commit(next);return {cleared:true};
  }
  save({draftId,expectedRevision,capture,comment,regions:rawRegions=[],marks:rawMarks=[],delivery,nativeReview:review}) {
    requireValue(validId(draftId) && Number.isSafeInteger(expectedRevision) && expectedRevision>=0,
      'Exact draft identity and expected revision required');
    requireValue(['EDITING','DRAFT'].includes(delivery), 'Unsupported draft delivery');
    requireValue(capture && typeof capture === 'object', 'Host capture is required');
    const frozen=snapshot(capture.snapshot),contextRef=frozen.observation.context_ref;
    const old=this.state.records[draftId];
    requireValue(!old || old.delivery!=='SEND', 'An already submitted command is immutable');
    requireValue((old?.revision??0)===expectedRevision, 'Quick draft changed; original content retained');
    requireValue(old || Object.keys(this.state.records).length<200, 'Quick inbox is full; existing drafts retained');
    const timestamp=this.clock();
    requireValue(capture.hostGeneration == null || validId(capture.hostGeneration), 'Invalid frozen host generation');
    const item={id:draftId,revision:expectedRevision+1,hostGeneration:capture.hostGeneration??this.hostGeneration,
      scope:scope(capture.scope),delivery,comment:boundedText(comment),regions:regions(rawRegions),marks:marks(rawMarks),
      ...(contextRef?{contextRef}:{}),snapshot:frozen,createdAt:old?.createdAt??timestamp,updatedAt:timestamp};
    const checked=nativeReview(review);if(checked)item.nativeReview=checked;
    if(delivery==='DRAFT') requireValue((item.comment.trim()||item.regions.length||item.marks.length) &&
      (item.contextRef || !item.regions.length&&!item.marks.length), 'Feedback needs text and a reference for its marks');
    item.sha256=digest(payload(item));
    if(old)this.archive(old);
    const next=clone(this.state);next.records[draftId]=item;this.commit(next);return clone(item);
  }
  acknowledge(reference) {
    const item=this.get(reference); const next=clone(this.state);
    const head=next.records[item.id];
    if(!head||head.sha256!==item.sha256)return {status:'HISTORICAL_INGESTED',ref:recordRef(item),execution_started:false};
    next.records[item.id].acknowledged={...recordRef(item),at:this.clock()};
    this.commit(next);return {status:'INGESTED',ref:recordRef(item),execution_started:false};
  }
  prepareSend(reference,{operationId,text,originalWorkInputSHA256}) {
    const item=this.get(reference); boundedText(text);
    requireValue(validId(operationId) && text.trim(), 'Actual message and operation identity required');
    const hasPage=item.contextRef||item.regions.length||item.marks.length,metadataOnly=!!item.scope.originalWorkChoice&&!item.scope.fileLinkRef&&!item.nativeReview&&originalWorkStatusText(text);
    requireValue(!item.scope.workInputUnavailable,'Original quick work input unavailable');
    requireValue(!item.scope.originalWorkChoice||!!item.scope.conversationRef&&!item.scope.fileLinkRef&&!item.nativeReview,'Original quick work needs its shared conversation');
    requireValue(!hasPage||metadataOnly||item.hostGeneration === this.hostGeneration, 'Saved page belongs to an earlier host; re-reference it before sending');
    requireValue(this.state.records[item.id]?.sha256===item.sha256,'Quick draft changed; reload its exact current version before sending');
    requireValue(originalWorkInputSHA256===undefined||HASH.test(originalWorkInputSHA256)&&!!item.scope.originalWorkChoice,'Invalid original quick request fingerprint');
    requireValue(!item.scope.originalWorkChoice||HASH.test(originalWorkInputSHA256),'Original quick request fingerprint required');
    const input={draftRef:recordRef(item),text,scope:item.scope,contextRef:item.contextRef??null,...(item.nativeReview?{nativeReview:nativeReview(item.nativeReview)}:{}),...(originalWorkInputSHA256?{original_work_input_sha256:originalWorkInputSHA256}:{}),...(metadataOnly?{metadata_only_status:true}:{})};
    const signature=digest(input),existing=this.state.commands[operationId];
    if(existing) {
      requireValue(existing.signature===signature,'Operation input differs; original command retained');
      return {dispatchable:false,...clone(existing)};
    }
    requireValue(item.delivery!=='SEND' && Object.keys(this.state.commands).length<400, 'Original command is already submitted or command index is full');
    const next=clone(this.state); const changed=next.records[item.id];
    changed.revision++;changed.delivery='SEND';changed.operationId=operationId;changed.updatedAt=this.clock();
    delete changed.acknowledged;changed.sha256=digest(payload(changed));
    const command={operationId,draftId:item.id,signature,input,status:'DISPATCHING',createdAt:this.clock()};
    this.archive(item);
    next.commands[operationId]=command;this.commit(next);
    return {dispatchable:true,...clone(command),record:clone(changed)};
  }
  command(operationId) {
    requireValue(validId(operationId) && this.state.commands[operationId], 'No owned quick operation');
    return clone(this.state.commands[operationId]);
  }
  ownsCommand(operationId){return validId(operationId)&&Object.hasOwn(this.state.commands,operationId);}
  continuationDraft(parentOperationId){return clone(this.state.continuationDrafts?.[parentOperationId]??null);}
  saveContinuation({parentOperationId,expectedRef=null,basis=null,text,version,request=null}){
    const command=this.command(parentOperationId),old=this.continuationDraft(parentOperationId);
    requireValue(command.operationId===parentOperationId,'Continuation parent differs');
    if(expectedRef!==null)ref(expectedRef);
    boundedText(text);requireValue(Number.isSafeInteger(version)&&version>=0,'Invalid continuation edit version');
    if(basis!==null)ref(basis);
    requireValue(!text||basis,'Exact continuation basis required');
    if(request!==null){
      requireValue(Object.keys(request).sort().join(',')==='body,draftVersion,kind,parentOperationId'&&request.parentOperationId===parentOperationId&&request.kind==='guidance'&&Number.isSafeInteger(request.draftVersion)&&request.draftVersion>=0,'Invalid continuation request');
      controlBody(request.kind,request.body);
    }
    const value={parentOperationId,revision:(old?.revision??0)+1,basis,text,version,request,updatedAt:this.clock()};
    const content=({revision,updatedAt,sha256,...item})=>item;
    // A lost save acknowledgement may be recovered only by identical content.
    // This path observes the saved value and performs no replacement.
    if(old&&digest(content(old))===digest(content(value)))return old;
    requireValue(old?expectedRef&&digest(ref(expectedRef))===digest({id:parentOperationId,revision:old.revision,sha256:old.sha256}):expectedRef===null,'Continuation draft conflicts; input retained');
    requireValue(old||Object.keys(this.state.continuationDrafts??{}).length<200,'Continuation draft storage is full');
    value.sha256=digest(value);const next=clone(this.state);next.continuationDrafts??={};next.continuationDrafts[parentOperationId]=value;this.commit(next);return clone(value);
  }
  prepareReviewControl(parentOperationId,{operationId,kind,body}){
    requireValue(validId(parentOperationId)&&validId(operationId)&&['guidance','permission','question','question-reject'].includes(kind),'Exact review control required');
    requireValue(body&&body.operation_id===operationId&&Buffer.byteLength(JSON.stringify(body))<=65536,'Invalid review control input');controlBody(kind,body);requireValue(!body.expected_native_message_ref||body.expected_native_message_ref.id===parentOperationId,'Ordinary control parent differs');
    const input={parentOperationId,kind,body},signature=digest(input),old=this.state.reviewControls?.[operationId];
    if(old){requireValue(old.signature===signature,'Review control input differs');return {dispatchable:false,...clone(old)};}
    requireValue(Object.keys(this.state.reviewControls??{}).length<128,'Review controls are full; original requests retained');
    requireValue(this.pendingReviewControls().length<16,'Unresolved review controls are full');
    const next=clone(this.state);next.reviewControls??={};next.reviewControls[operationId]={input,signature,status:'DISPATCHING'};this.commit(next);return {dispatchable:true,...clone(next.reviewControls[operationId])};
  }
  pendingReviewControls(parentOperationId){return Object.entries(this.state.reviewControls??{}).filter(([,v])=>(!parentOperationId||v.input.parentOperationId===parentOperationId)&&['DISPATCHING','UNKNOWN_OUTCOME','PROCESSING','REQUESTING'].includes(v.status)).map(([operationId,v])=>({operationId,...clone(v)}));}
  canonicalControls(parentOperationId){return Object.entries(this.state.reviewControls??{}).filter(([,row])=>row.input.parentOperationId===parentOperationId&&row.input.body.expected_canonical_message_ref).map(([operationId,row])=>({operationId,...clone(row)}));}
  reviewControl(operationId){requireValue(validId(operationId)&&this.state.reviewControls?.[operationId],'No owned review control');return clone(this.state.reviewControls[operationId]);}
  observeReviewControl(operationId,result){const old=this.reviewControl(operationId);requireValue(result?.operation_id===operationId&&typeof result.status==='string','Review control result differs');const projected=resultProjection(result);if(old.status===result.status&&digest(old.result??null)===digest(projected))return clone(result);const next=clone(this.state);next.reviewControls[operationId]={...old,status:result.status,result:projected};this.commit(next);return clone(result);}
  retainedSubmitted({draftId,operationId}){
    const command=this.command(operationId);
    requireValue(command.draftId===draftId,'Submitted question identity differs');
    // Read the actual atomic file afresh, not just this process projection.
    // No writes, authority changes, network requests or cancellation occur.
    const durable=new QuickJournal({dataDir:path.dirname(this.file),hostGeneration:this.hostGeneration,clock:this.clock});
    const saved=durable.command(operationId),record=durable.find(draftId);
    requireValue(digest(saved)===digest(command)&&digest(saved.input)===saved.signature&&
      digest(saved.input)===digest(command.input)&&record?.delivery==='SEND'&&record.operationId===operationId,
      'Submitted question is not durably available');
    return {status:'RETAINED',operationId,ref:recordRef(record)};
  }
  prepareCancel(operationId){
    const command=this.command(operationId);
    if(command.cancel)return {dispatchable:false,...command.cancel};
    requireValue(!command.message?.review?.stop_control,'Original review stop must be inspected');
    const reviewStop=command.status==='UNKNOWN_OUTCOME'&&command.input.nativeReview&&command.message?.operation_id===operationId&&command.message?.review?.can_cancel===true;
    if(reviewStop){ref(command.message.review.review_ref);requireValue(digest(command.input.nativeReview.selection)===digest(command.message.review.selection),'Review cancellation selection differs');}
    requireValue(['PROCESSING','REQUESTING','RUNNING','QUEUED','PENDING','CANCEL_REQUESTED'].includes(command.status)||reviewStop,'Original operation must be observed running before cancellation');
    const next=clone(this.state);next.commands[operationId].cancel={status:'DISPATCHING',requestedAt:this.clock()};this.commit(next);
    return {dispatchable:true,status:'DISPATCHING'};
  }
  prepareCanonicalCancel(operationId,target){
    const command=this.command(operationId),reference=ref(target);
    requireValue(command.message?.canonical_original===true&&digest(command.message.canonical_stop_ref??null)===digest(reference),'Canonical stop target changed');
    if(command.cancel?.target_ref&&digest(command.cancel.target_ref)===digest(reference))return {dispatchable:false,...command.cancel};
    const next=clone(this.state),row=next.commands[operationId];
    row.canonical_cancel_history??=[];
    requireValue(row.canonical_cancel_history.length<32,'Saved stop history is full; inspect original operations');
    if(row.cancel)row.canonical_cancel_history.push(row.cancel);
    row.cancel={status:'DISPATCHING',requestedAt:this.clock(),target_ref:reference};this.commit(next);
    return {dispatchable:true,...clone(row.cancel)};
  }
  observeCancel(operationId,status){
    this.command(operationId);requireValue(['REQUESTED','UNKNOWN'].includes(status),'Invalid cancellation observation');
    const next=clone(this.state);requireValue(next.commands[operationId].cancel,'No original cancel request');
    next.commands[operationId].cancel.status=status;this.commit(next);
  }
  observe(operationId,message) {
    const previous=this.command(operationId);
    requireValue(message && message.operation_id===operationId && typeof message.status==='string', 'Result belongs to another command');
    const first=previous.first_submission_observation??submissionObservation(message.first_submission_observation);
    const safeMessage={...message};delete safeMessage.first_submission_observation;
    if(first)safeMessage.first_submission_observation=first;
    const projected=resultProjection(safeMessage);
    if(previous.status===message.status&&digest(previous.message??null)===digest(projected))return clone(projected);
    const next=clone(this.state);const command=next.commands[operationId];
    if(first)command.first_submission_observation=first;
    command.status=message.status;command.message=projected;command.observedAt=this.clock();
    next.records[command.draftId].message=projected;this.commit(next);return clone(projected);
  }
}

module.exports={quickControlBody:controlBody,QuickJournal,quickReviewProjection:reviewProjection,quickNativeReview:nativeReview,quickResetNativeReview:resetNativeReview,quickScope:scope,quickRef:ref,quickFileLinkRef:fileLinkRef,quickAssociationScope:associationScope,quickAssociationKey:associationKey,quickRegions:regions,quickMarks:marks,quickDigest:digest};
