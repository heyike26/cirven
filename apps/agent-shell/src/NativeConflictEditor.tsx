import {createSignal,For,onCleanup,onMount,Show} from 'solid-js';
import {t} from './ui-language';
import {authoredErrorCopy,authoredText,type UIErrorCopy} from './authored-ui-copy';
import {type ApiClient} from './api';
import {type SourceRef} from './contracts';
import {configurationCanonical as canonical} from './conversation-configuration';
import {rememberReworkPreview,type ObjectLocator,type ReworkRecord} from './native-rework';
import {NativeReworkConflictsApi,type ConflictReview} from './native-rework-conflicts';
import {ConflictActions,conflictJournal,conflictTextSelection,emptyConflictDraft,type ConflictDraft,type ConflictJournal,type ConflictLocal} from './native-conflict-editor';

const phaseLabel={PENDING:'正在保存原预览请求',UNKNOWN:'原请求结果待核查',RECORDED:'新预览已记录'};
export default function NativeConflictEditor(props:{plan:ReworkRecord;client:ApiClient;disabled:boolean;onBusy:(value:boolean)=>void;onPending:(value:boolean)=>void;onPreview:(value:ReworkRecord)=>void}){
  const [local,setLocal]=createSignal<ConflictLocal>(),[review,setReview]=createSignal<ConflictReview>(),[busy,setBusy]=createSignal(false),[error,setError]=createSignal<UIErrorCopy>(''),[unsaved,setUnsaved]=createSignal(false),[localConflict,setLocalConflict]=createSignal(false),[message,setMessage]=createSignal('');
  const [selection,setSelection]=createSignal<{source:SourceRef;locator:ObjectLocator}>();
  let journal:ConflictJournal|undefined,actions:ConflictActions|undefined,alive=true,off:(()=>void)|undefined,timer:ReturnType<typeof setTimeout>|undefined,selectionEpoch=0;
  const abort=new AbortController(),reader=new NativeReworkConflictsApi(props.client),draft=()=>local()?.draft??emptyConflictDraft();
  const pending=()=>local()?.attempts.some(a=>a.phase!=='RECORDED'&&!a.retained)??false;
  function sync(){if(!alive||!journal)return;setLocal(journal.value());setUnsaved(journal.unsaved());props.onPending(journal.unsaved()||pending());}
  function showError(value:unknown){if(alive){if((value as Error)?.message==='NATIVE_CONFLICT_DRAFT_CONFLICT')setLocalConflict(true);setError(authoredErrorCopy(value));}}
  async function perform(work:()=>Promise<unknown>){if(busy())return;setBusy(true);props.onBusy(true);setError('');setMessage('');try{await work();}catch(e){showError(e);}finally{if(alive)setBusy(false);props.onBusy(false);}}
  async function persist(){clearTimeout(timer);if(journal)await journal.flush();}
  function edit(patch:Partial<ConflictDraft>){if(!journal)return;try{journal.edit({...draft(),...patch});clearTimeout(timer);timer=setTimeout(()=>void persist().catch(showError),300);}catch(e){showError(e);}}
  onMount(()=>{props.onPending(true);void conflictJournal(localStorage,props.plan).then(j=>{if(!alive)return;journal=j;actions=new ConflictActions(j,reader);off=j.subscribe(sync);sync();if(j.unsaved())void persist().catch(showError);}).catch(showError);});
  onCleanup(()=>{alive=false;selectionEpoch++;abort.abort();clearTimeout(timer);off?.();props.onBusy(false);props.onPending(false);if(journal?.unsaved())void journal.flush().catch(()=>{/* Global dirty registry retains the unsaved journal. */});});
  async function inspect(locator?:ObjectLocator,expected?:SourceRef){
    if(!journal)throw Error('NATIVE_CONFLICT_LOCAL_INVALID');await persist();const before=canonical(journal.current().draft),scope=props.plan.payload.write_scope;
    if(scope?.length!==1)throw Error('NATIVE_CONFLICT_CHANGED');const file=await props.client.file(props.plan.payload.project_id,scope[0].workspace_id,scope[0].path,abort.signal);
    if(!alive)return;if(!file.current||expected&&canonical(file.source_ref)!==canonical(expected))throw Error('NATIVE_CONFLICT_SELECTION_CHANGED');
    const previous=draft().query,currentLocator=locator??(previous&&canonical(previous.current_source)===canonical(file.source_ref)?previous.current_locator:null);
    const query={plan_ref:props.plan.record_ref,current_source:file.source_ref,current_locator:currentLocator},value=await reader.inspect(props.plan,query,abort.signal);
    if(!alive)return;if(canonical(journal.current().draft)!==before)throw Error('NATIVE_CONFLICT_DRAFT_CONFLICT');
    selectionEpoch++;setSelection(undefined);setReview(value);journal.edit({...draft(),query,review_sha256:value.review_sha256,replacement:draft().replacement??value.proposed_text,confirmed:false});await persist();
  }
  async function choose(element:HTMLTextAreaElement){
    const value=review(),epoch=++selectionEpoch;setSelection(undefined);if(!value?.current_content||element.selectionStart===element.selectionEnd)return;
    try{const locator=await conflictTextSelection(value.current_content,element.value,element.selectionStart,element.selectionEnd);if(alive&&epoch===selectionEpoch&&review()===value)setSelection({source:value.basis.current_source,locator});}catch(e){showError(e);}
  }
  async function publish(record:ReworkRecord|null,stamp:string){
    if(!alive)return;if(!record){setMessage('原请求结果尚未确定，请核查原编号。');return;}
    const attempt=journal!.value().attempts.find(a=>a.receipt?.record_ref.id===record.record_ref.id);if(!attempt)throw Error('NATIVE_CONFLICT_CHANGED');
    await rememberReworkPreview(localStorage,record,attempt.request.operation_id);
    if(!alive)return;if(canonical(journal!.value().draft)===stamp)props.onPreview(record);else setMessage('新预览已记录，当前输入已保留；可从原操作查看。');
  }
  async function submit(){if(!journal||!actions||!review())throw Error('NATIVE_CONFLICT_REVIEW_REQUIRED');await persist();const stamp=canonical(journal.current().draft);await publish(await actions.submit(props.plan,review()!),stamp);}
  async function recover(sum:string,continuing=false){if(!journal||!actions)return;await persist();const stamp=canonical(journal.current().draft);await publish(await(continuing?actions.continue(props.plan,sum):actions.inspect(props.plan,sum)),stamp);}
  const ready=()=>!!review()&&review()!.selection_status==='EXACT_CURRENT_OBJECT'&&draft().confirmed&&!!draft().rationale.trim()&&draft().replacement!==null&&draft().review_sha256===review()!.review_sha256&&!pending()&&!unsaved();
  return <details class="as-native-conflict" data-native-conflict-parent={props.plan.record_ref.id}>
    <summary>{t('人工修改冲突与三方对照')}</summary>
    <p class="as-muted">{t('对照原值、当前保存值和旧提议，再保存你的新预览。原确认与人工内容保留，保存预览不会写文件。')}</p>
    <Show when={error()}><p role="alert" class="as-inline-warning">{authoredText(error(),t)}</p></Show>
    <Show when={message()}><p role="status">{t(message())}</p></Show>
    <Show when={unsaved()||localConflict()}><div class="as-inline-warning"><p>{t('冲突处理输入或回执尚未保存，正常退出会等待保存。')}</p><button type="button" disabled={busy()} onClick={()=>void perform(persist)}>{t('重试本机保存')}</button><button type="button" disabled={busy()} onClick={()=>void perform(async()=>{await journal?.resolve('LOCAL');setLocalConflict(false);setReview(undefined);})}>{t('保留当前输入并合并原操作')}</button><button type="button" disabled={busy()} onClick={()=>void perform(async()=>{await journal?.resolve('SAVED');setLocalConflict(false);setReview(undefined);})}>{t('使用已存输入并保留当前副本')}</button></div></Show>
    <fieldset disabled={props.disabled||busy()||!local()}>
      <legend>{t('当前对象复核')}</legend>
      <button type="button" onClick={()=>void perform(()=>inspect())}>{t('读取当前保存版本并对照')}</button>
      <Show when={review()}>{v=><>
        <p class="as-muted">{v().basis.current_source.path} · {t('当前保存版本 v')}{v().basis.current_source.revision}</p>
        <div class="as-conflict-comparison"><For each={[{label:'原预览依据',value:v().base_text},{label:'当前保存对象',value:v().current_text},{label:'旧提议',value:v().proposed_text}]}>{row=><section><strong>{t(row.label)}</strong><pre>{row.value??t('请在当前保存文本中选择同一对象')}</pre></section>}</For></div>
        <Show when={v().basis.original_locator.kind==='TEXT_RANGE'}><details open={v().selection_status==='CURRENT_SELECTION_REQUIRED'}><summary>{t('明确当前文本对象')}</summary><label>{t('在当前保存文本中选择原对象对应范围')}<textarea rows="7" readOnly value={v().current_content??''} onSelect={event=>void choose(event.currentTarget)} onBlur={()=>{selectionEpoch++;}}/></label><button type="button" disabled={!selection()} onClick={()=>{const chosen=selection();if(chosen)void perform(()=>inspect(chosen.locator,chosen.source));}}>{t('以所选文本为当前对象')}</button></details></Show>
      </>}</Show>
      <Show when={draft().replacement!==null}><label>{review()?.basis.original_locator.kind==='JSON_POINTER'?t('人工决定的新值（JSON 原文）'):t('人工决定的新值（UTF-8 文本）')}<textarea rows="6" value={draft().replacement??''} onInput={event=>edit({replacement:event.currentTarget.value,confirmed:false})} onBlur={()=>void persist().catch(showError)}/></label><label>{t('处理依据')}<textarea rows="2" maxLength="4000" value={draft().rationale} onInput={event=>edit({rationale:event.currentTarget.value,confirmed:false})} onBlur={()=>void persist().catch(showError)}/></label></Show>
      <label class="as-rework-check"><input type="checkbox" checked={draft().confirmed} disabled={!review()||review()!.selection_status!=='EXACT_CURRENT_OBJECT'} onChange={event=>edit({confirmed:event.currentTarget.checked})}/>{t('我已核对当前对象，保留原任务输入条件，仅保存以上人工决定的新预览。')}</label>
      <button type="button" disabled={!ready()} onClick={()=>void perform(submit)}>{t('保存人工决定预览 · 不写文件')}</button>
    </fieldset>
    <Show when={local()?.copies.length}><details><summary>{t('保留的冲突输入副本')}</summary><For each={local()?.copies??[]}>{row=><div><p class="as-conflict-copy">{row.draft.replacement}</p><button type="button" disabled={busy()||props.disabled} onClick={()=>void perform(async()=>{await journal?.copyAction(row.id);setReview(undefined);})}>{t('换用此输入副本')}</button><button type="button" disabled={busy()||props.disabled} onClick={()=>void perform(async()=>{await journal?.copyAction(row.id,true);})}>{t('删除此输入副本')}</button></div>}</For></details></Show>
    <Show when={local()?.attempts.length}><details open={pending()}><summary>{t('人工预览原操作与恢复')}</summary><For each={local()?.attempts??[]}>{a=><section class="as-conflict-operation" data-resolution-phase={a.phase}><strong>{t(phaseLabel[a.phase])}</strong><small>{t('原操作编号：')}{a.request.operation_id}</small><Show when={a.initial_unknown}><p>{t('此前曾出现未知结果，原记录保留。')}</p></Show><button type="button" disabled={busy()||props.disabled} onClick={()=>void perform(()=>recover(a.checksum))}>{a.phase==='RECORDED'?t('查看已记录新预览'):t('核查原操作')}</button><Show when={a.phase==='UNKNOWN'&&!a.receipt}><button type="button" disabled={busy()||props.disabled} onClick={()=>void perform(()=>recover(a.checksum,true))}>{t('明确按原编号继续保存预览')}</button><Show when={!a.retained&&['NOT_FOUND','INCOMPLETE'].includes(a.last_lookup)}><button type="button" disabled={busy()||props.disabled} onClick={()=>void perform(async()=>{await journal?.retain(a.checksum);})}>{t('保留原结果，允许另一次预览请求')}</button></Show></Show><Show when={a.retained}><p>{t('原未知结果已保留，新请求仍须重新复核。')}</p></Show></section>}</For></details></Show>
  </details>;
}
