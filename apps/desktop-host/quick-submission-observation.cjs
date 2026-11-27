'use strict';
// A response observation is diagnostic evidence, never proof of execution or
// rejection. No arbitrary response text, headers, paths or bodies are retained.
const CODES=new Set(['REVISION_CONFLICT','INVALID_INPUT','NOT_FOUND','FORBIDDEN','UNAUTHORIZED',
  'BODY_TOO_LARGE','STORAGE_UNAVAILABLE','NOT_CONFIGURED','INTERNAL_ERROR','VALIDATION_ERROR','LIVE_BLOCKED']);
const REASONS=new Set(['HTTP_RESPONSE_UNCONFIRMED','LINKED_DOCUMENT_UNAVAILABLE','TRANSPORT_UNCONFIRMED','RECEIPT_IDENTITY_MISMATCH']);
function submissionObservation(value){
  if(!value||!['HTTP_RESPONSE','TRANSPORT_UNCONFIRMED'].includes(value.kind)||!REASONS.has(value.reason_code))return null;
  const result={kind:value.kind,reason_code:value.reason_code};
  if(value.kind==='HTTP_RESPONSE'){
    if(!Number.isSafeInteger(value.http_status)||value.http_status<400||value.http_status>599)return null;
    result.http_status=value.http_status;
    if(CODES.has(value.api_code))result.api_code=value.api_code;
    if(!['HTTP_RESPONSE_UNCONFIRMED','LINKED_DOCUMENT_UNAVAILABLE'].includes(value.reason_code))return null;
  }else if(!['TRANSPORT_UNCONFIRMED','RECEIPT_IDENTITY_MISMATCH'].includes(value.reason_code))return null;
  if(typeof value.observed_at==='string'&&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,3})?Z$/.test(value.observed_at))result.observed_at=value.observed_at;
  return result;
}
function httpSubmissionObservation(status,value){
  const code=value?.error?.code;
  // Only this exact authored reason is mapped. User text and server details
  // that happen to resemble errors are never copied into the journal.
  const linked=status===409&&code==='REVISION_CONFLICT'&&value?.error?.message==='Selected Windows file is busy or its namespace changed';
  return submissionObservation({kind:'HTTP_RESPONSE',http_status:status,api_code:code,
    reason_code:linked?'LINKED_DOCUMENT_UNAVAILABLE':'HTTP_RESPONSE_UNCONFIRMED'});
}
function firstSubmissionObservation(error,clock=()=>new Date().toISOString()){
  return {...(submissionObservation(error?.quick_submission_observation)??{
    kind:'TRANSPORT_UNCONFIRMED',reason_code:error?.quick_receipt_identity_mismatch===true?'RECEIPT_IDENTITY_MISMATCH':'TRANSPORT_UNCONFIRMED'}),observed_at:clock()};
}
module.exports={submissionObservation,httpSubmissionObservation,firstSubmissionObservation};
