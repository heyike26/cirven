import test from 'node:test';
import assert from 'node:assert/strict';
import {planningCheck} from '../src/planning-checks.ts';

test('network results express structure only and reject incomplete or semantic-success-shaped receipts',()=>{
  assert.deepEqual(planningCheck('network',{valid:true,node_count:6,question_count:2,semantic_correctness:'UNASSESSED',flow:{}}),{kind:'network',nodes:6,questions:2});
  for(const value of [{valid:true,node_count:6,question_count:2},{valid:true,node_count:-1,question_count:0,semantic_correctness:'UNASSESSED'},{valid:true,node_count:6,question_count:2,semantic_correctness:'VERIFIED'},null])assert.equal(planningCheck('network',value),undefined);
});
test('acceptance results retain exact task identities and concrete missing fields without claiming execution',()=>{
  const value={nodes:[{node_id:'任务甲',missing:[]},{node_id:'T02',missing:['outputs','acceptance']},{node_id:'T03',missing:['source_basis']}],complete_nodes:1,total_tasks:3,business_criteria_quality:'UNASSESSED',verified_execution:false};
  const before=JSON.stringify(value);
  assert.deepEqual(planningCheck('acceptance',value),{kind:'acceptance',complete:1,total:3,issues:[{id:'T02',missing:['outputs','acceptance']},{id:'T03',missing:['source_basis']}]});
  assert.equal(JSON.stringify(value),before);
  for(const change of [{complete_nodes:3},{total_tasks:2},{verified_execution:true},{business_criteria_quality:'PASSED'},{nodes:[{node_id:'T02',missing:[]},{node_id:'T02',missing:[]}]},{nodes:[{node_id:'T02',missing:['invented']}]},{nodes:[{node_id:'T02',missing:['outputs','outputs']}]}])assert.equal(planningCheck('acceptance',{...value,...change}),undefined);
});
