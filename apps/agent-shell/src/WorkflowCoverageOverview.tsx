import {createEffect,createMemo,createSignal,For,onCleanup,Show} from 'solid-js';
import {stageApi,type CoverageOverview,type CoverageOverviewRow,type CoveragePhase,type CoverageState,type Scope} from './stage-workflows';
import {WorkflowIcon} from './WorkflowIcon';

type Tr=(zh:string,en:string)=>string;
export type OverviewReading={
 filter:'gaps'|'all'|CoveragePhase;search:string;limit:number;top:number;focus?:string;
};
const PAGE_SIZE=32;
export const emptyOverviewReading=():OverviewReading=>({filter:'gaps',search:'',limit:PAGE_SIZE,top:0});
const phases:Record<CoveragePhase,[string,string]>={
 requirements:['需求','Requirements'],alternatives:['方案','Decisions'],design:['设计','Design'],
 development:['实现','Implementation'],cases:['用例','Cases'],testing:['测试','Results'],trace:['关联','Links'],
};
const status:Record<CoverageState,[string,string]>={
 unverified:['待核实','Unverified'],failed:['未通过','Failed'],unavailable:['不可读取','Unavailable'],
 stale:['待重核','Needs recheck'],missing:['缺记录','Missing'],review:['待审核','Needs approval'],covered:['已核对','Verified'],
};
const rowKey=(row:CoverageOverviewRow)=>JSON.stringify([row.node_id,row.stage_session_id,row.selector,row.row_id]);
const hasStages=(row:CoverageOverviewRow)=>Object.keys(row.states).some(key=>key!=='requirements'&&key!=='trace');
const needsAttention=(row:CoverageOverviewRow)=>!hasStages(row)||Object.values(row.states).some(state=>state!=='covered');

export default function WorkflowCoverageOverview(p:{
 scope:Scope;run:string;revision:number;t:Tr;restore:OverviewReading;
 onSelect:(item:CoverageOverviewRow,reading:OverviewReading)=>void;
}){
 const [report,setReport]=createSignal<CoverageOverview>();
 const [filter,setFilter]=createSignal(p.restore.filter);
 const [search,setSearch]=createSignal(p.restore.search);
 const [limit,setLimit]=createSignal(p.restore.limit);
 const [loading,setLoading]=createSignal(true);
 const [error,setError]=createSignal('');
 const [retry,setRetry]=createSignal(0);
 let root!:HTMLDivElement;
 let restored=false;
 const panel=()=>root.closest<HTMLElement>('.wf-dialog-body');
 createEffect(()=>{
  void p.revision;
  retry();
  const request=new AbortController();
  setLoading(true);
  setError('');
  void stageApi.coverageOverview(p.scope,p.run,request.signal).then(value=>{
   if(request.signal.aborted)return;
   setReport(value);
   setLoading(false);
  }).catch(reason=>{
   if(request.signal.aborted)return;
   setError(reason instanceof Error?reason.message:String(reason));
   setLoading(false);
  });
  onCleanup(()=>request.abort());
 });
 createEffect(()=>{
  if(!report()||restored)return;
  restored=true;
  const reading=p.restore;
  queueMicrotask(()=>{
   if(!root.isConnected)return;
   const scroll=panel();
   if(scroll)scroll.scrollTop=reading.top;
   const target=[...root.querySelectorAll<HTMLButtonElement>('button[data-coverage-overview-row]')]
    .find(button=>button.dataset.coverageOverviewRow===reading.focus);
   (reading.focus?(target??root.querySelector<HTMLInputElement>('input[type=search]')):undefined)?.focus({preventScroll:true});
  });
 });

 const attention=createMemo(()=>report()?.rows.filter(needsAttention).length??0);
 const sourceGroups=createMemo(()=>new Set(report()?.rows.map(row=>row.node_id)).size);
 const resultRows=createMemo(()=>{
  const query=search().trim().toLocaleLowerCase(), range=filter();
  return report()?.rows.filter(row=>{
   const inRange=range==='all'||(range==='gaps'?needsAttention(row):!!row.states[range]&&row.states[range]!=='covered');
   return inRange&&(!query||[row.row_id,row.title,row.stage_name].some(value=>value.toLocaleLowerCase().includes(query)));
  })??[];
 });

 function select(row:CoverageOverviewRow){
  p.onSelect(row,{filter:filter(),search:search(),limit:limit(),top:panel()?.scrollTop??0,focus:rowKey(row)});
 }
 return <div class="wf-coverage-overview" ref={root} aria-busy={loading()}>
  <div class="wf-overview-controls">
   <label><span>{p.t('范围','Scope')}</span><select value={filter()} onChange={event=>{
    setFilter(event.currentTarget.value as OverviewReading['filter']);setLimit(PAGE_SIZE);
   }}>
    <option value="gaps">{p.t('全部待核','Needs attention')}</option><option value="all">{p.t('全部需求','All requirements')}</option>
    <option value="requirements">{p.t('需求待核','Requirements to review')}</option>
    <For each={report()?.phases}>{phase=><option value={phase} selected={phase===filter()}>
     {p.t(`${phases[phase][0]}待核`,`${phases[phase][1]} to review`)}
    </option>}</For>
   </select></label>
   <label><span>{p.t('搜索需求','Find a requirement')}</span><input type="search" value={search()}
    placeholder={p.t('编号或名称','ID or title')} onInput={event=>{setSearch(event.currentTarget.value);setLimit(PAGE_SIZE);}}/></label>
  </div>
  <Show when={error()}><div class="wf-error" role="alert"><p>{error()}</p>
   <button type="button" onClick={()=>setRetry(value=>value+1)}>{p.t('重新读取','Retry')}</button>
  </div></Show>
  <div class="wf-overview-count" role="status">
   <Show when={report()} fallback={<Show when={loading()}>{p.t('正在核对当前阶段…','Checking current workflow…')}</Show>}>{summary=><>
    {p.t(`${attention()} 条待核 · ${summary().rows.length} 条需求`,`${attention()} need attention · ${summary().rows.length} requirements`)}
    <Show when={summary().pending_sources}> · {p.t(`${summary().pending_sources} 个需求阶段待产出`,`${summary().pending_sources} requirement stages awaiting output`)}</Show>
    <Show when={summary().unavailable_sources}> · {p.t(`${summary().unavailable_sources} 个需求阶段不可读取`,`${summary().unavailable_sources} requirement stages unavailable`)}</Show>
   </>}</Show>
  </div>
  <Show when={report()}>
   <Show when={resultRows().length} fallback={<p class="wf-overview-empty">{p.t('当前范围没有匹配的需求。','No requirements match this view.')}</p>}>
    <ul class="wf-overview-list"><For each={resultRows().slice(0,limit())}>{row=>{
     const gaps=()=>Object.entries(row.states).filter(([,value])=>value!=='covered') as [CoveragePhase,CoverageState][];
     return <li><button class="wf-overview-row" type="button" data-coverage-overview-row={rowKey(row)}
      disabled={loading()||!!error()||(report()?.revision??0)<p.revision} onClick={()=>select(row)}>
      <span class="wf-overview-row-main">
       <span class="wf-overview-id">{row.row_id}<Show when={sourceGroups()>1}> · {row.stage_name}</Show></span>
       <strong>{row.title}</strong>
       <span class="wf-overview-gaps">
        <For each={gaps()}>{([phase,state])=><span data-state={state}>{p.t(...phases[phase])} · {p.t(...status[state])}</span>}</For>
        <Show when={row.selector==='or'&&!hasStages(row)}>
         <span data-state="missing">{p.t('暂无可核对的下游阶段','No downstream stages to verify')}</span>
        </Show>
        <Show when={!needsAttention(row)}><span data-state="covered"><WorkflowIcon name="check" size={13}/>
         {p.t('已核对所列阶段','Configured stages verified')}</span></Show>
       </span>
      </span><WorkflowIcon name="next" size={16}/>
     </button></li>;
    }}</For></ul>
    <Show when={limit()<resultRows().length}><button type="button" class="wf-link wf-overview-more"
     onClick={()=>setLimit(value=>value+PAGE_SIZE)}>{p.t('显示更多','Show more')}</button></Show>
   </Show>
  </Show>
 </div>;
}
