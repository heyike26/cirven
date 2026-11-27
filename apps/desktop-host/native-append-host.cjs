'use strict';
// Main owns native confirmation and activation. Renderer receives only the
// public append state, never a ticket, host marker, HWND or trusted gesture.
const {randomUUID} = require('node:crypto');
const {exactKeys} = require('./ipc-policy.cjs');
const BASE='/api/desktop-host/native-append';
const HOST=BASE+'-host';

function operation(value) {
  if(typeof value!=='string'||! /^[A-Za-z0-9_-]{1,100}$/.test(value))throw new Error('Exact append operation required');
  return value;
}
function reference(value) {
  exactKeys(value,['id','revision','sha256']);
  operation(value.id);
  if(!Number.isSafeInteger(value.revision)||value.revision<1||! /^[a-f0-9]{64}$/.test(value.sha256))throw new Error('Exact append reference required');
  return {...value};
}
function sameRef(a,b) {
  return a?.id===b?.id&&a?.revision===b?.revision&&a?.sha256===b?.sha256;
}
function publicState(value) {
  if(!value||value.adapter_id!=='classic_notepad_append')throw new Error('Actual append state unavailable');
  // Whitelist rather than accidentally return new private host fields.
  const keys=['adapter_id','state','reason','scope','permission_state','activation_state',
    'foreground_required','no_selection_overwrite','no_save_action','automatic_retry',
    'native_input_global_enabled','input_epoch','expected_intent_epoch','task_completed',
    'qualification_ref','target_label','task_operation_id','preview_ref','payload_text',
    'payload_sha256','baseline_ref','approval_ref','handoff_ref','provider_state',
    'task_status','task','error','host_broker_status'];
  return Object.fromEntries(keys.filter(key=>Object.hasOwn(value,key)).map(key=>[key,value[key]]));
}

class NativeAppendHost {
  constructor({window,dialog,request,activate,platform=process.platform,isClosing=()=>false}) {
    this.window=window;this.dialog=dialog;this.request=request;this.activate=activate;
    this.platform=platform;this.isClosing=isClosing;this.busy=false;this.takeoverEpoch=0;
  }
  async get(body={}) {
    exactKeys(body,['operationId','contextRef'].filter(key=>Object.hasOwn(body,key)));
    if(body.contextRef!==undefined)reference(body.contextRef);
    if(body.operationId!==undefined) {
      const value=await this.request(HOST+'/get?operation_id='+encodeURIComponent(operation(body.operationId)));
      return publicState(value.task_state);
    }
    return publicState(await this.request(BASE+'/get',{
      operation_id:null,context_ref:body.contextRef===undefined?null:reference(body.contextRef)}));
  }
  prepare(body) {
    exactKeys(body,['operation_id','context_ref','raw_text','payload_source',
      ...['provider_profile_ref'].filter(key=>Object.hasOwn(body??{},key))]);
    operation(body.operation_id);reference(body.context_ref);
    if(body.provider_profile_ref)reference(body.provider_profile_ref);
    if(typeof body.raw_text!=='string'||body.raw_text.length>32000)throw new Error('Bounded raw instruction required');
    // The server validates codepoint spans/literal bytes and current baseline.
    return this.request(BASE+'/prepare',body).then(publicState);
  }
  approve(body) {
    exactKeys(body,['operationId','previewRef']);
    return this.confirmAndStart(operation(body.operationId),reference(body.previewRef));
  }
  start(body) {
    exactKeys(body,['operationId','previewRef','approvalRef']);
    reference(body.approvalRef);
    return this.confirmAndStart(operation(body.operationId),reference(body.previewRef),body.approvalRef);
  }
  async revoke(body) {
    exactKeys(body,['operationId','expectedIntentEpoch']);operation(body.operationId);
    if(!Number.isSafeInteger(body.expectedIntentEpoch)||body.expectedIntentEpoch<1)throw new Error('Current intent epoch required');
    // Stop only the exact private Main children before awaiting the server.
    // A late dialog/HTTP response also sees the local generation fence.
    this.takeoverEpoch++;
    const handoffStop=this.activate.cancelPending?.();
    const value=await this.request(HOST+'/revoke',{
      operation_id:body.operationId,expected_intent_epoch:body.expectedIntentEpoch});
    if(!value||typeof value.status!=='string')throw new Error('Actual revocation receipt unavailable');
    const keys=['status','operation_id','owner','intent_epoch','mutation_authority','current',
      'cancellation','readonly_intent_cancellation','readonly_intent_cancellation_status'];
    return {...Object.fromEntries(keys.filter(key=>Object.hasOwn(value,key)).map(key=>[key,value[key]])),
      ...handoffStop?{foreground_handoff_stop:handoffStop}:{}};
  }
  alive() {
    if(this.isClosing()||!this.window||this.window.isDestroyed())throw new Error('Owning app is closing');
  }
  async confirmAndStart(id,previewRef,approvalRef) {
    this.alive();
    if(this.busy)throw new Error('A native append confirmation is already in progress');
    if(this.platform!=='win32')return {...await this.get({operationId:id}),state:'NOT_ADAPTED',reason:'ACTUAL_WINDOWS_HANDOFF_REQUIRED'};
    this.busy=true;
    const epoch=this.takeoverEpoch;
    const current=()=>{this.alive();if(this.takeoverEpoch!==epoch)throw new Error('已请求接管，原操作未继续交接；请核查原回执。');};
    try {
      const review=await this.request(HOST+'/actual-review?operation_id='+encodeURIComponent(id));
      if(!sameRef(review.preview_ref,previewRef))throw new Error('Actual reviewed preview changed');
      if(review.status!=='REVIEW_READY'||!review.can_show_dialog)return publicState(review.task_state);
      if(approvalRef&&!sameRef(review.task_state?.approval_ref,approvalRef))throw new Error('Actual reviewed approval changed');
      if(review.operation_id!==id||typeof review.payload_text!=='string'||review.payload_text.length>512||
          typeof review.target_label!=='string'||review.target_label.length>2048)throw new Error('Actual native review is incomplete');
      reference(review.review_ref);current();
      const dialogRequestId='native-append-dialog-'+randomUUID();
      const result=await this.dialog.showMessageBox(this.window,{
        type:'question',title:'确认在原记事本末尾追加',
        message:'确认并追加以下原文？',
        detail:'目标：'+review.target_label+'\n\n'+review.payload_text+
          '\n\n只在当前经典记事本末尾输入一次；不覆盖选区，不保存文件。窗口、正文或光标改变时停止。',
        buttons:['确认并追加','取消'],defaultId:1,cancelId:1,noLink:true,
      });
      current();
      // Only the actual native Dialog callback enters this private route.
      const decision=await this.request(HOST+'/dialog-result',{
        operation_id:id,review_ref:review.review_ref,dialog_request_id:dialogRequestId,
        result:result?.response===0?'APPROVE':'DECLINE'});
      if(decision.status!=='APPROVED_HANDOFF_REQUIRED')return publicState(decision.task_state);
      if(!sameRef(decision.preview_ref,previewRef)||decision.operation_id!==id)throw new Error('Approved append identity changed');
      reference(decision.ticket_ref);current();
      let outcome='UNKNOWN';
      try {
        const actual=await this.activate({
          operation_id:id,ticket_ref:decision.ticket_ref,
          activation_target:decision.activation_target,activation_host:decision.activation_host,
          baseline:review.baseline,expires_unix_ms:Math.min(decision.expires_unix_ms,Date.now()+55000),
        });
        if(actual?.status==='ACTIVATION_REQUEST_RETURNED')outcome='RETURNED';
        else if(actual?.status==='OS_REFUSED')outcome='OS_REFUSED';
      } catch { /* Unknown helper completion is never retried or called success. */ }
      current();
      const committed=await this.request(HOST+'/commit-handoff',{
        operation_id:id,ticket_ref:decision.ticket_ref,helper_outcome:outcome});
      if(committed.status!=='HANDOFF_COMMITTED')return publicState(committed.task_state);
      current();
      const started=await this.request(HOST+'/start',{operation_id:id,ticket_ref:decision.ticket_ref});
      return publicState(started.task_state||started);
    } finally { this.busy=false; }
  }
}

module.exports={NativeAppendHost,publicState,sameRef};
