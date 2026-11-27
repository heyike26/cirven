// AUTHORED_NOT_RUN: actual ApiClient request freeze and explicit channel hint;
// HTTP and Main readiness are labelled UNIT boundaries, not native evidence.
import test from 'node:test';
import assert from 'node:assert/strict';
import {ApiClient} from '../src/api.ts';
import {authoredErrorCopy,authoredText} from '../src/authored-ui-copy.ts';
function setup(t,ensure){const before=globalThis.eeasDesktop;globalThis.eeasDesktop={ensureEditorCaptureChannel:ensure};t.after(()=>{if(before===undefined)delete globalThis.eeasDesktop;else globalThis.eeasDesktop=before;});const calls=[],api=new ApiClient(async(url,init)=>{if(url==='/api/bootstrap')return Response.json({csrf_token:'unit'});calls.push({url,method:init.method,body:JSON.parse(init.body??'null')});return Response.json({accepted:true});});return {api,calls};}
test('explicit original send freezes its operation and text before metadata readiness without retrying the business post',async t=>{
 let release,entered;const started=new Promise(r=>{entered=r;}),wait=new Promise(r=>{release=r;}),x=setup(t,async()=>{entered();await wait;}),body={operation_id:'original',raw_text:'ORIGINAL_INPUT',session_ref:{id:'s'}};
 const pending=x.api.request('/api/projects/p/agent-sessions/s/messages','POST',body);await started;body.raw_text='NEW_DRAFT';body.operation_id='injected';assert.equal(x.calls.length,0);release();await pending;assert.equal(x.calls.length,1);assert.equal(x.calls[0].body.raw_text,'ORIGINAL_INPUT');assert.equal(x.calls[0].body.operation_id,'original');
});
test('rejection, read and cancellation do not wait for an editor metadata connection',async t=>{
 let starts=0;const x=setup(t,async()=>{starts++;throw Error('should not run');});await x.api.request('/api/projects/p/agent-sessions/s/permissions/per_1/reply','POST',{reply:'reject'});await x.api.request('/api/projects/p/agent-sessions/s','GET');await x.api.request('/api/projects/p/agent-sessions/s/interrupt','POST',{});assert.equal(starts,0);assert.equal(x.calls.length,3);
});
test('abort during channel readiness prevents the original write, while channel failure alone never bypasses backend guards',async t=>{
 const abort=new AbortController(),x=setup(t,async()=>{abort.abort();});await assert.rejects(x.api.request('/api/projects/p/agent-sessions/s/messages','POST',{operation_id:'original'},abort.signal));assert.equal(x.calls.length,0);
 globalThis.eeasDesktop.ensureEditorCaptureChannel=async()=>{throw Error('UNIT unavailable');};await x.api.request('/api/projects/p/agent-sessions/s/load','POST',{operation_id:'load_original'});assert.equal(x.calls.length,1);
});
test('editor capture transport codes become readable authored copy without modifying exact raw failure evidence',()=>{
 const error=Error('EDITOR_CAPTURE_DIRTY: internal transport detail');assert.equal(authoredText(authoredErrorCopy(error)),'编辑窗口有未保存内容，请先保存或保留；原输入仍在。');assert.match(error.message,/EDITOR_CAPTURE_DIRTY:/);assert.doesNotMatch(authoredText(authoredErrorCopy(Error('EDITOR_CAPTURE_WAITING: opaque'))),/EDITOR_CAPTURE|opaque/);
});

// AUTHORED_NOT_RUN: the same metadata readiness channel covers direct edits;
// the backend independently validates the workspace grant and file CAS.
test('standalone workspace edit freezes its original CAS and uses the metadata-only channel once',async t=>{
 let checks=0;const x=setup(t,async()=>{checks++;}),body={operation_id:'edit_original',path:'a.py',expected_revision:4,expected_sha256:'a'.repeat(64),content:'KEEP_EXACT_EDIT'};
 await x.api.request('/api/projects/p/local-workspaces/w/edits','POST',body);assert.equal(checks,1);assert.equal(x.calls.length,1);assert.deepEqual(x.calls[0].body,body);assert.equal(x.calls[0].body.session_ref,undefined);
});

// AUTHORED_NOT_RUN: source-authority copy stays separate from exact raw errors.
test('source withdrawal notice is scoped to the original generation and retains readable text',async()=>{
 const {contextAuthorityNotice}=await import('../src/agent-sessions.ts'),issue={code:'SOURCE_CONTEXT_CHANGED',generation:3,operation_id:'original_task',scope:'FUTURE_HOST_ADMISSIONS_AND_API_RELAY',original_history_retained:true},session={generation:3,context_authority_issue:issue};
 assert.match(contextAuthorityNotice(session),/原聊天与草稿保留/);assert.match(contextAuthorityNotice(session,true),/this request was blocked/);assert.equal(contextAuthorityNotice({...session,generation:4}),'');const raw=Error('SOURCE_CONTEXT_CHANGED: original private detail');assert.doesNotMatch(authoredText(authoredErrorCopy(raw)),/private detail|SOURCE_CONTEXT/);assert.match(raw.message,/private detail/);
});
