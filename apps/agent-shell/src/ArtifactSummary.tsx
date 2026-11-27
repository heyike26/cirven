import {createEffect,createMemo,createSignal,For,Show,onCleanup}from'solid-js';
import type {ArtifactEnvelope,RendererContext}from'./contracts';
import {uiLocale}from'./ui-language';
import {artifactSummary,boundedArtifactDiagnostic,planningCapabilitySummary,type PlanningToolId}from'./artifact-summary';
import './artifact-summary.css';
type Props={artifact:ArtifactEnvelope;context:RendererContext};
const copy={zh:{fallback:'内容预览',unavailable:'此内容暂未提供专用预览',details:'技术详情',truncated:'仅显示部分技术信息',catalog:'需求工具',tools:'项',project:'项目范围',directory:'目录信息',empty:'目录中暂无工具',manage:'管理工具与技能',manageFailed:'管理界面暂未打开，请重试',skills:'配套技能',returned:'已返回',saved:'已保存',applied:'已写入',unknown:'待核对',failed:'未完成',readonly:'只读'},en:{fallback:'Content preview',unavailable:'A dedicated preview is not available',details:'Technical details',truncated:'Technical details are abbreviated',catalog:'Planning tools',tools:'items',project:'Project scope',directory:'Directory information',empty:'No tools in this directory',manage:'Manage tools and skills',manageFailed:'Could not open tool management. Try again',skills:'Related skills',returned:'Returned',saved:'Saved',applied:'Written',unknown:'Needs review',failed:'Incomplete',readonly:'Read only'}}as const;
const tools:Record<PlanningToolId,{zh:[string,string];en:[string,string]}>= {
 review_sources:{zh:['资料与来源','检查本轮资料与来源覆盖。'],en:['Sources','Review selected material and source coverage.']},
 validate_network:{zh:['任务依赖','检查节点、依赖关系与环。'],en:['Dependencies','Check task nodes, dependencies and cycles.']},
 acceptance_coverage:{zh:['交付与验收','检查任务的交付和验收条目。'],en:['Acceptance','Check deliverables and acceptance criteria.']},
 pending_questions:{zh:['待确认事项','查看缺口、影响与已给答复。'],en:['Open questions','Review gaps, affected tasks and answers.']},
 prompt_preview:{zh:['执行说明','查看逐项任务的执行说明。'],en:['Task instructions','Review instructions for each task.']},
 planning_templates:{zh:['工作模板','查看规格与澄清模板。'],en:['Templates','Browse specification and clarification templates.']}
};
const skills:Record<string,{zh:string;en:string}>={'source-review':{zh:'来源审阅',en:'Source review'},'clarify-gaps':{zh:'缺口澄清',en:'Gap clarification'},'task-decomposition':{zh:'任务拆解',en:'Task breakdown'},'acceptance-review':{zh:'验收复核',en:'Acceptance review'},'report-handoff':{zh:'报告交接',en:'Report handoff'}};
const language=()=>uiLocale().startsWith('en')?'en':'zh';
function Diagnostic(props:{value:unknown;identity:string}){
 const [open,setOpen]=createSignal(false),value=createMemo(()=>open()?boundedArtifactDiagnostic(props.value):undefined);let basis=props.identity;
 createEffect(()=>{const next=props.identity;if(next!==basis){basis=next;setOpen(false);}});
 return <details class="as-artifact-diagnostic" open={open()} onToggle={event=>setOpen(event.currentTarget.open)}><summary>{copy[language()].details}</summary><Show when={value()}>{result=><><pre tabIndex={0} aria-label={copy[language()].details}>{result().text}</pre><Show when={result().truncated}><small>{copy[language()].truncated}</small></Show></>}</Show></details>;
}
export function ArtifactSummary(props:Props){
 const summary=createMemo(()=>artifactSummary(props.artifact.data));
 const status=()=>{const value=summary().status,text=copy[language()];return value&&({RETURNED:text.returned,SAVED:text.saved,APPLIED:text.applied,UNKNOWN:text.unknown,UNKNOWN_OUTCOME:text.unknown,FAILED:text.failed,CANCELLED:text.failed}as Record<string,string>)[value];};
 return <section class="as-artifact-summary"><header><h2>{summary().title??copy[language()].fallback}</h2><Show when={status()}><span class="as-artifact-summary-status">{status()}</span></Show></header><p>{summary().summary??copy[language()].unavailable}</p><Diagnostic value={props.artifact.data} identity={JSON.stringify([props.artifact.type,props.artifact.artifactId,props.artifact.revision,props.artifact.contentHash])}/></section>;
}
export function PlanningCapabilitiesSummary(props:Props){
 const catalog=createMemo(()=>planningCapabilitySummary(props.artifact.data)),[busy,setBusy]=createSignal(false),[error,setError]=createSignal(false);let alive=true;onCleanup(()=>{alive=false;});
 const manage=async()=>{if(busy())return;setBusy(true);setError(false);try{await props.context.dispatch('capabilities.manage',props.artifact,{});}catch{if(alive)setError(true);}finally{if(alive)setBusy(false);}};
 return <Show when={catalog()} fallback={<ArtifactSummary {...props}/>}>{value=><section class="as-artifact-summary as-planning-capabilities"><header><h2>{copy[language()].catalog}</h2><span class="as-artifact-summary-status">{value().tools.length} {copy[language()].tools}</span></header><div class="as-artifact-summary-scope"><span>{copy[language()].directory}</span><Show when={value().projectScoped}><span>{copy[language()].project}</span></Show></div>
   <div class="as-planning-capability-grid"><For each={value().tools}>{tool=>{const publicCopy=()=>tools[tool.id as PlanningToolId]?.[language()];return <article class="as-planning-capability"><span class="as-planning-capability-symbol" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M7 4h10v16H7zM10 8h4M10 12h4M10 16h3"/></svg></span><div><h3 title={publicCopy()?.[0]??tool.title}>{publicCopy()?.[0]??tool.title}</h3><p title={publicCopy()?.[1]??tool.description}>{publicCopy()?.[1]??tool.description}</p></div></article>;}}</For></div>
   <Show when={!value().tools.length}><p>{copy[language()].empty}</p></Show><footer><button type="button" disabled={busy()} onClick={()=>void manage()}>{copy[language()].manage}<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m7 4 6 6-6 6"/></svg></button><Show when={error()}><p role="alert">{copy[language()].manageFailed}</p></Show></footer>
   <Show when={value().skills.length}><details class="as-artifact-skill-details"><summary>{copy[language()].skills} · {value().skills.length}</summary><ul><For each={value().skills}>{skill=><li>{skills[skill]?.[language()]??skill}</li>}</For></ul></details></Show><Diagnostic value={props.artifact.data} identity={JSON.stringify([props.artifact.type,props.artifact.artifactId,props.artifact.revision,props.artifact.contentHash])}/>
  </section>}</Show>;
}
