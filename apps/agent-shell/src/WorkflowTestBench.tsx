import {For,Index,Show} from 'solid-js';
import type {BenchContext,BenchDraft,BenchFirmware,BenchReceipt} from './stage-workflows';
import WorkflowDialog from './WorkflowDialog';
import {WorkflowIcon} from './WorkflowIcon';

type Tr=(zh:string,en:string)=>string;
type DeviceDraft=BenchDraft['devices'][number];
export function TestBenchDetails(p:{bench:BenchReceipt;t:Tr;fileHref?:(id:string)=>string}){
 const t=p.t;
 return <details class="wf-bench-receipt">
  <summary>{t('实测工位','Test bench')}<Show when={p.bench.devices.length}><small> · {p.bench.devices.length} {t('台设备','devices')}</small></Show></summary>
  <dl class="wf-bench-facts">
   <Show when={p.bench.host}><dt>{t('上位机版本','Host software version')}</dt><dd>{p.bench.host}</dd></Show>
   <Show when={p.bench.fixture}><dt>{t('夹具与接线','Fixture and wiring')}</dt><dd>{p.bench.fixture}</dd></Show>
  </dl>
  <For each={p.bench.devices}>{device=><section class="wf-bench-device">
   <h4>{device.label}</h4>
   <dl class="wf-bench-facts">
    <Show when={device.identity}><dt>{t('设备编号','Device ID')}</dt><dd>{device.identity}</dd></Show>
    <Show when={device.link}><dt>{t('连接方式','Connection')}</dt><dd>{device.link}</dd></Show>
    <Show when={device.firmware_label}><dt>{t('现场报告版本','Observed firmware version')}</dt><dd>{device.firmware_label}</dd></Show>
    <Show when={device.firmware}>{file=><><dt>{t('项目固件依据','Project firmware reference')}</dt><dd>
     <Show when={p.fileHref?.(file().id)} fallback={file().path}>{href=><a download="" href={href()}>{file().path}</a>}</Show>
     <br/><code title={file().sha256}>{file().sha256}</code></dd></>}</Show>
   </dl>
  </section>}</For>
 </details>;
}

export default function WorkflowTestBench(p:{
 open:boolean;draft:BenchDraft;firmwares:BenchFirmware[];latestAvailable:boolean;canReuse:boolean;t:Tr;
 retest:boolean;previous?:BenchContext['previous_bench'];
 firmwareStatus:'idle'|'loading'|'failed';onRetry:()=>void;onDraft:(draft:BenchDraft)=>void;onReuse:()=>void;onClose:()=>void
}){
 const t=p.t;
 let scroller:HTMLDivElement|undefined;
 const reuseAvailable=()=>!!p.previous||p.latestAvailable;
 function updateDevice(index:number,change:Partial<DeviceDraft>){
  p.onDraft({...p.draft,devices:p.draft.devices.map((device,i)=>i===index?{...device,...change}:device)});
 }
 function addDevice(){
  const index=p.draft.devices.length;
  p.onDraft({...p.draft,devices:[...p.draft.devices,{label:''}]});
  queueMicrotask(()=>{
   const target=scroller?.querySelector<HTMLInputElement>(`[data-bench-device="${index}"] input`);
   if(!target||!scroller||!p.open)return;
   const bounds=scroller.getBoundingClientRect(),field=target.getBoundingClientRect();
   if(field.top<bounds.top||field.bottom>bounds.bottom)scroller.scrollTop+=field.top-bounds.top-12;
   target.focus({preventScroll:true});
  });
 }
 return <WorkflowDialog open={p.open} title={t('实测工位','Test bench')} size="regular" childScroll
  locale={t('zh-CN','en')} onClose={p.onClose}>
  <div class="wf-manual">
   <div class="wf-stage-panel wf-manual-scroll" ref={scroller}>
    <Show when={p.retest||p.latestAvailable}><div class="wf-bench-reuse" data-retest={p.retest}>
     <div>
      <button type="button" data-visible={reuseAvailable()} aria-disabled={!p.canReuse||!reuseAvailable()}
       onClick={()=>{if(p.canReuse&&reuseAvailable())p.onReuse();}}>
       <WorkflowIcon name="history" size={15}/>{p.previous?t('沿用返工前工位','Reuse bench from before rework'):
        t('沿用最近记录的工位','Reuse the latest recorded bench')}
      </button>
     </div>
     <Show when={p.retest}><div class="wf-bench-reuse-source">
      <Show when={p.previous}>{source=><small>
       {source().status==='failed'?t('原实测失败','Previous observation failed'):t('原实测受阻','Previous observation blocked')} ·
       <time dateTime={source().at}>{new Date(source().at).toLocaleString()}</time>
      </small>}</Show>
      <small role="status">{p.previous?t('请重新核对本次连接和固件。','Verify the connection and firmware for this retest.'):''}</small>
     </div></Show>
    </div></Show>
    <div class="wf-bench-firmware-status">
     <small role="status">{p.firmwareStatus==='loading'?t('正在读取已审固件…','Reading approved firmware…'):
      p.firmwareStatus==='failed'?t('暂未读取已审固件。','Approved firmware could not be read.'):''}</small>
     <button type="button" class="wf-link" data-show={p.firmwareStatus!=='idle'} aria-disabled={p.firmwareStatus==='loading'}
      onClick={()=>{if(p.firmwareStatus==='failed')p.onRetry();}}>{t('重新读取','Retry')}</button>
    </div>
    <label>{t('上位机版本','Host software version')}<input maxLength={300} value={p.draft.host??''}
     onInput={e=>p.onDraft({...p.draft,host:e.currentTarget.value})}/></label>
    <label>{t('夹具与接线','Fixture and wiring')}<input maxLength={300} value={p.draft.fixture??''}
     onInput={e=>p.onDraft({...p.draft,fixture:e.currentTarget.value})}/></label>
    <Index each={p.draft.devices}>{(device,index)=><fieldset class="wf-bench-device" data-bench-device={index}>
     <legend>{t('设备','Device')} {index+1}</legend>
     <label>{t('设备名称和板卡版本','Device and board revision')}<input maxLength={160}
      value={device().label} onInput={e=>updateDevice(index,{label:e.currentTarget.value})}/></label>
     <div class="wf-controls">
      <label>{t('设备编号或探针序列号','Device or probe ID')}<input maxLength={160}
       value={device().identity??''} onInput={e=>updateDevice(index,{identity:e.currentTarget.value})}/></label>
      <label>{t('连接方式','Connection')}<input maxLength={160} placeholder="COM7 · 115200"
       value={device().link??''} onInput={e=>updateDevice(index,{link:e.currentTarget.value})}/></label>
     </div>
     <label>{t('现场报告的固件版本','Observed firmware version')}<input maxLength={160}
      value={device().firmware_label??''} onInput={e=>updateDevice(index,{firmware_label:e.currentTarget.value})}/></label>
     <Show when={p.firmwares.length||device().firmware_id}><label>{t('项目固件依据','Project firmware reference')}
      <select value={device().firmware_id??''} onChange={e=>updateDevice(index,{firmware_id:e.currentTarget.value||undefined})}>
       <option value="" selected={!device().firmware_id}>{t('不关联项目文件','No project file')}</option>
       <Show when={device().firmware_id&&!p.firmwares.some(file=>file.id===device().firmware_id)}>
        <option value={device().firmware_id}>{t('原文件已不可选，请重新选择','Previous file unavailable. Select again.')}</option>
       </Show>
       <For each={p.firmwares}>{file=><option value={file.id} selected={file.id===device().firmware_id}>{file.path} · {file.sha256.slice(0,12)}</option>}</For>
      </select>
     </label></Show>
     <div><button type="button" class="wf-link" aria-label={t(`移除设备 ${index+1}`,`Remove device ${index+1}`)}
      onClick={()=>p.onDraft({...p.draft,devices:p.draft.devices.filter((_,i)=>i!==index)})}>{t('移除设备','Remove device')}</button></div>
    </fieldset>}</Index>
    <div><button type="button" disabled={p.draft.devices.length>=6} onClick={addDevice}>
     <WorkflowIcon name="plus" size={15}/>{t('添加设备','Add device')}
    </button></div>
   </div>
   <footer class="wf-manual-actions"><button type="button" class="wf-primary" onClick={p.onClose}>{t('返回实测','Return to observation')}</button></footer>
  </div>
 </WorkflowDialog>;
}
