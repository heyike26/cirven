'use strict';
const {quickDigest}=require('./quick-journal.cjs');
const workInput=require('../../plugins/vscode-companion/work-input-wire.cjs');
const SHA=/^[a-f0-9]{64}$/;
// Public conversations continue their existing original actor. Explicit
// capability/file tasks retain their separate, scoped task protocol.
const shared=item=>!!item.scope?.conversationRef&&!item.nativeReview&&!item.scope.fileLinkRef;
const metadataStatus=(item,text)=>shared(item)&&!!item.scope.originalWorkChoice&&workInput.originalWorkStatusText(text);
function root(scope){return '/api/conversations/'+encodeURIComponent(scope.conversationRef.id)+'/messages';}
function input(item,text){
  if(item.scope.workInputUnavailable)throw Error('所选原工作的范围需要核对，原稿保留，请返回对话。');
  if(!item.scope.providerProfileRef)throw Error('请先选择模型，原问题和截图已保留。');
  const local=metadataStatus(item,text),choice=item.scope.originalWorkChoice?workInput.validateOriginalWorkChoice(item.scope.originalWorkChoice):null;
  const attachments=[];
  if(item.contextRef&&!local){
    const image=item.snapshot?.preview?.image_ref?.sha256;
    if(item.regions.length&&!image)throw Error('圈选缺少原截图，原稿保留，请重新引用。');
    const notes=(item.marks??[]).map(mark=>mark.kind==='text'?`位置 ${mark.x}, ${mark.y}：${mark.text}`:
      mark.points?`图形 ${mark.kind}，归一化路径：`+mark.points.map(point=>`${point.x},${point.y}`).join(' → '):
      `图形 ${mark.kind}，位置 ${mark.x},${mark.y}，宽高 ${mark.width},${mark.height}`).join('\n');
    if(Array.from(notes).length>16000)throw Error('这条图上批注超过单条输入范围，请分条发送；原稿已保留。');
    attachments.push({kind:image?'SCREENSHOT':'CURRENT_PAGE',context_ref:item.contextRef,
      label:Array.from(item.snapshot?.observation?.window?.title||'当前页面').slice(0,500).join(''),regions:image?item.regions:[],
      ...(image?{image_sha256:image}:{}),...(notes?{user_note:notes}:{})});
  }
  return {conversation_ref:item.scope.conversationRef,provider_profile_ref:item.scope.providerProfileRef,
    raw_text:text,agent:item.scope.agent??'build',input_attachments:attachments,
    ...(item.scope.contextSelectionId?{context_selection_id:item.scope.contextSelectionId}:{}),
    ...(item.scope.originalSource?{original_source:item.scope.originalSource}:{}),
    ...(choice?{original_work_ref:choice.reference}:{})};
}
function inputFingerprint(request){
  if(!request.original_work_ref||request.file_input!=null)return null;
  const value=JSON.parse(JSON.stringify(request));for(const key of ['file_input','context_selection_id','original_work_ref'])if(value[key]==null)delete value[key];
  value.agent??='build';value.original_source??=null;value.input_attachments=(value.input_attachments??[]).map(item=>({...item,regions:item.regions??[],image_sha256:item.image_sha256??null,user_note:item.user_note??null}));
  function tagged(item){if(item===null)return ['null'];if(typeof item==='boolean')return ['boolean',item];if(typeof item==='number'){if(!Number.isFinite(item)||Number.isInteger(item)&&!Number.isSafeInteger(item))throw Error('Unsafe canonical JSON number');const bytes=Buffer.alloc(8);bytes.writeDoubleBE(item===0?0:item);return ['number',bytes.toString('hex')];}if(typeof item==='string')return ['string',item];if(Array.isArray(item))return ['array',item.map(tagged)];if(item&&typeof item==='object')return ['object',Object.keys(item).sort().map(key=>[key,tagged(item[key])])];throw Error('Unsupported canonical input');}
  return quickDigest(['cirven.original-work-input-fingerprint.v1',tagged(value)]);
}
function projection(value,command,{continuation=false,text}={}){
  const scope=command.input.scope,key=command.operationId;
  if(!value||value.operation_id!==key||value.conversation_ref?.id!==scope.conversationRef.id||
      (!continuation&&value.conversation_ref?.revision!==scope.conversationRef.revision)||value.automatic_post_replay!==false||
      value.model_calls_by_query!==0||value.owner_started_by_query!==false)throw Error('Canonical reply identity differs');
  if(!continuation&&scope.contextSelectionId&&value.context_selection_id!==scope.contextSelectionId)throw Error('Canonical reviewed context selection differs');
  const source=value.original_source,expected=scope.originalSource;
  if(expected&&source&&(source.project_id!==expected.project_id||quickDigest(source.session_ref)!==quickDigest(expected.session_ref)))throw Error('Original reply owner differs');
  let work={};
  if(scope.originalWorkChoice){
    const choice=workInput.validateOriginalWorkChoice(scope.originalWorkChoice),raw=continuation?text:command.input.text,original=value.operation;
    if(typeof raw!=='string'||!expected||choice.owner.project_id!==expected.project_id||quickDigest(choice.owner.session_ref)!==quickDigest(expected.session_ref)||value.version!==1||!SHA.test(value.request_sha256??''))throw Error('Original quick work request differs');
    if(!continuation&&(!SHA.test(command.input.original_work_input_sha256??'')||value.original_work_input_sha256!==command.input.original_work_input_sha256))throw Error('Original quick work request fingerprint differs');
    const local=workInput.originalWorkStatusText(raw);
    work={original_work_ref:choice.reference,original_work_status_query:local,original_work_reference_verified:false};
    if(original){
      if(original.id!==key||original.automatic_replay!==false||!SHA.test(original.request_sha256??'')||!source||!Number.isSafeInteger(source.generation)||source.generation<1||(!continuation&&source.generation!==expected.generation)||(continuation&&source.generation<expected.generation))throw Error('Original quick work operation differs');
      workInput.verifyOriginalWorkInputReceipt(original,source,choice.reference,raw,local);
      work.original_work_reference_verified=true;
      if(original.role==='LOCAL_STATUS'){const status=structuredClone(original.local_status);if(Buffer.byteLength(JSON.stringify(status))>8192)throw Error('Original quick work status exceeds bound');Object.assign(work,{role:'LOCAL_STATUS',local_status:status,model_calls:0,native_post_count:0});}
    }
  }
  return {...work,operation_id:key,status:value.status,conversation_id:scope.conversationId??null,project_id:scope.projectId??null,
    conversation_ref:value.conversation_ref,canonical_original:true,original_source:source,
    ...(value.context_selection_id?{context_selection_id:value.context_selection_id}:{}),
    ...(value.context_summary?{context_summary:value.context_summary}:{}),
    ...(value.canonical_message_ref?{canonical_message_ref:value.canonical_message_ref}:{}),
    text:value.response?.text??'',request_sha256:value.request_sha256,
    control_capabilities:{can_cancel:value.can_cancel===true,can_guide:value.can_continue===true,can_status:value.can_continue===true},
    ...(value.cancel?{cancel:value.cancel}:{}),automatic_retry:false};
}
module.exports={canonicalQuick:shared,canonicalQuickMetadataStatus:metadataStatus,canonicalQuickRoot:root,canonicalQuickInput:input,canonicalQuickProjection:projection,canonicalQuickInputFingerprint:inputFingerprint};
