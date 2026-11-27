'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {createIdeApiAdapter}=require('../ide-api-adapter.cjs');
const {projectPending}=require('../ide-pending-controls.cjs');
const canonical=v=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
const hash=v=>crypto.createHash('sha256').update(canonical(v)).digest('hex');
const scope={project_id:'project',session_ref:{id:'session',revision:1,sha256:'a'.repeat(64)},generation:1};
const qref={id:'que_original',sha256:'b'.repeat(64),generation:1},pref={id:'per_original',sha256:'c'.repeat(64),generation:1};
const question={kind:'QUESTION',request_ref:qref,allowed_choices:['reply','reject'],native_request:{id:qref.id,questions:[{header:'位置',question:'在哪个文件应用修改？',multiple:false,custom:false,options:[{label:'原文件',description:'保留手工说明'},{label:'新副本',description:'保留原稿'}]}]}};
const permission={kind:'PERMISSION',request_ref:pref,allowed_choices:['once','reject'],reply_is_execution_authorization:true,native_request:{id:pref.id,permission:'edit',patterns:['src/calculator.py'],metadata:{secret:'MUST_NOT_PROJECT'}}};
const session=()=>({...scope,owner_status:'READY',native_status:'busy',connected_owner:true,configuration_current:true,observation_is_current:true,active_operation_id:'original-task',messages:[],todo:[],children:[],guidance:[],pending_questions:[question],pending_permissions:[permission],last_event_seq:1,automatic_post_replay:false});
function adapter(){const calls=[],api=createIdeApiAdapter({request:async(url,body)=>{calls.push({url,body});if(!body)return session();return {id:body.operation_id,kind:url.includes('/permissions/')?'PERMISSION_REPLY':url.endsWith('/reject')?'QUESTION_REJECT':'QUESTION_REPLY',status:'SUCCEEDED',session_ref:scope.session_ref,request:body,request_sha256:hash(body),automatic_replay:false};}});return {api,calls};}
test('question reply and rejection use exact pending identity and original owner service',async()=>{
  const {api,calls}=adapter();
  const result=await api('session.question.reply',{scope,payload:{operation_id:'answer',request_ref:qref,answers:[['原文件']]}});
  assert.equal(result.kind,'QUESTION_REPLY');assert.equal(calls.length,2);assert.match(calls[1].url,/questions\/que_original\/reply$/);assert.deepEqual(calls[1].body.request_ref,qref);
  await api('session.question.reject',{scope,payload:{operation_id:'decline',request_ref:qref}});
  assert.match(calls[3].url,/questions\/que_original\/reject$/);assert.equal(calls[3].body.answers,undefined);
});
test('changed generation, changed pending hash, unsupported choice and extra fields never POST',async()=>{
  for(const [method,payload]of [
    ['session.question.reply',{operation_id:'old_generation',request_ref:{...qref,generation:2},answers:[['原文件']]}],
    ['session.question.reply',{operation_id:'old_hash',request_ref:{...qref,sha256:'f'.repeat(64)},answers:[['原文件']]}],
    ['session.question.reply',{operation_id:'bad_answer',request_ref:qref,answers:[['凭空选项']]}],
    ['session.permission.reply',{operation_id:'no_always',request_ref:pref,reply:'always'}],
    ['session.permission.reply',{operation_id:'no_extra_grant',request_ref:pref,reply:'once',grant_all:true}],
  ]){const {api,calls}=adapter();await assert.rejects(api(method,{scope,payload}));assert.equal(calls.filter(c=>c.body).length,0);}
});
test('permission displays exact approved patterns without projecting metadata or widening once',async()=>{
  const projected=projectPending(permission);assert.equal(projected.display_complete,true);assert.deepEqual(projected.native_request.patterns,['src/calculator.py']);assert.doesNotMatch(JSON.stringify(projected),/MUST_NOT_PROJECT/);
  const {api,calls}=adapter();await api('session.permission.reply',{scope,payload:{operation_id:'once',request_ref:pref,reply:'once'}});assert.equal(calls[1].body.reply,'once');assert.deepEqual(calls[1].body.request_ref,pref);
  assert.equal(projectPending({...permission,native_request:{...permission.native_request,patterns:['x'.repeat(1001)]}}).display_complete,false);
});
test('lost POST response inspects original receipt once and never resends authorization',async()=>{
  let posted=0,lookups=0;
  const api=createIdeApiAdapter({request:async(url,body)=>{if(body){posted++;throw Error('lost response');}if(url.includes('/operations/')){lookups++;throw Error('not observed');}return session();}});
  await assert.rejects(api('session.permission.reply',{scope,payload:{operation_id:'unknown_once',request_ref:pref,reply:'once'}}),{code:'OPERATION_OUTCOME_UNKNOWN'});
  assert.equal(posted,1);assert.equal(lookups,1);
});
