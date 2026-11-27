import test from 'node:test';
import assert from 'node:assert/strict';
import {deliveryReview} from '../src/delivery-review.ts';

const file=(path,hash='a'.repeat(64))=>({path,sha256:hash});
const base=()=>({status:'STAGE_REPORT',operation_id:'read-old',session_ref:{id:'s',revision:1,sha256:'b'.repeat(64)},project_files:[file('delivery/report.docx'),file('delivery/task-T01.md')],artifacts:[{kind:'source_snapshot',path:'D:/private/source.json',filename:'source.json',sha256:'c'.repeat(64)}],report:{artifact:{filename:'报告.docx',sha256:'a'.repeat(64)}},file_checks:[{path:'delivery/report.docx',status:'CURRENT'},{path:'delivery/task-T01.md',status:'CURRENT'},{path:'D:/private/source.json',status:'CURRENT'}],basis_current:true,answer_view_current:true,template_source_current:true,current_review_status:'CURRENT',execution_authorized:false});
test('only the changed actual task is called changed; unchanged report and retained records stay clear',()=>{
  const receipt=base();receipt.file_checks[1].status='STALE';receipt.current_review_status='CHANGED_OUTPUTS';const old=JSON.stringify(receipt),review=deliveryReview(receipt);
  assert.deepEqual(review.issues,[{name:'task-T01.md',status:'STALE',retained:false}]);
  assert.deepEqual(review.notices,[{message:'{count} 份交付文件已变化或缺失，原交付依据保留。',params:{count:1}}]);
  assert.equal(JSON.stringify(receipt),old);
});
test('unknown retained records are distinct from changed output and do not expose internal JSON or absolute paths',()=>{
  const receipt=base();receipt.file_checks[2].status='UNKNOWN';receipt.current_review_status='CHANGED_OUTPUTS';const review=deliveryReview(receipt);
  assert.deepEqual(review.issues,[{name:'生成来源快照',status:'UNKNOWN',retained:true}]);
  assert.deepEqual(review.notices,[{message:'可打开文件版本一致；{count} 份留存记录需要核对。',params:{count:1}}]);
  assert.doesNotMatch(JSON.stringify(review),/D:|source\.json/);
});
test('missing checks, conflicting duplicates and future statuses never become current; independent basis warnings remain',()=>{
  const receipt=base();receipt.file_checks=[{path:'delivery/report.docx',status:'CURRENT'},{path:'delivery/report.docx',status:'STALE'},{path:'delivery/task-T01.md',status:'NEW_STATUS'}];receipt.basis_current=false;receipt.template_source_current=false;receipt.answer_view_current=false;receipt.current_review_status='CHANGED_OUTPUTS';
  const review=deliveryReview(receipt);assert.equal(review.issues.length,3);assert.ok(review.issues.every(issue=>issue.status==='UNKNOWN'));
  assert.equal(review.notices.length,5);assert.ok(review.notices.some(notice=>notice.message.startsWith('要求版本')));assert.ok(!review.notices.some(notice=>notice.message.startsWith('可打开')));
  const unobserved=base();unobserved.file_checks=[];assert.equal(deliveryReview(unobserved).issues.length,3);
});
test('one legacy path in both delivery groups is counted once and a current receipt adds no UI warnings',()=>{
  const receipt=base();assert.deepEqual(deliveryReview(receipt),{notices:[],issues:[]});
  receipt.harness={files:[receipt.project_files[1]]};receipt.file_checks.push({...receipt.file_checks[1]});receipt.file_checks.filter(row=>row.path==='delivery/task-T01.md').forEach(row=>row.status='MISSING');receipt.current_review_status='CHANGED_OUTPUTS';
  assert.equal(deliveryReview(receipt).issues.length,1);assert.equal(deliveryReview(receipt).issues[0].status,'MISSING');
});
