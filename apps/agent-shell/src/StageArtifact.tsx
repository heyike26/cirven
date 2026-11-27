import {batch,For,Show,createEffect,createMemo,createSignal,createUniqueId,onCleanup,onMount,type JSX} from 'solid-js';
import WorkflowDialog from './WorkflowDialog';
import {WorkflowIcon} from './WorkflowIcon';
import {TestBenchDetails} from './WorkflowTestBench';
import type {Annotation,CoverageSelector,ManualCheck,Row,RunStage,StageKind} from './stage-workflows';

export type Mark={target:string;anchor?:NonNullable<Annotation['anchor']>};
type Tr=(zh:string,en:string)=>string;
const fields:Record<string,[string,string]>={
  title:['名称','Title'],summary:['摘要','Summary'],lessons:['阶段经验','Lessons'],plugin_suggestions:['工具改进建议','Tool suggestions'],
  template_output:['模板输出','Template output'],template_edits:['模板填写内容','Template fields'],parent_id:['父级编号','Parent ID'],
  topic:['项目要素','Topic'],detail:['要点','Details'],category:['类别','Category'],source_ur:['用户需求','Source requests'],
  sub_description:['子需求描述','Subrequirement'],description:['描述','Description'],design_spec:['设计规格','Specifications'],
  constraints:['约束','Constraints'],remarks:['备注','Notes'],priority:['优先级','Priority'],acceptance:['通过标准','Acceptance criteria'],
  surfaces:['项目界面与设备','Project surfaces'],coverage:['专项覆盖','Coverage'],key_points:['提炼要点','Key points'],design_requirement:['设计需求','Design requirement'],requirement_ids:['覆盖的需求','Requirements'],
  dimensions:['判断维度','Evaluation criteria'],candidates:['候选方案','Candidates'],selected_id:['最终选择','Selected option'],
  rationale:['取舍理由','Rationale'],subcategory:['子类','Subtype'],content:['测试内容','Test content'],preconditions:['前置条件','Preconditions'],
  acceptance_refs:['通过标准对应动作','Acceptance checks'],coverage_refs:['专项对应动作','Specialty checks'],requirement_id:['需求编号','Requirement ID'],
  criterion:['标准序号','Criterion number'],action_id:['验收动作编号','Check ID'],
  behavior_scenarios:['设计场景','Design scenarios'],path_kind:['路径类型','Scenario type'],condition:['触发条件','Trigger condition'],
  input_pair:['正常与对照输入','Normal and contrast inputs'],normal_stdin:['正常标准输入','Normal standard input'],
  hardware_readback:['设备回读','Device readback'],device_id:['已审设备编号','Approved device ID'],
  device_id_pointer:['设备编号字段','Device ID field'],sample_time_pointer:['采样时间字段','Sample time field'],
  subject_path:['指定验收程序','Program to verify'],
  control_stdin:['对照标准输入','Contrast standard input'],control_expected_output:['对照预期输出','Expected contrast output'],
  control_exit_code:['对照预期退出码','Expected contrast exit code'],
  expected_output:['预期容器输出（包含换行）','Expected container output (including line breaks)'],
  method:['执行方式','Method'],steps:['测试步骤','Steps'],checks:['检查项','Checks'],expected:['预期结果','Expected result'],instruction:['实测操作','Observation procedure'],
  directory:['目录','Directory'],implementation_files:['必交实现文件','Required implementation files'],
  decision_ids:['采用方案','Decisions'],interfaces:['接口','Interfaces'],implementation:['实现','Implementation'],
  self_check:['自检记录','Self-check'],design_ids:['对应设计','Design references'],status:['状态','Status'],evidence_refs:['执行证据','Evidence'],
  case_id:['用例编号','Test case'],rework:['返工记录','Rework log'],path:['文件','File'],sha256:['SHA-256','SHA-256'],
  advantages:['优点','Advantages'],disadvantages:['缺点','Drawbacks'],evaluations:['评估','Evaluation'],name:['名称','Name'],at:['时间','Time'],
  build:['构建配置','Build configuration'],outputs:['构建输出','Build outputs'],actions:['验收动作','Checks'],id:['编号','ID'],server:['MCP 服务','MCP server'],tool:['工具','Tool'],arguments:['输入参数','Arguments'],pointer:['检查位置','Result path'],expected_json:['预期内容','Expected content'],argv:['运行命令','Command arguments'],image_id:['镜像标识','Image ID'],timeout_seconds:['超时（秒）','Timeout (seconds)'],requested_outputs:['成果物要求','Requested deliverables'],evidence_id:['证据编号','Evidence ID'],detail_text:['记录','Details']
};
const fieldValues:Record<string,[string,string]>={
 background:['项目背景','Project background'],development_constraints:['开发约束','Development constraints'],build_environment:['编译环境','Build environment'],runtime_environment:['执行环境','Runtime environment'],code_map:['代码图谱','Code map'],other_constraints:['其他约束','Other constraints'],
 functional:['功能需求','Functional'],nonfunctional:['非功能需求','Nonfunctional'],constraint:['约束条件','Constraint'],frontend:['前端页面','Frontend'],hardware:['硬件联动','Hardware integration'],runtime:['运行检查','Runtime'],layout:['页面布局','Layout'],motion:['动效','Motion'],interaction:['交互','Interaction'],accessibility:['无障碍','Accessibility']
};
const refKinds:Record<string,StageKind>={requirement_ids:'requirements',decision_ids:'alternatives',design_ids:'design',case_id:'cases'};
const checkMethods:Record<string,[string,string]>={
 text_equals:['核对文件正文','Compare file text'],sha256_equals:['核对文件摘要','Compare file hash'],json_valid:['校验 JSON','Validate JSON'],
 python_syntax:['校验 Python 语法','Check Python syntax'],mcp_response:['核对 MCP 响应','Check MCP response'],
 container_suite:['运行容器测试','Run container test'],manual_observation:['人工实测','Manual observation'],
};
const checkFieldOrder:Record<string,string[]>={
 action:['instruction','server','tool','arguments','path','subject_path','argv','image_id','timeout_seconds','pointer',
  'normal_stdin','expected','expected_json','expected_output','input_pair'],
 input_pair:['normal_stdin','control_stdin','control_expected_output','control_exit_code'],
};
export function rowForest(rows:Row[]){
  const ids=new Set(rows.map(r=>r.id));const children=new Map<string|null,Row[]>();
  for(const r of rows){const parent=typeof r.parent_id==='string'&&ids.has(r.parent_id)?r.parent_id:null;const list=children.get(parent)??[];list.push(r);children.set(parent,list);}
  return children;
}
const readable=(key:string)=>key.replace(/[_-]/g,' ');
export const stageFieldLabel=(key:string,t:Tr)=>fields[key]?t(...fields[key]):readable(key);
export function stageEventText(value:string,t:Tr){
 const name=value.toLowerCase();
 if(name==='manual_results_started')return t('已打开实测记录','Opened observation log');
 if(name==='manual_check_recorded')return t('已记录人工实测','Saved manual observation');
 if(name==='stop_requested')return t('已请求停止','Stop requested');
 if(name==='stopped')return t('已停止','Stopped');
 if(name==='unconfirmed')return t('结果未确认','Outcome unconfirmed');
 if(name==='stale')return t('需复测','Retest required');
 if(name==='connector_replied')return t('已收到工具回复','Tool replied');
 if(name==='connector_error')return t('工具返回错误','Tool reported an error');
 const words:Record<string,[string,string]>={supplement:['已添加补充要求','Follow-up saved'],withdraw_supplement:['已撤回补充要求','Follow-up withdrawn'],revised:['已创建修订','Revision created'],annotate:['已添加批注','Comment saved'],configure:['已保存本步配置','Stage settings saved'],note:['已添加笔记','Note added'],approve:['审核通过','Approved'],recover:['已核查执行状态','Execution checked'],activated:['已进入节点','Stage opened'],started:['开始执行','Execution started'],finished:['执行结束','Execution finished'],rework:['退回开发','Returned to development'],configured:['配置已更新','Configuration updated'],annotated:['已添加批注','Comment saved'],noted:['已添加笔记','Note saved'],ready:['待执行','Ready'],pending:['待开始','Pending'],running:['执行中','Running'],approved:['审核通过','Approved'],review:['待审核','In review'],needs_input:['待补充','Needs input'],failed:['执行失败','Failed'],completed:['已完成','Completed'],passed:['通过','Passed'],pass:['通过','Pass'],blocked:['阻塞','Blocked'],done:['已完成','Done'],implemented:['已实现','Implemented'],not_run:['未执行','Not run'],open:['待修改','Open'],applied:['已采用','Applied']};
 return words[name]?t(...words[name]):readable(value);
}
export function Values(p:{value:unknown;field:string;t:Tr;onRef?:(kind:StageKind,id:string)=>void;fileLink?:(id:string)=>string|undefined;selected?:string;evidence?:RunStage['verified_evidence']}){
 const label=(key:string)=>stageFieldLabel(key,p.t);
 const list=()=>Array.isArray(p.value)?p.value:[p.value];
 const fieldItems=()=>{
  const items=Object.entries(p.value as Record<string,unknown>);
  const order=checkFieldOrder[p.field];
  if(!order)return items;
  const position=(key:string)=>order.includes(key)?order.indexOf(key):order.length;
  return items.sort(([a],[b])=>position(a)-position(b));
 };
 if(['expected_output','normal_stdin','control_stdin','control_expected_output'].includes(p.field))return <pre class="wf-execution-output">{String(p.value??'')||p.t('空输入','Empty input')}</pre>;
 return <Show when={p.value!==undefined&&p.value!==null&&p.value!==''&&!(Array.isArray(p.value)&&!p.value.length)} fallback={<span class="wf-muted">—</span>}>
  <Show when={refKinds[p.field]} fallback={<Show when={Array.isArray(p.value)} fallback={<Show when={p.value&&typeof p.value==='object'} fallback={<span>{p.field==='status'?stageEventText(String(p.value),p.t):['topic','category','surfaces','coverage'].includes(p.field)&&fieldValues[String(p.value)]?p.t(...fieldValues[String(p.value)]):String(p.value??'')}</span>}><dl class="wf-subfields"><For each={fieldItems()}>{([key,val])=><><dt>{label(key)}</dt><dd><Values value={val} field={key} t={p.t} onRef={p.onRef} fileLink={p.fileLink} evidence={p.evidence}/></dd></>}</For></dl></Show>}>
   <ul class="wf-values"><For each={list()}>{item=>{
    if(item&&typeof item==='object'&&p.field==='coverage_refs'){
     const binding=item as Record<string,unknown>, category=String(binding.coverage);
     return <li>{fieldValues[category]?p.t(...fieldValues[category]):category} · <code>{String(binding.action_id)}</code></li>;
    }
    if(item&&typeof item==='object'&&p.field==='actions'){const action=item as Record<string,unknown>;
     const method=checkMethods[String(action.method)];return <li><details><summary>{method?p.t(...method):String(action.method)} · {String(action.id)}</summary><Values value={Object.fromEntries(Object.entries(action).filter(([k])=>!['id','method'].includes(k)))} field="action" t={p.t} onRef={p.onRef}/></details></li>;}
    if(item&&typeof item==='object'&&p.field==='candidates'){const candidate=item as Row;return <li><details><summary><span>{candidate.title}</span><Show when={candidate.id===p.selected}><small>{p.t('已选择','Selected')}</small></Show></summary><Values value={Object.fromEntries(Object.entries(candidate).filter(([k])=>!['id','title'].includes(k)))} field="candidate" t={p.t} onRef={p.onRef}/></details></li>;}
    if(typeof item==='string'&&p.field==='evidence_refs'){
      const receipt=p.evidence?.find(r=>r.id===item);
      if(receipt){
        const names:Record<string,[string,string]>={file:['文件版本','File version'],check:['测试检查','Test check'],design_item:['设计项完成记录','Design completion'],build:['构建记录','Build receipt']};
        const label=receipt.path||p.t(...(names[receipt.kind]??['执行记录','Execution record']));
        const href=p.fileLink?.(item);
        return <li><details class="wf-receipt"><summary>{label}</summary>
          <code>{receipt.id}</code><Show when={receipt.sha256}><code>{receipt.sha256}</code></Show>
          <Show when={href}>{url=><a href={url()} download="">{p.t('下载文件','Download file')}</a>}</Show></details></li>;
      }
    }
    const link=typeof item==='string'&&p.field==='evidence_refs'?p.fileLink?.(item):null;
    return <li><Show when={link} fallback={<Values value={item} field={['surfaces','coverage'].includes(p.field)?p.field:'value'} t={p.t} onRef={p.onRef} fileLink={p.fileLink} evidence={p.evidence}/>}>{href=><a href={href()} download="">{String(item)} · {p.t('下载','Download')}</a>}</Show></li>;
   }}</For></ul>
  </Show>}>
   <div class="wf-ref-links"><For each={list()}>{id=><Show when={p.onRef} fallback={<span>{String(id)}</span>}>
    <button type="button" onClick={()=>p.onRef?.(refKinds[p.field],String(id))}>{String(id)} ↗</button>
   </Show>}</For></div>
  </Show>
 </Show>;
}
function Markable(p:{id:string;field:string;value:unknown;t:Tr;children:import('solid-js').JSX.Element;marks:Annotation[];marking:boolean;onMark:(m:Mark)=>void}){
 const [points,setPoints]=createSignal<number[][]>([]);let pointer:number|undefined;
 const position=(ev:PointerEvent,e:HTMLElement)=>{const rect=e.getBoundingClientRect();return [Math.max(0,Math.min(1,(ev.clientX-rect.left)/Math.max(1,rect.width))),Math.max(0,Math.min(1,(ev.clientY-rect.top)/Math.max(1,rect.height)))].map(v=>Math.round(v*1000)/1000);};
 const lines=()=>[...p.marks.filter(a=>a.status==='open'&&a.target===p.id&&a.anchor?.field===p.field&&a.anchor.points?.length).map(a=>a.anchor!.points!),points()];
 return <div class="wf-markable" data-field={p.field} data-drawing={p.marking} onMouseUp={ev=>{
  if(p.marking)return;const s=window.getSelection();if(s&&!s.isCollapsed&&s.toString().trim()&&s.anchorNode&&s.focusNode&&ev.currentTarget.contains(s.anchorNode)&&ev.currentTarget.contains(s.focusNode))p.onMark({target:p.id,anchor:{kind:'quote',quote:s.toString(),field:p.field}});
 }} onPointerDown={ev=>{if(!p.marking||ev.button!==0||pointer!==undefined)return;pointer=ev.pointerId;ev.currentTarget.setPointerCapture(pointer);setPoints([position(ev,ev.currentTarget)]);}}
 onPointerMove={ev=>{if(pointer===ev.pointerId&&points().length<256)setPoints(old=>[...old,position(ev,ev.currentTarget)]);}}
 onPointerCancel={()=>{pointer=undefined;setPoints([]);}}
 onPointerUp={ev=>{if(pointer!==ev.pointerId)return;const final=[...points(),position(ev,ev.currentTarget)].slice(0,256);pointer=undefined;setPoints([]);p.onMark({target:p.id,anchor:{kind:'ink',field:p.field,points:final}});}}>
  {p.children}
  <svg class="wf-ink" aria-hidden="true" viewBox="0 0 1 1" preserveAspectRatio="none"><For each={lines().filter(x=>x.length>1)}>{line=><polyline points={line.map(x=>x.join(',')).join(' ')} vector-effect="non-scaling-stroke"/>}</For></svg>
 </div>;
}
type ArtifactProps={
 stage:Pick<RunStage,'artifact'|'source_urs'|'annotations'|'verified_evidence'|'operation_id'|'test_input_issues'>;kind:StageKind;t:Tr;marking:boolean;editable:boolean;onMark?:(mark:Mark,source?:HTMLElement)=>void;
 commentable?:boolean;commentTarget?:string;
 onRef?:(kind:StageKind,id:string)=>void;fileLink?:(id:string)=>string|undefined;deliveryLink?:(row:Row)=>string|undefined;
 onCoverage?:(selector:CoverageSelector,id:string,opener:HTMLButtonElement)=>void;
 approvedCases?:Row[];
 readScenarioBasis?:(row:string,signal:AbortSignal)=>Promise<ScenarioBasis>;
};
type ScenarioBasis={cases:Row[];requirements:Row[]};
type DesignScenario={id:string;path_kind:'normal'|'boundary'|'error'|'recovery';condition:string;
 requirement_id:string;criterion:number;case_id:string;action_id:string};
const stageFields:Partial<Record<StageKind,string[]>>={
 init:['detail','topic'],
 requirements:['description','sub_description','acceptance','design_spec','constraints','source_ur','category','surfaces','remarks'],
 alternatives:['key_points','design_requirement','requirement_ids','dimensions','candidates','rationale'],
 cases:['requirement_ids','content','preconditions','method','steps','checks','expected','actions','coverage_refs','coverage','category','subcategory'],
 design:['description','interfaces','implementation','implementation_files','self_check','requirement_ids','decision_ids','build'],
 development:['status','design_ids','self_check','evidence_refs'],
 testing:['case_id','status','evidence_refs','rework'],
 delivery:['path','description','sha256']
};
const indexLabels:Partial<Record<StageKind,[string,string]>>={
 requirements:['需求目录','Requirements'],alternatives:['需求层级','Requirement tree'],cases:['用例目录','Test cases'],
 design:['设计目录','Design files'],development:['开发项','Development items'],testing:['测试结果','Test results']
};
function hasValue(value:unknown){return value!=null&&value!==''&&(!Array.isArray(value)||value.length>0);}
function directoryParts(row:Row){return String(row.directory??'').split(/[\\/]/).filter(part=>part&&part!=='.');}

export function WorkflowReadingDetail(p:{
 title:string;label:string;basis:string;t:Tr;source?:boolean;size?:'regular';children:JSX.Element
}){
 const [opened,setOpened]=createSignal<string>();
 const open=()=>opened()!==undefined&&opened()===p.basis;
 createEffect(()=>{if(opened()!==undefined&&!open())setOpened(undefined);});
 return <>
  <button type="button" class="wf-reading-trigger" data-read-source={p.source||undefined} aria-haspopup="dialog"
   onClick={event=>{event.currentTarget.focus({preventScroll:true});setOpened(p.basis);}}>
   <WorkflowIcon name="file" size={15}/><span>{p.label}</span>
  </button>
  <WorkflowDialog open={open()} size={p.size} title={p.title} locale={p.t('zh-CN','en')} onClose={()=>setOpened(undefined)}>
   <Show when={open()}><div class="wf-reading-detail">{p.children}</div></Show>
  </WorkflowDialog>
 </>;
}

function DesignFiles(p:{paths:string[];t:Tr}){
 const [query,setQuery]=createSignal('');
 const visible=()=>p.paths.filter(path=>path.toLocaleLowerCase().includes(query().trim().toLocaleLowerCase()));
 return <div class="wf-design-files">
  <Show when={p.paths.length>8}><input type="text" role="searchbox" inputmode="search" aria-label={p.t('搜索路径','Search paths')}
   placeholder={p.t('搜索路径','Search paths')} value={query()} onInput={e=>setQuery(e.currentTarget.value)}/></Show>
  <Show when={visible().length} fallback={<p role="status">{p.t('没有匹配的文件路径','No matching file paths')}</p>}>
   <ul><For each={visible()}>{path=><li><code>{path}</code></li>}</For></ul>
  </Show>
 </div>;
}

function DesignScenarios(p:{items:DesignScenario[];read:(signal:AbortSignal)=>Promise<ScenarioBasis>;t:Tr}){
 const [record,setRecord]=createSignal<ScenarioBasis>(),[failed,setFailed]=createSignal(false),[retry,setRetry]=createSignal(0);
 createEffect(()=>{
  retry();
  const access=new AbortController();
  setFailed(false);
  void p.read(access.signal).then(data=>{if(!access.signal.aborted)setRecord(data);})
   .catch(()=>{if(!access.signal.aborted)setFailed(true);});
  onCleanup(()=>access.abort());
 });
 const paths:Record<DesignScenario['path_kind'],[string,string]>={
  normal:['正常','Normal'],boundary:['边界','Boundary'],error:['异常','Error'],recovery:['恢复','Recovery'],
 };
 return <Show when={record()} fallback={<Show when={failed()} fallback={<p role="status">{p.t('正在核对已审依据…','Reading approved references…')}</p>}>
  <p role="alert">{p.t('已审依据读取失败。','Could not read the approved references.')}</p>
  <button type="button" class="wf-link" onClick={()=>setRetry(n=>n+1)}>{p.t('重新读取','Read again')}</button>
 </Show>}>{basis=><ul class="wf-design-scenarios">
  <For each={p.items}>{item=>{
   const matches=()=>basis().cases.filter(row=>row.id===item.case_id);
   const action=()=>matches().length===1&&Array.isArray(matches()[0].actions)
    ? (matches()[0].actions as Row[]).find(check=>check.id===item.action_id):undefined;
   const requirement=()=>basis().requirements.filter(row=>row.id===item.requirement_id);
   const criterion=()=>requirement().length===1&&Array.isArray(requirement()[0].acceptance)
    ?(requirement()[0].acceptance as string[])[item.criterion-1]:undefined;
   return <li><details>
    <summary><WorkflowIcon name="next" size={14}/><strong>{p.t(...paths[item.path_kind])}</strong>
     <span>{item.condition}</span></summary>
    <p class="wf-scenario-ref">{item.requirement_id} · {p.t(`标准 ${item.criterion}`,`Criterion ${item.criterion}`)}</p>
    <Show when={criterion()}><p>{criterion()}</p></Show>
    <Show when={action()} fallback={<p role="status">{p.t('此版本的已审动作暂不可读取。','The approved check for this version is unavailable.')}</p>}>{check=><>
     <p class="wf-scenario-ref">{item.case_id} / {item.action_id} · {checkMethods[String(check().method)]?p.t(...checkMethods[String(check().method)]):String(check().method)}</p>
     <Values field="action" value={Object.fromEntries(Object.entries(check()).filter(([key])=>!['id','method'].includes(key)))} t={p.t}/>
    </>}</Show>
   </details></li>;
  }}</For>
 </ul>}</Show>;
}

export default function StageArtifact(p:ArtifactProps){
 const artifact=()=>p.stage.artifact!;
 const [wanted,setWanted]=createSignal<string>(),[search,setSearch]=createSignal('');
 const [compact,setCompact]=createSignal(false);
 const [wideIndex,setWideIndex]=createSignal(true),[compactIndex,setCompactIndex]=createSignal(false);
 const indexId=createUniqueId();
 const indexOpen=()=>compact()?compactIndex():wideIndex();
 function readingChanged(){region.dispatchEvent(new Event('workflow-reading-change',{bubbles:true}));}
 function toggleIndex(){
  if(compact())setCompactIndex(!compactIndex());else setWideIndex(!wideIndex());
  readingChanged();
 }
 const rows=createMemo(()=>{
  const all=artifact().items;
  if(p.kind!=='testing')return all;
  const rank=(r:Row)=>['failed','blocked'].includes(String(r.status))?0:r.status==='passed'?2:1;
  return [...all].sort((a,b)=>rank(a)-rank(b));
 });
 const rowsById=createMemo(()=>new Map(rows().map(row=>[row.id,row])));
 const row=(id:string)=>rowsById().get(id)!;
 const current=()=>rowsById().get(wanted()??'')??rows()[0];
 const indexed=()=>!!indexLabels[p.kind]&&(rows().length>1||p.kind==='design'&&rows().some(r=>directoryParts(r).length>0));
 const tree=createMemo(()=>rowForest(rows()));
 const filtered=()=>rows().filter(r=>[r.id,r.title,String(r.directory??''),String(r.case_id??'')].join(' ').toLocaleLowerCase().includes(search().trim().toLocaleLowerCase()));
 let region!:HTMLElement;
 let indexRail:HTMLDivElement|undefined,indexTrigger:HTMLButtonElement|undefined;
 function choose(id:string,focus=false){
  if(!rows().some(r=>r.id===id))return;
  setWanted(id);if(compact())setCompactIndex(false);
  if(focus)queueMicrotask(()=>{
   if(!region.isConnected||wanted()!==id)return;
   const title=Array.from(region.querySelectorAll<HTMLElement>('[data-workflow-row]')).find(el=>el.dataset.workflowRow===id)?.querySelector<HTMLElement>('[data-review-title]');
   if(title){title.focus({preventScroll:true});title.scrollIntoView({block:'nearest',inline:'nearest',behavior:'instant'});readingChanged();}
  });
 }
 function reveal(event:Event){
  const target=(event as CustomEvent<{id?:string;focus?:boolean;indexOpen?:boolean;search?:string}>).detail;
  if(!target?.id)return;
  setSearch(target.search??'');
  choose(target.id,target.focus!==false);
  if(!compact()&&target.indexOpen!==undefined)setWideIndex(target.indexOpen);
 }
 onMount(()=>{
  region.addEventListener('workflow-reveal-row',reveal);
  const update=()=>{
   const width=region.getBoundingClientRect().width;
   const nextCompact=width<=550;
   if(!width||nextCompact===compact())return;
   const returnFocus=indexRail?.contains(document.activeElement);
   batch(()=>{
    setCompactIndex(false);
    setCompact(nextCompact);
   });
   if(returnFocus)indexTrigger?.focus({preventScroll:true});
  };
  const observer=new ResizeObserver(update);observer.observe(region);update();
  onCleanup(()=>observer.disconnect());
 });
 onCleanup(()=>region?.removeEventListener('workflow-reveal-row',reveal));
 const marker=(mark:Mark)=>{if(p.editable)p.onMark?.(mark);};
 const outlineIndex=()=>p.kind==='requirements'||p.kind==='alternatives';
 function IndexRow(props:{row:Row}){
  // Generated “Item A-1” is a fallback label. Preserve the original title in the record and accessible name.
  const repeated=()=>outlineIndex()&&props.row.title.trim().normalize('NFKC').replace(/^(?:条目\s*:?\s*|item(?:\s+|\s*:\s*))/i,'').toLowerCase()===props.row.id.trim().normalize('NFKC').toLowerCase();
  const name=()=>repeated()?props.row.title:props.row.id+' '+props.row.title;
  return <button type="button" data-workflow-target={props.row.id} aria-label={name()} aria-current={current()?.id===props.row.id?'true':undefined}
    onClick={()=>choose(props.row.id,true)}><span class="wf-index-id">{props.row.id}</span><Show when={!repeated()}><span class="wf-index-name">{props.row.title}</span></Show></button>;
 }
 function IndexItems(){
  return <>
   <Show when={rows().length>8}><input aria-label={p.t('筛选当前目录','Filter this index')} placeholder={p.t('按名称或编号筛选','Filter by name or ID')} type="search" value={search()} onInput={e=>setSearch(e.currentTarget.value)}/></Show>
   <Show when={p.kind==='alternatives'&&!search().trim()&&tree().get(null)?.length} fallback={<Show when={p.kind==='design'&&!search().trim()} fallback={<ul class="wf-object-tree"><For each={filtered().map(r=>r.id)} fallback={<li>{p.t('没有匹配项','No matches')}</li>}>{id=><li><IndexRow row={row(id)}/></li>}</For></ul>}>
    <Directory prefix={[]}/></Show>}><Branch parent={null} depth={0}/></Show>
  </>;
 }
 function Branch(props:{parent:string|null;depth:number}){
  return <ul class="wf-object-tree"><For each={tree().get(props.parent)?.map(r=>r.id)}>{id=><li><IndexRow row={row(id)}/><Show when={props.depth<2&&tree().has(id)}><Branch parent={id} depth={props.depth+1}/></Show></li>}</For></ul>;
 }
 function Directory(props:{prefix:string[]}){
  const matches=()=>rows().filter(r=>props.prefix.every((part,i)=>directoryParts(r)[i]===part));
  const children=()=>[...new Set(matches().map(r=>directoryParts(r)[props.prefix.length]).filter(Boolean))].sort();
  return <ul class="wf-object-tree wf-directories"><For each={matches().filter(r=>directoryParts(r).length===props.prefix.length).map(r=>r.id)}>{id=><li><IndexRow row={row(id)}/></li>}</For>
   <For each={children()}>{name=><li><details open={props.prefix.length===0||[...props.prefix,name].every((part,i)=>directoryParts(current()!)[i]===part)}><summary><WorkflowIcon name="folder" size={15}/>{name}</summary><Directory prefix={[...props.prefix,name]}/></details></li>}</For></ul>;
 }
 function Field(props:{row:Row;name:string}){
  const name=()=>props.name;
  const sources=()=>name()==='source_ur'&&Array.isArray(props.row.source_ur)?props.row.source_ur as string[]:[];
  const designFiles=()=>name()==='implementation_files'&&Array.isArray(props.row.implementation_files)?
   props.row.implementation_files as string[]:[];
  const availableSources=()=>sources().filter(id=>p.stage.source_urs?.[id]!==undefined);
  return <div class="wf-record-field" data-field-name={name()}><dt>{name()==='status'&&p.kind==='testing'
   ?p.t('执行时结论','Recorded outcome'):name()==='status'&&p.kind==='development'?
    p.t('开发报告状态','Reported implementation status'):stageFieldLabel(name(),p.t)}</dt>
   <dd><Markable id={props.row.id} field={name()} value={props.row[name()]} t={p.t} marking={p.marking&&p.editable} marks={p.stage.annotations} onMark={marker}>
    <Show when={designFiles().length} fallback={
     <Show when={name()==='status'&&p.kind==='development'&&props.row.status==='done'} fallback={
      <Values field={name()} value={props.row[name()]} selected={String(props.row.selected_id??'')} t={p.t} onRef={p.onRef} fileLink={p.fileLink} evidence={p.stage.verified_evidence}/>
     }>{p.t('已登记实现，行为由测试核验','Implementation reported. Behaviour is verified in testing.')}</Show>
    }>
     <WorkflowReadingDetail title={p.t('必交实现文件','Required implementation files')}
      label={p.t(`查看文件 · ${designFiles().length}`,`View files · ${designFiles().length}`)}
      size="regular" basis={JSON.stringify([p.stage.operation_id,props.row.id,designFiles()])} t={p.t}>
      <DesignFiles paths={designFiles()} t={p.t}/>
     </WorkflowReadingDetail>
    </Show>
   </Markable>
   <Show when={availableSources().length}>
    <WorkflowReadingDetail title={p.t('需求原文','Original requests')} label={p.t('查看需求原文','Read original requests')}
     basis={JSON.stringify([p.stage.operation_id,props.row.id])} t={p.t} source>
     <For each={availableSources()}>{id=><blockquote><div class="wf-source-actions">
      <span class="wf-index-id">{id}</span>
      <Show when={p.onCoverage}><button type="button" class="wf-link" data-coverage-source={id} onClick={event=>p.onCoverage?.('ur',id,event.currentTarget)}
       aria-label={p.t('查看来源覆盖','View source coverage')+' · '+id}>{p.t('查看覆盖','View coverage')}</button></Show>
      </div><p>{p.stage.source_urs?.[id]}</p></blockquote>}</For>
    </WorkflowReadingDetail>
   </Show></dd></div>;
 }
 function ItemOrb(props:{id:string}){
  const label=p.t('批注此项','Annotate item');
  return <Show when={p.commentable}><button class="wf-annotation-orb" type="button" aria-label={label} title={label} disabled={!p.editable}
   data-annotating={p.commentTarget===props.id} onClick={e=>p.onMark?.({target:props.id},e.currentTarget)}><WorkflowIcon name="message" size={17}/></button></Show>;
 }
 function RecordPane(props:{row:Row}){
  const r=()=>props.row;
  const testCase=()=>p.approvedCases?.find(c=>c.id===r().case_id);
  const testStatus=()=>r().status==='passed'&&p.stage.test_input_issues?.[String(r().case_id)]
   ?'stale':String(r().status??'');
  const allSpecialtiesListed=()=>{
   const {coverage,coverage_refs}=r();
   if(!Array.isArray(coverage)||!Array.isArray(coverage_refs))return false;
   const described=new Set(coverage_refs.map(ref=>ref.coverage));
   return coverage.every(category=>described.has(category));
  };
  const keys=()=>[...new Set([...(stageFields[p.kind]??[]),...Object.keys(r())])].filter(k=>
   !['id','title','level','parent_id','priority'].includes(k)&&!(k==='behavior_scenarios'&&p.readScenarioBasis)&&
   !(p.kind==='design'&&k==='directory')&&
   !(k==='coverage'&&allSpecialtiesListed())&&hasValue(r()[k]));
  const scenarios=()=>Array.isArray(r().behavior_scenarios)?r().behavior_scenarios as DesignScenario[]:[];
  const emptyKeys=()=>Object.keys(r()).filter(k=>!['id','title','level','parent_id','priority'].includes(k)&&
   !(k==='implementation_files'&&r().build)&&!hasValue(r()[k]));
  const choices=()=>Array.isArray(r().candidates)?r().candidates as Row[]:[];
  const selectedCandidate=()=>choices().find(c=>c.id===r().selected_id);
  const linkedFile=()=>p.kind==='delivery'?p.deliveryLink?.(r()):undefined;
  const checks=()=>p.stage.verified_evidence.filter(e=>e.kind==='check'&&Array.isArray(r().evidence_refs)&&(r().evidence_refs as string[]).includes(e.id)) as Array<Record<string,unknown>>;
  const observed=()=>p.stage.verified_evidence.filter((e):e is ManualCheck=>
   e.kind==='manual_check'&&Array.isArray(r().evidence_refs)&&(r().evidence_refs as string[]).includes(e.id));
  const initTopic=()=>fieldValues[String(r().topic)]?p.t(...fieldValues[String(r().topic)]):r().title;
  if(p.kind==='init')return <article class="wf-brief-point" data-workflow-row={r().id}>
    <h3 tabindex="-1" data-review-title>{initTopic()}</h3><div class="wf-brief-content"><Markable id={r().id} field="detail" value={r().detail} t={p.t} marking={p.marking&&p.editable} marks={p.stage.annotations} onMark={marker}>
     <Show when={r().title!==r().detail&&r().title!==initTopic()}><strong>{r().title}</strong></Show><p>{String(r().detail??r().title)}</p></Markable>
     <ItemOrb id={r().id}/></div>
    </article>;
  return <article class="wf-record-pane" data-workflow-row={r().id}>
    <header class="wf-record-title"><div><span class="wf-index-id">{r().id}</span><h3 tabindex="-1" data-review-title>{r().title}</h3></div>
     <div class="wf-record-actions"><Show when={r().priority!=null}><span class="wf-record-priority">{String(r().priority)}</span></Show>
     <Show when={linkedFile()}>{href=><a class="wf-file-download" href={href()} download=""><WorkflowIcon name="download" size={16}/>{p.t('下载成果物','Download deliverable')}</a>}</Show>
     <ItemOrb id={r().id}/></div></header>
    <Show when={p.kind==='design'&&r().directory}>
     <Markable id={r().id} field="directory" value={r().directory} t={p.t} marking={p.marking&&p.editable} marks={p.stage.annotations} onMark={marker}>
      <div class="wf-file-location" data-field-name="directory"><WorkflowIcon name="folder" size={15}/><span>{String(r().directory)}</span></div>
     </Markable>
    </Show>
    <Show when={p.kind==='testing'&&testCase()}>{c=><div class="wf-case-comparison"><div><strong>{p.t('用例预期','Expected from the case')}</strong><span>{String(c().expected??'—')}</span></div><div><strong>{p.t('执行结果','Execution result')}</strong><span data-test-status={testStatus()}>{stageEventText(testStatus(),p.t)}</span><For each={checks()}>{check=><div class="wf-check-result"><Show when={typeof check.path==='string'}><code>{String(check.path)}</code></Show><Show when={typeof check.detail==='string'&&check.detail}><p>{String(check.detail)}</p></Show></div>}</For>
     <Show when={observed().length}><small>{p.t('含人工实测记录','Includes manual observations')}</small></Show>
     <Show when={observed().some(record=>record.bench)}>
      <WorkflowReadingDetail title={p.t('人工实测工位','Manual test benches')} label={p.t('查看实测工位','View test benches')}
       basis={JSON.stringify(observed().map(record=>record.id))} t={p.t}>
       <For each={observed()}>{record=><Show when={record.bench}>{value=><section>
        <strong>{record.action_id}</strong><TestBenchDetails bench={value()} t={p.t}/>
       </section>}</Show>}</For>
      </WorkflowReadingDetail>
     </Show></div></div>}</Show>
    <dl class="wf-fields wf-record-fields"><For each={keys()}>{key=><Show when={key==='candidates'&&choices().length} fallback={
      <Show when={!(key==='selected_id'&&selectedCandidate())}><Field name={key} row={r()}/></Show>}>
      <div class="wf-record-field wf-decision-field"><dt>{p.t('候选方案','Candidates')}</dt><dd>
       <Markable id={r().id} field="candidates" value={choices()} t={p.t} marking={p.marking&&p.editable} marks={p.stage.annotations} onMark={marker}>
       <div class="wf-candidate-grid"><For each={choices()}>{c=><section class="wf-candidate" data-picked={c.id===r().selected_id}>
        <header><span class="wf-index-id">{c.id}</span><Show when={c.id===r().selected_id}><span><WorkflowIcon name="check" size={14}/>{p.t('已选择','Selected')}</span></Show></header>
        <h4>{c.title}</h4><dl class="wf-candidate-facts"><For each={Object.entries(c).filter(([k,v])=>!['id','title'].includes(k)&&hasValue(v))}>{([k,v])=><div><dt>{fields[k]?p.t(...fields[k]):readable(k)}</dt><dd><Values field={k} value={v} t={p.t} onRef={p.onRef}/></dd></div>}</For></dl>
       </section>}</For></div></Markable></dd></div></Show>}</For></dl>
    <Show when={emptyKeys().length}>
     <WorkflowReadingDetail title={p.t('未填写字段','Empty fields')} label={p.t('查看未填写字段','View empty fields')+' · '+emptyKeys().length}
      basis={JSON.stringify([p.stage.operation_id,r().id])} t={p.t}>
      <dl class="wf-fields wf-record-fields"><For each={emptyKeys()}>{k=><Field name={k} row={r()}/>}</For></dl>
     </WorkflowReadingDetail>
    </Show>
    <Show when={p.readScenarioBasis&&scenarios().length}>
     <WorkflowReadingDetail size="regular" title={p.t('设计场景','Design scenarios')}
      label={p.t('核对设计场景','Review design scenarios')+' · '+scenarios().length}
      basis={JSON.stringify([p.stage.operation_id,r().id])} t={p.t}>
      <DesignScenarios items={scenarios()} read={signal=>p.readScenarioBasis!(r().id,signal)} t={p.t}/>
     </WorkflowReadingDetail>
    </Show>
    <Show when={p.kind==='requirements'&&p.onCoverage}>
     <button type="button" class="wf-link wf-record-coverage" data-coverage-source={r().id} onClick={event=>p.onCoverage?.('or',r().id,event.currentTarget)}>{p.t('查看需求覆盖','View requirement coverage')}</button>
    </Show>
    <Show when={p.kind==='cases'&&(!Array.isArray(r().actions)||!(r().actions as unknown[]).length)}><small>{p.t('验收动作待确定','Checks still need to be specified')}</small></Show>
   </article>;
 }
 return <section ref={region} class="wf-artifact" aria-label={p.t('阶段交付物','Stage output')} data-stage-kind={p.kind}>
  <Markable id="summary" field="summary" value={artifact().summary} t={p.t} marking={p.marking&&p.editable} marks={p.stage.annotations} onMark={marker}><p class="wf-summary" tabindex="-1">{artifact().summary}</p></Markable>
  <Show when={rows().length}><Show when={indexed()} fallback={<div class="wf-record-stack"><For each={rows().map(r=>r.id)}>{id=><RecordPane row={row(id)}/>}</For></div>}>
   <div class="wf-artifact-browser" data-index-open={indexOpen()} data-compact={compact()}>
    <div ref={indexRail} class="wf-index-rail"><div class="wf-index-dock">
     <button ref={indexTrigger} type="button" class="wf-index-toggle" aria-controls={indexId} aria-expanded={indexOpen()} aria-haspopup={compact()?'dialog':undefined}
      onClick={event=>{event.currentTarget.focus({preventScroll:true});toggleIndex();}}>
      <WorkflowIcon name="list" size={16}/><span>{!compact()&&indexOpen()?p.t('收起目录','Hide index'):p.t('展开目录','Show index')}</span><span aria-hidden="true" class="wf-index-size">{rows().length}</span><WorkflowIcon name="chevron" size={14}/>
     </button>
     <Show when={compact()} fallback={<nav id={indexId} class="wf-artifact-index wf-index-items" classList={{'wf-outline-index':outlineIndex()}} hidden={!indexOpen()} aria-label={p.t(...indexLabels[p.kind]!)}>
      <div class="wf-index-caption">{p.t(...indexLabels[p.kind]!)}</div><IndexItems/>
     </nav>}>
      <WorkflowDialog open={indexOpen()} title={p.t(...indexLabels[p.kind]!)} size="regular" locale={p.t('zh-CN','en')}
       onClose={()=>{setCompactIndex(false);readingChanged();}}>
       <nav id={indexId} class="wf-artifact-index wf-index-items" classList={{'wf-outline-index':outlineIndex()}} aria-label={p.t(...indexLabels[p.kind]!)}>
        <IndexItems/>
       </nav>
      </WorkflowDialog>
     </Show>
    </div></div>
    <Show when={current()}>{r=><RecordPane row={r()}/>}</Show>
   </div></Show></Show>
  <Show when={artifact().template_output}>{v=><WorkflowReadingDetail title={p.t('按模板生成的文稿','Document in your template')}
   label={p.t('查看模板文稿','Read generated document')} basis={p.stage.operation_id??''} t={p.t}>
   <p class="wf-reading-text">{v()}</p>
  </WorkflowReadingDetail>}</Show>
 </section>;
}
