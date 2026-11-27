import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';

// Reuse the existing actual JSX/ObjectDraft/ApiClient fixture without running
// its unrelated test cases. Only the element-host and controlled persistence
// boundary gain focus/selection/detach and explicit ACK/failure observations.
// This is Solid renderer UNIT evidence, not browser/native caret qualification.
const app=process.env.EEAS_FRONTEND_APP??resolve(dirname(fileURLToPath(import.meta.url)),'..');
const source=readFileSync(resolve(app,'tests/manual-harness-source59.test.mjs'),'utf8');
let prefix=source.split('\ntest(')[0];
const replacements=[
 ["},renderer=createRenderer(host),modules=new Map(),disposals=[];","},renderer=createRenderer(instrumentHost(host)),modules=new Map(),disposals=[];"],
 ["if(url.pathname.endsWith('/workbench-state/save')){if(state.failDraft)","if(url.pathname.endsWith('/workbench-state/save')){if(state.holdDraftSave)await state.holdDraftSave;if(state.rejectDraft)return response({error:'Original QA local draft rejected',code:'INVALID_INPUT'},422);if(state.failDraft)"],
 ["state.draftOperations.set(body.operation_id,copy(result));return response(result);","state.draftOperations.set(body.operation_id,copy(result));if(state.loseDraftReply)throw Error('Original QA persisted ACK lost');return response(result);"],
 ["t.after(()=>result.close());return result;","t.after(()=>result.close());return Object.assign(result,{load,modules,host});"]
];
for(const [before,after]of replacements){assert.equal(prefix.split(before).length,2,'one precise existing fixture seam');prefix=prefix.replace(before,after);}
const hostInstrumentation=String.raw`
function instrumentHost(host){
 const create=host.createElement,remove=host.removeNode,set=host.setProperty;
 document.activeElement=null;document.focusDetachCount=0;
 const contains=(ancestor,node)=>{for(let n=node;n;n=n.parent)if(n===ancestor)return true;return false;};
 host.createElement=type=>{const node=create(type);node.value='';node.selectionStart=0;node.selectionEnd=0;node.scrollTop=0;node.scrollLeft=0;node.open=false;
  node.focus=()=>{document.activeElement=node;};node.setSelectionRange=(start,end)=>{node.selectionStart=start;node.selectionEnd=end;};
  Object.defineProperty(node,'isConnected',{get(){for(let n=node;n;n=n.parent)if(n.type==='root')return true;return false;}});return node;};
 host.removeNode=(parent,node)=>{if(contains(node,document.activeElement)){document.focusDetachCount++;document.activeElement=null;}return remove(parent,node);};
 host.setProperty=(node,key,value)=>{set(node,key,value);if(key==='value'&&node.value!==String(value??'')){node.value=String(value??'');node.selectionStart=node.selectionEnd=node.value.length;}};
 return host;
}
`;
const oldApp=process.env.EEAS_FRONTEND_APP;process.env.EEAS_FRONTEND_APP=app;
const fixtures=await import('data:text/javascript;base64,'+Buffer.from(prefix+hostInstrumentation+'\nexport {harness,fixture,click,change,settle,until,capture,exportGate,copy,digest};').toString('base64'));
if(oldApp===undefined)delete process.env.EEAS_FRONTEND_APP;else process.env.EEAS_FRONTEND_APP=oldApp;
const {harness,click,change,settle,until,capture,exportGate,copy,digest}=fixtures;
const rowOf=node=>{for(let n=node;n;n=n.parent)if(n.props?.['data-manual-hunk'])return n;throw Error('Actual manual JSX row missing');};
const ancestor=(node,type)=>{for(let n=node;n;n=n.parent)if(n.type===type)return n;throw Error('Element host ancestor missing');};
function userInput(node,value){assert.ok(node?.isConnected,'actual current input is connected');assert.notEqual(node.props.disabled,true);node.value=value;node.setSelectionRange(value.length,value.length);node.props.onInput({currentTarget:node});}
async function ready(t){const s=harness(t);await exportGate(s);await capture(s);await s.flush();return s;}
async function reasonReady(t,mode='KEEP'){const s=await ready(t);click(s.button(mode==='KEEP'?'采用':'改写'));change(s.label('人工修改目标字段'),'acceptance');change(s.label('人工修改采用方式'),'ADD');await settle();return s;}
function identicalInput(s,label,expected,row){assert.equal(s.label(label),expected,'unrelated draft notification must retain the original JSX input');assert.equal(rowOf(expected),row);assert.ok(expected.isConnected);assert.equal(document.activeElement,expected);}

test('choice changes preserve the original hunk row and freshly revealed target controls',async t=>{
 const s=await ready(t),adopt=s.button('采用'),row=rowOf(adopt);click(adopt);
 assert.equal(rowOf(s.label('人工修改目标字段')),row,'KEEP must not detach the original row');
 const nodeSelect=s.label('人工修改目标节点'),field=s.label('人工修改目标字段');nodeSelect.focus();change(field,'acceptance');
 assert.equal(s.label('人工修改目标节点'),nodeSelect);assert.equal(s.label('人工修改目标字段'),field);assert.equal(rowOf(field),row);assert.equal(document.activeElement,nodeSelect);
 change(s.label('人工修改采用方式'),'ADD');assert.equal(rowOf(s.label('人工修改决定理由')),row);assert.equal(s.draft().fields.manualReview.choices[0].mode,'KEEP');assert.deepEqual(s.draft().fields.manualReview.applied,[]);assert.equal(s.state.saveWrites.length,0);
});

test('character input, selection, detail and scroll survive unrelated view updates and real draft ACKs',async t=>{
 const s=await reasonReady(t),reason=s.label('人工修改决定理由'),row=rowOf(reason),panel=ancestor(reason,'section'),detail=row.children.find(n=>n.type==='details');reason.focus();row.scrollTop=31;panel.scrollTop=244;detail.open=true;
 let text='';for(const char of Array.from('原创人工决定🙂逐字保留')){text+=char;userInput(reason,text);identicalInput(s,'人工修改决定理由',reason,row);assert.equal(reason.selectionStart,text.length);assert.equal(s.draft().fields.manualReview.choices[0].reason,text);}
 reason.setSelectionRange(2,5);s.draft().set('view','scroll',{x:0,y:244});identicalInput(s,'人工修改决定理由',reason,row);assert.deepEqual([reason.selectionStart,reason.selectionEnd],[2,5]);
 await s.flush();identicalInput(s,'人工修改决定理由',reason,row);assert.deepEqual([reason.selectionStart,reason.selectionEnd],[2,5]);assert.equal(row.scrollTop,31);assert.equal(panel.scrollTop,244);assert.equal(detail.open,true);assert.equal(s.draft().status,'saved');
 const stored=[...s.state.drafts.values()].find(v=>v.key.object_id==='proposal_B');assert.equal(stored.fields.manualReview.choices[0].reason,text);assert.equal(s.state.captureWrites.length,1);assert.equal(s.state.saveWrites.length,0);
});

test('rewrite and reason typing remain attached across held ACK, known rejection and original-operation inspection',async t=>{
 const s=await reasonReady(t,'REPLACE'),rewrite=s.label('改写人工修改'),reason=s.label('人工修改决定理由'),row=rowOf(reason);rewrite.focus();let text='';
 for(const char of Array.from('明确改写\n第二行🙂')){text+=char;userInput(rewrite,text);identicalInput(s,'改写人工修改',rewrite,row);assert.equal(s.draft().fields.manualReview.choices[0].text,text);}
 reason.focus();userInput(reason,'先核对');let release;s.state.holdDraftSave=new Promise(done=>release=done);const saving=s.draft().flush();await until(()=>s.draft().status==='saving');userInput(reason,'先核对后追加');reason.setSelectionRange(1,3);s.draft().set('view','scroll',{x:0,y:199});release();await saving;s.state.holdDraftSave=null;await s.flush();identicalInput(s,'人工修改决定理由',reason,row);assert.deepEqual([reason.selectionStart,reason.selectionEnd],[1,3]);assert.equal(reason.value,'先核对后追加');
 s.state.rejectDraft=true;userInput(reason,'明确422保留输入');await s.draft().flush();assert.equal(s.draft().status,'error');identicalInput(s,'人工修改决定理由',reason,row);assert.equal(reason.value,'明确422保留输入');s.state.rejectDraft=false;await s.draft().inspect();assert.equal(s.draft().status,'saved');identicalInput(s,'人工修改决定理由',reason,row);
 s.state.loseDraftReply=true;userInput(reason,'已保存但ACK丢失');await s.draft().flush();assert.equal(s.draft().status,'unknown');const writes=s.state.calls.filter(v=>v.path.endsWith('/workbench-state/save')).length;userInput(reason,'未知期间继续保留的新输入');s.state.loseDraftReply=false;await s.draft().inspect();assert.equal(s.state.calls.filter(v=>v.path.endsWith('/workbench-state/save')).length,writes,'inspection must only GET the original operation');identicalInput(s,'人工修改决定理由',reason,row);assert.equal(reason.value,'未知期间继续保留的新输入');await s.flush();assert.equal(s.draft().status,'saved');identicalInput(s,'人工修改决定理由',reason,row);assert.equal(s.state.captureWrites.length,1);assert.equal(s.state.saveWrites.length,0);
});

test('new immutable capture resets row identity and choices while stale locks and unmatched node placement stay real',async t=>{
 const s=await reasonReady(t),oldReason=s.label('人工修改决定理由'),oldRow=rowOf(oldReason);oldReason.focus();userInput(oldReason,'旧片段决定');
 s.state.capture=copy(s.state.capture);s.state.capture.current.status='STALE';s.state.capture.current.pending=[{path:s.state.gate.files[0].path,reason:'SAVED_BYTES_CHANGED'}];click(s.button('核对保存稿状态'));await until(()=>!!s.button('返回交付检查'));assert.equal(s.button('采用').props.disabled,true);assert.equal(rowOf(s.label('人工修改决定理由')),oldRow);
 s.state.gate.files[0].edited_sha256='8'.repeat(64);click(s.button('返回交付检查'));await until(()=>!!s.button('核对最新保存稿'));
 const fresh=copy(s.state.capture);fresh.snapshot.files[0].file_ref.edited_sha256='8'.repeat(64);fresh.snapshot.files[0].edited_source_ref.sha256='8'.repeat(64);fresh.snapshot.files[0].hunks[0].edited_sha256='8'.repeat(64);fresh.snapshot.files[0].hunks[0].sha256='9'.repeat(64);fresh.snapshot.files[0].hunks[0].added_text='- NEW_ACTUAL_C_2718\n';fresh.snapshot.files[0].hunks[0].keep_text='NEW_ACTUAL_C_2718';fresh.capture_ref={id:'7'.repeat(64),revision:1,sha256:digest(fresh.snapshot)};fresh.current={status:'APPLICABLE',pending:[],target_proposal_ref:fresh.snapshot.target_proposal_ref};s.state.capture=fresh;
 click(s.button('核对最新保存稿'));await until(()=>s.draft().fields.manualCapture?.capture_ref?.id===fresh.capture_ref.id);await settle();click(s.button('核对节点 {value0}',{value0:'C'}));await settle();const newRow=rowOf(s.button('采用'));assert.notEqual(newRow,oldRow,'new immutable capture cannot reuse stale row contents');assert.equal(oldRow.isConnected,false);assert.equal(s.draft().fields.manualReview.choices[0].mode,'');assert.equal(s.draft().fields.manualReview.choices[0].reason,'');assert.ok(s.text().includes('NEW_ACTUAL_C_2718'));
 click(s.button('删除此节点'));await settle();assert.ok(!s.draft().fields.candidate.nodes.some(n=>n.id==='C'));click(s.button('核对未匹配修改'));await settle();assert.ok(s.label('此节点的人工修改'));assert.equal(s.draft().fields.manualReview.applied.length,0);assert.equal(s.state.saveWrites.length,0);
});
