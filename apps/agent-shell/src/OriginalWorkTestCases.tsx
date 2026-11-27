import OriginalWorkRetest from './OriginalWorkRetest';
import {createEffect,createSignal,For,on,onCleanup,Show} from 'solid-js';
import type {AgentSession} from './agent-sessions';
import {workRequirementScope} from './original-work-requirements';
import {OriginalWorkTestCasesApi,type WorkCaseMappings} from './original-work-test-cases';
import {configurationCanonical as canonical} from './conversation-configuration';
import WorkCaseEditor from './WorkCaseEditor';

/** Explicit inspection only; declaring a mapping requires its own durable intent. */
export default function OriginalWorkTestCases(props:{session:AgentSession;root:string;locale:string}){
 const api=new OriginalWorkTestCasesApi(),[view,setView]=createSignal<WorkCaseMappings>(),[busy,setBusy]=createSignal(false),[failed,setFailed]=createSignal(false);
 const t=(zh:string,en:string)=>props.locale.startsWith('en')?en:zh,identity=()=>canonical([props.session.project_id,workRequirementScope(props.session,props.root)]);
 let section:HTMLDetailsElement|undefined,sequence=0,alive=true,abort:AbortController|undefined;
 onCleanup(()=>{alive=false;sequence++;abort?.abort();});
 createEffect(on(identity,()=>{sequence++;abort?.abort();setView(undefined);setBusy(false);setFailed(false);}));
 async function read(offset=view()?.page.offset??0){if(busy())return;const key=identity(),request=++sequence;abort?.abort();abort=new AbortController();const owned=abort;setBusy(true);setFailed(false);let timer:ReturnType<typeof setTimeout>|undefined;
  try{const next=await Promise.race([api.inspect(props.session,props.root,{offset},owned.signal),new Promise<never>((_,reject)=>{timer=setTimeout(()=>{owned.abort();reject(Error('WORK_CASE_INSPECTION_TIMEOUT'));},25000);})]);if(alive&&identity()===key&&sequence===request)setView(before=>({...next,rows:next.rows.map(row=>{const previous=before?.rows.find(old=>old.operation.operation_id===row.operation.operation_id);return previous&&canonical(previous)===canonical(row)?previous:row;})}));}
  catch{if(alive&&identity()===key&&sequence===request)setFailed(true);}
  finally{if(timer!==undefined)clearTimeout(timer);if(alive&&identity()===key&&sequence===request)setBusy(false);}
 }
 const caseStatus=(value:string)=>({PASSED:t('报告通过','Reported pass'),FAILED:t('报告失败','Reported failure'),ERROR:t('报告错误','Reported error'),SKIPPED:t('报告跳过','Reported skip'),UNKNOWN:t('报告结果未核实','Report outcome unverified')}[value]??t('报告结果未核实','Report outcome unverified'));
 const requirement=(status:string)=>({CURRENT:t('登记依据未变','Registered basis unchanged'),SELECTED_UNCHANGED_NEWER_BUNDLE:t('要求集有新版，对应用例的条目未变','New requirement set; mapped items unchanged'),NEEDS_REVIEW:t('对应要求或来源需要复核','Mapped requirements or sources need review'),REQUIREMENT_LINK_CHANGED:t('原要求关联已调整或解除','Original requirement link changed or removed'),UNAVAILABLE:t('原要求依据暂不可核对','Original requirement basis unavailable'),NOT_CHECKED_BUDGET:t('本次读取额度内未核对','Not checked within this read budget')}[status]??t('尚未核对','Not checked'));
 return <details ref={section} class="as-work-case-mappings" onToggle={()=>{if(section?.open&&!view()&&!busy())void read(0);}}><summary>{t('验收条目与用例对应','Acceptance items and cases')}</summary>
  <button type="button" disabled={busy()} onClick={()=>void read()}>{busy()?t('核对中…','Checking…'):t('核对已保存对应','Check saved mappings')}</button>
  <Show when={failed()}><p role="alert">{t('对应记录暂未核对完成，已读结果保留。','Mapping inspection is incomplete. Previous results are retained.')}</p></Show>
  <Show when={view()}>{v=><>
   <Show when={!v().page.total}><p>{t('此工作尚未声明验收条目与测试用例的对应。','This work has no declared acceptance-to-case mappings.')}</p></Show>
   <For each={v().rows}>{row=><section class="as-work-case-mapping"><strong>{row.operation.proof!.case_label.name||t('未命名用例','Unnamed case')}</strong><p>{caseStatus(row.operation.proof!.case_label.status)}</p><p>{t('用例序号 ','Case ordinal ')}{row.operation.proof!.case_label.ordinal+1} · {row.operation.proof!.acceptance_ids.join(', ')} · {t('要求版本 ','Requirement version ')}{row.operation.proof!.requirement_ref.revision}</p><p>{t('上次核对：','Last inspection: ')}{requirement(row.requirement_status)}</p><p>{row.report_status==='VERIFIED_SAVED_CASE'?t('原报告用例已核对','Original saved case verified'):row.report_status==='NOT_CHECKED_BUDGET'?t('原报告本次未在额度内核对','Original report not checked within this read budget'):t('原报告暂不可核对，原对应记录保留','Original report unavailable; mapping record retained')}</p><Show when={row.operation.proof!.command_status==='UNKNOWN_OUTCOME'}><p>{t('原命令结果仍未知。','The original command outcome is still unknown.')}</p></Show><Show when={row.operation.proof!.command_status==='EXITED'&&row.operation.proof!.command_exit_code!==0}><p>{t('原命令非零退出：','Original command exited nonzero: ')}{row.operation.proof!.command_exit_code}</p></Show><Show when={row.operation.proof!.association==='UNCHANGED_BYTES'||row.operation.proof!.association==='BEFORE_UNAVAILABLE'}><p>{t('报告是否由该命令新生成尚未证明。','New report generation by this command is not proven.')}</p></Show></section>}</For>
   <OriginalWorkRetest session={props.session} root={props.root} locale={props.locale}/>
   <WorkCaseEditor session={props.session} root={props.root} locale={props.locale} onSaved={()=>{if(!busy())void read();}}/>
   <p class="as-command-scope">{t('对应是人工覆盖声明；报告、当前工程和目标环境的实际验收分别核对。','Mappings are human coverage declarations. Report facts, current workspace and target acceptance are checked separately.')}</p>
   <nav class="as-command-report-pages" aria-label={t('对应记录分页','Mapping pages')}><button type="button" disabled={busy()||v().page.offset===0} onClick={()=>void read(Math.max(0,v().page.offset-20))}>{t('上一页','Previous')}</button><span>{v().page.total}</span><button type="button" disabled={busy()||v().page.next_offset===null} onClick={()=>{const next=v().page.next_offset;if(next!==null)void read(next);}}>{t('下一页','Next')}</button></nav>
  </>}</Show>
 </details>;
}
