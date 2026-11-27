import {createSignal,For,onCleanup,onMount,Show} from 'solid-js';
import {stageApi,type Definition,type DefinitionImprovement,type DefinitionImprovementSource,type Scope} from './stage-workflows';
import {WorkflowIcon} from './WorkflowIcon';
import './workflow-definition-history.css';

type Tr=(zh:string,en:string)=>string;
const categories={skills:'Skill',tools:'Tool',mcp:'MCP'};
const states={matches:['配置一致','Matches this version'],changed:['配置已变更','Configuration changed'],
 node_removed:['节点已移除','Stage removed']} as const;

export default function WorkflowDefinitionHistory(p:{scope:Scope;definition:Definition;t:Tr;
 onOpenSource:(source:DefinitionImprovementSource)=>Promise<void>}){
 const [records,setRecords]=createSignal<DefinitionImprovement[]>([]),[next,setNext]=createSignal<number|null>(null);
 const [listBusy,setListBusy]=createSignal(false),[listError,setListError]=createSignal('');
 const [selected,setSelected]=createSignal<DefinitionImprovement>(),[source,setSource]=createSignal<DefinitionImprovementSource>();
 const [detailBusy,setDetailBusy]=createSignal(false),[detailError,setDetailError]=createSignal('');
 const [opening,setOpening]=createSignal(false),[navigationError,setNavigationError]=createSignal('');
 const lifetime=new AbortController();
 let detailRequest:AbortController|undefined;
 let list:HTMLElement|undefined,back:HTMLButtonElement|undefined,lastTrigger:HTMLElement|undefined,listPosition=0;
 onCleanup(()=>{lifetime.abort();detailRequest?.abort();});
 function summary(row:DefinitionImprovement){
  return (row.change==='add'?p.t('新增','Added'):p.t('移除','Removed'))+' '+categories[row.category]+' · ';
 }
 function stateLabel(state:DefinitionImprovement['state']){
  const [zh,en]=states[state];
  return p.t(zh,en);
 }
 async function load(before:number|null){
  if(listBusy())return;
  setListBusy(true);setListError('');
  try{
   const found=await stageApi.definitionImprovements(p.scope,p.definition,before,lifetime.signal);
   if(lifetime.signal.aborted)return;
   setRecords(current=>before===null?found.records:[...current,...found.records]);
   setNext(found.next_before);
   if(before!==null&&found.records.length&&document.activeElement?.classList.contains('wf-history-more')){
    requestAnimationFrame(()=>Array.from(list?.querySelectorAll<HTMLElement>('[data-history-operation]')??[])
     .find(el=>el.dataset.historyOperation===found.records[0].operation_id)?.focus());
   }
  }catch(e){
   if(!lifetime.signal.aborted)setListError(e instanceof Error?e.message:String(e));
  }finally{if(!lifetime.signal.aborted)setListBusy(false);}
 }
 onMount(()=>void load(null));
 async function read(row:DefinitionImprovement){
  detailRequest?.abort();
  const request=detailRequest=new AbortController();
  setDetailBusy(true);setDetailError('');setSource(undefined);
  try{
   const found=await stageApi.definitionImprovementSource(p.scope,p.definition,row.operation_id,request.signal);
   if(!request.signal.aborted)setSource(found);
  }catch(e){
   if(!request.signal.aborted)setDetailError(e instanceof Error?e.message:String(e));
  }finally{if(!request.signal.aborted)setDetailBusy(false);}
 }
 function show(row:DefinitionImprovement,trigger:HTMLElement){
  lastTrigger=trigger;listPosition=list?.scrollTop??0;
  setNavigationError('');setSelected(row);
  queueMicrotask(()=>back?.focus({preventScroll:true}));
  void read(row);
 }
 function returnToList(){
  if(opening())return;
  detailRequest?.abort();setSelected(undefined);setSource(undefined);
  if(list)list.scrollTop=listPosition;
  lastTrigger?.focus({preventScroll:true});
 }
 async function openSource(){
  const current=source();
  if(!current||opening())return;
  setOpening(true);setNavigationError('');
  try{await p.onOpenSource(current);}
  catch(e){if(!lifetime.signal.aborted)setNavigationError(e instanceof Error?e.message:String(e));}
  finally{if(!lifetime.signal.aborted)setOpening(false);}
 }
 return <div class="wf-definition-history">
  <header class="wf-history-version"><strong>{p.definition.name}</strong>
   <span>v{p.definition.revision}</span>
  </header>
  <section ref={list} class="wf-history-scroll" hidden={!!selected()} aria-label={p.t('定义改进列表','Definition improvements')}>
   <Show when={listError()}><p class="wf-error" role="alert">{listError()}</p></Show>
   <Show when={listBusy()&&!records().length}><p role="status">{p.t('正在读取改进记录…','Loading improvements…')}</p></Show>
   <Show when={!listBusy()&&!listError()&&!records().length}><p>{p.t('这个版本没有改进记录。','This version has no improvement records.')}</p></Show>
   <ul class="wf-history-list"><For each={records()}>{row=><li>
    <button type="button" data-history-operation={row.operation_id} onClick={event=>show(row,event.currentTarget)}>
     <span><strong>{row.target_name}</strong><small>{summary(row)}<code>{row.capability_id}</code></small></span>
     <span class="wf-history-state"><small>v{row.revision} · {stateLabel(row.state)}</small><WorkflowIcon name="arrow" size={15}/></span>
    </button>
   </li>}</For></ul>
   <Show when={listError()||next()!==null}><button type="button" class="wf-link wf-history-more" aria-disabled={listBusy()}
    onClick={()=>void load(records().length?next():null)}>{listBusy()?p.t('正在读取…','Loading…'):
     listError()?p.t('重新读取','Try again'):p.t('更早的记录','Earlier records')}</button></Show>
  </section>
  <Show when={selected()} keyed>{row=><section class="wf-history-scroll wf-history-source" aria-label={p.t('原审核来源','Approved source')}>
   <button ref={back} type="button" class="wf-link" aria-disabled={opening()} onClick={returnToList}>
    <WorkflowIcon name="back" size={15}/>{p.t('返回改进列表','Back to improvements')}
   </button>
   <div class="wf-history-description"><h3>{row.target_name}</h3><p>{summary(row)}<code>{row.capability_id}</code></p>
    <small>v{row.revision} · {stateLabel(row.state)}</small>
   </div>
   <Show when={detailBusy()}><p role="status">{p.t('正在核对原审核版本…','Checking the original approval…')}</p></Show>
   <Show when={detailError()}><p class="wf-error" role="alert">{detailError()}</p>
    <button type="button" class="wf-link" onClick={()=>void read(row)}>{p.t('重新核对','Check again')}</button>
   </Show>
   <Show when={source()}>{value=><div class="wf-history-quote">
    <div><strong>{value().stage_name}</strong>
     <small>{value().historical?p.t('历史审核版本','Historical approval'):p.t('原审核版本','Original approval')} · <time dateTime={value().approved_at}>{new Date(value().approved_at).toLocaleString(p.t('zh-CN','en'))}</time></small>
    </div>
    <blockquote>{value().quote}</blockquote>
    <button type="button" class="wf-link" aria-disabled={opening()} onClick={()=>void openSource()}>
     <WorkflowIcon name="arrow" size={15}/>{opening()?p.t('正在打开原阶段…','Opening original stage…'):p.t('打开原阶段','Open original stage')}
    </button>
   </div>}</Show>
   <Show when={navigationError()}><p class="wf-error" role="alert">{navigationError()}</p></Show>
  </section>}</Show>
 </div>;
}
