import assert from 'node:assert/strict';
import test from 'node:test';
import {artifactReadingOperation} from '../src/workflow-reading.ts';

test('reading follows the adopted artifact through failed, questioned and interrupted attempts',()=>{
 const artifact={summary:'Reviewed checklist',items:[]};
 const log=[
  {event:'finished',status:'review',operation_id:'legacy-artifact'},
  {event:'started',operation_id:'new-attempt'},
  {event:'finished',status:'review',operation_id:'rejected-change',error:'Output was kept'},
  {event:'finished',status:'needs_input',operation_id:'clarification'},
  {event:'finished',status:'failed',operation_id:'failure',error:'Unavailable'},
  {event:'recover',operation_id:'recover',original_operation_id:'new-attempt'},
 ];
 assert.equal(artifactReadingOperation({artifact,log}),'legacy-artifact');
 log.push({event:'finished',status:'needs_input',operation_id:'adopted-partial',artifact_written:true});
 assert.equal(artifactReadingOperation({artifact,log}),'adopted-partial');
 assert.equal(artifactReadingOperation({artifact:null,log}),undefined);
});

test('manual observations keep the version that first established the test report',()=>{
 const log=[
  {event:'manual_results_started',operation_id:'manual-report'},
  {event:'manual_check_recorded',operation_id:'new-observation'},
 ];
 assert.equal(artifactReadingOperation({artifact:{summary:'Report',items:[]},log}),'manual-report');
});
