import {createEffect,createSignal,For,onCleanup,Show} from 'solid-js';
import {
 stageApi,type CoverageActionProof,type CoverageCriterion,type CoverageImplementationKind,
 type CoverageImplementationLink,type CoverageQuery,type CoverageReport,type CoverageResult,type CoverageTarget,
} from './stage-workflows';
import {WorkflowIcon} from './WorkflowIcon';

type Tr=(zh:string,en:string)=>string;
export type CoverageReading={expanded:string[];link?:string;top:number};
const implementationGroups:Record<CoverageImplementationKind,[string,string]> = {
 alternatives:['三级方案','Detailed decisions'],design:['详细设计','Detailed design'],development:['开发回执','Implementation receipts'],
};
const linkStatus:Record<CoverageImplementationLink['status'],[string,string]> = {
 approved:['已审核','Approved'],review:['待审核','Awaiting approval'],
 registered:['实现已登记','Implementation registered'],
 registered_review:['实现已登记，待审核','Implementation registered, awaiting approval'],
 unverified:['缺少可核对的实现回执','Implementation receipts missing or unverified'],
};
const proofStatus:Record<CoverageActionProof['proof'],[string,string]>={
 missing:['缺少对应回执','Matching receipt missing'],unconfirmed:['最新回执待核对','Latest receipt unconfirmed'],
 stale:['测试输入已变化','Test inputs changed'],failed:['动作执行失败','Action failed'],
 blocked:['动作执行受阻','Action blocked'],verified:['动作回执已核验','Action receipt verified'],
};
const scenarioPaths:Record<string,[string,string]>={
 normal:['正常','Normal'],boundary:['边界','Boundary'],error:['异常','Error'],recovery:['恢复','Recovery'],
};

export default function WorkflowCoverage(p:{
 origin:CoverageQuery;revision:number;t:Tr;onTarget:(target:CoverageTarget,reading:CoverageReading)=>void;
 error?:string;restore?:CoverageReading;
}){
 const [report,setReport]=createSignal<CoverageReport>();
 const [error,setError]=createSignal('');
 const [retry,setRetry]=createSignal(0);
 let root!:HTMLDivElement;
 let restored=false;
 createEffect(()=>{
  const origin=p.origin;
  void p.revision;
  retry();
  const control=new AbortController();
  setError('');
  void stageApi.coverage(origin,control.signal).then(value=>{
   if(!control.signal.aborted)setReport(previous=>JSON.stringify(previous)===JSON.stringify(value)?previous:value);
  }).catch(reason=>{
   if(control.signal.aborted)return;
   setError(reason instanceof Error?reason.message:String(reason));
  });
  onCleanup(()=>control.abort());
 });
 createEffect(()=>{
  const reading=p.restore;
  if(!report()||!reading||restored)return;
  restored=true;
  queueMicrotask(()=>{
   if(!root.isConnected)return;
   const body=root.closest<HTMLElement>('.wf-dialog-body');
   if(body)body.scrollTop=reading.top;
   const button=[...root.querySelectorAll<HTMLButtonElement>('[data-coverage-link]')]
    .find(element=>element.dataset.coverageLink===reading.link);
   if(button){
    button.focus({preventScroll:true});
    const frame=body?.getBoundingClientRect(),target=button.getBoundingClientRect();
    if(frame&&(target.top<frame.top||target.bottom>frame.bottom))button.scrollIntoView({block:'nearest',behavior:'instant'});
   }
  });
 });
 const status:Record<CoverageResult['status'],[string,string]>={
  passed:['已核验通过','Verified passed'],unverified:['通过声明待核验','Reported pass needs verification'],
  reported_failed:['报告失败','Reported failure'],blocked:['执行受阻','Blocked'],not_executed:['待执行','Not executed'],
 };
 function resultLabel(result:CoverageResult){
  if(result.status==='passed'&&result.basis==='manual')return p.t('人工实测通过','Manual observation passed');
  if(result.status==='passed'&&result.basis==='mixed')return p.t('人工与自动检查均通过','Manual and automated checks passed');
  return p.t(...status[result.status]);
 }
 function Target(props:{value:CoverageTarget;link?:string}){
  const link=()=>props.link??JSON.stringify([props.value.stage_session_id,props.value.operation_id,props.value.row_id]);
  return <button class="wf-coverage-target" data-coverage-link={link()} type="button"
   onClick={()=>p.onTarget(props.value,{
    expanded:[...root.querySelectorAll<HTMLDetailsElement>('details[data-coverage-group][open]')]
      .map(element=>element.dataset.coverageGroup!),
    link:link(),top:root.closest<HTMLElement>('.wf-dialog-body')?.scrollTop??0,
   })}>
   <span><span class="wf-index-id">{props.value.row_id}</span><strong>{props.value.title}</strong>
    <small>{props.value.stage_name}<Show when={report()?.historical||props.value.version>1}> · v{props.value.version}</Show></small>
   </span><WorkflowIcon name="arrow" size={16}/>
  </button>;
 }
 function Implementation(props:{row:string;value:NonNullable<CoverageReport['requirements'][number]['implementation']>}){
  return <div class="wf-coverage-implementation">
   <For each={Object.keys(implementationGroups) as CoverageImplementationKind[]}>{kind=>{
    const group=()=>JSON.stringify([props.row,kind]);
    return <Show when={props.value[kind]}>{links=>
     <details data-coverage-group={group()} open={p.restore?.expanded.includes(group())||undefined}>
      <summary>
       <WorkflowIcon name="chevron" size={14}/><span>{p.t(...implementationGroups[kind])}</span>
       <small>{links().length
        ? links().some(link=>link.status==='unverified') ? p.t('有回执待核对','Receipts need verification')
          : p.t(`${links().length} 条关联`,`${links().length} linked`)
        : p.t('暂无关联','No links yet')}</small>
      </summary>
      <Show when={links().length} fallback={<p class="wf-coverage-caption">{p.t('此版本下还没有可定位的成果。','This version has no traceable output yet.')}</p>}>
       <ul><For each={links()}>{link=><li>
        <Target value={link} link={JSON.stringify([group(),link.stage_session_id,link.row_id,link.basis_ids])}/>
        <p class="wf-coverage-caption">{p.t(...linkStatus[link.status])}
         <Show when={link.basis_ids?.length}> · {p.t('依据','Basis')} {link.basis_ids!.join('、')}</Show>
        </p>
       </li>}</For></ul>
      </Show>
     </details>
    }</Show>;
   }}</For>
  </div>;
 }
 function Criteria(props:{row:string;value:CoverageCriterion[]}){
  return <section class="wf-coverage-criteria">
   <h4>{p.t('逐条验收','Acceptance criteria')}</h4>
   <For each={props.value}>{criterion=>{
    const group=JSON.stringify([props.row,'criterion',criterion.index]);
    const verified=()=>criterion.actions.filter(action=>
     action.results.length>0&&action.results.every(result=>result.proof==='verified')).length;
    return <details data-coverage-group={group} open={p.restore?.expanded.includes(group)||undefined}>
     <summary>
      <WorkflowIcon name="chevron" size={14}/>
      <span class="wf-criterion-name"><span class="wf-index-id">{criterion.index}</span>{criterion.text}</span>
      <small>{criterion.actions.length===0?p.t('缺少动作','Action missing')
       : report()?.historical?p.t(`${criterion.actions.length} 个关联动作`,`${criterion.actions.length} linked actions`)
       : p.t(`${verified()}/${criterion.actions.length} 动作已核验`,`${verified()}/${criterion.actions.length} actions verified`)}</small>
     </summary>
     <Show when={criterion.actions.length} fallback={<p class="wf-coverage-caption wf-criterion-empty">
      {p.t('此标准尚无可唯一定位的用例动作。','No uniquely identifiable case action is linked to this criterion.')}
     </p>}>
      <ul class="wf-criterion-actions"><For each={criterion.actions}>{action=>{
       const prefix=[group,action.case.stage_session_id,action.case.row_id,action.id];
       return <li>
        <p class="wf-criterion-action-title"><strong>{action.id}</strong>
         <small>{action.case.approved?p.t('用例已审核','Case approved'):p.t('用例待审核','Case awaiting approval')}</small></p>
        <Target value={action.case} link={JSON.stringify([...prefix,'case'])}/>
        <Show when={action.scenarios.length} fallback={<p class="wf-coverage-caption">{p.t('暂无可定位的设计场景','No traceable design scenario yet')}</p>}>
         <ul><For each={action.scenarios}>{scenario=><li>
          <Target value={scenario} link={JSON.stringify([...prefix,'scenario',scenario.stage_session_id,scenario.row_id,scenario.scenario_id])}/>
          <p class="wf-coverage-caption">{scenario.scenario_id} · {p.t(...(scenarioPaths[scenario.path_kind]??[scenario.path_kind,scenario.path_kind]))}
           {' · '}{p.t(...linkStatus[scenario.status])}</p>
          <p class="wf-criterion-condition">{scenario.condition}</p>
         </li>}</For></ul>
        </Show>
        <Show when={action.results.length} fallback={<p class="wf-coverage-caption">{p.t('暂无对应测试结果','No matching test result yet')}</p>}>
         <ul><For each={action.results}>{result=><li>
          <Target value={result} link={JSON.stringify([...prefix,'result',result.stage_session_id,result.row_id])}/>
          <p class="wf-coverage-result-state" data-status={result.proof==='verified'?'passed':result.proof}>
           {p.t(...proofStatus[result.proof])}</p>
          <Show when={result.receipt_id}><p class="wf-criterion-receipt">
           <span>{p.t('回执','Receipt')} <code>{result.receipt_id}</code></span>
           <Show when={result.at}>{at=><time datetime={at()}>{new Date(at()).toLocaleString()}</time>}</Show>
          </p></Show>
         </li>}</For></ul>
        </Show>
       </li>;
      }}</For></ul>
     </Show>
    </details>;
   }}</For>
  </section>;
 }
 return <div class="wf-coverage" ref={root}>
  <Show when={p.error||error()}><p class="wf-error" role="alert">{p.error||error()}
   <Show when={error()}><button type="button" onClick={()=>setRetry(n=>n+1)}>{p.t('重读覆盖','Reload coverage')}</button></Show></p></Show>
  <Show when={report()} fallback={<Show when={!error()}><p role="status">{p.t('正在读取需求覆盖…','Loading requirement coverage…')}</p></Show>}>{value=>
   <>
    <Show when={value().sources.length}><section class="wf-coverage-sources">
     <h3>{p.t('用户需求原文','Original requests')}</h3>
     <ul><For each={value().sources}>{source=><li><span class="wf-index-id">{source.id}</span>
      <p>{source.text??p.t('此版本只保存了来源编号。','This version recorded the source ID.')}</p></li>}</For></ul>
    </section></Show>
    <Show when={value().historical}><p class="wf-coverage-caption">{p.t('历史覆盖 · 按当时的审核与执行版本查看。','Historical coverage · Showing the versions originally reviewed and tested.')}</p></Show>
    <Show when={value().ambiguous}><p class="wf-coverage-caption" role="status">{p.t('部分关联的编号重复，待确认对应条目。','Some references have duplicate IDs and need clarification.')}</p></Show>
    <Show when={value().version_uncertain}><p class="wf-coverage-caption" role="status">{p.t('部分旧记录缺少可唯一核对的版本。','Some older records lack a uniquely identifiable version.')}</p></Show>
    <section>
     <h3>{p.t('需求落实与测试','Implementation and testing')}</h3>
     <Show when={value().requirements.length} fallback={<p class="wf-coverage-caption">{value().withdrawn?p.t('这条原始需求已撤回。','This request was withdrawn.'):p.t('当前没有可定位的包需求。','There are no linked requirements yet.')}</p>}>
      <ol class="wf-coverage-requirements"><For each={value().requirements}>{requirement=><li>
       <Target value={requirement}/>
       <p class="wf-coverage-caption">{requirement.approved?p.t('包需求已审核','Requirement approved'):p.t('包需求待审核','Requirement awaiting approval')}</p>
        <Show when={requirement.criteria?.length}><Criteria row={requirement.row_id} value={requirement.criteria!}/></Show>
        <Show when={requirement.implementation}>{link=><Implementation row={requirement.row_id} value={link()}/>}</Show>
        <Show when={requirement.cases.length} fallback={<p class="wf-coverage-caption">{p.t('当前没有可定位的验收用例。','There are no linked acceptance cases yet.')}</p>}>
         <ol class="wf-coverage-cases"><For each={requirement.cases}>{item=><li>
          <Target value={item}/>
          <p class="wf-coverage-caption">{item.approved?p.t('用例已审核','Case approved'):p.t('用例待审核','Case awaiting approval')}</p>
          <Show when={item.results.length} fallback={<p class="wf-coverage-caption">{p.t('当前没有对应测试结果。','There are no matching test results yet.')}</p>}>
           <ul class="wf-coverage-results"><For each={item.results}>{result=><li>
            <Target value={result}/><small class="wf-coverage-result-state" data-status={result.status}>{resultLabel(result)}</small>
           </li>}</For></ul>
          </Show>
         </li>}</For></ol>
        </Show>
      </li>}</For></ol>
     </Show>
    </section>
   </>
  }</Show>
 </div>;
}
