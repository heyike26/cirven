import {createSignal,For,onCleanup,onMount,Show} from 'solid-js';
import {stageEventText,stageFieldLabel,Values} from './StageArtifact';
import {stageApi,type RevisionBasis,type RevisionChange,type RevisionField,type RevisionSelection,type RevisionSummary,type RevisionValue} from './stage-workflows';
import {WorkflowIcon} from './WorkflowIcon';
import './workflow-revision-diff.css';

type Tr=(zh:string,en:string)=>string;
type Selection={key:RevisionSelection;name:string;change?:RevisionChange};
const changes:Record<RevisionChange,[string,string]>={added:['新增','Added'],modified:['修改','Modified'],removed:['移除','Removed']};
const changeKinds=Object.keys(changes) as RevisionChange[];

export default function WorkflowRevisionDiff(p:{basis:RevisionBasis;t:Tr}){
 const [summary,setSummary]=createSignal<RevisionSummary>(),[selection,setSelection]=createSignal<Selection>();
 const [listLoading,setListLoading]=createSignal(false),[detailLoading,setDetailLoading]=createSignal(false);
 const [listError,setListError]=createSignal(''),[detailError,setDetailError]=createSignal('');
 const [fields,setFields]=createSignal<RevisionField[]>([]);
 const control=new AbortController();
 let detailRequest:AbortController|undefined,list:HTMLElement|undefined,last:HTMLElement|undefined,back:HTMLButtonElement|undefined;
 let listTop=0;
 onCleanup(()=>{control.abort();detailRequest?.abort();});

 async function load(offset:number){
  if(listLoading())return;
  setListLoading(true);setListError('');
  try{
   const response=await stageApi.revisionSummary(p.basis,offset,control.signal);
   if(!control.signal.aborted){
    setSummary(current=>offset&&current?{...response,rows:[...current.rows,...response.rows]}:response);
    if(offset&&document.activeElement?.classList.contains('wf-diff-more')){
     requestAnimationFrame(()=>Array.from(list?.querySelectorAll<HTMLElement>('[data-diff-row]')??[])
      .find(button=>button.dataset.diffRow===response.rows[0]?.id)?.focus());
    }
   }
  }catch(e){if(!control.signal.aborted)setListError(e instanceof Error?e.message:String(e));}
  finally{if(!control.signal.aborted)setListLoading(false);}
 }
 onMount(()=>void load(0));

 async function read(item:Selection){
  detailRequest?.abort();
  const request=detailRequest=new AbortController();
  setFields([]);setDetailError('');setDetailLoading(true);
  try{
   const detail=await stageApi.revisionFields(p.basis,item.key,request.signal);
   if(!request.signal.aborted)setFields(detail.fields);
  }catch(e){if(!request.signal.aborted)setDetailError(e instanceof Error?e.message:String(e));}
  finally{if(!request.signal.aborted)setDetailLoading(false);}
 }
 function open(item:Selection,trigger:HTMLElement){
  last=trigger;listTop=list?.scrollTop??0;
  setSelection(item);
  queueMicrotask(()=>back?.focus({preventScroll:true}));
  void read(item);
 }
 function returnToList(){
  detailRequest?.abort();setSelection(undefined);
  if(list)list.scrollTop=listTop;
  last?.focus({preventScroll:true});
 }
 function FieldValue(props:{label:string;value:RevisionValue;field:string;missingRow:boolean}){
  const current=()=>{const cell=props.value;return cell.present?cell:undefined;};
  return <div class="wf-diff-value">
   <strong>{props.label}</strong>
   <Show when={current()} fallback={<span class="wf-muted">{props.missingRow?p.t('该版本无此条目','Item absent in this version'):p.t('该版本无此字段','Field absent in this version')}</span>}>{cell=>
    <Show when={cell().value!==null} fallback={<span class="wf-muted">{p.t('空值','Null')}</span>}>
     <Values field={props.field} value={cell().value} t={p.t}/>
    </Show>}</Show>
  </div>;
 }
 return <div class="wf-revision-diff">
  <Show when={summary()}>{data=><header class="wf-diff-basis">
   <span>{p.t('上一版','Previous version')} · {stageEventText(data().previous_status,p.t)}</span>
   <WorkflowIcon name="arrow" size={15}/>
   <span>{p.t('本版','Selected version')} · {stageEventText(data().current_status,p.t)}</span>
  </header>}</Show>
  <section ref={list} class="wf-diff-scroll" hidden={!!selection()} aria-label={p.t('修订变更清单','Revision changes')}>
   <Show when={listError()}><p class="wf-error" role="alert">{listError()}</p></Show>
   <Show when={listLoading()&&!summary()}><p role="status">{p.t('正在读取两个成果版本…','Reading both output versions…')}</p></Show>
   <Show when={summary()}>{data=><>
    <div class="wf-diff-counts"><For each={changeKinds}>{kind=><span data-change={kind}>
     {p.t(...changes[kind])} <strong>{data().counts[kind]}</strong>
    </span>}</For></div>
    <Show when={data().reordered}><p class="wf-diff-note">{p.t('两个版本共有的条目顺序有变化。','Shared items were reordered.')}</p></Show>
    <Show when={data().artifact_changes}>
     <button class="wf-link wf-diff-overview" type="button" onClick={event=>open({key:{group:'artifact'},
      name:p.t('成果说明','Output details')},event.currentTarget)}>
      {p.t('成果说明','Output details')} · {data().artifact_changes}
      <WorkflowIcon name="arrow" size={15}/>
     </button>
    </Show>
    <Show when={!data().artifact_changes&&!data().reordered&&!data().rows.length}>
     <p>{p.t('两版成果内容一致。','Both versions contain the same output.')}</p>
    </Show>
    <ul class="wf-diff-rows"><For each={data().rows}>{row=><li>
     <button type="button" data-diff-row={row.id} onClick={event=>open(
      {key:{group:'row',row_id:row.id},name:row.title,change:row.change},event.currentTarget)}>
      <span><small>{row.id}</small><strong>{row.title}</strong></span>
      <span class="wf-diff-tag" data-change={row.change}>{p.t(...changes[row.change])}</span>
      <WorkflowIcon name="arrow" size={15}/>
     </button>
    </li>}</For></ul>
   </>}</Show>
   <Show when={listError()||summary()?.next_offset!=null}><button type="button" class="wf-link wf-diff-more"
    aria-disabled={listLoading()} onClick={()=>void load(summary()?.next_offset??0)}>
    {listLoading()?p.t('正在读取…','Loading…'):listError()?p.t('重新读取','Try again'):p.t('更多条目','More items')}
   </button></Show>
  </section>
  <Show when={selection()} keyed>{item=><section class="wf-diff-scroll wf-diff-detail" aria-label={p.t('字段差异','Field changes')}>
   <button ref={back} type="button" class="wf-link" onClick={returnToList}><WorkflowIcon name="back" size={15}/>{p.t('返回变更清单','Back to changes')}</button>
   <div class="wf-diff-detail-title"><Show when={item.key.group==='row'&&item.key.row_id}>{id=><small>
    {id()}<Show when={item.change}>{change=>' · '+p.t(...changes[change()])}</Show>
   </small>}</Show><h3>{item.name}</h3></div>
   <Show when={detailLoading()}><p role="status">{p.t('正在读取变更字段…','Loading changed fields…')}</p></Show>
   <Show when={detailError()}><p class="wf-error" role="alert">{detailError()}</p>
    <button type="button" class="wf-link" onClick={()=>void read(item)}>{p.t('重新读取','Try again')}</button>
   </Show>
   <For each={fields()}>{(field,i)=><details class="wf-diff-field" open={i()===0}>
    <summary>{stageFieldLabel(field.field,p.t)}</summary>
    <div class="wf-diff-pair">
     <FieldValue label={p.t('上一版','Previous version')} field={field.field} value={field.before} missingRow={item.change==='added'}/>
     <FieldValue label={p.t('本版','Selected version')} field={field.field} value={field.after} missingRow={item.change==='removed'}/>
    </div>
   </details>}</For>
  </section>}</Show>
 </div>;
}
