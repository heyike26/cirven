import {createEffect,createSignal,For,onCleanup,Show,untrack} from 'solid-js';
import WorkflowDialog from './WorkflowDialog';
import {ObservationFiles} from './WorkflowManualFiles';
import {TestBenchDetails} from './WorkflowTestBench';
import {WorkflowIcon} from './WorkflowIcon';
import WorkflowHardwareReadback from './WorkflowHardwareReadback';
import {stageApi,type ReworkEvidence,type RunStage,type StageRun} from './stage-workflows';

export default function WorkflowReworkEvidence(p:{
 run:StageRun;stage:RunStage;revision?:number;t:(zh:string,en:string)=>string;
}){
 const [open,setOpen]=createSignal(false);
 const [offset,setOffset]=createSignal(0),[retry,setRetry]=createSignal(0);
 const [data,setData]=createSignal<ReworkEvidence>(),[error,setError]=createSignal('');
 let basis:number|undefined;
 let body:HTMLDivElement|undefined;
 let closeButton:HTMLButtonElement|undefined;
 createEffect(()=>{
  if(!open())return;
  retry();
  const page=offset(),controller=new AbortController();
  const {run,stage}=untrack(()=>({run:p.run,stage:p.stage}));
  setData(undefined);setError('');
  if(body)body.scrollTop=0;
  void stageApi.reworkEvidence(run,stage,basis,page,controller.signal).then(result=>{
   if(!controller.signal.aborted)setData(result);
  }).catch(reason=>{
   if(!controller.signal.aborted)setError(reason instanceof Error?reason.message:
    p.t('暂时无法读取返工证据。','Could not read the rework evidence.'));
  });
  onCleanup(()=>controller.abort());
 });

 return <>
  <button type="button" class="wf-link wf-rework-evidence-trigger" aria-haspopup="dialog"
   onClick={()=>{basis=p.revision;setOffset(0);setOpen(true);}}>
   <WorkflowIcon name="file" size={15}/>{p.t('查看返工证据','View rework evidence')}
  </button>
  <WorkflowDialog open={open()} title={p.t('返工证据','Rework evidence')} size="regular" childScroll
   locale={p.t('zh-CN','en')} onClose={()=>setOpen(false)}>
   <Show when={open()}><div class="wf-rework-evidence">
    <div ref={body} class="wf-rework-evidence-scroll">
     <Show when={data()} fallback={<Show when={error()} fallback={
      <p role="status">{p.t('正在读取原测试记录…','Reading the original test results…')}</p>
     }>{message=><p class="wf-error" role="alert">{message()}</p>}</Show>}>{result=>
      <>
       <Show when={result().error}>{message=><p class="wf-rework-evidence-note">{message()}</p>}</Show>
       <Show when={result().records.length} fallback={<p>{p.t('这次返工没有逐项测试回执。','This rework has no individual test receipts.')}</p>}>
        <ul class="wf-rework-evidence-list" aria-label={p.t('原测试结果','Original test results')}>
         <For each={result().records}>{item=><li>
          <div class="wf-rework-evidence-heading">
           <h3>{item.case_id}<Show when={item.action_id}>{id=><small> · {id()}</small>}</Show></h3>
           <span class="wf-status" data-status="failed">{item.status==='blocked'?p.t('受阻','Blocked'):p.t('失败','Failed')}</span>
          </div>
          <Show when={item.at??item.recorded_at}>{at=><time dateTime={at()}>{new Date(at()).toLocaleString()}</time>}</Show>
          <dl>
           <Show when={item.environment}>{text=><div><dt>{p.t('实测环境','Environment')}</dt><dd>{text()}</dd></div>}</Show>
           <Show when={item.observation??item.detail}>{text=><div><dt>{p.t('现场记录','Observation')}</dt><dd>{text()}</dd></div>}</Show>
           <Show when={item.path}>{path=><div><dt>{p.t('测试路径','Test path')}</dt><dd><code>{path()}</code></dd></div>}</Show>
           <Show when={item.subject}>{subject=><div><dt>{p.t('已核对程序','Verified program')}</dt>
            <dd><code>{subject().path}</code><br/><small><code>{subject().sha256}</code></small></dd></div>}</Show>
           <Show when={item.server}>{server=><div><dt>{p.t('连接器','Connector')}</dt><dd>{[server(),item.tool].filter(Boolean).join(' / ')}</dd></div>}</Show>
           <Show when={item.hardware_readback}>{value=><div>
            <WorkflowHardwareReadback value={value()} issue={item.readback_issue} t={p.t}/>
           </div>}</Show>
           <Show when={item.exit_code!==undefined}><div><dt>{p.t('退出码','Exit code')}</dt><dd>{item.exit_code}</dd></div></Show>
           <Show when={item.control}>{control=><div>
            <dt>{p.t('对照输入','Contrast input')}</dt><dd>
             <strong>{!control().limit&&control().output_matches&&control().exit_code===control().expected_exit_code
              ?p.t('符合预期','Matches expectation'):p.t('与预期不符','Result differs')}</strong>
             <p>{p.t('实际退出码','Actual exit code')} {control().exit_code} · {p.t('预期','Expected')} {control().expected_exit_code}</p>
             <Show when={control().output_preview}><pre class="wf-execution-output">{control().output_preview}</pre></Show>
             <Show when={control().output_truncated||control().output_preview_partial}>
              <small>{p.t('当前显示部分输出。','Showing partial output.')}</small>
             </Show>
            </dd>
           </div>}</Show>
          </dl>
          <Show when={item.bench}>{bench=><TestBenchDetails bench={bench()} t={p.t}
           fileHref={id=>stageApi.evidenceFileHref(p.run,result().stage_session_id,id)}/>}</Show>
          <ObservationFiles files={item.attachments} t={p.t} href={id=>stageApi.manualFileHref(p.run,result(),item.id,id)}/>
         </li>}</For>
        </ul>
       </Show>
      </>
     }</Show>
    </div>
    <footer class="wf-rework-evidence-actions">
     <Show when={data()?.offset||data()?.next_offset}>
      <div class="wf-actions">
       <button type="button" disabled={!data()?.offset} onClick={()=>{
        closeButton?.focus({preventScroll:true});setOffset(value=>Math.max(0,value-12));
       }}>{p.t('上一组','Previous')}</button>
       <button type="button" disabled={data()?.next_offset==null} onClick={()=>{
        const page=data()?.next_offset;
        if(page!=null){closeButton?.focus({preventScroll:true});setOffset(page);}
       }}>{p.t('下一组','Next')}</button>
      </div>
     </Show>
     <div class="wf-actions">
      <Show when={error()}><button type="button" onClick={()=>{
       closeButton?.focus({preventScroll:true});setRetry(value=>value+1);
      }}>{p.t('重新读取','Read again')}</button></Show>
      <button type="button" ref={closeButton} onClick={()=>setOpen(false)}>{p.t('返回','Back')}</button>
     </div>
    </footer>
   </div></Show>
  </WorkflowDialog>
 </>;
}
