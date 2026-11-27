import test from 'node:test';
import assert from 'node:assert/strict';
import {deliveryComplete, savedDeliveryReport} from '../src/planning-delivery.ts';

const project='report-project',id='a'.repeat(64),sha='b'.repeat(64);
const basis={id:'original-plan',revision:5,sha256:'c'.repeat(64)};
function partial(kind='EXPORT'){
  const discovery=kind==='STAGE';
  return {operation_id:'original-operation',status:'FAILED_OR_PARTIAL',kind,automatic_model_replay:false,
    steps:{report:{status:discovery?'DRAFT':'CONFIRMED',source_kind:discovery?'DISCOVERY':'PROPOSAL',
      [discovery?'session_ref':'proposal_ref']:structuredClone(basis),
      artifact:{id,sha256:sha,filename:'完整的用户原文件名.docx',byte_length:277597,
        media_type:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        download_url:`/api/projects/${project}/${discovery?'discovery/sessions':'planning/proposals'}/${basis.id}/reports/${id}/download`},
      pending_json:{download_url:'/internal/pending.json'},source_snapshot:{download_url:'/internal/source.json'}}},
    failures:[{reason:'ManualChangesConflict'}]};
}

test('saved report is available in the original incomplete operation without inventing a successful delivery',()=>{
  for(const kind of ['EXPORT','STAGE'])for(const status of ['FAILED_OR_PARTIAL','PROCESSING','INTERRUPTED_REVIEW_REQUIRED']){
    const value={...partial(kind),status},before=structuredClone(value),saved=savedDeliveryReport(value,project);
    assert.equal(saved.report.artifact,value.steps.report.artifact);
    assert.deepEqual(saved.basis,basis);assert.equal(saved.sourceKind,kind==='EXPORT'?'PROPOSAL':'DISCOVERY');
    assert.deepEqual(Object.keys(saved.report),['artifact']);assert.equal(deliveryComplete(value),false);
    assert.deepEqual(value,before);
  }
});

test('only an exact report download for the current project and source can become an open action',()=>{
  for(const url of ['https://example.invalid/word.docx',`/api/projects/other/planning/proposals/${basis.id}/reports/${id}/download`,
    `/api/projects/${project}/planning/proposals/other/reports/${id}/download`,
    `/api/projects/${project}/discovery/sessions/${basis.id}/reports/${id}/download`,
    `/api/projects/${project}/planning/proposals/${basis.id}/reports/${id}/pending/json/download`,
    `/api/projects/${project}/planning/proposals/${basis.id}/reports/${id}/download?redirect=1`]){
    const value=partial();value.steps.report.artifact.download_url=url;
    assert.equal(savedDeliveryReport(value,project),undefined,url);
  }
  assert.equal(savedDeliveryReport(partial(),'../other'),undefined);
  assert.equal(savedDeliveryReport(partial(),'other'),undefined);
});

test('uncommitted, malformed and inconsistent report steps remain unavailable',()=>{
  const mutations=[
    v=>delete v.steps.report,v=>v.steps.report=null,v=>v.steps.report.status='GENERATING',v=>v.steps.report.status=['CONFIRMED'],
    v=>v.kind='UNKNOWN',v=>v.automatic_model_replay=true,v=>v.steps.report.source_kind='DISCOVERY',
    v=>v.steps.report.proposal_ref.revision=0,v=>v.steps.report.proposal_ref.sha256='bad',
    v=>v.steps.report.proposal_ref.id='../original-plan',v=>v.steps.report.artifact.id='unknown',
    v=>v.steps.report.artifact.sha256='bad',v=>v.steps.report.artifact.byte_length=0,
    v=>v.steps.report.artifact.byte_length=64*1024*1024+1,v=>v.steps.report.artifact.byte_length=1.1,
    v=>v.steps.report.artifact.filename='source.json',v=>v.steps.report.artifact.filename='bad\n.docx',
    v=>v.steps.report.artifact.media_type='application/json',v=>v.steps.report.artifact=null,
    v=>v.status='STAGE_REPORT',v=>v.status='EXPORTED_CONFIRMED_PLAN_DERIVATIVES',
  ];
  for(const change of mutations){const value=partial();change(value);assert.equal(savedDeliveryReport(value,project),undefined,change.toString());}
});
