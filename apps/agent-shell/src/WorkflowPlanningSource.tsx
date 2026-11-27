import {createSignal,onCleanup,Show} from 'solid-js';
import {stageApi,type RunStage,type PlanningSource,type StageRun} from './stage-workflows';
import StageArtifact from './StageArtifact';
import WorkflowDialog from './WorkflowDialog';

type ReadState={status:'loading'}|{status:'failed';error:string}|{status:'ready';source:PlanningSource};

export default function WorkflowPlanningSource(p:{
 run:StageRun;stage:RunStage;operation:string;t:(zh:string,en:string)=>string
}){
 const [open,setOpen]=createSignal(false);
 const [read,setRead]=createSignal<ReadState>({status:'loading'});
 const snapshot=()=>{const value=read();return value.status==='ready'?value.source:undefined;};
 const failure=()=>{const value=read();return value.status==='failed'?value.error:undefined;};
 const locale=()=>p.t('zh-CN','en');
 let controller:AbortController|undefined;
 onCleanup(()=>controller?.abort());

 async function load(){
  const origin=p.stage.reused_from;
  if(!origin||!p.stage.stage_session_id||controller)return;
  const request=controller=new AbortController();
  setRead({status:'loading'});
  try{
   const source=await stageApi.planningSource(p.run,p.stage,p.operation,request.signal);
   if(request.signal.aborted)return;
   if(source.operation_id!==p.operation||source.run_id!==origin.run_id||source.node_id!==origin.node_id||
       source.stage_session_id!==origin.stage_session_id||source.approval_sha256!==origin.approval_sha256)
    throw Error(p.t('返回的记录与这一版复用成果不一致。','The approval belongs to a different reused result.'));
   setRead({status:'ready',source});
  }catch(error){
   if(!request.signal.aborted)setRead({status:'failed',
    error:error instanceof Error?error.message:p.t('暂时无法读取原审批。','Could not read the original approval.')});
  }finally{if(controller===request)controller=undefined;}
 }
 function close(){
  controller?.abort();
  controller=undefined;
  setOpen(false);
 }

 return <>
  <button type="button" class="wf-planning-source-link" aria-haspopup="dialog" onClick={event=>{
   event.currentTarget.focus({preventScroll:true});
   setOpen(true);void load();
  }}>{p.t('核对原审批','Verify original approval')}</button>
  <WorkflowDialog open={open()} title={p.t('复用的原审批','Original approval')} size="regular" locale={locale()} onClose={close}>
   <div class="wf-planning-source">
    <Show when={snapshot()} fallback={<Show when={failure()} fallback={
     <p role="status">{p.t('正在读取原审批…','Reading the original approval…')}</p>
    }>{error=><div class="wf-planning-retry">
     <p class="wf-error" role="alert">{error()}</p>
     <button type="button" onClick={()=>void load()}>{p.t('重试读取','Retry reading')}</button>
    </div>}</Show>}>{source=><>
     <header>
      <h3>{source().stage_name}</h3>
      <p><span>{source().historical?p.t('历史审批 · 只读','Historical approval · Read-only'):p.t('已审核 · 只读','Approved · Read-only')}</span>
       <time dateTime={source().approved_at}>{new Date(source().approved_at).toLocaleString(locale())}</time>
       <code title={source().approval_sha256}>{source().approval_sha256.slice(0,12)}</code>
      </p>
     </header>
     <StageArtifact stage={{artifact:source().artifact,source_urs:source().source_urs,annotations:[],verified_evidence:[]}}
      kind={source().stage_kind} t={p.t} marking={false} editable={false}/>
     <Show when={source().artifact.template_output}>{content=><details class="wf-planning-template">
      <summary>{p.t('原审批文稿','Original approved document')}</summary><p>{content()}</p>
     </details>}</Show>
    </>}</Show>
   </div>
  </WorkflowDialog>
 </>;
}
