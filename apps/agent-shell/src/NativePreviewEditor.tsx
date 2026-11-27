import {createSignal,For,onCleanup,onMount,Show} from 'solid-js';
import {t} from './ui-language';
import {authoredErrorCopy,authoredText,type UIErrorCopy} from './authored-ui-copy';
import {configurationCanonical as canonical} from './conversation-configuration';
import {type NativeReworkApi,type ReworkRecord,type ChangeKind,type InputCondition} from './native-rework';
import {PreviewActions,previewJournal,emptyPreviewDraft,type PreviewDraft,type PreviewJournal,type PreviewLocal} from './native-preview-editor';

export default function NativePreviewEditor(props:{mapping:ReworkRecord;api:NativeReworkApi;disabled:boolean;sourceCurrent:boolean;onPending:(value:boolean)=>void;onBusy:(value:boolean)=>void;onPreview:(record:ReworkRecord)=>void}){
 const [local,setLocal]=createSignal<PreviewLocal>(),[busy,setBusy]=createSignal(false),[unsaved,setUnsaved]=createSignal(false),[conflict,setConflict]=createSignal(false),[error,setError]=createSignal<UIErrorCopy>(''),[notice,setNotice]=createSignal(''),[baseline,setBaseline]=createSignal<string>(),[ready,setReady]=createSignal(false);
 let journal:PreviewJournal|undefined,actions:PreviewActions|undefined,alive=true,off:(()=>void)|undefined,timer:ReturnType<typeof setTimeout>|undefined;
 const draft=()=>local()?.draft??emptyPreviewDraft(),pending=()=>local()?.attempts.some(a=>a.phase!=='RECORDED'&&!a.retained)??false;
 const conditionLabel:Record<string,string>={VALUE_CHANGED:'对象值变化时核对',ANY_FILE_VERSION_CHANGED:'任意文件版本变化时核对',MANUAL_REVIEW:'始终人工核对'};
 const label:Record<string,string>={EXPRESSION:'表达',DATA:'数据',CONSTRAINT:'约束',UNKNOWN:'尚未判定'};
 function sync(){if(!alive||!journal)return;setLocal(journal.value());setUnsaved(journal.unsaved());props.onPending(journal.unsaved()||pending());}
 function showError(e:unknown){if(!alive)return;if((e as Error)?.message==='NATIVE_PREVIEW_DRAFT_CONFLICT')setConflict(true);setError(authoredErrorCopy(e));}
 async function persist(){clearTimeout(timer);if(journal)await journal.flush();}
 function edit(patch:Partial<PreviewDraft>){if(!journal)return;try{journal.edit({...draft(),...patch});clearTimeout(timer);timer=setTimeout(()=>void persist().catch(showError),300);}catch(e){showError(e);}}
 async function perform(work:()=>Promise<unknown>){if(busy())return;setBusy(true);props.onBusy(true);setError('');setNotice('');try{await work();}catch(e){showError(e);}finally{if(alive)setBusy(false);props.onBusy(false);}}
 onMount(()=>{props.onPending(true);void previewJournal(localStorage,props.mapping).then(async j=>{if(!alive)return;journal=j;actions=new PreviewActions(j,props.api);off=j.subscribe(sync);sync();if(j.unsaved())await persist();if(!alive)return;const value=await props.api.binding(props.mapping);if(!alive)return;const text=props.mapping.payload.locator?.kind==='JSON_POINTER'?JSON.stringify(value,null,2):String(value??'');setBaseline(text);setReady(true);if(j.value().draft.replacement===null){j.edit({...j.value().draft,replacement:text});await persist();}}).catch(showError);});
 onCleanup(()=>{alive=false;clearTimeout(timer);off?.();props.onBusy(false);props.onPending(false);if(journal?.unsaved())void journal.flush().catch(()=>{/* Existing global close barrier retains this dirty journal. */});});
 async function publish(record:ReworkRecord|null,stamp:string){if(!record){if(alive)setNotice('原预览结果待核查，完整请求与当前输入保留。');return;}if(!alive)return;if(canonical(journal!.value().draft)===stamp)props.onPreview(record);else setNotice('预览已记录，当前新输入保留；可从原请求读取。');}
 async function submit(){if(!journal||!actions||!ready())return;const stamp=canonical(journal.value().draft);await persist();if(canonical(journal.current().draft)!==stamp)throw Error('NATIVE_PREVIEW_INPUT_CHANGED');await publish(await actions.submit(props.mapping),stamp);}
 async function recover(sum:string,continuing=false){if(!journal||!actions)return;const stamp=canonical(journal.value().draft);await persist();await publish(await(continuing?actions.continue(sum):actions.inspect(sum)),stamp);}
 const canSubmit=()=>props.sourceCurrent&&ready()&&!!journal&&!unsaved()&&!conflict()&&!pending()&&draft().replacement!==null&&draft().declared&&!!draft().rationale.trim();
 return <details class="as-rework-edit as-native-preview-editor"><summary>{t('我来填写局部变更提议')}</summary>
  <p>{t('也可用上方明确入口请求原 Agent 准备只读预览。这里的分类与条件将作为你的声明保存。')}</p>
  <Show when={unsaved()||conflict()}><div class="as-inline-warning" role="status"><p>{t('预览输入或原回执尚未保存，正常退出会等待保存。')}</p><button type="button" disabled={busy()} onClick={()=>void perform(persist)}>{t('重试本机保存')}</button><button type="button" disabled={busy()} onClick={()=>void perform(async()=>{await journal?.resolve('LOCAL');setConflict(false);})}>{t('保留当前输入并合并原操作')}</button><button type="button" disabled={busy()} onClick={()=>void perform(async()=>{await journal?.resolve('SAVED');setConflict(false);})}>{t('使用已存输入并保留当前副本')}</button></div></Show>
  <Show when={ready()} fallback={<p role="status">{t('正在读取实际原值；未提供写入资格。')}</p>}><div class="as-rework-value"><small>{t('固定原对象值')}</small><pre>{baseline()}</pre></div></Show>
  <Show when={local()}><fieldset disabled={props.disabled||!journal}>
    <label>{props.mapping.payload.locator?.kind==='JSON_POINTER'?t('新值（JSON）'):t('新值（UTF-8 文本）')}<textarea rows="4" value={draft().replacement??''} onInput={event=>edit({replacement:event.currentTarget.value})}/></label>
    <label>{t('变更分类')}<select value={draft().classification} onChange={event=>edit({classification:event.currentTarget.value as ChangeKind})}><For each={['UNKNOWN','CONSTRAINT','DATA','EXPRESSION']}>{value=><option value={value}>{t(value==='UNKNOWN'?'尚未判定':label[value])}</option>}</For></select></label>
    <label>{t('输入有效条件')}<select value={draft().condition} onChange={event=>edit({condition:event.currentTarget.value as InputCondition})}><option value="VALUE_CHANGED">{t('对象值变化时核对')}</option><option value="ANY_FILE_VERSION_CHANGED">{t('任意文件版本变化时核对')}</option><option value="MANUAL_REVIEW">{t('始终人工核对')}</option></select></label>
    <label>{t('声明依据')}<textarea rows="2" value={draft().rationale} onInput={event=>edit({rationale:event.currentTarget.value})} placeholder={t('说明这些任务为何依赖该对象；分类与语义仍需核验')}/></label>
    <label class="as-rework-check"><input type="checkbox" checked={draft().declared} onChange={event=>edit({declared:event.currentTarget.checked})}/>{t('我声明 ')}{props.mapping.payload.task_ids!.join('、')}{t(' 使用此实际对象作为输入，任意分类变更均按上述条件核对；其他任务保持未知。')}</label>
    <button type="button" disabled={busy()||!canSubmit()} onClick={()=>void perform(submit)}>{t('保存预览 · 尚不写文件')}</button>
  </fieldset></Show>
  <Show when={local()?.copies.length}><details class="as-native-preview-copies"><summary>{t('保留的预览输入副本')}</summary><For each={local()?.copies}>{copy=><div><p>{Array.from(copy.draft.replacement??'').slice(0,240).join('')}</p><button type="button" disabled={busy()||props.disabled} onClick={()=>void perform(async()=>{await persist();await journal?.copyAction(copy.id);})}>{t('恢复这份输入并保留当前副本')}</button></div>}</For></details></Show>
  <Show when={local()?.attempts.length}><details class="as-native-preview-history"><summary>{t('原预览请求与结果核查')}</summary><For each={local()?.attempts}>{attempt=><article class="as-rework-operation">
    <p>{t(attempt.phase==='RECORDED'?'预览已记录':'原预览请求待核查')}</p><details><summary>{t('原输入与依据')}</summary><p>{attempt.packet.input.operation_id}</p><p>{t(label[attempt.packet.input.classification_kind])} · {attempt.packet.input.rationale}</p><pre>{attempt.packet.input.proposed_value_text}</pre><p>{t('原关联版本')} {attempt.packet.input.mapping_ref.revision}</p><For each={attempt.packet.input.contracts}>{contract=><p>{contract.task_id} · {t(conditionLabel[contract.inputs[0].condition])}</p>}</For></details>
    <button type="button" disabled={busy()||props.disabled} onClick={()=>void perform(()=>recover(attempt.checksum))}>{t('读取原回执')}</button>
    <button type="button" disabled={busy()||props.disabled} onClick={()=>void perform(async()=>{await persist();await journal?.restoreRequest(attempt.checksum);})}>{t('恢复原请求为输入并保留当前副本')}</button>
    <Show when={attempt.phase==='UNKNOWN'&&['NOT_FOUND','INCOMPLETE'].includes(attempt.last_lookup)}><button type="button" disabled={busy()||props.disabled||!props.sourceCurrent} onClick={()=>void perform(()=>recover(attempt.checksum,true))}>{t('按原输入明确继续预览')}</button><Show when={!attempt.retained}><button type="button" disabled={busy()||props.disabled} onClick={()=>void perform(()=>journal!.retain(attempt.checksum))}>{t('保留未知结果并填写新的提议')}</button></Show></Show>
  </article>}</For></details></Show>
  <Show when={notice()}><p role="status">{t(notice())}</p></Show><Show when={error()}><p role="alert">{authoredText(error(),t)}</p></Show>
 </details>;
}
