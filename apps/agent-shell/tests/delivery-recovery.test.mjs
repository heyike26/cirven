import test from 'node:test';
import assert from 'node:assert/strict';
import { ApiError } from '../src/api.ts';
import { DeliveryHistoryRecovery, deliveryFeedItem, deliveryTarget, loadedDeliveryTargets, selectedDeliverySession, upsertDeliveryFeed, verifyDeliveryTarget } from '../src/delivery-recovery.ts';
import { saveDeliveryHistory } from '../src/planning-delivery.ts';
import { chatDeliveryFiles } from '../src/chat-artifacts.ts';

const basis={id:'source-session',revision:4,sha256:'a'.repeat(64)},target={projectId:'p',kind:'STAGE',basis};
const source={kind:'local_file',project_id:'p',workspace_id:'w',binding_revision:2,binding_sha256:'b'.repeat(64),path:'delivery/真实说明.docx',revision:1,sha256:'c'.repeat(64)};
const receipt=(key='original-op',changes={})=>({operation_id:key,status:'STAGE_REPORT',session_ref:basis,pending_count:1,blocking_count:1,report:{artifact:{id:'word',sha256:source.sha256,filename:'真实说明.docx'},pending:{download_url:'/pending'}},diagrams:[],artifacts:[],project_files:[{path:source.path,sha256:source.sha256,file_source_ref:source}],file_checks:[{path:source.path,status:'CURRENT'}],basis_current:true,current_review_status:'CURRENT',execution_authorized:false,historical_receipt_preserved:true,...changes});
const entity=(id='source-session',pid='p',discovery)=>({id,project_id:pid,revision:4,sha256:basis.sha256,payload:{discovery_ref:discovery?{...basis,id:discovery}:null}});
const artifact=(data=entity(),type='discovery')=>({artifactId:data.id,type,schemaVersion:1,revision:data.revision,contentHash:data.sha256,scope:{projectId:data.project_id},data});
function storage(){const values=new Map();return{getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};}
function deferred(){let resolve;const promise=new Promise(done=>resolve=done);return{promise,resolve};}

test('recovery scopes come from actual loaded session/proposals; historical receipt versions remain original and foreign scopes never create success cards',()=>{
  assert.deepEqual(deliveryTarget(artifact()),target);assert.throws(()=>deliveryTarget({...artifact(),scope:{projectId:'other'}}),/身份不一致/);assert.throws(()=>deliveryTarget({...artifact(),contentHash:'f'.repeat(64)}),/身份不一致/);
  const proposals=[entity('plan-a','p','source-session'),entity('plan-b','p','another-session'),entity('foreign','other','source-session')];
  assert.deepEqual(loadedDeliveryTargets('p',entity(),proposals).map(item=>item.basis.id),['source-session','plan-a']);assert.deepEqual(loadedDeliveryTargets('p',undefined,proposals),[]);assert.deepEqual(loadedDeliveryTargets('p',undefined,proposals,artifact(proposals[1],'proposal')).map(item=>item.basis.id),['plan-b']);
  const newer={...target,basis:{...basis,revision:5,sha256:'d'.repeat(64)}},original=receipt();const item=deliveryFeedItem(newer,original);assert.equal(item.artifact.data,original);assert.deepEqual(item.artifact.data.session_ref,basis);assert.match(item.body,/v4.*当前选中 v5.*历史来源保留/);assert.equal(item.artifact.revision,4);assert.deepEqual(item.artifact.scope,{projectId:'p'});
  assert.throws(()=>verifyDeliveryTarget(receipt('original-op',{session_ref:{...basis,id:'another-session'}}),target,'original-op'),/另一项目或来源/);assert.throws(()=>verifyDeliveryTarget(receipt('original-op',{project_files:[{path:source.path,sha256:source.sha256,file_source_ref:{...source,project_id:'other'}}]}),target,'original-op'),/另一项目或来源/);assert.throws(()=>verifyDeliveryTarget(receipt(),{...target,kind:'EXPORT'},'original-op'),/另一项目或来源/);
});

test('restart recovery and explicit reads upsert one actual card per original operation and never call generation or export',async()=>{
  const local=storage();saveDeliveryHistory(local,'p','STAGE',basis.id,'original-op');saveDeliveryHistory(local,'other','STAGE',basis.id,'not-this-project');let items=[],reads=0,writes=0;
  const client={operation:async(pid,key)=>{reads++;assert.equal(pid,'p');assert.equal(key,'original-op');return receipt(key);},stage:()=>writes++,export:()=>writes++},recovery=new DeliveryHistoryRecovery(client,local,item=>items=upsertDeliveryFeed(items,item));
  await recovery.restore([target]);await recovery.restore([target]);assert.equal(reads,1);assert.equal(items.length,1);assert.equal(chatDeliveryFiles(items[0].artifact.data).all.length,1);
  await recovery.read(target,'original-op');await recovery.read(target,'original-op');assert.equal(reads,3);assert.equal(items.length,1);assert.equal(items[0].artifact.artifactId,'original-op');assert.equal(writes,0);assert.deepEqual(JSON.parse(local.getItem('eeas-delivery-history:p:STAGE:source-session')),['original-op']);recovery.stop();
});
test('confirmed-plan export recovery retains the original proposal and all harness sources rather than generating a replacement',async()=>{
  const proposal=entity('plan-a','p','source-session'),exportTarget=deliveryTarget(artifact(proposal,'proposal')),local=storage();saveDeliveryHistory(local,'p','EXPORT',proposal.id,'export-old');
  const result={...receipt('export-old'),status:'EXPORTED_CONFIRMED_PLAN_DERIVATIVES',proposal_ref:{...basis,id:proposal.id},version_directory:'delivery/plan-v4',harness:{workspace_ref:{id:'w',revision:2,binding_sha256:source.binding_sha256},version_directory:'delivery/plan-v4',files:[{path:source.path,sha256:source.sha256,file_source_ref:source}]},manifest:{path:'delivery/manifest.json',sha256:'e'.repeat(64)},taskbook_ref:{taskbook_id:'derived-draft',revision:1},current_review_status:'SOURCE_OR_VERSION_CHANGED',basis_current:false};delete result.session_ref;
  const received=[],recovery=new DeliveryHistoryRecovery({operation:async()=>result},local,item=>received.push(item));await recovery.restore([{...exportTarget,basis:{...exportTarget.basis,revision:5,sha256:'f'.repeat(64)}}]);assert.equal(received.length,1);assert.equal(received[0].artifact.data,result);assert.deepEqual(received[0].artifact.data.proposal_ref,{...basis,id:proposal.id});assert.match(received[0].body,/提案 plan-a.*v4.*历史来源保留.*SOURCE_OR_VERSION_CHANGED/);assert.equal(chatDeliveryFiles(received[0].artifact.data).all.length,1);assert.deepEqual(chatDeliveryFiles(received[0].artifact.data).all[0].source,source);recovery.stop();
});

test('late project/session replies and older observations cannot overwrite a newer scoped receipt',async()=>{
  const local=storage();saveDeliveryHistory(local,'p','STAGE',basis.id,'old');const waiting=deferred(),received=[];const recovery=new DeliveryHistoryRecovery({operation:async()=>waiting.promise},local,item=>received.push(item));
  const first=recovery.restore([target]);await recovery.restore([{...target,projectId:'other'}]);waiting.resolve(receipt('old'));await first;assert.equal(received.length,0);
  const secondWait=deferred(),other=[];const two=new DeliveryHistoryRecovery({operation:async()=>secondWait.promise},local,item=>other.push(item));const oldSession=two.restore([target]);await two.restore([{...target,basis:{...basis,id:'new-session'}}]);secondWait.resolve(receipt('old'));await oldSession;assert.equal(other.length,0);
  const slow=deferred();let count=0,live=[];const latest=new DeliveryHistoryRecovery({operation:async()=>++count===1?slow.promise:receipt('same',{current_review_status:'CHANGED_OUTPUTS'})},local,item=>live=upsertDeliveryFeed(live,item));const earlier=latest.read(target,'same');await latest.read(target,'same');slow.resolve(receipt('same'));await assert.rejects(earlier,/更新的原回执/);assert.equal(live.length,1);assert.equal(live[0].artifact.data.current_review_status,'CHANGED_OUTPUTS');
  let current=true;const ended=deferred(),abandoned=[];const manual=new DeliveryHistoryRecovery({operation:async()=>ended.promise},local,item=>abandoned.push(item));const read=manual.read(target,'manual',()=>current);current=false;ended.resolve(receipt('manual'));await assert.rejects(read,/已切换/);assert.equal(abandoned.length,0);latest.stop();two.stop();manual.stop();recovery.stop();
});

test('failed, interrupted, missing and malformed receipts keep reviewable operation indexes without success file actions or replay',async()=>{
  const local=storage(),received=[];for(const key of ['partial','interrupted','processing','missing','invalid'])saveDeliveryHistory(local,'p','STAGE',basis.id,key);
  const values={partial:{project_id:'p',session_ref:basis,operation_id:'partial',status:'FAILED_OR_PARTIAL',kind:'STAGE',steps:{report:{saved:true}},failures:['write conflict'],automatic_model_replay:false},interrupted:{project_id:'p',session_ref:basis,operation_id:'interrupted',status:'INTERRUPTED_REVIEW_REQUIRED',kind:'STAGE',automatic_model_replay:false},processing:{project_id:'p',session_ref:basis,operation_id:'processing',status:'PROCESSING',kind:'STAGE',automatic_model_replay:false},invalid:receipt('invalid',{report:{}})};
  const recovery=new DeliveryHistoryRecovery({operation:async(pid,key)=>{if(key==='missing')throw new ApiError(404,'NOT_FOUND','original receipt absent');return values[key];}},local,item=>received.push(item));await recovery.restore([target]);
  const byId=new Map(received.map(item=>[item.id.split(':').at(-1),item]));assert.equal(byId.get('missing').status,'NO_RECORD');assert.match(byId.get('missing').body,/没有记录不证明未生成/);assert.equal(byId.get('invalid').status,'INSPECTION_UNAVAILABLE');
  for(const key of ['partial','interrupted','processing']){const item=byId.get(key);assert.equal(item.title,'交付操作待核对');assert.equal(chatDeliveryFiles(item.artifact.data).all.length,0);assert.match(item.body,/未重新生成/);}
  assert.equal(JSON.parse(local.getItem('eeas-delivery-history:p:STAGE:source-session')).length,5);assert.equal(received.length,5);recovery.stop();
});

test('returning to the same discussion restores a cleared feed without losing or replaying the original delivery',async()=>{
  const local=storage();saveDeliveryHistory(local,'p','STAGE',basis.id,'original-op');let reads=0,items=[];
  const recovery=new DeliveryHistoryRecovery({operation:async()=>{reads++;return receipt();}},local,item=>items=upsertDeliveryFeed(items,item));
  await recovery.restore([target]);assert.equal(items.length,1);items=[];
  recovery.resetView();await recovery.restore([target]);
  assert.equal(items.length,1);assert.equal(reads,2);assert.equal(items[0].artifact.data.operation_id,'original-op');
  assert.deepEqual(JSON.parse(local.getItem('eeas-delivery-history:p:STAGE:source-session')),['original-op']);recovery.stop();
});

test('resetting the view cannot reuse a request ticket and accept a stale manual observation',async()=>{
  const local=storage(),slow=deferred(),received=[];let calls=0;
  const recovery=new DeliveryHistoryRecovery({operation:async()=>++calls===1?slow.promise:receipt('same',{current_review_status:'CHANGED_OUTPUTS'})},local,item=>received.push(item));
  const old=recovery.read(target,'same');recovery.resetView();await recovery.read(target,'same');
  slow.resolve(receipt('same'));await assert.rejects(old,/更新的原回执/);
  assert.equal(received.length,1);assert.equal(received[0].artifact.data.current_review_status,'CHANGED_OUTPUTS');recovery.stop();
});


test('native discussion recovery never borrows the remembered discovery filter; returning to discovery retains its original selection',()=>{
  const session=entity(),foreign=entity('foreign','other'),standalone=entity('standalone'),linked=entity('linked','p',session.id);
  assert.equal(selectedDeliverySession('p',false,session,session.id,[session]),undefined);
  assert.deepEqual(loadedDeliveryTargets('p',selectedDeliverySession('p',false,undefined,session.id,[session]),[standalone,linked]).map(value=>value.basis.id),['standalone']);
  assert.equal(selectedDeliverySession('p',true,undefined,session.id,[session]),session);
  assert.equal(selectedDeliverySession('p',true,foreign,session.id,[foreign,session]),session);
  assert.deepEqual(loadedDeliveryTargets('p',selectedDeliverySession('p',true,undefined,session.id,[session]),[standalone,linked]).map(value=>value.basis.id),[session.id,'linked']);
  assert.equal(selectedDeliverySession('p',true,undefined,'missing',[foreign]),undefined);
});
