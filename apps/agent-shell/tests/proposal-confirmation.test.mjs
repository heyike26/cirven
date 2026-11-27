import test from 'node:test';
import assert from 'node:assert/strict';
import {adoptedProposalQuestions} from '../src/proposal-confirmation.ts';

const copy=value=>structuredClone(value);
function fixture(){
 const questions=Array.from({length:7},(_,index)=>({id:`Q${index+1}`,text:`Original question ${index+1}`,reason:'Review the original requirement',category:'DECISION',blocking:index<5,affects_node_ids:['T1'],requested_materials:[],source_anchors:[]}));
 const answers=questions.slice(0,5).map((question,index)=>({answer_id:`answer_${index}`,proposal_id:'proposal',proposal_revision:2,question_id:question.id,question_sha256:'b'.repeat(64),text:`Saved answer ${index}`,accepted:false}));
 const confirmation={accepted_revision:2,accepted_sha256:'a'.repeat(64),user_confirmed:true,execution_authorized:false,resolutions:answers.map(answer=>({question_id:answer.question_id,mode:'ANSWER_ACCEPTED',answer_id:answer.answer_id,answer_text:answer.text,answer_sha256:'c'.repeat(64),assumption:null,reason:'Explicitly accepted'}))};
 const data={id:'proposal',project_id:'project',revision:3,sha256:'d'.repeat(64),answers,payload:{project_id:'project',proposal_id:'proposal',revision:3,status:'CONFIRMED',candidate:{questions},confirmation}};
 return{type:'proposal',artifactId:'proposal',revision:3,contentHash:data.sha256,scope:{projectId:'project'},data};
}
const project=(artifact,questions=artifact.data.payload.candidate.questions)=>adoptedProposalQuestions(artifact,questions);

test('five immutable accepted answers are adopted while two genuine open questions remain; no source or draft is changed',()=>{
 const artifact=fixture(),before=copy(artifact),result=project(artifact);
 assert.deepEqual([...result.keys()],['Q1','Q2','Q3','Q4','Q5']);assert.equal(result.has('Q6'),false);assert.equal(result.get('Q1').text,'Saved answer 0');
 assert.deepEqual(artifact,before);assert.equal(artifact.data.answers[0].accepted,false,'legacy answer flag does not define adoption');
});
test('a legitimate earlier saved answer remains eligible in the exact later confirmation',()=>{
 const artifact=fixture();artifact.data.answers[0].proposal_revision=1;assert.equal(project(artifact).get('Q1').answerId,'answer_0');
 artifact.data.answers[0].proposal_revision=3;assert.equal(project(artifact).has('Q1'),false,'a future answer cannot belong to the accepted basis');
});
test('foreign owner, different hash/version, copied old confirmation or a draft never borrow adoption',()=>{
 const cases=[a=>a.type='discovery',a=>a.artifactId='other',a=>a.scope.projectId='other',a=>a.revision=4,a=>a.contentHash='e'.repeat(64),a=>a.data.payload.project_id='other',a=>a.data.payload.proposal_id='other',a=>a.data.payload.revision=4,a=>a.data.payload.status='DRAFT',a=>a.data.payload.confirmation.user_confirmed=false,a=>{a.data.revision=4;a.revision=4;a.data.payload.revision=4;},a=>a.data.payload.confirmation.accepted_sha256='invalid'];
 for(const mutate of cases){const artifact=fixture();mutate(artifact);assert.equal(project(artifact).size,0);}
});
test('edits to the displayed question fail closed without borrowing the saved decision; restored equal content is order independent',()=>{
 const artifact=fixture(),questions=copy(artifact.data.payload.candidate.questions);questions[0].text+=' draft edit';
 assert.equal(project(artifact,questions).has('Q1'),false);assert.equal(project(artifact,questions).size,4);
 questions[0]=Object.fromEntries(Object.entries(artifact.data.payload.candidate.questions[0]).reverse());assert.equal(project(artifact,questions).size,5);
});
test('mismatched answer references/text, duplicate decisions and incomplete records stay open',()=>{
 const changes=[a=>a.data.answers[0].proposal_id='other',a=>a.data.answers[0].question_id='Q6',a=>a.data.answers[0].text='Different answer',a=>a.data.answers[0].question_sha256='invalid',a=>a.data.answers.push(copy(a.data.answers[0])),a=>a.data.payload.confirmation.resolutions.push(copy(a.data.payload.confirmation.resolutions[0])),a=>delete a.data.payload.confirmation.resolutions[0].answer_sha256];
 for(const change of changes){const artifact=fixture();change(artifact);assert.equal(project(artifact).has('Q1'),false);assert.equal(project(artifact).size,4);}
 const absent=fixture();absent.data.payload.confirmation=null;absent.data.answers.forEach(answer=>answer.accepted=true);assert.equal(project(absent).size,0);
});
test('explicit assumptions are distinct from saved responses and malformed assumptions do not close a question',()=>{
 const artifact=fixture(),record=artifact.data.payload.confirmation.resolutions[0];Object.assign(record,{mode:'ASSUMPTION_ACCEPTED',answer_id:null,assumption:'Explicit user assumption'});
 assert.equal(project(artifact).get('Q1').mode,'ASSUMPTION_ACCEPTED');assert.equal(project(artifact).get('Q1').text,'Explicit user assumption');
 record.answer_id='answer_0';assert.equal(project(artifact).has('Q1'),false);record.answer_id=null;record.assumption=' ';assert.equal(project(artifact).has('Q1'),false);
});
