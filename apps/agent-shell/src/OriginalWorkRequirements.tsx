import OriginalWorkMaintenance from './OriginalWorkMaintenance';
import OriginalWorkExperience from './OriginalWorkExperience';
import {experienceContext} from './task-experience';
import OriginalWorkDeliveries from './OriginalWorkDeliveries';
import {createEffect,createSignal,For,on,onCleanup,Show} from 'solid-js';
import type {AgentSession} from './agent-sessions';
import {OriginalWorkRequirementsApi,type RequirementRef,type WorkRequirements} from './original-work-requirements';
import {originalFileScope} from './original-session-files';
import {workRequirementScope} from './original-work-requirements';
import WorkRequirementEditor from './WorkRequirementEditor';
import WorkRequirementSources from './WorkRequirementSources';
import OriginalWorkTestCases from './OriginalWorkTestCases';
import {configurationCanonical as canonical} from './conversation-configuration';
import type {OriginalWorkInputSource} from './original-work-input';

/** Saved requirement context of the same original work. This view dispatches no task. */
export default function OriginalWorkRequirements(props:{session:AgentSession;root:string;locale:string;open?:boolean;onContinueWork?:(source:OriginalWorkInputSource,current:()=>boolean)=>Promise<void>}){
 const api=new OriginalWorkRequirementsApi(),[view,setView]=createSignal<WorkRequirements>(),[busy,setBusy]=createSignal(false),[failed,setFailed]=createSignal(false);
 const t=(zh:string,en:string)=>props.locale.startsWith('en')?en:zh;
 const identity=()=>JSON.stringify([originalFileScope(props.session),props.root]);let section:HTMLDetailsElement|undefined,alive=true,sequence=0,abort:AbortController|undefined;
 onCleanup(()=>{alive=false;sequence++;abort?.abort();});
 createEffect(on(identity,()=>{sequence++;abort?.abort();setView(undefined);setBusy(false);setFailed(false);}));
 function display(next:WorkRequirements){if(next.project_id!==props.session.project_id||canonical(next.scope)!==canonical(workRequirementScope(props.session,props.root)))return;setView(previous=>{
   const reuse=<T extends {id?:string;operation_id?:string}>(old:T[]|undefined,rows:T[])=>rows.map(row=>{const before=old?.find(v=>(v.id??v.operation_id)===(row.id??row.operation_id));return before&&canonical(before)===canonical(row)?before:row;});
   next.links=reuse(previous?.links,next.links);next.impacts=reuse(previous?.impacts,next.impacts);
   if(next.selected&&previous?.selected&&canonical(next.selected.ref)===canonical(previous.selected.ref)){next.selected.requirements=reuse(previous.selected.requirements,next.selected.requirements);next.selected.acceptance=reuse(previous.selected.acceptance,next.selected.acceptance);}
   return next;
  });}
 async function read(selected:RequirementRef|null=view()?.selected?.ref??null,detail?:{kind:'requirement'|'acceptance';id:string}){
  if(busy())return;const key=identity(),request=++sequence;abort?.abort();abort=new AbortController();setBusy(true);setFailed(false);
  try{const next=await api.inspect(props.session,props.root,{selected,detail,catalog:false},abort.signal);if(alive&&sequence===request&&identity()===key)display(next);}
  catch{if(alive&&sequence===request&&identity()===key)setFailed(true);}
  finally{if(alive&&sequence===request&&identity()===key)setBusy(false);}
 }
 const status=(value:string)=>value==='CURRENT'?t('登记版本未变','Registered version unchanged'):value==='SELECTED_UNCHANGED_NEWER_BUNDLE'?t('要求集有新版，所选条目未变；新版尚未采用','A newer requirement set exists; selected items match. New version not adopted.'):value==='NEEDS_REVIEW'?t('所选依据需要复核','Selected basis needs review'):t('原依据暂不可核对','Original basis unavailable');
 async function continueInput(){const value=view(),key=identity(),request=sequence,current=()=>alive&&identity()===key&&sequence===request;if(!value||!props.onContinueWork||busy())return;setBusy(true);setFailed(false);try{await props.onContinueWork({root_basis:structuredClone(value.root_basis),root_sha256:value.root_sha256,label:value.label},current);}catch{if(current())setFailed(true);}finally{if(current())setBusy(false);}}
 return <details ref={section} open={props.open} class="as-work-requirements" onToggle={()=>{if(section?.open&&!view()&&!busy())void read(null);}}><summary>{t('对应要求','Linked requirements')}</summary>
  <button type="button" disabled={busy()} onClick={()=>void read()}>{busy()?t('核对中…','Checking…'):t('核对原关联','Check saved links')}</button>
  <Show when={failed()}><p role="alert">{t('关联暂未核对完成，已读内容保留。','Link inspection is unavailable. Previous results are retained.')}</p></Show>
  <Show when={view()}>{data=><>
   <Show when={props.onContinueWork}><button type="button" disabled={busy()} onClick={()=>void continueInput()}>{t('继续此工作','Continue this work')}</button></Show>
   <WorkRequirementEditor session={props.session} root={props.root} view={data()} locale={props.locale} onView={display}/>
   <WorkRequirementSources session={props.session} root={props.root} view={data()} locale={props.locale} onView={display}/>
   <OriginalWorkTestCases session={props.session} root={props.root} locale={props.locale}/>
   <OriginalWorkDeliveries session={props.session} root={props.root} rootSha={data().root_sha256} locale={props.locale}/>
   <OriginalWorkMaintenance session={props.session} root={props.root} rootSha={data().root_sha256} locale={props.locale}/>
   <OriginalWorkExperience context={experienceContext(props.session)} root={props.root} rootSha={data().root_sha256} locale={props.locale}/>
   <Show when={!data().links.length}><p>{t('此工作尚未关联要求。','This work has no linked requirements.')}</p></Show>
   <For each={data().impacts}>{impact=><section class="as-work-requirement-link"><strong>{impact.title??t('原要求暂不可读','Original requirements unavailable')}</strong><Show when={data().links.find(link=>link.operation_id===impact.operation_id)?.source_authority}><p>{t('外项目只读要求','Read-only requirements from another project')}</p></Show><p>{t('上次核对结果：','Last inspection: ')}{status(impact.review_status)}</p>
    <Show when={impact.changed_requirement_ids.length}><p>{t('已变更的所选要求：','Changed selected requirements: ')}{impact.changed_requirement_ids.join(', ')}</p></Show>
    <Show when={impact.changed_acceptance_ids.length}><p>{t('已变更的验收条目：','Changed acceptance items: ')}{impact.changed_acceptance_ids.join(', ')}</p></Show>
    <Show when={impact.stale_source_requirement_ids.length}><p>{t('来源版本需要复核：','Source versions needing review: ')}{impact.stale_source_requirement_ids.join(', ')}</p></Show>
    <button type="button" disabled={busy()} onClick={()=>void read(impact.requirement_ref)}>{t('查看关联时的要求','View originally linked requirements')}</button>
   </section>}</For>
   <Show when={data().selected}>{selected=><div class="as-work-requirement-items"><strong>{selected().title} · {t('版本 ','Version ')}{selected().ref.revision}</strong>
    <Show when={selected().origin==='mock'}><p>{t('此要求来自已人工确认的历史提案；生成方式不作为执行或测试证据。','These requirements come from a human-confirmed historical proposal. Its generation method is not execution or test evidence.')}</p></Show>
    <For each={selected().requirements.filter(row=>data().links.find(link=>link.requirement_ref.id===selected().ref.id)?.requirement_ids.includes(row.id))}>{row=><section><p><strong>{row.id}</strong> · {row.text_excerpt}</p><Show when={row.text_truncated}><button type="button" disabled={busy()} onClick={()=>void read(selected().ref,{kind:'requirement',id:row.id})}>{t('查看完整条目','Read full item')}</button></Show></section>}</For>
    <For each={selected().acceptance.filter(row=>data().links.find(link=>link.requirement_ref.id===selected().ref.id)?.acceptance_ids.includes(row.id))}>{row=><section><p><strong>{row.id}</strong> · {row.text_excerpt}</p><Show when={row.text_truncated}><button type="button" disabled={busy()} onClick={()=>void read(selected().ref,{kind:'acceptance',id:row.id})}>{t('查看完整条目','Read full item')}</button></Show></section>}</For>
    <Show when={selected().detail}>{detail=><p class="as-work-requirement-full">{detail().text}</p>}</Show>
   </div>}</Show>
   <p class="as-command-scope">{t('关联记录工作依据，不改变原指令或验收状态。登记来源版本与当前文件、目标环境分别核对。','Links record the work basis without changing its instruction or acceptance status. Registered source versions, current files and the target environment are checked separately.')}</p>
  </>}</Show>
 </details>;
}
