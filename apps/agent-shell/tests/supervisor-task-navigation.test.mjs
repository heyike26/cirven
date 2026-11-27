import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
const source=await readFile(new URL('../src/supervisor-task-navigation.ts',import.meta.url),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
const {selectedSupervisorOperation}=await import('data:text/javascript;base64,'+Buffer.from(compiled).toString('base64'));
const sha='a'.repeat(64),key={kind:'native_message',project_id:null,object_id:'original',operation_id:'original',generation:4};
const detail=()=>({key:{...key},task:{key:{...key}},input_text:'24条以外的原问题',answer_text:'该问题原答复',continuation:{kind:'supervisor',operation_id:'original',request_sha256:sha}});
const snapshot=()=>({conversation_id:'same_global',operations:[{operation_id:'original',request_sha256:sha,status:'RETURNED'}],messages:[{role:'assistant',operation_id:'recent',text:'无关的最近答复'}]});
test('old exact coordinator operation selects its own question and answer without depending on recent history or changing inputs',()=>{
  const d=detail(),s=snapshot(),before=JSON.stringify([d,s]);
  assert.deepEqual(selectedSupervisorOperation(d,s),{operation_id:'original',request_sha256:sha,question:d.input_text,answer_text:d.answer_text,status:'RETURNED'});
  assert.equal(JSON.stringify([d,s]),before);
});
test('a missing, foreign or changed target remains unavailable rather than adopting a recent conversation',()=>{
  for(const change of [d=>delete d.continuation,d=>d.continuation.request_sha256='b'.repeat(64),d=>d.key.project_id='foreign',d=>d.key.kind='planning',d=>d.task.key.generation++,d=>d.continuation.operation_id='recent',d=>d.input_text='',d=>d.answer_text='x'.repeat(48001)]){
    const d=detail();change(d);assert.throws(()=>selectedSupervisorOperation(d,snapshot()),/SUPERVISOR_TASK_REFERENCE_CHANGED/);
  }
  assert.throws(()=>selectedSupervisorOperation(detail(),undefined));
  assert.throws(()=>selectedSupervisorOperation(detail(),{...snapshot(),operations:[]}));
});
test('selection uses the verified operation current status and supplies no permissions or draft text mutation',()=>{
  const s=snapshot();s.operations[0].status='UNKNOWN';
  const value=selectedSupervisorOperation(detail(),s);
  assert.equal(value.status,'UNKNOWN');assert.deepEqual(Object.keys(value).sort(),['answer_text','operation_id','question','request_sha256','status']);
});
test('archived task selection accepts an exact freshly read operation outside the bounded live snapshot and rejects forged bases',()=>{
 const d=detail(),s={...snapshot(),operations:[]},op={operation_id:'original',request_sha256:sha,status:'RETURNED',created_at:'2026-10-26T00:00:00Z'};
 assert.equal(selectedSupervisorOperation(d,s,op).question,d.input_text);assert.throws(()=>selectedSupervisorOperation(d,s,{...op,request_sha256:'b'.repeat(64)}));
 const current=snapshot();current.operations[0].request_sha256='c'.repeat(64);assert.throws(()=>selectedSupervisorOperation(d,current,op));
});
