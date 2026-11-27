// AUTHORED_NOT_RUN: actual typed handoff parser, UNIT saved metadata only.
import test from 'node:test';
import assert from 'node:assert/strict';
import {validQuickRecord,sameQuickScope} from '../src/quick-inbox.ts';
const record=(patch={})=>({id:'original',revision:1,sha256:'a'.repeat(64),scope:{conversationId:'chat',workInputUnavailable:true},delivery:'EDITING',comment:'保留未提交的工作输入',regions:[],...patch});
test('meaningful editing input with a blocked work scope remains readable in the old inbox contract',()=>{assert.equal(validQuickRecord(record()),true);});
test('unrelated legacy editing slots do not become invented confirmed draft entries',()=>{assert.equal(validQuickRecord(record({scope:{conversationId:'chat'}})),false);assert.equal(validQuickRecord(record({delivery:'DRAFT',scope:{conversationId:'chat'}})),true);});
test('a previous selected-work draft is still visible in the same discussion after choosing another work',()=>{const s={conversationId:'chat',originalWorkChoice:{reference:{root_operation_id:'root_one'}}},target={conversationId:'chat',originalWorkChoice:{reference:{root_operation_id:'root_two'}}};assert.equal(sameQuickScope(s,target),true);assert.equal(validQuickRecord(record({scope:s})),true);});
test('Unicode input length matches the native codepoint limit without discarding valid emoji text',()=>{assert.equal(validQuickRecord(record({comment:'😀'.repeat(16000)})),true);assert.equal(validQuickRecord(record({comment:'😀'.repeat(16001)})),false);});
