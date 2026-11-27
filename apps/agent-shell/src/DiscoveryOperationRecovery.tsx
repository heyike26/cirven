import {createEffect,createSignal,For,onCleanup,Show,untrack} from 'solid-js';
import {discoveryApi} from './discovery-api';
import type {DiscoveryRecoveryOperation,DiscoveryRecoveryPage} from './discovery-contracts';
import TaskStatus from './TaskStatus';
import {t as uiText,uiLocale} from './ui-language';

export default function DiscoveryOperationRecovery(props:{projectId:string;sessionId:string;revision:number;onSelect:(operation:DiscoveryRecoveryOperation,explicit:boolean)=>void|Promise<unknown>}){
  const [page,setPage]=createSignal<DiscoveryRecoveryPage>();
  const [loading,setLoading]=createSignal(false),[failed,setFailed]=createSignal(false);
  let request=0,controller:AbortController|undefined,disposed=false;
  const identity=()=>JSON.stringify([props.projectId,props.sessionId,props.revision]);
  async function choose(operation:DiscoveryRecoveryOperation,explicit=true){
    setLoading(true);
    try{await props.onSelect(operation,explicit);}catch{if(!disposed)setFailed(true);}
    finally{if(!disposed)setLoading(false);}
  }
  async function load(before?:number,automatic=false){
    const key=identity(),owner=++request;controller?.abort();controller=new AbortController();
    setLoading(true);setFailed(false);
    try{
      const result=await discoveryApi.recoverable(props.projectId,props.sessionId,before,controller.signal);
      if(disposed||owner!==request||identity()!==key)return;
      setPage(result);
      // Unique across the complete result, never "the latest" from a partial page.
      if(automatic&&result.next_before===null&&result.operations.length===1&&result.operations[0].basis_revision===props.revision)await choose(result.operations[0],false);
    }catch(error){if(!disposed&&owner===request&&identity()===key&&!controller.signal.aborted)setFailed(true);}
    finally{if(!disposed&&owner===request)setLoading(false);}
  }
  createEffect(()=>{identity();untrack(()=>{setPage(undefined);void load(undefined,true);});});
  onCleanup(()=>{disposed=true;request++;controller?.abort();});
  const time=(value?:string|null)=>value?new Date(value).toLocaleString(uiLocale()==='en'?'en-US':'zh-CN') : '';
  return <Show when={failed()||page()?.operations.length||page()?.next_before}><details class="as-operation-recovery" aria-label={uiText('未完成的处理')}>
    <summary>{uiText(failed()?'处理记录暂不可用':'查看未完成的处理')}</summary><div class="as-operation-recovery-list">
    <p>{uiText(failed()?'暂时无法读取原处理记录。原输入保留，请稍后核查。':'选择要核查的未完成处理；核查不会重新调用模型。')}</p>
    <Show when={!failed()}><For each={page()?.operations}>{operation=><button type="button" disabled={loading()} onClick={()=>void choose(operation)}>
      <span><Show when={operation.sequence!=null}>{uiText('第 {value0} 次处理',{value0:String(operation.sequence)})}{' · '}</Show>{uiText(operation.kind==='PLAN'?'任务计划':'需求梳理')}<Show when={operation.basis_revision!=null}>{uiText(' · 版本 ')}{operation.basis_revision}</Show></span>
      <TaskStatus value={operation.status}/><small>{time(operation.created_at)}</small>
    </button>}</For></Show>
    <Show when={failed()} fallback={<Show when={page()?.next_before}><button type="button" disabled={loading()} onClick={()=>void load(page()!.next_before!)}>{uiText('读取更早的处理')}</button></Show>}><button type="button" disabled={loading()} onClick={()=>void load(undefined,true)}>{uiText('重新读取记录')}</button></Show>
  </div></details></Show>;
}
