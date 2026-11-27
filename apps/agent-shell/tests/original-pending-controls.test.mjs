import test from 'node:test';
import assert from 'node:assert/strict';
import {OriginalControlDecisions,ControlQuestionDraft,controlIdentity,controlQuestions,pendingPatches,pendingFileName,pendingDiffBody,pendingDiffPreview,pendingActionTitle,questionAnswers,flushOriginalControlDrafts} from '../src/original-pending-controls.ts';
import {sameOriginalFileTarget} from '../src/original-file-input.ts';
import {AgentSessionsApi,AgentSessionOutcomeError} from '../src/agent-sessions.ts';
import {ApiClient} from '../src/api.ts';

const ref={id:'a'.repeat(32),revision:1,sha256:'b'.repeat(64)};
const permission=(patch={})=>({kind:'PERMISSION',request_ref:{id:'per_one',generation:2,sha256:'c'.repeat(64)},allowed_choices:['once','reject'],reply_is_execution_authorization:true,native_request:{permission:'edit',patterns:['notes.txt'],metadata:{files:[{relativePath:'notes.txt',patch:'@@ -1 +1 @@\n-old\n+new\n'}]}},...patch});
const owner=(p=permission(),patch={})=>({project_id:'p',session_ref:ref,generation:2,active_operation_id:'message_one',owner_status:'READY',connected_owner:true,configuration_current:true,observation_is_current:true,pending_questions:p.kind==='QUESTION'?[p]:[],pending_permissions:p.kind==='QUESTION'?[]:[p],...patch});
const question=()=>permission({kind:'QUESTION',allowed_choices:['reply','reject'],native_request:{questions:[{question:'保留哪些内容？',multiple:true,options:[{label:'人工备注',description:'Do not change it'},{label:'原图'}]},{question:'交付名',options:[],custom:true}]}});
const storage=()=>{const values=new Map();return{values,getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v)};};
function locks(){const jobs=new Map();return{request(name,_opts,work){const old=jobs.get(name)??Promise.resolve(),next=old.catch(()=>{}).then(work);jobs.set(name,next);return next;}};}
const receipt=(id,status='RETURNED')=>({id,status,request_sha256:'d'.repeat(64),session_ref:ref,automatic_replay:false});

test('permission titles describe the actual requested action without claiming the files were selected',()=>{
 const expected={read:['读取文件内容','Read file contents'],glob:['查找文件','Find files'],grep:['搜索文件内容','Search file contents'],list:['查看目录','List directory'],edit:['确认文件修改','Review file changes'],bash:['运行命令','Run command']};
 for(const [name,labels] of Object.entries(expected)){
  const p=permission({native_request:{permission:name,patterns:['*']}}),before=structuredClone(p);
  assert.equal(pendingActionTitle(p,'zh-CN'),labels[0]);assert.equal(pendingActionTitle(p,'en'),labels[1]);assert.deepEqual(p,before);
 }
 assert.equal(pendingActionTitle(question(),'en'),'Your answer is needed');
 assert.equal(pendingActionTitle(permission({native_request:{permission:'private_unknown_tool'}}),'en'),'Review tool action');
});

test('the unique response key is durably stored before one dispatch; a second view cannot grant it again',async()=>{
 const st=storage(),lock=locks(),p=permission(),s=owner(p);let posts=0,release;const ready=new Promise(r=>release=r);
 const client={pendingReply:async(session,item,choice,answers,key)=>{posts++;const rows=[...st.values.values()].map(v=>JSON.parse(v));assert.equal(rows[0].attempts[0].operationId,key);assert.equal(rows[0].attempts[0].status,'ATTEMPTED');assert.equal(session.generation,2);assert.equal(item.request_ref.id,p.request_ref.id);assert.equal(choice,'once');await ready;return receipt(key);},operation:async()=>{throw Error('No query required');}};
 const a=new OriginalControlDecisions(st,client,lock),b=new OriginalControlDecisions(st,client,lock),first=a.decide(s,p,'once');await new Promise(r=>setImmediate(r));
 const second=b.decide(s,p,'once');await assert.rejects(()=>a.decide(s,p,'once'),/BUSY/);release();await first;await assert.rejects(()=>second,/CHECK_OR_REJECT/);assert.equal(posts,1);assert.equal(a.latest(s,p).status,'RETURNED');
});

test('network loss and renderer restart query the same operation and never resend the permission',async()=>{
 const st=storage(),p=permission(),s=owner(p),queries=[];let posts=0,key;
 const client={pendingReply:async(_s,_p,_c,_a,k)=>{posts++;key=k;throw new AgentSessionOutcomeError('connection lost',k);},operation:async(_s,k)=>{queries.push(k);return receipt(k);}};
 const a=new OriginalControlDecisions(st,client,locks());await assert.rejects(()=>a.decide(s,p,'once'),/connection lost/);const attempt=a.unresolved(s)[0];assert.equal(attempt.operationId,key);
 const restarted=new OriginalControlDecisions(st,client,locks());await assert.rejects(()=>restarted.decide(s,p,'reject'),/CHECK_OR_REJECT/);await restarted.inspect(s,attempt);assert.deepEqual(queries,[key]);assert.equal(posts,1);assert.equal(restarted.unresolved(s).length,0);
});

test('failed confirmation storage retains the original attempted key for read recovery',async()=>{
 const st=storage(),set=st.setItem,p=permission(),s=owner(p);let broken=false,posts=0;
 st.setItem=(k,v)=>{if(broken)throw Error('disk full');set(k,v);};
 const client={pendingReply:async(_s,_p,_c,_a,k)=>{posts++;broken=true;return receipt(k);},operation:async(_s,k)=>receipt(k)};
 const a=new OriginalControlDecisions(st,client,locks());await assert.rejects(()=>a.decide(s,p,'once'),/disk full/);broken=false;const saved=a.unresolved(s)[0];assert.equal(saved.status,'ATTEMPTED');await a.inspect(s,saved);assert.equal(a.latest(s,p).status,'RETURNED');assert.equal(posts,1);
});

test('initial save failure and stale request identity fail before any grant',async()=>{
 const p=permission(),s=owner(p);let posts=0;const client={pendingReply:async()=>{posts++;},operation:async()=>{throw Error();}},bad={getItem:()=>null,setItem:()=>{throw Error('disk full');}};
 await assert.rejects(()=>new OriginalControlDecisions(bad,client,locks()).decide(s,p,'once'),/disk full/);
 for(const changed of [{...s,generation:3},{...s,active_operation_id:null},{...s,connected_owner:false},{...s,pending_permissions:[]}])await assert.rejects(()=>new OriginalControlDecisions(storage(),client,locks()).decide(changed,p,'once'),/REQUEST_CHANGED/);
 await assert.rejects(()=>new OriginalControlDecisions(storage(),client,undefined).decide(s,p,'once'),/LOCK_UNAVAILABLE/);assert.equal(posts,0);
});

test('a stale file proposal permits explicit rejection and records recovery without a hidden message retry',async()=>{
 const st=storage(),p=permission(),s=owner(p),calls=[];const client={pendingReply:async(_s,_p,c,_a,key)=>{calls.push(c);if(c==='once')throw new AgentSessionOutcomeError('WORKSPACE_SOURCE_CHANGED: saved elsewhere',key,receipt(key,'FAILED'));return receipt(key);},operation:async(_s,key)=>receipt(key,'FAILED')};
 const a=new OriginalControlDecisions(st,client,locks());await assert.rejects(()=>a.decide(s,p,'once'),/SOURCE_CHANGED/);assert.equal(a.latest(s,p).sourceChanged,true);await assert.rejects(()=>a.decide(s,p,'once'),/CHECK_OR_REJECT/);await a.decide(s,p,'reject');
 const recoveredOwner={...s,active_operation_id:null,pending_permissions:[]},[action]=a.recoveries(recoveredOwner);assert.equal(action.choice,'reject');assert.deepEqual(calls,['once','reject']);await a.recoveryPrepared(recoveredOwner,action);assert.equal(a.recoveries(recoveredOwner).length,0);
});

test('unknown controls from an earlier generation remain read only observations',async()=>{
 const st=storage(),p=permission(),s=owner(p);let posts=0;const client={pendingReply:async(_s,_p,_c,_a,k)=>{posts++;throw new AgentSessionOutcomeError('lost',k);},operation:async(_s,key)=>receipt(key,'CANCELLED')};
 const a=new OriginalControlDecisions(st,client,locks());await assert.rejects(()=>a.decide(s,p,'once'));const next={...s,generation:3,active_operation_id:null,pending_permissions:[]};const saved=a.unresolved(next)[0];await a.inspect(next,saved);assert.equal(posts,1);assert.equal(a.unresolved(next).length,0);
});

test('captured request and answers cannot be swapped while the response waits for its lock',async()=>{
 const st=storage(),q=question(),s=owner(q),answers=[['人工备注'],['原样 😀\r\n']];let release,seen;
 const lock={request:async(_name,_options,work)=>{await new Promise(r=>release=r);return work();}};
 const a=new OriginalControlDecisions(st,{pendingReply:async(_s,p,c,value,k)=>{seen={p,c,value};return receipt(k);},operation:async()=>{throw Error();}},lock);
 const running=a.decide(s,q,'reply',answers);answers[1][0]='changed';q.native_request.questions[0].question='new question';release();await running;assert.equal(seen.p.native_request.questions[0].question,'保留哪些内容？');assert.equal(seen.value[1][0],'原样 😀\r\n');
});

test('answer drafts survive remount and restart, isolating parent, owner, request hash and generation',()=>{
 const st=storage(),q=question(),s=owner(q),i=controlIdentity(s,q),value={answers:[['人工备注']],custom:['','原样 {"user":"JSON"} 😀\r\n']};const a=new ControlQuestionDraft(st,i);a.set(value);assert.deepEqual(new ControlQuestionDraft(st,i).read(),value);
 for(const changed of [{...i,parent:'another'},{...i,session:{...ref,id:'e'.repeat(32)}},{...i,generation:3,request:{...i.request,generation:3}},{...i,request:{...i.request,sha256:'e'.repeat(64)}}])assert.deepEqual(new ControlQuestionDraft(st,changed).read(),{answers:[],custom:[]});
 assert.deepEqual(questionAnswers(q,value),[['人工备注'],['原样 {"user":"JSON"} 😀\r\n']]);
});

test('competing answer drafts preserve both before an explicit recovery choice; a failed save blocks the close barrier',()=>{
 const st=storage(),q=question(),i=controlIdentity(owner(q),q),a=new ControlQuestionDraft(st,i),b=new ControlQuestionDraft(st,i);a.set({answers:[['人工备注']],custom:['','first']});assert.throws(()=>b.set({answers:[['原图']],custom:['','second']}),/CONFLICT/);assert.throws(()=>flushOriginalControlDrafts(),/CONFLICT/);assert.equal(b.read().custom[1],'second');assert.equal(b.inspect().custom[1],'first');b.resolve('local');flushOriginalControlDrafts();assert.equal(new ControlQuestionDraft(st,i).read().custom[1],'second');assert([...st.values.keys()].some(k=>k.startsWith('eeas-discussion-draft-copies-v1:')));
});

test('malformed questions, non-options and disallowed custom replies cannot become an answer',()=>{
 const q=question();for(const value of [{answers:[[]],custom:[]},{answers:[['invented']],custom:['','name']}])assert.throws(()=>questionAnswers(q,value),/ANSWERS/);
 q.native_request.questions[1].custom=false;assert.throws(()=>questionAnswers(q,{answers:[['人工备注']],custom:['','illegal']}),/INVALID/);q.native_request.questions[0].options[0].label=42;assert.deepEqual(controlQuestions(q),[]);
});

test('only readable actual diff strings are projected, without rendering native JSON payloads',()=>{
 const p=permission();assert.deepEqual(pendingPatches(p),[{path:'notes.txt',patch:'@@ -1 +1 @@\n-old\n+new\n'}]);p.native_request.metadata={diff:'--- a\n+++ b\n-old\n+new',filepath:'中文/notes.txt',private:{token:'DO_NOT_RENDER'}};assert.deepEqual(pendingPatches(p),[{path:'中文/notes.txt',patch:p.native_request.metadata.diff}]);p.native_request.metadata.files=[{filePath:'a',patch:{private:true}}];assert.deepEqual(pendingPatches(p),[]);
});

test('refreshing current bytes preserves the selected target and never accepts another file or binding',()=>{
 const src={kind:'local_file',project_id:'p',workspace_id:'a'.repeat(64),binding_revision:1,binding_sha256:'b'.repeat(64),path:'说明.txt',revision:1,sha256:'c'.repeat(64)},a={kind:'WORKSPACE_FILE',source_ref:src};assert.equal(sameOriginalFileTarget(a,{...a,source_ref:{...src,revision:2,sha256:'d'.repeat(64)}}),true);
 for(const patch of [{path:'other.txt'},{workspace_id:'f'.repeat(64)},{binding_revision:2},{project_id:'another'}])assert.equal(sameOriginalFileTarget(a,{...a,source_ref:{...src,...patch}}),false);
 assert.equal(sameOriginalFileTarget(undefined,undefined),true);assert.equal(sameOriginalFileTarget(a,undefined),false);
});

test('real API decoding and localized failure copy retain a machine-readable stale guard for rejection and recovery',async()=>{
 const st=storage(),p=permission(),s=owner(p),calls=[];
 const api=new AgentSessionsApi(new ApiClient(async(url,init)=>{
  if(url==='/api/bootstrap')return Response.json({csrf_token:'opaque'});
  calls.push({url,method:init.method,body:init.body?JSON.parse(init.body):undefined});
  if(init.method==='POST'&&JSON.parse(init.body).reply==='once')return Response.json({error:{code:'REVISION_CONFLICT',message:'WORKSPACE_SOURCE_CHANGED: saved files or review evidence changed'}},{status:409});
  const key=init.method==='POST'?JSON.parse(init.body).operation_id:url.split('/').at(-1);
  return Response.json({...receipt(key,init.method==='POST'?'RETURNED':'FAILED'),...(init.method==='GET'?{error_code:'WORKSPACE_SOURCE_CHANGED'}:{})});
 }));
 const a=new OriginalControlDecisions(st,api,locks());
 await assert.rejects(()=>a.decide(s,p,'once'),e=>e instanceof AgentSessionOutcomeError&&e.failureCode==='WORKSPACE_SOURCE_CHANGED'&&e.message==='文件或修订依据已变更，请拒绝这次修改并重新检查。');
 assert.equal(a.latest(s,p).sourceChanged,true);await assert.rejects(()=>a.decide(s,p,'once'),/CHECK_OR_REJECT/);await a.decide(s,p,'reject');assert.equal(a.recoveries({...s,active_operation_id:null,pending_permissions:[]}).length,1);assert.deepEqual(calls.filter(c=>c.method==='POST').map(c=>c.body.reply),['once','reject']);
});

test('a lost HTTP response is confirmed from its exact receipt, including a persisted stale guard after restart',async()=>{
 for(const status of ['RETURNED','FAILED']){
  const st=storage(),p=permission(),s=owner(p);let posts=0;
  const api=new AgentSessionsApi(new ApiClient(async(url,init)=>{
   if(url==='/api/bootstrap')return Response.json({csrf_token:'opaque'});
   if(init.method==='POST'){posts++;throw Error('HTTP response lost');}
   return Response.json({...receipt(url.split('/').at(-1),status),...(status==='FAILED'?{error_code:'WORKSPACE_SOURCE_CHANGED'}:{})});
  }));
  const a=new OriginalControlDecisions(st,api,locks());if(status==='RETURNED')assert.equal((await a.decide(s,p,'once')).status,'RETURNED');else await assert.rejects(()=>a.decide(s,p,'once'),e=>e.failureCode==='WORKSPACE_SOURCE_CHANGED');
  const restarted=new OriginalControlDecisions(st,api,locks());assert.equal(restarted.latest(s,p).sourceChanged,status==='FAILED');assert.equal(restarted.unresolved(s).length,0);assert.equal(posts,1);
 }
});

test('diff summary omits structural path prelude only, preserving actual hunk content and exact filenames',()=>{
 const patch='Index: /very/long/path/说明.txt\n===\n--- old\n+++ new\n@@ -1,2 +1,2 @@\n--- a literal line removed\n+++ a literal line added\n unchanged\n';
 assert.equal(pendingDiffBody(patch),'@@ -1,2 +1,2 @@\n--- a literal line removed\n+++ a literal line added\n unchanged\n');assert.equal(pendingDiffBody('no hunk\n--- keep'),'no hunk\n--- keep');assert.equal(pendingFileName('D:\\项目\\中文 English.txt'),'中文 English.txt');
 const q=question();assert.deepEqual(questionAnswers(q,{answers:[['人工备注']],custom:['人工备注','raw 😀']}),[['人工备注'],['raw 😀']]);
});

test('real multi-select then later custom input remains dense through JSON storage and restart',()=>{
 const st=storage(),i=controlIdentity(owner(question()),question()),d={answers:[],custom:[]};d.answers[0]=['人工备注'];const first=new ControlQuestionDraft(st,i);first.set(d);d.custom[1]='中文原话 {"保留":true} 😀\n第二行';first.set(d);
 const restarted=new ControlQuestionDraft(st,i);assert.deepEqual(restarted.read(),{answers:[['人工备注']],custom:['','中文原话 {"保留":true} 😀\n第二行']});assert.deepEqual(questionAnswers(question(),restarted.read()),[['人工备注'],['中文原话 {"保留":true} 😀\n第二行']]);
});

test('the compact change preview keeps every exact addition and deletion; full context remains available separately',()=>{
 const raw='Index: /long/path\n--- a\n+++ b\n@@ -1,3 +1,3 @@\n keep\n-old\n+new\n tail\n@@ -50 +50 @@\n--- literal removal\n+++ literal addition\n';assert.equal(pendingDiffPreview(raw),'-old\n+new\n--- literal removal\n+++ literal addition');assert(pendingDiffBody(raw).includes(' tail'));assert.equal(pendingDiffPreview('unexpected format\n+literal'),'unexpected format\n+literal');
});
