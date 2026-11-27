"""AUTHORED_NOT_RUN: retained local rebuild; controlled Storage/HTTP only.

Real client/controller modules, no DOM/native/fs durability or business replay.
"""
import json,subprocess
from pathlib import Path
import pytest
from test_resource_allowance_client_journal import FIXTURE as BASE
ROOT=Path(__file__).resolve().parents[1]
RECOVERY=r'''
async function recover(){
 const s=new Storage(),key=allowanceLocalKey(owner),raw='broken JSON \ud800\0 手工 😀';s.values.set(key,raw);const before=[...s.values];
 const health=await inspectAllowanceLocal(s,owner);check(health.status==='INVALID'&&health.read_only&&s.values.get(key)===raw&&s.values.size===before.length);
 const p=await packet('from_server'),result=await rebuildAllowanceLocal(s,owner,health,page(p),draft,locks);check(result.business_requests_sent===0&&result.retained.length===1&&result.journal.value().attempts[0].observation.status==='APPLIED');
 const saved=JSON.parse(s.getItem(result.retained[0].key));check(saved.raw===raw&&saved.raw_encoding==='CANONICAL_JSON_STRING_UTF16'&&saved.raw_sha256===await hash(raw));check((await inspectAllowanceLocal(s,owner)).status==='VALID');
 // A corrupt packet's asserted APPLIED flag is not promoted. Good fields are reused from a fresh basis.
 const badStore=new Storage(),valid=new AllowanceJournal(owner,badStore,locks);await valid.select({...draft,fields:{...draft.fields,note:'未提交人工输入'}});const bad=JSON.parse(badStore.getItem(valid.key)),forged=await packet('bad_witness');bad.attempts=[{packet:{...forged,request_sha256:'f'.repeat(64)},phase:'OBSERVED',transmissions:1,initial_unknown:false,observation:receipt(forged),last_lookup:'FOUND',draft_snapshot:null}];const badRaw=JSON.stringify(bad);badStore.values.set(valid.key,badRaw);
 const h=await inspectAllowanceLocal(badStore,owner),proof=page(await packet('actual_original')),rebuilt=await rebuildAllowanceLocal(badStore,owner,h,proof,draft,locks);
 check(rebuilt.journal.value().draft.fields.note==='未提交人工输入'&&rebuilt.journal.value().attempts.length===1&&rebuilt.journal.value().attempts[0].packet.request.operation_id==='actual_original');check(JSON.parse(badStore.getItem(rebuilt.retained[0].key)).raw===badRaw);
 // Well-formed original packets salvaged from corrupt envelopes stay UNKNOWN if the server has not confirmed them.
 const pendingStore=new Storage(),u=await packet('uncertain_old'),corrupt={version:999,owner,revision:1,draft:null,copies:[],attempts:[{packet:u,phase:'OBSERVED',observation:receipt(u)}]};pendingStore.values.set(key,JSON.stringify(corrupt));const pendingHealth=await inspectAllowanceLocal(pendingStore,owner),empty={...page(u),packets:[]};const pending=await rebuildAllowanceLocal(pendingStore,owner,pendingHealth,empty,draft,locks);
 check(pending.journal.value().attempts[0].phase==='UNKNOWN'&&pending.journal.value().attempts[0].transmissions===1&&pending.journal.value().attempts[0].observation===null);
 console.log(JSON.stringify({status:'UNIT_PASS',old_UTF16_storage_retained_exactly:true,raw_false_receipt_not_promoted:true,source_unknown_never_resent:true}));
}
async function guards(){
 const p=await packet('guard'),key=allowanceLocalKey(owner);
 // No overwrite if the immutable old-value copy fails.
 const failed=new Storage();failed.values.set(key,'unreadable original');const h=await inspectAllowanceLocal(failed,owner),set=failed.setItem.bind(failed);failed.setItem=(k,v)=>{if(k.startsWith('eeas-resource-allowance-retained-v1:'))throw Error('UNIT retained copy not saved');set(k,v);};await rejects(()=>rebuildAllowanceLocal(failed,owner,h,page(p),draft,locks));check(failed.getItem(key)==='unreadable original');
 // Readback repairs a local write-ACK loss without repeating a business action.
 const ack=new Storage();ack.values.set(key,'old unique data');const a=await inspectAllowanceLocal(ack,owner),write=ack.setItem.bind(ack);ack.setItem=(k,v)=>{write(k,v);throw Error('UNIT saved local ACK lost');};const acked=await rebuildAllowanceLocal(ack,owner,a,page(p),draft,locks);check(acked.business_requests_sent===0&&acked.journal.value().attempts[0].phase==='OBSERVED');
 // Another window changing the original while it is being retained is never overwritten.
 const racing=new Storage();racing.values.set(key,'first original');const r=await inspectAllowanceLocal(racing,owner),put=racing.setItem.bind(racing);let raced=false;racing.setItem=(k,v)=>{put(k,v);if(!raced&&k.startsWith('eeas-resource-allowance-retained-v1:')){raced=true;racing.values.set(key,'other window input');}};await rejects(()=>rebuildAllowanceLocal(racing,owner,r,page(p),draft,locks));check(racing.getItem(key)==='other window input');
 // Even a forged INVALID descriptor cannot rebuild healthy storage.
 const healthy=new Storage(),j=await allowanceJournal(healthy,owner,locks);await j.select(draft);const before=healthy.getItem(key),healthyInfo=await inspectAllowanceLocal(healthy,owner);await rejects(()=>rebuildAllowanceLocal(healthy,owner,{...healthyInfo,status:'INVALID'},page(p),draft,locks));check(healthy.getItem(key)===before);await j.flush();
 // Foreign proof or capacity exhaustion cannot silently discard old copies.
 const bound=new Storage();bound.values.set(key,'bounded original');const b=await inspectAllowanceLocal(bound,owner),ownerHash=await hash(owner);for(let n=0;n<4;n++)bound.values.set('eeas-resource-allowance-retained-v1:'+ownerHash+':old'+n,'retained '+n);const original=[...bound.values];await rejects(()=>rebuildAllowanceLocal(bound,owner,b,page(p),draft,locks));check(JSON.stringify([...bound.values])===JSON.stringify(original));await rejects(()=>rebuildAllowanceLocal(bound,owner,b,{...page(p),project_id:'foreign'},draft,locks));check(bound.getItem(key)==='bounded original');
 console.log(JSON.stringify({status:'UNIT_PASS',failed_copy_never_overwrites:true,local_ACK_readback_no_business_repeat:true,racing_window_input_preserved:true,healthy_or_foreign_rebuild_refused:true,capacity_retains_old_copies:true}));
}
async function memory(){
 const storage=new Storage(),j=await allowanceJournal(storage,owner,locks),key=j.key;await j.select({...draft,fields:{...draft.fields,note:'memory original'}});j.edit({...draft,fields:{...draft.fields,note:'latest unsaved memory'}});storage.values.set(key,'damaged disk journal');const h=await inspectAllowanceLocal(storage,owner);check(j.unsaved()&&h.status==='INVALID');const p=await packet('known_after_loss'),result=await rebuildAllowanceLocal(storage,owner,h,page(p),draft,locks);check(result.journal===j&&j.value().draft.fields.note==='latest unsaved memory'&&!j.unsaved());const originals=result.retained.map(v=>JSON.parse(storage.getItem(v.key)).raw);check(originals.includes('damaged disk journal')&&originals.some(v=>v.includes('latest unsaved memory')));
 // A positive request still in flight cannot be retired by rebuilding its local index.
 const live=new Storage(),running=await allowanceJournal(live,owner,locks);await running.select(draft);let began,release,sends=0;const started=new Promise(r=>began=r),wait=new Promise(r=>release=r),sent=await packet('live_original');const flight=running.submit(sent,draft,()=>true,{dispatch:async()=>{sends++;began();await wait;return receipt(sent);}});await started;live.values.set(key,'corrupt while request is live');const health=await inspectAllowanceLocal(live,owner);await rejects(()=>rebuildAllowanceLocal(live,owner,health,page(sent),draft,locks));check(live.getItem(key)==='corrupt while request is live'&&sends===1);release();await rejects(()=>flight);check(running.value().attempts[0].observation.status==='APPLIED');const later=await inspectAllowanceLocal(live,owner),fixed=await rebuildAllowanceLocal(live,owner,later,page(sent),draft,locks);check(fixed.journal===running&&sends===1&&running.value().attempts[0].phase==='OBSERVED');
 console.log(JSON.stringify({status:'UNIT_PASS',unique_memory_input_retained:true,cache_instance_rebound_after_readback:true,inflight_business_not_restarted:true,known_ACK_preserved:true}));
}
({rebuild:recover,guards,memory}[process.argv[2]]??recover)().catch(e=>{console.error(e);process.exitCode=1;});
'''


def fixture_source():
    common=BASE[:BASE.index('(process.argv[2]')]
    common=common.replace('AllowanceJournal,allowanceLocalKey}','AllowanceJournal,allowanceLocalKey,allowanceJournal,inspectAllowanceLocal,rebuildAllowanceLocal}')
    return common+RECOVERY


@pytest.mark.parametrize('mode',['rebuild','guards','memory'])
def test_real_allowance_retained_rebuild_preserves_data_and_never_replays_business(tmp_path,mode):
    entry=tmp_path/'rebuild.ts';bundle=tmp_path/'rebuild.cjs';entry.write_text(fixture_source().replace('__SRC__',str(ROOT/'apps/agent-shell/src')))
    node=ROOT/'.node-tools/node_modules/node/bin/node';builder=tmp_path/'build.cjs'
    builder.write_text('require('+json.dumps(str(ROOT/'apps/agent-shell/node_modules/esbuild'))+').buildSync({entryPoints:['+json.dumps(str(entry))+'],bundle:true,platform:"node",format:"cjs",outfile:'+json.dumps(str(bundle))+'});')
    built=subprocess.run([str(node),str(builder)],cwd=ROOT,capture_output=True,text=True,timeout=60);assert built.returncode==0,built.stderr
    result=subprocess.run([str(node),str(bundle),mode],cwd=ROOT,capture_output=True,text=True,timeout=30);assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['status']=='UNIT_PASS'
