"""AUTHORED_NOT_RUN: actual typed client/controller with controlled HTTP/Storage.

No DOM, App, original model/SDK, Windows, disk flush or restore qualification.
"""
import json,subprocess,hashlib
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
FIXTURE=r'''
import {webcrypto} from 'node:crypto';
import {configurationHash as hash} from '__SRC__/conversation-configuration.ts';
import {ResourceAllowanceApi,allowanceOwner,allowanceWork,allowanceDraftLimits,validateAllowanceObservation} from '__SRC__/resource-allowance.ts';
import {AllowanceJournal,allowanceLocalKey} from '__SRC__/resource-allowance-journal.ts';
if(!globalThis.crypto)Object.defineProperty(globalThis,'crypto',{value:webcrypto});
const H='a'.repeat(64),session={project_id:'project',session_ref:{id:'e'.repeat(32),revision:1,sha256:H},generation:1},owner=allowanceOwner(session,'original',H),base={source_bytes:536870912,source_reads:8192,model_attempts:3,retained_output_bytes:134217728};
const draft={generation:1,basis:{id:H,revision:0,sha256:H},base_limits:base,fields:{source_mib:'512',source_reads:'8192',model_attempts:'4',retention_mib:'128',note:'保留😀'}};
async function packet(id,work='original'){const request={operation_id:id,session_ref:session.session_ref,generation:1,work_operation_id:work,expected_resource_ref:draft.basis,limits:{...base,model_attempts:4},note:'保留😀'};return {request,request_sha256:await hash(request)};}
function receipt(p,lookup=false){return {kind:'ORIGINAL_WORK_RESOURCE_ALLOWANCE_OPERATION',project_id:'project',session_ref:session.session_ref,work_operation_id:'original',resource_id:H,operation_id:p.request.operation_id,status:'APPLIED',reason:null,request_sha256:p.request_sha256,adjustment_ref:{id:'b'.repeat(64),revision:1,sha256:'c'.repeat(64)},recorded_revision:1,applied_revision:1,previous_limits:base,limits:p.request.limits,requested_limits:p.request.limits,current_resource_ref:{id:H,revision:1,sha256:'d'.repeat(64)},usage_refunded:false,unknown_reservations_released:false,model_or_tool_started:false,...lookup?{observed_generation:2,read_only:true,metadata_writes:0,model_calls:0}:{generation:1}};}
function unseen(p){return {kind:'ORIGINAL_WORK_RESOURCE_ALLOWANCE_OPERATION',project_id:'project',session_ref:session.session_ref,work_operation_id:'original',resource_id:H,operation_id:p.request.operation_id,status:'NOT_OBSERVED',reason:'ABSENCE_IS_NOT_PROOF_OF_NOT_APPLIED',automatic_replay_allowed:false,observed_generation:2,read_only:true,metadata_writes:0,model_calls:0};}
function page(p,observation=receipt(p,true)){return {kind:'ORIGINAL_WORK_ALLOWANCE_PACKET_PAGE',project_id:'project',session_ref:session.session_ref,resource_id:H,work_operation_id:'original',packets:[{packet_ref:{id:'f'.repeat(64),revision:1,sha256:H},request:p.request,request_sha256:p.request_sha256,observation}],legacy_operations_without_packet:0,capacity:32,read_only:true,metadata_writes:0,model_calls:0,presence_is_application_proof:false,automatic_replay_allowed:false};}
function check(v,message='allowance requirement failed'){if(!v)throw Error(message);}async function rejects(run){let failed=false;try{await run();}catch{failed=true;}check(failed);}
class Storage{values=new Map();failBefore=false;onceObservedAck=false;get length(){return this.values.size;}key(i){return [...this.values.keys()][i]??null;}getItem(k){return this.values.get(k)??null;}setItem(k,v){if(this.failBefore)throw Error('UNIT storage full');this.values.set(k,v);if(this.onceObservedAck&&JSON.parse(v).attempts.some(a=>a.phase==='OBSERVED')){this.onceObservedAck=false;throw Error('UNIT saved local ACK lost');}}}
const locks={request:async(_key,_options,run)=>run()};
async function client(){
 const p=await packet('typed'),calls=[];check(p.request_sha256==='__PYTHON_SHA__');let value=receipt(p);const reader=new ResourceAllowanceApi({projectPath:p=>'/api/projects/'+p,request:async(...args)=>{calls.push(args);return structuredClone(value);}});
 check((await reader.dispatch(owner,p)).status==='APPLIED');check(calls.length===1&&calls[0][1]==='POST'&&calls[0][4]===false&&calls[0][2].operation_id==='typed');
 for(const patch of [{project_id:'foreign'},{session_ref:{...session.session_ref,sha256:'f'.repeat(64)}},{resource_id:'f'.repeat(64)},{operation_id:'another'},{request_sha256:'f'.repeat(64)},{generation:2},{usage_refunded:true},{unknown_reservations_released:true},{model_or_tool_started:true},{limits:{...base,model_attempts:9}},{applied_revision:2}]){value={...receipt(p),...patch};await rejects(()=>reader.dispatch(owner,p));}
 const bad=await packet('no_send','foreign');const before=calls.length;await rejects(()=>reader.dispatch(owner,bad));check(calls.length===before);
 value=receipt(p,true);check((await reader.inspect(owner,p)).observed_generation===2);check(calls.at(-1)[1]==='GET');
 value=unseen(p);check((await reader.inspect(owner,p)).status==='NOT_OBSERVED');
 value=page(p);check((await reader.history(owner)).packets.length===1);
 const alias=await packet('followup_old','followup');value=page(alias);check((await reader.history(owner)).packets[0].request.work_operation_id==='followup');
 for(const patch of [{capacity:33},{automatic_replay_allowed:true},{legacy_operations_without_packet:-1},{presence_is_application_proof:true}]){value={...page(p),...patch};await rejects(()=>reader.history(owner));}
 check(allowanceWork({...session,active_operation_id:'followup'},{operation_id:'followup',usage:{authority:{kind:'ORIGINAL_WORK',session_ref:session.session_ref,root_operation_id:'original'}}})==='original');
 check(allowanceDraftLimits({...draft.fields,source_mib:' 0.5 ',source_reads:'0002'}).source_bytes===524288);
 check(allowanceDraftLimits({...draft.fields,model_attempts:'281474976710655'}).model_attempts===281474976710655);
 for(const raw of ['NaN','0','-1','140737488355327.999','281474976710656','1e99'])await rejects(async()=>allowanceDraftLimits({...draft.fields,model_attempts:raw}));
 console.log(JSON.stringify({status:'UNIT_PASS',one_POST_no401Replay:true,wrong_owner_and_alias_dispatch_refused:true,original_work_canonical:true,strict_decimal_no_rounding:true}));
}
async function journals(){
 let sends=0;
 // A failed pre-send write cannot transmit. Raw invalid fields stay recoverable.
 const storage=new Storage(),j=new AllowanceJournal(owner,storage,locks);await j.select(draft);j.edit({...draft,fields:{...draft.fields,model_attempts:'NaN'}});await j.flush();check(j.value().draft.fields.model_attempts==='NaN');j.edit(draft);await j.flush();storage.failBefore=true;
 await rejects(async()=>j.submit(await packet('prewrite'),draft,()=>true,{dispatch:async()=>{sends++;throw Error('must not run');}}));check(sends===0&&j.unsaved());storage.failBefore=false;await j.flush();
 const p=await packet('unknown'),reader={dispatch:async(o,p)=>{sends++;const saved=JSON.parse(storage.getItem(allowanceLocalKey(o)));check(saved.attempts.find(a=>a.packet.request.operation_id===p.request.operation_id).phase==='PENDING');throw Error('UNIT original HTTP ACK lost');},inspect:async(o,p)=>receipt(p,true)};
 await j.submit(p,draft,()=>true,reader);check(sends===1&&j.value().attempts.find(a=>a.packet.request.operation_id==='unknown').phase==='UNKNOWN');
 const reopened=new AllowanceJournal(owner,storage,locks);await reopened.inspect('unknown',reader);check(sends===1&&reopened.value().attempts.find(a=>a.packet.request.operation_id==='unknown').observation.status==='APPLIED');
 // Received business ACK survives local save-ACK failure and a metadata-only repair.
 const ackStore=new Storage(),ack=new AllowanceJournal(owner,ackStore,locks);await ack.select(draft);ackStore.onceObservedAck=true;const known=await packet('known');
 await rejects(()=>ack.submit(known,draft,()=>true,{dispatch:async()=>{sends++;return receipt(known);}}));check(ack.unsaved()&&ack.value().attempts[0].observation.status==='APPLIED');await ack.flush();check(!ack.unsaved()&&sends===2);
 // New input while an old business request is in flight is not cleared by its ACK.
 const liveStore=new Storage(),live=new AllowanceJournal(owner,liveStore,locks);await live.select(draft);let release,started;const began=new Promise(r=>started=r),wait=new Promise(r=>release=r),flight=await packet('in_flight');
 const pending=live.submit(flight,draft,()=>true,{dispatch:async()=>{sends++;started();await wait;return receipt(flight);}});await began;live.edit({...draft,fields:{...draft.fields,note:'new manual input'}});release();await pending;check(live.value().draft.fields.note==='new manual input');
 // Server packets recover a lost browser journal without resending even an unobserved operation.
 const lostStore=new Storage(),lost=new AllowanceJournal(owner,lostStore,locks),server=await packet('server_only');await lost.importHistory(page(server,unseen(server)));check(lost.value().attempts[0].phase==='UNKNOWN'&&lost.value().attempts[0].transmissions===1);await lost.inspect('server_only',{inspect:async()=>receipt(server,true)});check(sends===3&&lost.value().attempts[0].phase==='OBSERVED');
 // Conflicting windows retain both drafts and can merge without a business write.
 const shared=new Storage(),one=new AllowanceJournal(owner,shared,locks);await one.select(draft);const two=new AllowanceJournal(owner,shared,locks);two.edit({...draft,fields:{...draft.fields,note:'other window'}});await two.flush();one.edit({...draft,fields:{...draft.fields,note:'this window'}});await rejects(()=>one.flush());check(one.value().draft.fields.note==='this window'&&JSON.parse(shared.getItem(one.key)).draft.fields.note==='other window');await one.mergeExternal();check(one.value().copies.some(d=>d.fields.note==='other window')&&one.value().draft.fields.note==='this window'&&sends===3);
 console.log(JSON.stringify({status:'UNIT_PASS',prewriteFailure_zeroPOST:true,unknown_only_original_GET:true,ACK_and_new_input_retained:true,server_packet_recovery_no_replay:true,cross_window_merge_no_loss:true}));
}
(process.argv[2]==='client'?client():journals()).catch(e=>{console.error(e);process.exitCode=1;});
'''


@pytest.mark.parametrize('mode',['client','journal'])
def test_actual_allowance_client_and_journal_metadata_boundaries(tmp_path,mode):
    reference={'operation_id':'typed','session_ref':{'id':'e'*32,'revision':1,'sha256':'a'*64},'generation':1,'work_operation_id':'original','expected_resource_ref':{'id':'a'*64,'revision':0,'sha256':'a'*64},'limits':{'source_bytes':536870912,'source_reads':8192,'model_attempts':4,'retained_output_bytes':134217728},'note':'保留😀'}
    sha=hashlib.sha256(json.dumps(reference,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    entry=tmp_path/'allowance.ts';bundle=tmp_path/'allowance.cjs';entry.write_text(FIXTURE.replace('__SRC__',str(ROOT/'apps/agent-shell/src')).replace('__PYTHON_SHA__',sha))
    node=ROOT/'.node-tools/node_modules/node/bin/node';builder=tmp_path/'build.cjs'
    builder.write_text('require('+json.dumps(str(ROOT/'apps/agent-shell/node_modules/esbuild'))+').buildSync({entryPoints:['+json.dumps(str(entry))+'],bundle:true,platform:"node",format:"cjs",outfile:'+json.dumps(str(bundle))+'});')
    built=subprocess.run([str(node),str(builder)],cwd=ROOT,capture_output=True,text=True,timeout=60);assert built.returncode==0,built.stderr
    result=subprocess.run([str(node),str(bundle),mode],cwd=ROOT,capture_output=True,text=True,timeout=30);assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['status']=='UNIT_PASS'
