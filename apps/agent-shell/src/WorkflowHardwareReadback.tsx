import {Show} from 'solid-js';
import type {HardwareReadback,ReadbackIssue} from './stage-workflows';

const issues:Record<ReadbackIssue,[string,string]>={
 missing_readback:['缺少设备编号、采样时间或读值','Device ID, sample time, or reading is missing'],
 wrong_device:['设备与已审目标不同','The device differs from the approved target'],
 invalid_sample_time:['采样时间或时区无法核对','Sample time or time zone could not be verified'],
 outside_dispatch:['采样不在本次请求与返回之间','The sample falls outside this request and response'],
};

export default function WorkflowHardwareReadback(p:{
 value:HardwareReadback;issue?:ReadbackIssue;t:(zh:string,en:string)=>string;
}){
 const unknown=()=>p.t('未取得','Unavailable');
 return <>
  <dt>{p.t('设备回读','Device readback')}</dt>
  <dd>
   <strong>{p.value.device_id??unknown()}</strong> · {p.t('连接器上报','Reported by connector')}
   <Show when={p.issue}>{issue=><p>{p.t(...issues[issue()])}</p>}</Show>
  </dd>
  <dt>{p.t('采样时间','Sample time')}</dt><dd>{p.value.sampled_at??unknown()}</dd>
  <dt>{p.t('请求与返回','Request and response')}</dt><dd>{p.value.dispatched_at}<br/>{p.value.received_at}</dd>
  <Show when={p.value.observed_preview!==undefined}><dt>{p.t('实际读值','Observed reading')}</dt>
   <dd><code>{p.value.observed_preview}</code></dd>
  </Show>
 </>;
}
