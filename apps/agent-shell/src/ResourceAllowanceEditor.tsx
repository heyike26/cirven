import {createEffect,createSignal,onCleanup,Show,For} from 'solid-js';
import {authoredErrorCopy,authoredText,AuthoredUIError,type UIErrorCopy} from './authored-ui-copy';
import {t} from './ui-language';
import {configurationCanonical as canonical,configurationHash as hash} from './conversation-configuration';
import {originalResourceBytes,type AgentSession,type OriginalWorkResourceBudget} from './agent-sessions';
import {allowanceOwner,allowanceWork,allowanceDraft,allowanceDraftLimits,allowanceSame,allowanceCheck,ResourceAllowanceApi,type AllowanceDraft,type AllowancePacket,type AllowancePacketPage,type AllowanceObservation} from './resource-allowance';
import {allowanceJournal,inspectAllowanceLocal,rebuildAllowanceLocal,type AllowanceLocalHealth,type AllowanceAttempt,type AllowanceJournal} from './resource-allowance-journal';
export default function ResourceAllowanceEditor(props:{session:AgentSession;receipt:OriginalWorkResourceBudget;onRefreshed:(v:OriginalWorkResourceBudget)=>void}){
 const reader=new ResourceAllowanceApi(),[opened,setOpened]=createSignal(false),[local,setLocal]=createSignal<ReturnType<AllowanceJournal['value']>>(),[busy,setBusy]=createSignal(false),[error,setError]=createSignal<UIErrorCopy>(''),[notice,setNotice]=createSignal(''),[conflict,setConflict]=createSignal(false),[shown,setShown]=createSignal(8),[legacyCount,setLegacyCount]=createSignal(0),[health,setHealth]=createSignal<AllowanceLocalHealth>(),[localIssue,setLocalIssue]=createSignal(false),[serverPage,setServerPage]=createSignal<AllowancePacketPage>(),[lookups,setLookups]=createSignal<Record<string,AllowanceObservation>>({}),[retainedCount,setRetainedCount]=createSignal(0);
 let journal:AllowanceJournal|undefined,unsubscribe:(()=>void)|undefined,alive=true,epoch=0,seen='',saveTimer:ReturnType<typeof setTimeout>|undefined;
 const identity=()=>canonical([props.session.project_id,props.session.session_ref,allowanceWork(props.session,props.receipt),props.receipt.usage?.resource_ref.id]);
 const current=(key:string,n:number,generation?:number)=>alive&&key===identity()&&n===epoch&&(generation===undefined||props.session.generation===generation);
 const fail=(e:unknown)=>{if(e instanceof Error&&e.message==='RESOURCE_ALLOWANCE_DRAFT_CONFLICT'){setConflict(true);setError(authoredErrorCopy(new AuthoredUIError('其他窗口保存了不同记录；本窗口输入仍保留。')));}else setError(authoredErrorCopy(e));};
 const sync=()=>{if(alive&&journal)setLocal(journal.value());};
 const draft=()=>local()?.draft;
 const owner=()=>allowanceOwner(props.session,allowanceWork(props.session,props.receipt),props.receipt.usage!.resource_ref.id);
 function attach(value:AllowanceJournal,key:string,n:number){unsubscribe?.();journal=value;unsubscribe=value.subscribe(()=>{if(current(key,n))sync();});sync();}
 async function readonlyTask(fn:(key:string,n:number,generation:number)=>Promise<void>){if(busy())return;const key=identity(),n=epoch,generation=props.session.generation;setBusy(true);setError('');setNotice('');try{await fn(key,n,generation);}catch(e){if(current(key,n))fail(e);}finally{if(current(key,n))setBusy(false);}}
 async function readOriginals(){return readonlyTask(async(key,n,generation)=>{
  const scope=owner(),page=await reader.history(scope);if(!current(key,n,generation))return;setServerPage(page);setLegacyCount(page.legacy_operations_without_packet);setNotice(t('已读取原请求与回执；没有重发调整。'));
  try{const state=await inspectAllowanceLocal(localStorage,scope);if(!current(key,n,generation))return;setHealth(state);setLocalIssue(state.status==='INVALID');if(state.status==='INVALID'){setError(authoredErrorCopy(new AuthoredUIError('本机额度日志不可读取；服务端原记录仍可只读核对。')));return;}const j=await ensure(key,n);await j.importHistory(page);if(current(key,n))sync();}catch(e){if(current(key,n)){setLocalIssue(true);fail(e);}}
 });}
 async function inspectOriginal(a:AllowanceAttempt){return readonlyTask(async(key,n,generation)=>{
  const result=await reader.inspect(owner(),a.packet);if(!current(key,n,generation))return;setLookups(v=>({...v,[a.packet.request.operation_id]:result}));
  try{const state=await inspectAllowanceLocal(localStorage,owner());if(!current(key,n,generation))return;setHealth(state);setLocalIssue(state.status==='INVALID');if(state.status!=='INVALID'){const j=await ensure(key,n);await j.acceptLookup(a.packet,result);if(current(key,n))sync();}}catch(e){if(current(key,n)){setLocalIssue(true);fail(e);}}
 });}
 async function rebuild(){return readonlyTask(async(key,n,generation)=>{
  clearTimeout(saveTimer);const scope=owner(),session=structuredClone(props.session),visibleWork=props.receipt.operation_id!,state=await inspectAllowanceLocal(localStorage,scope);if(!current(key,n,generation))return;setHealth(state);
  if(state.status!=='INVALID'){setLocalIssue(false);const j=await ensure(key,n);await j.mergeExternal();if(current(key,n))setNotice(t('本机日志已可读取，原输入保留。'));return;}
  const fresh=await reader.current(session,visibleWork),page=await reader.history(scope);if(!current(key,n,generation))return;allowanceCheck(fresh.status==='AVAILABLE'&&fresh.usage?.resource_ref.id===scope.resource_id);
  const result=await rebuildAllowanceLocal(localStorage,scope,state,page,allowanceDraft(session,fresh));
  const after=await inspectAllowanceLocal(localStorage,scope);if(current(key,n,generation)){attach(result.journal,key,n);setServerPage(page);setLegacyCount(page.legacy_operations_without_packet);setRetainedCount(result.retained.length);setLocalIssue(false);setConflict(false);setError('');setHealth(after);props.onRefreshed(fresh);setNotice(t('旧存储值已完整保留，本机视图已重建；没有重发操作或修改服务端计量。'));}
 });}
 const rows=():AllowanceAttempt[]=>{const result=structuredClone(local()?.attempts??[]);for(const row of serverPage()?.packets??[]){const packet={request:row.request,request_sha256:row.request_sha256},index=result.findIndex(a=>a.packet.request.operation_id===packet.request.operation_id),old=index>=0?result[index]:undefined,known=row.observation.status==='APPLIED'||row.observation.status==='REJECTED';if(old&&!allowanceSame(old.packet,packet))continue;const observation=known?row.observation:old?.observation??null;const value:AllowanceAttempt={packet,phase:observation?'OBSERVED':'UNKNOWN',transmissions:1,initial_unknown:old?.initial_unknown??!known,observation,last_lookup:known?'FOUND':row.observation.status==='UNAVAILABLE'?'UNAVAILABLE':'NOT_OBSERVED',draft_snapshot:old?.draft_snapshot??null};if(index<0)result.push(value);else result[index]=value;}
  return result.map(a=>{const r=lookups()[a.packet.request.operation_id];if(!r)return a;const known=r.status==='APPLIED'||r.status==='REJECTED';return {...a,phase:known||a.observation?'OBSERVED':a.phase,observation:known?r:a.observation,last_lookup:known?'FOUND':r.status==='UNAVAILABLE'?'UNAVAILABLE':'NOT_OBSERVED'};});};

 async function ensure(key:string,n:number){
  allowanceCheck(props.receipt.status==='AVAILABLE'&&props.receipt.operation_id&&props.receipt.usage,'RESOURCE_ALLOWANCE_SCOPE_CHANGED');
  const context=structuredClone(props.session),receipt=structuredClone(props.receipt),o=allowanceOwner(context,allowanceWork(context,receipt),receipt.usage!.resource_ref.id),state=await inspectAllowanceLocal(localStorage,o);
  if(current(key,n)){setHealth(state);setLocalIssue(state.status==='INVALID');}if(state.status==='INVALID')throw new AuthoredUIError('本机额度日志不可读取；服务端原记录仍可只读核对。');
  const value=await allowanceJournal(localStorage,o);
  if(!current(key,n))return value;
  if(journal!==value)attach(value,key,n);
  if(!value.value().draft)await value.select(allowanceDraft(context,receipt));if(current(key,n))sync();return value;
 }
 async function task(fn:(j:AllowanceJournal,key:string,n:number,generation:number)=>Promise<void>){
  if(busy())return;const key=identity(),n=epoch,generation=props.session.generation;setBusy(true);setError('');setNotice('');
  try{const j=await ensure(key,n);if(current(key,n,generation))await fn(j,key,n,generation);}catch(e){if(current(key,n))fail(e);}finally{if(current(key,n)){setBusy(false);sync();}}
 }
 createEffect(()=>{const key=identity();if(key!==seen){seen=key;epoch++;unsubscribe?.();unsubscribe=undefined;journal=undefined;setLocal(undefined);setError('');setNotice('');setConflict(false);setBusy(false);setShown(8);setLegacyCount(0);setHealth(undefined);setLocalIssue(false);setServerPage(undefined);setLookups({});setRetainedCount(0);if(opened())void task(async()=>{});}});
 onCleanup(()=>{alive=false;epoch++;clearTimeout(saveTimer);unsubscribe?.();if(journal?.unsaved())void journal.flush().catch(()=>{});});
 function edit(field:keyof AllowanceDraft['fields'],value:string){if(!journal||!draft())return;const d=structuredClone(draft()!);d.fields[field]=value;journal.edit(d);clearTimeout(saveTimer);const j=journal,key=identity(),n=epoch;saveTimer=setTimeout(()=>{void j.flush().catch(e=>{if(current(key,n))fail(e);});},200);}
 async function refreshBasis(j:AllowanceJournal,key:string,n:number,generation:number,restore?:AllowancePacket){
  const session=structuredClone(props.session),work=props.receipt.operation_id!;await j.flush();const fresh=await reader.current(session,work);if(!current(key,n,generation))return;
  allowanceCheck(fresh.status==='AVAILABLE'&&fresh.usage&&fresh.usage.resource_ref.id===j.owner.resource_id,'RESOURCE_ALLOWANCE_SCOPE_CHANGED');
  const next=allowanceDraft(session,fresh),old=j.value().draft;
  if(restore){const r=restore.request;next.fields={source_mib:String(r.limits.source_bytes/1048576),source_reads:String(r.limits.source_reads),model_attempts:String(r.limits.model_attempts),retention_mib:String(r.limits.retained_output_bytes/1048576),note:r.note};}
  else if(old)next.fields=structuredClone(old.fields);
  await j.select(next);if(current(key,n,generation)){props.onRefreshed(fresh);setNotice(t('已保留输入并更新当前依据；尚未提交调整。'));}
 }
 async function apply(){return task(async(j,key,n,generation)=>{
  const context=structuredClone(props.session),before=j.value().draft;if(!before||before.generation!==generation)throw new AuthoredUIError('连接已变化，请保留输入并核对当前额度。');
  if(Array.from(before.fields.note).length>160)throw new AuthoredUIError('调整备注最多160个字符，原输入保留。');
  let limits;try{limits=allowanceDraftLimits(before.fields);}catch{throw new AuthoredUIError('请输入可精确计量的正数额度；原输入保留。');}
  const fresh=await reader.current(context,props.receipt.operation_id!);
  if(!current(key,n,generation))return;allowanceCheck(fresh.status==='AVAILABLE'&&fresh.usage&&fresh.usage.resource_ref.id===j.owner.resource_id,'RESOURCE_ALLOWANCE_SCOPE_CHANGED');
  props.onRefreshed(fresh);if(!allowanceSame(fresh.usage.limits,before.base_limits))throw new AuthoredUIError('限额已被其他操作修改；请保留输入并核对当前额度。');
  if(limits.source_bytes<fresh.usage.used.source_bytes+fresh.usage.pending_read_bytes||limits.source_reads<fresh.usage.used.source_reads||limits.model_attempts<fresh.usage.used.model_attempts)throw new AuthoredUIError('限额不能低于已消耗或已预留的数量。');
  const request={operation_id:'allowance-'+crypto.randomUUID(),session_ref:structuredClone(context.session_ref),generation,work_operation_id:j.owner.work_operation_id,expected_resource_ref:structuredClone(fresh.usage.resource_ref),limits,note:before.fields.note};
  const packet={request,request_sha256:await hash(request)};const result=await j.submit(packet,before,()=>current(key,n,generation),reader);
  if(!current(key,n,generation))return;
  setNotice(result?.status==='APPLIED'?t('限额已修改，原消耗与未知记录保持。'):result?.status==='REJECTED'?t('此次调整未应用；请核对当前额度后再决定。'):t('调整结果尚未确认；请查原记录，不要重复提交同一请求。'));
  // A subsequent read failure never changes a known APPLIED receipt to UNKNOWN.
  if(result)try{const observed=await reader.current(context,props.receipt.operation_id!);if(current(key,n,generation)){props.onRefreshed(observed);if(result.status==='APPLIED'&&observed.status==='AVAILABLE'&&observed.usage&&allowanceSame(observed.usage.limits,limits)&&allowanceSame(j.value().draft?.fields,before.fields)){const next=allowanceDraft(context,observed);next.fields=structuredClone(before.fields);await j.select(next);}}}catch{if(current(key,n,generation))setNotice(t('原操作结果已核实，资源视图尚未刷新。'));}
 });}
 const unchanged=()=>{try{return !!draft()&&!!props.receipt.usage&&allowanceSame(allowanceDraftLimits(draft()!.fields),props.receipt.usage.limits);}catch{return false;}};
 const status=(phase:string,result?:string)=>result==='APPLIED'?t('已应用'):result==='REJECTED'?t('未应用'):phase==='PREPARED'?t('已准备，尚未发送'):phase==='PENDING'?t('等待原操作回执'):t('结果待核查');
 const fields:[keyof AllowanceDraft['fields'],string,string][]=[['source_mib','资料读取上限（MiB）','decimal'],['source_reads','来源读取次数上限','numeric'],['model_attempts','原工作模型尝试上限','numeric'],['retention_mib','内容保留阈值（MiB）','decimal']];
 return <details class="as-resource-allowance" open={opened()} onToggle={e=>{setOpened(e.currentTarget.open);if(e.currentTarget.open&&!local())void task(async()=>{});}}>
  <summary>{t('调整原工作限额')}</summary>
  <p>{t('调整只影响后续准入；不退回消耗、不清除未知记录，也不放宽连接权限或单连接上限。')}</p>
  <Show when={error()}><p role="alert">{authoredText(error())}</p></Show><Show when={conflict()}><button type="button" disabled={busy()} onClick={()=>void task(async j=>{await j.mergeExternal();setConflict(false);})}>{t('合并其他窗口记录并保留输入')}</button></Show><Show when={notice()}><p role="status">{notice()}</p></Show>
  <Show when={draft()}>{d=><>
   <fieldset disabled={busy()||localIssue()} class="as-resource-allowance-fields"><For each={fields}>{([key,label,inputmode])=><label>{t(label)}<input maxLength={100} inputmode={inputmode as 'numeric'|'decimal'} type="text" value={d().fields[key]} onInput={e=>edit(key,e.currentTarget.value)}/></label>}</For>
    <label class="as-resource-allowance-note">{t('调整备注（可选）')}<textarea rows="2" maxLength={320} value={d().fields.note} onInput={e=>edit('note',e.currentTarget.value)}/></label>
   </fieldset>
   <Show when={d().generation!==props.session.generation}><p role="status">{t('连接已变化，请保留输入并核对当前额度。')}</p></Show>
   <div class="as-capability-actions"><button type="button" disabled={busy()||localIssue()||unchanged()||d().generation!==props.session.generation||(props.receipt.usage?.allowance_adjustments??32)>=(props.receipt.usage?.adjustment_capacity??32)} onClick={()=>void apply()}>{t('保存限额')}</button><button type="button" disabled={busy()} onClick={()=>void task((j,key,n,generation)=>refreshBasis(j,key,n,generation))}>{t('保留输入并核对当前额度')}</button></div>
   <Show when={(props.receipt.usage?.allowance_adjustments??0)>=(props.receipt.usage?.adjustment_capacity??32)}><p role="status">{t('调整记录已达容量上限，原记录保留。')}</p></Show>
  </>}</Show>
  <Show when={local()?.copies.length}><details><summary>{t('保留的输入副本')} · {local()!.copies.length}</summary><For each={local()!.copies}>{(_,i)=><button type="button" disabled={busy()} onClick={()=>void task(async j=>{await j.restoreCopy(i());})}>{t('恢复输入副本')} {i()+1}</button>}</For></details></Show>
  <details><summary>{t('原调整记录')} · {rows().length}</summary>
   <button type="button" disabled={busy()} onClick={()=>void readOriginals()}>{t('读取原调整记录')}</button>
   <Show when={localIssue()}><p role="status">{t('本机日志不可用时，以下原记录仍可只读查看。')}</p><Show when={health()?.status==='INVALID'}><button type="button" disabled={busy()} onClick={()=>void rebuild()}>{t('完整保留旧数据并重建本机视图')}</button></Show></Show>
   <Show when={retainedCount()>0}><p role="status">{t('已保留原始存储值及可用内存副本')} · {retainedCount()}</p></Show>
   <Show when={legacyCount()>0}><p>{t('有历史调整缺少完整请求包；未补造输入。')} · {legacyCount()}</p></Show>
   <ol class="as-resource-allowance-history"><For each={[...rows()].reverse().slice(0,shown())}>{a=><li>
    <span>{status(a.phase,a.observation?.status)} · {t('模型尝试')} {a.packet.request.limits.model_attempts}</span>
    <Show when={a.last_lookup==='NOT_OBSERVED'||a.last_lookup==='UNAVAILABLE'}><p>{t('尚未观察到可核实的结果；这不能证明原操作未应用。')}</p></Show>
    <Show when={a.observation?.reason==='RESOURCE_SNAPSHOT_CHANGED'}><p>{t('资源版本已变化，此次调整未应用。')}</p></Show>
    <Show when={a.observation?.reason==='BELOW_CONSUMED_OR_RESERVED'}><p>{t('限额不能低于已消耗或已预留的数量。')}</p></Show>
    <button type="button" disabled={busy()} onClick={()=>void inspectOriginal(a)}>{t('核对原记录')}</button>
    <details><summary>{t('查看此次输入')}</summary><p>{t('资料读取')} {originalResourceBytes(a.packet.request.limits.source_bytes)} · {t('读取次数')} {a.packet.request.limits.source_reads}</p><p>{t('内容保留量')} {originalResourceBytes(a.packet.request.limits.retained_output_bytes)}</p><Show when={a.packet.request.note}><p>{a.packet.request.note}</p></Show><button type="button" disabled={busy()||localIssue()} onClick={()=>void task((j,key,n,generation)=>refreshBasis(j,key,n,generation,a.packet))}>{t('用此输入拟定新调整')}</button></details>
   </li>}</For></ol>
   <Show when={rows().length>shown()}><button type="button" onClick={()=>setShown(v=>Math.min(64,v+8))}>{t('显示更多记录')}</button></Show>
  </details>
  <Show when={local()&&journal?.unsaved()}><p role="status">{t('本机记录尚未保存，当前输入和已核实结果仍保留。')}</p></Show>
 </details>;
}
