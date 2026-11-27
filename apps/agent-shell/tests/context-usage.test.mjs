// Authored for concentrated debug. Pure metadata parsing, not browser/native proof.
import test from 'node:test';
import assert from 'node:assert/strict';
import {contextUsageSummary} from '../src/context-usage.ts';

const chosen={selection_id:'a'.repeat(64),selection_receipt_id:'a'.repeat(64),mode:'AUTO',status:'SELECTED',
 project_id:'source-project',conversation_id:'native:'+'b'.repeat(64),scope_id:'topic',selected_count:2,
 omitted_count:140,characters:7000,model_calls:0};
test('a historical selection can be inspected without being interpreted as model dispatch or current source verification',()=>{
 assert.deepEqual(contextUsageSummary(chosen),chosen);
 assert.equal(contextUsageSummary({...chosen,mode:'MANUAL'}).mode,'MANUAL');
 assert.equal(contextUsageSummary({...chosen,status:'OFF',selection_id:null,selection_receipt_id:null,selected_count:0,omitted_count:0,characters:0}).status,'OFF');
});
test('corrupt counts, foreign paths and fabricated model calls cannot be presented as a context receipt',()=>{
 for(const patch of [{selection_id:'../source'},{project_id:'/actual/path'},{characters:16001},{selected_count:33},
  {omitted_count:-1},{conversation_id:'guessed window title'},{model_calls:1},{mode:'EXECUTE'},{selection_receipt_id:undefined}])
  assert.equal(contextUsageSummary({...chosen,...patch}),undefined);
 assert.equal(contextUsageSummary({unavailable:true}),undefined);assert.equal(contextUsageSummary(null),undefined);
});
