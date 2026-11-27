import test from 'node:test';
import assert from 'node:assert/strict';
import {reviewObjects} from '../src/workbench-review.ts';
const hash='a'.repeat(64),other='b'.repeat(64);
const plan={id:'plan',project_id:'p',revision:5,sha256:hash,review_status:'CURRENT',flow:{nodes:[]},payload:{status:'CONFIRMED',candidate:{title:'Same title',questions:[]}}};
const book=(id='book',revision=1,sha256=hash)=>({id,revision,sha256,project_id:'p',review_status:'CURRENT',payload:{title:'Same title'}});
const receipt=(revision=5)=>({operation_id:'delivery',status:'EXPORTED_CONFIRMED_PLAN_DERIVATIVES',proposal_ref:{id:'plan',revision,sha256:hash},taskbook_ref:{taskbook_id:'book',revision:1,sha256:hash}});
const rows=(deliveries,books=[book(),book('unlinked')],proposals=[plan])=>reviewObjects('p',{sessions:[],harnesses:[],reports:[],proposals,books,deliveries}).filter(v=>v.kind==='taskbook');
test('same-title taskbooks keep separate identity; only exact saved receipt bytes receive the true plan version',()=>{
 const result=rows([receipt(),receipt()]);assert.equal(result.length,2);assert.equal(result[0].revision,1);assert.equal(result[0].planRevision,5);assert.equal(result[1].planRevision,undefined);assert.equal(result[0].title,result[1].title);
 assert.equal(rows([])[0].planRevision,undefined);
 assert.equal(rows([receipt()],[book('book',2)])[0].planRevision,undefined,'Later manual book revisions are not the exported bytes');
 assert.equal(rows([receipt()],[book('book',1,other)])[0].planRevision,undefined);
 assert.equal(rows([{...receipt(),proposal_ref:{id:'foreign',revision:5,sha256:hash}}])[0].planRevision,undefined);
});
test('historical plan versions remain historical; conflicting exact origins never pick latest or conflate distinct plans',()=>{
 assert.equal(rows([receipt(2)])[0].planRevision,2);
 assert.equal(rows([receipt(2),receipt(5)])[0].planRevision,undefined);
 assert.equal(rows([receipt(),{...receipt(),proposal_ref:{id:'another',revision:5,sha256:hash}}],undefined,[plan,{...plan,id:'another'}])[0].planRevision,undefined);
 assert.equal(rows([{...receipt(),status:'FAILED_OR_PARTIAL',automatic_model_replay:false}])[0].planRevision,undefined);
 const unknown=structuredClone(receipt());delete unknown.taskbook_ref.sha256;assert.equal(rows([unknown])[0].planRevision,undefined);
});
