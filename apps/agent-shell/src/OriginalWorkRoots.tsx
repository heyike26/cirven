import {flushResourceAllowanceDrafts} from './resource-allowance-journal';
import {flushExperienceDrafts} from './task-experience-journal';
import {flushWorkMaintenanceDrafts} from './work-maintenance-editor';
import {flushNativeExportJournals} from './native-export-journal';
import {createEffect,createSignal,For,on,onCleanup,Show} from 'solid-js';
import type {AgentSession} from './agent-sessions';
import {originalFileScope} from './original-session-files';
import {OriginalWorkRootsApi,type WorkRootCatalog} from './original-work-roots';
import OriginalWorkRequirements from './OriginalWorkRequirements';
import {flushWorkRequirementDrafts} from './work-requirement-editor';
import {flushWorkCaseDrafts} from './work-case-editor';
import {flushWorkRetestDrafts} from './work-retest-editor';
import {flushWorkSourceDrafts} from './work-source-editor';
import {flushWorkDeliveryDrafts} from './work-delivery-editor';
import type {OriginalWorkInputSource} from './original-work-input';

/** One work selector for requirements and delivery; commands are optional. */
export default function OriginalWorkRoots(props:{session:AgentSession;locale:string;requested?:{root:string};onContinueWork?:(source:OriginalWorkInputSource,current:()=>boolean)=>Promise<void>}){
 const reader=new OriginalWorkRootsApi(),[view,setView]=createSignal<WorkRootCatalog>(),[selected,setSelected]=createSignal<string>(),[busy,setBusy]=createSignal(false),[error,setError]=createSignal(false);
 const t=(zh:string,en:string)=>props.locale.startsWith('en')?en:zh,scope=()=>originalFileScope(props.session),identity=()=>JSON.stringify(scope()),owner=()=>{const s=scope();return s&&JSON.stringify([s.project_id,s.session_ref,s.workspace_ref]);};
 let section:HTMLDetailsElement|undefined,alive=true,epoch=0,abort:AbortController|undefined;
 onCleanup(()=>{alive=false;epoch++;abort?.abort();});
 createEffect(on(owner,()=>setSelected(undefined)));
 createEffect(on(identity,()=>{epoch++;abort?.abort();setView(undefined);setBusy(false);setError(false);if(section?.open&&!selected())void read();}));
 async function read(offset=0){if(busy()||!scope())return;const key=identity(),n=epoch;setBusy(true);setError(false);abort?.abort();abort=new AbortController();try{const next=await reader.inspect(structuredClone(props.session),offset,abort.signal);if(alive&&epoch===n&&identity()===key)setView(next);}catch{if(alive&&epoch===n&&identity()===key)setError(true);}finally{if(alive&&epoch===n&&identity()===key)setBusy(false);}}
 async function select(root:string){if(busy()||!scope())return;const key=identity(),n=epoch;setBusy(true);setError(false);try{
  await flushWorkRequirementDrafts();await flushWorkCaseDrafts();await flushWorkRetestDrafts();await flushWorkSourceDrafts();await flushWorkDeliveryDrafts();await flushNativeExportJournals();await flushWorkMaintenanceDrafts();await flushExperienceDrafts();await flushResourceAllowanceDrafts();
  if(alive&&epoch===n&&identity()===key){setSelected(root);if(section)section.open=true;}
 }catch{if(alive&&epoch===n&&identity()===key)setError(true);}finally{if(alive&&epoch===n&&identity()===key)setBusy(false);}}
 async function continueInput(source:OriginalWorkInputSource,childCurrent:()=>boolean){
  const key=identity(),n=epoch,current=()=>alive&&epoch===n&&identity()===key&&childCurrent();
  if(busy()||!props.onContinueWork||!current())return;setBusy(true);setError(false);
  try{await flushWorkRequirementDrafts();await flushWorkCaseDrafts();await flushWorkRetestDrafts();await flushWorkSourceDrafts();await flushWorkDeliveryDrafts();await flushNativeExportJournals();await flushWorkMaintenanceDrafts();await flushExperienceDrafts();await flushResourceAllowanceDrafts();if(current())await props.onContinueWork(structuredClone(source),current);}
  catch{if(current())setError(true);}finally{if(current())setBusy(false);}
 }
 createEffect(on(()=>props.requested,request=>{if(request)void select(request.root);}));
 return <Show when={scope()}><details ref={section} class="as-work-root-selector" onToggle={()=>{if(section?.open&&!view()&&!selected()&&!busy())void read();}}><summary>{t('工作要求、交付与维护','Work requirements, delivery and maintenance')}</summary>
  <button type="button" disabled={busy()} onClick={()=>void read()}>{t('选择原工作','Choose original work')}</button>
  <Show when={error()}><p role="alert">{t('原工作暂未核对或草稿未保存；当前选择与原记录保留。','Original work could not be inspected or drafts are unsaved. Your selection and records are retained.')}</p><Show when={props.requested}><button type="button" disabled={busy()} onClick={()=>void select(props.requested!.root)}>{t('重试切换工作','Retry switching work')}</button></Show></Show>
  <Show when={view()}>{v=><>
   <Show when={!v().coverage.complete}><p role="status">{t('部分原工作元数据暂不可读，以下仅列已核对记录。','Some original work metadata is unavailable. Only verified records are listed.')}</p></Show>
   <Show when={v().coverage.unacknowledged_messages>0}><p>{t('尚未确认派发的请求保留在原会话记录中。','Requests without dispatch acknowledgement remain in the original conversation records.')}</p></Show>
   <Show when={!v().entries.length&&v().coverage.complete}><p>{t('此处尚无已确认派发的原工作。','No acknowledged original work is available here yet.')}</p></Show>
   <ul><For each={v().entries}>{entry=><li><button type="button" disabled={busy()} aria-pressed={selected()===entry.operation_id} onClick={()=>void select(entry.operation_id)}>{entry.label||t('原工作','Original work')}</button></li>}</For></ul>
   <Show when={v().next_offset!==null}><button type="button" disabled={busy()} onClick={()=>void read(v().next_offset!)}>{t('下一页工作','Next work page')}</button></Show>
  </>}</Show>
  <Show when={selected()}>{root=><OriginalWorkRequirements session={props.session} root={root()} locale={props.locale} onContinueWork={props.onContinueWork?continueInput:undefined} open/>}</Show>
 </details></Show>;
}
