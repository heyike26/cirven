import {evidenceDirectory} from './historical-renderer-evidence.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,writeFileSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {pathToFileURL} from 'node:url';

const app=process.env.EEAS_FRONTEND_APP??resolve(import.meta.dirname,'..');
let sequence=0;
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const copy=value=>structuredClone(value);
async function moduleFor(){return import(pathToFileURL(resolve(app,'src/workbench-state.ts')).href+'?barrier='+ ++sequence);}
const key=id=>({project_id:'isolated_B',kind:'experience',object_id:id,revision:1,sha256:'a'.repeat(64)});
function remoteFixture(){
 const calls=[],reads=[],state={revision:0,fields:{},view:{},dirty:false},receipts=new Map();
 const remote={load:async k=>({status:state.revision?'SAVED':'EMPTY',key:k,state_revision:state.revision,fields:copy(state.fields),view:copy(state.view),dirty:state.dirty}),
  save:async(k,revision,fields,view,dirty,id)=>{calls.push({key:copy(k),revision,fields:copy(fields),view:copy(view),dirty,id});await remote.beforeAck?.(calls.at(-1));const result={status:'SAVED',key:k,state_revision:revision+1,fields,view,dirty,operation_id:id};state.revision=revision+1;state.fields=copy(fields);state.view=copy(view);state.dirty=dirty;receipts.set(id,copy(result));return result;},
  operation:async(k,id)=>{reads.push(id);if(!receipts.has(id))throw Error('No original receipt');return copy(receipts.get(id));}};
 return {remote,calls,reads,state,receipts};
}
function cleanup(t,session){t.after(()=>clearTimeout(session.timer));}
function markup(node){const escape=value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');if(node.type==='#text')return escape(node.value);if(node.type==='root')return node.children.map(markup).join('');const attributes=Object.entries(node.props).filter(([name,value])=>name!=='children'&&!name.startsWith('on')&&name!=='value'&&typeof value!=='function'&&typeof value!=='object'&&value!==false&&value!==undefined).map(([name,value])=>' '+name+'="'+escape(value===true?'':value)+'"').join('');const value=node.props.value;return '<'+node.type+attributes+(node.type==='input'&&value!==undefined?' value="'+escape(value)+'"':'')+'>'+((node.type==='textarea'&&value!==undefined)?escape(value):node.children.map(markup).join(''))+'</'+node.type+'>';}

test('finish drains a late real view mutation with two acknowledged snapshots and preserves the original business request', {timeout:2500}, async t=>{
 const {ObjectDraftSession}=await moduleFor(),f=remoteFixture(),session=new ObjectDraftSession(key('late-scroll'),f.remote);cleanup(t,session);await session.ready;
 const entered=deferred(),release=deferred();f.remote.beforeAck=async()=>{if(f.calls.length===1){entered.resolve();await release.promise;}};
 const original={operation_id:'review-original',request_sha256:'b'.repeat(64),state:'PREPARED'};session.set('fields','reviewDraft',original);
 const finish=session.finish();await entered.promise;session.set('view','scroll',{x:0,y:416});release.resolve();await finish;
 assert.equal(f.calls.length,2);assert.deepEqual(f.calls[0].fields.reviewDraft,original);assert.deepEqual(f.calls[1].fields.reviewDraft,original);assert.deepEqual(f.state.view.scroll,{x:0,y:416});assert.notEqual(f.calls[0].id,f.calls[1].id);assert.equal(session.status,'saved');assert.equal(f.reads.length,0);
});

test('finish persists a new field arriving during save before it resolves, while keeping the same exact editing basis', {timeout:2500}, async t=>{
 const {ObjectDraftSession}=await moduleFor(),f=remoteFixture(),session=new ObjectDraftSession(key('late-field'),f.remote);cleanup(t,session);await session.ready;const entered=deferred(),release=deferred();
 f.remote.beforeAck=async()=>{if(f.calls.length===1){entered.resolve();await release.promise;}};session.set('fields','comment','第一次人工输入');const finish=session.finish();await entered.promise;session.set('fields','comment','保存途中用户的新输入😀');release.resolve();await finish;
 assert.equal(f.calls.length,2);assert.equal(f.state.fields.comment,'保存途中用户的新输入😀');assert.ok(f.calls.every(call=>JSON.stringify(call.key)===JSON.stringify(session.key)));assert.equal(session.status,'saved');
});

test('continuous changes exhaust exactly four immediate drain rounds and remain unsaved rather than acknowledging close', {timeout:2500}, async t=>{
 const {ObjectDraftSession}=await moduleFor(),f=remoteFixture(),session=new ObjectDraftSession(key('continuous'),f.remote);cleanup(t,session);await session.ready;
 f.remote.beforeAck=async()=>session.set('view','scroll',{x:0,y:f.calls.length});session.set('fields','comment','未丢弃的人工草稿');await assert.rejects(session.finish(),/仍有本机草稿没有完成保存/);
 assert.equal(f.calls.length,4);assert.equal(session.status,'ready');assert.equal(f.state.view.scroll.y,3);assert.equal(session.view.scroll.y,4);assert.equal(session.fields.comment,'未丢弃的人工草稿');
});

test('unknown local save is never retried by drain or close; only explicit original receipt inspection unlocks later changes', {timeout:2500}, async t=>{
 const {ObjectDraftSession}=await moduleFor(),f=remoteFixture(),session=new ObjectDraftSession(key('unknown'),f.remote);cleanup(t,session);await session.ready;const originalSave=f.remote.save;
 f.remote.save=async(...args)=>{const result=await originalSave(...args);if(f.calls.length===1){session.set('view','scroll',{x:0,y:24});throw Error('reply lost after original local commit');}return result;};
 session.set('fields','comment','原草稿');await assert.rejects(session.finish(),/待核查/);assert.equal(f.calls.length,1);const id=f.calls[0].id;await assert.rejects(session.finish(),/待核查/);assert.equal(f.calls.length,1);assert.equal(f.reads.length,0);
 await session.inspect();assert.deepEqual(f.reads,[id]);await session.finish();assert.equal(f.calls.length,2);assert.equal(f.state.fields.comment,'原草稿');assert.deepEqual(f.state.view.scroll,{x:0,y:24});
});

for(const [status,expected] of [[422,'error'],[409,'conflict']])test('local '+status+' stops draining without retrying or discarding current input', {timeout:2500}, async t=>{
 const {ObjectDraftSession}=await moduleFor(),f=remoteFixture(),session=new ObjectDraftSession(key('reject-'+status),f.remote);cleanup(t,session);await session.ready;f.remote.beforeAck=async()=>{session.set('fields','comment','拒绝期间输入保留');throw Object.assign(Error('definite local rejection'),{status});};
 session.set('fields','comment','原输入');await assert.rejects(session.finish());await assert.rejects(session.finish());assert.equal(f.calls.length,1);assert.equal(session.status,expected);assert.equal(session.fields.comment,'拒绝期间输入保留');assert.equal(f.reads.length,0);
});

test('a failed initial load with edits does not write, while an untouched failed read stays nonblocking for normal close', {timeout:2500}, async t=>{
 const {ObjectDraftSession}=await moduleFor(),f=remoteFixture();f.remote.load=async()=>{throw Error('load unavailable');};const session=new ObjectDraftSession(key('unloaded'),f.remote);cleanup(t,session);await session.ready;await session.finish();session.set('fields','comment','尚未恢复时用户输入');await assert.rejects(session.finish(),/暂未读到/);assert.equal(f.calls.length,0);assert.equal(session.fields.comment,'尚未恢复时用户输入');
});

test('the actual shared normal-close barrier waits for the latest acknowledged draft and leaves a readonly session untouched', {timeout:2500}, async t=>{
 const m=await moduleFor(),f=remoteFixture();Object.assign(m.workbenchStateApi,f.remote);const edited=m.objectDraft(key('close-edit')),readonly=m.objectDraft(key('close-read'));cleanup(t,edited);cleanup(t,readonly);await Promise.all([edited.ready,readonly.ready]);
 const entered=deferred(),release=deferred();f.remote.beforeAck=async()=>{if(f.calls.length===1){entered.resolve();await release.promise;}};edited.set('fields','comment','关闭时草稿');const closing=m.flushWorkbenchDrafts();await entered.promise;edited.set('view','scroll',{x:0,y:808});release.resolve();await closing;
 assert.equal(f.calls.length,2);assert.ok(f.calls.every(call=>call.key.object_id==='close-edit'));assert.equal(f.state.view.scroll.y,808);assert.equal(edited.status,'saved');
});

// Reuse only the original real JSX harness declaration; no old test is registered
// or rerun. Its HTTP/element host boundaries stay explicit and unchanged.
let experienceFixture,experienceOutput;
async function loadExperienceFixture(){if(experienceFixture)return experienceFixture;const file=resolve(app,'tests/writing-experience-source58.test.mjs'),source=readFileSync(file,'utf8'),cut=source.indexOf('\ntest(');assert.ok(cut>0);const dir=experienceOutput=evidenceDirectory('object-draft-barrier-source59');const generated=join(dir,'real-experience-harness.mjs');writeFileSync(generated,source.slice(0,cut).replace("process.env.EEAS_FRONTEND_APP??resolve(dirname(fileURLToPath(import.meta.url)),'..')",JSON.stringify(app))+'\nexport {harness,settle,until,idle,chooseSource,click,input,change};\n',{flag:'wx'});experienceFixture=await import(pathToFileURL(generated).href);return experienceFixture;}

test('one actual review-button click persists its frozen request plus a late scroll and dispatches exactly one original business review', {timeout:5000}, async t=>{
 const {harness,settle,until,chooseSource,click,input,change}=await loadExperienceFixture(),s=harness(t);await settle();await chooseSource(s);click(s.button('复制到当前项目草稿'));await until(()=>!!s.label('写法规则 structure'));
 change(s.label('接受规则 structure'),'',true);input(s.label('写法规则 structure'),'B明确审阅的新写法');input(s.label('章节标题 structure'),'B新标题');const reason=s.find('input',item=>item.parent?.type==='label'&&item.parent.children.some(child=>child.value?.includes('此次审阅理由')));input(reason,'本人核对并接受');
 writeFileSync(join(experienceOutput,'experience-review.actual-jsx.html'),markup(s.all()[0]),{flag:'wx'});
 const remote=s.draft.remote,originalSave=remote.save.bind(remote),prepared=[];let injected=false;remote.save=async(...args)=>{const result=await originalSave(...args);if(args[2].reviewDraft?.state==='PREPARED'){prepared.push(copy(args[2].reviewDraft));if(!injected){injected=true;s.draft.set('view','scroll',{x:0,y:416});}}return result;};
 click(s.button('保存接受的规则'));await until(()=>s.draft.fields.reviewDraft?.state==='COMPLETED');
 const writes=s.dispatches.filter(item=>item.id==='experience.review');assert.equal(writes.length,1);assert.equal(prepared.length,2);assert.equal(prepared[0].operation_id,prepared[1].operation_id);assert.equal(prepared[0].request_sha256,prepared[1].request_sha256);assert.deepEqual(writes[0].input.profile.profile_ref,prepared[0].profile_ref);assert.equal(writes[0].input.operationId,prepared[0].operation_id);assert.equal(writes[0].input.reason,'本人核对并接受');assert.equal(s.state.b[0].status,'REVIEWED');assert.equal(s.state.b[0].approved_rules[0].heading,'B新标题');assert.equal(s.draft.view.scroll.y,416);assert.ok(!s.text().includes('仍有本机草稿没有完成保存'));
});
