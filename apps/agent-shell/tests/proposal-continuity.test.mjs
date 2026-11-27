import test from 'node:test';
import assert from 'node:assert/strict';
import {adoptedProposalQuestions,retainedProposalQuestions,proposalConfirmationSelection} from '../src/proposal-confirmation.ts';
import {proposalFixture,editedFixture,observedAnswer,confirmedFixture,copy} from './proposal-continuity-fixture.mjs';
const questions=a=>a.data.payload.candidate.questions;
const changes=(a,drafts)=>proposalConfirmationSelection(a,questions(a),drafts);

test('an edited draft carries only server-retained decisions and never pretends to be confirmed',()=>{
 const artifact=editedFixture(),before=copy(artifact),retained=retainedProposalQuestions(artifact,questions(artifact));
 assert.equal(retained.size,9);assert.equal(retained.has('Q02'),false);assert.equal(retained.has('Q03'),false);assert.equal(retained.get('Q01').confirmedRevision,3);assert.equal(adoptedProposalQuestions(artifact,questions(artifact)).size,0);assert.deepEqual(artifact,before);
});
test('foreign or stale provenance, duplicate decisions, invalidation and edited displayed questions fail closed',()=>{
 const mutations=[a=>a.scope.projectId='foreign',a=>a.contentHash='f'.repeat(64),a=>a.data.payload.decision_continuity.basis_ref.revision=2,a=>a.data.payload.decision_continuity.basis_ref.id='other',a=>a.data.payload.decision_continuity.retained_decisions[0].provenance.proposal_ref.project_id='foreign',a=>a.data.payload.decision_continuity.retained_decisions[0].provenance.proposal_ref.revision=4,a=>a.data.payload.decision_continuity.retained_decisions[0].provenance.question_sha256='invalid',a=>a.data.payload.decision_continuity.retained_decisions[0].provenance.resolution_sha256='invalid',a=>a.data.payload.decision_continuity.retained_decisions.push(copy(a.data.payload.decision_continuity.retained_decisions[0])),a=>a.data.payload.decision_continuity.invalidated_question_ids.push('Q01'),a=>a.data.answers[0].question_sha256='f'.repeat(64)];
 for(const mutate of mutations){const artifact=editedFixture();mutate(artifact);assert.equal(retainedProposalQuestions(artifact,questions(artifact)).has('Q01'),false);}
 const artifact=editedFixture(),display=copy(questions(artifact));display[0].text+=' unsaved';assert.equal(retainedProposalQuestions(artifact,display).has('Q01'),false);
});
test('saving a new answer is an observation, not adoption, until a fresh explicit choice is submitted',()=>{
 const artifact=observedAnswer(proposalFixture()),before=copy(artifact),answer=artifact.data.answers.at(-1);
 assert.equal(adoptedProposalQuestions(artifact,questions(artifact)).size,11);assert.deepEqual(changes(artifact,{}),{decisions:[],incomplete:[]});
 const selected=changes(artifact,{Q12:{answerId:answer.answer_id,reason:'明确接受新的答复'}});
 assert.deepEqual(selected,{decisions:[{question_id:'Q12',mode:'ANSWER_ACCEPTED',answer_id:answer.answer_id,reason:'明确接受新的答复'}],incomplete:[]});assert.deepEqual(artifact,before);
 const successor=confirmedFixture(artifact,selected.decisions);assert.equal(adoptedProposalQuestions(successor,questions(successor)).size,12);assert.equal(successor.data.payload.confirmation.execution_authorized,false);assert.equal(successor.data.payload.confirmation.accepted_sha256,artifact.contentHash);
});
test('restored identical decisions are not replayed and do not hide a separate new choice',()=>{
 const artifact=observedAnswer(proposalFixture()),old=artifact.data.payload.confirmation.resolutions[0],answer=artifact.data.answers.at(-1),drafts={Q01:{answerId:old.answer_id,reason:old.reason},Q12:{answerId:answer.answer_id,reason:'接受本次新答复'}};
 assert.deepEqual(changes(artifact,drafts).decisions.map(row=>row.question_id),['Q12']);assert.deepEqual(changes(artifact,{Q01:drafts.Q01}),{decisions:[],incomplete:[]});
 const edited=editedFixture(),carry=edited.data.payload.decision_continuity.retained_decisions[0].resolution;assert.deepEqual(changes(edited,{Q01:{answerId:carry.answer_id,reason:carry.reason}}),{decisions:[],incomplete:[]});
});
test('reason correction and a distinct verified answer record remain explicit auditable changes',()=>{
 const artifact=proposalFixture(),old=artifact.data.payload.confirmation.resolutions[0];
 assert.equal(changes(artifact,{Q01:{answerId:old.answer_id,reason:'修正后的依据'}}).decisions.length,1);
 const next=observedAnswer(artifact,'Q01',old.answer_text);assert.equal(changes(next,{Q01:{answerId:next.data.answers.at(-1).answer_id,reason:old.reason}}).decisions.length,1);
});
test('incomplete, future, foreign and duplicate answer selections never become a confirmation packet',()=>{
 const artifact=observedAnswer(proposalFixture()),answer=artifact.data.answers.at(-1);
 for(const value of [{answerId:answer.answer_id,reason:' '},{answerId:'missing',reason:'reason'}])assert.deepEqual(changes(artifact,{Q12:value}),{decisions:[],incomplete:['Q12']});
 for(const mutate of [a=>a.data.answers.at(-1).proposal_revision=99,a=>a.data.answers.at(-1).proposal_id='foreign',a=>a.data.answers.push(copy(a.data.answers.at(-1)))]){const next=copy(artifact);mutate(next);assert.equal(changes(next,{Q12:{answerId:answer.answer_id,reason:'reason'}}).decisions.length,0);}
 assert.deepEqual(changes(artifact,{removed_question:{answerId:'stale',reason:'old'}}),{decisions:[],incomplete:[]});
});
test('fresh assumptions remain separate from saved answers and require explicit reasons',()=>{
 const artifact=proposalFixture(),selected=changes(artifact,{Q12:{assumption:'限定条件下的人工假设',reason:'负责人暂按此条件确认'}});assert.equal(selected.decisions[0].mode,'ASSUMPTION_ACCEPTED');assert.equal(selected.decisions[0].answer_id,undefined);assert.equal(adoptedProposalQuestions(artifact,questions(artifact)).has('Q12'),false);
});

test('server question scope epochs reject old explicit answers without changing retained authority',()=>{
 const artifact=editedFixture();artifact.data.payload.decision_continuity.question_scope_revisions={Q01:1,Q02:4,Q03:4};const old=artifact.data.answers.find(row=>row.question_id==='Q02');assert.deepEqual(changes(artifact,{Q02:{answerId:old.answer_id,reason:'新理由不能重签旧范围'}}),{decisions:[],incomplete:['Q02']});
 const next=observedAnswer(artifact,'Q02','按新范围重新给出的答复');assert.equal(changes(next,{Q02:{answerId:next.data.answers.at(-1).answer_id,reason:'明确接受当前范围'}}).decisions.length,1);assert.equal(retainedProposalQuestions(next,questions(next)).size,9);
});

test('only the exact server-approved legacy answer may cross its conservative epoch for a reason correction',()=>{
 const artifact=editedFixture();artifact.data.payload.decision_continuity.question_scope_revisions={Q01:4};const original=artifact.data.answers[0];assert.equal(retainedProposalQuestions(artifact,questions(artifact)).has('Q01'),true);assert.equal(changes(artifact,{Q01:{answerId:original.answer_id,reason:'修正原批准理由'}}).decisions.length,1);
 artifact.data.answers.push({...copy(original),answer_id:'same_text_but_unapproved'});assert.deepEqual(changes(artifact,{Q01:{answerId:'same_text_but_unapproved',reason:'相同文本不能借用旧批准'}}),{decisions:[],incomplete:['Q01']});
});
